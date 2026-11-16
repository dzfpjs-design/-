"""Synthetic protocol/IO spies, not PG, paid HTTP, IAM, source truth or E2E.

The real graph/source builders have their own suites. These fixtures expose
only explicit server boundaries and do not activate the shared kernel.
"""
import base64
import copy
import hashlib
import json
import sys
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import business_decision_contracts_v274 as decision
from generic_agents import business_decision_runtime_v276 as subject
from generic_agents import business_reasoning_v197 as legacy
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Audit, CloudCall, Invocation, Job, Task


def ident(n):
    return '27600000-0000-4000-8000-' + format(n, '012x')


def candidate_schema():
    return {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['candidate_ref'],
        'properties': {'candidate_ref': {'type': 'string', 'pattern': r'^candidate_[0-9]+$'}}}}


@pytest.fixture(autouse=True)
def explicit_frame_boundary(monkeypatch):
    # A marked builder spy, not a source-authenticity assertion. The production
    # module imports this server-owned interface lazily and accepts no module ID.
    module = NS(candidate_specs_schema=candidate_schema)
    monkeypatch.setitem(sys.modules, 'generic_agents.business_decision_frame_v276', module)
    return module


def declared_case():
    source = {'source_ref': 'source_sampling', 'kind': 'capability', 'source_id': ident(21),
        'content_hash': 'a' * 64, 'text': 'Can request simulated temperature.', 'readability': 'readable'}
    requirement = {'requirement_ref': 'need_temperature', 'entity_id': ident(20), 'field': 'temperature_c',
        'value_type': 'number', 'unit': 'Cel', 'semantics': 'instantaneous',
        'time_scope': {'start': '2026-11-01T11:59:00+00:00', 'end': '2026-11-01T12:01:00+00:00'},
        'required_kind': 'observation', 'time_mode': 'bounded'}
    inputs = {'schema': decision.INPUT, 'decision_intent': 'information_acquisition',
        'scope': {'task_id': ident(10), 'activation_id': 'reasoning_activation',
            'goal_snapshot_hash': 'b' * 64, 'entity_ids': [ident(20)]}, 'requirements': [requirement],
        'sources': [source], 'capabilities': [{'capability_ref': 'cap_sampling', 'capability_id': ident(21),
            'capability_hash': 'a' * 64, 'release_version': '1.0.0', 'input_schema_hash': 'c' * 64,
            'output_schema_hash': 'd' * 64, 'source_ref': source['source_ref'],
            'action_kind': 'information_acquisition', 'output_ports': [], 'information_targets': [{
                'port_ref': 'target_temperature', 'pointer': '/request/temperature_c',
                'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'}]}],
        'candidates': [{'candidate_ref': 'candidate_2', 'proposal': 'Request a simulated observation for review.',
            'capability_refs': ['cap_sampling'], 'requirement_mappings': [{
                'requirement_ref': 'need_temperature', 'capability_ref': 'cap_sampling',
                'delivery': 'requested_observation', 'port_ref': 'target_temperature'}], 'claims': []}],
        'facts': [{'fact_ref': 'fact_sampling', 'kind': 'capability_declaration', 'entity_id': ident(20),
            'field': 'sampling_contract', 'unit': None, 'semantics': 'statement',
            'time_scope': {'start': None, 'end': None}, 'value': 'can request temperature',
            'source_refs': [{'source_ref': source['source_ref'], 'quote_start': 0,
                'quote_end': len(source['text']), 'quote': source['text']}]}], 'known_conflicts': []}
    value = {'schema': decision.MODEL, 'input_hash': decision.input_hash(inputs),
        'summary': 'Request the missing simulation observation for review; no measured value has arrived.',
        'candidate_assessments': [{'candidate_ref': 'candidate_2', 'candidate_applicability': 'applicable',
            'applicability_reason': 'The declared target matches the requested field.',
            'evidence_integrity': 'supported', 'evidence_reason': 'The original capability declaration supports a request.',
            'capability_refs': ['cap_sampling'], 'requirement_refs': ['need_temperature'],
            'unmet_requirement_refs': [], 'fact_refs': ['fact_sampling'], 'source_refs': ['source_sampling'],
            'refutations': [], 'missing_information': []}], 'source_coverage': [{
                'source_ref': 'source_sampling', 'status': 'reviewed', 'fact_refs': ['fact_sampling'],
                'reason': 'The complete declaration was considered.'}], 'conflicts': [],
        'knowledge_gaps': [{'requirement_ref': 'need_temperature', 'reason': 'No observation has arrived.'}],
        'recommendation': {'candidate_ref': 'candidate_2', 'reason': 'Request an observation, subject to separate approval.',
            'fact_refs': ['fact_sampling']}}
    return inputs, value


def model_manifest():
    return {'contract_version': '1.0', 'kind': 'foundation_model', 'namespace': 'public_test',
        'name': 'public_decision_model', 'version': '1.0.0', 'owner': 'public synthetic fixture',
        'description': 'No credential, model call or actual source truth in this fixture.',
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
        'allowed_agent_roles': ['business'], 'execution': {'provider': 'deepseek', 'model': 'deepseek-chat',
            'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024},
        'data_policy': {'cloud_allowed': True}, 'budget': {'max_attempts': 1, 'max_concurrency': 1},
        'dependencies': [], 'specification': {}}


def scene():
    p = NS(user_id=ident(2), workspace_id=ident(1), role='operator')
    model_data = model_manifest()
    model = NS(id=ident(3), data=model_data, content_hash=digest(model_data), validated_hash=digest(model_data))
    data = {**model_manifest(), 'kind': 'tool', 'name': 'public_decision',
        'input_schema': subject.input_schema(), 'output_schema': subject.output_schema(),
        'execution': {'adapter': subject.ADAPTER, 'model_profile_id': model.id,
            'model_profile_hash': model.content_hash, 'side_effect': False}, 'dependencies': [model.id],
        'specification': {'schema': subject.CONTRACT, 'instruction': 'Compare the original proposals and retain uncertainty.'}}
    cap = NS(id=ident(4), data=data, content_hash=digest(data), validated_hash=digest(data))
    inputs = {'question': 'Which simulated observation request meets this technical goal?',
        'evidence_ids': [ident(5), ident(6)], 'data_packet': {'id': ident(7), 'evidence_id': ident(5),
            'sha256': 'e' * 64, 'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False},
        'document_packet': {'id': ident(8), 'evidence_id': ident(6), 'sha256': 'f' * 64, 'version': 1,
            'source_set_id': ident(9), 'source_set_hash': 'a' * 64},
        'decision_intent': 'information_acquisition', 'candidate_specs': [{'candidate_ref': 'candidate_2'}]}
    task = NS(id=ident(10), workspace_id=p.workspace_id, data={})
    key = 'public-decision:' + ident(11)
    job = NS(id=ident(12), workspace_id=p.workspace_id, idempotency_key=key, status='succeeded',
        kind='execute_full_semantic_graph_node', data={'actor_id': p.user_id, 'task_id': task.id,
            'activation_id': 'reasoning_activation', 'invocation_key': key, 'input_hash': digest(inputs),
            'inputs': copy.deepcopy(inputs), 'graph_hash': '1' * 64, 'cancel_epoch': 0})
    node = {'id': 'reasoning_node', 'execution': {'agent_role': 'business', 'adapter': subject.ADAPTER}}
    from generic_agents.workflow_graph_scope import SCHEMA
    scope = {'schema': SCHEMA, 'task_id': task.id, 'graph_hash': job.data['graph_hash'],
        'activation_id': job.data['activation_id'], 'node_id': node['id'], 'cancel_epoch': 0,
        'invocation_key': key, 'input_hash': digest(inputs), 'capability_id': cap.id, 'capability_hash': cap.content_hash}
    decision_input, value = declared_case()
    frozen_sources = {'schema': subject.sources.SCHEMA, 'task_id': task.id,
        'data': {'packet': inputs['data_packet']}, 'documents': {'packet': inputs['document_packet']},
        'evidence_ids': inputs['evidence_ids'], 'local_only': 'never send this local provenance marker'}
    frozen_sources['sources_hash'] = digest(frozen_sources)
    frame = {'decision_input': decision_input, 'sources': frozen_sources,
        'capability_pins': [{'capability_id': ident(21), 'content_hash': 'a' * 64}]}
    return NS(p=p, model=model, cap=cap, inputs=inputs, task=task, key=key, job=job, node=node,
        scope=scope, frame=frame, value=value)


def freeze(monkeypatch, explicit_frame_boundary):
    s = scene()
    explicit_frame_boundary.build_frame = lambda db, p, task, job, cap, inputs: copy.deepcopy(s.frame)
    monkeypatch.setattr(subject, '_current', lambda *a: (s.cap, s.model, s.task, s.job, s.node, s.scope))
    monkeypatch.setattr(subject.sources, 'resolve', lambda *a: copy.deepcopy(s.frame['sources']))
    monkeypatch.setattr(subject, '_evidence', lambda db, p, ids, model: ([{'evidence_id': i,
        'model_profile_id': model.id} for i in ids], [{'type': 'text', 'text': 'Explicit synthetic Evidence part'}]))
    def prepared(model, messages, schema):
        return {'messages': copy.deepcopy(messages), 'options': {'max_tokens': 1024},
            'request_hash': digest(messages), 'dispatch_options_hash': digest({'max_tokens': 1024}), 'token_bound': 2000}
    monkeypatch.setattr(subject.transport, 'prepare', prepared)
    _model, s.frozen = subject.prepare_request(None, s.p, s.cap, s.inputs, s.key)
    return s


def journal(monkeypatch, explicit_frame_boundary):
    s = freeze(monkeypatch, explicit_frame_boundary)
    explicit_frame_boundary.checked_frame = lambda *a: copy.deepcopy(s.frame)
    raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(s.value)}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}})
    receipt = {'call_id': ident(14), 'provider': 'deepseek', 'model': 'deepseek-chat', 'http_status': 200,
        'request_id': 'public-synthetic', 'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}}
    capture = {'raw_base64': base64.b64encode(raw.encode()).decode(), 'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'receipt': receipt}
    s.call = NS(id=ident(13), workspace_id=s.p.workspace_id, capability_id=s.cap.id, idempotency_key=s.key,
        status='succeeded', data={'capability_hash': s.cap.content_hash, 'input_hash': digest(s.inputs),
            'job_id': s.job.id, 'role': 'business', 'side_effect': False, 'approval': None,
            subject.STATE_KEY: {'schema': subject.STATE, 'request': s.frozen, 'call_id': ident(14), 'capture': capture}})
    s.ledger = NS(id=ident(14), workspace_id=s.p.workspace_id, capability_id=s.model.id, status='succeeded',
        reserved_tokens=2000, charged_tokens=50, data={'actor_id': s.p.user_id,
            'day': '2026-11-01',
            'request_hash': s.frozen['prepared']['request_hash'],
            'dispatch_options_hash': s.frozen['prepared']['dispatch_options_hash'], 'receipt': copy.deepcopy(receipt),
            'gateway_dispatch': {'identity': {'suggestion_id': s.call.id, 'job_id': s.job.id, 'task_id': s.task.id,
                'request_hash': s.frozen['request_hash'], 'workspace_id': s.p.workspace_id,
                'actor_id': s.p.user_id, 'capability_id': s.model.id}}})
    s.call.data['output'] = subject.check_result(s.frozen, s.value)
    s.task.data = {'graph_runtime': {'graph_hash': s.job.data['graph_hash'], 'activations': {
        'reasoning_activation': {'node_id': s.node['id'], 'epoch': 0, 'status': 'completed'}}},
        'graph_jobs': {'reasoning_activation': {'job_id': s.job.id, 'invocation_id': s.call.id, 'phase': 'succeeded'}}}
    anchor = subject._anchor(s.frozen)
    s.audits = [NS(action=action, actor_id=s.p.user_id, data=data) for action, data in [
        (subject.PREPARED, anchor), (subject.RESERVED, {**anchor, 'call_id': s.ledger.id}),
        (subject.CAPTURED, {**anchor, 'call_id': s.ledger.id, 'raw_sha256': capture['raw_sha256'],
            'receipt_hash': digest(receipt)})]]
    rows = {(Job, s.job.id): s.job, (Invocation, s.call.id): s.call,
        (Task, s.task.id): s.task, (CloudCall, s.ledger.id): s.ledger}
    def scoped(db, model, identity, p):
        row = rows[(model, identity)]
        assert row.workspace_id == p.workspace_id
        return row
    monkeypatch.setattr(subject, 'scoped', scoped)
    def same_sql(query):
        return str(query.compile(compile_kwargs={'literal_binds': True}))
    allowed = {same_sql(select(Audit).where(Audit.workspace_id == s.p.workspace_id,
        Audit.action == action, Audit.object_id == s.call.id)): action
        for action in (subject.PREPARED, subject.RESERVED, subject.CAPTURED)}
    invocation_query = select(Invocation).where(Invocation.workspace_id == s.p.workspace_id,
        Invocation.idempotency_key == s.key).execution_options(populate_existing=True)
    cloud_query = select(CloudCall).where(CloudCall.workspace_id == s.p.workspace_id,
        CloudCall.id == s.ledger.id).execution_options(populate_existing=True)
    reads = {same_sql(invocation_query): s.call, same_sql(cloud_query): s.ledger}
    class ReadOnlyIO:
        def scalar(self, query):
            assert query.get_execution_options().get('populate_existing') is True
            assert same_sql(query) in reads, 'unexpected SQL is never accepted'
            return reads[same_sql(query)]
        def scalars(self, query):
            assert same_sql(query) in allowed, 'unexpected SQL is never accepted'
            return [row for row in s.audits if row.action == allowed[same_sql(query)]]
        def commit(self):
            pytest.fail('historical read attempted a commit')
        def add(self, value):
            pytest.fail('historical read attempted a write')
    s.db = ReadOnlyIO()
    s.budget = NS(workspace_id=s.p.workspace_id, kind='cloud_budget', status='internal',
        data={'day': '2026-11-01', 'tokens': 2000, 'calls': 1})
    budget_query = select(Job).where(Job.workspace_id == s.p.workspace_id,
        Job.idempotency_key == 'cloud-budget:2026-11-01').with_for_update().execution_options(populate_existing=True)
    settlement_reads = {**reads, same_sql(invocation_query.with_for_update()): s.call,
        same_sql(cloud_query.with_for_update()): s.ledger,
        same_sql(select(CloudCall).where(CloudCall.id == s.ledger.id)
            .with_for_update().execution_options(populate_existing=True)): s.ledger,
        same_sql(budget_query): s.budget}
    class HistoricalSettlementIO(ReadOnlyIO):
        commits = 0
        queries = []
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def scalar(self, query):
            assert query.get_execution_options().get('populate_existing') is True
            sql = same_sql(query)
            assert sql in settlement_reads, 'unknown settlement SQL must fail closed'
            self.queries.append(sql)
            return settlement_reads[sql]
        def commit(self):
            self.commits += 1
    s.saved_db = HistoricalSettlementIO()
    from generic_agents import db as database
    marker = object()
    monkeypatch.setattr(database, 'engine', lambda: marker)
    def historical_session(engine):
        assert engine is marker
        return s.saved_db
    monkeypatch.setattr(subject, 'Session', historical_session)
    monkeypatch.setattr(subject, '_call', legacy._call)
    return s


def test_protocol_schema_is_independent_and_forged_protocol_stays_disabled():
    assert subject.ADAPTER != legacy.ADAPTER and subject.STATE_KEY != legacy.STATE_KEY
    assert subject.output_schema() == decision.output_schema() != legacy.output_schema()
    assert subject.model_value_schema() == decision.model_value_schema()
    class ForbiddenIO:
        def __getattribute__(self, name):
            pytest.fail('inactive protocol touched storage')
    from generic_agents import business_reasoning_capture_v275 as kernel
    from generic_agents import business_reasoning_protocols_v276 as protocols
    assert protocols.for_adapter(subject.ADAPTER) is subject
    with pytest.raises(ValueError, match='Unsupported server-owned'):
        kernel.execute(ForbiddenIO(), None, None, {}, 'forged', protocol=NS(__name__=subject.__name__))


def test_packet_ids_can_be_derived_without_rewriting_original_graph_inputs():
    inputs = scene().inputs
    expected = inputs.pop('evidence_ids')
    before = copy.deepcopy(inputs)
    assert subject._inputs(inputs) == inputs == before
    assert subject.evidence_ids(inputs) == expected
    assert 'evidence_ids' not in inputs


@pytest.mark.parametrize('damage', ['one_evidence', 'duplicate_evidence', 'wrong_packet_evidence', 'legacy_input', 'extra'])
def test_runtime_inputs_are_closed_and_two_distinct_actual_packet_eids(damage):
    inputs = scene().inputs
    if damage == 'one_evidence': inputs['evidence_ids'].pop()
    elif damage == 'duplicate_evidence': inputs['evidence_ids'][1] = inputs['evidence_ids'][0]
    elif damage == 'wrong_packet_evidence': inputs['data_packet']['evidence_id'] = ident(30)
    elif damage == 'legacy_input': inputs = {'question': 'legacy', 'evidence_ids': inputs['evidence_ids'], 'candidates': []}
    else: inputs['permissions_verified'] = True
    with pytest.raises((HTTPException, ValueError)):
        subject._inputs(inputs)


@pytest.mark.parametrize('damage', ['attempts', 'adapter', 'role', 'side_effect', 'cloud', 'model_hash', 'legacy_output'])
def test_manifest_does_not_relax_role_model_cloud_or_single_attempt(monkeypatch, damage):
    s = scene(); data = copy.deepcopy(s.cap.data)
    monkeypatch.setattr(subject, '_fresh_published', lambda *a: s.model)
    assert subject.validate_manifest(None, s.p, data) is s.model
    if damage == 'attempts': data['budget']['max_attempts'] = 2
    elif damage == 'adapter': data['execution']['adapter'] = legacy.ADAPTER
    elif damage == 'role': data['allowed_agent_roles'] = ['business', 'planner']
    elif damage == 'side_effect': data['execution']['side_effect'] = True
    elif damage == 'cloud': data['data_policy']['cloud_allowed'] = False
    elif damage == 'model_hash': data['execution']['model_profile_hash'] = '0' * 64
    else: data['output_schema'] = legacy.output_schema()
    with pytest.raises((HTTPException, ValueError)):
        subject.validate_manifest(None, s.p, data)


def test_prepare_sends_explicit_decision_input_hash_but_keeps_local_source_proofs(monkeypatch, explicit_frame_boundary):
    s = freeze(monkeypatch, explicit_frame_boundary)
    user = json.loads(s.frozen['messages'][1]['content'][0]['text'])
    assert s.frozen['messages'][0] == {'role': 'system', 'content': subject.SYSTEM_INSTRUCTION}
    assert user['decision_input'] == s.frame['decision_input']
    assert user['input_hash'] == decision.input_hash(s.frame['decision_input'])
    assert 'never send this local provenance marker' not in json.dumps(s.frozen['messages'])
    assert s.frozen['frame']['sources'] == s.frame['sources']
    result = subject.check_result(s.frozen, s.value)
    assert result['review_state'] == 'ready_for_review' and not result['current_facts_sufficient']
    assert result['current_outcome_verified'] is False and result['can_dispatch'] is False
    with pytest.raises(HTTPException):
        legacy.check_result(s.frozen, s.value)


def test_qualify_evidence_uses_the_two_family_sources_resolver(monkeypatch):
    s = scene(); seen = []
    monkeypatch.setattr(subject.sources, 'resolve', lambda *args: seen.append(args) or {'checked': True})
    assert subject.qualify_evidence(s.db if hasattr(s, 'db') else None, s.p, s.task, s.inputs) == {'checked': True}
    assert seen[0][2:] == (s.task, s.inputs)


@pytest.mark.parametrize('damage', [None, 'cap_hash', 'original_node', 'role', 'adapter', 'external', 'inputs'])
def test_current_uses_original_graph_scope_and_rejects_mismatched_external_node(monkeypatch, damage):
    from generic_agents import workflow_graph_scope as scope_family
    from generic_agents import workflow_graph as graph_family
    s = scene(); actual = copy.deepcopy(s.cap); node = copy.deepcopy(s.node); original_node = copy.deepcopy(node)
    external = [node['id']]; seen = []
    if damage == 'cap_hash': actual.content_hash = '0' * 64
    elif damage == 'original_node': original_node['id'] = 'another_original'
    elif damage == 'role': node['execution']['agent_role'] = original_node['execution']['agent_role'] = 'planner'
    elif damage == 'adapter': node['execution']['adapter'] = original_node['execution']['adapter'] = legacy.ADAPTER
    elif damage == 'external': external = []
    elif damage == 'inputs': s.job.data['inputs']['question'] = 'A different frozen question'
    monkeypatch.setattr(subject, '_fresh_published', lambda *a: actual)
    monkeypatch.setattr(subject, 'validate_manifest', lambda *a: s.model)
    def graph_context(db, p, key, cap):
        seen.append((p, key, cap))
        return s.task, s.job, node, s.scope
    monkeypatch.setattr(scope_family, 'graph_context', graph_context)
    monkeypatch.setattr(scope_family, '_frozen', lambda *a: (s.task, object(), {}, None, None, None, original_node))
    monkeypatch.setattr(graph_family, 'business_binding', lambda *a: {'external_node_ids': external})
    monkeypatch.setattr(subject, '_call', lambda *a: NS(data={}))
    if damage:
        with pytest.raises(HTTPException):
            subject._current(None, s.p, s.cap, s.inputs, s.key)
    else:
        assert subject._current(None, s.p, s.cap, s.inputs, s.key) == (actual, s.model, s.task, s.job, node, s.scope)
        assert seen == [(s.p, s.key, actual)]


def test_reserved_boundary_rechecks_original_frame_before_send(monkeypatch, explicit_frame_boundary):
    from generic_agents import workflow_graph_scope as scope_family
    from generic_agents import workflow_graph as graph_family
    current = subject._current
    s = journal(monkeypatch, explicit_frame_boundary)
    monkeypatch.setattr(subject, '_fresh_published', lambda *a: s.cap)
    monkeypatch.setattr(subject, 'validate_manifest', lambda *a: s.model)
    monkeypatch.setattr(scope_family, 'graph_context', lambda *a: (s.task, s.job, s.node, s.scope))
    monkeypatch.setattr(scope_family, '_frozen', lambda *a: (s.task, object(), {}, None, None, None, s.node))
    monkeypatch.setattr(graph_family, 'business_binding', lambda *a: {'external_node_ids': [s.node['id']]})
    assert current(s.db, s.p, s.cap, s.inputs, s.key)[0] is s.cap
    changed = copy.deepcopy(s.frame); changed['capability_pins'][0]['content_hash'] = '0' * 64
    explicit_frame_boundary.build_frame = lambda *a: changed
    with pytest.raises(HTTPException):
        current(s.db, s.p, s.cap, s.inputs, s.key)


@pytest.mark.parametrize('damage', ['missing_frame_key', 'wrong_task', 'changed_source', 'empty_pins'])
def test_frame_must_match_real_task_and_independent_source_snapshot(monkeypatch, explicit_frame_boundary, damage):
    s = scene(); frame = copy.deepcopy(s.frame)
    if damage == 'missing_frame_key': frame.pop('capability_pins')
    elif damage == 'wrong_task': frame['decision_input']['scope']['task_id'] = ident(99)
    elif damage == 'empty_pins': frame['capability_pins'] = []
    else:
        frame['sources']['local_only'] = 'changed'
        frame['sources']['sources_hash'] = digest({k: v for k, v in frame['sources'].items() if k != 'sources_hash'})
    explicit_frame_boundary.build_frame = lambda *a: frame
    monkeypatch.setattr(subject.sources, 'resolve', lambda *a: s.frame['sources'])
    with pytest.raises((HTTPException, ValueError)):
        subject._build_frame(None, s.p, s.task, s.job, s.cap, s.inputs, s.scope)


@pytest.mark.parametrize('terminal_status', ['succeeded', 'failed'])
def test_recover_terminal_original_raw_never_dispatches_or_rewrites_terminal_budget(monkeypatch, explicit_frame_boundary, terminal_status):
    s = journal(monkeypatch, explicit_frame_boundary)
    s.ledger.status = terminal_status
    monkeypatch.setattr(subject, '_current', lambda *a: pytest.fail('replay attempted live execution'))
    monkeypatch.setattr(subject.transport, 'call', lambda *a, **k: pytest.fail('replay attempted HTTP'))
    before = copy.deepcopy((s.call.data, s.ledger.data, s.ledger.status))
    result = subject.recover(s.db, s.p, s.cap, s.inputs, s.key)
    assert result == s.call.data['output'] and (s.call.data, s.ledger.data, s.ledger.status) == before
    assert s.saved_db.commits == 0
    s.call.data[subject.STATE_KEY]['capture'] = None
    with pytest.raises(ModelBlocked):
        subject.recover(s.db, s.p, s.cap, s.inputs, s.key)


@pytest.mark.parametrize('damage', ['charge', 'receipt', 'reserve_bound'])
def test_recover_blocks_changed_terminal_receipt_without_writing(monkeypatch, explicit_frame_boundary, damage):
    s = journal(monkeypatch, explicit_frame_boundary)
    if damage == 'charge': s.ledger.charged_tokens = 0
    elif damage == 'reserve_bound': s.ledger.reserved_tokens += 1
    else: s.ledger.data['receipt']['usage']['total_tokens'] += 1
    before = copy.deepcopy((s.call.data, s.ledger.data, s.ledger.status))
    with pytest.raises(HTTPException):
        subject.recover(s.db, s.p, s.cap, s.inputs, s.key)
    assert (s.call.data, s.ledger.data, s.ledger.status) == before


def reserved(s):
    s.ledger.status = 'reserved'; s.ledger.charged_tokens = s.ledger.reserved_tokens
    s.ledger.data.pop('receipt', None)
    return s


def replace_raw(s, raw, *, receipt=None):
    capture = s.call.data[subject.STATE_KEY]['capture']
    capture['raw_base64'] = base64.b64encode(raw.encode()).decode()
    capture['raw_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
    if receipt is not None: capture['receipt'] = copy.deepcopy(receipt)
    s.audits[2].data = {**subject._anchor(s.frozen), 'call_id': s.ledger.id,
        'raw_sha256': capture['raw_sha256'], 'receipt_hash': digest(capture['receipt'])}


def test_recover_settles_only_original_reserved_row_via_real_guarded_budget_sql(monkeypatch, explicit_frame_boundary):
    s = reserved(journal(monkeypatch, explicit_frame_boundary))
    from generic_agents import cloud_budget
    real_settle = cloud_budget.settle_paid_response; seen = []
    def settle(db, call_id, receipt, expected, status):
        seen.append((call_id, copy.deepcopy(expected), status))
        return real_settle(db, call_id, receipt, expected, status)
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', settle)
    monkeypatch.setattr(cloud_budget, 'reserve', lambda *a, **k: pytest.fail('recovery reserved a new call'))
    monkeypatch.setattr(subject.transport, 'call', lambda *a, **k: pytest.fail('recovery posted to a provider'))
    before = copy.deepcopy(s.call.data)
    assert subject.recover(s.db, s.p, s.cap, s.inputs, s.key) == s.call.data['output']
    assert s.call.data == before and s.ledger.status == 'succeeded' and s.ledger.charged_tokens == 50
    assert s.budget.data == {'day': '2026-11-01', 'tokens': 50, 'calls': 1}
    assert s.saved_db.commits == 1
    assert len(seen) == 1 and seen[0][0] == s.ledger.id and seen[0][2] == 'succeeded'
    assert seen[0][1]['actor_id'] == s.p.user_id and seen[0][1]['capability_id'] == s.model.id
    assert any('FOR UPDATE' in query and 'invocations' in query for query in s.saved_db.queries)
    assert any('FOR UPDATE' in query and 'cloud_model_calls' in query for query in s.saved_db.queries)
    assert all(ident(99) not in query for query in s.saved_db.queries)
    after = copy.deepcopy((s.ledger.data, s.ledger.status, s.budget.data))
    subject.recover(s.db, s.p, s.cap, s.inputs, s.key)
    assert (s.ledger.data, s.ledger.status, s.budget.data) == after and s.saved_db.commits == 1 and len(seen) == 1


def test_recover_failed_parse_settles_same_original_and_preserves_raw(monkeypatch, explicit_frame_boundary):
    s = reserved(journal(monkeypatch, explicit_frame_boundary))
    raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}})
    replace_raw(s, raw); before = copy.deepcopy(s.call.data)
    with pytest.raises(ModelBlocked):
        subject.recover(s.db, s.p, s.cap, s.inputs, s.key)
    assert s.call.data == before and s.ledger.status == 'failed' and s.ledger.charged_tokens == 50
    assert s.budget.data['tokens'] == 50 and s.budget.data['calls'] == 1 and s.saved_db.commits == 1


def test_recover_unknown_usage_keeps_original_reservation_without_fabricated_usage(monkeypatch, explicit_frame_boundary):
    s = reserved(journal(monkeypatch, explicit_frame_boundary))
    receipt = copy.deepcopy(s.call.data[subject.STATE_KEY]['capture']['receipt']); receipt['usage'] = {}
    replace_raw(s, 'unparseable original provider body', receipt=receipt)
    with pytest.raises(ModelBlocked):
        subject.recover(s.db, s.p, s.cap, s.inputs, s.key)
    assert s.ledger.status == 'failed' and s.ledger.charged_tokens == 2000
    assert s.ledger.data['receipt']['usage'] == {} and s.ledger.data['usage_unavailable'] is True
    assert s.budget.data['tokens'] == 2000 and s.budget.data['calls'] == 1


def test_oversized_saved_first_answer_can_close_only_its_original_reservation(monkeypatch, explicit_frame_boundary):
    s = reserved(journal(monkeypatch, explicit_frame_boundary))
    receipt = copy.deepcopy(s.call.data[subject.STATE_KEY]['capture']['receipt']); receipt['usage'] = {}
    replace_raw(s, 'x' * (subject.transport.MAX_RESPONSE_BYTES + 1), receipt=receipt)
    before = copy.deepcopy(s.call.data)
    monkeypatch.setattr(subject.transport, 'call', lambda *a, **k: pytest.fail('oversized replay attempted HTTP'))
    with pytest.raises(ModelBlocked):
        subject.recover(s.db, s.p, s.cap, s.inputs, s.key)
    assert s.call.data == before and s.ledger.status == 'failed' and s.ledger.charged_tokens == 2000
    assert s.ledger.data['receipt']['usage'] == {} and s.budget.data['tokens'] == 2000
    assert s.budget.data['calls'] == 1 and s.saved_db.commits == 1


@pytest.mark.parametrize('damage', ['audit', 'actor', 'request', 'bound', 'budget', 'unproven_usage'])
def test_original_settlement_guard_blocks_insufficient_proof(monkeypatch, explicit_frame_boundary, damage):
    s = reserved(journal(monkeypatch, explicit_frame_boundary))
    receipt = copy.deepcopy(s.call.data[subject.STATE_KEY]['capture']['receipt'])
    if damage == 'audit': s.audits.pop()
    elif damage == 'actor': s.ledger.data['actor_id'] = ident(99)
    elif damage == 'request': s.ledger.data['request_hash'] = '0' * 64
    elif damage == 'bound': s.ledger.reserved_tokens += 1
    elif damage == 'budget': s.budget.data['tokens'] = 0
    else: receipt['usage']['total_tokens'] += 1
    before = copy.deepcopy((s.call.data, s.ledger.data, s.ledger.status, s.budget.data))
    with pytest.raises((HTTPException, ModelBlocked)):
        subject._settle_capture(s.p, s.cap, s.inputs, s.key, receipt, 'succeeded')
    assert (s.call.data, s.ledger.data, s.ledger.status, s.budget.data) == before
    assert s.saved_db.commits == 0


def test_saved_result_retains_current_reader_and_original_actor(monkeypatch, explicit_frame_boundary):
    s = journal(monkeypatch, explicit_frame_boundary); reader = NS(user_id=ident(40), workspace_id=s.p.workspace_id)
    seen = []
    def checked(db, p, task, frozen):
        seen.append(p.user_id)
        return copy.deepcopy(s.frame['sources'])
    monkeypatch.setattr(subject.sources, 'checked_frozen', checked)
    def checked_frame(db, p, task, job, cap, inputs, frame):
        seen.append(p.user_id)
        assert job.data['actor_id'] == s.p.user_id and p.user_id != job.data['actor_id']
        return copy.deepcopy(s.frame)
    explicit_frame_boundary.checked_frame = checked_frame
    monkeypatch.setattr(subject, '_build_frame', lambda *a: pytest.fail('cold proof rebuilt current decision frame'))
    before = copy.deepcopy((s.call.data, s.ledger.data, s.task.data))
    assert subject.saved_result(s.db, reader, s.task, s.job, s.call, s.cap, s.inputs) == s.call.data['output']
    assert seen == [reader.user_id, reader.user_id] and (s.call.data, s.ledger.data, s.task.data) == before


@pytest.mark.parametrize('damage', ['audit_actor', 'missing_audit', 'ledger_actor', 'raw', 'frame',
    'ledger_reserved', 'charged_tokens', 'output', 'source', 'capability_proof', 'incomplete_activation'])
def test_saved_result_requires_all_original_cold_proofs(monkeypatch, explicit_frame_boundary, damage):
    s = journal(monkeypatch, explicit_frame_boundary)
    monkeypatch.setattr(subject.sources, 'checked_frozen', lambda *a: copy.deepcopy(s.frame['sources']))
    if damage == 'audit_actor': s.audits[0].actor_id = ident(50)
    elif damage == 'missing_audit': s.audits.pop()
    elif damage == 'ledger_actor': s.ledger.data['actor_id'] = ident(50)
    elif damage == 'raw': s.call.data[subject.STATE_KEY]['capture']['raw_base64'] = base64.b64encode(b'{}').decode()
    elif damage == 'frame': s.frozen['frame']['capability_pins'][0]['content_hash'] = '0' * 64
    elif damage == 'ledger_reserved': s.ledger.status = 'reserved'
    elif damage == 'charged_tokens': s.ledger.charged_tokens = 0
    elif damage == 'output': s.call.data['output']['can_dispatch'] = True
    elif damage == 'source':
        monkeypatch.setattr(subject.sources, 'checked_frozen', lambda *a: {'forged': True})
    elif damage == 'capability_proof':
        explicit_frame_boundary.checked_frame = lambda *a: {'forged': True}
    else: s.task.data['graph_runtime']['activations']['reasoning_activation']['status'] = 'ready'
    with pytest.raises(HTTPException):
        subject.saved_result(s.db, s.p, s.task, s.job, s.call, s.cap, s.inputs)


def test_new_request_exposes_only_mechanical_review_scope(monkeypatch, explicit_frame_boundary):
    from generic_agents.business_decision_assignment_v283 import review_assignment
    s=freeze(monkeypatch,explicit_frame_boundary)
    user=json.loads(s.frozen['messages'][1]['content'][0]['text'])
    assert user['review_assignment']==review_assignment(s.frame['decision_input'])
    assignment=user['review_assignment']
    assert set(assignment)=={'schema','input_hash','candidate_scope','source_scope'}
    assert assignment['candidate_scope']==[{'candidate_ref':'candidate_2',
        'capability_refs':['cap_sampling'],'requirement_refs':['need_temperature']}]
    assert assignment['source_scope']==[{'source_ref':'source_sampling','fact_refs':['fact_sampling']}]
    assert 'never send this local provenance marker' not in json.dumps(assignment)


def test_packet_enclosure_does_not_assign_its_cited_capability_facts():
    from generic_agents.business_decision_assignment_v283 import review_assignment
    inputs,_=declared_case();before=copy.deepcopy(inputs)
    inputs['sources'].append({'source_ref':'packet_context','kind':'packet','source_id':ident(30),
        'content_hash':'e'*64,'text':'Context mentions the declaration; no direct fact cites this packet.',
        'readability':'readable'})
    assignment=review_assignment(inputs)
    assert assignment['source_scope']==[{'source_ref':'source_sampling','fact_refs':['fact_sampling']},
        {'source_ref':'packet_context','fact_refs':[]}]
    assignment['candidate_scope'][0]['requirement_refs'].clear()
    assert inputs['requirements']==before['requirements']
    assert review_assignment(inputs)['candidate_scope'][0]['requirement_refs']==['need_temperature']


def test_every_candidate_receives_full_requirement_scope_without_judgment():
    from generic_agents.business_decision_assignment_v283 import review_assignment
    inputs,_=declared_case();candidate=copy.deepcopy(inputs['candidates'][0]);candidate.update(candidate_ref='candidate_unmapped',requirement_mappings=[]);inputs['candidates'].insert(0,candidate)
    assignment=review_assignment(inputs)
    assert [x['candidate_ref'] for x in assignment['candidate_scope']]==['candidate_unmapped','candidate_2']
    assert all(x['requirement_refs']==['need_temperature'] and set(x)=={'candidate_ref','capability_refs','requirement_refs'} for x in assignment['candidate_scope'])


def test_assignment_rejects_damaged_original_fact_references():
    from generic_agents.business_decision_assignment_v283 import review_assignment
    inputs,_=declared_case();inputs['facts'][0]['source_refs'][0]['source_ref']='unknown_source'
    with pytest.raises(decision.DecisionContractError):review_assignment(inputs)


def test_frozen_result_reader_does_not_rebuild_new_prompt_assignment(monkeypatch, explicit_frame_boundary):
    import generic_agents.business_decision_assignment_v283 as assignment
    s=freeze(monkeypatch,explicit_frame_boundary)
    monkeypatch.setattr(assignment,'review_assignment',lambda *_:pytest.fail('historical result rebuilt request scope'))
    output=subject.check_result(s.frozen,s.value)
    assert output['review_state']=='ready_for_review' and output['can_dispatch'] is False
