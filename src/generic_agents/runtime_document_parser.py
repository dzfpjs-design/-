"""Bounded local subprocess for the existing, inert native document reader.

The subprocess receives no application secrets/proxies and rejects Python
network/process audit events. This is not a kernel sandbox; macOS has no claimed
address-space hard limit. Original bytes and execution authority stay upstream.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time

from .document_parsers import MAX_BLOCKS, MAX_CHARACTERS, ParseProblem

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_WALL_SECONDS = 60
GUARD_INTERVAL_SECONDS = 0.5
_MAX_HEADER_BYTES = 2048
_ENGINES = {'.pdf': 'native-pdf-v1', '.docx': 'native-docx-v1', '.txt': 'native-text-v1', '.md': 'native-text-v1'}
_GENERIC = '本地文档解析未完成；原件已保留，请核对格式或拆分资料后重试'
_SAFE_ERRORS = frozenset({_GENERIC, '压缩文件目录异常或条目过多', '文件加密或解压体积超过上限',
    '文件压缩比异常，请拆分后重新上传', 'Office文件损坏或格式不匹配',
    '正文或表格超过解析上限，请拆分资料；原件已保留', '文本不是UTF-8，请转换编码后重新上传',
    '文本包含二进制内容', 'DOCX正文损坏或含不安全XML', 'DOCX缺少正文', 'PDF格式不匹配',
    'PDF已加密，请上传可读取副本', 'PDF超过500页，请拆分资料', 'PDF损坏或无法读取'})
_BOOTSTRAP = ("import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
    "runpy.run_module('generic_agents.runtime_document_parser',run_name='__main__')")


def _need(ok, message=_GENERIC):
    if not ok: raise ParseProblem(message)


def _input(content, name):
    _need(type(content) is bytes and 0 < len(content) <= MAX_INPUT_BYTES,
        '资料为空或超过20MiB；原件保留，请拆分资料后重试')
    _need(type(name) is str and 0 < len(name) <= 250 and '/' not in name and '\\' not in name
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
        and Path(name).suffix.lower() in _ENGINES, '请选择PDF、DOCX、TXT或MD原始资料')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result: raise ValueError('duplicate protocol key')
        result[key] = value
    return result


def _result(value, name):
    _need(type(value) is dict and set(value) == {'status', 'blocks', 'engine', 'warnings'})
    suffix = Path(name).suffix.lower()
    _need(value['status'] in {'parsed', 'needs_input'} and value['engine'] == _ENGINES[suffix]
        and type(value['blocks']) is list and len(value['blocks']) <= MAX_BLOCKS
        and type(value['warnings']) is list)
    _need(all(type(warning) is str for warning in value['warnings'])
        and sum(len(warning) for warning in value['warnings']) <= 16384)
    characters = 0
    for index, block in enumerate(value['blocks'], 1):
        _need(type(block) is dict and set(block) == {'id', 'text', 'kind', 'location'}
            and block['id'] == 'b' + str(index) and type(block['text']) is str
            and bool(block['text']) and block['text'].strip() == block['text'])
        characters += len(block['text']); _need(characters <= MAX_CHARACTERS)
        location = block['location']; _need(type(location) is dict)
        if suffix == '.pdf':
            _need(block['kind'] == 'page_text' and set(location) == {'page'}
                and type(location['page']) is int and 1 <= location['page'] <= 500)
        elif suffix == '.docx':
            _need(block['kind'] in {'paragraph', 'table_row'}
                and set(location) == ({'body_index', 'row'} if block['kind'] == 'table_row' else {'body_index'}))
            _need(all(type(number) is int and number >= 1 for number in location.values()))
        else:
            _need(block['kind'] == 'paragraph' and set(location) == {'line'}
                and type(location['line']) is int and location['line'] >= 1)
    _need(value['status'] != 'parsed' or bool(value['blocks']))
    return value


def _decode(raw, name, returncode):
    _need(0 < len(raw) <= MAX_OUTPUT_BYTES)
    try: envelope = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError): raise ParseProblem(_GENERIC) from None
    _need(type(envelope) is dict and type(envelope.get('ok')) is bool)
    if envelope['ok'] is False:
        _need(set(envelope) == {'ok', 'error'} and envelope['error'] in _SAFE_ERRORS)
        raise ParseProblem(envelope['error'])
    _need(returncode == 0 and set(envelope) == {'ok', 'result'})
    return _result(envelope['result'], name)


def _environment(folder):
    return {'PATH': '/usr/bin:/bin', 'HOME': folder, 'TMPDIR': folder, 'LANG': 'C', 'LC_ALL': 'C'}


def _stop(process):
    if process is None: return True
    try:
        if os.name == 'posix': os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None: process.kill()
    except (ProcessLookupError, OSError): pass
    try: process.wait(timeout=5); return True
    except (OSError, subprocess.TimeoutExpired):
        try: process.kill(); process.wait(timeout=5); return True
        except (OSError, subprocess.TimeoutExpired): return False


def parse_runtime_document(content, name, *, guard=None):
    """Return the existing parser DTO; guard errors propagate without rewriting.

    ``guard`` is server-owned current-authority/cancellation checking. It must
    not retain a transaction lock while the parser runs; no caller executable,
    module, environment, path or timeout may be supplied through this interface.
    """
    _input(content, name)
    guard_error = None
    def check():
        nonlocal guard_error
        if guard is not None:
            try: guard()
            except BaseException as error:
                guard_error = error
                raise
    try:
        with tempfile.TemporaryDirectory(prefix='ga-runtime-document-', ignore_cleanup_errors=True) as folder:
            os.chmod(folder, 0o700)
            metadata = json.dumps({'name': name, 'size': len(content)}, ensure_ascii=False).encode('utf-8')
            _need(len(metadata) <= _MAX_HEADER_BYTES)
            source = Path(folder) / 'input'; output = Path(folder) / 'output'
            source.write_bytes(struct.pack('!I', len(metadata)) + metadata + content)
            os.chmod(source, 0o600)
            process = None
            with source.open('rb') as incoming, output.open('w+b') as outgoing:
                os.chmod(output, 0o600)
                try:
                    check()
                    command = [sys.executable, '-I', '-B', '-X', 'utf8', '-c', _BOOTSTRAP,
                        str(Path(__file__).resolve().parent.parent), '--child']
                    process = subprocess.Popen(command, stdin=incoming, stdout=outgoing, stderr=subprocess.DEVNULL,
                        shell=False, cwd=folder, env=_environment(folder), start_new_session=True, close_fds=True)
                    started = time.monotonic(); next_guard = started
                    while True:
                        now = time.monotonic()
                        if now >= next_guard: check(); next_guard = now + GUARD_INTERVAL_SECONDS
                        _need(now - started <= MAX_WALL_SECONDS,
                            '本地文档解析超过60秒；原件已保留，请拆分资料后明确重试')
                        _need(os.fstat(outgoing.fileno()).st_size <= MAX_OUTPUT_BYTES,
                            '本地解析结果超过上限；原件已保留，请拆分资料')
                        if process.poll() is not None: break
                        time.sleep(0.05)
                    check()
                    outgoing.seek(0)
                    return _decode(outgoing.read(MAX_OUTPUT_BYTES + 1), name, process.returncode)
                finally:
                    stopped = _stop(process)
                    if not stopped and sys.exc_info()[0] is None:
                        raise ParseProblem('本地解析进程未能核对退出；原件保留，请管理员检查后台任务')
    except Exception as error:
        if error is guard_error or isinstance(error, ParseProblem): raise
        raise ParseProblem(_GENERIC) from None


def _limits():
    if os.name != 'posix': return
    import resource
    def cap(kind, limit):
        soft, hard = resource.getrlimit(kind)
        ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
        resource.setrlimit(kind, (ceiling if soft == resource.RLIM_INFINITY else min(soft, ceiling), ceiling))
    cap(resource.RLIMIT_CPU, 50)
    cap(resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES)
    cap(resource.RLIMIT_CORE, 0)
    cap(resource.RLIMIT_NOFILE, 128)
    if sys.platform.startswith('linux'): cap(resource.RLIMIT_AS, 1024 * 1024 * 1024)


def _inert(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn',
            'os.fork', 'os.forkpty', 'os.exec', 'os.spawn'}:
        raise PermissionError('Native parser cannot access network or launch another process')


def _child_main():
    try:
        os.environ.clear()
        _limits()
        sys.addaudithook(_inert)
        header = sys.stdin.buffer.read(4)
        _need(len(header) == 4)
        size = struct.unpack('!I', header)[0]; _need(0 < size <= _MAX_HEADER_BYTES)
        metadata = json.loads(sys.stdin.buffer.read(size), object_pairs_hook=_pairs)
        _need(type(metadata) is dict and set(metadata) == {'name', 'size'}
            and type(metadata['size']) is int and 0 < metadata['size'] <= MAX_INPUT_BYTES)
        content = sys.stdin.buffer.read(metadata['size'] + 1)
        _need(len(content) == metadata['size']); _input(content, metadata['name'])
        from .document_parsers import parse_document
        result = _result(parse_document(content, metadata['name']), metadata['name'])
        envelope = {'ok': True, 'result': result}
    except Exception as error:
        message = str(error) if isinstance(error, ParseProblem) and str(error) in _SAFE_ERRORS else _GENERIC
        envelope = {'ok': False, 'error': message}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_OUTPUT_BYTES:
        encoded = json.dumps({'ok': False, 'error': _GENERIC}, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(encoded); sys.stdout.buffer.flush()
    return 0 if envelope['ok'] else 1


if __name__ == '__main__':
    if sys.argv[1:] != ['--child']: raise SystemExit(2)
    raise SystemExit(_child_main())
