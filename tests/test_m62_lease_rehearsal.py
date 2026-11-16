"""Offline harness serialization regressions; no DB, process or socket calls."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import MappingProxyType
from uuid import UUID

import pytest
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.engine.row import Row


SCRIPT = Path(__file__).parents[1] / '.runtime/m62-lease-rehearsal.py'
SPEC = importlib.util.spec_from_file_location('m62_offline_lease_harness', SCRIPT)
h = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = h
SPEC.loader.exec_module(h)
JOB = UUID('e8ba0b67-6f12-4fc5-ab1e-ef8e4ad304b1')
CASE = 'c' * 32
WHEN = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def forbid_runtime(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('offline harness test attempted DB, process, socket or signal')
    monkeypatch.setattr(h.step, 'query', denied)
    monkeypatch.setattr(h.step, 'context', denied)
    monkeypatch.setattr(h.step, 'create_engine', denied)
    monkeypatch.setattr(h.m, 'create_engine', denied)
    monkeypatch.setattr(h.m, 'snapshot_owned', denied)
    monkeypatch.setattr(h.m.check, 'create_engine', denied)
    monkeypatch.setattr(h.subprocess, 'Popen', denied)
    monkeypatch.setattr(h.subprocess, 'run', denied)
    monkeypatch.setattr(h.os, 'kill', denied)
    monkeypatch.setattr(h.os, 'killpg', denied)
    monkeypatch.setattr(socket, 'socket', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def mapping(**values):
    metadata = SimpleResultMetaData(list(values))
    return Row(metadata, None, metadata._key_to_index, tuple(values.values()))._mapping


def test_actual_sqlalchemy_rowmapping_was_not_directly_json_serializable():
    row = mapping(id=str(JOB), status='queued', attempts=0,
        lease_until=None, data={'task_id': str(JOB)})
    with pytest.raises(TypeError):
        json.dumps(row)
    rows = h.plain_rows([row])
    assert type(rows[0]) is dict
    assert json.loads(json.dumps(rows))[0] == dict(row)


@pytest.mark.parametrize('value', [
    mapping(job_id=JOB, status='running', lease_until=WHEN,
        data={'lease_recoveries': 0, 'context': MappingProxyType({'owner': JOB})}),
    {'activity': [mapping(pid=43210, state='idle', xact_start=None,
        backend_xid=None, backend_xmin=None)], 'locks': [], 'remaining': Decimal('179.999999999999999999')},
    {'job': mapping(id=JOB, attempts=2, data={'lease_recoveries': 1}),
        'invocations': [mapping(status='failed', has_output=False, finished_at=WHEN)]},
])
def test_real_query_shapes_are_normalized_before_private_json_write(value, monkeypatch, tmp_path):
    captured = []
    def write(root, relative, normalized, *, replace=False):
        captured.append((root, relative, json.loads(json.dumps(normalized, allow_nan=False)), replace))
    monkeypatch.setattr(h.copies, 'private_json', write)
    h.write_private_json(tmp_path, 'proof.json', value)
    assert captured == [(tmp_path, 'proof.json', h.json_safe(value), False)]


def test_datetime_uuid_decimal_are_explicit_and_decimal_precision_is_retained():
    value = h.json_safe({'when': WHEN, 'job': JOB, 'duration': Decimal('0.00000000000000000001')})
    assert value == {'when': WHEN.isoformat(), 'job': str(JOB),
        'duration': {'m62_evidence_type': 'decimal', 'value': '1E-20'}}
    assert json.loads(json.dumps(value, allow_nan=False)) == value


@pytest.mark.parametrize('value', [
    object(), {'secret': object()}, {1: 'ambiguous-key'},
    datetime(2026, 10, 15), float('nan'), float('inf'), Decimal('NaN'), Decimal('Infinity'),
])
def test_unsupported_or_nonfinite_values_fail_before_any_file_write(value, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(h.copies, 'private_json', lambda *a, **kw: calls.append(True))
    with pytest.raises((TypeError, RuntimeError)):
        h.write_private_json(tmp_path, 'proof.json', value)
    assert calls == []


def test_new_attempt_directory_cannot_replace_legacy_attempt_one(monkeypatch, tmp_path):
    monkeypatch.setattr(h.step, 'CONTROL', tmp_path)
    case_root = tmp_path / ('case-' + CASE)
    original = case_root / 'lease-rehearsal'
    original.mkdir(mode=0o700, parents=True)
    marker = original / 'lease-rehearsal-proof.json'
    marker.write_bytes(b'{"passed":false,"error_type":"TypeError"}')
    before = marker.read_bytes()
    assert h.attempt_directory(CASE, 2) == case_root / 'lease-rehearsal-attempt-2'
    assert h.attempt_directory(CASE, 3) != h.attempt_directory(CASE, 2)
    assert marker.read_bytes() == before
    assert not h.attempt_directory(CASE, 2).exists()


@pytest.mark.parametrize('attempt', [None, 0, 1, True, 17, '2'])
def test_attempt_must_be_a_new_explicit_bounded_integer(attempt):
    with pytest.raises(RuntimeError, match='explicit_new_attempt_required'):
        h.attempt_directory(CASE, attempt)
