"""Select local pinned components without installation or an implicit legacy root."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from fastapi import HTTPException

from .config import settings

_SOURCE_ROOT = Path(__file__).absolute().parents[2]
_INVALID = "本地组件目录配置无效，请由服务器管理员检查绝对路径、所有者和权限。"


def validate_local_runtime_root(value: Path | str, *, source_root: Path = _SOURCE_ROOT) -> Path:
    """Read-only validation; a missing pack directory remains a missing pack.

    No symlink aliases, untrusted writable ancestors, or components in an
    immutable release are accepted. Fixed package manifests remain responsible
    for component bytes; selecting a root does not authorize arbitrary code.
    """
    try:
        path = Path(value)
        if (not path.is_absolute() or '..' in path.parts
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in str(path))
                or path.resolve(strict=False) != path):
            raise ValueError
        source_root = Path(source_root).absolute()
        if source_root.is_relative_to(path):
            raise ValueError
        if any((parent / name).is_file() for parent in (path, *path.parents)
                for name in ('source-manifest.json', 'release-manifest.json')):
            raise ValueError
        if any(path.is_relative_to(source_root / part) for part in ('src', 'static', 'scripts', 'tests')):
            raise ValueError
        nearest = path
        while not os.path.lexists(nearest):
            nearest = nearest.parent
        info = nearest.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError
        for parent in nearest.parents:
            info = parent.stat(follow_symlinks=False)
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                    or (info.st_mode & 0o022 and not sticky_root)):
                raise ValueError
        return path
    except (OSError, RuntimeError, TypeError, ValueError):
        raise HTTPException(409, _INVALID) from None


def local_runtime_root() -> Path:
    configuration = settings()
    selected = configuration.local_runtime_root
    if selected is None:
        selected = Path(configuration.storage_dir).absolute().parent / 'runtimes'
    return validate_local_runtime_root(selected)
