"""Real PG/local extraction/independent reception at a frozen knowledge cut.

The test constructs the private clock after installing a real lease fence; it
does not claim an adopted V288 creator, GoalEvaluation builder or field sensor.
No receiver, custody verifier, query engine, time filter or IAM is replaced.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import goal_evaluation_query_v288 as subject, leases, services
from generic_agents.contracts import EvidenceRequirement
from generic_agents.db import engine
from generic_agents.models import AuthSession, Event, Job, Membership, Memory, Observation
from generic_agents.security import Principal
from generic_agents.temporal import resolve
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_state_receipt_guard_pg_v245 import scene, receive


def execute_at(db, p, scene, job, cut, *, cases=False):
    fields = ['公开登记值']; requirement = EvidenceRequirement.model_validate({
        'source': 'observations', 'minimum_verification': 'unverified_extraction',
        'time': {'mode': 'latest', 'max_age_seconds': 600, 'timezone': 'Asia/Shanghai'}}).model_dump(mode='json')
    window = resolve(requirement['time'], reference=cut)
    clock = subject._Clock(subject._WITNESS, db, p.workspace_id, p.user_id,
        job.id, job.lease_token, str(uuid4()), str(uuid4()), subject.hashed({}),
        (scene.entity['id'],), tuple(fields), subject.hashed(requirement), canonical_json(window), cut)
    return services.query_evidence(db, p, [scene.entity['id']], fields,
        requirement=requirement, fixed_window=window, route_key=clock.query_id,
        learning=False, include_trusted_cases=cases, _goal_evaluation_cut=clock)


@pytest.mark.parametrize('at_receipt', [False, True])
def test_real_ack_is_visible_only_at_or_after_its_recorded_knowledge_time(client, scene, at_receipt):
    with Session(engine()) as db:
        before = db.scalar(select(func.clock_timestamp()))
    memory_id = receive(scene)
    with Session(engine()) as db:
        obs = db.get(Observation, scene.observation['id'])
        event = db.get(Event, scene.event_id); memory = db.get(Memory, memory_id)
        assert before < event.received_at
        cut = max(event.received_at, event.created_at, memory.created_at, obs.created_at) if at_receipt else before
        original = {name: copy.deepcopy(row.data) for name, row in (
            ('observation', obs), ('event', event), ('memory', memory))}
        member = db.scalar(select(Membership).where(Membership.workspace_id == obs.workspace_id, Membership.role == 'admin'))
        assert member is not None
        auth = db.scalar(select(AuthSession).where(AuthSession.user_id == member.user_id, AuthSession.revoked.is_(False)))
        assert auth is not None
        p = Principal(user_id=member.user_id, workspace_id=member.workspace_id, role=member.role, session_id=auth.id)
        job = Job(workspace_id=p.workspace_id, kind='v288_cut_pg_test_only', idempotency_key='v288-cut-test:' + str(uuid4()), status='running',
            lease_token=str(uuid4()), lease_until=db.scalar(select(func.clock_timestamp())) + timedelta(minutes=3),
            data={})
        db.add(job); db.commit(); job_id = job.id; token = job.lease_token
    with Session(engine()) as db:
        leases.attach(db, job_id, token)
        actual = db.get(Job, job_id)
        report = execute_at(db, p, scene, actual, cut)
        assert report['evaluated_at'] == cut.isoformat()
        assert report['resolved_time']['reference_at'] == cut.isoformat()
        if at_receipt:
            assert report['status'] == 'sufficient'
            assert len(report['observation_evidence']) == 1
            item = report['observation_evidence'][0]
            assert item['attributes'] == {'公开登记值': 27.5}
            assert item['data_reception']['event_id'] == scene.event_id
            assert item['goal_evaluation_knowledge_cut_v288']['memory_id'] == memory_id
        else:
            assert report['status'] == 'insufficient' and report['observation_evidence'] == []
            assert any(item.get('status') == 'not_received_at_evaluation_cut' for item in report['excluded_evidence'])
        assert not db.new and not db.dirty and not db.deleted
    with Session(engine()) as db:
        assert db.get(Observation, scene.observation['id']).data == original['observation']
        assert db.get(Event, scene.event_id).data == original['event']
        assert db.get(Memory, memory_id).data == original['memory']


def test_private_clock_without_actual_fence_cannot_read_even_real_received_data(client, scene):
    receive(scene)
    with Session(engine()) as db:
        obs = db.get(Observation, scene.observation['id'])
        member = db.scalar(select(Membership).where(Membership.workspace_id == obs.workspace_id, Membership.role == 'admin'))
        assert member is not None
        auth = db.scalar(select(AuthSession).where(AuthSession.user_id == member.user_id, AuthSession.revoked.is_(False)))
        assert auth is not None
        p = Principal(user_id=member.user_id, workspace_id=member.workspace_id, role=member.role, session_id=auth.id)
        job = SimpleNamespace(id=str(uuid4()), lease_token=str(uuid4()))
        db.info['worker_lease'] = {'job_id': job.id, 'lease_token': job.lease_token}
        with pytest.raises(HTTPException) as failure:
            execute_at(db, p, scene, job, db.scalar(select(func.clock_timestamp())))
        assert failure.value.status_code == 409
