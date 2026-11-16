"""Pure coordinator control-flow tests; source and authority IO are seams."""
import copy
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import workspace_monitoring as s
from generic_agents import workflow_monitor_schedule_v200 as monitor
from generic_agents.contracts import digest
from generic_agents.models import Acquisition, Audit, Job, MonitorPolicy, ObservationPlan
from generic_agents.monitor_scheduling_models_v253 import (
    MonitorSchedulingHeadV253 as ControlHead, MonitorSchedulingCommandV253 as ControlCommand)

AT = datetime(2026, 10, 25, tzinfo=timezone.utc)


def policy():
    return NS(id=str(uuid4()), workspace_id=str(uuid4()), approved_by=str(uuid4()),
        status='active', version=2, created_at=AT-timedelta(hours=1),
        data={'approved_hash': 'a'*64, 'cooldown_seconds': 600})


def head(*, status='active', restore=False, due=AT):
    return NS(id=str(uuid4()), version=1, record_hash='b'*64, status=status,
        next_due=due if status == 'active' else None, created_at=AT-timedelta(hours=1),
        data={'restore_legacy': restore, 'cycle_ids': []})


def test_original_active_schedule_due_is_stable_and_future_due_does_not_queue():
    p = policy(); h = head(due=AT+timedelta(seconds=60))
    assert s.due_hint(p, h, None, AT) is None
    first = s.due_hint(p, h, None, AT+timedelta(seconds=61))
    failed = NS(status='failed', data={}, created_at=AT)
    again = s.due_hint(p, h, failed, AT+timedelta(hours=1))
    assert first == again  # same failed original due/key, not a retry nonce
    assert s.request_key(s._request(p, first)) == s.request_key(s._request(p, again))
    h.version += 1; h.record_hash = 'c'*64; h.next_due += timedelta(seconds=60)
    changed = s.due_hint(p, h, failed, AT+timedelta(hours=1))
    assert s.request_key(s._request(p, changed)) != s.request_key(s._request(p, first))


def test_legacy_checks_follow_original_interval_and_failed_context_does_not_repeat():
    p = policy(); first = s.due_hint(p, None, None, AT)
    value = {'state': 'checked', 'checked_at': AT.isoformat()}
    value['record_hash'] = digest(value)
    original = NS(status='succeeded', data={s.KEY: s._request(p, first), s.RESULT_KEY: value})
    assert s.due_hint(p, None, original, AT+timedelta(seconds=599)) is None
    second = s.due_hint(p, None, original, AT+timedelta(seconds=600))
    assert second['due_at'] == (AT+timedelta(seconds=600)).isoformat()
    assert s.request_key(s._request(p, second)) != s.request_key(s._request(p, first))
    original.status = 'failed'
    assert s.due_hint(p, None, original, AT+timedelta(days=1)) is None
    p.version += 1
    assert s.due_hint(p, None, original, AT+timedelta(days=1)) is not None


@pytest.mark.parametrize('status,restore,mode', [('cancelled', False, 'suppressed'),
    ('blocked', False, 'suppressed'), ('expired', False, 'legacy'), ('cancelled', True, 'legacy')])
def test_raw_head_only_selects_hint_and_never_grants_legacy(status, restore, mode):
    hint = s.due_hint(policy(), head(status=status, restore=restore), None, AT)
    assert hint['mode'] == mode


@pytest.mark.parametrize('identity', [None, '', 'ALL', '12345678-1234-4234-8234-123456789ABc'])
def test_workspace_required_before_any_engine_or_stop_access(monkeypatch, identity):
    monkeypatch.setattr(s, 'engine', lambda: pytest.fail('invalid workspace connected'))
    with pytest.raises(ValueError): s.schedule_once(identity)


def test_stop_prevents_root_from_connecting(monkeypatch):
    stop = Event(); stop.set()
    monkeypatch.setattr(s, 'engine', lambda: pytest.fail('stopped root connected'))
    assert s.schedule_once(str(uuid4()), _stop_event=stop) == 0


class IO:
    def __init__(self, policy): self.policy = policy; self.events = []; self.control_reads = []
    def scalar(self, statement):
        description = statement.column_descriptions[0]
        if description.get('entity') is MonitorPolicy: return self.policy
        if description.get('name') == 'clock_timestamp' and description.get('entity') is None: return AT
        if description.get('name') == 'id' and description.get('entity') in (ObservationPlan, Acquisition, Audit, Job):
            assert statement._limit_clause is not None and statement._limit_clause.value == 1
            self.control_reads.append(description['entity'])
            return None  # Exact empty bounded independent-control trace probe.
        raise AssertionError('Unexpected scalar IO; no generic timestamp/count fallback')
    def scalars(self, statement):
        entity = statement.column_descriptions[0].get('entity')
        if entity in (ControlHead, ControlCommand):
            self.control_reads.append(entity)
            return []  # Explicitly no V253 control, not a bypassed gate.
        assert entity is Audit
        action = next(clause.right.value for clause in statement._where_criteria
            if getattr(getattr(clause, 'left', None), 'key', None) == 'action')
        return [event for event in self.events if event.action == action]
    def add(self, event):
        event.id = str(uuid4()); self.events.append(event)
    def flush(self): pass


@pytest.fixture
def scene(monkeypatch):
    original = policy(); db = IO(original)
    p = NS(user_id=original.approved_by, workspace_id=original.workspace_id, role='admin')
    hint = s.due_hint(original, None, None, AT)
    pin = s._request(original, hint)
    job = NS(id=str(uuid4()), workspace_id=p.workspace_id, kind=s.KIND,
        idempotency_key=s.request_key(pin), data={'actor_id': p.user_id, 'policy_id': original.id,
            s.KEY: pin, 'request_hash': digest(pin)})
    db.events.append(NS(id=str(uuid4()), actor_id=p.user_id, action=s.QUEUED,
        data=s._queue_anchor(job.id, pin)))
    monkeypatch.setattr(s, '_head', lambda *_args: None)
    return NS(db=db, p=p, job=job, policy=original, consumes=[], proposals=[])


def test_only_real_consumer_false_permits_legacy_and_result_recovery_never_repeats(scene, monkeypatch):
    from generic_agents import perception
    monkeypatch.setattr(monitor, 'consume_policy_tick', lambda *_args, **_kw: scene.consumes.append('checked') or False)
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *_args: scene.proposals.append('legacy') or [])
    first = s.process(scene.db, scene.p, scene.job)
    assert first['state'] == 'checked' and first['physical_state_verified'] is False
    assert scene.consumes == ['checked'] and scene.proposals == ['legacy']
    assert {ControlHead, ControlCommand, ObservationPlan, Acquisition, Audit, Job}.issubset(set(scene.db.control_reads))
    control_read_count = len(scene.db.control_reads)
    scene.policy.status = 'paused'  # historical recovery cannot reauthorize it
    recovered = s.process(scene.db, scene.p, scene.job)
    assert recovered == first and scene.consumes == ['checked'] and scene.proposals == ['legacy']
    assert len(scene.db.control_reads) == control_read_count


@pytest.mark.parametrize('status,state', [('cancelled', 'cancelled'), ('blocked', 'blocked'), ('expired', 'blocked')])
def test_stopped_consumed_head_never_falls_back(scene, monkeypatch, status, state):
    from generic_agents import perception
    current = head(status=status)
    monkeypatch.setattr(monitor, 'consume_policy_tick', lambda *_args, **_kw: True)
    monkeypatch.setattr(monitor, '_checked', lambda *_args: (current, [], []))
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *_args: pytest.fail('suppressed head used legacy'))
    assert s.process(scene.db, scene.p, scene.job)['state'] == state


@pytest.mark.parametrize('change', ['workspace', 'actor', 'hash', 'policy_version', 'paused', 'queue_audit', 'key'])
def test_current_scope_or_queue_tampering_blocks_before_consumer(scene, monkeypatch, change):
    from generic_agents.workflow_abilities import WorkflowBlocked
    if change == 'workspace': scene.p.workspace_id = str(uuid4())
    if change == 'actor': scene.policy.approved_by = str(uuid4())
    if change == 'hash': scene.policy.data['approved_hash'] = 'c'*64
    if change == 'policy_version': scene.policy.version += 1
    if change == 'paused': scene.policy.status = 'paused'
    if change == 'queue_audit': scene.db.events.clear()
    if change == 'key': scene.job.idempotency_key = 'another-key'
    monkeypatch.setattr(monitor, 'consume_policy_tick', lambda *_args, **_kw: pytest.fail('changed authority consumed'))
    with pytest.raises(WorkflowBlocked): s.process(scene.db, scene.p, scene.job)


def test_original_consumer_exception_is_visible_and_never_legacy_fallback(scene, monkeypatch):
    from generic_agents import perception
    from generic_agents.workflow_abilities import WorkflowBlocked
    def invalid(*_args, **_kw): raise HTTPException(409, '原调度独立审计缺失')
    monkeypatch.setattr(monitor, 'consume_policy_tick', invalid)
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *_args: pytest.fail('failure used legacy'))
    with pytest.raises(WorkflowBlocked, match='独立审计缺失'):
        s.process(scene.db, scene.p, scene.job)


def test_saved_result_needs_independent_original_audit(scene, monkeypatch):
    from generic_agents import perception
    from generic_agents.workflow_abilities import WorkflowBlocked
    monkeypatch.setattr(monitor, 'consume_policy_tick', lambda *_args, **_kw: False)
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *_args: [])
    s.process(scene.db, scene.p, scene.job)
    scene.db.events = [event for event in scene.db.events if event.action != s.CHECKED]
    with pytest.raises(WorkflowBlocked): s.process(scene.db, scene.p, scene.job)
