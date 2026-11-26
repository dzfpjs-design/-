"""Private aggregate socket receipts for a disposable, loopback-only stub.

The file is evidence of accepted connections, not HTTP requests or sends.
Anything except a valid ``stopped`` snapshot is incomplete evidence.
"""
import json
import os
from pathlib import Path
import tempfile
from threading import Lock


class ConnectionReceipt:
    def __init__(self, path):
        self.path = Path(path)
        self.count = 0
        self.failed = False
        self.lock = Lock()
        # Never replace evidence from another run, even a symlink.
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._save('starting', 0)

    def _save(self, state, count):
        payload = json.dumps({'state': state, 'accepted': count}, separators=(',', ':')).encode('ascii')
        fd, temp = tempfile.mkstemp(prefix='.connection-stats-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                if out.write(payload) != len(payload):
                    raise OSError('short connection receipt write')
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass

    def ready(self):
        with self.lock:
            self._save('running', self.count)

    def accepted(self):
        with self.lock:
            if self.failed:
                raise OSError('connection receipt unavailable')
            try:
                self._save('running', self.count + 1)
            except Exception:
                self.failed = True
                raise
            self.count += 1

    def stopped(self):
        with self.lock:
            if self.failed:
                return False
            try:
                self._save('stopped', self.count)
            except Exception:
                self.failed = True
                return False
            return True
