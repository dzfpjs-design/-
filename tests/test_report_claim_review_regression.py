"""Synthetic, disposable PG; no report/provider network."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import threading,socket
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.models import Audit,Workspace
from generic_agents.contracts import digest
from generic_agents.models import Job,CloudCall
from generic_agents.db import engine
from generic_agents.worker import claim
from generic_agents.report_evidence_worker_isolation import KIND
from generic_agents.cloud_scoped_extra_reservation import permit_key
from tests.test_report_worker_isolation_postgres import _real_scoped_plan
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene
from tests.test_material_ocr import local_runtime
@pytest.fixture(autouse=True)
def block_egress(monkeypatch):
 original=socket.socket.connect
 def connect(sock,address):
  if isinstance(address,tuple) and address and address[0] not in ('127.0.0.1','::1'):
   raise AssertionError('external network forbidden')
  return original(sock,address)
 monkeypatch.setattr(socket.socket,'connect',connect)

def test_many_concurrent_claims(reading_scene,monkeypatch):
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch);barrier=threading.Barrier(8)
 def one(_):
  barrier.wait(timeout=10);return claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)
 with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(one,range(8)))
 assert sum(x is not None for x in results)==1
 with Session(engine()) as db:
  assert db.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))==1
  assert db.scalar(select(func.count()).select_from(CloudCall).where(CloudCall.data['scoped_extra']['job_id'].as_string()==jid))==0

def test_used_permit_rejects_claim(reading_scene,monkeypatch):
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
 with Session(engine()) as db:
  permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_extra_allowance_v1',Job.data['manifest']['job_id'].as_string()==jid))
  assert permit;permit.data={**permit.data,'used_call_id':str(uuid4())};db.commit()
 assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id) is None

@pytest.mark.parametrize('mode',['key','marker_shape','marker_payload','marker_deleted','audit_hash'])
def test_marker_or_audit_damage_and_counter_reset_are_rejected(reading_scene,monkeypatch,mode):
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
 assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)
 with Session(engine()) as db:
  job=db.get(Job,jid);marker=db.scalar(select(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))
  assert marker;audit=db.scalar(select(Audit).where(Audit.object_id==marker.id,Audit.action=='cloud.report_scope.initial_claim_v1'));assert audit
  marker.idempotency_key='damaged:'+str(uuid4())
  if mode=='marker_shape':marker.kind='damaged';marker.data={**marker.data,'schema':'damaged'}
  if mode=='marker_payload':marker.kind='damaged';marker.data={**marker.data,'schema':'damaged','job_id':str(uuid4())}
  if mode=='marker_deleted':db.delete(marker)
  if mode=='audit_hash':audit.data={**audit.data,'payload_hash':'0'*64}
  job.status='queued';job.attempts=0;job.lease_token=None;job.lease_until=None
  job.data={**job.data,'lease_recoveries':0,'recovery_only':False};db.commit()
 assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id) is None


def test_foreign_workspace_witness_cannot_block_current_plan(reading_scene,monkeypatch):
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
 payload={'schema':'fl-initial-claim-v1','job_id':jid,'draft_id':rid,'actor_id':p.user_id}
 with Session(engine()) as db:
  other=Workspace(name='Synthetic separate workspace');db.add(other);db.flush()
  witness=Job(workspace_id=other.id,kind='fl_initial_claim_v1',status='internal',
    idempotency_key='fl-initial-claim-v1:'+jid,data=payload);db.add(witness);db.flush()
  db.add(Audit(workspace_id=other.id,actor_id=p.user_id,object_id=witness.id,
    action='cloud.report_scope.initial_claim_v1',data={'payload_hash':digest(payload)}));db.commit()
 assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)


@pytest.mark.parametrize('mode',['missing_audit','issuer','expiry','scope','grant_audit'])
def test_unverifiable_permit_or_grant_does_not_consume_first_claim(reading_scene,monkeypatch,mode):
 from datetime import timedelta
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
 with Session(engine()) as db:
  permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_extra_allowance_v1',Job.data['manifest']['job_id'].as_string()==jid))
  assert permit
  if mode=='grant_audit':
   grant=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_report_plan_allowance_v1',Job.data['manifest']['job_id'].as_string()==jid))
   grant.data={**grant.data,'audit_id':str(uuid4())}
  elif mode=='missing_audit':permit.data={**permit.data,'audit_id':str(uuid4())}
  else:
   data=dict(permit.data);pm=dict(data['manifest'])
   if mode=='issuer':pm['issuer_id']=str(uuid4())
   if mode=='scope':pm['scope_hash']='0'*64
   if mode=='expiry':
    now=db.scalar(select(func.clock_timestamp()))
    pm['not_before']=(now-timedelta(hours=2)).isoformat();pm['expires_at']=(now-timedelta(hours=1)).isoformat()
   data.update(manifest=pm,manifest_hash=digest(pm));permit.data=data
   audit=db.get(Audit,data['audit_id']);audit.data={**audit.data,'manifest_hash':data['manifest_hash']}
  db.commit()
 assert claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id) is None
 with Session(engine()) as db:
  assert db.get(Job,jid).status=='queued'
  assert db.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))==0


@pytest.mark.parametrize('mode',['inactive','demoted','logged_out'])
def test_persisted_scope_checks_current_issuer_but_not_expired_login(reading_scene,monkeypatch,mode):
 from generic_agents.models import AuthSession,Membership,User
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
 with Session(engine()) as db:
  if mode=='inactive':db.get(User,p.user_id).active=False
  if mode=='demoted':
   member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id));member.role='operator'
  if mode=='logged_out':db.get(AuthSession,p.session_id).revoked=True
  db.commit()
 result=claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)
 assert (result is not None)==(mode=='logged_out')


@pytest.mark.parametrize('mode',['issuer','permit_audit'])
def test_late_authority_change_rolls_back_claim_and_witness(reading_scene,monkeypatch,mode):
 from sqlalchemy import event
 from generic_agents.models import User
 p,rid,jid=_real_scoped_plan(reading_scene,monkeypatch)
 def drift(session):
  marker=next((x for x in session.identity_map.values() if isinstance(x,Job)
               and x.kind=='fl_initial_claim_v1' and x.data.get('job_id')==jid),None)
  if marker is None:return
  if mode=='issuer':session.get(User,p.user_id).active=False
  else:
   audit=next(x for x in session.identity_map.values() if isinstance(x,Audit)
              and x.action=='cloud.extra_allowance.issued_v1')
   audit.data={**audit.data,'manifest_hash':'0'*64}
 event.listen(Session,'before_commit',drift)
 try:
  with pytest.raises(ValueError):claim(job_id=jid,kinds=[KIND],workspace_id=p.workspace_id)
 finally:event.remove(Session,'before_commit',drift)
 with Session(engine()) as db:
  job=db.get(Job,jid);assert job.status=='queued' and job.attempts==0
  assert db.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key=='fl-initial-claim-v1:'+jid))==0
  assert db.get(User,p.user_id).active is True
