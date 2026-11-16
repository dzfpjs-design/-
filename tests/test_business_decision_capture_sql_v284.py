"""Real PostgreSQL V284 first-answer retention and terminal-merge regressions.

Storage-only proof: graph/current declarations, Frame/source truth, Evidence and
provider boundaries are explicit spies. This is not IAM, source authenticity,
paid HTTP, approval, acquisition, ACK or business-completion/E2E evidence.
Sessions, canonical payload writes, gateway terminal merge, V275 reserved and
independent capture callbacks, V284 prepare/check/state code, real rows, audits
and worker lease fencing are not mocked.
"""
import base64
import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import business_decision_contracts_v284 as decision
from generic_agents import business_decision_runtime_v284 as reasoning
from generic_agents import business_decision_runtime_v276 as previous
from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import gateway, worker, leases
from generic_agents import layered_approval_store_v175 as approvals
from generic_agents import workflow_graph_scope as graph_scope
from generic_agents import workflow_document_packet_v207 as packets
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Task, User
from generic_agents.security import Principal


def cap_row(workspace, data):
    return Capability(workspace_id=workspace, namespace=data['namespace'], name=data['name'],
        release_version=data['version'], kind=data['kind'], status='published', data=data,
        content_hash=digest(data), validated_hash=digest(data))


@pytest.fixture
def sql_scene(account):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_')
    workspace = account[2]
    with Session(bound, expire_on_commit=False) as db:
        actor = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(actor.id, workspace, 'admin', 'decision-capture-sql-v284')
        model_data = Manifest(kind='foundation_model', namespace='capture_sql_v284', name='model',
            version='1.0.0', owner=p.user_id, description='Synthetic provider boundary for SQL retention',
            allowed_agent_roles=['business'], execution={'adapter': 'cloud', 'provider': 'qwen',
                'model': 'qwen-plus', 'max_tokens': 128}, data_policy={'cloud_allowed': True}).model_dump(mode='json')
        model = cap_row(workspace, model_data); db.add(model); db.flush()
        cap_data = Manifest(kind='tool', namespace='capture_sql_v284', name='reasoner', version='1.0.0',
            owner=p.user_id, description='V284 storage durability regression', allowed_agent_roles=['business'],
            input_schema=reasoning.input_schema(), output_schema=reasoning.output_schema(),
            execution={'adapter': reasoning.ADAPTER, 'model_profile_id': model.id,
                'model_profile_hash': model.content_hash, 'side_effect': False},
            data_policy={'cloud_allowed': True}, dependencies=[model.id],
            specification={'schema': reasoning.CONTRACT, 'instruction': 'Review the original synthetic declarations'},
            budget={'max_attempts': 1}).model_dump(mode='json')
        cap = cap_row(workspace, cap_data); db.add(cap); db.flush()
        # An actual SQL capability identity/hash backs the deliberately spied
        # declaration. No collection operation is invoked or authorized here.
        sampling_data = Manifest(kind='tool', namespace='capture_sql_v284', name='sampling', version='2.0.0',
            owner=p.user_id, description='Synthetic simulated temperature-request declaration',
            allowed_agent_roles=['business'], input_schema={'type': 'object', 'properties': {}},
            output_schema={'type': 'object', 'properties': {'requested_observation': {'type': 'object',
                'properties': {'temperature_c': {'type': 'number', 'unit': 'Cel'}}}}},
            execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
                'operation': 'resense_from_context', 'side_effect': True}).model_dump(mode='json')
        sampling = cap_row(workspace, sampling_data); db.add(sampling); db.flush()
        package = cap_row(workspace, Manifest(kind='domain_package', namespace='capture_sql_v284',
            name='package', version='1.0.0', owner=p.user_id, description='Storage fixture package').model_dump(mode='json'))
        db.add(package); db.flush()
        task = Task(workspace_id=workspace, owner_id=p.user_id, package_id=package.id,
            title='V284 SQL first-response retention only', data={})
        db.add(task); db.flush()
        entity_id = str(uuid4())  # Pure input scope, not a claim of a real Entity/source.
        evidence_ids = [str(uuid4()), str(uuid4())]
        inputs = {'question': 'Which simulated temperature-request proposal can be reviewed?',
            'evidence_ids': evidence_ids,
            'data_packet': {'id': str(uuid4()), 'evidence_id': evidence_ids[0], 'sha256': 'e' * 64,
                'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False},
            'document_packet': {'id': str(uuid4()), 'evidence_id': evidence_ids[1], 'sha256': 'f' * 64,
                'version': 1, 'source_set_id': str(uuid4()), 'source_set_hash': 'a' * 64},
            'decision_intent': 'information_acquisition', 'candidate_specs': [{
                'candidate_ref': 'candidate_2', 'proposal': 'Request the missing simulated temperature for review.',
                'claims': [], 'capability_pins': [{'capability_ref': 'cap_sampling',
                    'capability_id': sampling.id, 'capability_hash': sampling.content_hash,
                    'release_version': sampling.release_version, 'delivery': 'requested_observation',
                    'ports': [{'field': 'temperature_c', 'pointer': '/requested_observation/temperature_c'}]}]}]}
        assert reasoning._inputs(inputs) == inputs  # Actual full CandidateSpec/input contract.
        key = 'decision-capture-sql-v284:' + str(uuid4())
        job = Job(workspace_id=workspace, kind=graph_scope.KIND, idempotency_key=key,
            data={'actor_id': p.user_id, 'task_id': task.id, 'activation_id': 'analysis',
                'invocation_key': key, 'input_hash': digest(inputs), 'inputs': copy.deepcopy(inputs),
                'graph_hash': digest({'explicit_graph_scope_spy': task.id}), 'cancel_epoch': 0})
        db.add(job); db.flush()
        text = 'This declaration can request simulated temperature; no measurement or ACK has arrived.'
        source = {'source_ref': 'source_sampling', 'kind': 'capability', 'source_id': sampling.id,
            'content_hash': sampling.content_hash, 'text': text, 'readability': 'readable'}
        decision_input = {'schema': decision.INPUT, 'decision_intent': 'information_acquisition',
            'scope': {'task_id': task.id, 'activation_id': 'analysis', 'goal_snapshot_hash': 'b' * 64,
                'entity_ids': [entity_id]}, 'requirements': [{'requirement_ref': 'need_temperature',
                'entity_id': entity_id, 'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel',
                'semantics': 'instantaneous', 'required_kind': 'observation', 'time_mode': 'bounded',
                'time_scope': {'start': '2026-11-01T11:59:00+00:00', 'end': '2026-11-01T12:01:00+00:00'}}],
            'sources': [source], 'capabilities': [{'capability_ref': 'cap_sampling',
                'capability_id': sampling.id, 'capability_hash': sampling.content_hash,
                'release_version': sampling.release_version, 'input_schema_hash': digest(sampling.data['input_schema']),
                'output_schema_hash': digest(sampling.data['output_schema']), 'source_ref': source['source_ref'],
                'action_kind': 'information_acquisition', 'output_ports': [], 'information_targets': [{
                    'port_ref': 'target_temperature', 'pointer': '/requested_observation/temperature_c',
                    'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'}]}],
            'candidates': [{'candidate_ref': 'candidate_2', 'proposal': inputs['candidate_specs'][0]['proposal'],
                'capability_refs': ['cap_sampling'], 'requirement_mappings': [{'requirement_ref': 'need_temperature',
                    'capability_ref': 'cap_sampling', 'delivery': 'requested_observation',
                    'port_ref': 'target_temperature'}], 'claims': []}],
            'facts': [{'fact_ref': 'fact_sampling', 'kind': 'capability_declaration', 'entity_id': entity_id,
                'field': 'sampling_contract', 'unit': None, 'semantics': 'statement',
                'time_scope': {'start': None, 'end': None}, 'value': 'can request simulated temperature',
                'source_refs': [{'source_ref': source['source_ref'], 'quote_start': 0, 'quote_end': len(text),
                    'quote': text}]}], 'known_conflicts': []}
        assert decision.check_input(decision_input) == decision_input
        frozen_sources = {'schema': reasoning.sources.SCHEMA, 'task_id': task.id,
            'data': {'packet': copy.deepcopy(inputs['data_packet'])},
            'documents': {'packet': copy.deepcopy(inputs['document_packet'])},
            'evidence_ids': evidence_ids, 'explicit_source_boundary_spy': True}
        frozen_sources['sources_hash'] = digest(frozen_sources)
        frame = {'decision_input': decision_input, 'sources': frozen_sources,
            'capability_pins': {'explicit_current_declaration_spy': True, 'capabilities': [{
                'capability_id': sampling.id, 'capability_hash': sampling.content_hash,
                'release_version': sampling.release_version}]}}
        db.commit()
        return SimpleNamespace(p=p, cap_id=cap.id, model_id=model.id, sampling_id=sampling.id,
            task_id=task.id, job_id=job.id, inputs=inputs, key=key, frame=frame)


@pytest.mark.parametrize('status', ['blocked_model', 'succeeded'])
def test_locked_terminal_reload_does_not_autoflush_dirty_old_data_over_another_session(sql_scene, status):
    s = sql_scene
    with Session(engine(), expire_on_commit=False) as initial:
        cap = initial.get(Capability, s.cap_id)
        call = Invocation(workspace_id=s.p.workspace_id, capability_id=cap.id, idempotency_key=s.key,
            status='running', data={'input_hash': digest(s.inputs), 'capability_hash': cap.content_hash,
                'role': 'business', 'approval': None, 'side_effect': False, 'started_at': '2026-11-01T12:00:00+00:00',
                reasoning.STATE_KEY: {'capture': None}})
        initial.add(call); initial.commit(); call_id = call.id
    with Session(engine(), expire_on_commit=False) as outer:
        stale = outer.get(Invocation, call_id)
        identity = {field: copy.deepcopy(stale.data.get(field)) for field in gateway._BUSINESS_OUTCOME_IDENTITY}
        stale.data = {**stale.data, 'uncommitted_old_payload': True}
        capture = {'raw_base64': base64.b64encode(b'original V284 reply').decode(),
            'raw_sha256': hashlib.sha256(b'original V284 reply').hexdigest(), 'receipt': {'call_id': str(uuid4())}}
        with Session(engine()) as independent:
            actual = independent.get(Invocation, call_id)
            actual.data = {**actual.data, reasoning.STATE_KEY: {'capture': copy.deepcopy(capture)},
                'independent_commit_marker': 'preserve'}
            independent.commit()
        assert stale.data[reasoning.STATE_KEY]['capture'] is None
        refreshed = gateway._business_reasoning_outcome_row(outer, s.p, stale, s.cap_id, s.key, identity)
        assert refreshed is stale and refreshed.data[reasoning.STATE_KEY]['capture'] == capture
        refreshed.status = status; refreshed.data = {**refreshed.data, 'terminal_marker': status}
        outer.commit()
    with Session(engine()) as checked:
        actual = checked.get(Invocation, call_id)
        assert actual.status == status and actual.data[reasoning.STATE_KEY]['capture'] == capture
        assert actual.data['independent_commit_marker'] == 'preserve'
        assert 'uncommitted_old_payload' not in actual.data


def model_answer():
    return {'schema': decision.MODEL, 'candidate_reviews': [{'candidate_ref': 'candidate_2',
        'suitability': 'suitable', 'reasoning': 'The declaration supports requesting the missing observation.',
        'supporting_fact_refs': ['fact_sampling'], 'blocking_conditions': []}],
        'source_reviews': [{'source_ref': 'source_sampling', 'assessment': 'reviewed',
            'reason': 'The original declaration was reviewed.'}], 'conflicts': [],
        'recommendation': {'candidate_ref': 'candidate_2', 'reason': 'Request the measurement for separate review.',
            'supporting_fact_refs': ['fact_sampling']}}


def install_declaration_and_provider_spies(monkeypatch, s, outcome):
    """Explicit I/O seams only; production prepare/state/derivation stay intact."""
    evidence_pins = [{'synthetic_evidence_id': identity, 'model_profile_id': s.model_id}
        for identity in s.inputs['evidence_ids']]
    trace = SimpleNamespace(posts=0, raw=None, capture_receipt=None, frozen=None, messages=None)

    def current(db, p, cap, inputs, key):
        assert p is s.p and key == s.key and inputs == s.inputs and cap.id == s.cap_id
        model = reasoning.validate_manifest(db, p, cap.data)  # Actual manifest/profile checks.
        task = db.get(Task, s.task_id); job = db.get(Job, s.job_id)
        node = {'id': 'analysis', 'execution': {'agent_role': 'business', 'adapter': reasoning.ADAPTER}}
        scope = {'schema': graph_scope.SCHEMA, 'task_id': task.id, 'graph_hash': job.data['graph_hash'],
            'activation_id': 'analysis', 'node_id': 'analysis', 'cancel_epoch': 0, 'invocation_key': key,
            'input_hash': digest(inputs), 'capability_id': cap.id, 'capability_hash': cap.content_hash}
        return cap, model, task, job, node, scope

    def frame_boundary(db, p, task, job, cap, inputs, scope):
        assert task.id == s.task_id and job.id == s.job_id and cap.id == s.cap_id and inputs == s.inputs
        assert scope['task_id'] == task.id and scope['node_id'] == 'analysis'
        return copy.deepcopy(s.frame)

    def qualify(db, p, task, inputs):
        assert task.id == s.task_id and inputs == s.inputs
        return copy.deepcopy(s.frame['sources'])

    def prepared(model, messages, schema):
        assert model.id == s.model_id and schema == reasoning.model_value_schema()
        trace.messages = copy.deepcopy(messages)
        options = {'model': 'qwen-plus', 'max_tokens': 128}
        return {'messages': copy.deepcopy(messages), 'options': options,
            'request_hash': digest(messages), 'dispatch_options_hash': digest(options), 'token_bound': 4096}

    monkeypatch.setattr(reasoning, '_current', current)
    monkeypatch.setattr(reasoning, '_build_frame', frame_boundary)
    monkeypatch.setattr(reasoning, 'qualify_evidence', qualify)
    monkeypatch.setattr(reasoning, '_evidence', lambda *args: (copy.deepcopy(evidence_pins), []))
    monkeypatch.setattr(transport, 'prepare', prepared)
    # These graph/packet/approval seams deliberately do not prove live IAM,
    # source authenticity, registered adoption or independent approval.
    monkeypatch.setattr(packets, 'qualify_consumer', lambda *args: None)
    monkeypatch.setattr(graph_scope, 'owned_graph_job', lambda *args: None)
    monkeypatch.setattr(approvals, 'before_workflow_dispatch', lambda *args: False)

    def provider_boundary(model, sent_messages, schema, principal, **kwargs):
        assert principal is s.p and model.id == s.model_id and sent_messages == trace.messages
        assert schema == reasoning.model_value_schema()
        trace.posts += 1
        with Session(engine()) as ledger_db:
            original = ledger_db.scalar(select(Invocation).where(Invocation.workspace_id == s.p.workspace_id,
                Invocation.idempotency_key == s.key))
            trace.frozen = copy.deepcopy(original.data[reasoning.STATE_KEY]['request'])
            assert trace.frozen['frame_hash'] == digest(s.frame)
            assert trace.frozen['pin']['decision_input_hash'] == decision.input_hash(s.frame['decision_input'])
            assert trace.frozen['pin']['sources_hash'] == s.frame['sources']['sources_hash']
            ledger = CloudCall(workspace_id=s.p.workspace_id, capability_id=s.model_id,
                reserved_tokens=4096, charged_tokens=4096, status='reserved',
                data={'actor_id': s.p.user_id, 'request_hash': trace.frozen['prepared']['request_hash'],
                    'dispatch_options_hash': trace.frozen['prepared']['dispatch_options_hash'],
                    'gateway_dispatch': {'identity': {**kwargs['dispatch_identity'],
                        'workspace_id': s.p.workspace_id, 'actor_id': s.p.user_id, 'capability_id': s.model_id}}})
            ledger_db.add(ledger); ledger_db.flush(); call_id = ledger.id; ledger_db.commit()
        kwargs['on_reserved'](call_id)  # Original V275 callback, with real SQL rows.
        answer = model_answer()
        if outcome == 'semantic_rejected':
            answer['candidate_reviews'][0]['supporting_fact_refs'] = ['unknown_fact']
        raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
            {} if outcome == 'schema_rejected' else answer)}}],
            'usage': {'prompt_tokens': 13, 'completion_tokens': 10, 'total_tokens': 23}})
        receipt = {'provider': 'qwen', 'model': 'qwen-plus', 'call_id': call_id,
            'request_id': 'synthetic-sql-v284-reply', 'http_status': 200, 'usage': {},
            'usage_unavailable': True, 'usage_unavailable_reason': 'unparsed_original_http', 'stage': transport.STAGE}
        trace.raw = raw; trace.capture_receipt = copy.deepcopy(receipt)
        kwargs['on_response'](raw, receipt)  # Original independent capture Session.
        receipt = transport.response_receipt(raw, receipt)
        try:
            result, receipt = transport.parse_response(raw, schema, receipt)
        except gateway.ModelBlocked:
            terminal = 'failed'; result = None
        else:
            terminal = 'succeeded'
        # The synthetic provider boundary closes this one actual ledger. This
        # is a storage test, not an assertion about live daily-budget admission.
        with Session(engine()) as ledger_db:
            ledger = ledger_db.get(CloudCall, call_id)
            ledger.status = terminal; ledger.charged_tokens = 23
            ledger.data = {**ledger.data, 'receipt': copy.deepcopy(receipt)}
            ledger_db.commit()
        if terminal == 'failed':
            raise gateway.ModelBlocked('Synthetic original response failed structural validation')
        return result, receipt

    monkeypatch.setattr(gateway, 'cloud_json', provider_boundary)
    return trace


@pytest.mark.parametrize('outcome,status,ledger_status', [
    ('schema_rejected', 'blocked_model', 'failed'),
    ('semantic_rejected', 'blocked_evidence', 'succeeded'),
    ('succeeded', 'succeeded', 'succeeded')])
def test_real_callbacks_and_gateway_terminal_keep_the_same_v284_first_answer(sql_scene, monkeypatch, outcome, status, ledger_status):
    s = sql_scene; trace = install_declaration_and_provider_spies(monkeypatch, s, outcome)
    claimed = worker.claim(job_id=s.job_id, kinds=[graph_scope.KIND], workspace_id=s.p.workspace_id)
    assert claimed is not None and claimed[0] == s.job_id
    with Session(engine(), expire_on_commit=False) as outer:
        leases.attach(outer, *claimed); outer.info['worker_actor'] = s.p
        call = gateway.invoke(outer, s.p, s.cap_id, 'business', s.inputs, s.key)
        call_id = call.id
    with Session(engine()) as checked:
        call = checked.get(Invocation, call_id); state = call.data[reasoning.STATE_KEY]; capture = state['capture']
        assert call.status == status and capture is not None and trace.posts == 1
        assert previous.STATE_KEY not in call.data
        assert call.data['role'] == 'business' and call.data['side_effect'] is False and call.data['job_id'] == s.job_id
        assert base64.b64decode(capture['raw_base64']) == trace.raw.encode('utf-8')
        assert capture['raw_sha256'] == hashlib.sha256(trace.raw.encode('utf-8')).hexdigest()
        assert capture['receipt'] == trace.capture_receipt
        assert reasoning._checked_state(checked, s.p, checked.get(Capability, s.cap_id),
            s.inputs, s.key, call) == state  # Production frozen checker, real SQL/audits.
        job = checked.get(Job, s.job_id)
        assert job.kind == graph_scope.KIND and job.status == 'running' and job.lease_token == claimed[1]
        assert job.data['actor_id'] == s.p.user_id and job.data['inputs'] == s.inputs
        assert db_count(checked, CloudCall, s.p.workspace_id) == 1
        assert db_count(checked, Invocation, s.p.workspace_id) == 1
        ledger = checked.get(CloudCall, state['call_id'])
        assert ledger.status == ledger_status and ledger.charged_tokens == 23
        actions = [reasoning.PREPARED, reasoning.RESERVED, reasoning.CAPTURED]
        events = list(checked.scalars(select(Audit).where(Audit.workspace_id == s.p.workspace_id,
            Audit.object_id == call.id, Audit.action.in_(actions))))
        assert sorted(event.action for event in events) == sorted(actions)
        assert all(event.actor_id == s.p.user_id for event in events)
        if outcome == 'succeeded':
            assert call.data['model_receipt']['call_id'] == ledger.id
            assert call.data['output']['model_value'] == model_answer()
            assert call.data['output']['mechanical_review']['missing_requirement_refs'] == ['need_temperature']
            assert call.data['output']['mechanical_review']['current_facts_sufficient'] is False
            assert call.data['output']['can_dispatch'] is False
        before_call = copy.deepcopy(call.data); before_ledger = copy.deepcopy(ledger.data)
        before_fee = ledger.charged_tokens; cap = checked.get(Capability, s.cap_id)
        if outcome == 'succeeded':
            assert reasoning.recover(checked, s.p, cap, s.inputs, s.key, invocation=call) == call.data['output']
        else:
            with pytest.raises((gateway.ModelBlocked, HTTPException)):
                reasoning.recover(checked, s.p, cap, s.inputs, s.key, invocation=call)
        # A legacy key alone cannot certify this SQL-loaded V284 record. Keep
        # this negative input local and roll it back rather than changing rows.
        with checked.no_autoflush:
            call.data = {key: value for key, value in call.data.items() if key != reasoning.STATE_KEY}
            call.data = {**call.data, previous.STATE_KEY: copy.deepcopy(state)}
            with pytest.raises(HTTPException):
                reasoning._checked_state(checked, s.p, cap, s.inputs, s.key, call)
        checked.rollback()
    with Session(engine(), expire_on_commit=False) as retry:
        leases.attach(retry, *claimed); retry.info['worker_actor'] = s.p
        try:
            repeated = gateway.invoke(retry, s.p, s.cap_id, 'business', s.inputs, s.key)
        except (gateway.ModelBlocked, HTTPException):
            assert outcome != 'succeeded'
        else:
            assert repeated.id == call_id
    with Session(engine()) as cold:
        actual = cold.get(Invocation, call_id); ledger = cold.get(CloudCall, actual.data[reasoning.STATE_KEY]['call_id'])
        assert actual.data == before_call and actual.status == status
        assert ledger.data == before_ledger and ledger.charged_tokens == before_fee and ledger.status == ledger_status
        assert db_count(cold, Invocation, s.p.workspace_id) == 1
        assert db_count(cold, CloudCall, s.p.workspace_id) == 1 and trace.posts == 1


def db_count(db, model, workspace):
    return db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
