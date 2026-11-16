"""Actual invoke/catch/save control flow with explicit SQL/IAM/lease IO spies.

These pure tests prove the diagnostic's scope, privacy and transaction staging,
not actual PG durability, worker custody, source authority or device execution.
The existing final commit/fence is modeled as an IO boundary; no new fence or
permission is manufactured by the diagnostic.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import gateway
from generic_agents.contracts import digest
from generic_agents.worker_failure_diagnostics_v289 import MAX_FRAMES, SCHEMA


SECRET = 'do-not-copy-detail-input-or-lease-v293'


class UnprintableFailure(ValueError):
    def __str__(self):
        raise AssertionError('The diagnostic must not format the actual exception')


class UnprintableDetail:
    def __str__(self):
        raise AssertionError('The diagnostic must not read HTTP detail')


def identity():
    return str(uuid4())


def local_manifest():
    return {'kind': 'tool', 'allowed_agent_roles': ['data'],
        'execution': {'adapter': 'workflow_service', 'service_actor': 'reader_service',
            'operation': 'create_goal_evaluation_packet_v288', 'side_effect': True},
        'specification': {'schema': 'workflow-goal-evaluation-packet-contract-v288'},
        'data_policy': {'cloud_allowed': False}, 'budget': {'max_concurrency': 1},
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}}


def scene(monkeypatch, failure, *, before_dispatch=False, local=True, reject_final=False):
    from generic_agents import business_tools, device_gateway_sources_v270, device_gateway_tools_v269
    from generic_agents import pixel_question_catalog_v252, multimodal_question_tool_v262
    from generic_agents import native_goal_queries, native_task_queries, workflow_local_transaction_capacity_v201
    from generic_agents import workflow_graph_scope, layered_approval_store_v175
    from generic_agents import workflow_goal_evaluation_packet_v288, workflow_service_context_v196
    p = NS(workspace_id=identity(), user_id=identity())
    cap = NS(id=identity(), workspace_id=p.workspace_id, content_hash='a' * 64, data=local_manifest())
    if not local:
        cap.data['execution']['operation'] = 'resense_from_context'
    inputs = {'private_input': SECRET}; key = 'graph:' + identity() + ':evaluation:1'
    approval = {'schema': 'workflow-service-approval-v195', 'task_id': identity()}
    owner = {'job_id': identity(), 'lease_token': SECRET}
    events = []; staged = []; durable = []; calls = []
    class IO:
        def __init__(self):
            self.row = None; self.previous = None; self.commits = 0; self.saved = None
        def scalar(self, statement):
            assert statement.column_descriptions[0]['entity'] is gateway.Invocation
            params = statement.compile().params
            assert p.workspace_id in params.values() and key in params.values()
            return self.previous
        def add(self, row):
            assert self.row is None
            self.row = row
        def flush(self):
            self.row.id = identity()
        def commit(self):
            self.commits += 1
            if self.commits == 2:
                events.append('original-final-fence')
                if reject_final:
                    # The existing transaction/fence is the explicit IO spy.
                    # Staged audits are not a committed independent record.
                    raise gateway.LeaseLost()
            self.saved = (self.row.status, copy.deepcopy(self.row.data))
            durable.extend(copy.deepcopy(staged)); staged.clear()
            events.append('commit:' + self.row.status)
    db = IO()
    monkeypatch.setattr(gateway, 'published', lambda *_args: cap)
    def scoped(actual, model, row_id, current, *args, **kwargs):
        from generic_agents.models import Capability
        assert actual is db and model is Capability and row_id == cap.id and current is p
        return cap
    monkeypatch.setattr(gateway, 'scoped', scoped)
    # This capability is neither a device Source/tool, pixel/sound tool nor a
    # native query. Their unrelated registration IO is an explicit boundary.
    monkeypatch.setattr(device_gateway_sources_v270, 'managed', lambda *_args: False)
    monkeypatch.setattr(device_gateway_tools_v269, 'applies', lambda *_args: False)
    monkeypatch.setattr(pixel_question_catalog_v252, 'applies', lambda *_args: False)
    monkeypatch.setattr(multimodal_question_tool_v262, 'applies', lambda *_args: False)
    monkeypatch.setattr(native_goal_queries, 'applies', lambda *_args: False)
    monkeypatch.setattr(native_task_queries, 'applies', lambda *_args: False)
    monkeypatch.setattr(workflow_local_transaction_capacity_v201, 'count_active', lambda *_args: 0)
    def approved(actual, current, selected, role, value, request_key, original):
        assert actual is db and current is p and selected is cap and role == 'data'
        assert value is inputs and request_key == key and original is approval
        events.append('original-approval-boundary')
    monkeypatch.setattr(business_tools, 'validate_call_approval', approved)
    monkeypatch.setattr(gateway, 'ownership', lambda actual: copy.deepcopy(owner))
    monkeypatch.setattr(gateway, 'checkpoint', lambda actual: events.append('original-checkpoint'))
    monkeypatch.setattr(workflow_graph_scope, 'owned_graph_job', lambda *_args: NS(id=owner['job_id']))
    monkeypatch.setattr(layered_approval_store_v175, 'before_workflow_dispatch', lambda *_args: False)
    def dispatch(actual, current, request_key, selected):
        assert actual is db and current is p and request_key == key and selected is cap
        events.append('original-graph-dispatch-boundary')
        if before_dispatch:
            raise failure
    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', dispatch)
    def execute(actual, current, selected, value, request_key, original):
        assert actual is db and current is p and selected is cap and value is inputs
        assert request_key == key and original is approval
        calls.append('actual-fixed-family-execute-boundary')
        if failure is not None:
            raise failure
        return {'ok': True}
    monkeypatch.setattr(workflow_goal_evaluation_packet_v288, 'execute', execute)
    monkeypatch.setattr(workflow_service_context_v196, 'execute', execute)
    def audit(actual, current, action, object_id, value):
        assert actual is db and current is p and object_id == db.row.id
        assert db.commits == 1
        staged.append((action, object_id, copy.deepcopy(value)))
    monkeypatch.setattr(gateway, 'audit', audit)
    return NS(db=db, p=p, cap=cap, inputs=inputs, key=key, approval=approval, owner=owner,
        events=events, staged=staged, durable=durable, calls=calls)


def invoke(s):
    return gateway.invoke(s.db, s.p, s.cap.id, 'data', s.inputs, s.key, approval=s.approval)


@pytest.mark.parametrize('kind', ['http_after_dispatch', 'http_before_dispatch', 'exception'])
def test_actual_failure_is_redacted_bound_and_saved_once_with_the_original_outcome(monkeypatch, kind):
    failure = UnprintableFailure(SECRET) if kind == 'exception' else HTTPException(409, UnprintableDetail())
    s = scene(monkeypatch, failure, before_dispatch=kind == 'http_before_dispatch')
    original = copy.deepcopy((s.cap.data, s.inputs, s.approval, s.owner))
    row = invoke(s)
    expected = 'blocked_evidence' if kind == 'http_before_dispatch' else 'unknown'
    assert row.status == expected and s.db.saved[0] == expected
    assert '本地目标评价' in row.data['error'] and '外部动作' not in row.data['error']
    assert '重试' not in row.data['error'] and s.db.commits == 2
    records = [value for action, _, value in s.durable if action == gateway._LOCAL_EVALUATION_FAILURE]
    assert len(records) == 1
    value = records[0]
    assert set(value) == {'schema', 'workspace_id', 'invocation_id', 'capability_id', 'capability_hash',
        'job_id', 'task_id', 'outcome_status', 'code', 'diagnostic', 'diagnostic_hash'}
    assert value['workspace_id'] == s.p.workspace_id and value['invocation_id'] == row.id
    assert value['capability_id'] == s.cap.id and value['capability_hash'] == row.data['capability_hash']
    assert value['job_id'] == s.owner['job_id'] and value['task_id'] == s.approval['task_id']
    assert value['outcome_status'] == expected and value['code'] == row.data['error_code']
    diagnostic = value['diagnostic']
    assert diagnostic['schema'] == SCHEMA and diagnostic['http_status'] == (None if kind == 'exception' else 409)
    assert diagnostic['error_type'] == ('UnprintableFailure' if kind == 'exception' else 'HTTPException')
    assert 0 < len(diagnostic['frames']) <= MAX_FRAMES
    assert all(set(frame) == {'module', 'function', 'line'} and frame['module'].endswith('.py')
        and frame['function'] in {'invoke', 'native_dispatch'} and type(frame['line']) is int
        for frame in diagnostic['frames'])
    assert any(frame['function'] == 'invoke' for frame in diagnostic['frames'])
    assert value['diagnostic_hash'] == digest(diagnostic)
    raw = json.dumps(value, ensure_ascii=False)
    assert all(private not in raw for private in (SECRET, 'detail', 'locals', 'lease_token', '/Users/',
        'private_input', 'Authorization', 'retry_allowed', 'resume_allowed', 'permissions_verified'))
    assert s.events[-2:] == ['original-final-fence', 'commit:' + expected]
    assert [action for action, _, _ in s.durable] == [gateway._LOCAL_EVALUATION_FAILURE, 'capability.invoke']
    assert (s.cap.data, s.inputs, s.approval, s.owner) == original
    # Historical early-return/replay behavior is preserved; no diagnostic is
    # backfilled and no second execution or final commit is created.
    s.db.previous = row; count = len(s.durable); commits = s.db.commits; calls = len(s.calls)
    assert invoke(s) is row
    assert len(s.durable) == count and s.db.commits == commits and len(s.calls) == calls


def test_diagnostic_and_outcome_cannot_escape_the_existing_final_commit_fence(monkeypatch):
    s = scene(monkeypatch, UnprintableFailure(SECRET), reject_final=True)
    with pytest.raises(gateway.LeaseLost): invoke(s)
    assert s.db.saved[0] == 'running' and s.db.commits == 2 and s.durable == []
    assert [action for action, _, _ in s.staged] == [gateway._LOCAL_EVALUATION_FAILURE, 'capability.invoke']


@pytest.mark.parametrize('failure', [None, gateway.LeaseLost()])
def test_success_and_original_lease_loss_do_not_produce_failure_audit(monkeypatch, failure):
    s = scene(monkeypatch, failure)
    if failure is not None:
        with pytest.raises(gateway.LeaseLost): invoke(s)
        assert s.db.saved[0] == 'running' and s.db.commits == 1 and s.durable == []
    else:
        assert invoke(s).status == 'succeeded'
        assert [action for action, _, _ in s.durable] == ['capability.invoke']


def test_unrelated_resense_keeps_original_unknown_status_and_wording(monkeypatch):
    s = scene(monkeypatch, HTTPException(409, {'private': SECRET}), local=False)
    row = invoke(s)
    assert row.status == 'unknown' and row.data['error'] == '外部动作已发送但结果不满足契约，禁止重放，请核对服务回执'
    assert 'error_code' not in row.data and [action for action, _, _ in s.durable] == ['capability.invoke']


@pytest.mark.parametrize('change', ['role', 'kind', 'allowed_roles', 'operation', 'actor', 'adapter',
    'side_effect_integer', 'execution_extra', 'contract', 'cloud_allowed'])
def test_nearby_or_marker_like_manifests_do_not_opt_in(monkeypatch, change):
    from generic_agents import worker_failure_diagnostics_v289 as capture
    cap = NS(data=local_manifest()); role = 'data'
    if change == 'role': role = 'business'
    elif change == 'kind': cap.data['kind'] = 'source'
    elif change == 'allowed_roles': cap.data['allowed_agent_roles'] = ['data', 'business']
    elif change == 'operation': cap.data['execution']['operation'] = 'plan_approved_goal_data_v288'
    elif change == 'actor': cap.data['execution']['service_actor'] = 'execution_service'
    elif change == 'adapter': cap.data['execution']['adapter'] = 'business_goal_ready_v288'
    elif change == 'side_effect_integer': cap.data['execution']['side_effect'] = 1
    elif change == 'execution_extra': cap.data['execution']['callback'] = 'untrusted'
    elif change == 'contract': cap.data['specification']['schema'] = 'old-local-contract'
    else: cap.data['data_policy']['cloud_allowed'] = True
    monkeypatch.setattr(capture, 'capture_failure', lambda *_args: pytest.fail('Other family entered new diagnostic capture'))
    assert gateway._local_goal_evaluation_diagnostic(cap, role, UnprintableFailure(SECRET)) is None
