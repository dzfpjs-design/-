"""Limit route uploads before multipart parsing, including anonymous streams."""
from fastapi import HTTPException
from starlette.responses import JSONResponse

from .dji_wayline_archive import MAX_ARCHIVE_BYTES

# Allow bounded space for multipart headers, boundaries and the request ID.
MULTIPART_OVERHEAD_BYTES = 65536


class DjiWaylineUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or scope.get('method') != 'POST'
                or scope.get('path') not in ('/api/dji/waylines', '/api/dji/waylines/')):
            return await self.app(scope, receive, send)
        limit = MAX_ARCHIVE_BYTES + MULTIPART_OVERHEAD_BYTES
        lengths = [v for k, v in scope.get('headers', []) if k.lower() == b'content-length']
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError
                declared = int(lengths[0])
                if declared > limit:
                    raise ValueError
            except (ValueError, OverflowError):
                return await JSONResponse(
                    {'detail': '航线上传请求过大或请求长度无效'}, status_code=413,
                    headers={'Cache-Control': 'private, no-store'},
                )(scope, receive, send)
        received = 0
        overflow = False
        replaced = False

        async def bounded_receive():
            nonlocal received, overflow
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    overflow = True
                    # Raising interrupts the parser; its exception cleanup closes
                    # any spooled files. Normalize the outer response below.
                    raise HTTPException(413, '航线上传请求超过大小限制')
            return message

        async def bounded_send(message):
            nonlocal replaced
            if overflow:
                if not replaced:
                    replaced = True
                    await JSONResponse(
                        {'detail': '航线上传请求超过大小限制'}, status_code=413,
                        headers={'Cache-Control': 'private, no-store'},
                    )(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except Exception:
            if not overflow:
                raise
            if not replaced:
                await JSONResponse(
                    {'detail': '航线上传请求超过大小限制'}, status_code=413,
                    headers={'Cache-Control': 'private, no-store'},
                )(scope, receive, send)
