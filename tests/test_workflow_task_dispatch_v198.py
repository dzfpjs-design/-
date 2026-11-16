"""Pure state/receipt tests with explicit SQL-fetch and live-authority IO seams.

The actual V198 inventory, parsing, hashes, transitions and original results run.
These are not registry, real V175 approval, PostgreSQL or human UX evidence.
Root separately exercises the installed capability and persistent API.
"""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import models as m, relations
from generic_agents import workflow_task_dispatch_v198 as service
from generic_agents import layered_approval_store_v175 as approval_store
from generic_agents.contracts import Manifest, digest
from generic_agents.goals import snapshot as goal_snapshot
from generic_agents.security import Principal


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def identity(label): return str(uuid5(NAMESPACE_URL, 'workflow-work-orders-v198/' + label))
def raw(body): return json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


class IO:
    """Explicit row-fetch substitute, never pretending to enforce PG constraints."""
    def __init__(self):
        self.rows = {}; self.added = []; self.locks = []; self.flushes = 0; self.failure = None

    def put(self, row):
        if row.id is None: row.id = str(uuid4())
        if hasattr(row, 'version') and row.version is None: row.version = 1
        self.rows.setdefault(type(row), {})[row.id] = row
        return row

    def scalar(self, statement):
        if self.failure is not None: raise self.failure
        entity = statement.column_descriptions[0].get('entity')
        if entity is None:
            assert 'clock_timestamp' in str(statement)
            return AT
        params = statement.compile().params
        values = list(self.rows.get(entity, {}).values())
        for name, value in params.items():
            key = name.rsplit('_', 1)[0]
            if key in {'id', 'workspace_id', 'user_id', 'idempotency_key'}:
                values = [row for row in values if not hasattr(row, key) or getattr(row, key) == value]
        if entity is m.User:
            values = [row for row in values if row.active is True]
            if 'JOIN' in str(statement):
                values = [row for row in values if any(member.user_id == row.id
                    and member.workspace_id == params['workspace_id_1'] and member.role in {'admin', 'operator'}
                    for member in self.rows.get(m.Membership, {}).values())]
        assert len(values) <= 1, 'The IO seam must not choose the first of multiple rows'
        return values[0] if values else None

    def execute(self, statement, params):
        if self.failure is not None: raise self.failure
        assert 'pg_advisory_xact_lock' in str(statement)
        self.locks.append((str(statement), copy.deepcopy(params)))

    def add(self, row):
        assert type(row) in {service.Batch, service.Item, service.Command, m.Audit}, \
            'Human work orders cannot add executable Tasks, Jobs, Observations, Entity state or device requests'
        self.added.append(type(row)); self.put(row)

    def flush(self): self.flushes += 1


@pytest.fixture
def scene(monkeypatch):
    s = NS(db=IO(), authority_calls=[], authority_error=None)
    s.workspace_id = identity('workspace')
    s.owner = Principal(identity('owner'), s.workspace_id, 'admin', 'synthetic-owner')
    s.assignee = Principal(identity('assignee'), s.workspace_id, 'operator', 'synthetic-assignee')
    s.other = Principal(identity('other'), s.workspace_id, 'operator', 'synthetic-other')
    for principal, name in ((s.owner, '原派发人'), (s.assignee, '实际负责人'), (s.other, '另一在岗成员')):
        s.db.put(m.User(id=principal.user_id, username=name, password_hash='not-an-authentication-proof', active=True))
        s.db.put(m.Membership(id=identity('membership-' + principal.user_id), workspace_id=s.workspace_id,
            user_id=principal.user_id, role=principal.role))
    s.inputs = {'scope': service.SCOPE, 'items': [
        {'title': '现场情况核查', 'description': '按原任务限定对象记录实际情况', 'expected_result': '提交情况说明，仍待独立验收',
            'assignee_id': s.assignee.user_id, 'due_at': (AT + timedelta(days=1)).isoformat()},
        {'title': '后续处置建议', 'description': '整理工作建议，不直接改变实体状态', 'expected_result': '提交建议及待核问题',
            'assignee_id': s.assignee.user_id, 'due_at': (AT + timedelta(days=1, hours=1)).isoformat()}]}
    manifest = Manifest(kind='tool', namespace='workflow_services', name='dispatch_tasks', version='1.0.0',
        owner='合成边界夹具', description='只验证工作单状态逻辑，不声称真实批准', allowed_agent_roles=['business'],
        input_schema=service.input_schema(), output_schema=service.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'task_center', 'operation': service.OP, 'side_effect': True},
        budget={'max_attempts': 2}).model_dump(mode='json')
    s.cap = m.Capability(id=identity('capability'), workspace_id=s.workspace_id, namespace=manifest['namespace'],
        name=manifest['name'], kind='tool', release_version='1.0.0', status='published', content_hash=digest(manifest),
        validated_hash=digest(manifest), data=manifest)
    s.package_id, s.instance_id, s.entity_id = identity('package'), identity('instance'), identity('entity')
    s.release = m.InstanceRelease(id=identity('release'), workspace_id=s.workspace_id, instance_id=s.instance_id,
        release_number=1, configuration_hash='c' * 64, actor_id=s.owner.user_id, package_id=s.package_id,
        status='active', data={'snapshot': {}})
    spec = {'entity_ids': [s.entity_id], 'required_fields': ['temperature_c'], 'deadline': None, 'max_tool_calls': 20}
    s.task = m.Task(id=identity('parent-task'), workspace_id=s.workspace_id, package_id=s.package_id,
        owner_id=s.owner.user_id, title='合成原图任务', status='workflow_running', data={
            'instance_id': s.instance_id, 'instance_release_id': s.release.id, 'business_id': 'source-business',
            'entity_ids': [s.entity_id], 'goal_spec': spec, 'goal_hash': digest(spec), 'goal_version': 1,
            'goal_confirmed_by': s.owner.user_id, 'goal_window': {'mode': 'latest'}})
    context = {'instance_id': s.instance_id, 'release_id': s.release.id, 'business_id': 'source-business',
        'entity_ids': [s.entity_id], 'goal_snapshot': goal_snapshot(s.task)}
    s.task.data = {**s.task.data, 'native_goal_context': context, 'native_goal_context_hash': digest(context)}
    s.key, s.token = 'graph:synthetic-original-dispatch', identity('original-lease')
    s.job = m.Job(id=identity('original-job'), workspace_id=s.workspace_id, kind='execute_full_semantic_graph_node',
        idempotency_key=s.key, status='running', attempts=1, lease_token=s.token,
        lease_until=AT + timedelta(minutes=3), available_at=AT, data={
            'task_id': s.task.id, 'actor_id': s.owner.user_id, 'activation_id': 'a1', 'graph_hash': 'd' * 64,
            'cancel_epoch': 0, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs), 'invocation_key': s.key})
    s.node = {'execution': {'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash}}
    s.approval = service.make_approval(s.task, s.job, s.node, s.inputs)
    s.transport = {'dispatch_ownership': {'job_id': s.job.id, 'lease_token': s.token},
        'dispatch_authorization': {'job_id': s.job.id, 'lease_token': s.token, 'input_hash': digest(s.inputs),
            'capability_hash': s.cap.content_hash, 'actor_id': s.owner.user_id, 'epoch': 0}}
    s.call = m.Invocation(id=identity('original-invocation'), workspace_id=s.workspace_id, capability_id=s.cap.id,
        idempotency_key=s.key, status='running', data={'job_id': s.job.id, 'lease_token': s.token,
            'role': 'business', 'capability_hash': s.cap.content_hash, 'input_hash': digest(s.inputs),
            'inputs': copy.deepcopy(s.inputs), 'approval': copy.deepcopy(s.approval), 'side_effect': True})
    s.binding = {'binding_hash': 'b' * 64, 'qualification_id': identity('qualification'),
        'qualification_hash': 'e' * 64, 'simulation_only': True, 'source': {'synthetic_checked_source': True}}
    s.current = {'task': s.task, 'job': s.job, 'release': s.release, 'binding': s.binding,
        'transport': s.transport, 'capability': s.cap,
        'delegation': {'agent_role': 'business', 'service_actor': 'task_center', 'operation': service.OP}}
    for row in (s.cap, s.release, s.task, s.job, s.call): s.db.put(row)

    def live_authority(db, p, cap, role, inputs, key, approval, *, _variant):
        assert db is s.db and p == s.owner and cap.id == s.cap.id and role == 'business'
        assert key == s.key and service._same(inputs, s.job.data['inputs'])
        assert service._same(approval, s.approval) and _variant is service
        service.validate_manifest(cap.data)
        s.authority_calls.append('explicit-live-authority-IO')
        if s.authority_error is not None: raise s.authority_error
        return s.current

    def fetch_rows(db, model, p, maximum, *conditions):
        assert db is s.db
        if db.failure is not None: raise db.failure
        rows = [row for row in db.rows.get(model, {}).values() if row.workspace_id == p.workspace_id]
        if model is m.Invocation:
            assert len(conditions) == 1 and service.CALL_KEY in conditions[0].compile().params.values()
            rows = [row for row in rows if service.CALL_KEY in row.data]
        elif model is m.Audit:
            rows = [row for row in rows if row.action in {service.CREATED, service.CHANGED, service.COMMANDED}]
        assert len(rows) <= maximum
        return sorted(rows, key=lambda row: row.id)

    def actor_fence(db, p):
        assert service._iam(db, p, write=True).id == p.user_id

    monkeypatch.setattr(service.shared, 'validate_call_approval', live_authority)
    monkeypatch.setattr(service, '_rows', fetch_rows)
    monkeypatch.setattr(relations, 'lock_current_actor', actor_fence)
    return s


def dispatch(s): return service.execute(s.db, s.owner, s.cap, copy.deepcopy(s.inputs), s.key, copy.deepcopy(s.approval))


def change(s, operation, version, *, actor=None, note='完整人稿\n需要保留', request_id=None, item_id=None):
    item_id = item_id or next(iter(s.db.rows[service.Item]))
    body = {'request_id': request_id or str(uuid4()), 'expected_version': version, 'note': note}
    wire = raw(body)
    return service.perform(s.db, actor or s.assignee, item_id, operation, wire, method='POST',
        path=service.command_path(item_id, operation)), wire


def test_actual_inventory_assigns_distinct_work_orders_and_only_recovers_same_batch(scene):
    s = scene; before_task = copy.deepcopy(s.task.data); result = dispatch(s)
    assert result['service_receipt']['status'] == 'assigned'
    assert result['service_receipt']['simulation_only'] is True
    assert result['service_receipt']['execution_completed'] is False
    assert result['service_receipt']['independent_acceptance'] is False
    assert len(s.db.rows[service.Batch]) == 1 and len(s.db.rows[service.Item]) == 2
    assert len({item['item_id'] for item in result['service_receipt']['items']}) == 2
    assert all(row.status == 'assigned' for row in s.db.rows[service.Item].values())
    assert s.task.data == before_task
    count = len(s.db.added)
    assert dispatch(s) == result and len(s.db.added) == count
    s.call.status = 'unknown'
    assert service.reconcile(s.db, s.owner, s.cap, s.inputs, s.key, s.approval, s.call) == result
    assert len(s.db.added) == count
    service.validate(service.output_schema(), result)


def test_progress_submit_return_revision_and_cancel_preserve_reports_without_parent_execution(scene):
    s = scene; dispatch(s); before = copy.deepcopy(s.task.data)
    first, first_wire = change(s, 'progress', 1)
    submitted, _ = change(s, 'submit', 2, note='已完成工作说明，等待独立验收')
    assert submitted['item']['status'] == 'completion_reported'
    assert all(submitted[key] is False for key in service.FLAGS)
    returned, _ = change(s, 'return', 3, actor=s.owner, note='说明尚缺依据，请修订后重新提交')
    assert returned['item']['status'] == 'returned'
    revised, _ = change(s, 'progress', 4, note='保留原说明并补充修订内容')
    assert revised['item']['status'] == 'in_progress'
    cancelled, _ = change(s, 'cancel', 5, actor=s.owner, note='停止本工作单，原报告保留')
    assert cancelled['item']['status'] == 'cancelled' and len(cancelled['item']['history']) == 6
    assert cancelled['item']['history'][1]['note'] == first['item']['note']
    recovered = service.recover(s.db, s.assignee, first['request_id'], hashlib.sha256(first_wire).hexdigest())
    assert recovered['found'] is True and recovered['result'] == first
    assert recovered['result']['item']['version'] == 2 and s.task.data == before
    assert not service.get_order(s.db, s.assignee, first['item']['id'])['permissions']['progress']


@pytest.mark.parametrize('fault', ['body', 'operation', 'other_item', 'actor'])
def test_same_nonce_cannot_change_body_operation_item_or_actor(scene, fault):
    s = scene; dispatch(s); result, wire = change(s, 'progress', 1)
    before = len(s.db.added); body = service.parse_wire(wire)
    item_id, operation, actor = result['item']['id'], 'progress', s.assignee
    if fault == 'body': wire = raw({**body, 'note': body['note'] + ' 不同正文'})
    elif fault == 'operation': operation = 'submit'
    elif fault == 'other_item': item_id = next(key for key in s.db.rows[service.Item] if key != item_id)
    else: actor = s.other
    with pytest.raises(HTTPException) as error:
        service.perform(s.db, actor, item_id, operation, wire, method='POST', path=service.command_path(item_id, operation))
    assert error.value.status_code == 409 and len(s.db.added) == before


def test_cas_refusal_is_explicit_only_after_full_inventory_and_original_reply_is_not_latest(scene):
    s = scene; dispatch(s); result, wire = change(s, 'progress', 1)
    item_id = result['item']['id']
    conflict_body = {'request_id': identity('cas-refused-submit'), 'expected_version': 1,
        'note': '本次提交未应用，完整保留原说明\n等待比较当前工作单'}
    conflict_wire = raw(conflict_body)
    conflict_sha = hashlib.sha256(conflict_wire).hexdigest()
    with pytest.raises(HTTPException) as error:
        service.perform(s.db, s.assignee, item_id, 'submit', conflict_wire, method='POST',
            path=service.command_path(item_id, 'submit'))
    assert error.value.status_code == 409
    assert error.value.detail == {'code': 'workflow_work_order_version_conflict', 'not_applied': True,
        'current_version': 2, 'request_id': conflict_body['request_id'], 'body_sha256': conflict_sha,
        'item_id': item_id, 'operation': 'submit'}
    again = service.perform(s.db, s.assignee, result['item']['id'], 'progress', wire, method='POST',
        path=service.command_path(result['item']['id'], 'progress'))
    assert again == result and len(s.db.rows[service.Command]) == 1


@pytest.mark.parametrize('fault', ['batch_audit', 'item', 'command_audit', 'state_audit', 'call_marker',
    'command_raw', 'current_state', 'orphan_audit', 'compound_rows_loss'])
def test_any_saved_inventory_or_independent_anchor_loss_blocks_cold_reads(scene, fault):
    s = scene; dispatch(s); result, wire = change(s, 'progress', 1)
    audits = s.db.rows[m.Audit]
    if fault in {'batch_audit', 'command_audit', 'state_audit'}:
        action = {'batch_audit': service.CREATED, 'command_audit': service.COMMANDED, 'state_audit': service.CHANGED}[fault]
        key = next(key for key, row in audits.items() if row.action == action)
        del audits[key]
    elif fault == 'item': del s.db.rows[service.Item][result['item']['id']]
    elif fault == 'call_marker': s.call.data = {k: v for k, v in s.call.data.items() if k != service.CALL_KEY}
    elif fault == 'command_raw': next(iter(s.db.rows[service.Command].values())).exact_body += ' '
    elif fault == 'current_state': s.db.rows[service.Item][result['item']['id']].data['note'] = 'tampered'
    elif fault == 'orphan_audit': s.db.add(m.Audit(workspace_id=s.workspace_id, actor_id=s.owner.user_id,
        action=service.CREATED, object_id=identity('orphan'), data={'schema': service.BATCH, 'record_hash': 'a' * 64}))
    else:
        for model in (service.Batch, service.Item, service.Command, m.Audit): s.db.rows[model] = {}
    with pytest.raises(HTTPException) as error:
        service.recover(s.db, s.assignee, result['request_id'], hashlib.sha256(wire).hexdigest())
    assert error.value.status_code == 409
    assert not isinstance(error.value.detail, dict) or 'not_applied' not in error.value.detail


@pytest.mark.parametrize('marker', [None, {}, False])
def test_present_but_invalid_invocation_marker_never_means_no_previous_dispatch(scene, marker):
    s = scene; s.call.data = {**s.call.data, service.CALL_KEY: marker}
    with pytest.raises(HTTPException): dispatch(s)
    assert not s.db.rows.get(service.Batch) and not s.db.rows.get(service.Item) and not s.db.added


def test_unknown_without_original_batch_is_read_only_blocked_not_recreated(scene):
    s = scene; s.call.status = 'unknown'
    with pytest.raises(HTTPException):
        service.reconcile(s.db, s.owner, s.cap, s.inputs, s.key, s.approval, s.call)
    assert not s.db.added


def test_personal_history_does_not_need_current_source_or_leak_to_another_member(scene):
    s = scene; dispatch(s); result, wire = change(s, 'progress', 1)
    s.authority_error = RuntimeError('No current report/provider may be consulted for original human reports')
    assert len(service.list_orders(s.db, s.assignee)['items']) == 2
    assert service.list_orders(s.db, s.other)['items'] == []
    with pytest.raises(HTTPException) as error: service.get_order(s.db, s.other, result['item']['id'])
    assert error.value.status_code == 403
    with pytest.raises(HTTPException): service.recover(s.db, s.other, result['request_id'], hashlib.sha256(wire).hexdigest())
    # Downgrade keeps original own receipts readable but cannot grant new writes.
    member = next(row for row in s.db.rows[m.Membership].values() if row.user_id == s.assignee.user_id)
    member.role = 'viewer'; viewer = Principal(s.assignee.user_id, s.workspace_id, 'viewer', 'synthetic-viewer')
    assert service.recover(s.db, viewer, result['request_id'], hashlib.sha256(wire).hexdigest())['result'] == result
    assert not any(service.get_order(s.db, viewer, result['item']['id'])['permissions'].values())
    with pytest.raises(HTTPException): change(s, 'submit', 2, actor=viewer)


@pytest.mark.parametrize('fault', ['viewer_assignee', 'inactive_assignee', 'past_due', 'beyond_goal', 'missing_approval'])
def test_new_assignment_requires_actual_member_deadline_and_explicit_live_authority(scene, fault):
    s = scene
    if fault == 'viewer_assignee':
        next(row for row in s.db.rows[m.Membership].values() if row.user_id == s.assignee.user_id).role = 'viewer'
    elif fault == 'inactive_assignee': s.db.rows[m.User][s.assignee.user_id].active = False
    elif fault == 'past_due': s.inputs['items'][0]['due_at'] = (AT - timedelta(seconds=1)).isoformat()
    elif fault == 'beyond_goal':
        spec = copy.deepcopy(s.task.data['goal_spec']); spec['deadline'] = (AT + timedelta(hours=2)).isoformat()
        s.task.data['goal_spec'] = spec; s.task.data['goal_hash'] = digest(spec)
        s.task.data['native_goal_context']['goal_snapshot'] = goal_snapshot(s.task)
        s.task.data['native_goal_context_hash'] = digest(s.task.data['native_goal_context'])
    else: s.authority_error = HTTPException(403, 'Explicit authority IO seam: original graph approval missing')
    # Refresh exact original transport inputs only for this synthetic new call.
    s.job.data.update(inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs))
    s.approval = service.make_approval(s.task, s.job, s.node, s.inputs)
    s.call.data.update(inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs), approval=copy.deepcopy(s.approval))
    s.transport['dispatch_authorization']['input_hash'] = digest(s.inputs)
    with pytest.raises(HTTPException): dispatch(s)
    assert not s.db.added and service.CALL_KEY not in s.call.data


@pytest.mark.parametrize('wire', [b'{"request_id":"x","expected_version":1,"note":"x"}',
    raw({'request_id': identity('nonce'), 'expected_version': True, 'note': '说明'}),
    raw({'request_id': identity('nonce'), 'expected_version': 1.0, 'note': '说明'}),
    raw({'request_id': identity('nonce'), 'expected_version': 0, 'note': '说明'}),
    raw({'request_id': identity('nonce'), 'expected_version': 1, 'note': ' '}),
    raw({'request_id': identity('nonce'), 'expected_version': 1, 'note': '说明', 'simulation_only': False}),
    b'{"request_id":"x","request_id":"y","expected_version":1,"note":"x"}',
    b'{"request_id":"x","expected_version":NaN,"note":"x"}', b'\xff'])
def test_original_json_is_closed_strict_and_never_coerces_or_discards_text(wire):
    with pytest.raises(ValueError): service.parse_wire(wire)


def test_utf8_notes_preserve_exact_bytes_and_raw_sha(scene):
    s = scene; dispatch(s)
    result, wire = change(s, 'progress', 1, note='  完整人稿\n数值1.0只是文字；不确认实体状态  ')
    command = next(iter(s.db.rows[service.Command].values()))
    assert command.exact_body.encode('utf-8') == wire and command.body_sha256 == hashlib.sha256(wire).hexdigest()
    assert result['item']['note'] == service.parse_wire(wire)['note']
    error = RuntimeError('actual SQL boundary failure, not a missing record')
    s.db.failure = error
    with pytest.raises(RuntimeError) as caught: service.recover(s.db, s.assignee, result['request_id'], command.body_sha256)
    assert caught.value is error
