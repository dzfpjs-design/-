"""Fixed registry/Gateway/capture paths, with explicit authority/DB/HTTP IO.

Actual family routing, callback order, saved capture and transport preparation
run. Fake IO here proves neither current permission, actual lease/money, PG
durability nor provider sending; Root separately validates the real worker.
"""
import copy
import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import business_reasoning_protocols_v276 as registry
from generic_agents import business_reasoning_capture_v275 as capture
from generic_agents import business_reasoning_v197 as legacy
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import gateway
from generic_agents.contracts import digest
from tests.test_gateway_local_goal_evaluation_diagnostics_v293 import scene as gateway_scene
from tests.test_same_goal_advice_money_v305 import scene as money_scene
from tests.test_same_goal_advice_runtime_v308 import scene as runtime_scene, _opinion
from tests.test_same_goal_advice_contracts_v302 import ref


def test_registry_adds_only_actual_fixed_plan_protocol_and_never_a_graph_activation():
    assert registry.ADVICE == runtime.ADAPTER == 'same_goal_advice_v302'
    assert registry.family(registry.ADVICE) is runtime
    assert registry.for_adapter(registry.ADVICE) is runtime and registry.require_protocol(runtime) is runtime
    assert registry.retained_state(registry.ADVICE, NS(data={runtime.STATE_KEY: {'retained': True}})) == {'retained': True}
    assert not registry.original_capture(registry.ADVICE, object(), object(), object(), {})
    with pytest.raises(ValueError): registry.recovery_record(registry.ADVICE)
    for fake in (NS(__name__=runtime.__name__, ADAPTER=runtime.ADAPTER), runtime.__name__, 'same_goal_advice_v308'):
        with pytest.raises(ValueError): registry.require_protocol(fake)


def test_real_capture_uses_original_snapshot_ids_limit_and_task_order_and_retains_raw_after_revocation(runtime_scene, monkeypatch):
    from generic_agents import db as dbmodule
    t = runtime_scene; order = []; current = runtime._current; original_call = runtime._call
    def current_io(*args): order.append('Task_Plan_Job_owner_IO'); return current(*args)
    def call_io(*args, **kwargs):
        if kwargs.get('lock'): order.append('SDK_lock_IO')
        return original_call(*args, **kwargs)
    monkeypatch.setattr(runtime, '_current', current_io)
    monkeypatch.setattr(runtime, '_call', call_io)
    monkeypatch.setattr(runtime, 'evidence_ids', lambda *args: pytest.fail('closed InvocationInput contains no chosen IDs'))
    monkeypatch.setattr(runtime, 'qualify_evidence', lambda *args: order.append('actual_live_source_IO'))
    def files(db, p, ids, model):
        assert ids == t.frame['evidence_ids'] and model.id == t.model.id
        order.append('actual_current_consent_IO')
        return copy.deepcopy(t.execution['snapshot']['preview']['evidence_pins']), copy.deepcopy(t.parts)
    monkeypatch.setattr(runtime, '_evidence', files)
    monkeypatch.setattr(runtime, 'audit', lambda db, p, action, identity, body: t.event(action, body, object_id=identity))
    monkeypatch.setattr(runtime, 'scoped', lambda db, cls, identity, p: next(row for row in t.rows[cls] if row.id == identity))
    @contextmanager
    def session(bind): yield t.db
    monkeypatch.setattr(runtime, 'Session', session)
    monkeypatch.setattr(dbmodule, 'engine', lambda: 'explicit_capture_session_IO')
    transmitted = []; original_raw = []
    def cloud(model, messages, schema, p, **kwargs):
        transmitted.append(copy.deepcopy({key: value for key, value in kwargs.items() if not callable(value)}))
        frozen = t.call.data[runtime.STATE_KEY]['request']
        assert kwargs['output_token_limit'] == 1024 and kwargs['evidence_stage'] == transport.STAGE
        assert kwargs['dispatch_identity'] == {'suggestion_id': t.call.id, 'job_id': t.send_job.id,
            'task_id': t.task.id, 'request_hash': frozen['request_hash']}
        ledger = runtime.CloudCall(id=ref('capture_real_reservation')['id'], workspace_id=t.p.workspace_id,
            capability_id=t.model.id, status='reserved', reserved_tokens=frozen['prepared']['token_bound'],
            data={'actor_id': t.p.user_id, 'request_hash': frozen['prepared']['request_hash'],
                'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'],
                'gateway_dispatch': {'identity': {**kwargs['dispatch_identity'], 'workspace_id': t.p.workspace_id,
                    'actor_id': t.p.user_id, 'capability_id': t.model.id}}})
        t.rows[runtime.CloudCall].append(ledger)
        kwargs['on_reserved'](ledger.id)
        opinion = _opinion(frozen['execution_snapshot']['preview']['cloud_request']['decision_input'])
        raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(opinion)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}})
        original_raw.append(raw)
        receipt = {'provider': 'deepseek', 'model': t.model.data['execution']['model'], 'http_status': 200,
            'call_id': ledger.id, 'request_id': 'actual_first_http_IO', 'usage': {}}
        # Paid response custody is deliberately historical at this point.
        t.allowed = False; t.allow_reader = False
        kwargs['on_response'](raw, receipt)
        return transport.parse_response(raw, schema, receipt)
    monkeypatch.setattr(gateway, 'cloud_json', cloud)
    result = capture.execute(t.db, t.p, t.advice, t.inputs, t.key, protocol=runtime)
    assert order.index('Task_Plan_Job_owner_IO') < order.index('SDK_lock_IO')
    assert len(transmitted) == 1 and result['review_state'] == 'ready_for_review'
    state = t.state(); actual = runtime.base64.b64decode(state['capture']['raw_base64']).decode('utf-8')
    assert actual == original_raw[0] and t.call.data['model_receipt']['usage']['total_tokens'] == 30
    assert t.commits == 4  # PREPARED, RESERVED, first raw, parsed receipt.
    assert [row.action for row in t.rows[runtime.Audit] if row.action in {runtime.PREPARED, runtime.RESERVED, runtime.CAPTURED}] == [
        runtime.PREPARED, runtime.RESERVED, runtime.CAPTURED]


def test_legacy_capture_keeps_original_gateway_call_signature_without_new_limit_or_plan_owner(monkeypatch):
    p = NS(workspace_id='explicit_workspace_IO', user_id='explicit_actor_IO')
    cap = NS(id='original_cap'); model = NS(id='original_model')
    frozen = {'messages': [], 'model_value_schema': {'type': 'object'}, 'request_hash': 'original_request',
        'pin': {'input_hash': 'actual_input_IO', 'capability_hash': 'actual_cap_IO', 'task_id': 'original_task', 'job_id': 'original_job'}}
    call = NS(id='original_sdk', status='running', capability_id=cap.id,
        data={'input_hash': 'actual_input_IO', 'capability_hash': 'actual_cap_IO'})
    seen = []
    class DB:
        def scalars(self, statement): return []
        def commit(self): seen.append('original_commit_IO')
    monkeypatch.setattr(legacy, '_call', lambda *args, **kwargs: call)
    monkeypatch.setattr(legacy, '_current', lambda *args: pytest.fail('legacy ownership entry is unchanged'))
    monkeypatch.setattr(legacy, 'prepare_request', lambda *args: (model, frozen))
    monkeypatch.setattr(legacy, '_anchor', lambda value: {'original_anchor': True})
    monkeypatch.setattr(legacy, 'audit', lambda *args: seen.append('original_audit_IO'))
    monkeypatch.setattr(legacy, 'check_result', lambda value, output: output)
    monkeypatch.setattr(legacy, '_checked_state', lambda *args: {})
    def cloud(model, messages, schema, actor, **kwargs):
        assert 'output_token_limit' not in kwargs
        seen.append('original_cloud_IO'); return {'original': True}, {'original_receipt': True}
    monkeypatch.setattr(gateway, 'cloud_json', cloud)
    assert capture.execute(DB(), p, cap, {}, 'original_key', protocol=legacy) == {'original': True}
    assert seen == ['original_audit_IO', 'original_commit_IO', 'original_cloud_IO', 'original_commit_IO']


def _gateway(monkeypatch, *, reject=False):
    from generic_agents import same_goal_advice_live_v308 as live
    from generic_agents import workflow_graph_scope, layered_approval_store_v175
    t = gateway_scene(monkeypatch, None); t.db.no_autoflush = nullcontext()
    t.cap.data = {'kind': 'tool', 'allowed_agent_roles': ['business'], 'execution': {'adapter': runtime.ADAPTER, 'side_effect': False},
        'specification': {}, 'data_policy': {'cloud_allowed': True}, 'budget': {'max_concurrency': 1},
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}}
    t.cap.content_hash = digest(t.cap.data)
    t.approval = {'schema': runtime.c.APPROVAL, 'plan_ref': ref('actual_plan_IO'), 'preview_hash': 'a' * 64, 'job_id': t.owner['job_id']}
    t.authorizations = []
    def authorize(db, p, cap, role, inputs, key, approval):
        assert db is t.db and p is t.p and cap is t.cap and role == 'business'
        assert inputs is t.inputs and key == t.key and approval is t.approval
        t.authorizations.append(None if db.row is None else db.row.status)
        if reject: raise HTTPException(409, 'actual fixed worker ownership IO refused')
    monkeypatch.setattr(live, 'authorize_gateway', authorize)
    monkeypatch.setattr(workflow_graph_scope, 'owned_graph_job', lambda *args: None)
    monkeypatch.setattr(layered_approval_store_v175, 'before_workflow_dispatch',
        lambda *args: pytest.fail('Plan-only advice cannot impersonate graph activation'))
    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', lambda *args: pytest.fail('no graph authority'))
    def execute(db, p, cap, inputs, key):
        assert db is t.db and db.row.status == 'running' and db.commits == 1
        # Independent capture Session replaced the durable payload, while
        # Gateway still retains the original pre-HTTP ORM object.
        actual = runtime.Invocation(id=db.row.id, workspace_id=p.workspace_id, capability_id=cap.id,
            idempotency_key=key, status='running', data={**copy.deepcopy(db.row.data),
                runtime.STATE_KEY: {'actual_durable_capture_IO': True}, 'model_receipt': {'actual_original_receipt_IO': True}})
        db.previous = actual; db.row = actual
        return {'proposed_for_human_review': True}
    monkeypatch.setattr(runtime, 'execute', execute)
    return t


def test_ordinary_gateway_requires_actual_fixed_authority_before_sdk_and_dispatch_and_merges_original_capture(monkeypatch):
    t = _gateway(monkeypatch)
    call = gateway.invoke(t.db, t.p, t.cap.id, 'business', t.inputs, t.key, approval=t.approval)
    assert t.authorizations == [None, None, 'running']
    assert call is t.db.row and call.status == 'succeeded'
    assert call.data[runtime.STATE_KEY] == {'actual_durable_capture_IO': True}
    assert call.data['model_receipt'] == {'actual_original_receipt_IO': True}
    assert call.data['output'] == {'proposed_for_human_review': True}
    assert t.db.commits == 2 and [action for action, _, _ in t.durable] == ['capability.invoke']


def test_ordinary_gateway_refusal_precedes_any_sdk_or_legacy_approval_lookup(monkeypatch):
    t = _gateway(monkeypatch, reject=True)
    with pytest.raises(HTTPException): gateway.invoke(t.db, t.p, t.cap.id, 'business', t.inputs, t.key, approval=t.approval)
    assert t.db.row is None and t.db.commits == 0 and t.authorizations == [None]


@pytest.mark.parametrize('explicit', [False, True])
def test_cloud_wrapper_none_omits_new_keyword_but_explicit_limit_reaches_fixed_internal_transport(monkeypatch, explicit):
    seen = []
    def cloud(*args, **kwargs): seen.append(kwargs); return 'original_single_call_IO'
    monkeypatch.setattr(gateway, '_cloud_json', cloud)
    value = gateway.cloud_json(NS(), [], {}, evidence_stage=transport.STAGE, output_token_limit=1024 if explicit else None)
    assert value == 'original_single_call_IO' and len(seen) == 1
    assert (seen[0]['output_token_limit'] == 1024) if explicit else ('output_token_limit' not in seen[0])


@pytest.mark.parametrize('limit', [None, 1024])
def test_actual_gateway_fixed_stage_passes_limit_to_real_prepare_without_second_transport(money_scene, monkeypatch, limit):
    t = money_scene; model = NS(data=t.model); seen = []
    monkeypatch.setattr(gateway, 'settings', lambda: NS(dashscope_api_key=None, deepseek_api_key=None))
    monkeypatch.setattr(gateway.os, 'getenv', lambda name: 'explicit-fake-credential-never-sent' if name == 'DEEPSEEK_API_KEY' else None)
    def call(cap, messages, schema, principal, **kwargs):
        prepared = (transport.prepare(cap, messages, schema) if 'output_token_limit' not in kwargs else
            transport.prepare(cap, messages, schema, output_token_limit=kwargs['output_token_limit']))
        seen.append((kwargs, prepared)); return {'advice': 'actual pure preparation'}, {'no_HTTP_IO': True}
    monkeypatch.setattr(transport, 'call', call)
    assert gateway._cloud_json(model, t.messages, t.schema, evidence_stage=transport.STAGE,
        output_token_limit=limit) == ({'advice': 'actual pure preparation'}, {'no_HTTP_IO': True})
    assert len(seen) == 1 and seen[0][1]['options']['max_tokens'] == (4096 if limit is None else 1024)
    assert ('output_token_limit' not in seen[0][0]) if limit is None else seen[0][0]['output_token_limit'] == limit
    assert t.model['execution']['max_tokens'] == 4096


@pytest.mark.parametrize('stage', [None, 'map-v4', 'catalogue-v211', 'catalogue-allocation-v213'])
def test_explicit_limit_cannot_silently_change_an_unrelated_stage_or_send(monkeypatch, stage):
    monkeypatch.setattr(gateway, 'settings', lambda: pytest.fail('no credential access for wrong stage'))
    with pytest.raises(gateway.ModelBlocked): gateway._cloud_json(NS(data={}), [], {},
        evidence_stage=stage, output_token_limit=1024)
