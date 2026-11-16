"""Private immutable on_response journal, independent of any DB transaction.

This stores gateway callback content, NOT the provider's original HTTP body.
An incomplete or conflicting slot must never trigger automatic paid retry.
The caller supplies a private absolute root; settings/environment are not read.
"""
import codecs
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import digest


MAX_RESPONSE_BYTES = 256 * 1024
_METADATA_BYTES = 8192
_UUID = r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
_SHA = re.compile(r'^[a-f0-9]{64}$')
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_BINDING = 'report-evidence-journal-binding-v1'
_SLOT = 'report-evidence-journal-slot-v1'
_ENVELOPE = 'report-evidence-journal-envelope-v1'
_DESCRIPTOR = 'report-evidence-journal-descriptor-v1'


class JournalError(ValueError):
    """Only a fixed safe code is exposed; no paths, wire, receipt or IO details."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class JournalDurabilityError(JournalError):
    """Not acknowledged durable. Keep the reserved call; do not pay again."""


class JournalBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema: Literal['report-evidence-journal-binding-v1']
    actor_id: Annotated[str, Field(pattern=_UUID)]
    workspace_id: Annotated[str, Field(pattern=_UUID)]
    draft_id: Annotated[str, Field(pattern=_UUID)]
    stage: Annotated[str, Field(pattern=r'^(?:map-(?:[1-9]|1[0-9]|2[0-4])|reduce)$')]
    reserved_call_id: Annotated[str, Field(pattern=_UUID)]
    source_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    catalog_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    generation_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    dispatch_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    messages_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


def _binding(value):
    if type(value) is JournalBinding:
        value = value.model_dump()
    if type(value) is not dict:
        raise JournalError('journal_binding_invalid')
    try:
        return JournalBinding.model_validate(value).model_dump()
    except ValidationError:
        raise JournalError('journal_binding_invalid') from None


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _safe_json(value, depth=0):
    if depth > 48:
        raise ValueError()
    kind = type(value)
    if kind is str:
        value.encode('utf-8', errors='strict')
        if '\x00' in value:
            raise ValueError()
    elif kind is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError()
            _safe_json(key, depth + 1); _safe_json(item, depth + 1)
    elif kind is list:
        for item in value:
            _safe_json(item, depth + 1)
    elif kind is float:
        if not math.isfinite(value):
            raise ValueError()
    elif value is not None and kind not in (int, bool):
        raise ValueError()


def _json_representable(value):
    """Exact JSON-type walk; never invoke unknown repr/pickle/custom encoders."""
    stack, ancestors = [(value, False)], set()
    while stack:
        item, leaving = stack.pop(); kind = type(item)
        if leaving:
            ancestors.remove(id(item)); continue
        if kind in (dict, list):
            if id(item) in ancestors:
                return False
            ancestors.add(id(item)); stack.append((item, True))
            if kind is dict:
                if any(type(key) is not str for key in item):
                    return False
                stack.extend((child, False) for child in item.values())
            else:
                stack.extend((child, False) for child in item)
        elif item is not None and kind not in (str, int, float, bool):
            return False
    return True


def _receipt(receipt):
    if not _json_representable(receipt):
        return 'unavailable', None
    try:
        raw = json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=True).encode('ascii')
    except (ValueError, RecursionError):
        return 'unavailable', None
    try:
        if type(receipt) is not dict:
            raise ValueError()
        _safe_json(receipt)
    except (ValueError, RecursionError):
        return 'invalid_json', raw
    return 'saved', raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _loads(raw):
    def reject(_):
        raise ValueError()
    value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs, parse_constant=reject)
    _safe_json(value)
    return value


def _owned(fd, *, directory):
    info = os.fstat(fd)
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not valid_type or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        raise JournalError('journal_storage_integrity')


def _directory(parent, name, create=False):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent); os.fsync(parent)
        except FileExistsError:
            pass
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
    try:
        _owned(fd, directory=True)
        return fd
    except BaseException:
        os.close(fd); raise


def _root(storage_root, create):
    if not isinstance(storage_root, Path) or not storage_root.is_absolute() or '..' in storage_root.parts or storage_root == Path('/'):
        raise JournalError('journal_root_invalid')
    fd = os.open('/', _DIR_FLAGS)
    try:
        parts = storage_root.parts[1:]
        for number, part in enumerate(parts):
            last = number == len(parts) - 1
            if last:
                child = _directory(fd, part, create)
            else:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd); raise


@contextmanager
def _stage(storage_root, binding, create=False):
    handles = []
    try:
        handles.append(_root(storage_root, create))
        for part in (binding['workspace_id'], binding['draft_id'], binding['stage']):
            handles.append(_directory(handles[-1], part, create))
        yield handles[-1]
    finally:
        for fd in reversed(handles):
            os.close(fd)


def _new_file(directory, name, content):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view[:65536])
            if not written:
                raise OSError()
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
        # Keep even incomplete writes. No retry may overwrite a partial answer.
        os.fsync(directory)


def _read_file(directory, name, maximum=None):
    fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
    try:
        _owned(fd, directory=False)
        if maximum is not None and os.fstat(fd).st_size > maximum:
            raise JournalError('journal_storage_integrity')
        parts = []
        while True:
            part = os.read(fd, 65536)
            if not part:
                return b''.join(parts)
            parts.append(part)
    finally:
        os.close(fd)


def _file_proof(directory, name):
    """Hash full oversized quarantine without loading another copy into RAM."""
    fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
    try:
        _owned(fd, directory=False); sha = hashlib.sha256(); size = 0
        decoder = codecs.getincrementaldecoder('utf-8')('strict'); utf8 = True
        while True:
            part = os.read(fd, 65536)
            if not part:
                break
            sha.update(part); size += len(part)
            if utf8:
                try:
                    decoder.decode(part)
                except UnicodeError:
                    utf8 = False
        if utf8:
            try:
                decoder.decode(b'', final=True)
            except UnicodeError:
                utf8 = False
        return size, sha.hexdigest(), utf8
    finally:
        os.close(fd)


def _slot_value(binding):
    return {'schema': _SLOT, 'binding': binding, 'binding_hash': digest(binding)}


def _check_slot(directory, binding):
    try:
        value = _loads(_read_file(directory, 'slot.json', _METADATA_BYTES))
    except (ValueError, RecursionError):
        raise JournalError('journal_partial_or_corrupt') from None
    if value != _slot_value(binding):
        raise JournalError('journal_slot_conflict')
    if set(os.listdir(directory)) != {'slot.json', binding['reserved_call_id']}:
        raise JournalError('journal_partial_or_corrupt')


def _reasons(wire_bytes, utf8, receipt_status, call_matches):
    result = []
    if wire_bytes > MAX_RESPONSE_BYTES:
        result.append('wire_capacity_exceeded')
    if not utf8:
        result.append('wire_encoding_invalid')
    if receipt_status != 'saved':
        result.append('receipt_invalid')
    elif not call_matches:
        result.append('receipt_call_mismatch')
    return result


def _bundle(directory, binding):
    try:
        raw = _read_file(directory, 'envelope.json', _METADATA_BYTES)
    except FileNotFoundError:
        raise JournalError('journal_partial_or_corrupt') from None
    try:
        value = _loads(raw)
    except (ValueError, RecursionError):
        raise JournalError('journal_partial_or_corrupt') from None
    keys = {'schema', 'binding', 'binding_hash', 'wire_kind', 'encoding', 'wire_bytes', 'wire_sha256',
        'receipt_status', 'receipt_bytes', 'receipt_sha256', 'consumable', 'quarantine_reasons'}
    if (type(value) is not dict or set(value) != keys or value['schema'] != _ENVELOPE
            or value['binding'] != binding or value['binding_hash'] != digest(binding)
            or type(value['wire_kind']) is not str or type(value['encoding']) is not str
            or (value['wire_kind'], value['encoding']) not in {('bytes', 'opaque-bytes-v1'), ('text', 'utf-8-surrogatepass-v1')}
            or type(value['consumable']) is not bool or type(value['wire_bytes']) is not int or value['wire_bytes'] < 0
            or type(value['receipt_bytes']) is not int or value['receipt_bytes'] < 0
            or type(value['wire_sha256']) is not str or not _SHA.fullmatch(value['wire_sha256'])
            or type(value['receipt_status']) is not str or value['receipt_status'] not in {'saved', 'invalid_json', 'unavailable'}):
        raise JournalError('journal_storage_integrity')
    size, sha, utf8 = _file_proof(directory, 'wire.bin')
    if size != value['wire_bytes'] or sha != value['wire_sha256']:
        raise JournalError('journal_storage_integrity')
    files = {'wire.bin', 'envelope.json'}; call_matches = False
    if value['receipt_status'] != 'unavailable':
        files.add('receipt.bin'); size, sha, _ = _file_proof(directory, 'receipt.bin')
        if size != value['receipt_bytes'] or sha != value['receipt_sha256']:
            raise JournalError('journal_storage_integrity')
        if value['receipt_status'] == 'saved':
            try:
                receipt_raw = _read_file(directory, 'receipt.bin')
                if len(receipt_raw) != value['receipt_bytes'] or hashlib.sha256(receipt_raw).hexdigest() != value['receipt_sha256']:
                    raise ValueError()
                receipt = _loads(receipt_raw)
                if type(receipt) is not dict:
                    raise ValueError()
                call_matches = receipt.get('call_id') == binding['reserved_call_id']
            except (ValueError, RecursionError):
                raise JournalError('journal_storage_integrity') from None
    elif value['receipt_bytes'] != 0 or value['receipt_sha256'] is not None:
        raise JournalError('journal_storage_integrity')
    reasons = _reasons(value['wire_bytes'], utf8, value['receipt_status'], call_matches)
    if set(os.listdir(directory)) != files or value['quarantine_reasons'] != reasons or value['consumable'] is not (not reasons):
        raise JournalError('journal_storage_integrity')
    descriptor = {**value, 'schema': _DESCRIPTOR, 'envelope_bytes': len(raw), 'envelope_sha256': hashlib.sha256(raw).hexdigest()}
    return {**descriptor, 'descriptor_hash': digest(descriptor)}


def write_response(storage_root, binding, wire, receipt):
    """Durably store callback content first, then receipt, then complete metadata.

    Receipt/schema/JSON parsing never precede the full wire fsync. Oversized or
    unsafe answers return a durable non-consumable descriptor, never truncation.
    """
    binding = _binding(binding)
    if type(wire) is str:
        content = wire.encode('utf-8', errors='surrogatepass'); kind, encoding = 'text', 'utf-8-surrogatepass-v1'
    elif type(wire) is bytes:
        content = wire; kind, encoding = 'bytes', 'opaque-bytes-v1'
    else:
        raise JournalError('journal_content_type_invalid')
    try:
        with _stage(storage_root, binding, True) as stage:
            try:
                _new_file(stage, 'slot.json', _json_bytes(_slot_value(binding))); fresh = True
            except FileExistsError:
                fresh = False
            if not fresh:
                _check_slot(stage, binding)
                directory = _directory(stage, binding['reserved_call_id'])
                try:
                    descriptor = _bundle(directory, binding); status, receipt_bytes = _receipt(receipt)
                    if (descriptor['wire_kind'] != kind or descriptor['encoding'] != encoding
                            or descriptor['wire_bytes'] != len(content)
                            or descriptor['wire_sha256'] != hashlib.sha256(content).hexdigest()
                            or status == 'unavailable' or status != descriptor['receipt_status']
                            or hashlib.sha256(receipt_bytes).hexdigest() != descriptor['receipt_sha256']):
                        raise JournalError('journal_response_conflict')
                    return descriptor
                finally:
                    os.close(directory)
            directory = _directory(stage, binding['reserved_call_id'], True)
            try:
                if os.listdir(directory):
                    raise JournalError('journal_partial_or_corrupt')
                _new_file(directory, 'wire.bin', content)
                status, receipt_bytes = _receipt(receipt)
                if receipt_bytes is not None:
                    _new_file(directory, 'receipt.bin', receipt_bytes)
                _, _, utf8 = _file_proof(directory, 'wire.bin')
                reasons = _reasons(len(content), utf8, status,
                    status == 'saved' and receipt.get('call_id') == binding['reserved_call_id'])
                envelope = {'schema': _ENVELOPE, 'binding': binding, 'binding_hash': digest(binding),
                    'wire_kind': kind, 'encoding': encoding, 'wire_bytes': len(content),
                    'wire_sha256': hashlib.sha256(content).hexdigest(), 'receipt_status': status,
                    'receipt_bytes': 0 if receipt_bytes is None else len(receipt_bytes),
                    'receipt_sha256': None if receipt_bytes is None else hashlib.sha256(receipt_bytes).hexdigest(),
                    'consumable': not reasons, 'quarantine_reasons': reasons}
                _new_file(directory, 'envelope.json', _json_bytes(envelope))
                return _bundle(directory, binding)
            finally:
                os.close(directory)
    except OSError:
        raise JournalDurabilityError('journal_not_durable_no_paid_retry') from None


def read_descriptor(storage_root, binding):
    binding = _binding(binding)
    try:
        with _stage(storage_root, binding) as stage:
            _check_slot(stage, binding); directory = _directory(stage, binding['reserved_call_id'])
            try:
                return _bundle(directory, binding)
            finally:
                os.close(directory)
    except OSError:
        raise JournalError('journal_missing_or_unsafe') from None


def read_response(storage_root, binding, expected_descriptor=None):
    binding = _binding(binding)
    try:
        with _stage(storage_root, binding) as stage:
            _check_slot(stage, binding); directory = _directory(stage, binding['reserved_call_id'])
            try:
                descriptor = _bundle(directory, binding)
                if expected_descriptor is not None and descriptor != expected_descriptor:
                    raise JournalError('journal_descriptor_conflict')
                if not descriptor['consumable']:
                    raise JournalError('journal_quarantined')
                wire = _read_file(directory, 'wire.bin', MAX_RESPONSE_BYTES)
                receipt_raw = _read_file(directory, 'receipt.bin')
                if (len(wire) != descriptor['wire_bytes'] or hashlib.sha256(wire).hexdigest() != descriptor['wire_sha256']
                        or len(receipt_raw) != descriptor['receipt_bytes']
                        or hashlib.sha256(receipt_raw).hexdigest() != descriptor['receipt_sha256']):
                    raise JournalError('journal_storage_integrity')
                content = wire.decode('utf-8') if descriptor['wire_kind'] == 'text' else wire
                return {'descriptor': descriptor, 'content': content, 'receipt': _loads(receipt_raw)}
            finally:
                os.close(directory)
    except OSError:
        raise JournalError('journal_missing_or_unsafe') from None


def parse_response(storage_root, binding, expected_descriptor=None):
    """Strict JSON only after persistence; return any JSON value without repair.

    The caller still applies MapResult/schema and semantic validation. Failures
    before gateway on_response remain unknown and outside journal durability.
    """
    response = read_response(storage_root, binding, expected_descriptor)
    content = response['content']; raw = content.encode('utf-8') if type(content) is str else content
    try:
        value = _loads(raw)
    except (ValueError, RecursionError):
        raise JournalError('journal_response_not_json') from None
    return {'descriptor': response['descriptor'], 'receipt': response['receipt'], 'value': value}
