"""Source-budget unit interfaces; no app, database, transport or clock waiting.

Rows and shared-video usage are explicit in-memory fixtures.  These tests do
not establish SQL isolation, real concurrent locks or device-side behavior.
"""
import copy
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest

from generic_agents import audio_stream_budget as subject
from generic_agents.contracts import digest


class MemoryDB:
    def __init__(self):
        self.streams = []; self.proofs = []; self.operations = []
        self.audio_open = self.video_open = self.continuous_open = None
        self.video_times = []; self.queries = []

    @staticmethod
    def entity(query):
        return query.column_descriptions[0]['entity'].__name__

    def scalars(self, query):
        self.queries.append(query)
        kind = self.entity(query)
        if kind == 'AudioStream': return list(self.streams)
        if kind == 'Audit': return list(self.proofs)
        raise AssertionError('unexpected unit-query entity: ' + kind)

    def scalar(self, query):
        self.queries.append(query)
        kind = self.entity(query)
        if kind == 'AudioStream': return self.audio_open
        if kind == 'StreamSession': return self.video_open
        if kind == 'ContinuousStream': return self.continuous_open
        raise AssertionError('unexpected unit scalar entity: ' + kind)

    def add(self, proof):
        self.operations.append('audit'); self.proofs.append(proof)

    def flush(self):
        self.operations.append('flush')


def controller(s, *, status='queued', generation=1, actor=None, workspace=None, channel=None, bounds=None):
    definition = {'binding': {'binding_hash': 'a' * 64, 'read_bounds': bounds or {
        'daily_limit': 10, 'cooldown_minutes': 0, 'max_read_seconds': 60}}}
    return SimpleNamespace(id=str(uuid4()), workspace_id=workspace or s.p.workspace_id,
        actor_id=actor or s.p.user_id, channel_id=channel or s.channel.id, status=status,
        data={'definition': definition, 'definition_hash': digest(definition),
            'generation': generation, 'read_reservations': []})


def reservation(s, stream, *, generation=1, connection='connection-A', when=None, seconds=10):
    stamp = when or s.current
    value = {'schema': subject.SCHEMA, 'controller_id': stream.id, 'workspace_id': stream.workspace_id,
        'actor_id': stream.actor_id, 'channel_id': stream.channel_id, 'source_key': s.key,
        'generation': generation, 'connection_id': connection, 'requested_seconds': seconds,
        'definition_hash': stream.data['definition_hash'],
        'binding_hash': stream.data['definition']['binding']['binding_hash'], 'reserved_at': stamp.isoformat(),
        'usage_before': {'used': 1, 'last_read_at': None}}
    receipt = {**value, 'receipt_hash': digest(value)}
    proof = SimpleNamespace(action=subject.ACTION, object_id=stream.id, workspace_id=stream.workspace_id,
        actor_id=stream.actor_id, data=copy.deepcopy(receipt), created_at=stamp)
    stream.data['read_reservations'].append(receipt); s.db.proofs.append(proof)
    return receipt, proof


@pytest.fixture
def scene(monkeypatch):
    import generic_agents
    s = SimpleNamespace(db=MemoryDB(), p=SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4())),
        channel=SimpleNamespace(id=str(uuid4())), sibling=SimpleNamespace(id=str(uuid4())), gateway=SimpleNamespace(id=str(uuid4())),
        current=datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc), key='physical-source-fixture')
    monkeypatch.setattr(subject, '_source', lambda db, channel, gateway: (s.key, [s.channel, s.sibling]))
    class BudgetWait(HTTPException):
        def __init__(self, reason, retry_at):
            super().__init__(409, reason); self.retry_at = retry_at
    def usage(db, channel, gateway, current):
        db.operations.append('shared_usage')
        times = [*db.video_times, *subject.read_times(db, channel, gateway, current)]
        day = current.astimezone(ZoneInfo('Asia/Shanghai')).date()
        return {'used': sum(stamp.astimezone(ZoneInfo('Asia/Shanghai')).date() == day for stamp in times),
            'last_read_at': max(times).isoformat() if times else None}
    video = SimpleNamespace(usage=usage, ReadBudgetWait=BudgetWait, CHINA=ZoneInfo('Asia/Shanghai'))
    media = SimpleNamespace(node_lock=lambda db: db.operations.append('node_lock'), clock=lambda db: s.current)
    for name, module in [('video_read_boundary', video), ('media_monitor', media)]:
        monkeypatch.setitem(sys.modules, 'generic_agents.' + name, module)
        monkeypatch.setattr(generic_agents, name, module, raising=False)
    s.wait = BudgetWait
    return s


def test_unfrozen_controller_with_no_read_receipts_cannot_poison_existing_history(scene):
    s = scene; original = controller(s, status='failed'); reservation(s, original)
    unstarted = SimpleNamespace(id=str(uuid4()), channel_id=s.sibling.id, data={'read_reservations': []})
    s.db.streams = [unstarted, original]
    assert subject.read_times(s.db, s.channel, s.gateway, s.current) == [s.current]
    assert 'definition' not in unstarted.data


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'stopped', 'completed'])
def test_terminal_or_previous_generation_attempts_still_consume_shared_read_history(scene, status):
    s = scene; row = controller(s, status=status, generation=3); reservation(s, row, generation=1)
    s.db.streams = [row]
    assert subject.read_times(s.db, s.channel, s.gateway, s.current) == [s.current]


@pytest.mark.parametrize('fault', ['missing_audit', 'orphan_audit', 'duplicate_audit', 'duplicate_receipt',
    'audit_actor', 'audit_workspace', 'audit_time', 'audit_boolean', 'bad_receipt_hash'])
def test_independent_reservation_inventory_and_exact_payload_cannot_be_repaired_or_equated(scene, fault):
    s = scene; row = controller(s); receipt, proof = reservation(s, row); s.db.streams = [row]
    if fault == 'missing_audit': s.db.proofs.clear()
    if fault == 'orphan_audit': row.data['read_reservations'].clear()
    if fault == 'duplicate_audit': s.db.proofs.append(copy.deepcopy(proof))
    if fault == 'duplicate_receipt': row.data['read_reservations'].append(copy.deepcopy(receipt))
    if fault == 'audit_actor': proof.actor_id = str(uuid4())
    if fault == 'audit_workspace': proof.workspace_id = str(uuid4())
    if fault == 'audit_time': proof.created_at += timedelta(microseconds=1)
    if fault == 'audit_boolean': proof.data['usage_before']['used'] = True
    if fault == 'bad_receipt_hash': receipt['receipt_hash'] = '0' * 64
    before = copy.deepcopy((row.data, [vars(item) for item in s.db.proofs]))
    with pytest.raises(HTTPException) as caught:
        subject.read_times(s.db, s.channel, s.gateway, s.current)
    assert caught.value.status_code == 409
    assert (row.data, [vars(item) for item in s.db.proofs]) == before


def test_guard_does_not_treat_a_boolean_changed_stored_receipt_as_the_same_original(scene):
    s = scene; row = controller(s); receipt, _ = reservation(s, row)
    incoming = copy.deepcopy(receipt)
    row.data['read_reservations'][0]['usage_before']['used'] = True
    with pytest.raises(HTTPException):
        subject.guard(s.db, s.p, row, incoming, s.channel, s.gateway)


def test_new_attempt_is_anchored_before_transport_and_exact_replay_does_not_spend_again(scene):
    s = scene; row = controller(s); s.db.streams = [row]
    receipt = subject.reserve(s.db, s.p, row, s.channel, s.gateway, 10, 'connection-A', 1)
    assert s.db.operations[0] == 'node_lock'
    assert len(row.data['read_reservations']) == len(s.db.proofs) == 1
    proof = s.db.proofs[0]
    assert proof.action == subject.ACTION and proof.data == receipt and proof.created_at == s.current
    original = copy.deepcopy(receipt); audit_count = len(s.db.proofs)
    replay = subject.reserve(s.db, s.p, row, s.channel, s.gateway, 10, 'connection-A', 1)
    assert replay == original and len(s.db.proofs) == audit_count
    assert row.data['read_reservations'] == [original]
    for seconds, generation in [(11, 1), (10, 2)]:
        with pytest.raises(HTTPException):
            subject.reserve(s.db, s.p, row, s.channel, s.gateway, seconds, 'connection-A', generation)
    assert row.data['read_reservations'] == [original] and len(s.db.proofs) == audit_count


@pytest.mark.parametrize('fault', ['actor', 'workspace', 'channel', 'generation', 'terminal'])
def test_scoped_actor_controller_and_generation_mismatch_never_add_a_reservation(scene, fault):
    s = scene; row = controller(s); s.db.streams = [row]
    if fault == 'actor': row.actor_id = str(uuid4())
    if fault == 'workspace': row.workspace_id = str(uuid4())
    if fault == 'channel': row.channel_id = str(uuid4())
    if fault == 'generation': row.data['generation'] = 2
    if fault == 'terminal': row.status = 'cancelled'
    with pytest.raises(HTTPException):
        subject.reserve(s.db, s.p, row, s.channel, s.gateway, 10, 'connection-A', 1)
    assert not row.data['read_reservations'] and not s.db.proofs


@pytest.mark.parametrize('occupied', ['audio_open', 'video_open', 'continuous_open'])
def test_shared_source_occupancy_blocks_new_attempt_before_any_budget_receipt(scene, occupied):
    s = scene; row = controller(s); s.db.streams = [row]
    setattr(s.db, occupied, str(uuid4()))
    with pytest.raises(s.wait) as caught:
        subject.reserve(s.db, s.p, row, s.channel, s.gateway, 10, 'connection-A', 1)
    assert caught.value.retry_at == s.current + timedelta(seconds=5)
    assert s.db.operations == ['node_lock'] and not row.data['read_reservations'] and not s.db.proofs


def test_sibling_physical_channel_and_failed_video_attempts_share_the_daily_limit(scene):
    s = scene; old = controller(s, status='failed', channel=s.sibling.id)
    reservation(s, old, when=s.current - timedelta(minutes=2))
    s.db.video_times = [s.current - timedelta(minutes=1)]
    row = controller(s, bounds={'daily_limit': 2, 'cooldown_minutes': 0, 'max_read_seconds': 60})
    s.db.streams = [old, row]
    with pytest.raises(s.wait) as caught:
        subject.reserve(s.db, s.p, row, s.channel, s.gateway, 10, 'connection-A', 1)
    expected = s.current.astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    assert caught.value.retry_at == expected and len(s.db.proofs) == 1
    assert row.data['read_reservations'] == []


def test_switching_from_video_to_audio_does_not_reset_source_cooldown(scene):
    s = scene; last = s.current - timedelta(seconds=10); s.db.video_times = [last]
    row = controller(s, bounds={'daily_limit': None, 'cooldown_minutes': 1, 'max_read_seconds': 60})
    s.db.streams = [row]
    with pytest.raises(s.wait) as caught:
        subject.reserve(s.db, s.p, row, s.channel, s.gateway, 10, 'connection-A', 1)
    assert caught.value.retry_at == last + timedelta(minutes=1)
    assert not row.data['read_reservations'] and not s.db.proofs


def test_device_duration_limit_is_not_replaced_by_the_controller_request(scene):
    s = scene; row = controller(s, bounds={'daily_limit': None, 'cooldown_minutes': 0, 'max_read_seconds': 5})
    s.db.streams = [row]
    with pytest.raises(HTTPException):
        subject.reserve(s.db, s.p, row, s.channel, s.gateway, 6, 'connection-A', 1)
    assert not row.data['read_reservations'] and not s.db.proofs
