"""Actual generic API/worker denial for restricted evaluation query records.

Markers and the retained audit below deliberately do not establish a valid
evaluation. They must restrict mutation without granting readable evidence.
No adopted creator, field measurement or successful evaluation is claimed.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import goal_evaluation_query_v288 as queries
from generic_agents.db import engine
from generic_agents.models import Audit, DataRun, Job, Membership
from generic_agents.worker import process_once


@pytest.mark.parametrize('marker_retained', [False, True])
@pytest.mark.parametrize('depth', [0, 2])
def test_normal_generic_api_and_worker_cannot_modify_or_reexecute_a_restricted_query(client, marker_retained, depth):
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.role == 'admin'))
        assert member is not None
        row = DataRun(workspace_id=member.workspace_id, owner_id=member.user_id,
            question='原任务独立核对的限制性技术记录', status='queued', version=1,
            data={queries.KEY: None} if marker_retained else {})
        db.add(row); db.flush(); root_id = row.id
        for level in range(depth):
            row = DataRun(workspace_id=member.workspace_id, owner_id=member.user_id,
                question='原评估的派生技术记录 ' + str(level + 1), status='queued', version=1,
                data={'parent_query_id': row.id})
            db.add(row); db.flush()
        job = Job(workspace_id=member.workspace_id, kind='data_query',
            idempotency_key='v288-forbidden-generic:' + str(uuid4()), status='queued',
            data={'query_id': row.id, 'actor_id': member.user_id})
        db.add(job); db.flush()
        if not marker_retained:
            db.add(Audit(workspace_id=member.workspace_id, actor_id=member.user_id,
                action=queries.SAVED, object_id=root_id,
                data={'evaluation_id': str(uuid4()), 'job_id': job.id}))
        db.commit(); query_id, job_id, workspace_id = row.id, job.id, member.workspace_id
        original = (row.status, row.version, copy.deepcopy(row.data))
    for suffix, body in (('/cancel', {'expected_version': 1, 'reason': '核验封存资料的通用写入限制'}),
            ('/review-derived', {'expected_version': 1, 'decision': 'confirmed', 'reason': '核验封存资料的通用写入限制'})):
        response = client.post('/api/data/queries/' + query_id + suffix, json=body)
        assert response.status_code == 409, (suffix, response.status_code, response.text)
    response = client.get('/api/data/queries/' + query_id)
    assert response.status_code == 409
    assert process_once(job_id, kinds=['data_query'], workspace_id=workspace_id, deliver=False)
    with Session(engine()) as db:
        row = db.get(DataRun, query_id); job = db.get(Job, job_id)
        assert (row.status, row.version, row.data) == original
        assert job.status == 'failed'
