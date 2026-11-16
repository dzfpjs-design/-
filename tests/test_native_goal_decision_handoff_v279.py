"""Explicit in-memory SQL/publication seams, not PG, dispatch or ACK evidence.

These tests use real ORM row types, Goal snapshots and the real receiver-event
predicate. Only the already-verified release/binding and request-origin boundary
are synthetic. Root owns the actual approved Task/worker/Source/receiver trial.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import native_goal_decision_handoff_v279 as subject
from generic_agents import authored_decision_workflow_store_v278 as family
from generic_agents import observation_requests
from generic_agents import data_gaps
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import digest
from generic_agents.models import Acquisition, Audit, DataGap, DataRun, Event, InstanceRelease, ObservationPlan, Task


def ident(n): return '27900000-0000-4000-8000-' + format(n, '012x')


def fixture(monkeypatch, *, schema=subject.BINDING):
    p = NS(workspace_id=ident(1), user_id=ident(2), role='operator')
    spec = {'contract_version': '1.0', 'purpose': '核对本次温度参数模拟观察与独立收件',
        'entity_ids': [ident(3)], 'required_fields': ['temperature_c'],
        'subgoals': [{'id': 'observe', 'description': '原资料充分', 'criterion_ids': ['fresh_data']}],
        'criteria': [{'id': 'fresh_data', 'description': '原要求的最新观测资料充分', 'kind': 'evidence_sufficient'}],
        'evidence': {'source': 'observations', 'time': {'mode': 'latest', 'timezone': 'Asia/Shanghai',
            'max_age_seconds': 1800}, 'minimum_verification': 'unverified_extraction', 'minimum_records_per_entity': 1},
        'observation_mode': 'allow_new', 'deadline': None, 'max_steps': 12, 'max_tool_calls': 8,
        'stop_on_conflict': True, 'relation_query': None, 'data_query': None}
    window = {'mode': 'latest', 'start': '2026-11-01T12:00:00+00:00', 'end': '2026-11-01T12:30:00+00:00'}
    task = Task(id=ident(4), workspace_id=p.workspace_id, package_id=ident(5), owner_id=p.user_id,
        title=spec['purpose'], status='workflow_waiting', version=1, data={'instance_id': ident(6),
            'instance_release_id': ident(7), 'business_id': 'business', 'entity_ids': spec['entity_ids'],
            'goal_spec': spec, 'goal_version': 1, 'goal_hash': digest(spec), 'goal_window': window,
            'goal_confirmed_by': p.user_id, 'graph_runtime': {'graph_hash': digest({'actual': 'graph'})}})
    goal_snapshot = {'version': 1, 'hash': digest(spec), 'spec': spec, 'time': window}
    goal = {'schema': 'native-business-goal-v1', 'instance_id': ident(6), 'release_id': ident(7),
        'business_id': 'business', 'entity_ids': spec['entity_ids'], 'actor_id': p.user_id, 'goal_snapshot': goal_snapshot}
    request = {'question': spec['purpose'], 'entity_ids': spec['entity_ids'], 'fields': spec['required_fields'],
        'evidence_requirement': spec['evidence'], 'mode': 'structured', 'model_profile_id': None,
        'tool_steps': [], 'allow_external_tools': False}
    current = {'status': 'insufficient', 'result': {'observation_evidence': [], 'status': 'insufficient'}}
    run = DataRun(id=ident(8), workspace_id=p.workspace_id, owner_id=p.user_id, question=spec['purpose'],
        status='insufficient', version=1, data={'request': copy.deepcopy(request),
            'native_goal_task_id': task.id, 'native_goal_context_hash': digest(goal),
            'native_goal_request_hash': digest(request), 'goal_delegation': {'task_id': task.id, 'goal_snapshot': goal_snapshot},
            'result': copy.deepcopy(current), 'native_goal_result_hash': digest(current), 'data_gap_ids': [ident(9)]})
    task.data = {**task.data, 'native_goal_context': goal, 'native_goal_context_hash': digest(goal), 'native_goal_query_id': run.id}
    gap = DataGap(id=ident(9), workspace_id=p.workspace_id, query_id=run.id, entity_id=ident(3),
        status='received', version=1, data={'business_task_id': task.id, 'actor_id': p.user_id,
            'request': copy.deepcopy(request), 'missing_fields': ['temperature_c']})
    binding = {'schema': schema, 'business_name': '公开模拟业务', 'flow': {'steps': []},
        'simulation_only': True, 'graph': {'actual': 'graph'}, 'binding_hash': 'a' * 64}
    release = InstanceRelease(id=ident(7), workspace_id=p.workspace_id, instance_id=ident(6),
        package_id=task.package_id, data={'snapshot': {'businesses': [{'id': 'business',
            'name': binding['business_name'], 'flow': binding['flow'], 'execution_mode': 'confirmed_graph_workflow'}]}})
    monkeypatch.setattr(subject.workflow_graph, 'business_binding', lambda actual_release, business:
        copy.deepcopy(binding) if actual_release is release else pytest.fail('wrong release'))
    checked = []
    monkeypatch.setattr(family, 'check_binding', lambda value: checked.append(copy.deepcopy(value)) or copy.deepcopy(value))
    pins = []
    def pin(db, actual_p, actual_gap):
        assert actual_p is p and actual_gap is gap and actual_gap.status != 'resolved'
        pins.append(gap.id)
        return {'gap_id': gap.id, 'query_id': run.id, 'entity_id': gap.entity_id,
            'fields': sorted(gap.data['missing_fields']), 'request': copy.deepcopy(gap.data['request']),
            'query_request_hash': digest(run.data['request']), 'goal_delegation': copy.deepcopy(run.data['goal_delegation'])}
    monkeypatch.setattr(observation_requests, 'pin', pin)
    events = []; received = set(); gap_events = []; acquisitions = set(); plans = set()
    def same_sql(query): return str(query.compile(compile_kwargs={'literal_binds': True}))
    release_sql = same_sql(select(InstanceRelease).where(InstanceRelease.workspace_id == p.workspace_id,
        InstanceRelease.id == release.id).execution_options(populate_existing=True))
    audit_sql = same_sql(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == subject.ACTION, Audit.object_id == gap.id).limit(2))
    acquisition_sql = same_sql(select(Acquisition.id).where(Acquisition.workspace_id == p.workspace_id,
        Acquisition.data['data_gap_id'].as_string() == gap.id).limit(1))
    plan_sql = same_sql(select(ObservationPlan.id).where(ObservationPlan.workspace_id == p.workspace_id,
        ObservationPlan.data['observation_request']['gap_id'].as_string() == gap.id).limit(1))
    class IO:
        def scalar(self, query):
            sql = same_sql(query)
            if sql == release_sql:
                assert query.get_execution_options().get('populate_existing') is True
                return release
            if sql == acquisition_sql:
                return ident(60) if acquisitions else None
            if sql == plan_sql:
                return ident(61) if plans else None
            for observation_id in [ident(50)]:
                expected = select(Event.id).where(Event.workspace_id == p.workspace_id,
                    Event.kind == 'observation.data', Event.idempotency_key == 'data:' + observation_id,
                    Event.status == 'received')
                if sql == same_sql(expected): return ident(51) if observation_id in received else None
            pytest.fail('unknown SQL is never accepted')
        def scalars(self, query):
            assert same_sql(query) == audit_sql
            return events
        def commit(self): pytest.fail('handoff took transaction ownership')
        def add(self, value): pytest.fail('handoff created an unrelated row')
    def save(db, actual_p, action, object_id, value):
        assert actual_p is p and action == subject.ACTION and object_id == gap.id
        events.append(NS(actor_id=actual_p.user_id, data=copy.deepcopy(value)))
    monkeypatch.setattr(subject, 'audit', save)
    monkeypatch.setattr(data_gaps, 'record', lambda *a, **k: gap_events.append((a[3], k)))
    monkeypatch.setattr(data_gaps, 'route', lambda *a, **k: pytest.fail('handoff routed perception'))
    monkeypatch.setattr(data_gaps, 'coordinate', lambda *a, **k: pytest.fail('handoff coordinated a parallel Task'))
    return NS(p=p, task=task, run=run, gap=gap, current=current, binding=binding, release=release,
        checked=checked, pins=pins, events=events, received=received, gap_events=gap_events,
        acquisitions=acquisitions, plans=plans, db=IO())


def invoke(s): return subject.defer_route(s.db, s.p, s.task, s.run, s.gap, s.current)


def test_received_gap_with_no_acquisition_is_handed_off_once_without_fake_dispatch(monkeypatch):
    s = fixture(monkeypatch); original = copy.deepcopy((s.task.data, s.run.data))
    assert invoke(s) is True and s.gap.status == 'received'
    assert 'acquisition_ids' not in s.gap.data and 'observation_plan_id' not in s.gap.data
    assert s.gap.data['business_task_id'] == s.task.id and s.gap.data['decision_reason'] == subject.WAIT_REASON
    record = copy.deepcopy(s.gap.data[subject.KEY]); version = s.gap.version
    assert record['observation_request']['gap_id'] == s.gap.id and len(s.events) == 1
    assert invoke(s) is True and s.gap.data[subject.KEY] == record and s.gap.version == version and len(s.events) == 1
    assert (s.task.data, s.run.data) == original
    monkeypatch.setattr(reader, 'scoped', lambda db, model, identity, p: s.gap if model is DataGap and identity == s.gap.id
        else pytest.fail('reader asked for another Gap'))
    assert reader._gaps(s.db, s.p, s.task, s.run) == [s.gap]
    # The normal reader permits received/open Gaps; its policy/Goal/source
    # guards remain required before it returns a real context for V196.
    assert s.gap.status not in {'resolved', 'cancelled', 'needs_review', 'pending_delivery'}


@pytest.mark.parametrize('schema', ['business-flow-nested-execution-binding-v195', 'authored-service-workflow-binding-v270'])
def test_original_families_return_to_unchanged_native_route_even_with_client_flags(monkeypatch, schema):
    s = fixture(monkeypatch, schema=schema); s.task.data['client_says_v278'] = True
    before = copy.deepcopy((s.gap.data, s.gap.status, s.gap.version))
    assert invoke(s) is False and not s.events and not s.checked and not s.pins
    assert (s.gap.data, s.gap.status, s.gap.version) == before


def test_non_graph_native_workflow_does_not_enter_graph_family_resolution(monkeypatch):
    s = fixture(monkeypatch); s.task.data.pop('graph_runtime')
    s.release.data['snapshot']['businesses'][0]['execution_mode'] = 'confirmed_human_workflow'
    monkeypatch.setattr(subject.workflow_graph, 'business_binding', lambda *a: pytest.fail('compact workflow entered graph resolver'))
    assert invoke(s) is False and not s.events and subject.KEY not in s.gap.data


@pytest.mark.parametrize('declaration', ['origin', 'binding', 'handoff'])
def test_non_graph_mode_cannot_hide_an_authored_decision_or_retained_handoff(monkeypatch, declaration):
    s = fixture(monkeypatch)
    business = s.release.data['snapshot']['businesses'][0]; business['execution_mode'] = 'confirmed_human_workflow'
    if declaration == 'origin': business['authored_workflow_origin'] = {'original': 'saved'}
    elif declaration == 'binding':
        s.release.data['snapshot']['semantic_runtime_plan'] = {'graph_adoptions': [{'binding': s.binding}]}
    else: s.gap.data[subject.KEY] = {'schema': subject.SCHEMA}
    monkeypatch.setattr(subject.workflow_graph, 'business_binding', lambda *a: pytest.fail('non-graph reached graph resolver'))
    with pytest.raises(HTTPException): invoke(s)


@pytest.mark.parametrize('version', ['v278', 'v286', 'v270'])
def test_actual_authored_graph_resolver_and_binding_contract_choose_only_supported_decision_families(monkeypatch, version):
    """Real frozen graph/binding code; synthetic release row, not adoption IO."""
    real_resolver = subject.workflow_graph.business_binding; real_check = family.check_binding
    s = fixture(monkeypatch)
    monkeypatch.setattr(subject.workflow_graph, 'business_binding', real_resolver)
    monkeypatch.setattr(family, 'check_binding', real_check)
    if version == 'v278':
        from tests.test_authored_decision_workflow_v278 import command, cold_binding
    elif version == 'v286':
        from tests.test_authored_decision_workflow_v286 import command, cold_binding
    else:
        from tests.test_authored_service_workflow_v270 import command, cold_binding
    from generic_agents import authored_workflow_runtime_v208 as runtime
    from generic_agents import authored_workflow_store_v208 as common
    binding = cold_binding(command()); issued = common.origin(ident(80), binding, 'b' * 64)
    business = {'id': 'business', 'name': binding['business_name'], 'flow': binding['flow'], 'available': True,
        'issues': [], 'execution_mode': 'confirmed_graph_workflow', runtime.BUSINESS_MARKER: issued}
    roles = {key: {field: value[field] for field in ('user_id', 'username')}
        for key, value in binding['role_bindings'].items()}
    snapshot = {'businesses': [business], 'roles': roles}
    snapshot['semantic_runtime_plan'] = runtime.plan_value([business], [{'origin': issued, 'binding': binding}], roles)
    s.release.data = {'snapshot': snapshot}
    s.task.data['graph_runtime'] = {'graph_hash': digest(binding['graph'])}
    assert invoke(s) is (version in {'v278','v286'})
    assert len(s.events) == (1 if version in {'v278','v286'} else 0)


@pytest.mark.parametrize('damage', ['release', 'goal_hash', 'run_task', 'request_hash', 'gap_parent', 'entity',
    'fields', 'request_scope', 'query_result', 'binding'])
def test_invalid_originals_never_fall_back_to_native_acquisition(monkeypatch, damage):
    s = fixture(monkeypatch)
    if damage == 'release': s.release.package_id = ident(99)
    elif damage == 'goal_hash': s.task.data['native_goal_context_hash'] = '0' * 64
    elif damage == 'run_task': s.run.data['native_goal_task_id'] = ident(99)
    elif damage == 'request_hash': s.run.data['native_goal_request_hash'] = '0' * 64
    elif damage == 'gap_parent': s.gap.data['business_task_id'] = ident(99)
    elif damage == 'entity': s.gap.entity_id = ident(99)
    elif damage == 'fields': s.gap.data['missing_fields'] = ['not_in_goal']
    elif damage == 'request_scope': s.gap.data['request']['entity_ids'] = [ident(99)]
    elif damage == 'query_result': s.run.data['native_goal_result_hash'] = '0' * 64
    else: monkeypatch.setattr(family, 'check_binding', lambda value: (_ for _ in ()).throw(HTTPException(409, 'forged binding')))
    with pytest.raises(HTTPException): invoke(s)
    assert not s.events


@pytest.mark.parametrize('damage', ['missing_record', 'missing_audit', 'duplicate_audit', 'audit_actor', 'record', 'rehashed_record'])
def test_same_gap_handoff_requires_original_marker_and_independent_audit(monkeypatch, damage):
    s = fixture(monkeypatch); invoke(s)
    if damage == 'missing_record': s.gap.data.pop(subject.KEY)
    elif damage == 'missing_audit': s.events.clear()
    elif damage == 'duplicate_audit': s.events.append(copy.deepcopy(s.events[0]))
    elif damage == 'audit_actor': s.events[0].actor_id = ident(99)
    elif damage == 'record': s.gap.data[subject.KEY]['query_id'] = ident(99)
    else:
        record = s.gap.data[subject.KEY]; record['actor_id'] = ident(99)
        record['record_hash'] = digest({key: value for key, value in record.items() if key != 'record_hash'})
    with pytest.raises(HTTPException): invoke(s)


def test_later_checks_preserve_actual_v196_acquisition_status_and_reason(monkeypatch):
    s = fixture(monkeypatch); invoke(s)
    s.gap.status = 'awaiting_observation'; s.gap.data = {**s.gap.data,
        'acquisition_ids': [ident(60)], 'observation_plan_id': ident(61), 'decision_reason': '原V196正在等原采集回执'}
    before = copy.deepcopy((s.gap.data, s.gap.status, s.gap.version))
    assert invoke(s) is True and (s.gap.data, s.gap.status, s.gap.version) == before and len(s.events) == 1


def test_existing_query_becomes_sufficient_only_real_received_event_can_resolve(monkeypatch):
    s = fixture(monkeypatch); invoke(s)
    s.current = {'status': 'sufficient', 'result': {'status': 'sufficient',
        'observation_evidence': [{'observation_id': ident(50), 'entity_id': s.gap.entity_id,
            'attributes': {'temperature_c': 25}}]}}
    s.run.data = {**s.run.data, 'result': copy.deepcopy(s.current), 'native_goal_result_hash': digest(s.current)}
    assert invoke(s) is True and s.gap.status == 'received' and not s.gap_events
    s.received.add(ident(50))
    assert invoke(s) is True and s.gap.status == 'resolved'
    assert len(s.events) == 1 and s.gap_events[0][0] == 'verified'
    assert s.gap_events[0][1]['query_id'] == s.run.id
    version = s.gap.version; pins = len(s.pins)
    assert invoke(s) is True and s.gap.version == version and len(s.pins) == pins


def test_real_empty_field_conflict_becomes_review_without_fabricated_observation_request(monkeypatch):
    s = fixture(monkeypatch)
    s.current = {'status': 'conflict', 'result': {'status': 'conflict', 'observation_evidence': [],
        'conflicts': [{'entity_id': s.gap.entity_id, 'field': 'temperature_c', 'observation_ids': [ident(50), ident(52)]}]}}
    s.run.data = {**s.run.data, 'result': copy.deepcopy(s.current), 'native_goal_result_hash': digest(s.current)}
    s.gap.data = {**s.gap.data, 'kind': 'conflict', 'missing_fields': [],
        'conflict_fields': ['temperature_c'], 'conflict_refs': copy.deepcopy(s.current['result']['conflicts'])}
    assert invoke(s) is True and s.gap.status == 'needs_review' and not s.pins
    assert s.gap.data[subject.KEY]['observation_request'] is None and s.gap.data['missing_fields'] == []
    assert s.gap.data['decision_reason'] == subject.CONFLICT_REASON
    assert 'acquisition_ids' not in s.gap.data and len(s.events) == 1
    version = s.gap.version
    assert invoke(s) is True and s.gap.version == version and len(s.gap_events) == 1


def test_pure_conflict_with_actual_acquisition_row_preserves_its_existing_state(monkeypatch):
    s = fixture(monkeypatch)
    s.current = {'status': 'conflict', 'result': {'status': 'conflict', 'observation_evidence': []}}
    s.run.data = {**s.run.data, 'result': copy.deepcopy(s.current), 'native_goal_result_hash': digest(s.current)}
    s.gap.data = {**s.gap.data, 'kind': 'conflict', 'missing_fields': [], 'decision_reason': '原采集仍有独立历史待核'}
    s.acquisitions.add(ident(60))
    assert invoke(s) is True and s.gap.status == 'received' and not s.gap_events
    assert s.gap.data['decision_reason'] == '原采集仍有独立历史待核'


def test_plan_without_acquisition_preserves_existing_resource_wait(monkeypatch):
    s = fixture(monkeypatch)
    s.gap.data['decision_reason'] = '原V196计划正在等资源，尚未签发Acq'
    s.plans.add(ident(61))
    assert invoke(s) is True and s.gap.status == 'received'
    assert s.gap.data['decision_reason'] == '原V196计划正在等资源，尚未签发Acq'
    assert not s.acquisitions and not s.gap_events


@pytest.mark.parametrize('status', ['resolved', 'cancelled'])
def test_already_terminal_gap_with_no_handoff_is_not_reopened_or_resubmitted(monkeypatch, status):
    s = fixture(monkeypatch); s.gap.status = status
    before = copy.deepcopy((s.gap.data, s.gap.version))
    assert invoke(s) is True and not s.events and not s.pins
    assert (s.gap.data, s.gap.version) == before and s.gap.status == status


@pytest.mark.parametrize('kind,refreshed,expected', [
    ('v278', False, []), ('v278', True, []),
    ('compact', False, ['refresh', 'route', 'enqueue', 'coordinate']),
    ('legacy_graph', False, ['refresh', 'route', 'enqueue', 'coordinate']),
    ('legacy_graph', True, ['refresh', 'enqueue', 'coordinate'])])
def test_actual_native_process_order_defers_new_family_before_all_legacy_coordination(monkeypatch, kind, refreshed, expected):
    """Actual process function with explicit auth/data-agent/DB boundary spies.

    This proves orchestration order only, not PG approval or query authenticity;
    it never reports Goal success or a real acquisition/observation/ACK.
    """
    from generic_agents import native_goals, native_goal_time, agents, services
    from generic_agents import goal_conflict_observation_service_v259 as conflict
    from generic_agents.models import Memory
    s = fixture(monkeypatch, schema=subject.BINDING if kind == 'v278' else 'authored-service-workflow-binding-v270')
    if kind == 'compact':
        s.task.data.pop('graph_runtime')
        s.release.data['snapshot']['businesses'][0]['execution_mode'] = 'confirmed_human_workflow'
        monkeypatch.setattr(subject.workflow_graph, 'business_binding', lambda *a: pytest.fail('compact entered graph resolver'))
    rows = {(Task, s.task.id): s.task, (DataRun, s.run.id): s.run, (DataGap, s.gap.id): s.gap}
    monkeypatch.setattr(native_goals, 'scoped', lambda db, model, identity, p, *a, **k: rows[(model, identity)])
    monkeypatch.setattr(native_goals, 'guard', lambda *a, **k: s.task.data['native_goal_context'])
    monkeypatch.setattr(conflict, '_verify_task', lambda *a: [])
    monkeypatch.setattr(native_goal_time, 'admitted', lambda *a: True)
    monkeypatch.setattr(native_goal_time, 'timeout', lambda *a: False)
    monkeypatch.setattr(native_goal_time, 'expire', lambda *a: False)
    monkeypatch.setattr(native_goal_time, 'arm', lambda *a, **k: None)
    monkeypatch.setattr(agents, 'data_agent', lambda *a, **k: copy.deepcopy(s.current))
    monkeypatch.setattr(native_goals, 'assessment', lambda *a, **k:
        ({'status': 'insufficient'}, {'ready_for_acceptance': False, 'completed': False, 'basis_hash': 'a' * 64}))
    calls = []
    monkeypatch.setattr(conflict, 'refresh_owned_gap', lambda *a: calls.append('refresh') or refreshed)
    monkeypatch.setattr(data_gaps, 'route', lambda *a: calls.append('route') or 'succeeded')
    monkeypatch.setattr(data_gaps, 'coordinate', lambda *a: calls.append('coordinate') or True)
    monkeypatch.setattr(services, 'enqueue_event', lambda *a, **k:
        calls.append('enqueue') or NS(status='pending_delivery', attempts=0))
    saved = []
    class ProcessIO(type(s.db)):
        def add(self, row):
            assert isinstance(row, Memory), 'no parallel Task, query, Acquisition or ACK row'
            row.id = ident(95); saved.append(row)
        def flush(self): pass
    db = ProcessIO()
    goal_job = NS(data={'task_id': s.task.id, 'context_hash': s.task.data['native_goal_context_hash']})
    native_goals.process(db, s.p, goal_job)
    assert calls == expected and len(saved) == 1
    assert saved[0].data['report']['evaluation']['completed'] is False
    assert len(s.events) == (1 if kind == 'v278' else 0)
