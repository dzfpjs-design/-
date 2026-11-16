"""Select a verified, project-owned backend bundle for local services."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re


def service_environment(root, inherited):
    root = Path(root).resolve()
    pointer = root / '.runtime/active-backend-source.json'
    result = dict(inherited)
    if not pointer.exists():
        return result
    if pointer.is_symlink() or pointer.stat().st_size > 8192:
        raise RuntimeError('运行版本记录异常，未启动服务')
    data = json.loads(pointer.read_text())
    if type(data.get('format')) is not int or data['format'] != 1 or not isinstance(data.get('source_dir'), str):
        raise RuntimeError('运行版本格式异常，未启动服务')
    relative = PurePosixPath(data['source_dir'])
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError('运行目录不属于本项目，未启动服务')
    source = (root / relative).resolve()
    releases = (root / '.runtime/releases').resolve()
    if not releases.is_relative_to(root) or not source.is_relative_to(releases) or source == releases:
        raise RuntimeError('运行目录不属于发布包，未启动服务')
    manifest = source / 'source-manifest.json'
    expected = data.get('manifest_sha256')
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise RuntimeError('运行版本摘要缺失，未启动服务')
    if manifest.is_symlink() or sha256(manifest.read_bytes()).hexdigest() != expected:
        raise RuntimeError('运行版本清单改变，未启动服务')
    entries = json.loads(manifest.read_text()).get('files')
    if not isinstance(entries, list) or not entries:
        raise RuntimeError('运行文件清单为空，未启动服务')
    recorded = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError('运行文件记录异常，未启动服务')
        name, file_hash = entry.get('path'), entry.get('sha256')
        if not isinstance(name, str) or not isinstance(file_hash, str):
            raise RuntimeError('运行文件记录异常，未启动服务')
        path = PurePosixPath(name)
        if not path.parts or path.is_absolute() or '..' in path.parts or path.parts[0] != 'generic_agents' or path.suffix != '.py':
            raise RuntimeError('运行文件路径异常，未启动服务')
        target = source / name
        if name in recorded or target.is_symlink() or not target.resolve().is_relative_to(source):
            raise RuntimeError('运行文件重复或越界，未启动服务')
        if sha256(target.read_bytes()).hexdigest() != file_hash:
            raise RuntimeError('运行代码改变，未启动服务')
        recorded.add(name)
    actual = {str(path.relative_to(source)) for path in (source / 'generic_agents').rglob('*.py')}
    if actual != recorded or 'generic_agents/__init__.py' not in recorded:
        raise RuntimeError('运行代码与清单不一致，未启动服务')
    result['PYTHONPATH'] = str(source)
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    result['PYTHONSAFEPATH'] = '1'
    result.setdefault('GA_SETUP_BUILD_DIR', str(root / 'design/resource-studio/dist/runtime'))
    return result
