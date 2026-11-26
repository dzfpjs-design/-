"""Conservative one-attempt marker for a private synthetic integration run."""
import os
from pathlib import Path
import stat


def mark_attempt():
    root = Path(os.environ['GA_SYNTHETIC_RUN_ROOT'])
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('invalid synthetic run root')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('synthetic root must be private')
        fd = os.open('provider-send-claimed', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            os.write(fd, b'original send may have started; do not retry\n')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
    finally:
        os.close(directory)
