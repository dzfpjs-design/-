"""Synthetic IO boundaries around actual V288 store/contract/presentation code.

Root runs these sources. SQL inventories, IAM, leased creator, original causal
producer, independent receipt authentication and evidence storage are explicit
spies here; these are not PG, adopted-family, live IAM, SDK, ACK or model proof.
The receipt-cut/identity comparisons and immutable record code remain real.
"""
import copy
import hashlib
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import workflow_goal_evaluation_store_v288 as subject
from generic_agents import entity_release_compat, original_acquisition_reception_v127, mapped_acquisition_origin_v177
from generic_agents import instance_data_receipts, native_goals
from generic_agents import models as m
from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from generic_agents.contracts import digest
from generic_agents.temporal import instant
from tests.test_workflow_goal_evaluation_contracts_v288 import scene as contract_scene, uid, CUT, CHECKED


def same_sql(left, right):
    return str(left) == str(right) and left.compile().params == right.compile().params


def display_task(record):
    entity = record['evaluation_request']['entity_ids'][0]
    return NS(id=record['scope']['task_id'], workspace_id=record['scope']['workspace_id'], data={
        'instance_id': record['scope']['instance_id'], 'entity_ids': [entity],
        'goal_spec': record['goal_snapshot']['spec'], 'goal_version': record['goal_snapshot']['version'],
        'native_goal_query_id': record['original_query_ref']['query_id'], 'object_snapshots': [{
            'id': entity, 'type_name': '天气设备', 'version': 1,
            'attributes': {'name': '试验林地天气站', 'temperature': 21}, 'fields': [
                {'key': 'name', 'label': '名称', 'kind': '文本', 'required': True, 'unit': None},
                {'key': 'temperature', 'label': '温度', 'kind': '数值', 'required': False, 'unit': '℃'}]}]})


@pytest.fixture
def presentation(monkeypatch):
    record = contract_scene(acquisition=False)[1]; task = display_task(record)
    # Explicit frozen object-contract boundary. Real object compatibility and
    # publication are independently authenticated in adopted-runtime tests.
    monkeypatch.setattr(entity_release_compat, 'verify_task_binding_snapshot', lambda actual: {'checked': actual.id})
    return task, copy.deepcopy(record)


def test_public_evidence_has_exact_scalars_and_no_internal_provenance(presentation):
    task, record = presentation
    report = record['evaluation_result']['result']; report['observation_evidence'][0]['attributes']['temperature'] = 9007199254740993
    record['scope']['internal_debug'] = {'headers': {'Authorization': 'secret'}, 'endpoint': 'https://private.invalid'}
    body = subject._document(task, record).decode()
    assert '试验林地天气站' in body and '温度：9007199254740993；原目录单位：℃' in body
    assert '未据此独立核验本条观测单位，也未转换' in body
    assert CUT in body and report['observation_evidence'][0]['observed_at'] in body
    assert '尚不等于事实已核实' in body and '不授予采集' in body and '模拟资料' in body
    assert all(value not in body for value in ('Authorization', 'private.invalid', 'lease', uid(1), uid(2), uid(40)))
    # The human body has no self-hash cycle and cannot be altered by local pins.
    before = subject._document(task, record)
    record['record_hash'] = 'f' * 64; record['evidence_ref']['sha256'] = 'e' * 64
    assert subject._document(task, record) == before


def test_unknown_unit_null_and_conflicting_values_remain_explicit(presentation):
    task, record = presentation; field = task.data['object_snapshots'][0]['fields'][1]; field['unit'] = None
    report = record['evaluation_result']['result']; original = report['observation_evidence'][0]
    other = copy.deepcopy(original); other['attributes']['temperature'] = 22
    original['attributes']['temperature'] = None; report['observation_evidence'].append(other)
    report['status'] = 'conflict'; report['conflicts'] = [{'entity_id': original['entity_id'],
        'field': 'temperature', 'observed_at': original['observed_at']}]
    body = subject._document(task, record).decode()
    assert '原记录未提供值' in body and '温度：22' in body and '冲突待核对' in body
    assert '原目录未登记单位，未补猜或转换' in body and '℃' not in body


@pytest.mark.parametrize('value', [uid(40), 'Bearer private-token', 'password=private',
    'Authorization: Basic dXNlcjpwYXNz', 'Cookie: session=private', 'Set-Cookie: session=private',
    '"Authorization": "Basic dXNlcjpwYXNz"', '"Cookie": "session=private"',
    'Proxy-Authorization: Basic dXNlcjpwYXNz', 'X-API-Key: private',
    'refresh_token=private', 'client_secret: private', 'rtsp://user:private@camera.invalid/live'])
def test_credential_or_uuid_field_value_refuses_whole_evidence(presentation, value):
    task, record = presentation
    record['evaluation_result']['result']['observation_evidence'][0]['attributes']['temperature'] = value
    with pytest.raises(HTTPException): subject._document(task, record)


def test_public_renderer_rejects_credential_label_and_never_truncates(presentation):
    task, record = presentation; field = task.data['object_snapshots'][0]['fields'][1]
    field['label'] = 'Authorization'
    with pytest.raises(HTTPException): subject._document(task, record)
    field['label'] = '温度'; task.data['object_snapshots'][0]['attributes']['name'] = '林' * 21000
    with pytest.raises(HTTPException, match='完整呈现上限'): subject._document(task, record)


@pytest.fixture
def receipts(monkeypatch):
    from generic_agents import goal_ack_origin_mqtt_v297
    # This explicit original-acquisition IO fixture has no passive witnesses.
    # MQTT routing and reverse witness checks have separate actual-PG coverage.
    monkeypatch.setattr(goal_ack_origin_mqtt_v297, 'handles', lambda *_args: False)
    record = contract_scene(acquisition=False)[1]; task = display_task(record)
    p = NS(workspace_id=task.workspace_id, user_id=uid(90), role='viewer')
    observed = instant('2026-11-01T12:01:00+00:00'); received = instant('2026-11-01T12:02:00+00:00')
    receipt = {'event_id': uid(43), 'receipt_hash': '1' * 64, 'source_hash': '2' * 64, 'extractor_hash': '3' * 64}
    obs = NS(id=uid(40), workspace_id=p.workspace_id, entity_id=task.data['entity_ids'][0], source_id=uid(41),
        status='processed', version=2, created_at=observed, observed_at=observed,
        data={'extractor_id': uid(42), 'extracted': {'attributes': {'temperature': None}}})
    acq = NS(id=uid(39), version=3, status='succeeded', data={'closing_observation_id': obs.id})
    event = NS(id=uid(43), version=1, status='received', created_at=observed, received_at=received, data={})
    memory = NS(id=uid(44), version=1, created_at=received, data={'data_reception': receipt})
    source = NS(id=uid(41), content_hash=receipt['source_hash']); extractor = NS(id=uid(42), content_hash=receipt['extractor_hash'])
    rows = {(m.Observation, obs.id): obs, (m.Event, event.id): event,
        (m.Capability, source.id): source, (m.Capability, extractor.id): extractor}
    origin = NS(task=NS(id=uid(91), workspace_id=p.workspace_id), instance=NS(id=task.data['instance_id']),
        entity=NS(id=obs.entity_id), acquisition=acq)
    mapped = {'acquisition_id': acq.id, 'simulation': True, 'test_only': True}
    class DB:
        def scalars(self, query):
            expected = select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
                m.Memory.data['event_id'].as_string() == event.id,
                m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)
            assert same_sql(query, expected), 'Unknown SQL; no permissive default rows'
            return [memory]
    db = DB()
    monkeypatch.setattr(subject, 'fresh', lambda actual, principal, model, identity: rows[model, identity])
    monkeypatch.setattr(original_acquisition_reception_v127, 'context', lambda *_args: origin)
    monkeypatch.setattr(mapped_acquisition_origin_v177, 'guard', lambda *_args, **_kwargs: mapped)
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', lambda *_args: {'data_reception': receipt})
    state = {'result': {'observation_evidence': [{'observation_id': obs.id, 'entity_id': obs.entity_id,
        'source_id': obs.source_id, 'version': obs.version, 'observed_at': observed.isoformat(),
        'attributes': {'temperature': None}, 'data_reception': receipt}]}}
    return NS(db=db, p=p, task=task, state=state, obs=obs, origin=origin, mapped=mapped,
        event=event, memory=memory, window=record['derived_window'])


def test_ack_keeps_actual_origin_task_and_null_without_promoting_value(receipts):
    s = receipts; result = subject._acks(s.db, s.p, s.task, s.state, instant(CUT), s.window)
    assert result[0]['task_id'] == s.task.id and result[0]['origin_task_id'] == s.origin.task.id != s.task.id
    assert result[0]['fields'] == ['temperature'] and result[0]['simulation_only'] is True
    assert s.obs.data['extracted']['attributes']['temperature'] is None
    assert not set(subject.contract.FLAGS) & set(result[0])


@pytest.mark.parametrize('change', [
    lambda s: setattr(s.event, 'received_at', instant(CHECKED)),
    lambda s: setattr(s.memory, 'created_at', instant(CHECKED)),
    lambda s: setattr(s.event, 'status', 'pending'),
    lambda s: s.mapped.update(acquisition_id=uid(99)),
    lambda s: setattr(s.origin.task, 'workspace_id', uid(99)),
])
def test_original_receipt_cut_and_scope_mismatch_refuse(receipts, change):
    change(receipts); s = receipts
    with pytest.raises(HTTPException): subject._acks(s.db, s.p, s.task, s.state, instant(CUT), s.window)


def test_manual_receipt_without_original_acquisition_is_explicitly_unsupported(receipts, monkeypatch):
    monkeypatch.setattr(original_acquisition_reception_v127, 'context', lambda *_args: None)
    s = receipts
    with pytest.raises(HTTPException, match='不支持该观测来源'):
        subject._acks(s.db, s.p, s.task, s.state, instant(CUT), s.window)


@pytest.fixture
def runtime(presentation, monkeypatch):
    task, record = presentation; record = copy.deepcopy(record)
    p = NS(workspace_id=task.workspace_id, user_id=record['producer']['actor_id'], role='operator')
    inputs = {'local_plan': {'synthetic_boundary': 'actual plan schema/auth are separately tested'}}
    record['producer']['input_hash'] = digest(inputs)
    job = NS(id=record['request_id'], kind='execute_full_semantic_graph_node', idempotency_key=record['request_key'], data={
        'task_id': task.id, 'actor_id': p.user_id, 'activation_id': 'evaluation_activation', 'input_hash': digest(inputs)})
    cap = NS(id=record['producer']['capability_id'], content_hash=record['producer']['capability_hash'])
    approval = {'task_id': task.id, 'synthetic': 'original approval boundary'}
    call = NS(id=record['evaluation_id'], idempotency_key=job.idempotency_key,
        data={'inputs': inputs, 'input_hash': digest(inputs), 'approval': approval,
            'lease_token': record['producer']['lease_token']})
    frozen = {'query_ref': record['evaluation_query_ref'], 'request': record['evaluation_request'],
        'result': record['evaluation_result'], 'provenance': {'scope': record['scope'],
            'creator': {**record['producer'], 'request_key': job.idempotency_key},
            **{key: record[key] for key in ('original_query_ref', 'preparation_policy', 'goal_snapshot',
                'cut_at', 'cut_checked_at', 'derived_window')}}}
    upstream = {'binding': {'preparation_policy': record['preparation_policy'], 'simulation_only': True},
        'trigger': record['trigger'], 'resense': None}
    s = NS(task=task, p=p, job=job, cap=cap, call=call, approval=approval, inputs=inputs,
        record=record, created=[], events=[], queries=0, content=None, evidence=None, auth=[], reads=[])
    class DB:
        @property
        def no_autoflush(self): return nullcontext()
        def scalar(self, query):
            if same_sql(query, select(func.clock_timestamp())): return instant(CUT)
            expected = select(GoalEvaluationV288).where(GoalEvaluationV288.workspace_id == p.workspace_id,
                GoalEvaluationV288.id == call.id)
            assert same_sql(query, expected), 'Unknown scalar SQL'
            return s.created[0] if s.created else None
        def scalars(self, query):
            audits = select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action == subject.CREATED, m.Audit.object_id == call.id).limit(2)
            if same_sql(query, audits): return list(s.events)
            generations = select(GoalEvaluationV288).where(GoalEvaluationV288.workspace_id == p.workspace_id,
                GoalEvaluationV288.task_id == task.id, GoalEvaluationV288.goal_version == task.data['goal_version'])
            generations = generations.order_by(GoalEvaluationV288.generation).limit(1001)
            assert same_sql(query, generations), 'Unknown inventory SQL'
            return list(s.created)
        def add(self, row):
            assert type(row) is GoalEvaluationV288, 'Only independent evaluation record; no SDK/Job/ACK/Goal write'
            s.created.append(row)
        def flush(self): pass
        def commit(self): pytest.fail('store has no transaction boundary')
    s.db = DB(); s.queued = []
    monkeypatch.setattr(native_goals, 'queue', lambda db, principal, task, key, **extra: s.queued.append((key, copy.deepcopy(extra))))
    # Explicit leased creator, approved upstream, frozen query and receipt IO
    # boundaries. Store orchestration, closed record, journal, audits remain real.
    monkeypatch.setattr(subject, '_inputs', lambda value: copy.deepcopy(value))
    def approval_check(*_args): s.auth.append('live creator'); return {'task': task, 'job': job}
    monkeypatch.setattr(subject, 'validate_call_approval', approval_check)
    monkeypatch.setattr(subject.service, '_call', lambda *_args, **_kwargs: call)
    monkeypatch.setattr(subject, '_upstream', lambda *_args: copy.deepcopy(upstream))
    monkeypatch.setattr(subject, '_acks', lambda *_args: copy.deepcopy(record['ack_refs']))
    monkeypatch.setattr(subject.queries, '_reader', lambda db, reader: s.reads.append(reader.user_id))
    monkeypatch.setattr(subject.queries, 'snapshot', lambda actual: copy.deepcopy(record['goal_snapshot']))
    monkeypatch.setattr(subject.queries, 'checked_frozen', lambda *_args, **_kwargs: copy.deepcopy(frozen))
    def create(_db, principal, actual_task, actual_job, **kwargs):
        assert principal is p and actual_task is task and actual_job is job
        assert kwargs == {'evaluation_id': call.id, 'cut_at': CUT,
            'parent_query_id': task.data['native_goal_query_id'], 'preparation_policy': record['preparation_policy']}
        s.queries += 1; return NS(id=record['evaluation_query_ref']['query_id'])
    monkeypatch.setattr(subject.queries, 'create_frozen', create)
    def evidence(_db, principal, content, media_type, *, cloud_model_id):
        assert principal is p and media_type == 'text/plain' and cloud_model_id is None
        s.content = content; s.evidence = NS(id=record['evidence_ref']['evidence_id'],
            sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content), media_type=media_type,
            data={'cloud_model_ids': []})
        return s.evidence
    monkeypatch.setattr(subject, 'save_evidence', evidence)
    monkeypatch.setattr(subject, 'evidence_bytes', lambda *_args: (s.evidence, s.content))
    def audit(_db, principal, action, object_id, data):
        s.events.append(NS(action=action, object_id=object_id, actor_id=principal.user_id, data=copy.deepcopy(data)))
    monkeypatch.setattr(subject, 'audit', audit)
    return s


def invoke(s): return subject.execute(s.db, s.p, s.cap, s.inputs, s.job.idempotency_key, s.approval)


def test_save_and_reconcile_are_original_only_and_leave_q0_goal_unchanged(runtime):
    s = runtime; before = copy.deepcopy(s.task.data)
    result = invoke(s)
    assert s.queries == 1 and len(s.created) == len(s.events) == 1 and s.events[0].action == subject.CREATED
    assert s.created[0].id == s.call.id and s.created[0].data['request_id'] == s.job.id
    assert s.created[0].original_query_id != s.created[0].evaluation_query_id
    assert s.task.data == before and all(result[key] is False for key in subject.ports.FLAGS)
    assert s.queued == [('goal-evaluation-v288:' + s.call.id, {
        'goal_evaluation_ref_v288': result['evaluation_ref'], 'goal_evaluation_producer_job_v288': s.job.id})]
    assert result['route'] == 'data_ready' and result['packet']['cloud_authorized'] is False
    assert subject.reconcile(s.db, s.p, s.cap, s.inputs, s.job.idempotency_key, s.approval, s.call) == result
    assert invoke(s) == result and s.queries == 1 and len(s.created) == len(s.queued) == 1


def test_orphan_record_and_missing_journal_cannot_generate_new_query(runtime):
    s = runtime; invoke(s); del s.call.data[subject.KEY]
    with pytest.raises(HTTPException, match='journal缺失'): invoke(s)
    assert s.queries == 1 and len(s.created) == 1


def test_foreign_state_never_starts_new_evaluation(runtime):
    s = runtime; s.call.data[subject.FOREIGN[0]] = {'old': 'not a V288 evaluation journal'}
    with pytest.raises(HTTPException, match='其它协议状态'): invoke(s)
    assert s.queries == 0 and s.created == []


def test_acquire_does_not_borrow_unrelated_original_ack(runtime, monkeypatch):
    s = runtime
    def upstream(*_args):
        return {'binding': {'preparation_policy': s.record['preparation_policy'], 'simulation_only': True},
            'trigger': s.record['trigger'], 'resense': {'service_receipt': {'observations': [{
                'acquisition_id': uid(999), 'observation_id': uid(998), 'data_receipt_hash': '9' * 64}]}}}
    monkeypatch.setattr(subject, '_upstream', upstream)  # original V271 authentication boundary
    with pytest.raises(HTTPException): invoke(s)
    assert s.created == [] and s.events == [] and s.evidence is None


def test_cold_saved_result_uses_reader_iam_and_actual_completion_boundary(runtime, monkeypatch):
    s = runtime; original = invoke(s); independent = NS(user_id=uid(98), workspace_id=s.p.workspace_id, role='viewer')
    checks = []
    def completed(_db, principal, task, activation_id, node_id):
        checks.append((principal.user_id, activation_id, node_id))
        return {'task': task, 'job': s.job, 'call': s.call, 'cap': s.cap, 'approval': s.approval}
    monkeypatch.setattr(subject, '_cold_node', completed)  # original SDK/native completion/graph boundary
    s.call.data['output'] = original
    assert subject.saved_result(s.db, independent, s.task, s.job, s.call, s.cap, s.inputs) == original
    assert checks == [(independent.user_id, 'evaluation_activation', 'evaluation_packet')]
    assert s.reads[-1] == independent.user_id and s.queries == 1


def test_unknown_receipt_cannot_recompute_unsaved_evaluation(runtime):
    s = runtime
    with pytest.raises(HTTPException, match='不重跑查询'):
        subject.reconcile(s.db, s.p, s.cap, s.inputs, s.job.idempotency_key, s.approval, s.call)
    assert s.queries == 0 and s.created == []


def test_cold_iam_denial_does_not_write_or_recompute(runtime, monkeypatch):
    s = runtime; invoke(s)
    def denied(*_args): raise HTTPException(403, 'synthetic denied current reader')
    monkeypatch.setattr(subject.queries, '_reader', denied)
    with pytest.raises(HTTPException) as error:
        subject.reconcile(s.db, s.p, s.cap, s.inputs, s.job.idempotency_key, s.approval, s.call)
    assert error.value.status_code == 403 and s.queries == 1 and len(s.created) == 1


@pytest.mark.parametrize('outside_window', [False, True])
def test_received_but_excluded_resense_is_recorded_without_admitting_it(receipts, outside_window):
    s = receipts; s.origin.task.id = s.task.id
    if outside_window:
        s.obs.observed_at = instant('2026-11-01T11:00:00+00:00')
    state = {'status': 'insufficient', 'result': {'status': 'insufficient', 'observation_evidence': [],
        'excluded_evidence': [{'observation_id': s.obs.id, 'reason': 'confirmation or time does not meet Goal'}]}}
    original = copy.deepcopy(state)
    resense = {'service_receipt': {'observations': [{'acquisition_id': s.origin.acquisition.id,
        'observation_id': s.obs.id, 'data_receipt_hash': digest({'data_reception': s.memory.data['data_reception']})}],
        'followup_observations': []}}
    refs = subject._acks(s.db, s.p, s.task, state, instant(CUT), s.window, resense)
    assert len(refs) == 1 and refs[0]['query_admitted'] is False
    assert refs[0]['origin_task_id'] == s.task.id and state == original
    assert s.obs.data['extracted']['attributes']['temperature'] is None
    resense['service_receipt']['observations'][0]['data_receipt_hash'] = '9' * 64
    with pytest.raises(HTTPException): subject._acks(s.db, s.p, s.task, state, instant(CUT), s.window, resense)


def test_excluded_observation_is_not_added_without_the_actual_resense_receipt(receipts):
    s = receipts
    assert subject._acks(s.db, s.p, s.task, {'result': {'observation_evidence': []}}, instant(CUT), s.window) == []


def test_real_store_persists_and_reopens_insufficient_collection_evaluation(runtime, monkeypatch):
    # The orchestration/record/presentation are actual; creator/query/receiver IO
    # remain explicit fixture boundaries. This is not real PG/ACK/IAM proof.
    s = runtime; record = s.record; state = record['evaluation_result']
    state['status'] = state['result']['status'] = 'insufficient'
    state['result']['observation_evidence'] = []
    state['result']['gaps'] = [{'entity_id': record['goal_snapshot']['spec']['entity_ids'][0],
        'missing_fields': ['temperature']}]
    record['evaluation_query_ref']['result_hash'] = subject.hashed(state)
    record['preparation_policy']['allow_acquisition'] = True
    record['preparation_policy']['policy_hash'] = subject.contract.policy_hash(record['preparation_policy'])
    record['ack_refs'][0]['query_admitted'] = False
    record['ack_refs'][0]['origin_task_id'] = s.task.id
    record['trigger'].update(kind='acquisition_completed', reader_context_hash='a' * 64,
        resense_invocation_id=uid(999), resense_receipt_hash='b' * 64)
    ack = record['ack_refs'][0]
    # This orchestration fixture spies on _acks; the two public receipt hash
    # domains still differ exactly as in the actual V195/V271 producer.
    resense = {'service_receipt': {'observations': [{'acquisition_id': ack['acquisition_id'],
        'observation_id': ack['observation_id'], 'data_receipt_hash': digest({
            'data_reception': {'receipt_hash': ack['data_reception_hash']}})}]}}
    monkeypatch.setattr(subject, '_upstream', lambda *_args: {'binding': {
        'preparation_policy': record['preparation_policy'], 'simulation_only': True},
        'trigger': record['trigger'], 'resense': resense})
    result = invoke(s)
    assert result['route'] == 'review_required' and result['query_status'] == 'insufficient'
    assert '资料不足' in s.content.decode()
    assert s.created[0].data['ack_refs'][0]['query_admitted'] is False
    assert s.created[0].data['evaluation_result']['result']['observation_evidence'] == []
    assert subject.reconcile(s.db, s.p, s.cap, s.inputs, s.job.idempotency_key, s.approval, s.call) == result
    assert s.queries == 1 and len(s.created) == len(s.events) == 1
