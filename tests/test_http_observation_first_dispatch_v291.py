"""Virgin mapped Acq inventory over explicit source-authority and SQL IO spies.

The actual _bindings, first-dispatch predicate, bounded SQL inventories and
original dispatch-Job attribution run. Original service SDK/state/source proof
and query rows are IO boundaries, not PG/IAM/lease/provider evidence. Root owns
the real retained Task's readonly inventory and subsequent exact worker proof.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import http_observation_task_consumer_v270 as subject
from generic_agents import http_observation_transport_v177 as transport
from generic_agents import http_observation_worker_v177 as http
from generic_agents import http_observation_exchange_v177 as ledger
from generic_agents import native_goal_acquisitions as native
from generic_agents import workflow_service_v195 as service
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.monitor_scheduling_service_v253 import KEY, ACTION_PIN
from generic_agents.contracts import digest


def ident(value): return str(UUID(int=value))


def scene(monkeypatch):
    p = NS(workspace_id=ident(1)); task = NS(id=ident(2), workspace_id=p.workspace_id,
        data={'instance_id': ident(3), 'instance_release_id': ident(4)})
    source = NS(id=ident(5), workspace_id=p.workspace_id, kind='source', status='published',
        data={'specification': {transport.MAPPING_KEY: {'original_contract': True}},
            'data_policy': {'simulation': True, 'test_only': True}})
    source.content_hash = source.validated_hash = digest(source.data)
    request = {'query_id': ident(6), 'gap_id': ident(7)}
    policy = NS(id=ident(8), data={'approved_hash': 'a' * 64})
    plan = NS(id=ident(9), workspace_id=p.workspace_id, policy_id=policy.id, entity_id=ident(10),
        status='awaiting_dispatch', data={'observation_request': request})
    acquisition = NS(id=ident(11), workspace_id=p.workspace_id, capability_id=source.id,
        entity_id=plan.entity_id, status='authorized', version=1, idempotency_key='sensing:' + plan.id + ':1',
        data={'actor_id': ident(12), 'source_hash': source.content_hash, KEY: {'original_pin': True}})
    plan.data['current_acquisition_id'] = acquisition.id
    pin = {'acquisition_id': acquisition.id, 'source_hash': source.content_hash,
        'observation_plan_id': plan.id, 'policy_id': policy.id}
    call = NS(id=ident(13), data={'service_state': {'acquisition_id': acquisition.id}})
    state = {'state_hash': 'b' * 64, 'actor_id': acquisition.data['actor_id'],
        'operation_data': {'observation_request': request, 'acquisition_pins': [pin]}}
    origin = {'pin': pin, 'request': request, 'invocation_id': call.id, 'service_state_hash': state['state_hash']}
    job = NS(id=ident(14), workspace_id=p.workspace_id, kind='dispatch_acquisition', status='queued',
        attempts=0, lease_token=None, lease_until=None, idempotency_key='dispatch:' + acquisition.id,
        data={'acquisition_id': acquisition.id, 'actor_id': acquisition.data['actor_id']})
    anchor = NS(id=ident(15), action=ACTION_PIN, actor_id=state['actor_id'],
        data={'schema': 'monitor-scheduling-acquisition-audit-v253', 'acquisition_id': acquisition.id,
            'policy_id': policy.id, 'actor_id': state['actor_id'],
            'pin_hash': digest(acquisition.data[KEY]), 'pin': acquisition.data[KEY]})
    inventories = {subject.m.Job: [job], subject.m.Audit: [anchor], subject.m.Invocation: [call]}
    traces = []; strict = []
    class DB:
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']
            sql = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert p.workspace_id in sql
            traces.append((model, sql))
            if statement.get_execution_options().get('yield_per', 0) > 0:
                table = model.__tablename__
                assert sql.partition('WHERE')[2].strip().startswith(f"{table}.workspace_id = '{p.workspace_id}' ORDER BY {table}.id")
                assert 'LIMIT' not in sql
                return Stream(inventories.get(model, []))
            assert 'LIMIT' in sql
            if model is subject.m.Audit and 'audit_events.object_id =' in sql:
                return [row for row in inventories[model] if row is anchor]
            if model is subject.m.Audit and ledger.ACTION in sql:
                return [row for row in inventories[model] if row.action == ledger.ACTION]
            return inventories.get(model, [])
    class Stream(list):
        def close(self):
            pass
    db = DB()
    monkeypatch.setattr(subject, '_get', lambda actual_db, principal, model, identity:
        source if model is subject.m.Capability else policy if model is subject.m.MonitorPolicy else call)
    monkeypatch.setattr(subject, '_origin', lambda *args: copy.deepcopy(state))
    monkeypatch.setattr(native, 'acquisition', lambda *args: (acquisition, plan))
    monkeypatch.setattr(service, '_acquisition_pin', lambda *args: copy.deepcopy(pin))
    def strict_missing(*args):
        strict.append('original-find-exchange')
        raise HTTPException(409, 'Original mapped missing/orphan guard retained')
    monkeypatch.setattr(http, 'find_exchange', strict_missing)
    return NS(p=p, task=task, source=source, plan=plan, acquisition=acquisition, job=job,
        origin=origin, anchor=anchor, inventories=inventories, traces=traces, strict=strict, db=db)


def test_new_virgin_binding_selects_only_original_queued_dispatch_without_creating_an_exchange(monkeypatch):
    s = scene(monkeypatch); before = copy.deepcopy((s.acquisition.__dict__, s.job.__dict__, s.plan.__dict__))
    bindings = subject._bindings(s.db, s.p, s.task, {s.acquisition.id: s.origin})
    assert set(bindings) == {s.acquisition.id} and bindings[s.acquisition.id]['exchange'] is None
    subject._job(s.db, s.p, s.task, bindings, {}, s.job)
    assert bindings[s.acquisition.id]['acquisition'] is s.acquisition
    assert not s.strict and not s.inventories.get(Exchange)
    assert (s.acquisition.__dict__, s.job.__dict__, s.plan.__dict__) == before
    assert {model for model, sql in s.traces} >= {subject.m.Job, subject.m.Audit, Exchange,
        subject.m.Event, subject.m.Memory, subject.m.Invocation}


@pytest.mark.parametrize('change', [
    lambda s: setattr(s.job, 'attempts', 1),
    lambda s: setattr(s.job, 'status', 'running'),
    lambda s: setattr(s.job, 'lease_token', ident(20)),
    lambda s: setattr(s.job, 'lease_until', 'retained-lease'),
    lambda s: setattr(s.job, 'idempotency_key', 'dispatch:' + s.acquisition.id + ':resume:1'),
    lambda s: s.job.data.update(recovery_only=True),
    lambda s: setattr(s.acquisition, 'status', 'dispatching'),
    lambda s: setattr(s.acquisition, 'version', 2),
])
def test_attempted_or_changed_original_uses_strict_existing_missing_exchange_guard(monkeypatch, change):
    s = scene(monkeypatch); change(s)
    with pytest.raises(HTTPException): subject._bindings(s.db, s.p, s.task, {s.acquisition.id: s.origin})
    assert s.strict == ['original-find-exchange']


@pytest.mark.parametrize('model', [subject.m.Event, subject.m.Memory, subject.m.Invocation, subject.m.Audit])
def test_reverse_receipt_wire_or_http_audit_cannot_pass_as_never_dispatched(monkeypatch, model):
    s = scene(monkeypatch)
    remnant = NS(id=ident(21), action=ledger.ACTION, data={'state': {'data': {'freeze': {
        'parent_pins': {'acquisition_id': s.acquisition.id}, 'wire_request': {'request_key': s.acquisition.idempotency_key}}}}})
    s.inventories.setdefault(model, []).append(remnant)
    with pytest.raises(HTTPException): subject._bindings(s.db, s.p, s.task, {s.acquisition.id: s.origin})
    assert s.job.attempts == 0 and s.job.status == 'queued'


def test_missing_schedule_audit_or_dispatch_marker_does_not_create_a_new_exchange(monkeypatch):
    s = scene(monkeypatch); s.inventories[subject.m.Audit].clear()
    with pytest.raises(HTTPException): subject._bindings(s.db, s.p, s.task, {s.acquisition.id: s.origin})
    s = scene(monkeypatch); s.acquisition.data['http_observation_exchange_id_v177'] = ident(22)
    with pytest.raises(HTTPException): subject._bindings(s.db, s.p, s.task, {s.acquisition.id: s.origin})
