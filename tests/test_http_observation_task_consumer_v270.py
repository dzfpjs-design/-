"""Pure scope/dispatch/receiver contracts. No model, network or ACK is faked.

These tests verify selection boundaries using forbidden-IO sentinels. They do
not establish a PostgreSQL journey or target-runtime acceptance; Root owns the
independent integration run. Authored statically with the new consumer.
"""
import copy
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace as Row

import pytest
from fastapi import HTTPException

from generic_agents import http_observation_task_consumer_v270 as subject

W = '1a0bc001-a462-42da-b822-a89e541bdc8d'
I = '2a0bc001-a462-42da-b822-a89e541bdc8d'
T = '3a0bc001-a462-42da-b822-a89e541bdc8d'
J = '4a0bc001-a462-42da-b822-a89e541bdc8d'
O = '5a0bc001-a462-42da-b822-a89e541bdc8d'
A = '6a0bc001-a462-42da-b822-a89e541bdc8d'
E = '7a0bc001-a462-42da-b822-a89e541bdc8d'
U = '8a0bc001-a462-42da-b822-a89e541bdc8d'
R = '9a0bc001-a462-42da-b822-a89e541bdc8d'


@pytest.fixture(autouse=True)
def no_real_io(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('Pure consumer contracts must not open a database or network')
    monkeypatch.setattr(subject, 'engine', denied)
    monkeypatch.setattr(subject, 'Session', denied)


def module(monkeypatch, name, **values):
    fake = Row(**values)
    monkeypatch.setitem(sys.modules, 'generic_agents.' + name, fake)
    monkeypatch.setattr(sys.modules['generic_agents'], name, fake, raising=False)


def reader(monkeypatch):
    db = object()
    @contextmanager
    def opened():
        yield db
    monkeypatch.setattr(subject, '_reader', opened)
    return db


def result(mode='discovery'):
    value = subject._result(W, I, T, mode)
    value['inventory'] = 'bounded_durable_snapshot'
    return value


@pytest.mark.parametrize('change', [
    {'workspace_id': None}, {'instance_id': I.upper()}, {'task_id': None},
    {'task_id': T.replace('-', '')}, {'family': None}, {'family': 'all'},
    {'family': 'weather_source_allowlist'}, {'limit': True}, {'limit': 0}, {'limit': 51},
])
def test_exact_task_instance_workspace_family_required_before_io(change):
    args = dict(workspace_id=W, instance_id=I, task_id=T, family=subject.FAMILY)
    args.update(change)
    with pytest.raises(HTTPException):
        subject.discover(**args)


def test_only_original_five_job_kinds_are_supported():
    assert set(subject.KINDS) == {'dispatch_acquisition', 'read_http_observation_receipt_v177',
        'process_http_mapped_observation_v177', 'settle_received_acquisition_v245',
        'check_native_business_goal'}


def test_stopped_entry_never_installs_handlers_or_opens_database(monkeypatch):
    stop = Event(); stop.set()
    monkeypatch.setattr(subject.signal, 'signal', lambda *_a: pytest.fail('installed signal while stopped'))
    assert subject.tick(W, I, T, family=subject.FAMILY, stop_event=stop)['state'] == 'stopped'
    assert subject.receive(W, I, T, family=subject.FAMILY, stop_event=stop)['state'] == 'stopped'
    assert subject.run(W, I, T, family=subject.FAMILY, mode='jobs', stop_event=stop) == 0


def test_inventory_overflow_cannot_become_partial_success():
    db = Row(scalars=lambda _query: [1, 2, 3])
    query = Row(limit=lambda bound: bound)
    with pytest.raises(HTTPException) as error:
        subject._rows(db, query, maximum=2)
    assert error.value.detail == subject.CAPACITY


def test_reverse_original_exchange_cannot_hide_behind_missing_forward_resense_pins(monkeypatch):
    module(monkeypatch, 'native_goal_acquisitions', acquisition=lambda *a: pytest.fail('No original pins'))
    module(monkeypatch, 'workflow_service_v195')
    module(monkeypatch, 'http_observation_worker_v177')
    module(monkeypatch, 'http_observation_exchange_v177', ACTION='http.observation.exchange.changed_v177')
    module(monkeypatch, 'http_observation_transport_v177', MAPPING_KEY='http_observation_mapping_v177')
    # ORM shape only for the bounded query, never a database or fake Exchange.
    module(monkeypatch, 'http_observation_exchange_models_v177', HTTPObservationExchangeV177=subject.m.Job)
    monkeypatch.setattr(subject, '_rows', lambda *a: [Row(acquisition_id=A, id=E)])
    with pytest.raises(HTTPException):
        subject._bindings(object(), Row(workspace_id=W), Row(id=T), {})


def test_original_service_acquisition_pin_must_match_before_source_selection(monkeypatch):
    module(monkeypatch, 'native_goal_acquisitions', acquisition=lambda *a: (Row(id=A), Row(policy_id=J)))
    module(monkeypatch, 'workflow_service_v195', _acquisition_pin=lambda *a: {'acquisition_id': O})
    module(monkeypatch, 'http_observation_worker_v177', find_exchange=lambda *a: pytest.fail('Unproved pin reached source'))
    module(monkeypatch, 'http_observation_exchange_v177', ACTION='http.observation.exchange.changed_v177')
    module(monkeypatch, 'http_observation_transport_v177', MAPPING_KEY='http_observation_mapping_v177')
    module(monkeypatch, 'http_observation_exchange_models_v177', HTTPObservationExchangeV177=subject.m.Job)
    monkeypatch.setattr(subject, '_get', lambda *a: Row(id=J))
    original = {A: {'pin': {'acquisition_id': A}, 'request': {'original': True}}}
    with pytest.raises(HTTPException):
        subject._bindings(object(), Row(workspace_id=W), Row(id=T), original)


def service_binding():
    from generic_agents import authored_service_workflow_graph_v270 as graph
    return {'schema': graph.BINDING, 'template': graph.TEMPLATE, 'simulation_only': True,
        'service_delegations': graph.declarations(), 'graph': {'nodes': [
            {'id': identity, 'kind': 'ability', 'execution': {'adapter': 'workflow_service', 'agent_role': 'business'}}
            for identity in ('planner', 'resense')]}}, graph


def test_v270_delegations_use_own_frozen_verifier_without_translation(monkeypatch):
    binding, graph = service_binding()
    seen = []
    def verifier(actual):
        seen.append(actual)
        return graph.service_delegations(actual)
    module(monkeypatch, 'authored_service_workflow_store_v270', service_delegations=verifier)
    assert 'translation' not in binding
    assert subject._service_delegations(binding) == graph.declarations()
    assert seen == [binding]


@pytest.mark.parametrize('alter', ['role', 'operation', 'service_actor', 'missing_node'])
def test_v270_forged_delegations_are_rejected_by_original_family_guard(monkeypatch, alter):
    binding, graph = service_binding()
    module(monkeypatch, 'authored_service_workflow_store_v270', service_delegations=graph.service_delegations)
    if alter == 'role':
        binding['service_delegations']['resense']['agent_role'] = 'perception'
    elif alter == 'operation':
        binding['service_delegations']['resense']['operation'] = 'unregistered_operation'
    elif alter == 'service_actor':
        binding['service_delegations']['resense']['service_actor'] = 'data_service'
    else:
        binding['graph']['nodes'].pop()
    with pytest.raises((HTTPException, ValueError)):
        subject._service_delegations(binding)


def test_original_v195_delegation_path_is_preserved_and_unknown_family_rejected(monkeypatch):
    seen = []; original = {'translation': {'server_owned': True}, 'schema': 'business-flow-nested-execution-binding-v195'}
    module(monkeypatch, 'business_flow_nested_adoption_v195', BINDING=original['schema'],
        service_delegations=lambda value: seen.append(value) or {'original': True})
    assert subject._service_delegations(original) == {'original': True}
    assert seen == [original['translation']]
    with pytest.raises(HTTPException):
        subject._service_delegations({**original, 'schema': 'unknown-service-family'})
    assert len(seen) == 1


def test_context_reader_v270_without_translation_calls_full_binding_verifier(monkeypatch):
    from generic_agents import workflow_task_context_v196 as context_reader
    binding = {'schema': 'authored-service-workflow-binding-v270', 'binding_hash': 'a' * 64}
    checked = {'verified_by_original_family': True}
    seen = []
    module(monkeypatch, 'authored_service_workflow_store_v270',
        check_binding=lambda value: seen.append(value) or checked)
    assert 'translation' not in binding
    assert context_reader._checked_source_binding(binding) is checked
    assert seen == [binding]


@pytest.mark.parametrize('schema', ['unknown-service-family', 'authored-workflow-binding-v208',
    'authored-service-workflow-binding-v270-spoof', None])
def test_context_reader_rejects_unknown_or_spoofed_binding_schema(schema):
    from generic_agents import workflow_task_context_v196 as context_reader
    with pytest.raises(HTTPException):
        context_reader._checked_source_binding({'schema': schema, 'translation': {}, 'simulation_only': True})


def test_context_reader_cannot_accept_v270_schema_and_pure_graph_as_binding_authority():
    from generic_agents import workflow_task_context_v196 as context_reader
    binding, _graph = service_binding()
    # Correct schema/delegations and an internally hashed client graph still
    # omit the full original authored binding; the real checker must reject.
    binding['binding_hash'] = subject.digest(binding)
    with pytest.raises(HTTPException):
        context_reader._checked_source_binding(binding)


def test_exact_original_worker_dispatch_without_receiving_or_global_fallback(monkeypatch):
    value = result(); value['jobs'] = [{'job_id': J, 'kind': subject.KINDS[0], 'ready': True}]
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: copy.deepcopy(value))
    db = reader(monkeypatch)
    job = Row(id=J, kind=subject.KINDS[0], data={'actor_id': U})
    monkeypatch.setattr(subject, '_inventory', lambda *a: (None, None, {}, {}, [job]))
    calls = []; actors = []
    module(monkeypatch, 'worker', actor=lambda *a: actors.append(a),
        process_once=lambda **k: calls.append(k) or True,
        deliver_events=lambda **k: pytest.fail('Job scheduler performed an ACK'))
    answer = subject.tick(W, I, T, family=subject.FAMILY)
    assert answer['processed'] is True and answer['job_id'] == J
    assert actors == [(db, W, U)]
    assert calls == [{'job_id': J, 'kinds': [subject.KINDS[0]], 'deliver': False,
        'workspace_id': W, '_stop_event': None}]


def test_recheck_failure_never_calls_original_worker(monkeypatch):
    value = result(); value['jobs'] = [{'job_id': J, 'kind': subject.KINDS[0], 'ready': True}]
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: copy.deepcopy(value))
    reader(monkeypatch)
    monkeypatch.setattr(subject, '_inventory', lambda *a: (None, None, {}, {}, []))
    module(monkeypatch, 'worker', actor=lambda *a: pytest.fail('no proved original actor'),
        process_once=lambda **k: pytest.fail('claimed a missing original'))
    answer = subject.tick(W, I, T, family=subject.FAMILY)
    assert answer['processed'] is False and answer['state'] == 'blocked'


def test_restart_tick_discovers_current_original_jobs_again(monkeypatch):
    calls = []; value = result()
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: calls.append((a, k)) or copy.deepcopy(value))
    module(monkeypatch, 'worker', actor=lambda *a: None, process_once=lambda **k: False)
    subject.tick(W, I, T, family=subject.FAMILY)
    subject.tick(W, I, T, family=subject.FAMILY)
    assert len(calls) == 2
    assert all(call[0] == (W, I, T) for call in calls)


def test_async_get_pacing_and_limit_come_from_original_durable_history(monkeypatch):
    module(monkeypatch, 'http_observation_exchange_v177', can_resume_observation=lambda _row: False)
    current = datetime(2026, 11, 1, tzinfo=timezone.utc)
    exchange = Row(status='awaiting_receipt', data={'read_jobs': [{'job_id': J}], 'active_read_job_id': J})
    old = Row(id=J, status='succeeded', created_at=current)
    assert subject._poll_state(exchange, [old], current)['state'] == 'waiting_backoff'
    assert subject._poll_state(exchange, [old], current + timedelta(seconds=5))['state'] == 'ready'
    old.status = 'running'
    assert subject._poll_state(exchange, [old], current + timedelta(days=1))['state'] == 'waiting_read'
    old.status = 'succeeded'
    jobs = [Row(id=str(n), status='succeeded', created_at=current) for n in range(subject.MAX_READS)]
    exchange.data = {'read_jobs': [{'job_id': job.id} for job in jobs], 'active_read_job_id': jobs[-1].id}
    assert subject._poll_state(exchange, jobs, current + timedelta(days=1))['state'] == 'blocked_read_limit'


def test_missing_original_get_job_does_not_reset_retry_budget(monkeypatch):
    module(monkeypatch, 'http_observation_exchange_v177', can_resume_observation=lambda _row: False)
    exchange = Row(status='awaiting_receipt', data={'read_jobs': [{'job_id': J}], 'active_read_job_id': J})
    with pytest.raises(HTTPException):
        subject._poll_state(exchange, [], datetime.now(timezone.utc))


def test_get_backoff_starts_after_original_job_completion(monkeypatch):
    module(monkeypatch, 'http_observation_exchange_v177', can_resume_observation=lambda _row: False)
    current = datetime(2026, 11, 1, tzinfo=timezone.utc)
    exchange = Row(status='awaiting_receipt', data={'read_jobs': [{'job_id': J}], 'active_read_job_id': J})
    old = Row(id=J, status='succeeded', created_at=current - timedelta(minutes=1),
        data={'finished_at': current.isoformat()})
    assert subject._poll_state(exchange, [old], current)['state'] == 'waiting_backoff'
    assert subject._poll_state(exchange, [old], current + timedelta(seconds=5))['state'] == 'ready'


def test_ended_exchange_does_not_schedule_a_new_get(monkeypatch):
    module(monkeypatch, 'http_observation_exchange_v177', can_resume_observation=lambda _row: False)
    exchange = Row(status='received', data={})
    assert subject._poll_state(exchange, [], datetime.now(timezone.utc)) == {'state': 'not_needed'}


def receipt_parts():
    task = Row(id=T, data={'instance_id': I, 'instance_release_id': R})
    original = {'observation': Row(id=O), 'process': Row(id=J),
        'exchange': Row(id=E, dispatch_job_id=A, actor_id=U), 'proof': {'acquisition_id': A}}
    receipt = {'instance_id': I, 'release_id': R, 'observation_id': O,
        'simulation_origin': {'simulation': True, 'test_only': True,
            'field_verified': False, 'production_activation': False}}
    return task, original, Row(id=E), receipt


@pytest.mark.parametrize('field,value', [
    ('simulation', False), ('test_only', False), ('field_verified', True),
    ('production_activation', True), ('simulation', 1),
])
def test_receiver_cannot_relabel_an_original_receipt(field, value):
    task, original, event, receipt = receipt_parts()
    receipt['simulation_origin'][field] = value
    with pytest.raises(HTTPException):
        subject._receipt_record(task, original, event, receipt)


def test_receiver_trace_identifies_original_jobs_and_exact_cold_receipt():
    task, original, event, receipt = receipt_parts()
    record = subject._receipt_record(task, original, event, receipt)
    assert record['dispatch_job_id'] == A and record['processing_job_id'] == J
    assert record['receipt_hash'] == subject.digest(receipt)
    assert record['origin_actor_id'] == U and record['production_activation'] is False


def test_independent_receiver_invokes_original_data_receiver_then_cold_reads(monkeypatch):
    value = result(); value['observations'] = [{'observation_id': O, 'processing_job_id': J,
        'processing_status': 'succeeded', 'status': 'processed', 'event_id': E, 'event_status': 'pending_delivery'}]
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: copy.deepcopy(value))
    reader(monkeypatch)
    monkeypatch.setattr(subject, '_inventory', lambda *a: (None, None, {}, {O: {
        'process': Row(status='succeeded'), 'observation': Row(status='processed')}}, []))
    calls = []
    module(monkeypatch, 'worker', deliver_events=lambda **k: calls.append(('original_receiver', k)),
        process_once=lambda **k: pytest.fail('Receiver ran a model or settlement Job'))
    monkeypatch.setattr(subject, '_receipt', lambda *a: calls.append(('cold_receipt', a[1:])) or (None, None, {}))
    monkeypatch.setattr(subject, '_record_receipt', lambda *a: calls.append(('trace', a)) or {'observation_id': O})
    answer = subject.receive(W, I, T, family=subject.FAMILY)
    assert answer['state'] == 'received'
    assert calls == [('original_receiver', {'workspace_id': W, 'observation_id': O, 'event_kind': 'observation.data'}),
        ('cold_receipt', (W, I, T, O)), ('trace', (W, I, T, O))]


def test_received_event_is_never_replayed_to_create_a_missing_ack(monkeypatch):
    value = result(); value['observations'] = [{'observation_id': O, 'processing_job_id': J,
        'processing_status': 'succeeded', 'status': 'processed', 'event_id': E, 'event_status': 'received'}]
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: copy.deepcopy(value))
    reader(monkeypatch)
    def blocked(*_a):
        raise HTTPException(409, subject.ACK_ERROR)
    monkeypatch.setattr(subject, '_receipt', blocked)
    module(monkeypatch, 'worker', deliver_events=lambda **k: pytest.fail('Replayed an acknowledged event'))
    answer = subject.receive(W, I, T, family=subject.FAMILY)
    assert answer['state'] == 'blocked' and answer['received'] == []


@pytest.mark.parametrize('change', [{'processing_status': 'running'}, {'status': 'failed'},
    {'event_id': None}, {'event_status': 'blocked_delivery'}])
def test_receiver_waits_or_preserves_existing_delivery_block(monkeypatch, change):
    value = result(); row = {'observation_id': O, 'processing_status': 'succeeded',
        'status': 'processed', 'event_id': E, 'event_status': 'pending_delivery'}
    row.update(change); value['observations'] = [row]
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: copy.deepcopy(value))
    module(monkeypatch, 'worker', deliver_events=lambda **k: pytest.fail('Delivered an incomplete or blocked original'))
    answer = subject.receive(W, I, T, family=subject.FAMILY)
    assert answer['received'] == [] and (answer['waiting'] or answer['blocked'])


def test_already_received_first_page_cannot_starve_later_original(monkeypatch):
    value = result(); value['received_count'] = 1
    done = {'observation_id': A, 'receiver_recorded': True}
    pending = {'observation_id': O, 'processing_status': 'succeeded', 'status': 'processed',
        'event_id': E, 'event_status': 'received', 'receiver_recorded': False}
    value['observations'] = [done, pending]
    monkeypatch.setattr(subject, 'discover', lambda *a, **k: copy.deepcopy(value))
    reader(monkeypatch)
    module(monkeypatch, 'worker', deliver_events=lambda **k: pytest.fail('Replayed an already received original'))
    monkeypatch.setattr(subject, '_receipt', lambda *a: (None, None, {}))
    monkeypatch.setattr(subject, '_record_receipt', lambda *a: {'observation_id': a[-1]})
    assert subject.receive(W, I, T, family=subject.FAMILY, limit=1)['received'] == [{'observation_id': O}]


def test_goal_check_rejects_another_actor_or_context_without_acceptance():
    context = {'actor_id': U, 'scope': 'original'}; task = Row(id=T, data={'native_goal_context': context})
    job = Row(data={'task_id': T, 'actor_id': U, 'context_hash': subject.digest(context)},
        idempotency_key='native-goal:' + T + ':' + subject.digest(context) + ':definition')
    subject._goal_job(task, job)
    job.data['actor_id'] = A
    with pytest.raises(HTTPException):
        subject._goal_job(task, job)


def test_superseded_cancelled_goal_timer_is_not_a_current_failure():
    task = Row(data={'native_goal_check_job_pin': {'job_id': J}})
    old = Row(id=A, kind='check_native_business_goal', status='cancelled')
    assert subject._unresolved_failure(task, {}, {}, old) is False


def test_failed_original_get_is_historical_after_exchange_received():
    exchange = Row(id=E, status='received', data={'active_read_job_id': J})
    old = Row(id=A, kind='read_http_observation_receipt_v177', status='failed', data={'exchange_id': E})
    assert subject._unresolved_failure(Row(data={}), {O: {'exchange': exchange}}, {}, old) is False


@pytest.mark.parametrize('mode', [None, 'all', 'accept', 'data_service'])
def test_daemon_modes_cannot_expand_into_global_work_or_acceptance(mode):
    with pytest.raises(HTTPException):
        subject.run(W, I, T, family=subject.FAMILY, mode=mode)
