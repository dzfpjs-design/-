"""Real PostgreSQL two-Session first-answer retention regressions.

This is a storage/terminal-merge test, not a paid model, graph-approval or
Evidence-consent acceptance test. Graph/Evidence declaration boundaries and
external HTTP are spies; Sessions, canonical payload writes, original callbacks,
Invocation/CloudCall rows, three audits and lease fencing use the real test DB.
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

from generic_agents import business_reasoning_v197 as reasoning, gateway, worker, leases
from generic_agents import cloud_business_reasoning_v197 as transport
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
        p = Principal(actor.id, workspace, 'admin', 'capture-sql-regression')
        model_data = Manifest(kind='foundation_model', namespace='capture_sql_v271', name='model',
            version='1.0.0', owner=p.user_id, description='Synthetic HTTP boundary for SQL retention',
            allowed_agent_roles=['business'], execution={'adapter': 'cloud', 'provider': 'qwen',
                'model': 'qwen-plus', 'max_tokens': 128}, data_policy={'cloud_allowed': True}).model_dump(mode='json')
        model = cap_row(workspace, model_data); db.add(model); db.flush()
        cap_data = Manifest(kind='tool', namespace='capture_sql_v271', name='reasoner', version='1.0.0',
            owner=p.user_id, description='SQL durability regression', allowed_agent_roles=['business'],
            input_schema=reasoning.input_schema(), output_schema=reasoning.output_schema(),
            execution={'adapter': reasoning.ADAPTER, 'model_profile_id': model.id,
                'model_profile_hash': model.content_hash, 'side_effect': False},
            data_policy={'cloud_allowed': True}, dependencies=[model.id],
            specification={'schema': reasoning.CONTRACT, 'instruction': 'Compare synthetic fixture evidence'},
            budget={'max_attempts': 1}).model_dump(mode='json')
        cap = cap_row(workspace, cap_data); db.add(cap); db.flush()
        package = cap_row(workspace, Manifest(kind='domain_package', namespace='capture_sql_v271',
            name='package', version='1.0.0', owner=p.user_id, description='Persistence fixture package').model_dump(mode='json'))
        db.add(package); db.flush()
        task = Task(workspace_id=workspace, owner_id=p.user_id, package_id=package.id,
            title='SQL first-response retention', data={})
        db.add(task); db.flush()
        inputs = {'question': 'Compare the synthetic proposal', 'evidence_ids': [str(uuid4())],
            'candidates': [{'candidate_ref': 'candidate_1', 'summary': 'An unverified proposal', 'context': None}]}
        key = 'capture-sql:' + str(uuid4())
        job = Job(workspace_id=workspace, kind=graph_scope.KIND, idempotency_key=key,
            data={'actor_id': p.user_id, 'task_id': task.id, 'activation_id': 'analysis',
                'invocation_key': key, 'input_hash': digest(inputs), 'inputs': copy.deepcopy(inputs)})
        db.add(job); db.commit()
        return SimpleNamespace(p=p, cap_id=cap.id, model_id=model.id, task_id=task.id,
            job_id=job.id, inputs=inputs, key=key)


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
        # Make the stale ORM object genuinely dirty. An ordinary SELECT would
        # autoflush it and erase the independent commit before reading it.
        stale.data = {**stale.data, 'uncommitted_old_payload': True}
        capture = {'raw_base64': base64.b64encode(b'original paid reply').decode(),
            'raw_sha256': hashlib.sha256(b'original paid reply').hexdigest(), 'receipt': {'call_id': str(uuid4())}}
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


def install_declaration_and_http_spies(monkeypatch, s, outcome):
    messages = [{'role': 'user', 'content': 'Synthetic evidence only'}]
    options = {'model': 'qwen-plus', 'max_tokens': 128}
    evidence_pins = [{'synthetic_evidence_id': s.inputs['evidence_ids'][0]}]
    state = SimpleNamespace(posts=0, raw=None, capture_receipt=None)
    def frozen_request(db, p, cap, inputs, key):
        model = db.get(Capability, s.model_id)
        prepared = {'messages': copy.deepcopy(messages), 'options': copy.deepcopy(options),
            'request_hash': digest(messages), 'dispatch_options_hash': digest(options), 'token_bound': 4096}
        frozen = {'schema': reasoning.STATE + '/request', 'inputs': copy.deepcopy(inputs),
            'messages': copy.deepcopy(messages), 'model_value_schema': reasoning.model_value_schema(
                inputs['evidence_ids'], [row['candidate_ref'] for row in inputs['candidates']]),
            'prepared': prepared, 'evidence_pins': copy.deepcopy(evidence_pins),
            'capability_snapshot': copy.deepcopy(cap.data), 'model_snapshot': copy.deepcopy(model.data),
            'pin': {'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'task_id': s.task_id,
                'job_id': s.job_id, 'activation_id': 'analysis', 'node_id': 'analysis', 'scope_hash': 'a' * 64,
                'capability_id': cap.id, 'capability_hash': cap.content_hash, 'model_profile_id': model.id,
                'model_profile_hash': model.content_hash, 'request_key_hash': digest(key), 'input_hash': digest(inputs)}}
        frozen['request_hash'] = digest(frozen); state.frozen = copy.deepcopy(frozen)
        return model, frozen
    def current(db, p, cap, inputs, key):
        return cap, db.get(Capability, s.model_id), db.get(Task, s.task_id), db.get(Job, s.job_id), {}, {}
    monkeypatch.setattr(reasoning, 'prepare_request', frozen_request)
    monkeypatch.setattr(reasoning, '_current', current)
    monkeypatch.setattr(reasoning, '_evidence', lambda *args: (copy.deepcopy(evidence_pins), []))
    monkeypatch.setattr(packets, 'qualify_consumer', lambda *args: None)
    monkeypatch.setattr(graph_scope, 'owned_graph_job', lambda *args: None)
    monkeypatch.setattr(approvals, 'before_workflow_dispatch', lambda *args: False)
    if outcome == 'semantic_rejected':
        real_check = reasoning.check_result
        def semantic_rejection(frozen, value):
            real_check(frozen, value)
            raise HTTPException(409, 'Forced post-parse semantic rejection for storage regression')
        monkeypatch.setattr(reasoning, 'check_result', semantic_rejection)
    def http_boundary(model, sent_messages, schema, principal, **kwargs):
        assert principal is s.p and sent_messages == messages
        state.posts += 1
        with Session(engine()) as ledger_db:
            ledger = CloudCall(workspace_id=s.p.workspace_id, capability_id=s.model_id,
                reserved_tokens=4096, charged_tokens=4096, status='reserved',
                data={'actor_id': s.p.user_id, 'request_hash': state.frozen['prepared']['request_hash'],
                    'dispatch_options_hash': state.frozen['prepared']['dispatch_options_hash'],
                    'gateway_dispatch': {'identity': {**kwargs['dispatch_identity'],
                        'workspace_id': s.p.workspace_id, 'actor_id': s.p.user_id, 'capability_id': s.model_id}}})
            ledger_db.add(ledger); ledger_db.flush(); call_id = ledger.id; ledger_db.commit()
        kwargs['on_reserved'](call_id)
        answer = {'summary': 'A synthetic comparison', 'candidate_assessments': [{
            'candidate_ref': 'candidate_1', 'assessment': 'insufficient', 'reason': 'More evidence needed',
            'evidence_refs': [], 'missing_information': ['Independent confirmation']}],
            'recommendation': {'candidate_ref': None, 'reason': 'No supported choice', 'evidence_refs': []}}
        raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
            {} if outcome == 'schema_rejected' else answer)}}],
            'usage': {'prompt_tokens': 13, 'completion_tokens': 10, 'total_tokens': 23}})
        receipt = {'provider': 'qwen', 'model': 'qwen-plus', 'call_id': call_id, 'request_id': 'synthetic-sql-reply',
            'http_status': 200, 'usage': {}, 'usage_unavailable': True,
            'usage_unavailable_reason': 'unparsed_original_http', 'stage': transport.STAGE}
        state.raw = raw; state.capture_receipt = copy.deepcopy(receipt)
        kwargs['on_response'](raw, receipt)  # Actual independent SQL capture callback.
        receipt = transport.response_receipt(raw, receipt)
        try:
            result, receipt = transport.parse_response(raw, schema, receipt)
        except gateway.ModelBlocked:
            terminal = 'failed'; result = None
        else:
            terminal = 'succeeded'
        with Session(engine()) as ledger_db:
            ledger = ledger_db.get(CloudCall, call_id)
            ledger.status = terminal; ledger.charged_tokens = 23
            ledger.data = {**ledger.data, 'receipt': copy.deepcopy(receipt)}
            ledger_db.commit()
        if terminal == 'failed':
            raise gateway.ModelBlocked('Synthetic original response failed structural validation')
        return result, receipt
    monkeypatch.setattr(gateway, 'cloud_json', http_boundary)
    return state


@pytest.mark.parametrize('outcome,status,ledger_status', [
    ('schema_rejected', 'blocked_model', 'failed'),
    ('semantic_rejected', 'blocked_evidence', 'succeeded'),
    ('succeeded', 'succeeded', 'succeeded')])
def test_real_callbacks_and_gateway_terminal_preserve_the_same_first_answer(sql_scene, monkeypatch, outcome, status, ledger_status):
    s = sql_scene; trace = install_declaration_and_http_spies(monkeypatch, s, outcome)
    claimed = worker.claim(job_id=s.job_id, kinds=[graph_scope.KIND], workspace_id=s.p.workspace_id)
    assert claimed is not None and claimed[0] == s.job_id
    with Session(engine(), expire_on_commit=False) as outer:
        leases.attach(outer, *claimed); outer.info['worker_actor'] = s.p
        call = gateway.invoke(outer, s.p, s.cap_id, 'business', s.inputs, s.key)
        call_id = call.id
    with Session(engine()) as checked:
        call = checked.get(Invocation, call_id); capture = call.data[reasoning.STATE_KEY]['capture']
        assert call.status == status and capture is not None and trace.posts == 1
        assert base64.b64decode(capture['raw_base64']) == trace.raw.encode('utf-8')
        assert capture['raw_sha256'] == hashlib.sha256(trace.raw.encode('utf-8')).hexdigest()
        assert capture['receipt'] == trace.capture_receipt
        assert db_count(checked, CloudCall, s.p.workspace_id) == 1
        ledger = checked.get(CloudCall, call.data[reasoning.STATE_KEY]['call_id'])
        assert ledger.status == ledger_status and ledger.charged_tokens == 23
        assert sorted(checked.scalars(select(Audit.action).where(Audit.workspace_id == s.p.workspace_id,
            Audit.object_id == call.id, Audit.action.in_([reasoning.PREPARED, reasoning.RESERVED, reasoning.CAPTURED])))) == sorted(
                [reasoning.PREPARED, reasoning.RESERVED, reasoning.CAPTURED])
        if outcome == 'succeeded':
            assert call.data['model_receipt']['call_id'] == ledger.id
        before_call = copy.deepcopy(call.data); before_ledger = copy.deepcopy(ledger.data)
        before_fee = ledger.charged_tokens; cap = checked.get(Capability, s.cap_id)
        if outcome == 'succeeded':
            assert reasoning.recover(checked, s.p, cap, s.inputs, s.key, invocation=call) == call.data['output']
        else:
            with pytest.raises((gateway.ModelBlocked, HTTPException)):
                reasoning.recover(checked, s.p, cap, s.inputs, s.key, invocation=call)
    with Session(engine()) as cold:
        actual = cold.get(Invocation, call_id); ledger = cold.get(CloudCall, actual.data[reasoning.STATE_KEY]['call_id'])
        assert actual.data == before_call and actual.status == status
        assert ledger.data == before_ledger and ledger.charged_tokens == before_fee and ledger.status == ledger_status
        assert db_count(cold, CloudCall, s.p.workspace_id) == 1 and trace.posts == 1


def db_count(db, model, workspace):
    return db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
