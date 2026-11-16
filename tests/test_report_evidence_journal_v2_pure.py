"""New Reduce namespace/limits with real private synthetic filesystem only."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_evidence_journal as old
from generic_agents import report_evidence_journal_v2 as journal
from tests.test_report_evidence_journal_pure import binding as old_binding, receipt, location, files


def binding(): return {**old_binding(stage='reduce'), 'schema': 'report-evidence-journal-binding-v2'}


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('Journal tests cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def root(tmp_path): return tmp_path.resolve() / 'new-reduce-journal'


def test_large_reduce_roundtrip_is_exact_immutable_and_cold_readable(root):
    bound = binding(); wire = json.dumps({'synthetic': '原字🙂' * 40000}, ensure_ascii=False)
    assert 256 * 1024 < len(wire.encode()) < 1024 * 1024
    original_receipt = receipt(bound); before = copy.deepcopy((bound, original_receipt))
    descriptor = journal.write_response(root, bound, wire, original_receipt)
    assert descriptor['schema'] == 'report-evidence-journal-descriptor-v2' and descriptor['consumable']
    assert descriptor['wire_bytes'] == len(wire.encode())
    assert descriptor['wire_sha256'] == hashlib.sha256(wire.encode()).hexdigest()
    loaded = journal.read_response(root, bound, descriptor)
    assert loaded['content'] == wire and loaded['receipt'] == original_receipt
    assert journal.read_descriptor(root, bound) == descriptor
    assert journal.parse_response(root, bound, descriptor)['value'] == json.loads(wire)
    assert (bound, original_receipt) == before
    previous = files(root)
    with pytest.raises(journal.JournalError): journal.write_response(root, bound, wire + ' ', original_receipt)
    assert files(root) == previous


def test_old_namespace_keeps_256_kib_quarantine_and_cannot_read_new_binding(root):
    bound = old_binding(stage='reduce'); wire = 'x' * (256 * 1024 + 1)
    descriptor = old.write_response(root, bound, wire, receipt(bound))
    assert not descriptor['consumable'] and descriptor['schema'] == 'report-evidence-journal-descriptor-v1'
    assert (location(root, bound) / 'wire.bin').read_bytes() == wire.encode()
    with pytest.raises(old.JournalError): old.read_response(root, bound, descriptor)
    with pytest.raises(old.JournalError): old.read_response(root, binding())
    assert old.MAX_RESPONSE_BYTES == 256 * 1024 and journal.MAX_RESPONSE_BYTES == 1024 * 1024


@pytest.mark.parametrize('change', ['old_schema', 'map_stage', 'unknown_schema', 'extra'])
def test_new_namespace_never_reinterprets_old_or_map_bindings(root, change):
    bound = binding()
    if change == 'old_schema': bound['schema'] = 'report-evidence-journal-binding-v1'
    elif change == 'map_stage': bound['stage'] = 'map-1'
    elif change == 'unknown_schema': bound['schema'] = 'report-evidence-journal-binding-v3'
    else: bound['allow_old'] = True
    with pytest.raises(journal.JournalError): journal.write_response(root, bound, '{}', receipt(bound))
    assert not root.exists()


def test_above_new_limit_preserves_every_original_byte_before_quarantine(root):
    bound = binding(); wire = b'x' * (1024 * 1024 + 1)
    descriptor = journal.write_response(root, bound, wire, receipt(bound))
    assert not descriptor['consumable'] and descriptor['wire_bytes'] == len(wire)
    assert (location(root, bound) / 'wire.bin').read_bytes() == wire
    assert descriptor['wire_sha256'] == hashlib.sha256(wire).hexdigest()
    with pytest.raises(journal.JournalError): journal.read_response(root, bound, descriptor)


def test_original_invalid_json_is_saved_before_parse_and_never_repaired(root):
    bound = binding(); wire = '{"value":1,"value":2}'
    descriptor = journal.write_response(root, bound, wire, receipt(bound))
    assert descriptor['consumable']
    assert journal.read_response(root, bound, descriptor)['content'] == wire
    before = files(root)
    with pytest.raises(journal.JournalError): journal.parse_response(root, bound, descriptor)
    assert files(root) == before


def test_descriptor_versions_do_not_cross_adopt_even_same_scope_and_bytes(root):
    bound = binding(); descriptor = journal.write_response(root, bound, '{}', receipt(bound))
    changed = {**descriptor, 'schema': 'report-evidence-journal-descriptor-v1'}
    with pytest.raises(journal.JournalError): journal.read_response(root, bound, changed)
    assert journal.read_response(root, bound, descriptor)['content'] == '{}'
