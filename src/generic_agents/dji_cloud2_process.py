"""One isolated SDK child process. No protocol data or credentials are logged.

READY only acknowledges SDK initialization; a successful LIST is required to
establish connectivity. Callers must fence every report against revocation.
"""
from __future__ import annotations

import base64
import os
import re
import select
import subprocess
import threading
import time
import uuid

MAX_INPUT = 16384
MAX_OUTPUT = 262144
MAX_DEVICES = 1000
_SAFE_CODE = re.compile(r"[A-Z_]{1,40}\Z")


class BridgeError(Exception):
    pass


class BridgeRevoked(BridgeError):
    pass


def _encode(value):
    if not isinstance(value, str) or '\x00' in value:
        raise BridgeError('invalid_input')
    return base64.urlsafe_b64encode(value.encode('utf-8')).rstrip(b'=').decode('ascii')


def _decode(value, maximum=1024):
    if len(value) > maximum * 4 // 3 + 4 or not re.fullmatch(r'[A-Za-z0-9_-]*', value):
        raise BridgeError('invalid_protocol')
    try:
        data = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if len(data) > maximum:
            raise ValueError()
        result = data.decode('utf-8')
        if _encode(result) != value:
            raise ValueError()
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in result):
            raise ValueError()
        return result
    except (ValueError, UnicodeError):
        raise BridgeError('invalid_protocol') from None


def init_frame(host, port, http_headers, websocket_headers):
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise BridgeError('invalid_input')
    fields = ['INIT', '1', _encode(host), str(port)]
    for headers in (http_headers, websocket_headers):
        if not isinstance(headers, dict) or not 1 <= len(headers) <= 8:
            raise BridgeError('invalid_input')
        fields.append(str(len(headers)))
        for name, value in sorted(headers.items()):
            fields.extend([_encode(name), _encode(value)])
    frame = ('\t'.join(fields) + '\n').encode('ascii')
    if len(frame) > MAX_INPUT:
        raise BridgeError('invalid_input')
    return frame


def parse_result(line, request_id):
    try:
        fields = line.decode('ascii').split('\t')
    except UnicodeError:
        raise BridgeError('invalid_protocol') from None
    if len(fields) >= 1 and fields[0] == 'ERROR':
        if len(fields) != 3 or fields[1] != request_id or not _SAFE_CODE.fullmatch(fields[2]):
            raise BridgeError('invalid_protocol')
        if fields[2] not in ('AUTH', 'UPSTREAM', 'TIMEOUT'):
            raise BridgeError('invalid_protocol')
        raise BridgeError('authentication_failed' if fields[2] == 'AUTH' else 'upstream_error')
    if len(fields) < 3 or fields[0] != 'RESULT' or fields[1] != request_id:
        raise BridgeError('invalid_protocol')
    if not re.fullmatch(r'(0|[1-9][0-9]{0,3})', fields[2]):
        raise BridgeError('invalid_protocol')
    count = int(fields[2])
    if count > MAX_DEVICES or len(fields) != 3 + count * 4:
        raise BridgeError('invalid_protocol')
    devices = []
    for i in range(count):
        sn, name, model, online = fields[3 + i*4:7 + i*4]
        if online not in ('0', '1'):
            raise BridgeError('invalid_protocol')
        serial = _decode(sn, 256)
        if not serial:
            raise BridgeError('invalid_protocol')
        devices.append({'serial': serial, 'name': _decode(name, 256),
                        'model': _decode(model, 256), 'online': online == '1'})
    return devices


def parse_websocket_status(line, request_id):
    """Read only a fixed status from the SDK's actual notification callbacks."""
    try:
        fields = line.decode('ascii').split('\t')
    except UnicodeError:
        raise BridgeError('invalid_protocol') from None
    if fields == ['ERROR', request_id, 'SDK_UNSUPPORTED']:
        raise BridgeError('sdk_unsupported')
    if (len(fields) != 4 or fields[0] != 'WS_STATUS' or fields[1] != request_id
            or (fields[2], fields[3]) not in {('PENDING', 'NONE'), ('CONNECTED', 'NONE'),
                                              ('DISCONNECTED', 'NONE'), ('DISCONNECTED', 'AUTH_401'),
                                              ('DISCONNECTED', 'AUTH_403')}):
        raise BridgeError('invalid_protocol')
    return fields[2].lower(), None if fields[3] == 'NONE' else fields[3].lower()


class Bridge:
    def __init__(self, command, authorize, stop, *, timeout=15, interval=0.25, authorization_ttl=5):
        self.command = command
        self.authorize = authorize
        self.stop = stop
        self.timeout = timeout
        self.interval = interval
        self.process = None
        self.buffer = bytearray()
        self.authorization_ttl = authorization_ttl
        self.last_authorized = time.monotonic()
        self.watchdog_stop = threading.Event()
        self.authorization_expired = threading.Event()
        self.watchdog = None

    def _allowed(self):
        if self.stop.is_set():
            raise BridgeRevoked('stopped')
        try:
            allowed = self.authorize()
        except Exception:
            raise BridgeRevoked('authorization_unavailable') from None
        if allowed is not True:
            raise BridgeRevoked('revoked')
        if self.authorization_expired.is_set():
            raise BridgeRevoked('authorization_expired')
        self.last_authorized = time.monotonic()

    def _watch_authorization(self, process):
        while not self.watchdog_stop.wait(.25):
            if self.stop.is_set() or time.monotonic() - self.last_authorized > self.authorization_ttl:
                self.authorization_expired.set()
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                except (OSError, ProcessLookupError):
                    pass
                return

    def start(self, frame):
        self._allowed()
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, close_fds=True, bufsize=0,
                                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                                 'TZ': 'Asia/Shanghai'})
            os.set_blocking(self.process.stdin.fileno(), False)
            self.last_authorized = time.monotonic()
            self.watchdog = threading.Thread(target=self._watch_authorization, args=(self.process,), daemon=True)
            self.watchdog.start()
            self._allowed()
            self._send(frame)
            ready = self._readline()
            if ready == b'FATAL\tSDK_UNSUPPORTED':
                raise BridgeError('sdk_unsupported')
            if ready != b'READY\t1':
                raise BridgeError('initialization_failed')
        except Exception:
            self.close()
            raise

    def _send(self, data):
        deadline = time.monotonic() + self.timeout
        offset = 0
        try:
            fd = self.process.stdin.fileno()
            while offset < len(data):
                self._allowed()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError('timeout')
                _, writable, _ = select.select([], [fd], [], min(self.interval, remaining))
                if writable:
                    try:
                        offset += os.write(fd, data[offset:])
                    except BlockingIOError:
                        continue
                elif self.process.poll() is not None:
                    raise BridgeError('disconnected')
        except (OSError, ValueError):
            raise BridgeError('disconnected') from None

    def _readline(self):
        deadline = time.monotonic() + self.timeout
        fd = self.process.stdout.fileno()
        while True:
            self._allowed()
            if b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                if len(line) > MAX_OUTPUT:
                    raise BridgeError('invalid_protocol')
                return bytes(line)
            if len(self.buffer) > MAX_OUTPUT:
                raise BridgeError('invalid_protocol')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError('timeout')
            try:
                readable, _, _ = select.select([fd], [], [], min(self.interval, remaining))
                if readable:
                    block = os.read(fd, min(8192, MAX_OUTPUT + 1 - len(self.buffer)))
                    if not block:
                        raise BridgeError('disconnected')
                    self.buffer.extend(block)
                elif self.process.poll() is not None:
                    raise BridgeError('disconnected')
            except (OSError, ValueError):
                raise BridgeError('disconnected') from None

    def list_devices(self):
        request_id = str(uuid.uuid4())
        self._send(('LIST\t' + request_id + '\n').encode('ascii'))
        result = parse_result(self._readline(), request_id)
        self._allowed()
        return result

    def websocket_status(self):
        request_id = str(uuid.uuid4())
        self._send(('WS_STATUS\t' + request_id + '\n').encode('ascii'))
        result = parse_websocket_status(self._readline(), request_id)
        self._allowed()
        return result

    def close(self):
        self.watchdog_stop.set()
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        finally:
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
            self.buffer.clear()
