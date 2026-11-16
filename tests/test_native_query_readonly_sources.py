"""Real query reconstruction, catalog, schema and receipt checks at IO seams.

The object proofs, request producer, registered manifests and deterministic
query receipt validation are real. Authority/evidence/database reads are
explicit in-memory boundaries; these tests do not claim PostgreSQL durability.
"""
import copy
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import data_requirements, data_tools, entity_release_compat as compat
from generic_agents import goals, goal_queries, native_goal_queries as queries
from generic_agents import native_goals, native_query_catalog as catalog, workspace_api
from generic_agents import models as m
from generic_agents.contracts import GoalSpec, digest
from generic_agents.instance_compiler import identifier
from generic_agents.temporal import resolve
from tests.test_entity_release_compat_pure import World


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'native-readonly-query:' + label))


@pytest.fixture(autouse=True)
def forbid_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure query tests prohibit SQL engines, writes, network and processes')
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'execute', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene(monkeypatch, *, readonly=True):
    w = World(monkeypatch)
    probes = []; flags = {'role': True}
    tools = []
    for manifest in catalog.manifests():
        value = manifest.model_dump(mode='json')
        cap = NS(id=uid(manifest.name), workspace_id=w.p.workspace_id, kind=value['kind'],
            release_version=manifest.version, status='published', data=value,
            content_hash=digest(value), validated_hash=digest(value))
        w.db.rows[m.Capability][cap.id] = cap; tools.append(cap)
    package = w.db.rows[m.Capability][w.r2.package_id]
    package.data['dependencies'] = [cap.id for cap in tools]
    package.data['specification'].update(data_tool_ids=list(package.data['dependencies']),
        native_query_catalog=catalog.pins(tools), timezone='Asia/Shanghai')
    package.content_hash = package.validated_hash = digest(package.data)
    field = identifier('field', '标题')
    keyword = next(cap for cap in tools if cap.data['specification']['method'] == 'keyword')
    spec = GoalSpec(purpose='筛选指定登记记录', entity_ids=[w.entity.id], required_fields=[field],
        subgoals=[{'id': 'records', 'description': '核对已保存登记', 'criterion_ids': ['complete']}],
        criteria=[{'id': 'complete', 'description': '登记资料充分', 'kind': 'evidence_sufficient'}],
        evidence={'source': 'archive', 'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
        data_query={'tool_steps': [{'id': 'filter', 'capability_id': keyword.id,
            'input_from': 'evidence', 'parameters': {'field': field, 'text': '合成'}}],
            'max_tool_seconds': 60, 'allow_external_tools': False}).model_dump()
    template = w.task()
    task = m.Task(id=uid('task'), workspace_id=w.p.workspace_id, owner_id=w.p.user_id,
        package_id=w.r2.package_id, version=4, status='workflow_completed',
        data={**copy.deepcopy(template.data), 'workflow_runtime': {'receipts': []},
            'goal_spec': spec, 'goal_hash': digest(spec), 'goal_version': 1,
            'goal_confirmed_by': w.p.user_id, 'fields': [field],
            'goal_window': resolve(spec['evidence']['time'], datetime(2026, 10, 24, tzinfo=timezone.utc)),
            'native_goal_context': {'actor_id': w.p.user_id}, 'data_query_run_id': uid('query')})
    task.data = {**task.data, 'native_goal_context_hash': digest(task.data['native_goal_context'])}
    business = {'available': True}
    w.db.rows[m.Task] = {task.id: task}
    w.db.rows[m.DataRun] = {}; w.db.rows[m.Invocation] = {}
    original_scoped = w.db.scoped

    def scoped(db, model, identity, p, lock=False):
        assert db is w.db and p is w.p
        probes.append(('row', model, identity, lock))
        assert not readonly or lock is False, 'read-only path attempted a row lock'
        return original_scoped(db, model, identity, p, lock)

    def get(model, identity):
        probes.append(('get', model, identity))
        return w.db.rows[model].get(identity)

    def forbidden(*args, **kwargs):
        pytest.fail('query reconstruction must not write, enqueue or invoke')

    w.db.scoped = scoped; w.db.get = get
    w.db.add = forbidden; w.db.flush = forbidden; w.db.commit = forbidden; w.db.execute = forbidden
    monkeypatch.setattr(compat, 'scoped', scoped)
    for module in (catalog, data_tools, queries):
        monkeypatch.setattr(module, 'published', w.db.published)
    for module in (data_tools, data_requirements, queries):
        monkeypatch.setattr(module, 'scoped', scoped)
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *a: (task, w.r2, business))
    def guard(db, p, selected, **kwargs):
        assert db is w.db and p is w.p and selected is task
        probes.append(('guard', kwargs['lock_sources']))
        assert not readonly or kwargs['lock_sources'] is False
        if not flags['role']: raise HTTPException(403, 'current role was revoked')
    monkeypatch.setattr(native_goals, 'guard', guard)
    base = {'status': 'sufficient', 'evidence': [{'entity_id': w.entity.id, 'version': w.entity.version,
        'attributes': {field: w.entity.data[field]}}], 'observation_evidence': [], 'gaps': []}
    def evidence(db, p, entities, fields, **kwargs):
        assert db is w.db and p is w.p and entities == [w.entity.id] and fields == [field]
        probes.append(('evidence', copy.deepcopy(kwargs)))
        return copy.deepcopy(base)
    monkeypatch.setattr(goals, 'query_evidence', evidence)
    monkeypatch.setattr(queries, 'query_evidence', evidence)

    # Fixture construction uses the same request/schema producers as a real
    # saved query, without calling ensure, a worker, a gateway or an SQL engine.
    request = queries._request(w.db, w.p, task, lock_sources=not readonly)
    steps = spec['data_query']['tool_steps']; step = steps[0]
    contract = queries._contract(task, request, base, {keyword.id: keyword})
    row = NS(id=uid('query'), workspace_id=w.p.workspace_id, owner_id=w.p.user_id,
        version=2, status='sufficient', data={'request': copy.deepcopy(request),
            'native_query_job_id': uid('job'), 'native_query_contract': contract,
            'native_query_contract_hash': digest(contract), 'native_goal_task_id': task.id,
            'native_goal_context_hash': contract['context_hash'],
            'goal_delegation': {'task_id': task.id, 'goal_snapshot': goals.snapshot(task)},
            'native_goal_request_hash': digest(request)})
    records = data_tools.frame(base); key = 'data-tool:' + row.id + ':' + step['id']
    bound_inputs = queries._inputs(request, keyword, step, records, key)
    output = data_tools.builtin(keyword.data['execution']['handler'], bound_inputs)
    start = datetime(2026, 10, 24, 0, 0, 1, tzinfo=timezone.utc)
    call = NS(id=uid('call'), workspace_id=w.p.workspace_id, idempotency_key=key,
        capability_id=keyword.id, status='succeeded', data={'role': 'data',
            'input_hash': digest(bound_inputs), 'capability_hash': keyword.content_hash,
            'output': copy.deepcopy(output), 'job_id': row.data['native_query_job_id'],
            'started_at': start.isoformat(), 'finished_at': (start + timedelta(seconds=1)).isoformat(),
            'duration_ms': 1000})
    trace = {'step': copy.deepcopy(step), 'capability_id': keyword.id,
        'capability_hash': keyword.content_hash, 'release_version': keyword.release_version,
        'inputs': copy.deepcopy(bound_inputs), 'input_hash': digest(bound_inputs),
        'invocation_id': call.id, 'status': 'succeeded', 'output': copy.deepcopy(output)}
    result = {**copy.deepcopy(base), 'derived_records': copy.deepcopy(output['records']),
        'query_tool_steps': copy.deepcopy(steps), 'tool_trace': [copy.deepcopy(trace)],
        'derivation_verification': 'deterministic_registered_computation'}
    context = {'source_records': copy.deepcopy(records), 'steps': copy.deepcopy(steps),
        'started_at': (start - timedelta(seconds=1)).isoformat(),
        'signature': digest({'records': records, 'steps': steps,
            'capabilities': {keyword.id: keyword.content_hash},
            'requirement': request['evidence_requirement'], 'relations': None})}
    row.data.update(result=result, goal_result_hash=digest(result), tool_trace=[trace], tool_context=context)
    w.db.rows[m.DataRun][row.id] = row; w.db.rows[m.Invocation][call.id] = call
    probes.clear(); w.db.reads.clear()
    return NS(**vars(w), task=task, package=package, caps=tools, cap=keyword, row=row, call=call,
        base=base, field=field, probes=probes, flags=flags)


def original(c):
    return copy.deepcopy((c.task.data, c.row.data, c.call.data, c.call.status, c.row.version))


@pytest.mark.parametrize('readonly,result_locks', [(True, False), (False, False), (False, True)])
def test_full_real_gate_rebuilds_request_catalog_steps_schema_and_receipt_in_original_modes(monkeypatch, readonly, result_locks):
    c = scene(monkeypatch, readonly=readonly)
    before = original(c)
    kwargs = {'lock_sources': False} if readonly else {}
    assert queries.gate(c.db, c.p, c.task, lock=result_locks, **kwargs) is c.row
    assert ('guard', not readonly) in c.probes
    source_reads = [p for p in c.probes if p[0] == 'row' and p[1] is m.Capability]
    assert source_reads and any(p[3] is (not readonly) for p in source_reads)
    assert all(p[3] is False for p in source_reads) if readonly else any(p[3] is True for p in source_reads)
    assert ('row', m.Invocation, c.call.id, result_locks) in c.probes
    assert original(c) == before


def test_query_goal_false_reaches_complete_native_read_without_mutating_originals(monkeypatch):
    c = scene(monkeypatch)
    before = original(c)
    result = goals.query_goal(c.db, c.p, c.task, learning=False, lock_sources=False)
    assert result['data_query']['status'] == 'sufficient'
    assert result['derived_records'] == c.row.data['result']['derived_records']
    assert ('guard', False) in c.probes
    evidence = next(p[1] for p in c.probes if p[0] == 'evidence')
    assert evidence['include_trusted_cases'] is False  # Cross-release object grants no old Memory authority.
    assert original(c) == before
    result['derived_records'][0]['values'].clear()
    assert original(c) == before


@pytest.mark.parametrize('fault', ['source_hash', 'source_scope', 'capability_publication',
    'contract', 'unknown_call', 'input_hash', 'forged_output', 'role'])
def test_read_only_keeps_all_original_source_scope_capability_and_deterministic_receipt_rejections(monkeypatch, fault):
    c = scene(monkeypatch)
    if fault == 'source_hash': c.db.rows[m.Capability][c.r1.package_id].data['changed'] = True
    elif fault == 'source_scope': c.r1.instance_id = 'foreign-instance'
    elif fault == 'capability_publication': c.cap.status = 'retired'
    elif fault == 'contract': c.row.data['native_query_contract_hash'] = '0' * 64
    elif fault == 'unknown_call': c.call.status = 'unknown'
    elif fault == 'input_hash': c.call.data['input_hash'] = '0' * 64
    elif fault == 'role': c.flags['role'] = False
    else:
        fake = copy.deepcopy(c.call.data['output'])
        fake['records'][0]['values'][c.field] = '合成但不属于原输入的伪造值'
        c.call.data['output'] = copy.deepcopy(fake)
        c.row.data['tool_trace'][0]['output'] = copy.deepcopy(fake)
        c.row.data['result']['tool_trace'][0]['output'] = copy.deepcopy(fake)
        c.row.data['result']['derived_records'] = copy.deepcopy(fake['records'])
        c.row.data['goal_result_hash'] = digest(c.row.data['result'])
    before = original(c)
    with pytest.raises(HTTPException):
        queries.gate(c.db, c.p, c.task, lock_sources=False)
    assert original(c) == before
    assert all(p[3] is False for p in c.probes if p[0] == 'row')


def test_request_and_spec_default_write_paths_keep_real_source_locks(monkeypatch):
    c = scene(monkeypatch, readonly=False)
    before = original(c)
    default = queries._request(c.db, c.p, c.task)
    assert default == c.row.data['request']
    queries.validate_spec(c.db, c.p, c.task, c.task.data['goal_spec'])
    assert any(p[0] == 'row' and p[1] is m.Capability and p[3] is True for p in c.probes)
    assert original(c) == before


def test_query_goal_default_keeps_source_locks_without_adding_result_row_locks(monkeypatch):
    c = scene(monkeypatch, readonly=False)
    before = original(c)
    result = goals.query_goal(c.db, c.p, c.task, learning=False)
    assert result['data_query']['status'] == 'sufficient'
    assert ('guard', True) in c.probes
    assert any(p[0] == 'row' and p[1] is m.Capability and p[3] is True for p in c.probes)
    assert ('row', m.Invocation, c.call.id, False) in c.probes
    assert original(c) == before


@pytest.mark.parametrize('readonly', [True, False])
def test_acceptance_assessment_forwards_source_and_result_lock_modes_together(monkeypatch, readonly):
    class ReachedQueryGate(Exception):
        pass
    calls = []
    monkeypatch.setattr(native_goals, 'guard', lambda *a, **kw: None)
    monkeypatch.setattr(native_goals, 'query_goal', lambda *a, **kw: {'status': 'sufficient'})
    def gate(*args, **kwargs):
        calls.append(kwargs)
        raise ReachedQueryGate
    monkeypatch.setattr(queries, 'gate', gate)
    task = NS(data={'goal_spec': {'data_query': {'declared': True}}})
    with pytest.raises(ReachedQueryGate):
        native_goals.assessment(None, None, task, accepted=True,
            **({'lock_sources': False} if readonly else {}))
    assert calls == [{'lock': not readonly, 'lock_sources': not readonly}]


def test_pure_query_view_rechecks_original_result_without_source_locks_or_worker_recovery(monkeypatch):
    c = scene(monkeypatch)
    before = original(c)
    workers = []
    monkeypatch.setattr(queries, 'worker_view', lambda *a, **kw: workers.append(kw) or {'state': 'saved'})
    result = queries.view(c.db, c.p, c.task)
    assert result['current_valid'] is True and result['scope_error'] is None
    assert result['derived_records'] == c.row.data['result']['derived_records']
    assert workers == [{'historical': False, 'scope_error': None}]
    assert original(c) == before


def test_old_nonworkflow_read_keeps_its_four_positional_argument_contract(monkeypatch):
    c = scene(monkeypatch)
    legacy = NS(data={'goal_spec': c.task.data['goal_spec'], 'entity_ids': [c.entity.id],
        'fields': [c.field], 'goal_window': c.task.data['goal_window']})
    calls = []
    def read(db, p, task, result):
        calls.append((db, p, task))
        return {**result, 'legacy_read': True}
    monkeypatch.setattr(goal_queries, 'read', read)
    result = goals.query_goal(c.db, c.p, legacy, lock_sources=False)
    assert result['legacy_read'] is True and calls == [(c.db, c.p, legacy)]
    assert not any(p[0] == 'guard' for p in c.probes)


def test_absent_query_does_not_enter_saved_native_query_reader(monkeypatch):
    c = scene(monkeypatch)
    c.task.data = {**c.task.data, 'goal_spec': {**c.task.data['goal_spec'], 'data_query': None}}
    def forbidden(*a, **kw): pytest.fail('no data_query cannot enter saved-query derivation')
    monkeypatch.setattr(queries, 'read', forbidden)
    assert goals.query_goal(c.db, c.p, c.task, lock_sources=False) == c.base
