"""Actual ORM Event status projection only, no fabricated ACK or native proof."""
import copy
from uuid import uuid4
import pytest
from generic_agents import audio_streams as streams, models as m
from generic_agents.security import Principal


class ReceptionCatalog:
    def __init__(self, event, memories=None): self.event = event; self.memories = memories or []
    def scalars(self, query):
        entity = query.column_descriptions[0]['entity']
        if entity is m.Event: return [self.event]
        assert entity is m.Memory
        return self.memories


def scene(monkeypatch, *, status='blocked_delivery', marked=None):
    from generic_agents import instance_data_receipts
    actor = str(uuid4()); workspace = str(uuid4()); observation_id = str(uuid4())
    p = Principal(actor, workspace, 'viewer', 'reception-projection-test')
    row = m.Observation(id=observation_id, workspace_id=workspace, source_id=str(uuid4()),
        source_record_id='projection-unit', processing_version='original', status='processed', data={})
    event = m.Event(id=str(uuid4()), workspace_id=workspace, kind='observation.data',
        idempotency_key='data:' + row.id, status=status, received_at=None, data={'observation_id': row.id})
    memories = []
    if marked == 'observation': row.data = {'data_reception': {'not_a_receipt': True}}
    if marked == 'event': event.data = {**event.data, 'data_reception': {'not_a_receipt': True}}
    if marked == 'memory':
        memories = [m.Memory(id=str(uuid4()), workspace_id=workspace, status='candidate', text='incomplete',
            data={'type': 'observation_evidence', 'event_id': event.id})]
    def no_ack(*args): raise AssertionError('A nonreceived event must not be treated as ACK')
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', no_ack)
    return ReceptionCatalog(event, memories), p, row, event


def test_blocked_original_data_event_exposes_existing_recovery_state_without_mutation(monkeypatch):
    db, p, row, event = scene(monkeypatch)
    original = copy.deepcopy((row.data, event.data, event.status, event.received_at))
    value = streams._reception(db, p, row)
    assert value == {'state': 'blocked_integrity', 'event_id': event.id, 'memory_id': None, 'receipt': None,
        'integrity_issue': '原数据交接受阻，原模型结果保留；恢复依据后在交付恢复中重试'}
    assert (row.data, event.data, event.status, event.received_at) == original


def test_pending_data_event_remains_waiting_and_never_fabricates_receipt(monkeypatch):
    db, p, row, event = scene(monkeypatch, status='pending_delivery')
    value = streams._reception(db, p, row)
    assert value == {'state': 'waiting_data', 'event_id': event.id, 'memory_id': None,
        'receipt': None, 'integrity_issue': None}
    assert event.status == 'pending_delivery' and event.received_at is None


@pytest.mark.parametrize('marked', ['observation', 'event', 'memory'])
def test_partial_receipt_markers_are_rejected_before_simple_blocked_event_projection(monkeypatch, marked):
    db, p, row, event = scene(monkeypatch, marked=marked)
    value = streams._reception(db, p, row)
    assert value['state'] == 'blocked_integrity' and value['receipt'] is None and value['memory_id'] is None
    assert value['integrity_issue'] == '原音或独立接收记录无法核对，未把声音候选作为已接收数据'
    assert event.status == 'blocked_delivery' and event.received_at is None
