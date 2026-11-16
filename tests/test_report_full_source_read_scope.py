"""Snapshot-scope orchestration and real immutable-file checks, not PG proof.

Only SQL Session/Connection/mode reads and the expensive full-source loader are
explicit boundary spies. Evidence files are real bounded tmp files, and final
reads use actual evidence_bytes/path/SHA checks. Root separately runs the real
Task HTTP/PostgreSQL journey; these tests do not fabricate its source authority.
"""
import asyncio
import copy
import hashlib
import inspect
import math
import socket
import subprocess
from contextlib import contextmanager
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import NAMESPACE_URL, uuid5
from typing import get_args

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy.sql import operators

from generic_agents import evidence
from generic_agents import report_full_source_read_scope as read
from generic_agents import report_full_reading_source as full
from generic_agents.models import Evidence
from tests.test_report_full_reading_source_pure import snapshot


def uid(value):
    return str(uuid5(NAMESPACE_URL, 'public-full-source-read-scope:' + str(value)))


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('scope tests cannot dispatch or access a network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def scene(monkeypatch, tmp_path):
    p = NS(user_id=uid('actor'), workspace_id=uid('workspace'), role='operator')
    transaction = NS(is_active=True)
    session_transaction = NS(is_active=True)
    state = {'connection_transaction': transaction, 'session_transaction': None,
        'read_only': 'on', 'isolation': 'repeatable read'}
    connection = Mock(spec=Connection)
    connection.closed = False
    connection.get_transaction.side_effect = lambda: state['connection_transaction']
    mode_reads = []
    def mode(sql):
        mode_reads.append(sql)
        assert sql == ("SELECT current_setting('transaction_read_only'), "
            "current_setting('transaction_isolation')")
        return NS(one=lambda: (state['read_only'], state['isolation']))
    connection.exec_driver_sql.side_effect = mode
    db = Mock(spec=Session)
    db.info = {}; db.new = set(); db.dirty = set(); db.deleted = set()
    db.get_bind.return_value = connection
    db.in_nested_transaction.return_value = False
    db.get_transaction.side_effect = lambda: state['session_transaction']
    def begin():
        assert state['session_transaction'] is None
        state['session_transaction'] = session_transaction
        return session_transaction
    db.begin.side_effect = begin
    rows, paths, bytes_by_id, file_reads = {}, {}, {}, []
    for name in ('word', 'pdf', 'png1', 'png2'):
        content = ('bounded synthetic immutable evidence ' + name).encode()
        sha = hashlib.sha256(content).hexdigest()
        identity = uid(name)
        row = Evidence(id=identity, workspace_id=p.workspace_id, sha256=sha,
            size_bytes=len(content), media_type='application/octet-stream',
            storage_key=p.workspace_id + '/' + sha + '.bin', data={})
        path = tmp_path / row.storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        rows[identity], paths[identity], bytes_by_id[identity] = row, path, content
    def scalar(statement):
        assert statement.column_descriptions[0]['entity'] is Evidence
        values = list(rows.values())
        for clause in statement._where_criteria:
            assert clause.operator is operators.eq
            values = [row for row in values if getattr(row, clause.left.key) == clause.right.value]
        assert len(values) <= 1
        if values: file_reads.append(values[0].id)
        return values[0] if values else None
    db.scalar.side_effect = scalar
    monkeypatch.setattr(evidence, 'settings', lambda: NS(storage_dir=tmp_path))
    value = {'original': {'evidence_id': uid('word'), 'evidence_sha256': rows[uid('word')].sha256},
        'copy': {'evidence_id': uid('pdf'), 'ref': {'hash': rows[uid('pdf')].sha256}},
        'pages': [{'image': {'evidence_id': uid(name), 'sha256': rows[uid(name)].sha256}}
            for name in ('png1', 'png2')],
        'checked_source_fixture': True, 'payload': {'number': 1.0, 'negative_zero': -0.0}}
    loads = []
    def load():
        loads.append('complete-source-validator IO boundary')
        return copy.deepcopy(value)
    policy = ('synthetic-full-validator', load)
    @contextmanager
    def permit():
        with read._attested_task_read_session(db, p, connection, transaction):
            yield
    def resolve(selection=None, *, lock=False, chosen_policy=None, chosen_loader=None):
        return read.read_full_source(db, p, {'strict_selection': 1} if selection is None else selection,
            lock=lock, policy=policy if chosen_policy is None else chosen_policy,
            compute=load if chosen_loader is None else chosen_loader)
    return NS(p=p, db=db, connection=connection, transaction=transaction,
        session_transaction=session_transaction, state=state, rows=rows, paths=paths,
        bytes=bytes_by_id, file_reads=file_reads, mode_reads=mode_reads,
        value=value, loads=loads, load=load, policy=policy, permit=permit, resolve=resolve)


def test_only_one_attested_operation_reuses_source_and_finishes_real_files(scene):
    s = scene
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            one = s.resolve(); one['payload']['number'] = 999
            assert s.resolve()['payload']['number'] == 1.0
            assert len(s.loads) == 1 and not s.file_reads
        assert set(s.file_reads) == set(s.rows)
        assert len(s.file_reads) == len(s.rows)
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            negative_zero = s.resolve()['payload']['negative_zero']
            assert type(negative_zero) is float and math.copysign(1, negative_zero) == -1
        assert len(s.loads) == 2
    assert read._PERMIT_KEY not in s.db.info


def test_ordinary_session_and_write_call_never_reuse(scene):
    s = scene
    with read.task_full_source_read_scope(s.db, s.p, uid('task')):
        s.resolve(); s.resolve()
    assert len(s.loads) == 2 and not s.file_reads
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            s.resolve(lock=True); s.resolve(lock=True)
    assert len(s.loads) == 4 and not s.file_reads


@pytest.mark.parametrize('field,value', [('read_only', 'off'), ('isolation', 'read committed')])
def test_permit_requires_real_server_modes_at_issuance(scene, field, value):
    s = scene; s.state[field] = value
    with pytest.raises(HTTPException):
        with s.permit(): pass
    assert not s.loads and read._PERMIT_KEY not in s.db.info


def test_plain_boolean_cannot_attest_a_server_snapshot(scene):
    s = scene; s.db.info[read._PERMIT_KEY] = True
    with pytest.raises(HTTPException):
        with read.task_full_source_read_scope(s.db, s.p, uid('task')): pass
    assert not s.loads


@pytest.mark.parametrize('drift', ['connection', 'connection_transaction', 'session_transaction',
    'closed_connection', 'ended_transaction', 'ended_session_transaction', 'readonly', 'isolation',
    'dirty', 'new', 'deleted', 'nested', 'actor', 'workspace', 'permit'])
def test_live_scope_drift_fails_closed_not_as_a_cache_miss(scene, drift):
    s = scene
    with s.permit():
        with pytest.raises(HTTPException):
            with read.task_full_source_read_scope(s.db, s.p, uid('task')):
                s.resolve()
                if drift == 'connection': s.db.get_bind.return_value = Mock(spec=Connection)
                elif drift == 'connection_transaction': s.state['connection_transaction'] = NS(is_active=True)
                elif drift == 'session_transaction': s.state['session_transaction'] = NS(is_active=True)
                elif drift == 'closed_connection': s.connection.closed = True
                elif drift == 'ended_transaction': s.transaction.is_active = False
                elif drift == 'ended_session_transaction': s.session_transaction.is_active = False
                elif drift == 'readonly': s.state['read_only'] = 'off'
                elif drift == 'isolation': s.state['isolation'] = 'read committed'
                elif drift in {'dirty', 'new', 'deleted'}: getattr(s.db, drift).add(object())
                elif drift == 'nested': s.db.in_nested_transaction.return_value = True
                elif drift == 'actor': s.p.user_id = uid('another actor')
                elif drift == 'workspace': s.p.workspace_id = uid('another workspace')
                elif drift == 'permit': s.db.info[read._PERMIT_KEY] = object()
                s.resolve()
    assert len(s.loads) == 1


@pytest.mark.parametrize('name', ['word', 'pdf', 'png1', 'png2'])
@pytest.mark.parametrize('damage', ['changed', 'missing', 'path'])
def test_file_change_refuses_the_whole_endpoint_before_return(scene, name, damage):
    s = scene; identity = uid(name); returned = []
    def endpoint():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            value = s.resolve(); s.resolve()
            if damage == 'changed': s.paths[identity].write_bytes(b'changed after initial verification')
            elif damage == 'missing': s.paths[identity].unlink()
            else: s.rows[identity].storage_key = 'another-workspace/unsafe.bin'
            return value
    with s.permit():
        with pytest.raises(HTTPException): returned.append(endpoint())
    assert returned == [] and len(s.loads) == 1


def test_exceptions_do_not_cache_failures_and_copied_context_cannot_revive(scene):
    s = scene; failure = []
    def failed():
        failure.append(1)
        raise ValueError('original validator failed')
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            for _ in range(2):
                with pytest.raises(ValueError): s.resolve(chosen_loader=failed)
            s.resolve()
            inherited = copy_context()
        with pytest.raises(HTTPException): inherited.run(s.resolve)
    assert len(failure) == 2 and len(s.loads) == 1


def test_child_thread_cannot_consume_parent_snapshot_entries(scene):
    s = scene
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            s.resolve(); inherited = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(HTTPException): pool.submit(inherited.run, s.resolve).result()
    assert len(s.loads) == 1


def test_async_sibling_cannot_consume_inherited_parent_entries(scene):
    s = scene
    async def sibling():
        with pytest.raises(HTTPException): s.resolve()
    async def operation():
        with s.permit():
            with read.task_full_source_read_scope(s.db, s.p, uid('task')):
                s.resolve()
                await asyncio.create_task(sibling())
    asyncio.run(operation())
    assert len(s.loads) == 1 and set(s.file_reads) == set(s.rows)


def test_actual_async_cancellation_closes_scope_permit_and_inherited_context(scene):
    s = scene; captured = {}
    async def operation():
        with s.permit():
            captured['permit'] = s.db.info[read._PERMIT_KEY]
            with read.task_full_source_read_scope(s.db, s.p, uid('task')):
                s.resolve()
                captured['scope'] = read._CURRENT.get()
                captured['context'] = copy_context()
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError): asyncio.run(operation())
    assert captured['permit'].closed and captured['scope'].closed
    assert captured['scope'].entries == {} and captured['scope'].files == {}
    assert read._CURRENT.get() is None and read._PERMIT_KEY not in s.db.info
    with pytest.raises(HTTPException): captured['context'].run(s.resolve)
    assert len(s.loads) == 1 and s.file_reads == []


@pytest.mark.parametrize('selections', [({'n': 1}, {'n': 1.0}), ({'n': 0.0}, {'n': -0.0}),
    ({'revision': 1, 'claimed_hash': 'a' * 64}, {'revision': 2, 'claimed_hash': 'a' * 64})])
def test_key_uses_complete_typed_input_not_claimed_hash(scene, selections):
    s = scene
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            for selection in selections: s.resolve(selection)
    assert len(s.loads) == 2


def test_actual_validator_policy_change_does_not_reuse_its_old_result(scene):
    s = scene
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            s.resolve(); s.resolve(chosen_policy=('different-validator', object()))
    assert len(s.loads) == 2


@pytest.mark.parametrize('bound,value', [('MAX_ENTRIES', 0), ('MAX_BYTES', 1), ('MAX_FILES', 1)])
def test_capacity_falls_back_to_the_complete_original_loader(scene, monkeypatch, bound, value):
    s = scene; monkeypatch.setattr(read, bound, value)
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            assert s.resolve() == s.value
            assert s.resolve() == s.value
    assert len(s.loads) == 2
    # These full, just-computed results were never admitted for reuse. They
    # retain the original timing semantics, not a new exit-file requirement.
    assert s.file_reads == []


def test_overflow_does_not_remove_already_admitted_file_proofs(scene, monkeypatch):
    s = scene; monkeypatch.setattr(read, 'MAX_ENTRIES', 1)
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            s.resolve({'source': 'first'})
            s.resolve({'source': 'uncached second'})
    assert len(s.loads) == 2 and set(s.file_reads) == set(s.rows)


def test_unattested_source_never_runs_a_cache_policy_or_changes_original_error(scene):
    s = scene
    def forbidden_policy():
        raise AssertionError('ordinary caller cannot run read-scope policy code')
    assert read.read_full_source(s.db, s.p, {}, lock=False,
        policy_factory=forbidden_policy, compute=s.load) == s.value
    def original_failure():
        raise HTTPException(404, 'original-source-error')
    with pytest.raises(HTTPException) as error:
        read.read_full_source(s.db, s.p, {}, lock=False,
            policy_factory=forbidden_policy, compute=original_failure)
    assert error.value.status_code == 404 and error.value.detail == 'original-source-error'


def test_nested_same_get_shares_lifetime_but_different_task_refuses(scene):
    s = scene; task = uid('task')
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, task):
            s.resolve()
            with read.task_full_source_read_scope(s.db, s.p, task): s.resolve()
            with pytest.raises(HTTPException):
                with read.task_full_source_read_scope(s.db, s.p, uid('other task')): pass
    assert len(s.loads) == 1 and len(s.file_reads) == len(s.rows)


def test_public_freeze_keeps_strict_selection_before_a_cache_hit(scene, monkeypatch):
    s = scene; selected = snapshot(2)['selection']
    # Expensive full-source DB/OCR validation is the explicitly marked loader
    # boundary here; actual selection validation and scope/final files execute.
    def loaded(db, p, selection, *, lock):
        assert db is s.db and p is s.p and lock is False
        assert isinstance(selection, full.FullSourceSelection)
        return s.load()
    monkeypatch.setattr(full, '_freeze_full_source', loaded)
    with s.permit():
        with read.task_full_source_read_scope(s.db, s.p, uid('task')):
            full.freeze_full_source(s.db, s.p, selected, lock=False)
            full.freeze_full_source(s.db, s.p, selected, lock=False)
            damaged = copy.deepcopy(selected); damaged['generation'] = True
            with pytest.raises((HTTPException, ValidationError)):
                full.freeze_full_source(s.db, s.p, damaged, lock=False)
    assert len(s.loads) == 1


def test_approval_get_declares_snapshot_dependency_but_all_commands_keep_original_db():
    from generic_agents import layered_approval_api_v175 as api
    from generic_agents.signal_dual_read_session import read_session
    from generic_agents.setup_api import DB
    annotation = inspect.signature(api.detail).parameters['db'].annotation
    assert get_args(annotation)[1].dependency is read_session
    for endpoint in (api.revise, api.submit, api.decide, api.withdraw, api.start,
            api.resubmit_acceptance, api.accept):
        assert inspect.signature(endpoint).parameters['db'].annotation is DB
    route = next(route for route in api.router.routes if getattr(route, 'endpoint', None) is api.detail)
    assert route.methods == {'GET'} and route.path == '/tasks/{identity}/approval-v175'


@pytest.mark.parametrize('endpoint', ['task', 'approval'])
@pytest.mark.parametrize('changed', [False, True])
def test_actual_sync_get_wrapper_finishes_scope_before_return(scene, monkeypatch, endpoint, changed):
    s = scene; task_id = uid('task')
    def detail(*args):
        value = s.resolve(); s.resolve()
        if changed: s.paths[uid('word')].write_bytes(b'changed during GET')
        return value
    if endpoint == 'task':
        from generic_agents import workspace_api as api
        monkeypatch.setattr(api, '_detail_report_text_operation', detail)
    else:
        from generic_agents import layered_approval_api_v175 as api
        monkeypatch.setattr(api, 'scoped', lambda db, model, identity, principal: NS(id=identity))
        monkeypatch.setattr(api.store, 'detail', detail)
    with s.permit():
        if changed:
            with pytest.raises(HTTPException): api.detail(task_id, s.p, s.db)
        else:
            assert api.detail(task_id, s.p, s.db) == s.value
            assert set(s.file_reads) == set(s.rows)
    assert len(s.loads) == 1
