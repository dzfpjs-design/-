"""Prepare pinned public weights in an explicit server-selected runtime root."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys

import httpx

ROOT = Path(__file__).absolute().parents[1]


def select_root(value):
    source = ROOT / 'src'
    for name, module in tuple(sys.modules.items()):
        if name == 'generic_agents' or name.startswith('generic_agents.'):
            location = getattr(module, '__file__', None)
            if location and not Path(location).absolute().is_relative_to(source):
                raise ValueError('组件路径校验器不属于当前安装源码')
    sys.path.insert(0, str(source))
    module = importlib.import_module('generic_agents.runtime_paths')
    if Path(module.__file__).absolute() != source / 'generic_agents/runtime_paths.py':
        raise ValueError('组件路径校验器不属于当前安装源码')
    return module.validate_local_runtime_root(value, source_root=ROOT)


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def checked_file(path):
    if path.resolve(strict=False) != path:
        raise ValueError('组件文件不能使用路径别名')
    if not os.path.lexists(path):
        return False
    info = path.stat(follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise ValueError('组件文件所有者、类型或权限不安全')
    return True


def directory(path):
    select_root(str(path))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    select_root(str(path))


def matched(path, item):
    return checked_file(path) and path.stat().st_size == item['bytes'] and sha(path) == item['sha256']


def prepare_weights(root, recipe):
    root = select_root(str(root))
    weights = root / 'audio-bootstrap/weights'
    select_root(str(weights))
    items = recipe['files']
    if not items or any(Path(item['name']).name != item['name'] for item in items):
        raise ValueError('模型文件名不属于固定配方')
    if all(matched(weights / item['name'], item) for item in items):
        return {'state': 'complete', 'files': len(items), 'cache_verified': True,
                'weights_loaded': False, 'venv_created': False}
    directory(weights)

    def require_https(request):
        if request.url.scheme != 'https':
            raise ValueError('公开权重下载必须使用HTTPS')

    with httpx.Client(trust_env=False, follow_redirects=True,
                      timeout=httpx.Timeout(30, connect=8),
                      event_hooks={'request': [require_https]}) as client:
        for item in items:
            target = weights / item['name']
            part = weights / (item['name'] + '.part')
            if matched(target, item):
                continue
            if matched(part, item):
                os.replace(part, target)
                continue
            offset = part.stat().st_size if checked_file(part) else 0
            if offset >= item['bytes']:
                offset = 0
            url = f"https://modelscope.cn/models/{recipe['repository']}/resolve/{item['revision']}/{item['name']}"
            with client.stream('GET', url, headers={'Range': f'bytes={offset}-'} if offset else {}) as response:
                response.raise_for_status()
                if offset and response.status_code == 206:
                    if response.headers.get('content-range') != f"bytes {offset}-{item['bytes'] - 1}/{item['bytes']}":
                        raise ValueError('下载断点范围与固定配方不一致')
                else:
                    offset = 0
                flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_APPEND if offset else os.O_TRUNC)
                descriptor = os.open(part, flags, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    info = os.fstat(stream.fileno())
                    if (info.st_uid != os.getuid() or info.st_nlink != 1
                            or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022):
                        raise ValueError('下载暂存文件不安全')
                    size = offset
                    for block in response.iter_bytes(1024 * 1024):
                        size += len(block)
                        if size > item['bytes']:
                            raise ValueError('官方文件超过固定配方大小')
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
            if not matched(part, item):
                raise ValueError('官方文件摘要与固定配方不一致，不加载')
            checked_file(target)
            os.replace(part, target)
    return {'state': 'complete', 'files': len(items), 'cache_verified': False,
            'weights_loaded': False, 'venv_created': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='在显式本地组件目录准备固定语音权重；不创建解释器、不加载模型。')
    parser.add_argument('--runtime-root', required=True, help='服务器控制的绝对本地组件目录')
    args = parser.parse_args(argv)
    root = select_root(args.runtime_root)
    recipe = json.loads((ROOT / 'model-packs/sensevoice-small-v1.json').read_text())
    print(json.dumps(prepare_weights(root, recipe), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('本地语音权重准备失败；保留已有文件，请核对路径和固定配方。', file=sys.stderr)
        raise SystemExit(1) from None
