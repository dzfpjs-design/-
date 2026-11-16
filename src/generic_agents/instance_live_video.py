"""Bounded, instance-authorized RTSP camera preview; no audio or recording."""
import json
import os
import select as select_io
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .config import settings
from .db import engine
from .instance_observation_catalog import media_resource_proof, one_resource, source_kind
from .contracts import digest
from .media import origin, source_state, stream_url, valid_path, validated_channel
from .models import AuthSession, MediaChannel, MediaGateway, Membership, User, now
from .services import scoped
from .setup_api import DB, P

router = APIRouter(prefix='/api/instance-observations')
_slots = threading.BoundedSemaphore(2)
MAX_SECONDS = 60
FRAME_INTERVAL = 0.5
MAX_JPEG = 1024 * 1024


def readonly_proof(db, p, channel_id):
    """Read authority is independent of model/recording validation."""
    channel = scoped(db, MediaChannel, channel_id, p)
    gateway = scoped(db, MediaGateway, channel.gateway_id, p)
    if channel.status not in ('confirmed', 'validated') or not channel.data.get('read_authorized') or gateway.status != 'ready':
        raise HTTPException(409, '通道或网关尚未确认读取授权')
    api = origin(gateway.data['api_origin'], 'api')
    rtsp = origin(gateway.data['rtsp_origin'], 'rtsp')
    path = valid_path(channel.path)
    return {'source_kind': 'media_live_readonly_v1', 'channel_id': channel.id,
            'gateway_id': gateway.id, 'channel_generation': channel.data['generation'],
            'gateway_generation': gateway.data['generation'],
            'configuration_hash': digest({'api': api, 'rtsp': rtsp, 'path': path}),
            'physical_device_identity': 'unconfirmed'}


def _current(db, p, instance_id, resource_id):
    """Recheck the session, membership, published resource and source proof."""
    auth = db.get(AuthSession, p.session_id)
    user = db.get(User, p.user_id)
    member = db.scalar(select(Membership).where(Membership.user_id == p.user_id,
                                                Membership.workspace_id == p.workspace_id))
    if (not auth or auth.revoked or auth.user_id != p.user_id or auth.expires_at <= now()
            or not user or not user.active or not member or member.role != p.role):
        raise HTTPException(403, '账号会话或工作区授权已失效')
    resource = one_resource(db, p, instance_id, resource_id)
    if source_kind(resource) != 'video' or resource.get('status') in ('disabled', 'inactive', 'deleted', 'paused'):
        raise HTTPException(409, '该实例来源未启用实时视频')
    actual = media_resource_proof(db, p, resource)
    if resource.get('backend_source_proof') != actual:
        raise HTTPException(409, '来源配置已变化，请重新核对并发布')
    if actual['source_kind'] == 'media_live_readonly_v1':
        channel = scoped(db, MediaChannel, actual['channel_id'], p)
        gateway = scoped(db, MediaGateway, actual['gateway_id'], p)
    else:
        channel, gateway = validated_channel(db, p, actual['channel_id'])
    return channel, gateway, actual


def _part(jpeg):
    return b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n'


def _stop(proc):
    if proc is None: return
    try:
        if proc.poll() is None:
            try: proc.terminate()
            except ProcessLookupError: pass
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: proc.kill()
                except ProcessLookupError: pass
                proc.wait(timeout=2)
    finally:
        if proc.stdout: proc.stdout.close()


class _Lease:
    """One owner for the process and slot across disconnect and generator cleanup."""
    def __init__(self):
        self.proc = None
        self.closed = False
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            if self.closed: return
            self.closed = True
            try: _stop(self.proc)
            finally: _slots.release()


class _LiveResponse(StreamingResponse):
    def __init__(self, lease, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lease = lease

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette skips background tasks on ASGI 2.4 send failures.
            # Close synchronously even when cancellation is already active.
            self.lease.close()


def _read_exact(proc, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select_io.select([proc.stdout], [], [], remaining)[0]:
            raise TimeoutError('preview timeout')
        data = os.read(proc.stdout.fileno(), size - len(result))
        if not data: raise EOFError('stream ended')
        result.extend(data)
    return bytes(result)


def _read_frame(proc, deadline):
    size = struct.unpack('>I', _read_exact(proc, 4, deadline))[0]
    if size <= 0 or size > MAX_JPEG: raise ValueError('frame size exceeded')
    image = _read_exact(proc, size, deadline)
    if not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
        raise ValueError('invalid JPEG')
    return image


def _frames(lease, first, p, instance_id, resource_id, expected, initial_source):
    proc = lease.proc
    started = time.monotonic()
    last_source = started
    try:
        yield _part(first)
        while True:
            clock = time.monotonic()
            if clock - started >= MAX_SECONDS:
                break
            frame = _read_frame(proc, min(started + MAX_SECONDS, clock + 5))
            clock = time.monotonic()
            if clock - started >= MAX_SECONDS:
                break
            # Read first: revocation may happen while waiting for a frame.
            # Every emitted frame follows a fresh scoped check.
            with sessionmaker(engine(), expire_on_commit=False)() as db:
                channel, gateway, proof = _current(db, p, instance_id, resource_id)
                if proof != expected:
                    break
                if clock - last_source >= 10:
                    if source_state(gateway, channel).get('source_id') != initial_source:
                        break
                    last_source = clock
                    # The network check may block. Recheck after it returns.
                    db.expire_all()
                    _, _, proof = _current(db, p, instance_id, resource_id)
                    if proof != expected:
                        break
            yield _part(frame)
    except (Exception, GeneratorExit):
        # A disconnected client, revoked grant, stream failure or bad frame ends
        # the preview. The next request must authenticate and revalidate again.
        pass
    finally:
        lease.close()


@router.get('/{instance_id}/sources/{resource_id}/live')
def live(instance_id: str, resource_id: str, p: P, db: DB):
    channel, gateway, proof = _current(db, p, instance_id, resource_id)
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '实时画面连接数已满，请稍后重试')
    lease = _Lease()
    try:
        state = source_state(gateway, channel)
        # Keep the configured venv path itself: resolving the symlink would
        # select the base interpreter and lose the venv's decoder packages.
        executable = settings().specialist_python.absolute()
        if not executable.is_file(): raise RuntimeError('isolated media runtime unavailable')
        proc = subprocess.Popen([str(executable), '-u', str(Path(__file__).with_name('instance_live_video_runner.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True,
            env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
        lease.proc = proc
        proc.stdin.write(json.dumps({'url': stream_url(gateway, channel), 'parent_pid': os.getpid()}).encode() + b'\n')
        proc.stdin.close()
        first = _read_frame(proc, time.monotonic() + 12)
        # Opening/decoding may take seconds. Do not send the buffered first
        # frame under a session, membership, publication, or source grant that
        # was revoked while the decoder was connecting.
        fresh_source = source_state(gateway, channel)
        db.expire_all()
        _, _, fresh_proof = _current(db, p, instance_id, resource_id)
        if fresh_proof != proof or fresh_source.get('source_id') != state['source_id']:
            raise ValueError('source changed during connection')
        return _LiveResponse(lease, _frames(lease, first, p, instance_id, resource_id,
                                         proof, state['source_id']),
            media_type='multipart/x-mixed-replace; boundary=frame',
            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                     'X-Accel-Buffering': 'no'})
    except Exception:
        lease.close()
        raise HTTPException(409, '实时画面暂不可用，请检查通道在线状态、编码和读取授权') from None
