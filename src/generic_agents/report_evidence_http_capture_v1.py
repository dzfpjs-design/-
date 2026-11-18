"""UNINTEGRATED private FS capture proposal: no HTTP, DB, model parsing or grant.

A caller must first verify the paid reservation and frozen request. This layer
only binds and saves bytes. A stage and a call UUID are each exclusively claimed
before a sink is returned. Failed/partial claims are never deleted or reused.
Only explicit EOF may seal a complete capture. Even ready captures cannot adopt
facts: HTTP/envelope/schema/source/role/fee checks belong to the future caller.

load/read_body are read-only and never repair metadata or promote a journal.
Permissions and hashes protect local storage discipline, not against a malicious
process with the same OS account. No clock, permissions, budget or byte hash here
authorizes a provider request. The author did not execute this proposal.
"""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat


ENDPOINT = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'
DELIVERY = 'qwen-tokenplan-native-json-schema-v1'
CHUNK_LIMIT = 64 * 1024
MAP_LIMIT = 256 * 1024
REDUCE_LIMIT = 1024 * 1024
_HASH = re.compile(r'^[a-f0-9]{64}$')
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ = os.O_RDONLY | os.O_NOFOLLOW
_REASONS = {'interrupted', 'transport_error', 'deadline', 'cancelled', 'io_error',
    'capacity_exceeded', 'chunk_limit', 'unexpected_content_encoding'}
_FILES = {'slot.json', 'body.bin', 'started.json', 'headers.json', 'end.json'}


class CaptureError(ValueError):
    """Expose fixed codes only, without paths, response text or credentials."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise CaptureError(code)


def _closed(value, keys):
    return type(value) is dict and set(value) == set(keys.split())


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('ascii')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _hash(_json(value))


def _context(value):
    _need(_closed(value, 'binding request_hash endpoint delivery schema_proof_hash profile_hash token_bound max_bytes'), 'capture_context_invalid')
    binding = value['binding']
    _need(type(binding) is dict, 'capture_context_invalid')
    if binding.get('stage') == 'reduce':
        from generic_agents.report_evidence_journal_v2 import JournalBinding
        limit = REDUCE_LIMIT
    else:
        from generic_agents.report_evidence_journal import JournalBinding
        limit = MAP_LIMIT
    try:
        checked = JournalBinding.model_validate(binding).model_dump()
    except ValueError:
        raise CaptureError('capture_context_invalid') from None
    _need(_json(checked) == _json(binding), 'capture_context_invalid')
    known_transport = (value['endpoint'] == ENDPOINT and value['delivery'] == DELIVERY)
    if value['delivery'] == 'qwen-public-claim-native-v168':
        known_transport = value['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    elif value['delivery'] == 'qwen-public-claim-enum-native-v172':
        known_transport = value['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    elif value['delivery'] == 'qwen-public-saved-map-design-native-v176':
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
    elif value['delivery'] == 'qwen-public-catalogue-allocation-native-v213':
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
    elif value['delivery'] == 'qwen-public-business-catalogue-native-v211':
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
    elif value['delivery'] in {'qwen-public-indexed-design-native-v178','qwen-public-indexed-design-native-v179','qwen-public-scoped-design-native-v181','qwen-public-business-flow-native-v182','qwen-public-business-flow-native-v183','qwen-public-business-flow-native-v184','qwen-public-business-flow-native-v185'}:
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
    elif value['delivery'] == 'deepseek-public-business-flow-native-v187':
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://api.deepseek.com/chat/completions')
    elif value['delivery'] == 'deepseek-public-business-flow-native-v190':
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://api.deepseek.com/chat/completions')
    elif value['delivery'] == 'deepseek-public-business-flow-native-v193':
        known_transport = (binding['stage'] == 'reduce'
            and value['endpoint'] == 'https://api.deepseek.com/chat/completions')
    elif value['delivery'] == 'deepseek-delegated-evidence-json-v1':
        known_transport = (value['endpoint'] == 'https://api.deepseek.com/chat/completions'
            and (binding['stage'] == 'reduce' or re.fullmatch(r'map-(?:[1-9]|1[0-9]|2[0-4])', binding['stage'])))
    _need(known_transport
        and all(type(value[key]) is str and _HASH.fullmatch(value[key])
            for key in ('request_hash', 'schema_proof_hash', 'profile_hash'))
        and type(value['token_bound']) is int and 1 <= value['token_bound'] <= 1000000
        and type(value['max_bytes']) is int and value['max_bytes'] == limit, 'capture_context_invalid')
    return copy.deepcopy(value)


def _owned(fd, directory=False):
    info = os.fstat(fd)
    _need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
        and (directory or info.st_nlink == 1), 'capture_storage_unsafe')
    return info


def _directory(parent, name, create=False, exclusive=False):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            if exclusive:
                raise CaptureError('capture_stage_already_claimed') from None
    fd = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        _owned(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _root(root, create=False):
    _need(type(root) is Path or isinstance(root, Path), 'capture_root_invalid')
    _need(root.is_absolute() and '..' not in root.parts and root != Path('/'), 'capture_root_invalid')
    fd = os.open('/', _DIRECTORY)
    try:
        for position, part in enumerate(root.parts[1:]):
            last = position == len(root.parts) - 2
            child = _directory(fd, part, create) if last else os.open(part, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_all(fd, raw):
    view = memoryview(raw)
    while view:
        written = os.write(fd, view[:CHUNK_LIMIT])
        if not written:
            raise OSError()
        view = view[written:]


def _new(directory, name, raw):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=directory)
    try:
        _owned(fd)
        _write_all(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
        os.fsync(directory)


def _read(directory, name, maximum):
    fd = os.open(name, _READ, dir_fd=directory)
    try:
        before = _owned(fd)
        _need(before.st_size <= maximum, 'capture_storage_unsafe')
        parts, size = [], 0
        while True:
            chunk = os.read(fd, min(CHUNK_LIMIT, maximum + 1 - size))
            if not chunk:
                break
            parts.append(chunk)
            size += len(chunk)
            _need(size <= maximum, 'capture_storage_unsafe')
        after = _owned(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        _need(identity(before) == identity(after) and size == after.st_size, 'capture_changed')
        return b''.join(parts)
    finally:
        os.close(fd)


def _metadata(directory, name):
    raw = _read(directory, name, 16384)
    def pairs(items):
        value = {}
        for key, child in items:
            _need(key not in value, 'capture_metadata_invalid')
            value[key] = child
        return value
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(CaptureError('capture_metadata_invalid')))
        _need(_json(value) == raw, 'capture_metadata_invalid')
        return value
    except (ValueError, UnicodeError, json.JSONDecodeError, RecursionError, TypeError):
        raise CaptureError('capture_metadata_invalid') from None


def _slot(context):
    return {'schema': 'report-evidence-http-capture-slot-v1', 'context': context,
        'context_hash': _digest(context)}


def _claim(root_fd, context, create=False):
    directory = _directory(root_fd, '_calls', create)
    try:
        name = context['binding']['reserved_call_id'] + '.json'
        if create:
            try:
                _new(directory, name, _json(_slot(context)))
            except FileExistsError:
                raise CaptureError('capture_call_already_claimed') from None
        _need(_metadata(directory, name) == _slot(context), 'capture_context_mismatch')
    finally:
        os.close(directory)


@contextmanager
def _stage(root, context):
    handles = []
    try:
        handles.append(_root(root))
        binding = context['binding']
        for part in (binding['workspace_id'], binding['draft_id'], binding['stage']):
            handles.append(_directory(handles[-1], part))
        try:
            _claim(handles[0], context)
        except FileNotFoundError:
            raise CaptureError('capture_context_mismatch') from None
        yield handles[-1]
    finally:
        for fd in reversed(handles):
            os.close(fd)


@contextmanager
def _lock(fd, exclusive):
    try:
        # A brief bounded internal read must not make a paid writer lose bytes.
        # Readers never wait on the writer; caller owns the transport deadline.
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        raise CaptureError('capture_busy') from None
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _safe_header(value):
    safe = value is None or (type(value) is str and len(value) <= 256
        and all(32 <= ord(char) <= 126 for char in value))
    return value if safe else None, safe


def _headers(context, status, encoding, request_id):
    _need(type(status) is int and 100 <= status <= 599, 'capture_status_invalid')
    text, safe = _safe_header(encoding)
    identifier, id_safe = _safe_header(request_id)
    return {'schema': 'report-evidence-http-capture-headers-v1', 'context_hash': _digest(context),
        'http_status': status, 'http_content_encoding': text, 'encoding_safe': safe,
        'original_encoding_sha256': _hash(encoding.encode('utf-8', 'surrogatepass')) if type(encoding) is str else None,
        'request_id': identifier, 'request_id_safe': id_safe}


def _supported(headers):
    value = headers['http_content_encoding']
    return headers['encoding_safe'] and (value is None or value.strip().lower() in ('', 'identity'))


def _read_headers(directory, context):
    value = _metadata(directory, 'headers.json')
    _need(_closed(value, 'schema context_hash http_status http_content_encoding encoding_safe original_encoding_sha256 request_id request_id_safe')
        and value['schema'] == 'report-evidence-http-capture-headers-v1'
        and value['context_hash'] == _digest(context)
        and type(value['http_status']) is int and 100 <= value['http_status'] <= 599
        and type(value['encoding_safe']) is bool and type(value['request_id_safe']) is bool,
        'capture_metadata_invalid')
    for field, flag in (('http_content_encoding', 'encoding_safe'), ('request_id', 'request_id_safe')):
        text, safe = _safe_header(value[field])
        _need(safe and text == value[field] and (value[flag] or text is None), 'capture_metadata_invalid')
    original = value['original_encoding_sha256']
    _need(original is None or type(original) is str and _HASH.fullmatch(original), 'capture_metadata_invalid')
    if value['encoding_safe']:
        expected = None if value['http_content_encoding'] is None else _hash(value['http_content_encoding'].encode())
        _need(original == expected, 'capture_metadata_invalid')
    return value


def _dto(context, *, state, started=False, headers=None, size=0, body_hash=None,
         complete=False, observed=None, omitted=None, prefix=False, reason=None):
    value = {'schema': 'report-evidence-http-capture-state-v1', 'context_hash': _digest(context),
        'state': state, 'request_started': started, 'headers_saved': headers is not None,
        'http_status': None if headers is None else headers['http_status'],
        'http_content_encoding': None if headers is None else headers['http_content_encoding'],
        'content_encoding_safe': None if headers is None else headers['encoding_safe'],
        'request_id': None if headers is None else headers['request_id'],
        'request_id_safe': None if headers is None else headers['request_id_safe'],
        'headers_hash': None if headers is None else _digest(headers),
        'body_bytes': size, 'body_sha256': body_hash, 'response_complete': complete,
        'observed_bytes': observed, 'omitted_bytes': omitted, 'prefix_only': prefix,
        'reason': reason, 'ready': state == 'complete', 'can_adopt': False}
    return {**value, 'capture_hash': _digest(value)}


def _load(directory, context):
    files = set(os.listdir(directory))
    _need(files <= _FILES, 'capture_storage_unsafe')
    if not {'slot.json', 'body.bin'} <= files:
        return _dto(context, state='quarantined', reason='capture_prepare_incomplete')
    _need(_metadata(directory, 'slot.json') == _slot(context), 'capture_context_mismatch')
    raw = _read(directory, 'body.bin', context['max_bytes'] + 1)
    size, body_hash = len(raw), _hash(raw)
    started = 'started.json' in files
    if started:
        _need(_metadata(directory, 'started.json') == {'schema': 'report-evidence-http-capture-started-v1',
            'context_hash': _digest(context)}, 'capture_metadata_invalid')
    headers = _read_headers(directory, context) if 'headers.json' in files else None
    _need(headers is None or started, 'capture_metadata_invalid')
    _need(not size or started and headers is not None, 'capture_metadata_invalid')
    if 'end.json' not in files:
        return _dto(context, state='quarantined' if size > context['max_bytes'] else 'partial',
            started=started, headers=headers, size=size, body_hash=body_hash,
            observed=size, prefix=size > context['max_bytes'],
            reason='capacity_exceeded' if size > context['max_bytes'] else None)
    end = _metadata(directory, 'end.json')
    _need(_closed(end, 'schema context_hash headers_hash body_bytes body_sha256 response_complete observed_bytes omitted_bytes reason')
        and end['schema'] == 'report-evidence-http-capture-end-v1' and end['context_hash'] == _digest(context)
        and end['headers_hash'] == (None if headers is None else _digest(headers))
        and type(end['body_bytes']) is int and end['body_bytes'] == size and end['body_sha256'] == body_hash
        and type(end['response_complete']) is bool and type(end['observed_bytes']) is int
        and end['observed_bytes'] >= size and type(end['omitted_bytes']) is int
        and end['omitted_bytes'] == end['observed_bytes'] - size
        and (end['reason'] is None or type(end['reason']) is str and end['reason'] in _REASONS), 'capture_metadata_invalid')
    complete = end['response_complete']
    prefix = size > context['max_bytes'] or end['omitted_bytes'] > 0
    if complete:
        _need(started and headers is not None and not prefix
            and end['reason'] == (None if _supported(headers) else 'unexpected_content_encoding'), 'capture_metadata_invalid')
        state = 'complete' if _supported(headers) else 'quarantined'
    else:
        _need(end['reason'] in _REASONS, 'capture_metadata_invalid')
        state = 'quarantined' if prefix or end['reason'] in {'capacity_exceeded', 'chunk_limit', 'io_error'} else 'partial'
    return _dto(context, state=state, started=started, headers=headers, size=size, body_hash=body_hash,
        complete=complete, observed=end['observed_bytes'], omitted=end['omitted_bytes'],
        prefix=prefix, reason=end['reason'])


def load(storage_root, context):
    """Read a bounded status only. Missing does not authorize preparing a retry."""
    context = _context(context)
    try:
        with _stage(storage_root, context) as directory:
            try:
                fd = os.open('body.bin', _READ, dir_fd=directory)
            except FileNotFoundError:
                return _load(directory, context)
            try:
                _owned(fd)
                with _lock(fd, False):
                    return _load(directory, context)
            finally:
                os.close(fd)
    except FileNotFoundError:
        return _dto(context, state='missing')
    except OSError:
        raise CaptureError('capture_storage_unsafe') from None


def read_body(storage_root, context, expected_capture_hash):
    """Read original bytes with an exact fresh status fence, including partials.

    Partial/quarantined bytes are for trace visibility only; can_adopt stays false.
    A concurrent append or terminal change rejects the stale read, without writes.
    """
    context = _context(context)
    _need(type(expected_capture_hash) is str and _HASH.fullmatch(expected_capture_hash), 'capture_status_hash_invalid')
    before = load(storage_root, context)
    _need(before['state'] != 'missing' and before['body_sha256'] is not None
        and before['capture_hash'] == expected_capture_hash, 'capture_changed')
    try:
        with _stage(storage_root, context) as directory:
            raw = _read(directory, 'body.bin', context['max_bytes'] + 1)
        _need(len(raw) == before['body_bytes'] and _hash(raw) == before['body_sha256']
            and load(storage_root, context) == before, 'capture_changed')
        return raw
    except OSError:
        raise CaptureError('capture_storage_unsafe') from None


class NativeHttpCapture:
    """Typed FS-only sink returned by prepare; close never invents EOF."""
    def __init__(self, *_args, **_kwargs):
        raise CaptureError('capture_use_prepare')

    @property
    def context(self):
        return copy.deepcopy(self._context)

    def _active(self):
        _need(not self._closed and not self._poisoned, 'capture_sink_closed_or_poisoned')
        info = _owned(self._body)
        current = os.stat('body.bin', dir_fd=self._directory, follow_symlinks=False)
        _need((info.st_dev, info.st_ino) == self._body_identity
            and (current.st_dev, current.st_ino) == self._body_identity
            and _metadata(self._directory, 'slot.json') == _slot(self._context), 'capture_storage_unsafe')
        _need('end.json' not in os.listdir(self._directory), 'capture_already_ended')

    @contextmanager
    def _writing(self):
        try:
            _need(not self._closed, 'capture_sink_closed_or_poisoned')
            with _lock(self._body, True):
                self._active()
                yield
        except OSError:
            self._poisoned = True
            raise CaptureError('capture_not_durable') from None

    def mark_request_started(self):
        with self._writing():
            _new(self._directory, 'started.json', _json({'schema': 'report-evidence-http-capture-started-v1',
                'context_hash': _digest(self._context)}))

    def begin_response(self, status, content_encoding, request_id):
        with self._writing():
            _need('started.json' in os.listdir(self._directory), 'capture_request_not_started')
            _new(self._directory, 'headers.json', _json(_headers(self._context, status, content_encoding, request_id)))

    def _seal(self, complete, reason, observed=None):
        raw = _read(self._directory, 'body.bin', self._context['max_bytes'] + 1)
        files = set(os.listdir(self._directory))
        headers = _read_headers(self._directory, self._context) if 'headers.json' in files else None
        if complete:
            _need('started.json' in files and headers is not None, 'capture_response_not_started')
            _need(len(raw) <= self._context['max_bytes'], 'capture_capacity_exceeded')
            reason = None if _supported(headers) else 'unexpected_content_encoding'
        observed = len(raw) if observed is None else observed
        _new(self._directory, 'end.json', _json({'schema': 'report-evidence-http-capture-end-v1',
            'context_hash': _digest(self._context), 'headers_hash': None if headers is None else _digest(headers),
            'body_bytes': len(raw), 'body_sha256': _hash(raw), 'response_complete': complete,
            'observed_bytes': observed, 'omitted_bytes': observed - len(raw), 'reason': reason}))

    def append(self, rawbytes):
        _need(type(rawbytes) is bytes, 'capture_chunk_invalid')
        with self._writing():
            _need('headers.json' in os.listdir(self._directory), 'capture_response_not_started')
            size = os.fstat(self._body).st_size
            room = max(0, self._context['max_bytes'] + 1 - size)
            saved = rawbytes[:min(room, CHUNK_LIMIT)]
            _write_all(self._body, saved)
            os.fsync(self._body)
            os.fsync(self._directory)
            if len(rawbytes) > CHUNK_LIMIT or size + len(rawbytes) > self._context['max_bytes']:
                reason = 'chunk_limit' if len(rawbytes) > CHUNK_LIMIT else 'capacity_exceeded'
                self._seal(False, reason, size + len(rawbytes))
                raise CaptureError('capture_' + reason)

    def finish(self, *, eof):
        _need(type(eof) is bool and eof is True, 'capture_eof_required')
        with self._writing():
            # Final body fsync precedes immutable complete metadata and dir fsync.
            os.fsync(self._body)
            os.fsync(self._directory)
            self._seal(True, None)
        return load(self._root, self._context)

    def abort(self, reason):
        _need(type(reason) is str and reason in _REASONS - {'unexpected_content_encoding'}, 'capture_abort_reason_invalid')
        with self._writing():
            os.fsync(self._body)
            os.fsync(self._directory)
            self._seal(False, reason)
        return load(self._root, self._context)

    def close(self):
        if not self._closed:
            self._closed = True
            os.close(self._body)
            os.close(self._directory)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


def prepare(storage_root, context):
    """Claim one new call and one new stage. Any failure preserves all claims."""
    context = _context(context)
    handles, body = [], None
    try:
        handles.append(_root(storage_root, True))
        _claim(handles[0], context, True)
        binding = context['binding']
        for position, part in enumerate((binding['workspace_id'], binding['draft_id'], binding['stage'])):
            handles.append(_directory(handles[-1], part, True, exclusive=position == 2))
        directory = handles[-1]
        _new(directory, 'slot.json', _json(_slot(context)))
        _new(directory, 'body.bin', b'')
        body = os.open('body.bin', os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=directory)
        info = _owned(body)
        sink = object.__new__(NativeHttpCapture)
        sink._root, sink._context = storage_root, context
        sink._directory, sink._body = os.dup(directory), body
        sink._body_identity = (info.st_dev, info.st_ino)
        sink._closed, sink._poisoned = False, False
        body = None
        return sink
    except OSError:
        raise CaptureError('capture_not_durable') from None
    finally:
        if body is not None:
            os.close(body)
        for fd in reversed(handles):
            os.close(fd)
