"""Server-only authenticated storage for connection-scoped bearer credentials.

No GET projection calls this module. The master key is explicitly provisioned
outside the database; loss/corruption blocks writes and consumption, never
creates a replacement or falls back to environment variables. The first driver
is an explicitly allowlisted, numeric-loopback procurement simulator only.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException

from .config import settings

KEY_ERROR = '网关凭据保护密钥不可用，原连接保留；请由管理员恢复原密钥'
CREDENTIAL_ERROR = '网关凭据无法核对，原连接保留；请核对凭据与连接版本'


def _need(condition, message=CREDENTIAL_ERROR):
    if not condition:
        raise HTTPException(409, message)


def _master_key():
    configured = settings().device_gateway_key_file
    try:
        if configured is None:
            raise ValueError()
        path = Path(configured).absolute()
        if path.resolve(strict=True) != path:
            raise ValueError()
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ValueError()
        # A corrupt configured path can be a FIFO. Do not block in open before
        # fstat has a chance to reject its file type.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
                    or info.st_size != 32):
                raise ValueError()
            key = os.read(fd, 33)
        finally:
            os.close(fd)
        if len(key) != 32:
            raise ValueError()
        return key
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(409, KEY_ERROR) from None


def create_key(path):
    """Explicit deployment operation; never invoked by HTTP or a reader."""
    path = Path(path).absolute()
    parent = path.parent
    _need(parent.resolve(strict=True) == parent, KEY_ERROR)
    info = parent.stat()
    _need(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077, KEY_ERROR)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        raw = os.urandom(32)
        _need(os.write(fd, raw) == len(raw), KEY_ERROR)
        os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _keys():
    # Separate encryption and request-fingerprint purposes, both from the
    # explicitly provisioned high-entropy master. No bare secret hash in DB.
    raw = HKDF(algorithm=hashes.SHA256(), length=64, salt=None,
        info=b'generic-agents-device-gateway-v268').derive(_master_key())
    return raw[:32], raw[32:]


def _aad(workspace_id, connection_id, revision):
    try:
        _need(all(type(value) is str and str(UUID(value)) == value
            for value in (workspace_id, connection_id)))
        _need(type(revision) is int and revision >= 1)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(409, CREDENTIAL_ERROR) from None
    return json.dumps(['gateway-credential-v268', workspace_id, connection_id, revision],
        ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _secret(value):
    _need(type(value) is str and 8 <= len(value) <= 4096
        and all(0x21 <= ord(char) <= 0x7e for char in value))
    return value.encode('ascii')


def seal(workspace_id, connection_id, revision, secret):
    aad = _aad(workspace_id, connection_id, revision)
    plaintext = _secret(secret)
    nonce = os.urandom(12)
    return 'gac268.' + base64.urlsafe_b64encode(
        nonce + AESGCM(_keys()[0]).encrypt(nonce, plaintext, aad)).decode('ascii')


def open_secret(workspace_id, connection_id, revision, ciphertext):
    aad = _aad(workspace_id, connection_id, revision)
    try:
        _need(type(ciphertext) is str and ciphertext.startswith('gac268.')
            and len(ciphertext) <= 6000)
        raw = base64.b64decode(ciphertext[7:], altchars=b'-_', validate=True)
        _need(len(raw) >= 36)
        plaintext = AESGCM(_keys()[0]).decrypt(raw[:12], raw[12:], aad).decode('ascii')
        _secret(plaintext)
        return plaintext
    except HTTPException:
        raise
    except Exception:
        # AEAD errors and malformed encodings carry no source text or key path.
        raise HTTPException(409, CREDENTIAL_ERROR) from None


def request_fingerprint(safe_payload, secret=None):
    _need(type(safe_payload) is dict)
    body = {'schema': 'gateway-request-fingerprint-v268', 'payload': safe_payload,
        'credential': None if secret is None else _secret(secret).decode('ascii')}
    try:
        raw = json.dumps(body, sort_keys=True, ensure_ascii=True,
            allow_nan=False, separators=(',', ':')).encode('ascii')
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(422, '网关申请内容格式无效') from None
    return hmac.new(_keys()[1], raw, hashlib.sha256).hexdigest()


def validate_origin(origin):
    # Exact numeric origin avoids DNS rebinding and secret egress. This driver
    # has no general private-network exception and no hostname/DNS fallback.
    if (type(origin) is not str
            or re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})', origin) is None
            or not 1 <= int(origin.rsplit(':', 1)[1]) <= 65535
            or origin not in {s.strip() for s in settings().device_gateway_allowed_origins.split(',') if s.strip()}):
        raise HTTPException(422, '测试设备服务地址尚未由管理员按完整地址授权')
    return origin
