"""Actual source assembly/manifest/schema/CAS/units run with explicit IO spies.

V312 custody, IAM, publication, evaluation SDK/Qe/ACK authenticity and independent
original verification are labelled IO boundaries here. No PG, real broker,
actual authorization, producer invocation or full product journey is claimed.
"""
import copy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_grant_sources_v313 as s
from generic_agents.contracts import GoalSpec, digest
from generic_agents.layered_approval_contract_v175 import PolicyCommand
from generic_agents.models import Capability, Entity, Plan, Task
from generic_agents.security import Principal
from tests.test_same_goal_advice_contracts_v302 import ref

AT = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)


def uid(label): return ref('grant-source-' + label)['id']


@pytest.fixture
def scene(monkeypatch):
    p = Principal(uid('actor'), uid('workspace'), 'admin', 'actual-IAM-IO')
    spec = GoalSpec.model_validate({'purpose': '原业务结论的只读汇总',
        'required_fields': ['temperature', 'category'], 'entity_ids': [uid('entity')],
        'max_steps': 12, 'max_tool_calls': 20, 'deadline': None,
        'subgoals': [{'id': 'original_data', 'description': '核对原范围资料', 'criterion_ids': ['sufficient']}],
        'criteria': [{'id': 'sufficient', 'description': '原范围资料充分', 'kind': 'evidence_sufficient'}],
        'evidence': {'source': 'either', 'minimum_verification': 'confirmed'}}).model_dump(mode='json')
    task = Task(id=uid('task'), workspace_id=p.workspace_id, package_id=uid('package'), owner_id=p.user_id,
        title='原目标', status='workflow_completed', version=5,
        data={'goal_spec': spec, 'goal_hash': digest(spec), 'goal_version': 1,
            'goal_confirmed_by': p.user_id, 'goal_confirmed_at': AT.isoformat(),
            'goal_window': {'reference_at': AT.isoformat()}, 'native_goal_context_hash': 'a' * 64,
            'entity_ids': spec['entity_ids'], 'instance_id': uid('instance'), 'business_id': 'business_one'})
    goal = s.snapshot(task)
    source = {'task_id': task.id, 'advice_plan_id': uid('advice'), 'decision_intent': 'business_conclusion',
        'original_graph_hash': 'b' * 64, 'review_decision': 'accepted'}
    continuation = Plan(id=uid('continuation'), workspace_id=p.workspace_id, task_id=task.id,
        version=2, status='draft', data={'source_hash': digest(source)})
    advice = Plan(id=uid('advice'), workspace_id=p.workspace_id, task_id=task.id, status='reviewed')
    revision = {'record_hash': 'c' * 64, 'draft': {'purpose': '人工拟定的办理方案'}}
    package_data = {'specification': {'entity_schemas': {'station': {'type': 'object', 'properties': {
        'temperature': {'type': 'number', 'unit': '℃'}, 'category': {'type': 'string'}}}}}}
    package = Capability(id=task.package_id, workspace_id=p.workspace_id, data=package_data,
        content_hash=digest(package_data), validated_hash=digest(package_data), status='published')
    release = NS(id=uid('release'), package_id=package.id, configuration_hash='d' * 64, data={'snapshot': {
        'object_types': [{'key': 'station', 'fields': [{'key': 'temperature', 'label': '气温', 'unit': '℃'},
            {'key': 'category', 'label': '类别', 'unit': None}]}]}})
    entity = Entity(id=uid('entity'), workspace_id=p.workspace_id, entity_type='station')
    cap_data = s.c.manifest()
    cap = Capability(id=uid('tool'), workspace_id=p.workspace_id, kind='tool', namespace=s.c.NAMESPACE,
        name=cap_data['name'], release_version='1.0.0', status='published', data=cap_data,
        content_hash=digest(cap_data), validated_hash=digest(cap_data))
    archived = {'entity_id': entity.id, 'version': 1, 'attributes': {'temperature': 20},
        'observed_at': AT.isoformat(), 'source': 'resource_entity'}
    observed = {'observation_id': uid('observation'), 'entity_id': entity.id, 'source_id': uid('source'),
        'version': 2, 'attributes': {'temperature': 25}, 'observed_at': AT.isoformat()}
    record = {'scope': {'task_id': task.id}, 'goal_snapshot': goal, 'record_hash': 'e' * 64,
        'cut_at': AT.isoformat(), 'evaluation_query_ref': {'query_id': uid('query'), 'version': 1,
            'request_hash': '1' * 64, 'result_hash': '2' * 64, 'data_hash': '3' * 64},
        'evaluation_result': {'status': 'sufficient', 'result': {'status': 'sufficient', 'conflicts': [],
            'evidence': [archived], 'observation_evidence': [observed]}}, 'simulation_only': True, 'test_only': True}
    evaluation = NS(id=uid('evaluation'), task_id=task.id, record_hash=record['record_hash'], generation=4, data=record)
    evaluation_ref = {'id': evaluation.id, 'record_hash': evaluation.record_hash}
    current = {'evaluation_ref': evaluation_ref, 'evidence': {'status': 'sufficient', 'conflicts': []},
        'goal_check_job_id': uid('goaljob'), 'goal_check_id': uid('memory'), 'report_hash': '4' * 64}
    task.data = {**task.data, s.provider.KEY: {'evaluation_ref': evaluation_ref, 'head_hash': '5' * 64}}
    stage = {'name': '业务负责人审批', 'role_key': 'leader', 'scope_id': uid('unit')}
    policy_spec = PolicyCommand.model_validate({'request_id': uid('policy-command'),
        'expected_version': 0, 'name': '原业务层级审批政策', 'scope_id': uid('unit'),
        'instance_id': task.data['instance_id'], 'business_id': task.data['business_id'],
        'stages': [stage], 'executor': stage, 'acceptance': stage, 'enabled': True}).model_dump(mode='json')
    policy = NS(id=uid('policy'), workspace_id=p.workspace_id, instance_id=task.data['instance_id'],
        business_id=task.data['business_id'])
    policy_value = {'schema': 'approval-policy-version-v175', 'spec': policy_spec}
    version = NS(id=uid('policyversion'), workspace_id=p.workspace_id, policy_id=policy.id, revision=3,
        data={**policy_value, 'record_hash': digest(policy_value)})
    policy_ref = {'policy_id': policy.id, 'version_id': version.id, 'revision': version.revision,
        'record_hash': digest(version.data)}
    t = NS(p=p, task=task, continuation=continuation, advice=advice, revision=revision, source=source,
        package=package, release=release, entity=entity, cap=cap, caps=[cap], record=record, evaluation=evaluation,
        current=current, policy=policy, version=version, policy_spec=policy_spec, policy_ref=policy_ref,
        active=True, permitted=True, origin_ok=True, events=[], at=AT)
    t.rows = {(Plan, advice.id): advice, (Capability, cap.id): cap, (Capability, package.id): package,
        (Entity, entity.id): entity, (s.Evaluation, evaluation.id): evaluation}
    class DB:
        no_autoflush = nullcontext()
        def scalar(self, query):
            assert 'clock_timestamp' in str(query); t.events.append('DB-clock-IO'); return t.at
        def scalars(self, query):
            assert p.workspace_id in query.compile().params.values()
            assert s.c.SCOPE in query.compile().params.values()
            t.events.append('published-directory-SQL-IO'); return t.caps
        def add(self, row): raise AssertionError('source cannot persist')
        def flush(self): raise AssertionError('source cannot flush')
        def commit(self): raise AssertionError('source cannot commit')
    t.db = DB()
    def reader(db, actor, *, write=False):
        t.events.append(('current-IAM-IO', write))
        if not t.permitted or actor.workspace_id != p.workspace_id: raise HTTPException(403, 'current member IO refused')
    monkeypatch.setattr(s.drafts, '_reader', reader)
    def task_read(db, actor, identity, *, lock=False):
        assert identity == task.id and actor.workspace_id == p.workspace_id
        t.events.append(('Task-IO', lock)); return task
    monkeypatch.setattr(s.drafts.existing, '_task', task_read)
    monkeypatch.setattr(s, 'lock_current_actor', lambda *args: t.events.append('actor-lock-IO'))
    monkeypatch.setattr(s.drafts, '_inventory', lambda *args: [{'plan': continuation,
        'source': copy.deepcopy(source), 'revision': revision, 'events': {'explicit-custody-IO': True}}])
    monkeypatch.setattr(s.origins, '_source', lambda *args: (None, None, None, copy.deepcopy(source),
        {'release': release}, None))
    def guard(db, actor, actual, *, lock_sources):
        t.events.append(('current-Goal-IO', lock_sources))
        if not t.active: raise HTTPException(409, 'Goal revoked/expired IO')
    monkeypatch.setattr(s.origins, '_planning_guard', guard)
    def fresh(db, actor, kind, identity, *, lock=False):
        assert actor.workspace_id == p.workspace_id
        t.events.append(('fresh-row-IO', kind, identity, lock)); return t.rows[(kind, identity)]
    monkeypatch.setattr(s.provider, '_fresh', fresh)
    def publication(db, actor, identity, kind):
        assert actor.workspace_id == p.workspace_id and kind == 'tool'
        value = t.rows[(Capability, identity)]
        if value.status != 'published': raise HTTPException(409, 'not published IO')
        assert value.content_hash == digest(value.data) == value.validated_hash
        return value
    monkeypatch.setattr(s, 'published', publication)
    monkeypatch.setattr(s.approvals, 'policy_for', lambda *args: t.policy)
    monkeypatch.setattr(s.approvals, 'current_policy', lambda *args, **kw: t.policy)
    monkeypatch.setattr(s.approvals, 'policy_value', lambda *args: (version, policy_spec))
    monkeypatch.setattr(s.approvals, 'unit', lambda *args, **kw: NS(name='本工作区实际组织 IO'))
    def current_read(db, actor, actual, *, lock_sources):
        t.events.append(('actual-evaluation-proof-IO', lock_sources)); return t.current
    monkeypatch.setattr(s.provider, 'require_current', current_read)
    def fact_origins(db, actor, actual, saved):
        t.events.append('independent-originals-IO')
        if not t.origin_ok: raise HTTPException(409, 'actual raw receipt/ACK IO refused')
        return [{'kind': 'archive', 'record': copy.deepcopy(archived), 'actual-origin-IO': 'archive'},
            {'kind': 'observation', 'record': copy.deepcopy(observed), 'actual-origin-IO': 'MQTT',
                'report': {'field_units': {'temperature': '℃'}, 'extracted': {}},
                'observation': {'data': {'field_units': {'temperature': '℃'}}}}]
    monkeypatch.setattr(s.originals, '_origins', fact_origins)
    t.body = {'request_id': uid('request'), 'expected_task_version': task.version,
        'expected_continuation_version': continuation.version, 'revision_hash': revision['record_hash'],
        'source_hash': digest(source), 'capability_pin': s._pin(cap), 'field': 'temperature',
        'operation': 'mean', 'policy_ref': policy_ref, 'validity_seconds': 7200, 'reason': '明确增量只读授权'}
    return t


def issue(t): return s.issue_basis(t.db, t.p, t.task.id, t.advice.id, t.continuation.id, t.body)


def test_real_assembly_pins_original_revision_source_policy_and_server_values_once(scene):
    t = scene; original = copy.deepcopy(t.task.data); saved = issue(t); basis = saved['basis']
    assert set(saved) == {'task', 'continuation', 'source_hash', 'source', 'basis'}
    assert basis['schema'] == s.c.BASIS and basis['record_hash'] == s.c.record_hash(basis)
    assert basis['continuation_ref'] == {'id': t.continuation.id, 'version': 2,
        'revision_hash': t.revision['record_hash'], 'source_hash': digest(t.source)}
    assert basis['inputs'] == {'field': 'temperature', 'operation': 'mean', 'records': [
        {'entity_id': t.entity.id, 'version': 2, 'attributes': {'temperature': 25}}]}
    assert basis['input_hash'] == digest(basis['inputs'])
    assert basis['provenance'][0]['observation_id'] == uid('observation')
    assert basis['field'] == {'key': 'temperature', 'label': '气温', 'unit': '℃'}
    assert basis['policy'] == {'ref': t.policy_ref, 'spec': t.policy_spec,
        'subject_kind': 'goal_continuation_read_v313', 'application': 'explicit_additive_continuation_grant'}
    assert basis['simulation_only'] is basis['test_only'] is True
    assert basis['max_calls'] == 1 and basis['agent_role'] == 'data'
    assert all(basis[key] is False for key in ('side_effect', 'external_allowed', 'cloud_allowed'))
    assert t.events.count(('actual-evaluation-proof-IO', True)) == 1
    assert t.events.count('independent-originals-IO') == 1
    assert t.task.data == original and t.task.status == 'workflow_completed' and t.continuation.status == 'draft'
    assert t.events.index(('Task-IO', True)) < t.events.index('actor-lock-IO')


def test_deadline_clamps_real_dbclock_without_client_renewal(scene):
    t = scene; t.task.data['goal_spec']['deadline'] = (AT + timedelta(minutes=30)).isoformat()
    t.task.data['goal_hash'] = digest(t.task.data['goal_spec'])
    t.record['goal_snapshot'] = s.snapshot(t.task)
    basis = issue(t)['basis']
    assert basis['issued_at'] == AT.isoformat()
    assert basis['expires_at'] == (AT + timedelta(minutes=30)).isoformat()


@pytest.mark.parametrize('fault', ['task_version', 'plan_version', 'revision', 'source', 'cap_pin', 'policy_pin',
    'withdrawn', 'goal_expired', 'goal_accepted', 'non_admin', 'iam', 'unpublished', 'graph_handler',
    'field', 'string_field', 'origin', 'evaluation', 'all_missing', 'boolean', 'nonfinite', 'unit'])
def test_actual_source_or_authority_drift_refuses_basis_without_persistence(scene, fault):
    t = scene
    if fault == 'task_version': t.body['expected_task_version'] -= 1
    elif fault == 'plan_version': t.body['expected_continuation_version'] -= 1
    elif fault == 'revision': t.body['revision_hash'] = '0' * 64
    elif fault == 'source': t.body['source_hash'] = '0' * 64
    elif fault == 'cap_pin': t.body['capability_pin']['content_hash'] = '0' * 64
    elif fault == 'policy_pin': t.body['policy_ref']['record_hash'] = '0' * 64
    elif fault == 'withdrawn': t.continuation.status = 'withdrawn'
    elif fault == 'goal_expired': t.active = False
    elif fault == 'goal_accepted': t.active = False; t.task.data['native_goal_acceptance'] = {'real-guard-IO': True}
    elif fault == 'non_admin': t.p = Principal(t.p.user_id, t.p.workspace_id, 'operator', 'no-admin')
    elif fault == 'iam': t.permitted = False
    elif fault == 'unpublished': t.cap.status = 'draft'
    elif fault == 'graph_handler':
        t.cap.data['execution']['handler'] = 'workflow_task_context_v196'
        t.cap.content_hash = t.cap.validated_hash = digest(t.cap.data);t.body['capability_pin'] = s._pin(t.cap)
    elif fault == 'field': t.body['field'] = 'outside_original_Goal'
    elif fault == 'string_field': t.body['field'] = 'category'
    elif fault == 'origin': t.origin_ok = False
    elif fault == 'evaluation': t.current['evidence']['status'] = 'insufficient'
    elif fault in {'all_missing', 'boolean', 'nonfinite'}:
        t.record['evaluation_result']['result']['observation_evidence'][0]['attributes'] = (
            {} if fault == 'all_missing' else {'temperature': True if fault == 'boolean' else float('inf')})
    else:
        t.release.data['snapshot']['object_types'][0]['fields'][0]['unit'] = '℉'
    with pytest.raises(HTTPException): issue(t)


def test_context_is_human_metadata_and_has_no_records_or_implicit_grant(scene):
    t = scene; value = s.context(t.db, t.p, t.task.id, t.advice.id, t.continuation.id)
    assert value['permissions'] == {'can_issue': True} and value['schema'] == s.c.CONTEXT
    assert value['tools'] == [{**s._pin(t.cap), 'name': t.cap.data['name'], 'description': t.cap.data['description'],
        'fields': [{'key': 'temperature', 'label': '气温', 'unit': '℃'}], 'binding': 'observation_records'}]
    assert value['policy']['stages'][0]['scope_name'] == '本工作区实际组织 IO'
    assert all(value[flag] is False for flag in s.c.FLAGS)
    assert not any(key in repr(value) for key in ('"records"', 'input_hash', 'CloudCall', 'lease_token'))


@pytest.mark.parametrize('fault', ['no_tool', 'no_policy', 'stale', 'withdrawn', 'viewer'])
def test_legitimate_unavailability_is_partitioned_and_never_grants_permission(scene, fault):
    t = scene
    if fault == 'no_tool': t.caps = []
    elif fault == 'no_policy': t.policy = None
    elif fault == 'stale': t.current['evidence']['status'] = 'insufficient'
    elif fault == 'withdrawn': t.continuation.status = 'withdrawn'
    else: t.p = Principal(t.p.user_id, t.p.workspace_id, 'viewer', 'read-only')
    result = s.context(t.db, t.p, t.task.id, t.advice.id, t.continuation.id)
    assert result['permissions']['can_issue'] is False and result['blocked_reasons']


def test_aggregate_records_keep_missing_rows_and_reject_mixed_scope(scene):
    t = scene
    loaded = {'task': t.task}; field = {'key': 'temperature', 'label': '气温', 'unit': '℃', 'schema': {'type': 'number'}}
    first = copy.deepcopy(t.record['evaluation_result']['result']['observation_evidence'][0])
    second = {**copy.deepcopy(first), 'observation_id': uid('missing-observation'), 'version': 3, 'attributes': {}}
    metadata = {'report': {'extracted': {}, 'field_units': {'temperature': '℃'}},
        'observation': {'data': {'field_units': {'temperature': '℃'}}}}
    origins = [{'kind': 'observation', 'record': first, **copy.deepcopy(metadata)},
        {'kind': 'observation', 'record': second, **copy.deepcopy(metadata)}]
    records, provenance = s._records(loaded, {'origins': origins}, 'observation_records', field)
    assert records[1]['attributes'] == {} and provenance[1]['record_hash'] == digest(second)
    second['entity_id'] = uid('outside-Goal')
    with pytest.raises(HTTPException): s._records(loaded, {'origins': origins}, 'observation_records', field)


def test_archive_factory_selects_only_actual_archive_rows(scene):
    t = scene; t.cap.data = s.c.manifest('archive_records')
    t.cap.content_hash = t.cap.validated_hash = digest(t.cap.data); t.body['capability_pin'] = s._pin(t.cap)
    basis = issue(t)['basis']
    assert basis['records_binding'] == 'archive_records'
    assert basis['inputs']['records'][0]['attributes'] == {'temperature': 20}
    assert basis['provenance'][0]['observation_id'] is None


@pytest.mark.parametrize('fault', ['x_unit_conflict', 'integer_fraction', 'maximum', 'report_unit',
    'extracted_unit', 'observation_unit', 'origin_unit', 'missing_measurement_unit'])
def test_original_unit_views_and_actual_numeric_schema_are_required(scene, monkeypatch, fault):
    t = scene; properties = t.package.data['specification']['entity_schemas']['station']['properties']
    if fault == 'x_unit_conflict': properties['temperature']['x-unit'] = 'K'
    elif fault == 'integer_fraction':
        properties['temperature']['type'] = 'integer'
        t.record['evaluation_result']['result']['observation_evidence'][0]['attributes']['temperature'] = 25.5
    elif fault == 'maximum': properties['temperature']['maximum'] = 24
    if fault in {'x_unit_conflict', 'integer_fraction', 'maximum'}:
        t.package.content_hash = t.package.validated_hash = digest(t.package.data)
    else:
        original = s.originals._origins
        def changed(*args):
            rows = original(*args); observation = rows[1]
            if fault == 'report_unit': observation['report']['field_units']['temperature'] = 'K'
            elif fault == 'extracted_unit': observation['report']['extracted']['field_units'] = {'temperature': 'K'}
            elif fault == 'observation_unit': observation['observation']['data']['field_units']['temperature'] = 'K'
            elif fault == 'origin_unit': observation['origin'] = {'field_units': {'temperature': 'K'}}
            else:
                observation['report'].pop('field_units'); observation['observation']['data'].pop('field_units')
            return rows
        monkeypatch.setattr(s.originals, '_origins', changed)
    with pytest.raises(HTTPException): issue(t)


def test_x_unit_is_a_real_domain_unit_without_invented_measurement_unit(scene):
    t = scene; prop = t.package.data['specification']['entity_schemas']['station']['properties']['temperature']
    prop['x-unit'] = prop.pop('unit')
    t.release.data['snapshot']['object_types'][0]['fields'][0]['unit'] = None
    t.package.content_hash = t.package.validated_hash = digest(t.package.data)
    assert issue(t)['basis']['field']['unit'] == '℃'
