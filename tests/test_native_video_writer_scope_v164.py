"""Closed writer rejection IO and source AST; not PG/model/atomicity evidence.

The source factory and provenance/legacy checks are named boundary seams. No
successful new Observation/Event/Memory or permission proof is manufactured.
Every tested writer path stops before a new-row write; Root separately proves
real publication/RTSP/CPU and commit visibility with a second DB connection.
"""
import ast
from copy import deepcopy
import inspect
import textwrap
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from generic_agents import models as m
from generic_agents import instance_data_receipts, instance_domain, native_receipts, worker
from generic_agents import native_video_provenance_v164 as origin
from generic_agents.contracts import digest
from generic_agents.security import Principal


def uid(number):
    return '164bfa00-0000-4000-8000-' + f'{number:012x}'


class AtLegacyWriteBoundary(Exception):
    pass


class RejectWrites:
    """Only the planned actual SELECTs and exact native-sync advisory lock."""
    def __init__(self, instance_id, reads):
        self.instance_id = instance_id; self.reads = list(reads); self.calls = []

    def _read(self, kind, statement):
        assert self.reads, 'unexpected writer read'
        expected_kind, expected, result = self.reads.pop(0)
        assert kind == expected_kind and statement._for_update_arg is None
        assert statement.compare(expected)
        # Also check the real model's canonical marker/owner projection binds.
        assert statement.compile().params == expected.compile().params
        self.calls.append(kind + ':' + statement.column_descriptions[0]['entity'].__name__)
        if isinstance(result, Exception): raise result
        return result

    def scalar(self, statement): return self._read('scalar', statement)
    def scalars(self, statement): return self._read('scalars', statement)

    def execute(self, statement, parameters):
        assert str(statement) == 'SELECT pg_advisory_xact_lock(:key)'
        assert parameters == {'key': int(digest({'native_sync': self.instance_id})[:16], 16) - (1 << 63)}
        assert self.calls == ['scalar:DomainInstance']
        self.calls.append('advisory')

    def add(self, *_args): pytest.fail('rejected writer must not add a row')
    def flush(self, *_args): pytest.fail('rejected writer must not flush a new row')
    def commit(self): pytest.fail('writer boundary must not commit')


def scoped(model, identity, value, p):
    return ('scalar', select(model).where(model.id == identity, model.workspace_id == p.workspace_id), value)


def observation_rows(p, source, packet, rows):
    key = packet['source_key'] + ':' + packet['entity_id']
    return ('scalars', select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        m.Observation.source_id == source.id, m.Observation.source_record_id == key), rows)


def boundary():
    p = Principal(uid(1), uid(2), 'admin', 'writer-rejection-seam')
    legacy = NS(id=uid(3), instance_id=uid(4))
    modern = NS(id=uid(5), instance_id=uid(4))
    instance = NS(id=uid(4), active_release_id=legacy.id)
    source = NS(id=uid(6))
    packet = {'schema': 'native-observation-v2', 'source_key': 'VideoRun:' + uid(7),
        'source_machine_hash': 'a' * 64, 'entity_id': uid(8), 'business_day': '2026-10-21',
        'attributes': {'已保存画面数': 1}, 'evidence_ids': [uid(9)], 'task_id': None}
    run = NS(id=uid(7), data={'source_context': {'domain_binding': {
        'instance_id': instance.id, 'instance_release_id': modern.id}}})
    return p, instance, source, packet, run, modern, legacy


def source_boundary(monkeypatch, db, p, instance, source):
    def existing_source(actual_db, actual_p, actual_instance):
        assert actual_db is db and actual_p is p and actual_instance is instance
        assert db.calls == ['scalar:DomainInstance']
        return source
    monkeypatch.setattr(native_receipts, 'source', existing_source)


def test_original_modern_video_cannot_borrow_current_legacy_release(monkeypatch):
    p, instance, source, packet, run, modern, legacy = boundary()
    reads = [scoped(m.DomainInstance, instance.id, instance, p), observation_rows(p, source, packet, []),
        scoped(m.VideoRun, run.id, run, p), scoped(m.InstanceRelease, modern.id, modern, p)]
    db = RejectWrites(instance.id, reads); source_boundary(monkeypatch, db, p, instance, source)
    monkeypatch.setattr(origin, 'has_run_trace', lambda *_args: False)

    def original_plan(actual_db, actual_p, release):
        assert actual_db is db and actual_p is p and release is modern
        return {'schema': 'instance-data-requirements-v3'}

    def no_current_or_entity_fallback(*_args, **_kwargs):
        pytest.fail('Run fixed modern release must not be replaced by current/Entity legacy release')

    monkeypatch.setattr(instance_data_receipts, 'verify_release', original_plan)
    monkeypatch.setattr(instance_domain, 'generated_release', no_current_or_entity_fallback)
    before = deepcopy(packet)
    with pytest.raises(HTTPException) as error:
        native_receipts._save_packets(db, p, instance.id, [packet])
    assert error.value.status_code == 409 and db.reads == []
    assert instance.active_release_id == legacy.id and digest(packet) == digest(before)


@pytest.mark.parametrize('foreign', ['binding', 'release', 'both'])
def test_original_foreign_release_or_instance_is_rejected_before_plan_or_new_rows(monkeypatch, foreign):
    p, instance, source, packet, run, modern, _legacy = boundary()
    if foreign in {'binding', 'both'}: run.data['source_context']['domain_binding']['instance_id'] = uid(99)
    if foreign in {'release', 'both'}: modern.instance_id = uid(99)
    db = RejectWrites(instance.id, [scoped(m.DomainInstance, instance.id, instance, p),
        observation_rows(p, source, packet, []), scoped(m.VideoRun, run.id, run, p),
        scoped(m.InstanceRelease, modern.id, modern, p)])
    source_boundary(monkeypatch, db, p, instance, source)
    monkeypatch.setattr(origin, 'has_run_trace', lambda *_args: False)

    def forbidden(*_args, **_kwargs):
        pytest.fail('foreign fixed scope must fail before plan check or Entity fallback')

    monkeypatch.setattr(instance_data_receipts, 'verify_release', forbidden)
    monkeypatch.setattr(instance_domain, 'generated_release', forbidden)
    with pytest.raises(HTTPException) as error:
        native_receipts._save_packets(db, p, instance.id, [packet])
    assert error.value.status_code == 409 and db.reads == []


def test_incomplete_independent_video_trace_never_falls_back_to_legacy(monkeypatch):
    p, instance, source, packet, run, _modern, _legacy = boundary()
    db = RejectWrites(instance.id, [scoped(m.DomainInstance, instance.id, instance, p),
        observation_rows(p, source, packet, [])])
    source_boundary(monkeypatch, db, p, instance, source)
    calls = []; failure = HTTPException(409, 'INPUT/RESULT 独立库存不完整')

    def traced(actual_db, actual_p, identity):
        assert actual_db is db and actual_p is p and identity == run.id
        calls.append('trace')
        return True

    def incomplete(actual_db, actual_p, identity, *, live=False):
        assert actual_db is db and actual_p is p and identity == run.id and live is True
        calls.append('checked')
        raise failure

    monkeypatch.setattr(origin, 'has_run_trace', traced)
    monkeypatch.setattr(origin, 'checked_run', incomplete)
    with pytest.raises(HTTPException) as error:
        native_receipts._save_packets(db, p, instance.id, [packet])
    assert error.value is failure and calls == ['trace', 'checked'] and db.reads == []


@pytest.mark.parametrize('replacement', [1.0, True])
def test_existing_same_source_packet_requires_typed_equality_before_reuse(monkeypatch, replacement):
    p, instance, source, packet, _run, _modern, _legacy = boundary()
    original_packet = deepcopy(packet)
    packet['attributes']['已保存画面数'] = replacement
    assert packet == original_packet and digest(packet) != digest(original_packet)
    original = NS(id=uid(10), data={'native_packet': original_packet})
    db = RejectWrites(instance.id, [scoped(m.DomainInstance, instance.id, instance, p),
        observation_rows(p, source, packet, [original])])
    source_boundary(monkeypatch, db, p, instance, source)
    calls = []

    def historical(actual_db, actual_p, row, *, historical=False):
        assert actual_db is db and actual_p is p and row is original and historical is True
        calls.append('historical')
        return {'checked_context_seam': True}

    def forbidden(*_args, **_kwargs):
        pytest.fail('existing changed packet must reject before any new provenance path')

    monkeypatch.setattr(native_receipts, 'checked', historical)
    monkeypatch.setattr(origin, 'has_run_trace', forbidden)
    with pytest.raises(HTTPException) as error:
        native_receipts._save_packets(db, p, instance.id, [packet])
    assert error.value.status_code == 409 and calls == ['historical'] and db.reads == []
    assert digest(original.data['native_packet']) == digest(original_packet)


def test_fixed_legacy_release_is_not_replaced_by_current_modern_before_write_boundary(monkeypatch):
    p, instance, source, packet, run, modern, legacy = boundary()
    instance.active_release_id = modern.id
    run.data['source_context']['domain_binding']['instance_release_id'] = legacy.id
    stop = AtLegacyWriteBoundary('stop at original-row lookup before any new Observation is added')
    next_row = select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        m.Observation.source_id == source.id,
        m.Observation.source_record_id == packet['source_key'] + ':' + packet['entity_id'],
        m.Observation.processing_version == digest(packet)[:40])
    db = RejectWrites(instance.id, [scoped(m.DomainInstance, instance.id, instance, p),
        observation_rows(p, source, packet, []), scoped(m.VideoRun, run.id, run, p),
        scoped(m.InstanceRelease, legacy.id, legacy, p), ('scalar', next_row, stop)])
    source_boundary(monkeypatch, db, p, instance, source)
    monkeypatch.setattr(origin, 'has_run_trace', lambda *_args: False)

    def legacy_only(actual_db, actual_p, release):
        assert actual_db is db and actual_p is p and release is legacy
        return None

    def forbidden(*_args, **_kwargs):
        pytest.fail('fixed legacy Run does not select current release or Entity origin')

    monkeypatch.setattr(instance_data_receipts, 'verify_release', legacy_only)
    monkeypatch.setattr(instance_domain, 'generated_release', forbidden)
    with pytest.raises(AtLegacyWriteBoundary) as error:
        native_receipts._save_packets(db, p, instance.id, [packet])
    assert error.value is stop and db.reads == []


def test_native_video_enqueue_is_only_inside_final_fenced_parent_success_transaction():
    """AST placement only; the real second-connection proof belongs to Root."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(worker.process_once)))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'enqueue_video']
    assert len(calls) == 1
    enqueue = calls[0]; chain = []; parent = enqueue
    while parent in parents:
        parent = parents[parent]; chain.append(parent)
    guard = next(node for node in chain if isinstance(node, ast.If))
    expected_guard = ast.parse("status == 'succeeded' and job.kind == 'process_video'", mode='eval').body
    assert ast.dump(guard.test) == ast.dump(expected_guard)
    transaction = next(node for node in chain if isinstance(node, ast.With))
    assert len(transaction.items) == 1
    expected_session = ast.parse('Session(engine())', mode='eval').body
    assert ast.dump(transaction.items[0].context_expr) == ast.dump(expected_session)
    assert isinstance(transaction.items[0].optional_vars, ast.Name)
    assert transaction.items[0].optional_vars.id == 'db'
    attach = [node for node in ast.walk(transaction) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'attach']
    assert len(attach) == 1 and ast.dump(attach[0]) == ast.dump(ast.parse('attach(db, job_id, token)', mode='eval').body)
    status = [node for node in transaction.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == 'job' and node.targets[0].attr == 'status']
    commits = [node for node in ast.walk(transaction) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == 'commit'
        and isinstance(node.func.value, ast.Name) and node.func.value.id == 'db']
    assert len(status) == len(commits) == 1
    assert isinstance(status[0].value, ast.Name) and status[0].value.id == 'status'
    assert attach[0].lineno < enqueue.lineno < status[0].lineno < commits[0].lineno
