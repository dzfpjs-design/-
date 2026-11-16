"""Server-selected local audio paths; selection never installs or runs code."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from fastapi import HTTPException

from .config import settings
from .runtime_paths import local_runtime_root, validate_local_runtime_root

_INVALID = '本地音频组件目录配置无效，请管理员核对绝对路径、所有者和权限。'


@dataclass(frozen=True)
class LocalAudioPaths:
    # The selected server root is retained even when individual server-only
    # overrides point to a different, independently validated private directory.
    root: Path
    python: Path
    weights: Path


def _python_path(value: Path) -> Path:
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in str(path))):
        raise ValueError
    # A venv's final bin/python link is legitimate. Every directory leading to
    # that literal executable must still be canonical, private and trusted.
    validate_local_runtime_root(path.parent)
    if os.path.lexists(path):
        target = path.resolve(strict=True) if path.is_symlink() else path
        info = target.stat(follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o022 or not info.st_mode & 0o111):
            raise ValueError
        # The target file must not be replaceable through an untrusted parent.
        # Standard system-owned base interpreters remain valid venv targets.
        for parent in target.parents:
            info = parent.stat(follow_symlinks=False)
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                    or (info.st_mode & 0o022 and not sticky_root)):
                raise ValueError
    return path


def _paths(kind: str) -> LocalAudioPaths:
    try:
        configuration = settings()
        root = local_runtime_root()
        selected_python = getattr(configuration, kind + '_python')
        selected_weights = getattr(configuration, kind + '_weights')
        python = root / 'venvs' / kind / 'bin/python' if selected_python is None else selected_python
        weights = root / (kind + '-bootstrap') / 'weights' if selected_weights is None else selected_weights
        return LocalAudioPaths(root, _python_path(python), validate_local_runtime_root(weights))
    except HTTPException:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise HTTPException(409, _INVALID) from None


def audio_paths() -> LocalAudioPaths:
    return _paths('audio')


def vad_paths() -> LocalAudioPaths:
    return _paths('vad')
