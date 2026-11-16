"""Public synthetic IO boundaries; real schemas, pins and replay implementation.

These tests do not represent PG, source-file consent, IAM, a paid model or
professional-quality acceptance. Root owns the independent actual journey.
"""
import base64
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import business_reasoning_v197 as subject
from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_document_packet_v207 as document_packet
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Capability, Evidence, Invocation, Audit, Job, CloudCall


def ident(n): return '19700000-0000-4000-8000-' + format(n, '012x')


def local_context():
    task, query, gap, entity = [ident(i) for i in range(10, 14)]
    delegation = {'task_id': task}
    request = {'gap_id': gap, 'entity_id': entity, 'query_id': query, 'fields': ['field_one'],
        'query_request_hash': 'a' * 64, 'goal_delegation': delegation,
        'opaque_local': '只在本机保留的上下文标记'}
    result = {'schema': reader.CONTEXT, 'scope': {'workspace_id': ident(1), 'task_id': task,
        'instance_id': ident(14), 'release_id': ident(15), 'package_id': ident(16), 'business_id': 'public_business',
        'graph_hash': 'a' * 64, 'source_binding_hash': 'b' * 64, 'goal_snapshot_hash': 'c' * 64,
        'native_goal_context_hash': 'd' * 64}, 'query': {'query_id': query, 'request_hash': 'a' * 64,
            'goal_delegation_hash': digest(delegation), 'version_at_read': 1, 'result_hash_at_read': 'e' * 64},
        'gap': {'gap_id': gap, 'entity_id': entity, 'version_at_read': 1, 'fields': ['field_one'],
            'observation_request': request, 'observation_request_hash': digest(request)},
        'policy': {'policy_id': ident(17), 'approved_hash': 'f' * 64, 'approved_by': ident(2),
            'version_at_read': 1, 'source_pins': [{'source_id': ident(18), 'source_hash': 'a' * 64}]},
        'producer': {'capability_id': ident(19), 'invocation_id': ident(20), 'job_id': ident(21),
            'actor_id': ident(2), 'capability_hash': 'b' * 64, 'activation_id': 'producer',
            'node_id': 'read_context', 'cancel_epoch': 0, 'input_hash': 'c' * 64,
            'request_key_hash': 'd' * 64}, 'permissions_verified': False, 'can_dispatch': False}
    return {**result, 'context_hash': digest(result)}


def manifest():
    return {'contract_version': '1.0', 'kind': 'foundation_model', 'namespace': 'public_test',
        'name': 'public_qwen', 'version': '1.0.0', 'owner': 'public fixture',
        'description': 'Public synthetic profile, no real credential',
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
        'allowed_agent_roles': ['business'], 'execution': {'provider': 'qwen', 'model': 'qwen-plus',
            'secret_ref': 'DASHSCOPE_API_KEY', 'max_tokens': 1024, 'supports_images': True},
        'data_policy': {'cloud_allowed': True}, 'budget': {'max_attempts': 1, 'max_concurrency': 1},
        'dependencies': [], 'specification': {}}


def scene():
    model_data = manifest()
    model = NS(id=ident(3), workspace_id=ident(1), kind='foundation_model', status='published',
        data=model_data, content_hash=digest(model_data), release_version='1.0.0')
    model.validated_hash = model.content_hash
    data = {**manifest(), 'kind': 'tool', 'name': 'public_business_reasoner',
        'input_schema': subject.input_schema(), 'output_schema': subject.output_schema(),
        'execution': {'adapter': subject.ADAPTER, 'model_profile_id': model.id,
            'model_profile_hash': model.content_hash, 'side_effect': False},
        'dependencies': [model.id], 'specification': {'schema': subject.CONTRACT,
            'instruction': '比较本次证据与候选提案，保留缺项和冲突，不替人批准'}}
    cap = NS(id=ident(4), workspace_id=ident(1), kind='tool', status='published', data=data,
        content_hash=digest(data), release_version='1.0.0'); cap.validated_hash = cap.content_hash
    inputs = {'question': '根据本次明确提供的证据比较两个候选提案', 'evidence_ids': [ident(5), ident(6)],
        'candidates': [{'candidate_ref': 'proposal_a', 'summary': '候选甲，仅为待核提案', 'context': local_context()},
            {'candidate_ref': 'proposal_b', 'summary': '候选乙，仅为待核提案', 'context': None}]}
    p = NS(user_id=ident(2), workspace_id=ident(1), role='operator')
    task = NS(id=ident(10), workspace_id=p.workspace_id, data={})
    key = 'public-business-reasoning:' + ident(7)
    job = NS(id=ident(8), workspace_id=p.workspace_id, data={'actor_id': p.user_id, 'task_id': task.id,
        'activation_id': 'analysis_activation', 'invocation_key': key, 'input_hash': digest(inputs),
        'inputs': copy.deepcopy(inputs)})
    node = {'id': 'analysis_node', 'execution': {'agent_role': 'business'}}
    scope = {'activation_id': 'analysis_activation', 'input_hash': digest(inputs)}
    call = NS(id=ident(9), workspace_id=p.workspace_id, capability_id=cap.id,
        idempotency_key=key, status='running', data={'input_hash': digest(inputs), 'role': 'business',
            'capability_hash': cap.content_hash, 'job_id': job.id})
    output = {'summary': '候选比较仍以实际证据和人工核对为准', 'candidate_assessments': [
        {'candidate_ref': item['candidate_ref'], 'assessment': 'supported', 'reason': '本次资料能够支持这一提案的比较依据',
            'evidence_refs': [inputs['evidence_ids'][i]], 'missing_information': []}
        for i, item in enumerate(inputs['candidates'])],
        'recommendation': {'candidate_ref': 'proposal_a', 'reason': '供人工核对的建议，不构成批准',
            'evidence_refs': [inputs['evidence_ids'][0]]}}
    return NS(model=model, cap=cap, inputs=inputs, p=p, task=task, job=job, node=node, scope=scope,
        key=key, call=call, output=output)


class FakeDB:
    def __init__(self, s):
        self.s = s; self.events = []; self.commits = 0; self.read_only = False
        self.fresh_overrides = {}; self.after_commit = None
        self.invocation_queries = []; self.invocation_lock_held = False
    def scalar(self, query):
        entity = query.column_descriptions[0]['entity']
        description = query.column_descriptions[0]
        if entity in {document_packet.Packet, document_packet.Command, Audit, Invocation} and description['name'] == 'id':
            # This legacy fixture has no document-packet authority. Keep the
            # real inverse-inventory reads, accepting only their exact scoped
            # queries; an Invocation entity read still follows the old branch.
            values = list(query.compile().params.values())
            ids = [identity for identity in self.s.inputs['evidence_ids'] if identity in values]
            assert len(ids) == 1 and query._for_update_arg is None
            identity = ids[0]
            if entity is document_packet.Packet:
                expected = select(entity.id).where(entity.workspace_id == self.s.p.workspace_id, entity.evidence_id == identity)
            elif entity is document_packet.Command:
                expected = select(entity.id).where(entity.workspace_id == self.s.p.workspace_id,
                    entity.data['output']['packet']['evidence_id'].as_string() == identity)
            elif entity is Audit:
                expected = select(Audit.id).where(Audit.workspace_id == self.s.p.workspace_id,
                    Audit.action == document_packet.PRODUCED, Audit.data['evidence_id'].as_string() == identity)
            else:
                expected = select(Invocation.id).where(Invocation.workspace_id == self.s.p.workspace_id,
                    Invocation.data['capability_snapshot']['execution']['adapter'].as_string() == document_packet.ADAPTER,
                    Invocation.data['output']['packet']['evidence_id'].as_string() == identity)
            assert query.compare(expected, compare_values=True), 'unexpected packet authority query must not be swallowed'
            return None
        if entity in {Capability, Evidence}:
            assert query.get_execution_options().get('populate_existing') is True
            params = list(query.compile().params.values())
            assert self.s.p.workspace_id in params
            rows = [self.s.cap, self.s.model] if entity is Capability else list(self.evidence.values())
            row = next(row for row in rows if row.id in params)
            for field, value in self.fresh_overrides.get(row.id, {}).items():
                setattr(row, field, copy.deepcopy(value))
            return row
        assert entity is Invocation and description['expr'] is Invocation
        locked = query._for_update_arg is not None
        self.invocation_queries.append(locked)
        self.invocation_lock_held = self.invocation_lock_held or locked
        return self.s.call
    def scalars(self, query):
        params = query.compile().params
        return [event for event in self.events if event.workspace_id == params.get('workspace_id_1')
            and event.action == params.get('action_1') and event.object_id == params.get('object_id_1')]
    def add(self, event):
        assert not self.read_only; assert isinstance(event, Audit); self.events.append(event)
    def commit(self):
        assert not self.read_only; self.commits += 1
        self.invocation_lock_held = False
        if self.after_commit: self.after_commit(self.commits)
    def __enter__(self): return self
    def __exit__(self, *_args): self.invocation_lock_held = False; return False


def install(monkeypatch, s):
    from generic_agents import cloud_budget, evidence, gateway, workflow_graph_scope, workflow_graph, db as actual_db
    db = FakeDB(s); calls = []; posts = []; current = []; scope_binding = {'external_node_ids': [s.node['id']]}
    evidence_rows = {identity: NS(id=identity, sha256=hashlib.sha256(identity.encode()).hexdigest(),
        workspace_id=s.p.workspace_id, version=2, size_bytes=100, media_type='text/plain',
        data={'cloud_model_ids': [s.model.id]}) for identity in s.inputs['evidence_ids']}
    db.evidence = evidence_rows
    row_map = {(Job, s.job.id): s.job, **{(type(row), key): row for key, row in evidence_rows.items()}}
    def published(_db, p, identity, kind=None):
        row = s.model if identity == s.model.id else s.cap
        assert row.id == identity and p.workspace_id == row.workspace_id
        if kind: assert row.kind == kind
        if row.status != 'published' or row.content_hash != digest(row.data) or row.validated_hash != row.content_hash:
            raise HTTPException(409, 'synthetic current published profile unavailable')
        return row
    def scoped(_db, cls, identity, p, lock=False):
        assert p.workspace_id == s.p.workspace_id
        if cls is Job: assert identity == s.job.id; return s.job
        if cls is CloudCall: assert identity == s.ledger.id; return s.ledger
        assert identity in evidence_rows; return evidence_rows[identity]
    def graph_context(_db, p, key, cap):
        assert key == s.key and cap.id == s.cap.id and p.user_id == s.p.user_id
        current.append(True); return s.task, s.job, s.node, s.scope
    checked_contexts = []
    def checked_context(_db, p, value, *, task, dispatch):
        assert task.id == s.task.id and dispatch is False; reader._check_context(value)
        checked_contexts.append(copy.deepcopy(value)); return copy.deepcopy(value)
    def cloud_parts(_db, p, ids, model):
        assert len(ids) == 1 and ids[0] in evidence_rows and model.id == s.model.id
        if model.id not in evidence_rows[ids[0]].data['cloud_model_ids']:
            raise HTTPException(403, 'synthetic current per-model consent revoked')
        return [{'type': 'text', 'text': '已逐项授权的公开证据内容：' + ids[0]}]
    def packet_evidence(_db, cls, identity, p, lock=False):
        assert _db is db and cls is Evidence and lock is False and p.workspace_id == s.p.workspace_id
        assert identity in evidence_rows
        return evidence_rows[identity]
    monkeypatch.setattr(subject, 'published', published); monkeypatch.setattr(subject, 'scoped', scoped)
    monkeypatch.setattr(workflow_graph_scope, 'graph_context', graph_context)
    monkeypatch.setattr(workflow_graph_scope, '_frozen', lambda *_args: (s.task, NS(), {}, {}, {}, {}, s.node))
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *_args: scope_binding)
    monkeypatch.setattr(reader, 'checked_context', checked_context)
    monkeypatch.setattr(evidence, 'cloud_parts', cloud_parts)
    monkeypatch.setattr(document_packet, 'scoped', packet_evidence)
    monkeypatch.setattr(subject, 'Session', lambda _engine: db); monkeypatch.setattr(actual_db, 'engine', lambda: None)
    settlements = []
    def settle(_db, call_id, receipt, expected, status):
        assert not db.read_only and s.ledger.status == 'reserved' and call_id == s.ledger.id
        state = s.call.data[subject.STATE_KEY]; frozen = state['request']
        assert expected == {'workspace_id': s.p.workspace_id, 'capability_id': s.model.id,
            'actor_id': s.p.user_id, 'request_hash': frozen['prepared']['request_hash'],
            'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'],
            'provider': 'qwen', 'model': 'qwen-plus'}
        settlements.append((call_id, copy.deepcopy(receipt), copy.deepcopy(expected), status))
        s.ledger.status = status; s.ledger.data['receipt'] = copy.deepcopy(receipt)
        s.ledger.charged_tokens = receipt.get('usage', {}).get('total_tokens', s.ledger.reserved_tokens)
        return s.ledger
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', settle)
    def cloud(model, messages, schema, principal, **options):
        calls.append(copy.deepcopy(options['dispatch_identity']))
        prepared = transport.prepare(model, messages, schema)
        s.ledger = NS(id=ident(90), workspace_id=s.p.workspace_id, capability_id=model.id,
            status='reserved', reserved_tokens=10000, charged_tokens=10000,
            data={'actor_id': s.p.user_id, 'request_hash': prepared['request_hash'],
                'dispatch_options_hash': prepared['dispatch_options_hash'], 'gateway_dispatch': {'identity': {
                    **options['dispatch_identity'], 'workspace_id': s.p.workspace_id,
                    'actor_id': s.p.user_id, 'capability_id': model.id}}})
        try: options['on_reserved'](s.ledger.id)
        except Exception:
            s.ledger.status = 'failed'
            raise
        posts.append(prepared['request_hash'])
        receipt = {'provider': 'qwen', 'model': 'qwen-plus', 'call_id': s.ledger.id,
            'request_id': 'public-provider-receipt', 'usage': {}, 'usage_unavailable': True,
            'http_status': 200, 'stage': transport.STAGE}
        envelope = {'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(s.output, ensure_ascii=False)}}]}
        if hasattr(s, 'reply_usage'): envelope['usage'] = copy.deepcopy(s.reply_usage)
        raw = json.dumps(envelope, ensure_ascii=False)
        options['on_response'](raw, receipt)
        if not getattr(s, 'keep_reserved_after_capture', False):
            s.ledger.status = 'failed' if getattr(s, 'lose_after_capture', False) else 'succeeded'
            s.ledger.data['receipt'] = copy.deepcopy(receipt)
        if getattr(s, 'lose_after_capture', False): raise ModelBlocked('synthetic crash after durable first answer')
        return copy.deepcopy(s.output), receipt
    monkeypatch.setattr(gateway, 'cloud_json', cloud)
    return NS(db=db, calls=calls, posts=posts, current=current, contexts=checked_contexts, binding=scope_binding,
        evidence=evidence_rows, settlements=settlements)


def test_exact_manifest_profile_pins_and_structured_ports(monkeypatch):
    s = scene(); env = install(monkeypatch, s)
    assert subject.validate_manifest(env.db, s.p, s.cap.data).id == s.model.id
    from generic_agents.full_semantic_graph_v1 import _field
    assert _field(subject.output_schema(), 'review_state') == ('string', False)
    assert _field(subject.output_schema(), 'recommended_context') == ('object', True)
    for mutate in [lambda d: d['execution'].update(side_effect=0),
        lambda d: d['execution'].update(endpoint='https://not-a-profile.invalid'),
        lambda d: d['budget'].update(max_attempts=2),
        lambda d: d.update(allowed_agent_roles=['perception']),
        lambda d: d['execution'].update(model_profile_hash='0' * 64),
        lambda d: d.update(dependencies=[]), lambda d: d['data_policy'].update(cloud_allowed=False)]:
        value = copy.deepcopy(s.cap.data); mutate(value)
        with pytest.raises((HTTPException, ValueError)): subject.validate_manifest(env.db, s.p, value)


def test_actual_prepare_calls_context_guard_and_never_sends_local_context_or_unselected_material(monkeypatch):
    s = scene(); env = install(monkeypatch, s); model, frozen = subject.prepare_request(env.db, s.p, s.cap, s.inputs, s.key)
    sent = json.dumps(frozen['prepared']['messages'], ensure_ascii=False)
    assert len(env.contexts) == 1 and env.contexts[0] == s.inputs['candidates'][0]['context']
    assert '只在本机保留' not in sent and 'opaque_local' not in sent and 'context_hash' not in sent
    assert all(identity in sent for identity in s.inputs['evidence_ids'])
    assert '候选甲' in sent and model.id == s.model.id
    assert len(frozen['evidence_pins']) == 2 and frozen['inputs'] == s.inputs
    env.binding['external_node_ids'] = []
    with pytest.raises(HTTPException): subject.prepare_request(env.db, s.p, s.cap, s.inputs, s.key)
    assert env.calls == []


def test_resolved_original_context_can_be_analysed_read_only_without_device_permission(monkeypatch):
    from generic_agents import perception, workflow_service_context_v196 as service
    s = scene(); env = install(monkeypatch, s); modes = []
    def resolved_context(db, p, supplied, *, task, dispatch):
        assert db is env.db and p is s.p and task is s.task
        reader._check_context(supplied)
        assert supplied == s.inputs['candidates'][0]['context']
        modes.append(dispatch)
        # The real V196 resolver separately tests resolved current inventory:
        # it is readable, but cannot license another acquisition dispatch.
        if dispatch: raise HTTPException(409, '原缺口已补齐，不能再派发设备')
        return copy.deepcopy(supplied)
    monkeypatch.setattr(reader, 'checked_context', resolved_context)
    def no_device(*args, **kwargs): pytest.fail('Read-only reasoning cannot dispatch a device/service')
    monkeypatch.setattr(perception, 'propose_acquisitions', no_device)
    monkeypatch.setattr(service, 'execute', no_device)
    output = subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    assert modes == [False, False] and len(env.posts) == 1
    assert output['recommended_context'] == s.inputs['candidates'][0]['context']
    assert output['permissions_verified'] is False and output['can_dispatch'] is False
    with pytest.raises(HTTPException):
        resolved_context(env.db, s.p, output['recommended_context'], task=s.task, dispatch=True)


@pytest.mark.parametrize('declaration', [{}, {'schema': 'report-business-flow-output-v193'}, False, 'report-only'])
def test_special_purpose_report_profile_cannot_be_registered_or_dispatched_for_reasoning(monkeypatch, declaration):
    s = scene(); env = install(monkeypatch, s)
    s.model.data['execution']['report_evidence_output'] = copy.deepcopy(declaration)
    s.model.content_hash = digest(s.model.data); s.model.validated_hash = s.model.content_hash
    s.cap.data['execution']['model_profile_hash'] = s.model.content_hash
    s.cap.content_hash = digest(s.cap.data); s.cap.validated_hash = s.cap.content_hash
    with pytest.raises(HTTPException) as registration:
        subject.validate_manifest(env.db, s.p, s.cap.data)
    assert registration.value.status_code == 422
    with pytest.raises(HTTPException): subject.prepare_request(env.db, s.p, s.cap, s.inputs, s.key)
    assert env.calls == [] and env.current == []


@pytest.mark.parametrize('revocation', ['tool', 'model', 'model_cloud', 'model_purpose', 'evidence_consent'])
def test_reserved_callback_reloads_retained_orm_rows_after_prepared_commit_before_any_post(monkeypatch, revocation):
    s = scene(); env = install(monkeypatch, s); old_model_hash = s.model.content_hash
    def separate_transaction(commits):
        if commits != 1: return
        # Simulate another committed database view, while strong ORM objects
        # held by execute keep the old values until populate_existing SELECT.
        assert s.call.data[subject.STATE_KEY]['capture'] is None
        assert s.model.content_hash == old_model_hash and s.model.status == 'published'
        if revocation == 'tool': env.db.fresh_overrides[s.cap.id] = {'status': 'paused'}
        elif revocation == 'model': env.db.fresh_overrides[s.model.id] = {'status': 'paused'}
        elif revocation in {'model_cloud', 'model_purpose'}:
            value = copy.deepcopy(s.model.data)
            if revocation == 'model_cloud': value['data_policy']['cloud_allowed'] = False
            else: value['execution']['report_evidence_output'] = {'schema': 'report-business-flow-output-v193'}
            env.db.fresh_overrides[s.model.id] = {'data': value, 'content_hash': digest(value),
                'validated_hash': digest(value)}
        else:
            identity = s.inputs['evidence_ids'][0]
            assert env.evidence[identity].data['cloud_model_ids'] == [s.model.id]
            env.db.fresh_overrides[identity] = {'data': {'cloud_model_ids': []}, 'version': 3}
    env.db.after_commit = separate_transaction
    with pytest.raises(HTTPException): subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    assert len(env.calls) == 1 and env.posts == [] and s.ledger.status == 'failed'
    assert [event.action for event in env.db.events] == [subject.PREPARED]
    assert s.call.data[subject.STATE_KEY]['capture'] is None
    assert s.call.data[subject.STATE_KEY]['call_id'] is None


@pytest.mark.parametrize('fault', ['candidate_omitted', 'candidate_reordered', 'candidate_invented', 'duplicate',
    'unsupported_recommendation', 'unknown_evidence', 'supported_no_evidence', 'insufficient_without_gap', 'extra_command'])
def test_result_rejects_unpinned_or_incomplete_analysis(monkeypatch, fault):
    s = scene(); env = install(monkeypatch, s); _, frozen = subject.prepare_request(env.db, s.p, s.cap, s.inputs, s.key)
    output = copy.deepcopy(s.output)
    if fault == 'candidate_omitted': output['candidate_assessments'].pop()
    elif fault == 'candidate_reordered': output['candidate_assessments'].reverse()
    elif fault == 'candidate_invented': output['candidate_assessments'][0]['candidate_ref'] = 'invented'
    elif fault == 'duplicate': output['candidate_assessments'][1] = copy.deepcopy(output['candidate_assessments'][0])
    elif fault == 'unsupported_recommendation': output['candidate_assessments'][0]['assessment'] = 'conflicting'
    elif fault == 'unknown_evidence': output['recommendation']['evidence_refs'] = [ident(999)]
    elif fault == 'supported_no_evidence': output['candidate_assessments'][0]['evidence_refs'] = []
    elif fault == 'insufficient_without_gap': output['candidate_assessments'][1]['assessment'] = 'insufficient'
    else: output['dispatch_device'] = True
    with pytest.raises((HTTPException, ValueError)): subject.check_result(frozen, output)


def test_projection_is_determined_by_gaps_and_conflicts_preserving_unchanged_local_context(monkeypatch):
    s = scene(); env = install(monkeypatch, s); _, frozen = subject.prepare_request(env.db, s.p, s.cap, s.inputs, s.key)
    original = copy.deepcopy(s.output); result = subject.check_result(frozen, original)
    assert result['review_state'] == 'ready_for_human_review' and result['analysis'] == original
    assert result['recommended_context'] == s.inputs['candidates'][0]['context']
    assert result['permissions_verified'] is False and result['can_dispatch'] is False
    output = copy.deepcopy(s.output); output['candidate_assessments'][1].update(assessment='insufficient', missing_information=['缺少观测依据'])
    assert subject.check_result(frozen, output)['review_state'] == 'needs_information'
    output['candidate_assessments'][1].update(assessment='conflicting', missing_information=[])
    assert subject.check_result(frozen, output)['review_state'] == 'conflicting_evidence'
    output['recommendation']['candidate_ref'] = None
    assert subject.check_result(frozen, output)['recommended_context'] is None


def test_first_capture_is_durable_and_terminal_failed_recovery_preserves_original_fee_and_status(monkeypatch):
    s = scene(); env = install(monkeypatch, s); s.lose_after_capture = True
    with pytest.raises(ModelBlocked): subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    state = copy.deepcopy(s.call.data[subject.STATE_KEY]); assert state['capture'] is not None
    assert len(env.calls) == 1 and len(env.current) == 2
    assert [event.action for event in env.db.events] == [subject.PREPARED, subject.RESERVED, subject.CAPTURED]
    assert hashlib.sha256(base64.b64decode(state['capture']['raw_base64'])).hexdigest() == state['capture']['raw_sha256']
    env.db.read_only = True; before = copy.deepcopy(s.call.data); old_ledger = copy.deepcopy(vars(s.ledger))
    output = subject.recover(env.db, s.p, s.cap, s.inputs, s.key)
    assert output['review_state'] == 'ready_for_human_review' and len(env.calls) == 1
    assert s.call.data == before and state == s.call.data[subject.STATE_KEY]
    assert vars(s.ledger) == old_ledger and s.ledger.status == 'failed' and env.settlements == []


@pytest.mark.parametrize('valid', [True, False])
def test_crash_after_capture_only_settles_original_reserved_call_without_new_transport(monkeypatch, valid):
    s = scene(); env = install(monkeypatch, s)
    s.lose_after_capture = True; s.keep_reserved_after_capture = True
    s.reply_usage = {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
    if not valid: s.output['candidate_assessments'][0]['evidence_refs'] = [ident(999)]
    with pytest.raises(ModelBlocked): subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    original = copy.deepcopy(s.call.data); assert s.ledger.status == 'reserved'
    if valid:
        output = subject.recover(env.db, s.p, s.cap, s.inputs, s.key)
        assert output['permissions_verified'] is False and output['can_dispatch'] is False
    else:
        with pytest.raises((ModelBlocked, HTTPException, ValueError)):
            subject.recover(env.db, s.p, s.cap, s.inputs, s.key)
    assert len(env.settlements) == 1 and env.settlements[0][0] == s.ledger.id
    assert env.settlements[0][3] == ('succeeded' if valid else 'failed')
    assert s.ledger.charged_tokens == 30 and s.call.data == original and len(env.calls) == 1
    old_ledger = copy.deepcopy(vars(s.ledger)); env.db.read_only = True
    if valid: subject.recover(env.db, s.p, s.cap, s.inputs, s.key)
    else:
        with pytest.raises((ModelBlocked, HTTPException, ValueError)):
            subject.recover(env.db, s.p, s.cap, s.inputs, s.key)
    assert vars(s.ledger) == old_ledger and len(env.settlements) == 1 and len(env.calls) == 1


def test_reserved_capture_missing_usage_keeps_original_conservative_charge(monkeypatch):
    s = scene(); env = install(monkeypatch, s)
    s.lose_after_capture = True; s.keep_reserved_after_capture = True
    with pytest.raises(ModelBlocked): subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    subject.recover(env.db, s.p, s.cap, s.inputs, s.key)
    assert env.settlements[0][1]['usage'] == {} and env.settlements[0][1]['usage_unavailable'] is True
    assert s.ledger.charged_tokens == s.ledger.reserved_tokens == 10000 and len(env.calls) == 1


def test_execute_existing_answer_enters_historical_settlement_without_own_invocation_lock(monkeypatch):
    s = scene(); env = install(monkeypatch, s)
    s.lose_after_capture = True
    with pytest.raises(ModelBlocked): subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    env.db.invocation_queries.clear(); assert env.db.invocation_lock_held is False
    original = copy.deepcopy(s.call.data); settle = subject._settle_capture; checked = []
    def independent(*args, **kwargs):
        assert env.db.invocation_lock_held is False
        checked.append(True); return settle(*args, **kwargs)
    monkeypatch.setattr(subject, '_settle_capture', independent)
    result = subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    assert result['schema'] == subject.RESULT and checked == [True]
    assert env.db.invocation_queries == [False, True]
    assert s.call.data == original and len(env.posts) == 1 and env.settlements == []


def test_execute_losing_initial_state_race_refuses_reentry_without_recover_or_second_provider(monkeypatch):
    s = scene(); env = install(monkeypatch, s); original_call = subject._call
    concurrent_state = {'preserved_original': 'another owner prepared this request'}
    def race(db, p, key, *, lock=False):
        if lock: s.call.data = {**s.call.data, subject.STATE_KEY: copy.deepcopy(concurrent_state)}
        return original_call(db, p, key, lock=lock)
    monkeypatch.setattr(subject, '_call', race)
    monkeypatch.setattr(subject, 'recover', lambda *_args, **_kwargs: pytest.fail('cannot recover while holding original row lock'))
    with pytest.raises(HTTPException) as error: subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    assert error.value.status_code == 409 and s.call.data[subject.STATE_KEY] == concurrent_state
    assert env.calls == [] and env.posts == [] and env.db.commits == 0
    assert env.db.invocation_queries == [False, True]


@pytest.mark.parametrize('fault', ['actor', 'input', 'raw', 'audit', 'model_pin', 'ledger_pin'])
def test_capture_or_identity_tampering_never_replays_provider(monkeypatch, fault):
    s = scene(); env = install(monkeypatch, s); subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    inputs = copy.deepcopy(s.inputs); principal = s.p
    if fault == 'actor': principal = NS(**{**vars(s.p), 'user_id': ident(999)})
    elif fault == 'input': inputs['question'] += ' altered'
    elif fault == 'raw': s.call.data[subject.STATE_KEY]['capture']['raw_base64'] = base64.b64encode(b'{}').decode()
    elif fault == 'audit': env.db.events.pop()
    elif fault == 'model_pin': s.call.data[subject.STATE_KEY]['request']['pin']['model_profile_hash'] = '0' * 64
    else: s.ledger.data['request_hash'] = '0' * 64
    with pytest.raises((HTTPException, ValueError)): subject.recover(env.db, principal, s.cap, inputs, s.key)
    assert len(env.calls) == 1


def test_existing_reservation_without_capture_is_not_a_new_charge_permission(monkeypatch):
    s = scene(); env = install(monkeypatch, s); subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    s.call.data[subject.STATE_KEY]['capture'] = None
    env.db.events = [event for event in env.db.events if event.action != subject.CAPTURED]
    with pytest.raises(ModelBlocked): subject.execute(env.db, s.p, s.cap, s.inputs, s.key)
    assert len(env.calls) == 1
