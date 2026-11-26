#!/usr/bin/env python3
"""Run a release API while preserving an existing local launcher configuration.

This supervisor does not migrate databases, start workers or send signals to
recorded PIDs.  Run ``serve`` in a supervised foreground session and use the
private control socket for ``status`` and ``stop``.  A stale state is an error,
not permission to stop or replace an unidentified process.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
import threading
import time


class Refused(Exception):
    pass


def _private_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise Refused('私有运行目录不安全。')
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (resolved != path or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise Refused('私有运行目录不安全。')
    if len(os.fsencode(resolved / 'control.sock')) >= 100:
        raise Refused('私有运行目录路径过长。')
    return resolved


def _read_private(path: Path, maximum: int = 262144) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > maximum):
                raise Refused('运行配置文件不安全。')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise Refused('运行配置文件过大。')
            return data
        finally:
            os.close(fd)
    except OSError:
        raise Refused('运行配置文件不可用。') from None


def _release_paths(root: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or root.is_symlink():
        raise Refused('发布目录不安全。')
    resolved = root.resolve(strict=True)
    if resolved != root or not resolved.is_dir():
        raise Refused('发布目录不安全。')
    source = (resolved / 'src').resolve(strict=True)
    static = (resolved / 'static' / 'setup').resolve(strict=True)
    if (not source.is_relative_to(resolved) or not static.is_relative_to(resolved)
            or not (source / 'generic_agents' / 'api.py').is_file()
            or not (source / 'generic_agents' / '__init__.py').is_file()
            or not (static / 'index.html').is_file()
            or not (resolved / 'model-packs').is_dir()
            or not (resolved / 'runtime-packs').is_dir()):
        raise Refused('发布代码或页面不完整。')
    return source, static


def _artifact_digest(root: Path) -> str:
    source, static = _release_paths(root)
    digest = hashlib.sha256()
    count = 0
    # A reviewed release is frozen before it is started.  Owner-writable
    # artifacts could otherwise drift after the startup digest check.
    for directory in (root, root / 'static', source, static,
                      root / 'model-packs', root / 'runtime-packs'):
        if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o222:
            raise Refused('发布工件必须先设置为只读。')
    for base, label in ((source, 'src'), (static, 'static/setup'),
                        (root / 'model-packs', 'model-packs'),
                        (root / 'runtime-packs', 'runtime-packs')):
        if base.is_symlink():
            raise Refused('发布目录不能包含链接。')
        for path in sorted(base.rglob('*')):
            if path.is_symlink():
                raise Refused('发布目录不能包含链接。')
            if stat.S_IMODE(path.stat().st_mode) & 0o222:
                raise Refused('发布工件必须先设置为只读。')
            if not path.is_file():
                continue
            if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.name.startswith('.env'):
                raise Refused('发布目录不能包含环境凭据。')
            count += 1
            if count > 20000 or path.stat().st_size > 80 * 1024 * 1024:
                raise Refused('发布目录超出核验范围。')
            rel = label + '/' + path.relative_to(base).as_posix()
            digest.update(rel.encode('utf-8') + b'\0')
            with path.open('rb') as stream:
                file_digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    file_digest.update(chunk)
            digest.update(file_digest.digest())
    if not count:
        raise Refused('发布目录为空。')
    return digest.hexdigest()


def _runtime_link(release: Path, expected: Path | None) -> None:
    link = release / '.runtime'
    if expected is None:
        if link.exists() or link.is_symlink():
            raise Refused('运行时路径尚未核对。')
        return
    if not expected.is_absolute() or expected.is_symlink():
        raise Refused('运行时路径不安全。')
    resolved = expected.resolve(strict=True)
    if resolved != expected or not resolved.is_dir() or not link.is_symlink() or link.resolve(strict=True) != resolved:
        raise Refused('运行时路径与既有环境不一致。')


def _write_exclusive(path: Path, value: dict) -> None:
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def _validate_launcher(raw: bytes) -> None:
    """Only a pinned environment-only launcher ending in one API call is valid."""
    try:
        tree = ast.parse(raw)
        last = tree.body[-1]
        if (not isinstance(last, ast.Expr) or not isinstance(last.value, ast.Call)
                or ast.unparse(last.value.func) != 'uvicorn.run'):
            raise ValueError('unexpected final call')
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == 'uvicorn.run']
        if len(calls) != 1:
            raise ValueError('multiple api calls')
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name not in {'os', 'sys', 'json', 'uvicorn'} for alias in node.names):
                    raise ValueError('unexpected import')
            elif isinstance(node, ast.ImportFrom):
                allowed = {'pathlib': {'Path'}, 'sqlalchemy.engine': {'URL'},
                           'dotenv': {'dotenv_values'}}
                if (node.module not in allowed or node.level != 0
                        or any(alias.name not in allowed[node.module] for alias in node.names)):
                    raise ValueError('unexpected import')
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                   ast.Lambda, ast.AsyncFor, ast.AsyncWith, ast.Await)):
                raise ValueError('unexpected executable')
            elif isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                forbidden = ('subprocess', 'multiprocessing', 'threading', 'socket',
                             'requests', 'urllib', 'os.system', 'os.spawn', 'os.exec',
                             'os.fork', 'os.kill', 'os.popen', 'importlib', '__import__',
                             'exec', 'eval', 'compile', 'getattr', 'setattr', 'open')
                if any(name == item or name.startswith(item + '.') for item in forbidden):
                    raise ValueError('unexpected call')
    except (SyntaxError, UnicodeError, ValueError, IndexError):
        raise Refused('原运行配置包含未支持的启动方式。') from None


def _state(directory: Path) -> dict:
    try:
        value = json.loads(_read_private(directory / 'active.json', 8192))
        if (type(value) is not dict or set(value) != {'token', 'run_id', 'port'}
                or not isinstance(value['token'], str) or len(value['token']) != 64
                or not isinstance(value['run_id'], str) or len(value['run_id']) != 32
                or type(value['port']) is not int or not 1 <= value['port'] <= 65535):
            raise ValueError('bad state')
        return value
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise Refused('运行状态无法核对；不会操作其他进程。') from None


def _control(directory: Path, command: str) -> dict:
    state = _state(directory)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect(str(directory / 'control.sock'))
        sock.sendall(json.dumps({'token': state['token'], 'run_id': state['run_id'],
                                 'command': command}, separators=(',', ':')).encode() + b'\n')
        raw = b''
        while b'\n' not in raw and len(raw) <= 8192:
            part = sock.recv(4096)
            if not part:
                break
            raw += part
        result = json.loads(raw)
        if (type(result) is not dict or result.get('run_id') != state['run_id']
                or result.get('port') != state['port']):
            raise ValueError('unexpected peer')
        return result
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise Refused('无法确认运行状态；不会操作其他进程。') from None
    finally:
        sock.close()


def _control_loop(listener: socket.socket, state: dict, server, closing: threading.Event) -> None:
    listener.settimeout(0.25)
    while not closing.is_set():
        try:
            peer, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        with peer:
            peer.settimeout(2)
            try:
                raw = b''
                while b'\n' not in raw and len(raw) <= 8192:
                    chunk = peer.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                value = json.loads(raw)
                if (type(value) is not dict or type(value.get('token')) is not str
                        or not hmac.compare_digest(value['token'], state['token'])
                        or value.get('run_id') != state['run_id']
                        or value.get('command') not in ('status', 'stop')):
                    continue
                command = value['command']
                payload = {'run_id': state['run_id'], 'port': state['port'],
                           'state': ('stopping' if server.should_exit else
                                     'ready' if server.started else 'starting')}
                peer.sendall(json.dumps(payload, separators=(',', ':')).encode() + b'\n')
                if command == 'stop':
                    server.should_exit = True
            except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                continue


def serve(*, release: Path, legacy: Path, legacy_sha256: str, artifact_sha256: str,
          directory: Path, port: int, runtime_root: Path | None = None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise Refused('本机监听端口无效。')
    directory = _private_directory(directory)
    source, static = _release_paths(release)
    _runtime_link(release, runtime_root)
    raw = _read_private(legacy)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), legacy_sha256):
        raise Refused('原运行配置已变化，未启动。')
    _validate_launcher(raw)
    if not hmac.compare_digest(_artifact_digest(release), artifact_sha256):
        raise Refused('发布工件已变化，未启动。')
    lock_fd = os.open(directory / 'supervisor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_info = os.fstat(lock_fd)
    if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
            or stat.S_IMODE(lock_info.st_mode) & 0o077):
        os.close(lock_fd)
        raise Refused('运行控制文件不安全。')
    listener = bound = None
    created_state = created_socket = None
    closing = threading.Event()
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('已有受控服务正在运行。') from None
        if (directory / 'active.json').exists() or (directory / 'control.sock').exists():
            raise Refused('已有运行记录或残留状态，请先核对。')
        # Hold the bound socket throughout startup; another local process cannot
        # claim the port between a preflight check and the Uvicorn bind.
        bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            bound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            bound.bind(('127.0.0.1', port))
            bound.listen(128)
        except OSError:
            raise Refused('端口已占用，未启动也未停止其他进程。') from None
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(directory / 'control.sock'))
        sock_info = (directory / 'control.sock').lstat()
        created_socket = (sock_info.st_dev, sock_info.st_ino)
        os.chmod(directory / 'control.sock', 0o600)
        listener.listen(8)
        state = {'token': secrets.token_hex(32), 'run_id': secrets.token_hex(16), 'port': port}
        _write_exclusive(directory / 'active.json', state)
        state_info = (directory / 'active.json').lstat()
        created_state = (state_info.st_dev, state_info.st_ino)

        # The original launcher sets the existing environment, storage and
        # specialist runtime paths.  Only its final Uvicorn call is redirected.
        import uvicorn
        original_run = uvicorn.run
        entered = False
        legacy_globals = {'__name__': '__main__', '__file__': str(legacy)}

        def run_release(app, **kwargs):
            nonlocal entered
            if (entered or app != 'generic_agents.api:app'
                    or set(kwargs) != {'host', 'port', 'log_level', 'access_log'}
                    or kwargs.get('host') != '127.0.0.1' or kwargs.get('port') != 8766
                    or kwargs.get('log_level') != 'error' or kwargs.get('access_log') is not False):
                raise Refused('原运行配置的启动方式已变化。')
            if any(name == 'generic_agents' or name.startswith('generic_agents.') for name in sys.modules):
                raise Refused('代码模块已提前加载，无法安全切换。')
            # The legacy configuration may take time to initialize.  Recheck
            # immediately before importing the release, not only at preflight.
            _runtime_link(release, runtime_root)
            if not hmac.compare_digest(_artifact_digest(release), artifact_sha256):
                raise Refused('发布工件已变化，未启动。')
            old_root = legacy_globals.get('root')
            if not isinstance(old_root, Path) or not old_root.is_absolute():
                raise Refused('原代码目录无法核对。')
            old_source = (old_root / 'src').resolve()
            # Do not fall through to the previous checkout for project modules.
            sys.path[:] = [entry for entry in sys.path
                           if Path(entry or os.getcwd()).resolve() != old_source]
            entered = True
            sys.path.insert(0, str(source))
            os.environ['GA_SETUP_BUILD_DIR'] = str(static)
            os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
            sys.dont_write_bytecode = True
            config = uvicorn.Config(app, host='127.0.0.1', port=port,
                                    log_level=kwargs['log_level'],
                                    access_log=kwargs['access_log'])
            server = uvicorn.Server(config)
            thread = threading.Thread(target=_control_loop,
                args=(listener, state, server, closing), daemon=True)
            thread.start()
            server.run(sockets=[bound])

        uvicorn.run = run_release
        try:
            # Execute the exact bytes that were verified, without writing a
            # copy of the original launcher or its configuration to disk.
            exec(compile(raw, str(legacy), 'exec'), legacy_globals)
            if not entered:
                raise Refused('原运行配置没有启动预期服务。')
        finally:
            uvicorn.run = original_run
    finally:
        closing.set()
        if listener is not None:
            listener.close()
        if bound is not None:
            bound.close()
        # The directory is 0700 and the exclusive lock is held.  Never remove
        # an entry that this invocation did not create.
        for name, identity in (('control.sock', created_socket), ('active.json', created_state)):
            if identity is None:
                continue
            try:
                info = (directory / name).lstat()
                if (info.st_dev, info.st_ino) == identity:
                    (directory / name).unlink()
            except FileNotFoundError:
                pass
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='本机测试 API 安全启动与停止')
    sub = parser.add_subparsers(dest='command', required=True)
    measure = sub.add_parser('measure')
    measure.add_argument('--release', required=True, type=Path)
    run = sub.add_parser('serve')
    run.add_argument('--release', required=True, type=Path)
    run.add_argument('--legacy-launcher', required=True, type=Path)
    run.add_argument('--legacy-sha256', required=True)
    run.add_argument('--artifact-sha256', required=True)
    run.add_argument('--runtime-root', type=Path)
    run.add_argument('--state-dir', required=True, type=Path)
    run.add_argument('--port', required=True, type=int)
    for action in ('status', 'stop'):
        part = sub.add_parser(action)
        part.add_argument('--state-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'measure':
            print(_artifact_digest(args.release))
        elif args.command == 'serve':
            serve(release=args.release, legacy=args.legacy_launcher,
                  legacy_sha256=args.legacy_sha256, artifact_sha256=args.artifact_sha256,
                  directory=args.state_dir, port=args.port, runtime_root=args.runtime_root)
        else:
            result = _control(_private_directory(args.state_dir), args.command)
            messages = {'ready': '服务已就绪。', 'starting': '服务正在启动。', 'stopping': '服务正在停止。'}
            print('服务已收到停止请求。' if args.command == 'stop' else
                  messages.get(result.get('state'), '服务状态待核对。'))
        return 0
    except Exception:
        # Original local launcher errors can contain credentials or private
        # paths; never render their messages or tracebacks to the console.
        print('操作未完成，请检查运行状态和配置。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
