"""Private evidence for an explicitly offline synthetic integration run."""
import json
import os
from pathlib import Path
import stat
from uuid import UUID


def _directory():
    root = Path(os.environ['GA_SYNTHETIC_RUN_ROOT'])
    info = root.lstat()
    if not root.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('invalid private synthetic run directory')
    return root


def _write_once(name, value):
    root = _directory()
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            payload = json.dumps(value, ensure_ascii=True, sort_keys=True).encode()
            with os.fdopen(fd, 'wb', closefd=False) as out:
                out.write(payload); out.flush(); os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
    finally:
        os.close(directory)


def record_once():
    # Written before the fake response, so even a process crash cannot cause
    # this run identifier to issue a second test request.
    _write_once('fake_request.json', {'run_id': os.environ['GA_SYNTHETIC_RUN_ID'], 'fake_requests': 1})


def save_state(value):
    if set(value) != {'run_id','workspace_id','call_id','intent_id','permit_id'}:
        raise ValueError('unexpected synthetic state')
    for field in value:
        if str(UUID(value[field])) != value[field]:
            raise ValueError('invalid synthetic identifier')
    _write_once('receipt_ref.json', value)


def read_state():
    root = _directory()
    info = (root / 'receipt_ref.json').lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise RuntimeError('invalid synthetic receipt reference')
    value = json.loads((root / 'receipt_ref.json').read_text())
    if value.get('run_id') != os.environ['GA_SYNTHETIC_RUN_ID']:
        raise RuntimeError('synthetic run identity changed')
    return value


def read_request_marker():
    root = _directory()
    info = (root / 'fake_request.json').lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise RuntimeError('invalid fake request marker')
    value = json.loads((root / 'fake_request.json').read_text())
    if value != {'run_id': os.environ['GA_SYNTHETIC_RUN_ID'], 'fake_requests': 1}:
        raise RuntimeError('unexpected fake request count')
    return value
