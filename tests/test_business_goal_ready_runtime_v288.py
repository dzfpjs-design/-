"""Pure protocol and explicit source/producer spies, never PG/IAM/paid proof.

The UUID projection and V284 mechanical evaluator are actual functions. These
tests do not establish an adopted family, real reception, model response or
completed Goal. Root runs them; the static author has not executed them.
"""
import copy
import base64
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import business_goal_ready_runtime_v288 as subject
from generic_agents import business_goal_ready_sources_v288 as sources
from generic_agents import business_goal_ready_frame_v288 as frame
from generic_agents import business_decision_runtime_v284 as old
from generic_agents import business_decision_contracts_v284 as decision
from generic_agents.contracts import digest
from generic_agents.models import Capability, Invocation, Job
from generic_agents.models import Audit
from sqlalchemy import select
from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from generic_agents.temporal import instant
from tests.test_business_decision_runtime_v284 import declared_case, ident, model_manifest
from tests.test_workflow_goal_evaluation_contracts_v288 import scene as record_scene


def projection_fixture():
    value, model = declared_case()
    frozen = {'decision_input': value, 'sources': {'local': {'task_id': ident(10)}},
        'capability_pins': {'real_source': ident(21)}}
    inputs = {'question': '核对对象 ' + ident(20)}
    pins = [{'evidence_id': ident(40), 'media_type': 'text/plain', 'model_profile_id': ident(41)}]
    parts = [{'type': 'text', 'text': '证据引用 ' + ident(40)}]
    return frozen, inputs, pins, parts, model


def project(frozen, inputs, pins, parts, *, invocation=ident(70), key='graph:actual:one', instruction=None):
    return subject._cloud_projection(inputs, frozen, '比较当前原事实 ' + ident(10) if instruction is None else instruction, parts, pins,
        invocation_id=invocation, key=key)


def test_model_view_has_stable_private_aliases_and_actual_mechanical_refs():
    frozen, inputs, pins, parts, model = projection_fixture(); original = copy.deepcopy((frozen, inputs, pins, parts))
    value = project(frozen, inputs, pins, parts); again = project(frozen, inputs, pins, parts)
    assert value == again and (frozen, inputs, pins, parts) == original
    messages = subject._messages(value); cloud = json.dumps(messages, ensure_ascii=False)
    for identity in value['mapping']:
        assert identity not in cloud
    assert value['mapping']
    content = json.loads(messages[1]['content'][0]['text'])
    assert set(content) == {'question', 'registered_instruction', 'decision_input', 'input_hash'}
    assert 'mapping' not in content and 'original_evidence_parts' not in content
    assert value['local_input_hash'] == decision.input_hash(frozen['decision_input'])
    assert value['cloud_input_hash'] == decision.input_hash(value['decision_input'])
    assert value['local_input_hash'] != value['cloud_input_hash']
    assert value['decision_input']['candidates'][0]['candidate_ref'] == 'candidate_2'
    assert value['decision_input']['facts'][0]['fact_ref'] == 'fact_sampling'
    assert value['decision_input']['sources'][0]['source_ref'] == 'source_sampling'
    # Both actual and model-only inputs accept the same unmodified ModelValue.
    actual = decision.derive_result(frozen['decision_input'], model)
    anonymous = decision.derive_result(value['decision_input'], model)
    assert actual['model_value'] == anonymous['model_value']


def test_uuid_aliases_are_scoped_to_original_invocation_and_request():
    frozen, inputs, pins, parts, _ = projection_fixture()
    first = project(frozen, inputs, pins, parts)
    assert first['mapping'] != project(frozen, inputs, pins, parts, invocation=ident(71))['mapping']
    assert first['mapping'] != project(frozen, inputs, pins, parts, key='graph:other:one')['mapping']


@pytest.mark.parametrize('only_in', ['question', 'instruction', 'evidence_part'])
def test_uuid_found_only_in_an_outbound_text_cannot_escape_the_model_projection(only_in):
    frozen, inputs, pins, parts, _ = projection_fixture()
    identity = ident(90); instruction = '只比较已保存的实际资料'
    assert identity not in json.dumps([frozen, pins], ensure_ascii=False)
    if only_in == 'question': inputs['question'] = '用户问题中的独有编号 ' + identity
    elif only_in == 'instruction': instruction += '，原业务编号 ' + identity
    else: parts.append({'type': 'text', 'text': '正文中另有独立编号 ' + identity})
    original = copy.deepcopy((frozen, inputs, pins, parts, instruction))
    value = project(frozen, inputs, pins, parts, instruction=instruction)
    cloud = json.dumps(subject._messages(value), ensure_ascii=False)
    assert identity in value['mapping'] and identity not in cloud and value['mapping'][identity] in cloud
    assert value == project(frozen, inputs, pins, parts, instruction=instruction)
    assert value['mapping'][identity] != project(frozen, inputs, pins, parts,
        instruction=instruction, invocation=ident(71))['mapping'][identity]
    assert value['mapping'][identity] != project(frozen, inputs, pins, parts,
        instruction=instruction, key='graph:other:one')['mapping'][identity]
    assert (frozen, inputs, pins, parts, instruction) == original
    assert value['decision_input']['candidates'][0]['candidate_ref'] == 'candidate_2'


def test_logical_reference_equal_to_real_internal_uuid_is_rejected():
    frozen, inputs, pins, parts, _ = projection_fixture()
    frozen['decision_input']['candidates'][0]['candidate_ref'] = ident(20)
    with pytest.raises(HTTPException): project(frozen, inputs, pins, parts)


@pytest.mark.parametrize('secret', ['Authorization: Basic dXNlcjpwYXNz', 'Cookie: session=private',
    'password=private', 'rtsp://user:private@example.invalid/stream',
    '"Cookie": "session=private"', '"Set-Cookie": "session=private"'])
def test_anonymous_uuid_projection_does_not_make_credentials_public(secret):
    frozen, inputs, pins, parts, _ = projection_fixture(); inputs['question'] = secret
    with pytest.raises(HTTPException): project(frozen, inputs, pins, parts)


def test_uuid_projection_never_rewrites_binary_evidence():
    frozen, inputs, pins, parts, _ = projection_fixture(); pins[0]['media_type'] = 'image/png'
    with pytest.raises(HTTPException): project(frozen, inputs, pins, parts)


def test_new_runtime_owns_independent_sources_state_and_actual_shared_capture(monkeypatch):
    assert subject.ADAPTER != old.ADAPTER and subject.STATE_KEY != old.STATE_KEY and subject.STATE != old.STATE
    assert {subject.PREPARED, subject.RESERVED, subject.CAPTURED}.isdisjoint({old.PREPARED, old.RESERVED, old.CAPTURED})
    assert subject.output_schema() == old.output_schema() and subject.model_value_schema() == old.model_value_schema()
    from generic_agents import business_reasoning_capture_v275 as kernel
    seen = []
    def capture(*args, protocol):
        assert protocol is subject
        seen.append(args)
        return {'shared_kernel_spy': True}
    monkeypatch.setattr(kernel, 'execute', capture)
    marker = object()
    assert subject.execute(marker, None, None, {}, 'original') == {'shared_kernel_spy': True}
    assert seen == [(marker, None, None, {}, 'original')]


def test_excluded_ack_never_becomes_an_admitted_frame_fact():
    # An authentic receipt alone need not satisfy a confirmed/time requirement.
    # This is a pure projection boundary: no reception truth is fabricated.
    report = {'evidence': [], 'observation_evidence': [], 'conflicts': []}
    frozen = {'data': {'evaluation_record': {'evaluation_result': {'result': report},
        'ack_refs': [{'query_admitted': False, 'observation_id': ident(1)}]}, 'fact_origins': []}}
    assert frame._facts(None, None, frozen, []) == ([], [], [])


def test_live_rolling_freshness_uses_current_clock_but_fixed_history_does_not_roll():
    _, record, _, _ = record_scene()
    class Clock:
        def __init__(self, at): self.at = instant(at); self.reads = 0
        def scalar(self, statement):
            assert 'clock_timestamp' in str(statement)
            self.reads += 1; return self.at
    assert sources._freshness(Clock('2026-11-01T12:05:01+00:00'), record) is None
    with pytest.raises(HTTPException): sources._freshness(Clock('2026-11-01T12:20:00+00:00'), record)
    _, historical, _, _ = record_scene(fixed=True)
    clock = Clock('2026-11-02T12:20:00+00:00')
    assert sources._freshness(clock, historical) is None and clock.reads == 0


@pytest.mark.parametrize('source', ['archive', 'either'])
def test_live_archive_expiry_is_not_hidden_by_a_still_recent_observed_time(source):
    _, record, _, _ = record_scene()
    record['goal_snapshot']['spec']['evidence']['source'] = source
    record['evaluation_result']['result']['evidence'] = [{'entity_id': ident(20),
        'observed_at': '2026-11-01T12:04:59+00:00', 'valid_until': '2026-11-01T12:05:00.500000+00:00'}]
    class Clock:
        def scalar(self, _statement): return instant('2026-11-01T12:05:01+00:00')
    with pytest.raises(HTTPException): sources._freshness(Clock(), record)
    record['evaluation_result']['result']['evidence'][0]['valid_until'] = None
    assert sources._freshness(Clock(), record) is None


def test_historical_source_verification_requests_the_original_cut_not_live_expiry(monkeypatch):
    frozen = {'schema': sources.SCHEMA, 'task_id': ident(10), 'release_id': ident(11),
        'package_id': ident(12), 'package_hash': 'a' * 64, 'data': {'packet': {'id': ident(13)}},
        'documents': {'packet': {'id': ident(14)}}, 'evidence_ids': [ident(15), ident(16)]}
    frozen['sources_hash'] = digest(frozen); seen = []
    def original(_db, _p, _task, inputs, *, live):
        assert live is False and inputs['data_packet'] == frozen['data']['packet']
        seen.append(copy.deepcopy(inputs)); return copy.deepcopy(frozen)
    monkeypatch.setattr(sources, '_resolve', original)
    assert sources.checked_frozen(None, None, None, frozen) == frozen and len(seen) == 1


def _prepared_journal_boundary(monkeypatch):
    """Real transport.prepare; only persisted SDK/source/ledger IO is synthetic."""
    frozen_frame, _question, evidence_pins, parts, model_value = projection_fixture()
    evidence_pins.append({'evidence_id': ident(42), 'media_type': 'text/plain', 'model_profile_id': ident(41)})
    inputs = {'question': '核对已保存原资料', 'data_packet': {'id': ident(50), 'evidence_id': ident(40),
        'sha256': 'a' * 64, 'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False},
        'document_packet': {'id': ident(51), 'evidence_id': ident(42), 'sha256': 'b' * 64, 'version': 1,
            'source_set_id': ident(52), 'source_set_hash': 'c' * 64},
        'decision_intent': 'information_acquisition', 'candidate_specs': [{'candidate_ref': 'candidate_2',
            'proposal': '核对原资料', 'claims': [], 'capability_pins': [{'capability_ref': 'cap_sampling',
                'capability_id': ident(21), 'capability_hash': 'a' * 64, 'release_version': '1.0.0',
                'delivery': 'requested_observation', 'ports': [{'field': 'temperature_c',
                    'pointer': '/requested_observation/temperature_c'}]}]}]}
    instruction = 'Compare the original saved facts without changing them.'
    model = model_manifest(); model_id = ident(41)
    cap_data = {**model_manifest(), 'kind': 'tool', 'name': 'evaluated_fact_comparison',
        'execution': {'adapter': subject.ADAPTER, 'model_profile_id': model_id,
            'model_profile_hash': digest(model), 'side_effect': False}, 'dependencies': [model_id],
        'specification': {'schema': subject.CONTRACT, 'instruction': instruction},
        'input_schema': subject.input_schema(), 'output_schema': subject.output_schema()}
    cap = NS(id=ident(4), data=cap_data)
    frozen_frame['sources'] = {'schema': sources.SCHEMA, 'task_id': ident(10), 'evidence_ids': [ident(40), ident(42)]}
    frozen_frame['sources']['sources_hash'] = digest(frozen_frame['sources'])
    key = 'graph:actual:prepared-one'; call_id = ident(70); job_id = ident(12)
    projection = subject._cloud_projection(inputs, frozen_frame, instruction, parts, evidence_pins,
        invocation_id=call_id, key=key)
    messages = subject._messages(projection)
    prepared = subject.transport.prepare(NS(data=model), messages, subject.model_value_schema())
    pin = {'workspace_id': ident(1), 'actor_id': ident(2), 'invocation_id': call_id, 'task_id': ident(10),
        'job_id': job_id, 'activation_id': 'reasoning_activation', 'node_id': 'reasoning', 'scope_hash': 'd' * 64,
        'capability_id': cap.id, 'capability_hash': digest(cap_data), 'model_profile_id': model_id,
        'model_profile_hash': digest(model), 'request_key_hash': digest(key), 'input_hash': digest(inputs),
        'decision_input_hash': decision.input_hash(frozen_frame['decision_input']),
        'sources_hash': frozen_frame['sources']['sources_hash']}
    frozen = {'schema': subject.STATE + '/request', 'inputs': inputs, 'frame': frozen_frame,
        'frame_hash': digest(frozen_frame), 'messages': messages, 'model_value_schema': subject.model_value_schema(),
        'prepared': prepared, 'evidence_pins': evidence_pins, 'cloud_projection': projection,
        'capability_snapshot': cap_data, 'model_snapshot': model, 'pin': pin}
    frozen['request_hash'] = digest(frozen)
    receipt = {'call_id': ident(14), 'provider': model['execution']['provider'], 'model': model['execution']['model'],
        'http_status': 200, 'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}}
    raw = json.dumps({'choices': [{'message': {'content': json.dumps(model_value)}}], 'usage': receipt['usage']}).encode()
    state = {'schema': subject.STATE, 'request': frozen, 'call_id': ident(14), 'capture': {
        'raw_base64': base64.b64encode(raw).decode(), 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'receipt': receipt}}
    call = NS(id=call_id, workspace_id=ident(1), capability_id=cap.id, idempotency_key=key,
        data={'capability_hash': digest(cap_data), 'input_hash': digest(inputs), 'job_id': job_id, subject.STATE_KEY: state})
    job = NS(id=job_id, idempotency_key=key, data={'actor_id': ident(2), 'task_id': ident(10),
        'activation_id': pin['activation_id'], 'invocation_key': key, 'input_hash': digest(inputs), 'inputs': copy.deepcopy(inputs)})
    ledger = NS(capability_id=model_id, data={'actor_id': ident(2), 'request_hash': prepared['request_hash'],
        'dispatch_options_hash': prepared['dispatch_options_hash'], 'gateway_dispatch': {'identity': {
            'suggestion_id': call_id, 'job_id': job_id, 'task_id': ident(10), 'request_hash': frozen['request_hash'],
            'workspace_id': ident(1), 'actor_id': ident(2), 'capability_id': model_id}}})
    events = {subject.PREPARED: subject._anchor(frozen),
        subject.RESERVED: {**subject._anchor(frozen), 'call_id': ident(14)},
        subject.CAPTURED: {**subject._anchor(frozen), 'call_id': ident(14),
            'raw_sha256': state['capture']['raw_sha256'], 'receipt_hash': digest(receipt)}}
    p = NS(user_id=ident(2), workspace_id=ident(1))
    allowed = {str(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == action,
        Audit.object_id == call_id).compile(compile_kwargs={'literal_binds': True})): action for action in events}
    class ReadOnlyIO:
        def scalars(self, statement):
            action = allowed[str(statement.compile(compile_kwargs={'literal_binds': True}))]
            return [NS(actor_id=p.user_id, data=copy.deepcopy(events[action]))]
    monkeypatch.setattr(subject, 'scoped', lambda _db, model, identity, _p: job
        if model is Job and identity == job_id else pytest.fail('unexpected scoped IO'))
    monkeypatch.setattr(subject, '_cloud_call', lambda _db, _p, identity, **_kw: ledger
        if identity == ident(14) else pytest.fail('unexpected cloud ledger IO'))
    return NS(db=ReadOnlyIO(), p=p, call=call, cap=cap, inputs=inputs, key=key, state=state)


def test_original_capture_cold_check_rederives_real_transport_schema_prefix_options_and_bounds(monkeypatch):
    s = _prepared_journal_boundary(monkeypatch); before = copy.deepcopy(s.call.data)
    prepared = s.state['request']['prepared']
    assert prepared['messages'] != s.state['request']['messages']
    assert prepared['request_hash'] == digest(prepared['messages'])
    assert subject._checked_state(s.db, s.p, s.cap, s.inputs, s.key, s.call) == s.state
    assert s.call.data == before
    # Rehashing just the private request cannot make a different dispatch bound
    # equal to the real pure transport, even with a synthetic ledger boundary.
    prepared['token_bound'] += 1
    s.state['request']['request_hash'] = digest({key: value for key, value in s.state['request'].items() if key != 'request_hash'})
    with pytest.raises(HTTPException): subject._checked_state(s.db, s.p, s.cap, s.inputs, s.key, s.call)


@pytest.fixture
def source_boundary(monkeypatch):
    _, record, _, _ = record_scene(); task = NS(id=record['scope']['task_id'])
    row = NS(id=record['evaluation_id'], invocation_id=record['evaluation_id'],
        job_id=record['request_id'], evidence_id=record['evidence_ref']['evidence_id'],
        record_hash=record['record_hash'], data=record)
    call = NS(id=row.id, capability_id=ident(51)); job = NS(id=row.job_id, status='succeeded', data={'inputs': {}})
    cap = NS(id=call.capability_id); release = NS(id=record['scope']['release_id'])
    output = {'schema': sources.packet.RESULT, 'route': 'data_ready', 'query_status': 'sufficient',
        'reason_code': 'evaluation_reports_sufficient', 'evaluation_ref': {'id': row.id, 'record_hash': row.record_hash},
        'evidence_ids': [row.evidence_id], 'packet': {'id': row.id, 'evidence_id': row.evidence_id,
            'sha256': record['evidence_ref']['sha256'], 'version': 1, 'simulation_only': True,
            'proposal_only': True, 'cloud_authorized': False}, **sources.packet.FLAGS}
    expected = {(GoalEvaluationV288, row.id): row, (Invocation, call.id): call, (Job, job.id): job,
        (Capability, cap.id): cap, (Capability, ident(52)): NS(content_hash='a' * 64)}
    task.package_id = ident(52); called = []
    monkeypatch.setattr(sources, 'fresh', lambda _db, _p, model, identity: expected[(model, identity)])
    monkeypatch.setattr(sources, '_producer', lambda *_args, **_kw: (task, release, {}, copy.deepcopy(output)))
    def saved(_db, _p, actual, original_job, original_call, actual_cap, inputs):
        assert (actual, original_job, original_call, actual_cap, inputs) == (task, job, call, cap, {})
        called.append(call.id); return copy.deepcopy(output)
    monkeypatch.setattr(sources.packet, 'saved_result', saved)
    monkeypatch.setattr(sources.original, '_data', lambda *_a, **_k: pytest.fail('V288 cannot read V199 inventory'))
    monkeypatch.setattr(sources.store, '_document', lambda *_a: b'original public text')
    monkeypatch.setattr(sources, '_origins', lambda *_a: [])
    from generic_agents import evidence
    monkeypatch.setattr(evidence, 'evidence_bytes', lambda *_a: (NS(id=record['evidence_ref']['evidence_id'], sha256=record['evidence_ref']['sha256'], media_type='text/plain'), b'original public text'))
    return NS(task=task, row=row, output=output, called=called)


def test_data_packet_uses_original_evaluation_producer_and_never_old_v199(source_boundary):
    s = source_boundary
    release, _hash, data = sources._data(None, None, s.task, s.output['packet'], live=False)
    assert s.called == [s.row.id] and data['evaluation_record'] == s.row.data
    assert data['packet_id'] == s.row.id and 'provenance' not in data and 'context_result' not in data


def test_a_rehashed_reference_cannot_select_another_evidence(source_boundary):
    s = source_boundary; reference = copy.deepcopy(s.output['packet']); reference['evidence_id'] = ident(99)
    with pytest.raises(HTTPException): sources._data(None, None, s.task, reference, live=False)


def test_cloud_qualification_requires_the_exact_completed_goal_evaluation(monkeypatch):
    from generic_agents import goal_evaluation_provider_v288 as provider
    value = {'data': {'evaluation_record': {'evaluation_id': ident(80), 'record_hash': 'a' * 64}}}
    db, p, task = object(), object(), object(); calls = []
    monkeypatch.setattr(sources, 'resolve', lambda *args: copy.deepcopy(value))
    def current(actual_db, actual_p, actual_task, *, expected_ref):
        assert (actual_db, actual_p, actual_task) == (db, p, task)
        calls.append(expected_ref)
    monkeypatch.setattr(provider, 'require_current', current)
    assert subject.qualify_evidence(db, p, task, {}) == value
    assert calls == [{'id': ident(80), 'record_hash': 'a' * 64}]
    def rejected(*args, **kwargs): raise HTTPException(409, 'original Goal check is pending or stale')
    monkeypatch.setattr(provider, 'require_current', rejected)
    with pytest.raises(HTTPException): subject.qualify_evidence(db, p, task, {})
