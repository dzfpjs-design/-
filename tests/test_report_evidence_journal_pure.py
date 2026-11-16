"""Private synthetic temp files only; root executes, no DB/provider/runtime IO."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
from uuid import UUID

import pytest

from generic_agents import report_evidence_journal as journal


def identity(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def binding(stage='map-1', call=4):
    return {'schema': 'report-evidence-journal-binding-v1', 'actor_id': identity(1),
        'workspace_id': identity(2), 'draft_id': identity(3), 'stage': stage,
        'reserved_call_id': identity(call), 'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64,
        'generation_hash': 'c' * 64, 'dispatch_hash': 'd' * 64, 'messages_hash': 'e' * 64}


def receipt(bound):
    return {'provider': 'synthetic-only', 'model': 'no-provider-called', 'call_id': bound['reserved_call_id'],
        'request_id': None, 'usage': {'prompt_tokens': 7, 'completion_tokens': 9, 'total_tokens': 16},
        'extra_field_not_dropped': {'amount': 1.0, 'flags': [True, False, None], 'unicode': '原字段🙂'}}


def location(root, bound):
    return root / bound['workspace_id'] / bound['draft_id'] / bound['stage'] / bound['reserved_call_id']


def files(root):
    return {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_mtime_ns)
        for path in root.rglob('*') if path.is_file()}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('journal tests cannot connect or launch anything')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def root(tmp_path):
    # Product traversal rejects symlink ancestors, including platform /tmp aliases.
    return tmp_path.resolve() / 'private-journal'


@pytest.mark.parametrize('wire', [' {"value":"原文🙂"}\n', b' {"value":"original"}\n'])
def test_full_roundtrip_scope_hash_modes_and_reconstruction_without_database(root, wire):
    bound = binding(); original_receipt = receipt(bound)
    descriptor = journal.write_response(root, bound, wire, original_receipt)
    assert descriptor['consumable'] is True and descriptor['binding'] == bound
    assert descriptor['wire_kind'] == ('text' if type(wire) is str else 'bytes')
    assert descriptor['wire_bytes'] == len(wire.encode('utf-8') if type(wire) is str else wire)
    assert descriptor['wire_sha256'] == hashlib.sha256(wire.encode('utf-8') if type(wire) is str else wire).hexdigest()
    assert descriptor['receipt_status'] == 'saved'
    assert 'content' not in descriptor and 'receipt' not in descriptor and 'value' not in descriptor
    assert len(json.dumps(descriptor).encode()) < 8192
    # Simulate losing all DB/callback objects: exact known binding is sufficient.
    rebound = json.loads(json.dumps(bound))
    assert journal.read_descriptor(Path(str(root)), rebound) == descriptor
    loaded = journal.read_response(Path(str(root)), rebound, descriptor)
    assert loaded['content'] == wire and loaded['receipt'] == original_receipt
    assert journal.parse_response(root, rebound, descriptor)['value'] == json.loads(wire)
    assert all(mode == 0o600 for _, mode, _ in files(root).values())
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in [root, *root.rglob('*')] if path.is_dir())


def test_wire_file_and_parent_are_fsynced_before_receipt_validation(root, monkeypatch):
    bound = binding(); path = location(root, bound) / 'wire.bin'; observed = []
    real_fsync, real_receipt = journal.os.fsync, journal._receipt
    def fsync(fd):
        observed.append(os.fstat(fd).st_ino); return real_fsync(fd)
    def after_wire(value):
        assert path.read_bytes() == b'{"ready":true}'
        assert path.stat().st_ino in observed and path.parent.stat().st_ino in observed
        return real_receipt(value)
    monkeypatch.setattr(journal.os, 'fsync', fsync)
    monkeypatch.setattr(journal, '_receipt', after_wire)
    assert journal.write_response(root, bound, b'{"ready":true}', receipt(bound))['consumable']


def test_same_identity_content_and_complete_receipt_replay_is_readonly(root, monkeypatch):
    bound = binding(); original = receipt(bound)
    descriptor = journal.write_response(root, bound, '{}', original); before = files(root)
    def no_write(*args, **kwargs):
        raise AssertionError('idempotent replay cannot write/fsync files')
    monkeypatch.setattr(journal.os, 'write', no_write)
    monkeypatch.setattr(journal.os, 'fsync', no_write)
    assert journal.write_response(root, copy.deepcopy(bound), '{}', copy.deepcopy(original)) == descriptor
    assert files(root) == before


@pytest.mark.parametrize('change', ['wire', 'kind', 'receipt', 'actor', 'call', 'source_hash', 'catalog_hash',
    'generation_hash', 'dispatch_hash', 'messages_hash'])
def test_any_response_or_owned_identity_conflict_preserves_original_files(root, change):
    bound = binding(); original = receipt(bound)
    journal.write_response(root, bound, '{}', original); before = files(root)
    modified = copy.deepcopy(bound); wire = '{}'; changed_receipt = copy.deepcopy(original)
    if change == 'wire': wire = '{"different":true}'
    elif change == 'kind': wire = b'{}'
    elif change == 'receipt': changed_receipt['extra_field_not_dropped']['amount'] = 2.0
    elif change == 'actor': modified['actor_id'] = identity(99)
    elif change == 'call': modified['reserved_call_id'] = identity(99)
    else: modified[change] = 'f' * 64
    with pytest.raises(journal.JournalError):
        journal.write_response(root, modified, wire, changed_receipt)
    assert files(root) == before


def test_one_fixed_call_per_stage_caps_the_entire_draft_at_25_slots(root):
    for number, stage in enumerate([*(f'map-{n}' for n in range(1, 25)), 'reduce'], 10):
        bound = binding(stage, number)
        journal.write_response(root, bound, '{}', receipt(bound))
    draft = root / identity(2) / identity(3)
    assert len(list(draft.iterdir())) == 25
    before = files(root)
    for stage in ['map-0', 'map-25', 'map-01', '../map-1', 'reduce/other']:
        bound = binding(stage, 100)
        with pytest.raises(journal.JournalError):
            journal.write_response(root, bound, '{}', receipt(bound))
    other_call = binding('map-1', 101)
    with pytest.raises(journal.JournalError) as caught:
        journal.write_response(root, other_call, '{}', receipt(other_call))
    assert caught.value.code == 'journal_slot_conflict' and files(root) == before


def test_oversize_answer_is_fully_durable_quarantine_not_a_truncated_413(root):
    bound = binding(); wire = b'x' * (journal.MAX_RESPONSE_BYTES + 100)
    descriptor = journal.write_response(root, bound, wire, receipt(bound))
    assert descriptor['consumable'] is False and descriptor['quarantine_reasons'] == ['wire_capacity_exceeded']
    assert descriptor['wire_bytes'] == len(wire)
    assert (location(root, bound) / 'wire.bin').read_bytes() == wire
    assert journal.read_descriptor(root, bound) == descriptor
    assert journal.write_response(root, bound, wire, receipt(bound)) == descriptor
    with pytest.raises(journal.JournalError, match='journal_quarantined'):
        journal.read_response(root, bound)


@pytest.mark.parametrize('wire', ['unpaired:\ud800', b'invalid:\xff'])
def test_invalid_unicode_or_byte_encoding_is_preserved_reversibly_but_never_consumed(root, wire):
    bound = binding(); descriptor = journal.write_response(root, bound, wire, receipt(bound))
    expected = wire.encode('utf-8', errors='surrogatepass') if type(wire) is str else wire
    assert (location(root, bound) / 'wire.bin').read_bytes() == expected
    if type(wire) is str:
        assert expected.decode('utf-8', errors='surrogatepass') == wire
    assert descriptor['consumable'] is False and descriptor['quarantine_reasons'] == ['wire_encoding_invalid']
    assert journal.read_descriptor(root, bound) == descriptor


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf'), '\x00', '\ud800'])
def test_invalid_receipt_json_values_are_retained_in_ascii_quarantine_without_fake_usage(root, invalid):
    bound = binding(); original = receipt(bound); original['usage']['invalid_original_value'] = invalid
    descriptor = journal.write_response(root, bound, '{}', original)
    raw_receipt = (location(root, bound) / 'receipt.bin').read_bytes()
    assert raw_receipt == json.dumps(original, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=True).encode('ascii')
    assert descriptor['receipt_status'] == 'invalid_json' and descriptor['consumable'] is False
    assert descriptor['quarantine_reasons'] == ['receipt_invalid']
    assert journal.read_descriptor(root, bound) == descriptor
    assert journal.write_response(root, bound, '{}', original) == descriptor


def test_deep_representable_receipt_is_preserved_and_unknown_python_object_never_repr_or_pickle(root):
    bound = binding(); original = receipt(bound); deep = []
    for _ in range(60): deep = [deep]
    original['usage']['deep'] = deep
    descriptor = journal.write_response(root, bound, '{}', original)
    assert descriptor['receipt_status'] == 'invalid_json' and not descriptor['consumable']
    assert (location(root, bound) / 'receipt.bin').read_bytes() == json.dumps(
        original, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
    class Unknown:
        def __repr__(self):
            raise AssertionError('private unknown object must never be repr-ed')
        def __reduce__(self):
            raise AssertionError('private unknown object must never be pickled')
    other = binding('map-2', 8); descriptor = journal.write_response(root, other, '{}', {'usage': Unknown()})
    assert descriptor['receipt_status'] == 'unavailable' and descriptor['receipt_bytes'] == 0
    assert descriptor['receipt_sha256'] is None and not descriptor['consumable']
    assert (location(root, other) / 'wire.bin').read_bytes() == b'{}'
    assert not (location(root, other) / 'receipt.bin').exists()
    assert journal.read_descriptor(root, other) == descriptor


def test_safe_receipt_wrong_call_remains_full_original_but_cannot_be_consumed(root):
    bound = binding(); original = receipt(bound); original['call_id'] = identity(99)
    descriptor = journal.write_response(root, bound, '{}', original)
    assert descriptor['receipt_status'] == 'saved' and not descriptor['consumable']
    assert descriptor['quarantine_reasons'] == ['receipt_call_mismatch']
    assert json.loads((location(root, bound) / 'receipt.bin').read_bytes()) == original


@pytest.mark.parametrize('wire', ['{', '{"x":1,"x":2}', 'NaN', 'Infinity', '-Infinity', '{"x":1e999}',
    '{"x":"\\ud800"}', '{"x":"\\u0000"}', '[' * 70 + '0' + ']' * 70])
def test_strict_json_rejection_happens_after_durability_and_never_repairs_original(root, wire):
    bound = binding(); descriptor = journal.write_response(root, bound, wire, receipt(bound)); before = files(root)
    assert descriptor['consumable']  # transport eligibility is not syntax/schema/semantic success.
    with pytest.raises(journal.JournalError, match='journal_response_not_json'):
        journal.parse_response(root, bound, descriptor)
    assert journal.read_response(root, bound, descriptor)['content'] == wire
    assert files(root) == before


def test_partial_wire_write_or_missing_receipt_is_preserved_and_never_overwritten(root, monkeypatch):
    bound = binding(); real_new = journal._new_file
    def partial(directory, name, content):
        if name == 'wire.bin':
            real_new(directory, name, content[:3]); raise OSError('synthetic disk failure, never exposed')
        return real_new(directory, name, content)
    monkeypatch.setattr(journal, '_new_file', partial)
    with pytest.raises(journal.JournalDurabilityError) as caught:
        journal.write_response(root, bound, '{"full":true}', receipt(bound))
    assert caught.value.code == 'journal_not_durable_no_paid_retry'
    assert 'synthetic' not in str(caught.value)
    assert (location(root, bound) / 'wire.bin').read_bytes() == b'{"f'
    before = files(root); monkeypatch.setattr(journal, '_new_file', real_new)
    with pytest.raises(journal.JournalError, match='journal_partial_or_corrupt'):
        journal.write_response(root, bound, '{"full":true}', receipt(bound))
    with pytest.raises(journal.JournalError, match='journal_partial_or_corrupt'):
        journal.read_descriptor(root, bound)
    assert files(root) == before
    # A separate complete slot with its receipt removed must also fail closed.
    other = binding('map-2', 8); journal.write_response(root, other, '{}', receipt(other))
    (location(root, other) / 'receipt.bin').unlink()
    with pytest.raises(journal.JournalError):
        journal.read_response(root, other)


def test_receipt_processing_failure_cannot_remove_the_already_fsynced_original_wire(root, monkeypatch):
    bound = binding()
    def failed(_):
        raise OSError('synthetic subsequent failure')
    monkeypatch.setattr(journal, '_receipt', failed)
    with pytest.raises(journal.JournalDurabilityError):
        journal.write_response(root, bound, '{}', receipt(bound))
    assert (location(root, bound) / 'wire.bin').read_bytes() == b'{}'
    assert not (location(root, bound) / 'envelope.json').exists()


@pytest.mark.parametrize('change', ['wire', 'receipt', 'mode', 'hardlink', 'symlink'])
def test_reader_rejects_corrupt_content_unsafe_permissions_and_link_substitution(root, tmp_path, change):
    bound = binding(); descriptor = journal.write_response(root, bound, '{}', receipt(bound)); path = location(root, bound)
    if change == 'wire': (path / 'wire.bin').write_bytes(b'[]')
    if change == 'receipt': (path / 'receipt.bin').write_bytes(b'{}')
    if change == 'mode': (path / 'wire.bin').chmod(0o644)
    if change == 'hardlink': os.link(path / 'wire.bin', tmp_path / 'extra-link')
    if change == 'symlink':
        original = tmp_path / 'outside'; original.write_bytes(b'{}'); original.chmod(0o600)
        (path / 'wire.bin').unlink(); (path / 'wire.bin').symlink_to(original)
    with pytest.raises(journal.JournalError):
        journal.read_response(root, bound, descriptor)


def test_root_or_stage_symlink_cannot_redirect_a_paid_answer(root, tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir(mode=0o700)
    root.symlink_to(outside, target_is_directory=True)
    bound = binding()
    with pytest.raises(journal.JournalError):
        journal.write_response(root, bound, '{}', receipt(bound))
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('filename', ['wire.bin', 'receipt.bin'])
def test_loaded_bytes_are_reverified_after_metadata_hash_pass(root, monkeypatch, filename):
    bound = binding(); descriptor = journal.write_response(root, bound, '{}', receipt(bound))
    real_read, count = journal._read_file, 0
    def changed_after_check(directory, name, maximum=None):
        nonlocal count
        raw = real_read(directory, name, maximum)
        if name == filename:
            count += 1
            # receipt is loaded once during _bundle before actual consumption.
            if filename == 'wire.bin' or count == 2:
                return b'{} ' if filename == 'wire.bin' else raw + b' '
        return raw
    monkeypatch.setattr(journal, '_read_file', changed_after_check)
    with pytest.raises(journal.JournalError, match='journal_storage_integrity'):
        journal.read_response(root, bound, descriptor)


def test_expected_descriptor_binding_is_exact_and_read_paths_do_not_create_new_scope(root):
    bound = binding(); descriptor = journal.write_response(root, bound, '{}', receipt(bound)); before = files(root)
    modified = copy.deepcopy(descriptor); modified['wire_bytes'] = 100
    with pytest.raises(journal.JournalError, match='journal_descriptor_conflict'):
        journal.read_response(root, bound, modified)
    for key in ['workspace_id', 'draft_id', 'actor_id', 'reserved_call_id']:
        wrong = {**bound, key: identity(99)}
        with pytest.raises(journal.JournalError):
            journal.read_descriptor(root, wrong)
    assert files(root) == before


@pytest.mark.parametrize('change', ['../escape', 'UPPERCASE', 'zero', 'extra'])
def test_bad_binding_or_relative_root_rejects_before_creating_any_files(root, change):
    bound = binding()
    if change == '../escape': bound['workspace_id'] = '../escape'
    if change == 'UPPERCASE': bound['source_hash'] = 'A' * 64
    if change == 'zero': bound['reserved_call_id'] = '00000000-0000-0000-0000-000000000000'
    if change == 'extra': bound['private_payload'] = 'cannot enter metadata'
    with pytest.raises(journal.JournalError):
        journal.write_response(root, bound, '{}', receipt(bound))
    assert not root.exists()
    with pytest.raises(journal.JournalError, match='journal_root_invalid'):
        journal.write_response(Path('relative'), binding(), '{}', receipt(binding()))
