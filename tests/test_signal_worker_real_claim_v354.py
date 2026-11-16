"""Real PostgreSQL claim isolation and expired-lease recovery; no handlers run."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,Workspace
from generic_agents.worker import claim
from generic_agents.signal_approval_routing_v247 import KIND


def test_real_claim_is_workspace_scoped_concurrent_and_recovers_expired_original(account):
    own=account[2]
    with Session(engine()) as db:
        other=Workspace(name='Other isolated test workspace');db.add(other);db.flush()
        a=Job(workspace_id=own,kind=KIND,idempotency_key='scope-'+uuid4().hex,data={})
        b=Job(workspace_id=other.id,kind=KIND,idempotency_key='scope-'+uuid4().hex,data={})
        db.add_all([a,b]);db.flush();own_id,other_id,other_workspace=a.id,b.id,other.id;db.commit()
    # Both unrestricted polling forms must exclude this protected family.
    assert claim() is None and claim(kinds=[KIND]) is None
    assert claim(job_id=other_id,kinds=[KIND],workspace_id=own) is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=[x.result() for x in [pool.submit(claim,kinds=[KIND],workspace_id=own) for _ in range(2)]]
    acquired=[x for x in values if x is not None];assert len(acquired)==1 and acquired[0][0]==own_id
    original_token=acquired[0][1]
    with Session(engine()) as db:
        a,b=db.get(Job,own_id),db.get(Job,other_id)
        assert a.status=='running' and a.attempts==1 and a.lease_token==original_token
        assert b.status=='queued' and b.attempts==0 and b.lease_token is None
        a.lease_until=datetime.now(timezone.utc)-timedelta(seconds=5);db.commit()
    recovered=claim(job_id=own_id,kinds=[KIND],workspace_id=own)
    assert recovered and recovered[0]==own_id and recovered[1]!=original_token
    with Session(engine()) as db:
        a,b=db.get(Job,own_id),db.get(Job,other_id)
        assert a.status=='running' and a.attempts==2 and a.lease_token==recovered[1]
        assert a.data['lease_recoveries']==1 and b.status=='queued' and b.attempts==0
        assert b.workspace_id==other_workspace
