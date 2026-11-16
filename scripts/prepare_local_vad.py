"""Prepare verified official wheels only in an explicit local runtime root."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import venv
import zipfile

import httpx

ROOT = Path(__file__).absolute().parents[1]
REQ = ['onnxruntime==1.31.0', 'numpy==2.3.3', 'av==16.1.0']


def tools():
    spec = importlib.util.spec_from_file_location('_local_audio_prepare', ROOT / 'scripts/prepare_local_audio.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def lock_text(pins):
    return '\n'.join(item['name'] + '==' + item['version'] + ' --hash=sha256:' + item['sha256'] for item in pins) + '\n'


def metadata(content):
    lines = content.decode('utf-8').splitlines()
    names = [line[6:] for line in lines if line.startswith('Name: ')]
    versions = [line[9:] for line in lines if line.startswith('Version: ')]
    if len(names) != 1 or len(versions) != 1 or not names[0] or not versions[0]:
        raise ValueError('依赖元数据不完整')
    return names[0], versions[0]


def runtime_matches(environment, output, helper):
    """Read metadata and pinned wheel bytes, without importing native modules."""
    try:
        helper.select_root(str(environment / 'bin'))
        python = environment / 'bin/python'
        if not python.is_file() or not os.access(python, os.X_OK):
            return False
        target = python.resolve(strict=True)
        info = target.stat()
        if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022:
            return False
        cfg = environment / 'pyvenv.cfg'
        if not helper.checked_file(cfg):
            return False
        values = dict(line.split(' = ', 1) for line in cfg.read_text().splitlines() if ' = ' in line)
        if (values.get('include-system-site-packages') != 'false'
                or not Path(values.get('home', '')).is_absolute()
                or not Path(values.get('home', '')).is_dir()
                or not Path(values.get('executable', '')).is_absolute()
                or not Path(values.get('executable', '')).is_file()):
            return False
        index = output / 'runtime-wheels.json'
        lock = output / 'requirements.lock'
        if not helper.checked_file(index) or not helper.checked_file(lock):
            return False
        pins = json.loads(index.read_text())
        if not isinstance(pins, list) or not pins:
            return False
        versions = {}
        filenames = set()
        for item in pins:
            if (not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key]
                    for key in ('name', 'version', 'filename', 'sha256', 'url'))
                    or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                    or type(item.get('size_bytes')) is not int or item['size_bytes'] <= 0):
                return False
            name = normalized(item['name'])
            endpoint = urlsplit(item['url'])
            if (name in versions or item['filename'] in filenames
                    or Path(item['filename']).name != item['filename'] or not item['filename'].endswith('.whl')
                    or endpoint.scheme != 'https' or endpoint.hostname != 'files.pythonhosted.org'
                    or endpoint.username or endpoint.password):
                return False
            versions[name] = item['version']
            filenames.add(item['filename'])
            wheel = output / 'wheels' / item['filename']
            if (not helper.checked_file(wheel) or wheel.stat().st_size != item['size_bytes']
                    or helper.sha(wheel) != item['sha256']):
                return False
        if any(versions.get(normalized(name)) != version for name, version in (item.split('==') for item in REQ)):
            return False
        if lock.read_text() != lock_text(pins) or {item.name for item in (output / 'wheels').glob('*.whl')} != filenames:
            return False
        installed = {}
        for item in environment.glob('lib/python*/site-packages/*.dist-info/METADATA'):
            if not helper.checked_file(item):
                return False
            name, version = metadata(item.read_bytes())
            name = normalized(name)
            if name in installed:
                return False
            installed[name] = version
        return all(installed.get(name) == version for name, version in versions.items())
    except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        return False


def publish(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def prepare_runtime(root, helper):
    root = helper.select_root(str(root))
    environment = root / 'venvs/vad'
    output = root / 'vad-bootstrap'
    wheels = output / 'wheels'
    helper.select_root(str(environment))
    helper.select_root(str(wheels))
    if os.path.lexists(environment):
        if not runtime_matches(environment, output, helper):
            raise ValueError('已有VAD环境无法由固定依赖记录证明；保留原目录，请管理员检查，不自动重装')
        return {'state': 'runtime_ready', 'cache_verified': True, 'dependency_reinstall': False,
                'model_loaded': False, 'runtime_native_import_verified': False}
    helper.directory(environment.parent)
    helper.directory(wheels)
    venv.EnvBuilder(with_pip=True).create(environment)
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    env.update(PIP_CONFIG_FILE='/dev/null', NETRC='/dev/null', PIP_KEYRING_PROVIDER='disabled',
               PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_CACHE_DIR='1', PYTHONDONTWRITEBYTECODE='1')
    descriptor = os.open(output / 'bootstrap.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as log:
        subprocess.run([str(environment / 'bin/python'), '-B', '-m', 'pip', 'download',
                        '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', '--timeout', '15',
                        '--retries', '1', '--dest', str(wheels), *REQ],
                       env=env, stdout=log, stderr=log, check=True, timeout=600)
        pins = []
        with httpx.Client(trust_env=False, timeout=20) as client:
            for path in sorted(wheels.glob('*.whl')):
                if not helper.checked_file(path):
                    raise ValueError('依赖下载文件不安全')
                with zipfile.ZipFile(path) as archive:
                    records = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
                    if len(records) != 1:
                        raise ValueError('依赖包元数据不唯一')
                    name, version = metadata(archive.read(records[0]))
                response = client.get(f'https://pypi.org/pypi/{name}/{version}/json')
                response.raise_for_status()
                sources = [item for item in response.json()['urls'] if item['filename'] == path.name]
                if (len(sources) != 1 or helper.sha(path) != sources[0]['digests']['sha256']
                        or path.stat().st_size != sources[0]['size']):
                    raise ValueError('官方依赖摘要或大小校验失败')
                endpoint = urlsplit(sources[0]['url'])
                if endpoint.scheme != 'https' or endpoint.hostname != 'files.pythonhosted.org' or endpoint.username or endpoint.password:
                    raise ValueError('依赖来源不在官方文件域名')
                pins.append({'name': name, 'version': version, 'filename': path.name,
                             'sha256': helper.sha(path), 'size_bytes': path.stat().st_size, 'url': sources[0]['url']})
        publish(output / 'runtime-wheels.json', (json.dumps(pins, indent=2) + '\n').encode())
        publish(output / 'requirements.lock', lock_text(pins).encode())
        subprocess.run([str(environment / 'bin/python'), '-B', '-m', 'pip', 'install', '--no-index',
                        '--find-links', str(wheels), '--require-hashes', '-r', str(output / 'requirements.lock')],
                       env=env, stdout=log, stderr=log, check=True, timeout=600)
    if not runtime_matches(environment, output, helper):
        raise ValueError('依赖安装结果与固定记录不一致；保留目录，不加载模型')
    return {'state': 'runtime_ready', 'cache_verified': False, 'dependency_reinstall': False,
            'model_loaded': False, 'runtime_native_import_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='在显式本地组件目录准备VAD官方依赖；不加载模型。')
    parser.add_argument('--runtime-root', required=True, help='服务器控制的绝对本地组件目录')
    args = parser.parse_args(argv)
    helper = tools()
    root = helper.select_root(args.runtime_root)
    print(json.dumps(prepare_runtime(root, helper), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('本地VAD依赖准备失败；保留已有文件，请核对路径和固定依赖记录。', file=sys.stderr)
        raise SystemExit(1) from None
