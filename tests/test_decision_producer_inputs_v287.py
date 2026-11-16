"""Original V284 journal → V285 producer, with explicit read-only IO spies.

Production prepare_request, input/manifest/result schemas, saved_result,
_checked_state_for_actor, raw parser, closed-ledger checks, native Invocation
and Audit checks remain active. Graph, source/Frame/Evidence authenticity and
SQL row retrieval are deliberate spies, not IAM, PG, provider or E2E proof.
No dispatch, paid retry, new capture, Acquisition or ACK occurs here.
"""
import base64
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import business_decision_runtime_v284 as runtime
from generic_agents import business_decision_frame_v276 as frame_family
from generic_agents import business_decision_contracts_v284 as decision
from generic_agents import workflow_decision_tool_planner_v285 as subject
from generic_agents import workflow_graph_scope as scope
from generic_agents.business_reasoning_protocols_v276 import recovery_record
from generic_agents.contracts import Manifest, digest
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Task
from tests.test_workflow_decision_tool_planner_v285 import case, ident


def sql(statement):
    return str(statement.compile(compile_kwargs={'literal_binds': True}))


def scene(monkeypatch):
    value = case()
    value.model['candidate_reviews'][0]['suitability'] = 'uncertain'
    value.model['recommendation'].update(candidate_ref=None, supporting_fact_refs=[])
    result = decision.derive_result(value.frame['decision_input'], value.model)
    original = NS(workspace_id=ident(1), user_id=ident(3), role='operator')
    reader = NS(workspace_id=original.workspace_id, user_id=ident(2), role='operator')
    data_packet = {'id': ident(101), 'evidence_id': ident(102), 'sha256': 'a' * 64,
        'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False}
    document_packet = {'id': ident(103), 'evidence_id': ident(104), 'sha256': 'b' * 64,
        'version': 1, 'source_set_id': ident(105), 'source_set_hash': 'c' * 64}
    inputs = {'question': '核对本次模拟温度资料，并保留观测不足和不推荐。',
        'data_packet': data_packet, 'document_packet': document_packet,
        'decision_intent': 'information_acquisition', 'candidate_specs': [{
            'candidate_ref': 'candidate_2', 'proposal': '申请当前缺失的模拟观测，另行审批和接收。',
            'claims': [], 'capability_pins': [{'capability_ref': 'cap_resense', **value.target,
                'delivery': 'requested_observation', 'ports': [{'field': 'temperature_c',
                    'pointer': '/requested_observation/temperature_c'}]}]}]}
    # Match the normal graph: redundant evidence_ids and Invocation.inputs are
    # absent. Evidence identities are derived from the two original packet refs.
    assert 'evidence_ids' not in inputs
    model_data = Manifest(kind='foundation_model', namespace='producer_v287', name='model',
        version='1.0.0', owner=original.user_id, description='Synthetic provider declaration',
        allowed_agent_roles=['business'], execution={'adapter': 'cloud', 'provider': 'qwen',
            'model': 'qwen-plus', 'max_tokens': 128}, data_policy={'cloud_allowed': True}).model_dump(mode='json')
    model = NS(id=ident(106), data=model_data, content_hash=digest(model_data))
    cap_data = Manifest(kind='tool', namespace='producer_v287', name='reasoner', version='1.0.0',
        owner=original.user_id, description='Original V284 review fixture', allowed_agent_roles=['business'],
        input_schema=runtime.input_schema(), output_schema=runtime.output_schema(),
        execution={'adapter': runtime.ADAPTER, 'model_profile_id': model.id,
            'model_profile_hash': model.content_hash, 'side_effect': False},
        specification={'schema': runtime.CONTRACT, 'instruction': 'Review the original declarations.'},
        dependencies=[model.id], data_policy={'cloud_allowed': True}, budget={'max_attempts': 1}).model_dump(mode='json')
    cap = NS(id=ident(107), workspace_id=original.workspace_id, kind='tool', status='published',
        release_version='1.0.0', data=cap_data, content_hash=digest(cap_data), validated_hash=digest(cap_data))
    node = {'id': 'reasoner', 'actor_id': original.user_id, 'execution': {
        'adapter': runtime.ADAPTER, 'agent_role': 'business', 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'release_version': cap.release_version,
        'input_schema': cap_data['input_schema'], 'output_schema': cap_data['output_schema']}}
    activation = {'id': 'reasoner', 'node_id': node['id'], 'status': 'completed', 'epoch': 0,
        'output': copy.deepcopy(result)}
    current = {'id': 'planner', 'epoch': 0, 'inputs_snapshot': {'bindings': {'decision_result': {
        'missing': False, 'producer_activation_id': activation['id'], 'value_hash': digest(result)}}}}
    state = {'graph_hash': 'd' * 64, 'activations': {'reasoner': activation, 'planner': current}}
    task = NS(id=ident(10), workspace_id=original.workspace_id, data={'graph_runtime': state})
    key = 'graph:' + task.id + ':reasoner:1'
    job = NS(id=ident(108), workspace_id=original.workspace_id, kind=scope.KIND, status='succeeded',
        idempotency_key=key, data={'task_id': task.id, 'actor_id': original.user_id,
            'activation_id': activation['id'], 'invocation_key': key, 'inputs': copy.deepcopy(inputs),
            'input_hash': digest(inputs), 'graph_hash': state['graph_hash'], 'cancel_epoch': 0})
    consumer = NS(data={'activation_id': current['id']})
    owner = {'job_id': job.id, 'lease_token': 'original-dispatch-lease'}
    transport = {'job_id': job.id, 'invocation_id': ident(109), 'phase': 'succeeded',
        'dispatch_committed': True, 'dispatch_ownership': owner, 'output_hash': digest(result),
        'dispatch_authorization': {**owner, 'actor_id': original.user_id, 'epoch': 0,
            'input_hash': digest(inputs), 'capability_hash': cap.content_hash}}
    task.data['graph_jobs'] = {activation['id']: transport}
    frame = copy.deepcopy(value.frame)
    frame['sources'].update(schema=runtime.sources.SCHEMA, task_id=task.id,
        evidence_ids=[data_packet['evidence_id'], document_packet['evidence_id']],
        documents={'packet': copy.deepcopy(document_packet)})
    frame['sources']['data']['packet'] = copy.deepcopy(data_packet)
    frame['sources']['sources_hash'] = digest(frame['sources'])
    original_scope = {'schema': scope.SCHEMA, 'task_id': task.id, 'graph_hash': state['graph_hash'],
        'activation_id': activation['id'], 'node_id': node['id'], 'cancel_epoch': 0,
        'invocation_key': key, 'input_hash': digest(inputs), 'capability_id': cap.id,
        'capability_hash': cap.content_hash}
    monkeypatch.setattr(runtime, '_current', lambda *args: (cap, model, task, job, node, original_scope))
    monkeypatch.setattr(runtime, '_build_frame', lambda *args: copy.deepcopy(frame))
    monkeypatch.setattr(runtime, '_evidence', lambda *args: ([{'evidence_id': identity,
        'model_profile_id': model.id} for identity in frame['sources']['evidence_ids']], []))
    def prepared(actual_model, messages, schema):
        assert actual_model is model and schema == decision.model_value_schema()
        options = {'max_tokens': 128}
        return {'messages': copy.deepcopy(messages), 'options': options,
            'request_hash': digest(messages), 'dispatch_options_hash': digest(options), 'token_bound': 2000}
    monkeypatch.setattr(runtime.transport, 'prepare', prepared)
    _model, frozen = runtime.prepare_request(None, original, cap, inputs, key)
    raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value.model)}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}}).encode()
    receipt = {'call_id': ident(110), 'provider': 'qwen', 'model': 'qwen-plus', 'http_status': 200,
        'request_id': 'synthetic-original', 'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}}
    capture = {'raw_base64': base64.b64encode(raw).decode(), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'receipt': receipt}
    call = NS(id=ident(109), workspace_id=original.workspace_id, capability_id=cap.id,
        idempotency_key=key, status='succeeded', data={'role': 'business', 'approval': None,
            'side_effect': False, **owner, 'input_hash': digest(inputs), 'capability_hash': cap.content_hash,
            'output': copy.deepcopy(result), runtime.STATE_KEY: {'schema': runtime.STATE,
                'request': frozen, 'call_id': receipt['call_id'], 'capture': capture}})
    ledger = NS(id=receipt['call_id'], workspace_id=original.workspace_id, capability_id=model.id,
        status='succeeded', reserved_tokens=2000, charged_tokens=50, data={'actor_id': original.user_id,
            'request_hash': frozen['prepared']['request_hash'],
            'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'], 'receipt': copy.deepcopy(receipt),
            'gateway_dispatch': {'identity': {'suggestion_id': call.id, 'job_id': job.id, 'task_id': task.id,
                'request_hash': frozen['request_hash'], 'workspace_id': original.workspace_id,
                'actor_id': original.user_id, 'capability_id': model.id}}})
    rows = {(Task, task.id): task, (Job, job.id): job, (Invocation, call.id): call,
        (Capability, cap.id): cap, (CloudCall, ledger.id): ledger}
    def fresh(db, actual_p, cls, identity):
        row = rows[(cls, identity)]
        assert actual_p is reader and row.workspace_id == actual_p.workspace_id
        return row
    monkeypatch.setattr(subject.sources, 'fresh', fresh)
    monkeypatch.setattr(runtime, 'scoped', lambda db, cls, identity, actual_p:
        fresh(db, actual_p, cls, identity))
    def native_scope(db, actual_p, task_id, activation_id):
        assert actual_p is reader and task_id == task.id
        return task, None, None, None, state, state['activations'][activation_id], node
    monkeypatch.setattr(scope, '_frozen', native_scope)
    monkeypatch.setattr(scope, '_job_matches', lambda *args: transport)
    monkeypatch.setattr(runtime.sources, 'checked_frozen', lambda *args: copy.deepcopy(frame['sources']))
    def checked_frame(db, actual_p, actual_task, actual_job, actual_cap, actual_inputs, supplied, *, _server_protocol):
        assert actual_p is reader and actual_job is job and actual_cap is cap
        assert _server_protocol == runtime.ADAPTER and actual_inputs == inputs and supplied == frame
        return copy.deepcopy(frame)
    monkeypatch.setattr(frame_family, 'checked_frame', checked_frame)
    marker, recovery_action = recovery_record(runtime.ADAPTER)
    events = {runtime.PREPARED: [], runtime.RESERVED: [], runtime.CAPTURED: [],
        'capability.invoke': [NS(actor_id=original.user_id, data={'status': 'succeeded'})], recovery_action: [],
        'workflow.graph.node_completed': [NS(actor_id=original.user_id, data={
            'activation_id': activation['id'], 'invocation_id': call.id})],
        'workflow.graph.dispatch_authorized': [NS(actor_id=original.user_id, data={
            'activation_id': activation['id'], 'job_id': job.id, 'epoch': 0, 'input_hash': digest(inputs)})]}
    def reanchor():
        anchor = runtime._anchor(frozen)
        for action, data in [(runtime.PREPARED, anchor),
                (runtime.RESERVED, {**anchor, 'call_id': ledger.id}),
                (runtime.CAPTURED, {**anchor, 'call_id': ledger.id,
                    'raw_sha256': capture['raw_sha256'], 'receipt_hash': digest(receipt)})]:
            events[action] = [NS(actor_id=original.user_id, data=copy.deepcopy(data))]
    reanchor()
    queries = {sql(select(Audit).where(Audit.workspace_id == reader.workspace_id,
        Audit.action == action, Audit.object_id == call.id)): action
        for action in (runtime.PREPARED, runtime.RESERVED, runtime.CAPTURED)}
    for action in ('capability.invoke', recovery_action):
        queries[sql(select(Audit).where(Audit.workspace_id == reader.workspace_id,
            Audit.action == action, Audit.object_id == call.id).limit(2))] = action
    for action, field, identity in [('workflow.graph.node_completed', 'activation_id', activation['id']),
            ('workflow.graph.dispatch_authorized', 'job_id', job.id)]:
        queries[sql(select(Audit).where(Audit.workspace_id == reader.workspace_id, Audit.action == action,
            Audit.object_id == task.id, Audit.data[field].as_string() == identity).limit(2))] = action
    ledger_query = sql(select(CloudCall).where(CloudCall.workspace_id == reader.workspace_id,
        CloudCall.id == ledger.id).execution_options(populate_existing=True))
    class ReadOnlyIO:
        def scalar(self, query):
            assert query.get_execution_options().get('populate_existing') is True
            assert sql(query) == ledger_query, 'unrecognized SQL is rejected'
            return ledger
        def scalars(self, query):
            assert sql(query) in queries, 'unrecognized SQL is rejected'
            return events[queries[sql(query)]]
        def add(self, *args): pytest.fail('producer proof attempted storage writes')
        def commit(self): pytest.fail('producer proof attempted a commit')
    monkeypatch.setattr(runtime.transport, 'call', lambda *args, **kwargs: pytest.fail('provider boundary reached'))
    return NS(reader=reader, original=original, task=task, consumer=consumer, job=job, call=call,
        cap=cap, ledger=ledger, result=result, frame=frame, frozen=frozen, capture=capture,
        events=events, raw=raw, reanchor=reanchor, db=ReadOnlyIO())


def producer(s):
    return subject._producer(s.db, s.reader, s.task, s.consumer, s.result)


@pytest.mark.parametrize('top_present', [False, True])
def test_original_job_and_real_frozen_request_are_authoritative_with_optional_equal_top_inputs(monkeypatch, top_present):
    s = scene(monkeypatch)
    if top_present: s.call.data['inputs'] = copy.deepcopy(s.job.data['inputs'])
    before = copy.deepcopy((s.call.data, s.job.data, s.ledger.data))
    pin, frame = producer(s)
    assert pin['input_hash'] == digest(s.job.data['inputs'])
    assert pin['actor_id'] == s.original.user_id != s.reader.user_id
    assert pin['capture_hash'] == hashlib.sha256(s.raw).hexdigest() and frame == s.frame
    assert (s.call.data, s.job.data, s.ledger.data) == before
    assert ('inputs' in s.call.data) is top_present


@pytest.mark.parametrize('wrong', [None, {}, {'question': 'a different original input'}])
def test_present_top_inputs_must_match_even_when_full_original_journal_is_valid(monkeypatch, wrong):
    s = scene(monkeypatch); s.call.data['inputs'] = wrong
    with pytest.raises(HTTPException): producer(s)


def test_missing_top_inputs_does_not_allow_changed_frozen_request_even_with_resealed_outer_anchors(monkeypatch):
    s = scene(monkeypatch)
    s.frozen['inputs']['question'] += ' altered'
    s.frozen['request_hash'] = digest({key: value for key, value in s.frozen.items() if key != 'request_hash'})
    s.ledger.data['gateway_dispatch']['identity']['request_hash'] = s.frozen['request_hash']
    s.reanchor()
    with pytest.raises(HTTPException): producer(s)


@pytest.mark.parametrize('damage', ['missing_capture', 'missing_capture_audit', 'changed_raw_hash', 'unsettled_ledger'])
def test_absent_optional_top_field_never_substitutes_for_capture_audit_or_terminal_ledger(monkeypatch, damage):
    s = scene(monkeypatch)
    if damage == 'missing_capture': s.call.data[runtime.STATE_KEY]['capture'] = None
    elif damage == 'missing_capture_audit': s.events[runtime.CAPTURED] = []
    elif damage == 'changed_raw_hash': s.capture['raw_sha256'] = '0' * 64
    else: s.ledger.status = 'reserved'
    with pytest.raises(HTTPException): producer(s)
