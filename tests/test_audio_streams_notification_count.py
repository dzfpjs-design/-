"""Notification count query layout, using actual ORM + producer proof factory.

This is an isolated SQL/proof contract test, not a PostgreSQL delivery journey.
"""
from uuid import uuid4
from sqlalchemy.dialects import postgresql
from generic_agents import models as m, audio_streams as streams, audio_stream_delivery as delivery
from generic_agents.security import Principal


class CountQuery:
    def __init__(self, p, stream_id, result):
        self.p = p; self.stream_id = stream_id; self.result = result; self.sql = None
    def scalar(self, query):
        self.sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        assert 'JOIN jobs ON' in self.sql
        assert m.Audit.__tablename__ + ".data ->> 'job_id'" in self.sql
        assert "jobs.data ->> 'stream_id'" in self.sql
        assert m.Audit.__tablename__ + ".data ->> 'stream_id'" not in self.sql
        assert m.Audit.__tablename__ + '.workspace_id = ' in self.sql and 'jobs.workspace_id = ' in self.sql
        assert self.p.workspace_id in self.sql and self.stream_id in self.sql
        assert delivery.NOTIFIED in self.sql and delivery.KIND in self.sql
        return self.result


def test_saved_notification_count_joins_real_producer_job_from_exact_issued_anchor():
    workspace = str(uuid4()); stream_id = str(uuid4()); actor = str(uuid4())
    p = Principal(actor, workspace, 'admin', 'notification-count-contract')
    job = m.Job(id=str(uuid4()), workspace_id=workspace, kind=delivery.KIND,
        idempotency_key=delivery.JOB_PREFIX + str(uuid4()), status='succeeded',
        data={'stream_id': stream_id, 'mode': 'received'})
    event = m.Event(id=str(uuid4()), workspace_id=workspace, kind='perception.audio_candidate',
        idempotency_key=delivery.CANDIDATE_PREFIX + str(uuid4()), status='received',
        data={'schema': delivery.CANDIDATE_SCHEMA, 'stream_id': stream_id})
    issued = m.Audit(id=str(uuid4()), workspace_id=workspace, actor_id=actor,
        action=delivery.NOTIFIED, object_id=event.id, data=delivery._event_anchor(event, job))
    assert set(issued.data) == {'schema', 'event_id', 'workspace_id', 'key', 'job_id', 'mode', 'payload_hash'}
    assert 'stream_id' not in issued.data and issued.data['job_id'] == job.id and job.data['stream_id'] == stream_id
    db = CountQuery(p, stream_id, 1)
    assert streams._notification_count(db, p, stream_id) == 1
    assert 'count(' + m.Audit.__tablename__ + '.id)' in db.sql


def test_empty_notification_inventory_is_zero_without_counting_unrelated_job_types():
    p = Principal(str(uuid4()), str(uuid4()), 'viewer', 'notification-count-contract')
    db = CountQuery(p, str(uuid4()), 0)
    assert streams._notification_count(db, p, db.stream_id) == 0
