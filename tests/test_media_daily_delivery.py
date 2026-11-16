"""Actual frozen/ACK logic at explicit SQL/file boundaries; no real inference."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import BooleanClauseList

from generic_agents import media_daily_delivery as s
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.security import Principal

AT = datetime(2026, 10, 25, tzinfo=timezone.utc)


class IO:
    def __init__(self):
        self.rows = {}; self.reads = []

    def put(self, row):
        self.rows.setdefault(type(row), []).append(row)

    def add(self, row):
        if row.id is None: row.id = str(uuid4())
        if row.created_at is None: row.created_at = AT
        if row.version is None: row.version = 1
        self.put(row)

    def flush(self):
        pass

    def match(self, row, clause):
        if isinstance(clause, BooleanClauseList):
            values = [self.match(row, child) for child in clause.clauses]
            return all(values) if clause.operator.__name__ == 'and_' else any(values)
        left = clause.left
        if hasattr(left, 'right') and getattr(left.right, 'value', None) is not None:
            actual = row.data.get(left.right.value)
        else:
            actual = getattr(row, left.key)
        expected = clause.right.value
        return actual in expected if clause.operator.__name__ == 'in_op' else actual == expected

    def scalars(self, statement):
        entity = statement.column_descriptions[0]['entity']; self.reads.append(entity)
        values = [row for row in self.rows.get(entity, [])
            if statement.whereclause is None or self.match(row, statement.whereclause)]
        return values[:statement._limit_clause.value] if statement._limit_clause is not None else values

    def scalar(self, statement):
        values = self.scalars(statement)
        if not values: return None
        return values[0].id if statement.column_descriptions[0]['name'] == 'id' else values[0]


@pytest.fixture
def scene(monkeypatch):
    db = IO(); p = Principal(str(uuid4()), str(uuid4()), 'admin', 'explicit-io-boundary')
    user = m.User(id=p.user_id, username='public-media-source', password_hash='not-authentication', active=True)
    ev = m.Evidence(id=str(uuid4()), workspace_id=p.workspace_id, sha256='a'*64, size_bytes=3,
        storage_key=p.workspace_id+'/'+'a'*64+'.bin', media_type='audio/wav', data={}, version=1)
    row = m.AudioRun(id=str(uuid4()), workspace_id=p.workspace_id, evidence_id=ev.id,
        request_id=str(uuid4()), status='succeeded', version=1,
        data={'input_sha256': ev.sha256, 'evidence_id': ev.id, 'result_hash': 'original-result'})
    job = m.Job(id=str(uuid4()), workspace_id=p.workspace_id, kind='transcribe_audio',
        status='succeeded', idempotency_key='original-audio-job', version=1,
        data={'actor_id': p.user_id, 'run_id': row.id})
    row.data = {**row.data, 'job_id': job.id}
    source_audit = m.Audit(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id,
        action='audio.queued', object_id=row.id, data={'input_sha256': ev.sha256}, version=1, created_at=AT)
    daily = m.AudioDaily(id=str(uuid4()), workspace_id=p.workspace_id, received_day='2026-10-25', version=1)
    value = {'received_day': daily.received_day, 'records': [{'id': row.id, 'kind': 'transcription',
        'source_version': 1, 'input_sha256': ev.sha256}], 'not_authoritative': True, 'cloud_calls': 0}
    memory = m.Memory(id=str(uuid4()), workspace_id=p.workspace_id, status='candidate',
        text='producer candidate, not ACK', data={**value, 'type':'audio_daily_summary',
            'daily_id': daily.id, 'receipt_hash': digest(value)}, version=1)
    daily.data = {**value, 'receipt_hash': digest(value), 'data_memory_id': memory.id, 'updated_at': AT.isoformat()}
    for item in [user, ev, row, job, source_audit, daily, memory]: db.put(item)
    from generic_agents import geo_storage, relations
    missing = {'file': False}

    def original(db, p, identity):
        if missing['file']: raise HTTPException(409, 'synthetic original unavailable')
        assert identity == ev.id and p.workspace_id == ev.workspace_id
        return ev, 'explicit-file-check-seam'

    monkeypatch.setattr(geo_storage, 'path_for', original)
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *_: None)
    return NS(db=db, p=p, source=row, job=job, daily=daily, producer=memory, evidence=ev,
        source_audit=source_audit, missing=missing)


def test_publication_is_frozen_and_not_a_data_ack(scene):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio')
    assert event.status == 'pending_delivery' and event.attempts == 0
    assert s.checked(x.db, x.p, event)['state']['memory_id'] is None
    assert len(x.db.rows[m.Memory]) == 1
    assert s.publish_revision(x.db, x.daily, 'audio') is event
    assert len(x.db.rows[m.Event]) == len([a for a in x.db.rows[m.Audit] if a.action == s.PUBLICATION]) == 1


def test_independent_receiver_has_exact_one_memory_and_ack_and_no_repeat(scene):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio'); frozen = copy.deepcopy(event.data)
    assert s.receive(x.db, x.p, event) is True
    proof = s.checked(x.db, x.p, event)
    assert event.status == 'received' and event.attempts == 1 and proof['state']['memory_id']
    assert len(x.db.rows[m.Memory]) == 2 and event.data == frozen
    assert s.receive(x.db, x.p, event) is False
    assert len(x.db.rows[m.Memory]) == 2 and event.attempts == 1


def test_blocked_never_auto_resets_and_explicit_retry_preserves_event_and_payload(scene):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio'); frozen = copy.deepcopy(event.data)
    x.missing['file'] = True
    assert not s.receive(x.db, x.p, event) and event.status == 'blocked_delivery'
    assert len(x.db.rows[m.Memory]) == 1 and event.attempts == 1
    x.missing['file'] = False
    assert not s.receive(x.db, x.p, event) and event.status == 'blocked_delivery'
    body = s.Retry(request_id=uuid4(), expected_version=event.version,
        payload_hash=event.data['payload_hash'], reason='原文件已恢复，仅重试原摘要接收')
    assert s.retry(x.db, x.p, event, body)['status'] == 'pending_delivery'
    assert s.retry(x.db, x.p, event, body)['status'] == 'pending_delivery'
    assert s.receive(x.db, x.p, event) and event.data == frozen
    assert event.attempts == 2 and len(x.db.rows[m.Memory]) == 2
    assert s.retry(x.db, x.p, event, body)['status'] == 'received'


@pytest.mark.parametrize('fault', ['cas', 'hash', 'actor', 'body', 'file'])
def test_retry_does_not_launder_changed_request_or_unrestored_original(scene, fault):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio')
    x.missing['file'] = True; s.receive(x.db, x.p, event); x.missing['file'] = False
    body = s.Retry(request_id=uuid4(), expected_version=event.version,
        payload_hash=event.data['payload_hash'], reason='恢复原件')
    if fault == 'cas': body.expected_version += 1
    elif fault == 'hash': body.payload_hash = 'f'*64
    elif fault == 'file': x.missing['file'] = True
    else:
        s.retry(x.db, x.p, event, body)
        if fault == 'actor': x.p = Principal(str(uuid4()), x.p.workspace_id, 'admin', 'other')
        else: body.reason = 'another body'
    before = copy.deepcopy((event.status, event.version, event.data))
    with pytest.raises(HTTPException): s.retry(x.db, x.p, event, body)
    assert (event.status, event.version, event.data) == before


@pytest.mark.parametrize('fault', ['payload', 'publication_missing', 'publication_actor', 'source_audit',
    'source_job_actor', 'source_job_workspace', 'source_file_identity', 'producer_memory'])
def test_independent_source_payload_and_audits_cannot_be_replaced(scene, fault):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio')
    if fault == 'payload': event.data = {**event.data, 'day': '2020-01-01'}
    elif fault == 'publication_missing': x.db.rows[m.Audit] = [x.source_audit]
    elif fault == 'publication_actor': x.db.rows[m.Audit][-1].actor_id = str(uuid4())
    elif fault == 'source_audit': x.source_audit.data = {'changed': True}
    elif fault == 'source_job_actor': x.job.data = {**x.job.data, 'actor_id': str(uuid4())}
    elif fault == 'source_job_workspace': x.job.workspace_id = str(uuid4())
    elif fault == 'source_file_identity': x.source.data = {**x.source.data, 'input_sha256': 'b'*64}
    else: x.producer.data = {**x.producer.data, 'fabricated': True}
    try: received = s.receive(x.db, x.p, event)
    except HTTPException: received = False
    assert not received and event.status != 'received' and len(x.db.rows[m.Memory]) == 1


def test_late_revision_does_not_replace_old_revision_or_reject_normal_review_version(scene):
    x = scene; first = s.publish_revision(x.db, x.daily, 'audio'); frozen = copy.deepcopy(first.data)
    x.source.version += 1; x.source.data = {**x.source.data, 'review': {'note':'later human review'}}
    x.daily.version += 1
    value = s.summary_value(x.daily); value['records'][0]['source_version'] = 2
    memory = m.Memory(workspace_id=x.p.workspace_id, status='candidate', text='new producer revision',
        data={**value, 'type':'audio_daily_summary', 'daily_id':x.daily.id, 'receipt_hash':digest(value)})
    x.db.add(memory)
    x.daily.data = {**value, 'receipt_hash':digest(value), 'data_memory_id':memory.id}
    second = s.publish_revision(x.db, x.daily, 'audio')
    assert first.id != second.id and first.data == frozen
    assert s.receive(x.db, x.p, first) and s.receive(x.db, x.p, second)
    assert first.data['daily_revision'] == 1 and second.data['daily_revision'] == 2
    assert len([row for row in x.db.rows[m.Memory] if row.data.get('type') == 'media_daily_received']) == 2


def test_past_actor_may_be_inactive_and_normal_review_does_not_erase_historical_ack(scene):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio')
    x.db.rows[m.User][0].active = False
    x.source.version += 1; x.source.status = 'succeeded'
    assert s.receive(x.db, x.p, event)
    assert s.checked(x.db, x.p, event)['state']['status'] == 'received'


def test_received_memory_and_state_audit_are_both_required(scene):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio'); s.receive(x.db, x.p, event)
    memory = x.db.rows[m.Memory][-1]; memory.data = {**memory.data, 'invented': True}
    with pytest.raises(HTTPException): s.checked(x.db, x.p, event)


def test_cross_workspace_cannot_read_or_receive_the_original(scene):
    x = scene; event = s.publish_revision(x.db, x.daily, 'audio')
    other = Principal(x.p.user_id, str(uuid4()), 'admin', 'foreign')
    with pytest.raises(HTTPException): s.receive(x.db, other, event)
    assert event.status == 'pending_delivery' and len(x.db.rows[m.Memory]) == 1
