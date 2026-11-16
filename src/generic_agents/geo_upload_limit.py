"""Bound multipart transport before FastAPI spools an entire raster request."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .geo_codec import RECIPE

class GeoUploadLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('path')!='/api/geo/scenes' or scope.get('method')!='POST':return await self.app(scope,receive,send)
        limit=RECIPE['max_upload_bytes']+65536  # bounded multipart fields/boundaries, not extra image bytes
        headers=dict(scope.get('headers',[]))
        try:declared=int(headers.get(b'content-length',b'0'))
        except ValueError:declared=-1
        if declared<0 or declared>limit:
            return await JSONResponse({'detail':'影像上传请求超过本地配额，未解析或保存；请分幅上传'},status_code=413,headers={'Cache-Control':'private, no-store'})(scope,receive,send)
        received=0
        async def bounded():
            nonlocal received
            message=await receive()
            if message['type']=='http.request':
                received+=len(message.get('body',b''))
                if received>limit:raise HTTPException(413,'影像上传请求超过本地配额，原请求未完成；请分幅上传')
            return message
        return await self.app(scope,bounded,send)
