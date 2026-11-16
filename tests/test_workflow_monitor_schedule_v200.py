"""Real schedule inventory/consumption with named source/approval IO seams.

These are not proof of a normally adopted V195 Task, device frequency control,
paid inference or operator UX. PG cases additionally exercise the new records
and independent audits in separate real Sessions; Root alone executes them.
"""
import base64
import copy
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.sql.elements import BooleanClauseList

from generic_agents import models as m
from generic_agents import workflow_monitor_schedule_v200 as subject
from generic_agents import workflow_graph_scope as scope
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.security import Principal
from generic_agents.monitor_scheduling_models_v253 import (
    MonitorSchedulingHeadV253 as ControlHead, MonitorSchedulingCommandV253 as ControlCommand)
from tests.test_business_reasoning_v197 import manifest as base_manifest, scene as reasoning_scene

AT = datetime(2026, 10, 1, tzinfo=timezone.utc)


def ident(label): return str(uuid5(NAMESPACE_URL, 'monitor-schedule-v200/' + label))


def manifest():
    return {**base_manifest(), 'kind': 'tool', 'namespace': 'schedule_test', 'name': 'monitor_schedule',
        'allowed_agent_roles': ['business'], 'input_schema': subject.input_schema(),
        'output_schema': subject.output_schema(), 'dependencies': [], 'data_policy': {'cloud_allowed': False},
        'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': subject.OP, 'side_effect': True}, 'budget': {'max_attempts': 1}, 'specification': {}}


def inputs(policy_id=None, entities=None):
    scene = reasoning_scene()
    for item in scene.inputs['candidates']: item['context'] = None
    result = subject.reasoning.check_result({'inputs': scene.inputs}, scene.output)
    objects = entities or [ident('object-a'), ident('object-b'), ident('object-c')]
    return {'reasoning_result': result, 'expected_schedule_pin': None,
        'policy_pin': {'policy_id': policy_id or ident('policy'),
        'approved_hash': 'a' * 64}, 'profiles': [
            {'candidate_ref': 'proposal_a', 'interval_seconds': 600, 'priority_entity_ids': [objects[1]],
                'valid_until': (AT + timedelta(hours=2)).isoformat()},
            {'candidate_ref': 'proposal_b', 'interval_seconds': 900, 'priority_entity_ids': [],
                'valid_until': (AT + timedelta(hours=1)).isoformat()}]}


class Rows(list):
    def all(self): return list(self)


class IO:
    """Only SQL row retrieval/clock/transaction boundary is substituted."""
    def __init__(self): self.rows = {}; self.at = AT; self.locks = []; self.flushes = 0; self.control_probes = []
    def put(self, row):
        if row.id is None: row.id = str(uuid4())
        if hasattr(row, 'version') and row.version is None: row.version = 1
        if getattr(row, 'created_at', None) is None: row.created_at = self.at
        self.rows.setdefault(type(row), {})[row.id] = row
        return row
    def _matches(self, row, clause):
        if isinstance(clause, BooleanClauseList):
            assert clause.operator.__name__ == 'and_'
            return all(self._matches(row, child) for child in clause.clauses)
        left, right = clause.left, clause.right
        name = getattr(left, 'key', None)
        if name == 'data': raise AssertionError('whole payload is not an IO filter')
        if hasattr(left, 'right') and getattr(left.right, 'value', None) in {'policy_id', 'request_id'}:
            actual = row.data.get(left.right.value)
        else:
            assert name in {'id', 'workspace_id', 'policy_id', 'action', 'object_id', 'idempotency_key', 'request_id'}
            actual = getattr(row, name)
        assert hasattr(right, 'value')
        if clause.operator.__name__ == 'in_op': return actual in right.value
        assert clause.operator.__name__ == 'eq'
        return actual == right.value
    def scalars(self, query):
        entity = query.column_descriptions[0]['entity']
        if entity in (ControlHead, ControlCommand):
            assert not self.rows.get(entity)
            self.control_probes.append(entity)
            return Rows([])  # This old-source fixture explicitly has no V253 control.
        values = list(self.rows.get(entity, {}).values())
        if query.whereclause is not None: values = [v for v in values if self._matches(v, query.whereclause)]
        return Rows(values)
    def scalar(self, query):
        description=query.column_descriptions[0];entity=description.get('entity')
        if description.get('name')=='id' and entity in (m.ObservationPlan,m.Acquisition,m.Audit,m.Job):
            assert query._limit_clause is not None and query._limit_clause.value==1
            # Exact empty independent-control probes, while legacy V200 Audits
            # and its real producer Job remain visible to their original IO.
            values=list(self.rows.get(entity,{}).values())
            if entity is m.Audit:
                assert not any(v.action=='monitor.scheduling_acquisition_v253' or
                    v.action=='workspace.monitor.tick_queued_v203' and 'control' in v.data.get('pin',{}).get('schedule_hint',{}) for v in values)
            elif entity is m.Job:
                assert not any('control' in v.data.get('workspace_monitor_tick',{}).get('schedule_hint',{}) for v in values)
            else:assert not any('monitor_scheduling_v253' in v.data for v in values)
            self.control_probes.append(entity)
            return None
        if entity is None:
            assert 'clock_timestamp' in str(query)
            return self.at
        rows = self.scalars(query); assert len(rows) <= 1
        return rows[0] if rows else None
    def add(self, row):
        assert type(row) in {subject.Head, subject.Installation, subject.Cycle, subject.StopCommand, m.Audit}
        self.put(row)
    def flush(self): self.flushes += 1
    def execute(self, statement, params):
        assert 'pg_advisory_xact_lock' in str(statement)
        self.locks.append(copy.deepcopy(params))
    @contextmanager
    def begin_nested(self):
        # Preserve row identities as a Session does; restore their attributes.
        before = {kind: {key: (row, {name: copy.deepcopy(getattr(row, name)) for name in
            ('data', 'version', 'record_hash', 'status', 'next_due') if hasattr(row, name)})
            for key, row in rows.items()} for kind, rows in self.rows.items()}
        try: yield
        except BaseException:
            self.rows = {kind: {key: pair[0] for key, pair in rows.items()} for kind, rows in before.items()}
            for rows in before.values():
                for row, values in rows.values():
                    for name, value in values.items(): setattr(row, name, value)
            raise


def install_seams(monkeypatch, s):
    """Authority is explicit IO; no claimed real source/175 chain here."""
    def current_context(db, p, cap, role, value, key, approval):
        assert p.workspace_id == s.p.workspace_id and p.user_id == s.p.user_id
        assert role == 'business' and key == s.key and approval == s.approval
        subject.need(s.authority_error is None)
        assert digest(value) == digest(s.inputs) and cap.id == s.cap.id
        return s.context
    def original_call(db, p, context, key, value, approval, supplied=None, **kw):
        assert context['task'].id == s.task.id and key == s.key
        assert supplied is None or supplied.id == s.call.id
        return s.call
    def producer(db, p, context, result):
        assert result == s.inputs['reasoning_result']; return copy.deepcopy(s.producer)
    def policy(db, p, context, value, at):
        assert value['policy_pin']['policy_id'] == s.policy.id
        assert context['task'].id == s.task.id and at == s.db.at
        return s.policy
    monkeypatch.setattr(subject, 'validate_call_approval', current_context)
    monkeypatch.setattr(subject.shared, '_call', original_call)
    monkeypatch.setattr(subject, '_producer', producer)
    monkeypatch.setattr(subject, '_policy', policy)
    monkeypatch.setattr(subject, '_approval_proof', lambda db, p, task: copy.deepcopy(s.approval_pin))
    def tick_authority(db, p, policy, value):
        s.authority_checks.append((policy.id, value['id']))
        subject.need(s.authority_error is None and policy.status == 'active'
            and policy.data['approved_hash'] == value['policy_hash'])
        return s.p
    monkeypatch.setattr(subject, '_tick_authority', tick_authority)
    from generic_agents import perception, signal_dual_handoffs, relations
    monkeypatch.setattr(signal_dual_handoffs, 'prelock_task', lambda db, p, task_id: s.parent_locks.append(task_id))
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: subject.need(s.authority_error is None))
    monkeypatch.setattr(perception, 'authorized_monitor', lambda db, p, policy:
        subject.need(policy.status == 'active' and policy.data['approved_hash'] == s.inputs['policy_pin']['approved_hash']))
    def propose(db, p, policy_id, *, entity_ids=None, observation_request=None):
        assert observation_request is None and policy_id == s.policy.id
        s.proposals.append(copy.deepcopy(entity_ids))
        if s.proposal_error: raise s.proposal_error
        return [ident('acquisition-' + str(len(s.proposals)))]
    monkeypatch.setattr(perception, 'propose_acquisitions', propose)


@pytest.fixture
def scene(monkeypatch):
    s = NS(db=IO(), authority_error=None, authority_checks=[], proposals=[], proposal_error=None, parent_locks=[])
    s.p = Principal(ident('actor'), ident('workspace'), 'operator', 'synthetic-source-and-approval-seam')
    s.entities = [ident('object-a'), ident('object-b'), ident('object-c')]
    s.inputs = inputs(entities=s.entities); data = manifest()
    s.cap = m.Capability(id=ident('cap'), workspace_id=s.p.workspace_id, kind='tool', namespace='schedule_test',
        name='monitor_schedule', release_version='1.0.0', status='published', content_hash=digest(data),
        validated_hash=digest(data), data=data)
    s.task = m.Task(id=ident('task'), workspace_id=s.p.workspace_id, owner_id=s.p.user_id,
        package_id=ident('package'), title='合成调度安装任务，不是已采用真实流程', status='workflow_running',
        data={'instance_id': ident('instance'), 'entity_ids': s.entities})
    s.release = NS(id=ident('release'), package_id=s.task.package_id)
    s.key = 'graph-monitor-schedule:' + ident('job')
    s.job = m.Job(id=ident('job'), workspace_id=s.p.workspace_id, kind=scope.KIND,
        idempotency_key=s.key, status='running', attempts=1, data={'activation_id': 'consumer',
            'actor_id': s.p.user_id, 'task_id': s.task.id, 'invocation_key': s.key, 'inputs': s.inputs})
    s.approval = {'activation_id': 'consumer', 'cancel_epoch': 0, 'capability_hash': s.cap.content_hash}
    s.approval_pin = {'managed_id': ident('managed'), 'submission_id': ident('submission'),
        'submission_hash': 'c' * 64, 'execution_hash': 'd' * 64}
    s.producer = {'candidates': [{'candidate_ref': v['candidate_ref'], 'summary': '合成已冻结提案', 'context': None}
        for v in s.inputs['profiles']], 'output_hash': digest(s.inputs['reasoning_result']), 'job_id': ident('producer-job')}
    s.policy = m.MonitorPolicy(id=s.inputs['policy_pin']['policy_id'], workspace_id=s.p.workspace_id,
        status='active', approved_by=s.p.user_id, data={'approved_hash': 'a' * 64, 'entity_ids': s.entities,
            'cooldown_seconds': 60, 'valid_until': (AT + timedelta(days=1)).isoformat(), 'daily_budget': 5})
    s.call = m.Invocation(id=ident('call'), workspace_id=s.p.workspace_id, capability_id=s.cap.id,
        idempotency_key=s.key, status='running', data={})
    s.context = {'task': s.task, 'job': s.job, 'release': s.release, 'capability': s.cap,
        'binding': {'binding_hash': 'b' * 64, 'simulation_only': True}}
    for row in (s.task, s.policy, s.call): s.db.put(row)
    install_seams(monkeypatch, s)
    return s


def pending_install(s):
    with pytest.raises(subject.shared.PendingService):
        subject.execute(s.db, s.p, s.cap, copy.deepcopy(s.inputs), s.key, s.approval)
    head, installs, cycles = subject._checked(s.db, s.p, s.policy.id)
    assert len(installs) == 1 and not cycles and head.status == 'active'
    return head, installs[0]


def test_registration_is_real_side_effect_local_service_with_closed_profiles():
    assert subject.validate_manifest(manifest())['execution']['side_effect'] is True
    value = inputs(); assert subject._inputs(value) == value
    assert value['reasoning_result']['recommended_context'] is None
    for field, bad in [('side_effect', False), ('service_actor', 'business'), ('operation', 'resense_with_receipt')]:
        data = manifest(); data['execution'][field] = bad
        with pytest.raises(HTTPException): subject.validate_manifest(data)
    data = manifest(); data['budget']['max_attempts'] = 2
    with pytest.raises(HTTPException): subject.validate_manifest(data)


@pytest.mark.parametrize('change', ['float_interval', 'bool_interval', 'duplicate', 'unknown', 'naive_time'])
def test_input_does_not_coerce_profiles_or_create_authority(change):
    value = inputs()
    if change == 'float_interval': value['profiles'][0]['interval_seconds'] = 600.0
    if change == 'bool_interval': value['profiles'][0]['interval_seconds'] = True
    if change == 'duplicate': value['profiles'].append(copy.deepcopy(value['profiles'][0]))
    if change == 'unknown': value['profiles'][0]['device_sampling_hz'] = 100
    if change == 'naive_time': value['profiles'][0]['valid_until'] = '2026-10-01T12:00:00'
    with pytest.raises((HTTPException, ValueError)): subject._inputs(value)


def test_install_is_pending_until_due_worker_consumes_actual_original_policy(scene):
    s = scene; original = copy.deepcopy(s.policy.data); head, install = pending_install(s)
    assert not s.proposals and subject.CALL_KEY in s.call.data
    with pytest.raises(subject.shared.PendingService):
        subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is True
    assert s.proposals == [[s.entities[1], s.entities[0], s.entities[2]]]
    assert s.parent_locks == [s.task.id] and len(s.authority_checks) == 1
    checked_head, records, cycles = subject._checked(s.db, s.p, s.policy.id)
    assert checked_head.version == 2 and len(records) == len(cycles) == 1
    assert checked_head.next_due == AT + timedelta(seconds=600)
    reply = subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    receipt = reply['service_receipt']
    assert receipt['cycle_id'] == cycles[0].id and receipt['status'] == 'check_consumed'
    assert receipt['simulation_only'] is True
    assert receipt['hardware_sampling_rate_changed'] is receipt['device_execution_completed'] is receipt['independent_acceptance'] is False
    assert s.policy.data == original
    reply['service_receipt']['priority_entity_ids'].clear()
    assert install.data['selected_profile']['priority_entity_ids'] == [s.entities[1]]


def test_same_key_and_not_due_ticks_do_not_reinstall_or_repropose(scene):
    s = scene; pending_install(s)
    subject.consume_policy_tick(s.db, s.p, s.policy)
    first = subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is True
    assert len(s.proposals) == 1
    s.db.at += timedelta(seconds=600)
    subject.consume_policy_tick(s.db, s.p, s.policy)
    assert len(s.proposals) == 2
    assert subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call) == first
    assert len(subject._checked(s.db, s.p, s.policy.id)[1]) == 1


@pytest.mark.parametrize('bad', ['needs_information', 'conflicting_evidence', 'missing_recommendation', 'unfrozen_profiles'])
def test_install_does_not_choose_ambiguous_or_unapproved_candidate(scene, bad):
    s = scene
    if bad in {'needs_information', 'conflicting_evidence'}: s.inputs['reasoning_result']['review_state'] = bad
    elif bad == 'missing_recommendation': s.inputs['reasoning_result']['recommended_candidate_ref'] = None
    else: s.producer['candidates'].reverse()
    with pytest.raises(HTTPException): subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert not s.db.rows.get(subject.Installation) and not s.proposals


@pytest.mark.parametrize('change', ['missing_head', 'missing_install_audit', 'missing_state_audit', 'forged_state', 'extra_audit'])
def test_orphan_or_resealed_inventory_cannot_fallback_or_be_rebuilt(scene, change):
    s = scene; head, record = pending_install(s)
    if change == 'missing_head': del s.db.rows[subject.Head][head.id]
    elif change in {'missing_install_audit', 'missing_state_audit'}:
        action = subject.INSTALLED if change == 'missing_install_audit' else subject.STATE
        event = next(v for v in s.db.rows[m.Audit].values() if v.action == action)
        del s.db.rows[m.Audit][event.id]
    elif change == 'forged_state':
        body = {k: copy.deepcopy(v) for k, v in head.data.items() if k != 'record_hash'}
        body['status'] = 'expired'; body['next_due'] = None
        head.data = subject._seal(body); head.record_hash = head.data['record_hash']; head.status = 'expired'; head.next_due = None
    else:
        s.db.put(m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id, action=subject.INSTALLED,
            object_id=ident('deleted-install'), data={'policy_id': s.policy.id}))
    with pytest.raises(HTTPException): subject.consume_policy_tick(s.db, s.p, s.policy)
    assert not s.proposals


def test_legitimate_absence_uses_legacy_but_not_orphan_audit(scene):
    s = scene
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is False
    s.db.put(m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id, action=subject.STATE,
        object_id=ident('missing'), data={'policy_id': s.policy.id}))
    with pytest.raises(HTTPException): subject.consume_policy_tick(s.db, s.p, s.policy)


def test_queued_original_head_pin_is_checked_under_existing_schedule_lock(scene):
    s = scene; pending_install(s)
    original_head = subject._checked(s.db, s.p, s.policy.id)[0]
    pin = subject.policy_tick_pin(original_head)
    changed = copy.deepcopy(pin); changed['version'] += 1
    before = copy.deepcopy(original_head.data)
    with pytest.raises(HTTPException):
        subject.consume_policy_tick(s.db, s.p, s.policy, expected_head_pin=changed)
    assert not s.proposals and original_head.data == before
    assert subject._checked(s.db, s.p, s.policy.id)[2] == []


def test_queued_legacy_none_cannot_consume_a_later_installed_head(scene):
    s = scene; pending_install(s)
    with pytest.raises(HTTPException):
        subject.consume_policy_tick(s.db, s.p, s.policy, expected_head_pin=None)
    assert not s.proposals and subject._checked(s.db, s.p, s.policy.id)[2] == []


def test_queued_legacy_policy_pin_change_blocks_before_false_fallback(scene):
    s = scene
    pin = subject.policy_tick_policy_pin(s.policy)
    pin['approved_hash'] = '0' * 64
    with pytest.raises(HTTPException):
        subject.consume_policy_tick(s.db, s.p, s.policy,
            expected_head_pin=None, expected_policy_pin=pin)
    assert not s.proposals


@pytest.mark.parametrize('failure', ['revoked_actor', 'policy_changed', 'proposal_rejected'])
def test_current_revocation_or_failed_proposal_is_visible_block_not_success(scene, failure):
    s = scene; pending_install(s)
    if failure == 'revoked_actor': s.authority_error = 'revoked'
    elif failure == 'policy_changed': s.policy.data = {**s.policy.data, 'approved_hash': 'f' * 64}
    else: s.proposal_error = HTTPException(409, '原预算或资源占用拒绝')
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is True
    head, _, cycles = subject._checked(s.db, s.p, s.policy.id)
    assert head.status == 'blocked' and cycles[0].outcome == 'blocked'
    assert cycles[0].data['acquisition_ids'] == [] and head.next_due is None
    with pytest.raises(HTTPException): subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    count = len(s.proposals)
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is True and len(s.proposals) == count


def test_expiry_is_explicit_then_restores_original_policy_without_extending_it(scene):
    s = scene; pending_install(s); original = copy.deepcopy(s.policy.data)
    s.db.at = AT + timedelta(hours=2)
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is True
    assert subject._checked(s.db, s.p, s.policy.id)[0].status == 'expired'
    assert not s.proposals and s.policy.data == original
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is False


def producer_scene(monkeypatch):
    """Keep real 197 envelope/parser/result; substitute source/store lookup only."""
    r = reasoning_scene(); r.inputs['candidates'][0]['context'] = None
    r.job.data = {**r.job.data, 'inputs': copy.deepcopy(r.inputs), 'input_hash': digest(r.inputs)}
    r.call.data = {**r.call.data, 'input_hash': digest(r.inputs)}
    result = subject.reasoning.check_result({'inputs': r.inputs}, r.output)
    db = IO(); r.call.status = 'succeeded'; r.call.data['output'] = result
    producer = {'id': 'original-analysis', 'status': 'completed', 'epoch': 0, 'output': copy.deepcopy(result)}
    consumer = {'id': 'schedule-consumer', 'epoch': 0, 'inputs_snapshot': {'bindings': {'reasoning_result': {
        'missing': False, 'producer_activation_id': producer['id'], 'value_hash': digest(result)}}}}
    r.job.status = 'succeeded'
    r.task.data = {'graph_runtime': {'activations': {producer['id']: producer, consumer['id']: consumer}},
        'graph_jobs': {producer['id']: {'job_id': r.job.id, 'invocation_id': r.call.id}}}
    r.node['execution']['capability_hash'] = r.cap.content_hash
    context_job = NS(data={'activation_id': consumer['id']})
    db.put(m.Audit(workspace_id=r.p.workspace_id, actor_id=r.p.user_id, action='capability.invoke',
        object_id=r.call.id, data={'status': 'succeeded'}))
    envelope = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(r.output)}}],
        'usage': {'prompt_tokens': 12, 'completion_tokens': 30, 'total_tokens': 42}})
    from hashlib import sha256
    frozen = {'request': {'inputs': copy.deepcopy(r.inputs),
        'model_value_schema': subject.reasoning.model_value_schema(r.inputs['evidence_ids'], ['proposal_a', 'proposal_b'])},
        'capture': {'raw_base64': base64.b64encode(envelope.encode()).decode(),
            'raw_sha256': sha256(envelope.encode()).hexdigest(), 'receipt': {'http_status': 200}}}
    monkeypatch.setattr(subject, 'scoped', lambda db, model, identity, p, *args:
        r.job if model is m.Job and identity == r.job.id else r.call if model is m.Invocation and identity == r.call.id else None)
    monkeypatch.setattr(subject, 'published', lambda db, p, identity, *args: r.cap)
    monkeypatch.setattr(scope, '_frozen', lambda *args: (r.task, None, None, {}, {}, producer, r.node))
    monkeypatch.setattr(scope, '_job_matches', lambda task, job, state, activation, node:
        subject.need(job.id == r.job.id and activation['id'] == producer['id']))
    from generic_agents import worker
    monkeypatch.setattr(worker, 'actor', lambda db, ws, actor_id: r.p)
    def checked(db, actor, cap, value, key, call):
        assert value == r.inputs and key == r.key and call is r.call
        return copy.deepcopy(frozen)
    monkeypatch.setattr(subject.reasoning, '_checked_state', checked)
    return NS(db=db, r=r, result=result, context={'task': r.task, 'job': context_job},
        producer=producer, consumer=consumer, frozen=frozen)


def test_original_producer_uses_actual_197_parse_and_check_result_abi(monkeypatch):
    s = producer_scene(monkeypatch)
    pin = subject._producer(s.db, s.r.p, s.context, s.result)
    assert pin['job_id'] == s.r.job.id and pin['invocation_id'] == s.r.call.id
    assert pin['request_key_hash'] == digest(s.r.key) and pin['actor_id'] == s.r.p.user_id
    assert pin['output_hash'] == digest(s.result) and pin['candidates'] == s.r.inputs['candidates']
    assert pin['capture_hash'] == s.frozen['capture']['raw_sha256']


@pytest.mark.parametrize('bad', ['other_value', 'missing_binding', 'missing_audit', 'running_job', 'bad_model_answer'])
def test_original_producer_no_marker_or_equal_text_fallback(monkeypatch, bad):
    s = producer_scene(monkeypatch)
    if bad == 'other_value': s.producer['output']['analysis']['summary'] += ' changed'
    if bad == 'missing_binding': s.consumer['inputs_snapshot']['bindings'].clear()
    if bad == 'missing_audit': s.db.rows[m.Audit].clear()
    if bad == 'running_job': s.r.job.status = 'running'
    if bad == 'bad_model_answer':
        raw = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})
        s.frozen['capture']['raw_base64'] = base64.b64encode(raw.encode()).decode()
    from generic_agents.workflow_task_context_v196 import ProducerPending
    with pytest.raises((HTTPException, ProducerPending, ValueError, ModelBlocked)):
        subject._producer(s.db, s.r.p, s.context, s.result)


def test_completed_and_accepted_are_history_not_new_goal_execution(monkeypatch, scene):
    s = scene; _, install = pending_install(s)
    # Restore the actual tick implementation; each current-authority IO seam
    # below is explicit and records calls. This does not manufacture V175 rows.
    monkeypatch.undo()
    from generic_agents import worker, workflow_graph, relations, native_goals, instance_api, perception
    from generic_agents import layered_approval_store_v175 as approvals
    install_value = copy.deepcopy(install.data)
    source = install_value['source']; s.task.status = 'workflow_completed'
    activation = {'id': 'consumer', 'epoch': 0}; state = {'status': 'succeeded', 'cancel_epoch': 0}
    monkeypatch.setattr(scope, '_frozen', lambda *args: (s.task, s.release, {'id': 'business'}, {}, state, activation, {}))
    monkeypatch.setattr(scope, '_job_matches', lambda *args: {})
    monkeypatch.setattr(worker, 'actor', lambda *args: s.p)
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: None)
    monkeypatch.setattr(subject.kernel, 'ability_inputs', lambda *args: copy.deepcopy(s.inputs))
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *args: {'binding_hash': source['binding_hash']})
    monkeypatch.setattr(subject, '_producer', lambda *args: copy.deepcopy(s.producer))
    monkeypatch.setattr(subject, 'published', lambda db, p, identity, *args: s.cap)
    actual_scoped = subject.scoped
    entity = NS(namespace='current-namespace')
    monkeypatch.setattr(subject, 'scoped', lambda db, model, identity, p, *args:
        s.job if model is m.Job else entity if model is m.Entity else actual_scoped(db, model, identity, p, *args))
    checked = []
    monkeypatch.setattr(native_goals, 'saved_context', lambda *args: {'instance_id': source['instance_id'],
        'release_id': source['release_id'], 'entity_ids': s.entities})
    monkeypatch.setattr(instance_api, 'active', lambda *args: (NS(id=source['instance_id'], namespace=entity.namespace,
        data={'enabled_business_ids': ['business']}), s.release))
    monkeypatch.setattr(perception, 'authorized_monitor', lambda *args: checked.append('current_monitor'))
    managed = NS(id=s.approval_pin['managed_id'], policy_id=ident('approval-policy'), status='accepted')
    execution = {'initialized': True, 'submission_hash': s.approval_pin['submission_hash']}
    # Match a real stable execution proof captured at installation, rather than
    # asserting any accepted marker authorizes a new Task dispatch.
    source['approval_pin']['execution_hash'] = digest(execution)
    monkeypatch.setattr(approvals, 'managed', lambda *args: managed)
    monkeypatch.setattr(approvals, 'checked', lambda row: {'execution': copy.deepcopy(execution)})
    monkeypatch.setattr(approvals, 'current_policy', lambda *args: NS(enabled=True))
    monkeypatch.setattr(approvals, 'policy_value', lambda *args: checked.append('approval_stop_switch'))
    monkeypatch.setattr(approvals, 'current_submission', lambda *args: (NS(id=s.approval_pin['submission_id']), {}, {}))
    monkeypatch.setattr(approvals, 'row_hash', lambda *args: s.approval_pin['submission_hash'])
    def forbidden(*args, **kwargs): raise AssertionError('a historical install may not call executing-only Goal/live gates')
    monkeypatch.setattr(native_goals, 'guard', forbidden); monkeypatch.setattr(workflow_graph, 'live', forbidden)
    assert subject._tick_authority(s.db, s.p, s.policy, install_value) == s.p
    assert checked == ['approval_stop_switch', 'current_monitor']
    for status in ('executing', 'acceptance_returned'):
        managed.status = status
        assert subject._tick_authority(s.db, s.p, s.policy, install_value) == s.p
    s.task.data = {**s.task.data, 'native_goal_cancelled': {'reason': '明确停止'}}
    with pytest.raises(HTTPException): subject._tick_authority(s.db, s.p, s.policy, install_value)


def next_installation(s, pin):
    """A separate explicitly approved-consumer IO seam, not a second device call."""
    s.inputs = {**copy.deepcopy(s.inputs), 'expected_schedule_pin': copy.deepcopy(pin)}
    s.key = 'graph-monitor-schedule:' + str(uuid4())
    s.job = m.Job(id=str(uuid4()), workspace_id=s.p.workspace_id, kind=scope.KIND, idempotency_key=s.key,
        status='running', attempts=1, data={'actor_id': s.p.user_id, 'task_id': s.task.id,
            'activation_id': 'consumer', 'inputs': s.inputs, 'invocation_key': s.key})
    s.call = m.Invocation(id=str(uuid4()), workspace_id=s.p.workspace_id, capability_id=s.cap.id,
        idempotency_key=s.key, status='running', data={})
    s.context['job'] = s.job
    s.db.put(s.job); s.db.put(s.call)


def test_new_approved_decision_replaces_only_exact_checked_configuration_pin(scene):
    s = scene; head, original = pending_install(s)
    old_pin = subject.schedule_pin(original)
    next_installation(s, old_pin)
    with pytest.raises(HTTPException, match='首'):
        subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert len(subject._checked(s.db, s.p, s.policy.id)[1]) == 1
    subject.consume_policy_tick(s.db, s.p, s.policy)
    # A due cycle changes Head.version but deliberately not the config CAS pin.
    assert head.version == 2 and subject.schedule_pin(original) == old_pin
    with pytest.raises(subject.shared.PendingService):
        subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    current, installs, cycles = subject._checked(s.db, s.p, s.policy.id)
    assert len(installs) == 2 and len(cycles) == 1 and original.id in current.data['installation_ids']
    assert current.data['active_installation_id'] != original.id and current.version == 3
    next_installation(s, old_pin)
    with pytest.raises(HTTPException): subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert len(subject._checked(s.db, s.p, s.policy.id)[1]) == 2


def test_profile_capacity_reserves_expiry_and_never_truncates_history(monkeypatch, scene):
    s = scene; monkeypatch.setattr(subject, 'MAX_RECORDS', 4)
    for item in s.inputs['profiles']:
        item['interval_seconds'] = 60; item['valid_until'] = (AT + timedelta(seconds=180)).isoformat()
    _, installed = pending_install(s)
    assert installed.data['capacity'] == {'existing_cycles': 0, 'maximum_new_cycles': 4, 'terminal_slot_reserved': True}
    for seconds in (0, 60, 120):
        s.db.at = AT + timedelta(seconds=seconds)
        assert subject.consume_policy_tick(s.db, s.p, s.policy) is True
    original_hash = subject._checked(s.db, s.p, s.policy.id)[0].record_hash
    next_installation(s, subject.schedule_pin(installed))
    for item in s.inputs['profiles']: item['valid_until'] = (AT + timedelta(seconds=240)).isoformat()
    with pytest.raises(HTTPException): subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert subject._checked(s.db, s.p, s.policy.id)[0].record_hash == original_hash
    s.db.at = AT + timedelta(seconds=180)
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is True
    head, records, cycles = subject._checked(s.db, s.p, s.policy.id)
    assert len(cycles) == 4 and [row.outcome for row in cycles].count('expired') == 1
    assert head.status == 'expired' and len(records) == 1
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is False


def test_valid_60_second_24_hour_profile_is_refused_before_install_if_history_cannot_hold_it(scene):
    s = scene
    for item in s.inputs['profiles']:
        item['interval_seconds'] = 60; item['valid_until'] = (AT + timedelta(hours=24)).isoformat()
    with pytest.raises(HTTPException): subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert not s.db.rows.get(subject.Head) and not s.db.rows.get(subject.Installation)


def stop_body(s, installed, restore=False):
    body = {'request_id': str(uuid4()), 'expected_schedule_pin': subject.schedule_pin(installed),
        'reason': '明确停止后续检查，不取消正在执行的设备动作', 'restore_legacy': restore}
    return body, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


@pytest.mark.parametrize('restore', [False, True])
def test_stop_original_nonce_is_durable_exact_cas_and_only_explicit_restore_uses_legacy(scene, restore):
    s = scene; _, installed = pending_install(s)
    body, raw = stop_body(s, installed, restore)
    path = '/api/workspace/monitor-schedules-v200/' + s.policy.id + '/stop'
    first = subject.stop_schedule(s.db, s.p, s.policy.id, body, raw, path=path)
    assert first['status'] == 'cancelled' and first['device_execution_cancelled'] is False
    assert subject.stop_schedule(s.db, s.p, s.policy.id, body, raw, path=path) == first
    from hashlib import sha256
    recovered = subject.original_stop_request(s.db, s.p, s.policy.id, body['request_id'], sha256(raw).hexdigest(), path=path)
    assert recovered['found'] is True and recovered['result'] == first
    assert subject.consume_policy_tick(s.db, s.p, s.policy) is (not restore)
    assert not s.proposals and len(s.db.rows[subject.StopCommand]) == 1
    with pytest.raises(HTTPException) as stopped:
        subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert '首个检查尚未生效' in stopped.value.detail
    changed = {**body, 'reason': '另一个正文'}
    new_raw = json.dumps(changed, ensure_ascii=False).encode()
    with pytest.raises(HTTPException): subject.stop_schedule(s.db, s.p, s.policy.id, changed, new_raw, path=path)
    command = next(iter(s.db.rows[subject.StopCommand].values()))
    del s.db.rows[subject.StopCommand][command.id]
    with pytest.raises(HTTPException):
        subject.original_stop_request(s.db, s.p, s.policy.id, body['request_id'], sha256(raw).hexdigest(), path=path)


def test_stop_stale_configuration_does_not_cancel_new_installation(scene):
    s = scene; _, old = pending_install(s); subject.consume_policy_tick(s.db, s.p, s.policy)
    body, raw = stop_body(s, old)
    next_installation(s, subject.schedule_pin(old))
    with pytest.raises(subject.shared.PendingService): subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    before = subject._checked(s.db, s.p, s.policy.id)[0].record_hash
    with pytest.raises(HTTPException): subject.stop_schedule(s.db, s.p, s.policy.id, body, raw, path='/schedule/stop')
    assert subject._checked(s.db, s.p, s.policy.id)[0].record_hash == before
    assert not s.db.rows.get(subject.StopCommand)


@pytest.fixture
def pg_scene(account, client, monkeypatch):
    """Real isolated PG; normal source/approval/proposal are named IO seams.

    This is deliberately not a normally adopted/175-approved source Task.
    It cannot be cited as the real 27-action workflow or physical scheduling.
    """
    from sqlalchemy import func
    from sqlalchemy.orm import Session
    from tests.test_workflow_provenance_wait_pg_v197 import scene as previous_scene
    from generic_agents import perception, signal_dual_handoffs
    s = previous_scene.__wrapped__(account, client, monkeypatch)
    s.proposals = []; s.producer = None
    with Session(s.bound, expire_on_commit=False) as db:
        task, cap, job = db.get(m.Task, s.task_id), db.get(m.Capability, s.cap_id), db.get(m.Job, s.job_id)
        original = db.get(m.Entity, task.data['entity_ids'][0]); objects = [original.id]
        for suffix in ('b', 'c'):
            row = m.Entity(workspace_id=s.p.workspace_id, namespace=original.namespace,
                entity_type=original.entity_type, schema_capability_id=original.schema_capability_id,
                external_id='schedule-pg-' + suffix + '-' + str(uuid4()), data={'test_only': True})
            db.add(row); db.flush(); objects.append(row.id)
        at = db.scalar(select(func.clock_timestamp()))
        policy_value = {'entity_ids': objects, 'cooldown_seconds': 60,
            'valid_until': (at + timedelta(days=1)).isoformat(), 'daily_budget': 4,
            'test_only_explicit_authority_seam': True}
        policy = m.MonitorPolicy(workspace_id=s.p.workspace_id, status='active', approved_by=s.p.user_id,
            data={**policy_value, 'approved_hash': digest(policy_value)})
        db.add(policy); db.flush()
        value = inputs(policy.id, objects); value['policy_pin']['approved_hash'] = policy.data['approved_hash']
        for item in value['profiles']: item['valid_until'] = (at + timedelta(hours=1)).isoformat()
        data = manifest(); cap.data = data; cap.content_hash = cap.validated_hash = digest(data)
        cap.name = 'schedule_pg_' + str(uuid4())
        job.data = {**job.data, 'inputs': value, 'input_hash': digest(value)}
        task.data = {**task.data, 'entity_ids': objects}
        call = m.Invocation(workspace_id=s.p.workspace_id, capability_id=cap.id,
            idempotency_key=s.key, status='running', data={})
        db.add(call); db.flush(); db.commit()
        s.inputs, s.policy_id, s.call_id, s.objects = copy.deepcopy(value), policy.id, call.id, objects
        s.approval = {'activation_id': s.activation_id, 'cancel_epoch': 0, 'capability_hash': cap.content_hash}
        s.approval_pin = {'managed_id': str(uuid4()), 'submission_id': str(uuid4()),
            'submission_hash': 'c' * 64, 'execution_hash': 'd' * 64}
        s.producer = {'candidates': [{'candidate_ref': item['candidate_ref'], 'summary': '显式IO边界提案',
            'context': None} for item in value['profiles']], 'output_hash': digest(value['reasoning_result'])}
    def context(db, p, cap, role, value, key, approval):
        assert p.user_id == s.p.user_id and p.workspace_id == s.p.workspace_id
        assert cap.id == s.cap_id and key == s.key and value == s.inputs and approval == s.approval
        return {'task': db.get(m.Task, s.task_id), 'job': db.get(m.Job, s.job_id),
            'release': db.get(m.InstanceRelease, s.release_id), 'capability': db.get(m.Capability, s.cap_id),
            'binding': {'binding_hash': 'b' * 64, 'simulation_only': True}}
    def call_lookup(db, p, actual, key, value, approval, supplied=None, **kw):
        call = db.get(m.Invocation, s.call_id)
        assert call.idempotency_key == key and call.workspace_id == p.workspace_id
        assert supplied is None or supplied.id == call.id
        return call
    monkeypatch.setattr(subject, 'validate_call_approval', context)
    monkeypatch.setattr(subject.shared, '_call', call_lookup)
    monkeypatch.setattr(subject, '_producer', lambda *args: copy.deepcopy(s.producer))
    monkeypatch.setattr(subject, '_policy', lambda db, p, ctx, value, at: db.get(m.MonitorPolicy, s.policy_id))
    monkeypatch.setattr(subject, '_approval_proof', lambda *args: copy.deepcopy(s.approval_pin))
    monkeypatch.setattr(subject, '_tick_authority', lambda db, p, policy, install: p)
    monkeypatch.setattr(signal_dual_handoffs, 'prelock_task', lambda *args: None)
    def propose(db, p, policy_id, *, entity_ids, observation_request=None):
        assert policy_id == s.policy_id and observation_request is None
        s.proposals.append(copy.deepcopy(entity_ids))
        # This PG case proves durable consumption and order, not a new device
        # action. An actual no-new-acquisition check legitimately returns [].
        return []
    monkeypatch.setattr(perception, 'propose_acquisitions', propose)
    return s


def test_pg_install_consume_and_original_receipt_survive_separate_sessions(pg_scene):
    from sqlalchemy import func
    from sqlalchemy.orm import Session
    s = pg_scene
    with Session(s.bound) as db:
        before = {kind: db.scalar(select(func.count()).select_from(kind).where(kind.workspace_id == s.p.workspace_id))
            for kind in (m.Acquisition, m.CloudCall, m.Job, m.Invocation)}
        policy_before = copy.deepcopy(db.get(m.MonitorPolicy, s.policy_id).data)
        with pytest.raises(subject.shared.PendingService):
            subject.execute(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key, s.approval)
        db.commit()
    with Session(s.bound) as db:
        head, records, cycles = subject._checked(db, s.p, s.policy_id)
        assert head.version == 1 and len(records) == 1 and not cycles
        assert subject.consume_policy_tick(db, s.p, db.get(m.MonitorPolicy, s.policy_id)) is True
        db.commit()
    with Session(s.bound) as db:
        head, records, cycles = subject._checked(db, s.p, s.policy_id)
        assert head.version == 2 and len(cycles) == 1 and cycles[0].outcome == 'checked'
        first = subject.reconcile(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key,
            s.approval, db.get(m.Invocation, s.call_id))
        assert subject.consume_policy_tick(db, s.p, db.get(m.MonitorPolicy, s.policy_id)) is True
        assert db.get(m.MonitorPolicy, s.policy_id).data == policy_before
        db.commit()
    with Session(s.bound) as db:
        assert subject.reconcile(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key,
            s.approval, db.get(m.Invocation, s.call_id)) == first
        assert {kind: db.scalar(select(func.count()).select_from(kind).where(kind.workspace_id == s.p.workspace_id))
            for kind in before} == before
    assert s.proposals == [[s.objects[1], s.objects[0], s.objects[2]]]


def test_pg_missing_independent_install_audit_never_allows_legacy_fallback(pg_scene):
    from sqlalchemy.orm import Session
    s = pg_scene
    with Session(s.bound) as db:
        with pytest.raises(subject.shared.PendingService):
            subject.execute(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key, s.approval)
        db.commit()
    # Keep actual Head/Installation, remove only its independent install Audit.
    # Real FK parents remain intact; this models a damaged authority anchor.
    with Session(s.bound) as db:
        head, installs, cycles = subject._checked(db, s.p, s.policy_id)
        event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == subject.INSTALLED, m.Audit.object_id == installs[0].id))
        assert event is not None
        db.delete(event); db.commit()
    with Session(s.bound) as db:
        with pytest.raises(HTTPException): subject.consume_policy_tick(db, s.p, db.get(m.MonitorPolicy, s.policy_id))
        db.rollback()
    assert not s.proposals


def test_pg_replacement_and_stop_exact_nonce_survive_cold_sessions_without_new_proposal(pg_scene):
    """Actual ledger/IAM/SQL; adopted graph/175/producer remain explicit seams."""
    from hashlib import sha256
    from sqlalchemy import func
    from sqlalchemy.orm import Session
    s = pg_scene
    with Session(s.bound) as db:
        original_policy = copy.deepcopy(db.get(m.MonitorPolicy, s.policy_id).data)
        no_external_before = {kind: db.scalar(select(func.count()).select_from(kind).where(kind.workspace_id == s.p.workspace_id))
            for kind in (m.Acquisition, m.CloudCall)}
        with pytest.raises(subject.shared.PendingService):
            subject.execute(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key, s.approval)
        db.commit()
    with Session(s.bound) as db:
        assert subject.consume_policy_tick(db, s.p, db.get(m.MonitorPolicy, s.policy_id)) is True
        head, records, cycles = subject._checked(db, s.p, s.policy_id)
        original_record = copy.deepcopy(records[0].data)
        original_cycle = copy.deepcopy(cycles[0].data)
        original_call = copy.deepcopy(db.get(m.Invocation, s.call_id).data)
        old_call_id = s.call_id
        # A genuinely distinct persisted consumer Job/Invocation, not replay of
        # the first call. The graph/approval lookup is the fixture's named seam.
        old_job = db.get(m.Job, s.job_id)
        s.inputs = {**copy.deepcopy(s.inputs), 'expected_schedule_pin': subject.schedule_pin(records[0])}
        s.key = 'graph-schedule-replacement:' + str(uuid4())
        job = m.Job(workspace_id=s.p.workspace_id, kind=scope.KIND, idempotency_key=s.key,
            status='running', attempts=1, available_at=old_job.available_at,
            lease_token=str(uuid4()), lease_until=old_job.lease_until,
            data={**copy.deepcopy(old_job.data), 'invocation_key': s.key,
                'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs)})
        call = m.Invocation(workspace_id=s.p.workspace_id, capability_id=s.cap_id,
            idempotency_key=s.key, status='running', data={})
        db.add_all([job, call]); db.flush(); s.job_id, s.call_id = job.id, call.id
        with pytest.raises(subject.shared.PendingService):
            subject.execute(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key, s.approval)
        db.commit()
    path = '/api/workspace/monitor-schedules-v200/' + s.policy_id + '/stop'
    with Session(s.bound) as db:
        head, records, cycles = subject._checked(db, s.p, s.policy_id)
        assert len(records) == 2 and len(cycles) == 1
        new_record = next(row for row in records if row.id == head.data['active_installation_id'])
        assert new_record.id != original_record['id']
        stale, stale_raw = stop_body(s, original_record)
        with pytest.raises(HTTPException): subject.stop_schedule(db, s.p, s.policy_id, stale, stale_raw, path=path)
        assert not list(db.scalars(select(subject.StopCommand).where(subject.StopCommand.policy_id == s.policy_id)))
        body, raw = stop_body(s, new_record)
        result = subject.stop_schedule(db, s.p, s.policy_id, body, raw, path=path)
        assert result['status'] == 'cancelled' and result['device_execution_cancelled'] is False
        db.commit()
    with Session(s.bound) as db:
        read = subject.original_stop_request(db, s.p, s.policy_id, body['request_id'], sha256(raw).hexdigest(), path=path)
        assert read['found'] is True and read['result'] == result
        assert subject.stop_schedule(db, s.p, s.policy_id, body, raw, path=path) == result
        command = db.scalar(select(subject.StopCommand).where(subject.StopCommand.policy_id == s.policy_id,
            subject.StopCommand.request_id == body['request_id']))
        assert command.body_sha256 == sha256(raw).hexdigest()
        assert base64.b64decode(command.data['raw_base64'], validate=True) == raw
        changed = {**body, 'reason': '同号不同正文不能重放'}
        with pytest.raises(HTTPException):
            subject.stop_schedule(db, s.p, s.policy_id, changed, json.dumps(changed, ensure_ascii=False).encode(), path=path)
        assert subject.consume_policy_tick(db, s.p, db.get(m.MonitorPolicy, s.policy_id)) is True
        with pytest.raises(HTTPException):
            subject.reconcile(db, s.p, db.get(m.Capability, s.cap_id), s.inputs, s.key,
                s.approval, db.get(m.Invocation, s.call_id))
        head, records, cycles = subject._checked(db, s.p, s.policy_id)
        assert head.status == 'cancelled' and len(records) == 2 and len(cycles) == 1
        assert next(row.data for row in records if row.id == original_record['id']) == original_record
        assert cycles[0].data == original_cycle and db.get(m.Invocation, old_call_id).data == original_call
        assert db.get(m.MonitorPolicy, s.policy_id).data == original_policy
        assert {kind: db.scalar(select(func.count()).select_from(kind).where(kind.workspace_id == s.p.workspace_id))
            for kind in no_external_before} == no_external_before
        db.commit()
    assert len(s.proposals) == 1

