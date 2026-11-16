"""Normal extraction/reception plus explicit original-event command recovery.

The positive path uses a newly published ordinary instance and local numeric
extraction. Deliberate database corruption below is negative/fault-repair test
setup; no successful receipt, Memory, Job or Event is fabricated.
"""
import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event as Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session

from generic_agents import models as m,worker,workspace_observation_intake as intake
from generic_agents import observation_data_delivery_v245 as delivery
from generic_agents.contracts import digest
from generic_agents.instance_data_receipts import observation_receipt
from tests.test_scoped_data_gap_intake_pg_v245 import scene,ingest,event,good


def path(s,observation):
    return '/api/workspace/instances/'+s.release['instance_id']+'/observations/'+observation['id']+'/data-delivery-v245'


def processed(s):
    observation=ingest(s)
    with Session(s.bound) as db:
        job=db.scalar(select(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.idempotency_key=='extract:'+observation['id']))
        identity=job.id
    assert worker.process_once(identity,kinds=['process_observation'],deliver=False,workspace_id=s.workspace_id)
    with Session(s.bound) as db:
        assert event(db,s,observation['id']).status=='pending_delivery'
        assert db.get(m.Observation,observation['id']).status=='processed'
    return observation,identity


def request(context,*,nonce=None):
    value={'schema':'observation-data-delivery-retry-command-v245','request_id':nonce or str(uuid4()),
        'expected_context_hash':context['context_hash'],'reason':'已核对原输入与接收原因，仅恢复原数据事件','confirm_retry':True}
    return value,json.dumps(value,ensure_ascii=False,indent=2).encode('utf8')+b'\n'


def command(s,url,raw,status=200):
    return good(s.client.post(url+'/retry',content=raw,headers={'Content-Type':'application/json'}),status)


def recovery(s,url,value,raw,status=200):
    return good(s.client.get(url+'/requests/'+value['request_id'],params={'expected_sha256':hashlib.sha256(raw).hexdigest()}),status)


def counts(db,s):
    return {model.__name__:db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.workspace_id))
        for model in (m.Observation,m.Invocation,m.Memory,m.Event,m.CloudCall)}


def test_repaired_original_blocked_event_requires_explicit_release_then_normal_receiver_ack(scene):
    s=scene;observation,job_id=processed(s);url=path(s,observation)
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);identity=row.id;original=copy.deepcopy(row.data)
        row.data={**row.data,'extracted':{'attributes':{s.field:999},'abnormal':[]}};db.commit()
    intake.deliver_pending(s.workspace_id)
    broken=good(s.client.get(url));assert broken['state']=='blocked_integrity' and not broken['can_retry']
    _,bad_raw=request(broken);command(s,url,bad_raw,409)
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);assert row.id==identity and row.status=='blocked_delivery' and row.attempts==1
        # Explicit fault repair restores only the original producer payload;
        # keep the original failure diagnostics, attempts and event identity.
        row.data={**row.data,**original};db.commit();before=counts(db,s)
    ready=good(s.client.get(url));assert ready['state']=='blocked' and ready['can_retry']
    assert good(s.client.get(url))['context_hash']==ready['context_hash']
    value,raw=request(ready);result=command(s,url,raw)
    assert result['decision']=='released_for_delivery' and result['event_id']==identity
    assert result['body_sha256']==hashlib.sha256(raw).hexdigest() and result['cloud_calls']==result['device_actions']==0
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);assert row.id==identity and row.status=='pending_delivery' and row.attempts==1
        assert row.received_at is None and row.data['delivery_error']
        assert counts(db,s)==before and 'data_reception' not in db.get(m.Observation,observation['id']).data
        saved=db.scalar(select(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.kind==delivery.KIND))
        audit=db.scalar(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,m.Audit.action==delivery.ACTION))
        assert saved.status=='succeeded' and saved.attempts==0 and saved.lease_token is None and saved.lease_until is None
        assert saved.data['exact_body'].encode('utf8')==raw and audit.object_id==saved.id and audit.data==saved.data
        assert saved.data['previous']['status']=='blocked_delivery' and saved.data['previous']['attempts']==1
        assert delivery.KIND not in worker.SETUP_KINDS
    # The authorization command performs neither extraction nor reception.
    # Only the real bounded product receiver produces the independent ACK.
    intake.deliver_pending(s.workspace_id);intake.deliver_pending(s.workspace_id)
    received=good(s.client.get(url));assert received['state']=='received' and not received['can_retry']
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);proof=observation_receipt(db,s.p,db.get(m.Observation,observation['id']))
        assert row.id==identity and row.status=='received' and row.attempts==2
        assert received['receipt']['receipt_hash']==digest(proof['data_reception'])
        after=counts(db,s);assert after['Memory']==before['Memory']+1 and after['Invocation']==before['Invocation']
        assert after['Observation']==before['Observation'] and after['CloudCall']==0
        assert db.get(m.Job,job_id).attempts==1
    restored=recovery(s,url,value,raw);assert restored['found'] and restored['result']==result and restored['body']==value
    assert restored['current']==received and command(s,url,raw)==result
    changed=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf8')
    command(s,url,changed,409)


def test_pending_keeps_original_event_and_completed_ack_returns_original_receipt(scene):
    s=scene;observation,job_id=processed(s);url=path(s,observation);pending=good(s.client.get(url))
    assert pending['state']=='pending' and not pending['can_retry'] and pending['receipt'] is None
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);identity=row.id;snapshot=(row.status,row.attempts,row.received_at,copy.deepcopy(row.data));before=counts(db,s)
    value,raw=request(pending);result=command(s,url,raw);assert result['decision']=='pending_retained'
    with Session(s.bound) as db:
        row=event(db,s,observation['id'])
        assert (row.status,row.attempts,row.received_at,row.data)==snapshot and counts(db,s)==before
    unknown,unknown_raw=request(pending)
    found=recovery(s,url,unknown,unknown_raw);assert found['found'] is False and found['body'] is found['result'] is None
    intake.deliver_pending(s.workspace_id)
    received=good(s.client.get(url));value2,raw2=request(received);result2=command(s,url,raw2)
    assert result2['decision']=='already_received' and result2['event_id']==identity
    assert good(s.client.get(url))['receipt']==received['receipt']
    assert recovery(s,url,value,raw)['result']==result and recovery(s,url,value2,raw2)['result']==result2
    with Session(s.bound) as db:
        assert counts(db,s)['Memory']==before['Memory']+1 and db.get(m.Job,job_id).attempts==1


def test_saved_command_recovers_with_blocked_current_when_received_memory_is_lost(scene):
    s=scene;observation,_=processed(s);url=path(s,observation);value,raw=request(good(s.client.get(url)))
    result=command(s,url,raw);intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);memory=db.scalar(select(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
            m.Memory.data['event_id'].as_string()==row.id,m.Memory.data['type'].as_string()=='observation_evidence'))
        assert memory is not None;db.delete(memory);db.commit();before=counts(db,s)
    restored=recovery(s,url,value,raw)
    assert restored['result']==result and restored['body']==value and restored['current']['state']=='blocked_integrity'
    assert restored['current']['receipt'] is None and restored['current']['can_retry'] is False
    _,fresh_raw=request(restored['current']);command(s,url,fresh_raw,409);command(s,url,raw,409)
    intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:assert counts(db,s)==before and event(db,s,observation['id']).status=='received'


@pytest.mark.parametrize('damage',['job','audit','raw','route','actor'])
def test_command_history_needs_independent_raw_route_actor_audit_not_only_terminal_status(scene,damage):
    s=scene;observation,_=processed(s);url=path(s,observation);value,raw=request(good(s.client.get(url)))
    command(s,url,raw)
    with Session(s.bound) as db:
        job=db.scalar(select(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.kind==delivery.KIND))
        audit=db.scalar(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,m.Audit.action==delivery.ACTION))
        if damage=='job':job.attempts=1
        elif damage=='audit':db.delete(audit)
        else:
            data=copy.deepcopy(job.data)
            if damage=='raw':data['exact_body']+='\n'
            elif damage=='route':data['observation_id']=str(uuid4())
            else:data['actor_id']=str(uuid4())
            # Even a self-consistent row checksum and matching independent
            # audit cannot replace the exact request, route or current actor.
            data['record_hash']=digest({k:v for k,v in data.items() if k!='record_hash'})
            job.data=data;audit.data=copy.deepcopy(data)
        db.commit();before=counts(db,s)
    recovery(s,url,value,raw,409);command(s,url,raw,409)
    with Session(s.bound) as db:assert counts(db,s)==before


def test_context_scope_current_role_and_stale_fingerprint_are_enforced(scene):
    s=scene;observation,_=processed(s);url=path(s,observation);view=good(s.client.get(url));_,raw=request(view)
    stale=copy.deepcopy(view);stale['context_hash']='f'*64;_,stale_raw=request(stale);command(s,url,stale_raw,409)
    wrong=url.replace(s.release['instance_id'],str(uuid4()));assert s.client.get(wrong).status_code==404
    with Session(s.bound) as db:
        other=m.Workspace(name='explicit foreign scope sentinel');db.add(other);db.flush()
        db.add(m.Membership(workspace_id=other.id,user_id=s.actor_id,role='admin'));db.commit();foreign=other.id
    assert s.client.get(url,headers={'X-Workspace-ID':foreign}).status_code==404
    assert s.client.post(url+'/retry',content=raw,headers={'X-Workspace-ID':foreign,'Content-Type':'application/json'}).status_code==404
    with Session(s.bound) as db:
        membership=db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.workspace_id,m.Membership.user_id==s.actor_id))
        membership.role='viewer';db.commit()
    assert good(s.client.get(url))['state']=='pending'
    command(s,url,raw,403)
    with Session(s.bound) as db:
        assert not list(db.scalars(select(m.Job).where(m.Job.kind==delivery.KIND)))
        assert not list(db.scalars(select(m.Audit).where(m.Audit.action==delivery.ACTION)))


def test_saved_result_survives_current_release_custody_damage_without_repair(scene):
    s=scene;observation,_=processed(s);url=path(s,observation);value,raw=request(good(s.client.get(url)))
    result=command(s,url,raw)
    with Session(s.bound) as db:
        release=db.get(m.InstanceRelease,s.release['id'])
        release.configuration_hash='0'*64;db.commit();before=counts(db,s)
    restored=recovery(s,url,value,raw)
    assert restored['found'] and restored['result']==result and restored['current']['state']=='blocked_integrity'
    assert restored['current']['can_retry'] is False and restored['current']['receipt'] is None
    _,fresh=request(restored['current']);command(s,url,fresh,409)
    with Session(s.bound) as db:assert counts(db,s)==before


def test_current_actor_share_lock_linearizes_command_before_actual_membership_revocation(scene,monkeypatch):
    s=scene;observation,_=processed(s);url=path(s,observation);_,raw=request(good(s.client.get(url)))
    held=Barrier();release=Barrier();started=Barrier();pid=[];real_base=delivery._base
    def paused(*args,**kwargs):
        result=real_base(*args,**kwargs)
        if kwargs.get('lock'):
            held.set()
            assert release.wait(5),'test never released the actual Actor/Event/Observation locks'
        return result
    monkeypatch.setattr(delivery,'_base',paused)
    def authorize():
        with Session(s.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"));db.execute(text("SET LOCAL statement_timeout='6s'"))
            result=delivery.retry(db,s.p,s.release['instance_id'],observation['id'],raw);db.commit();return result
    def revoke():
        with Session(s.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"));db.execute(text("SET LOCAL statement_timeout='6s'"))
            pid.append(db.scalar(text('SELECT pg_backend_pid()')));started.set()
            membership=db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.workspace_id,m.Membership.user_id==s.actor_id))
            membership.role='viewer';db.commit();return True
    with ThreadPoolExecutor(max_workers=2) as pool:
        command_future=pool.submit(authorize);assert held.wait(4)
        revoke_future=pool.submit(revoke)
        try:
            assert started.wait(2)
            blocked=False;deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                with s.bound.connect() as conn:
                    blocked=conn.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid[0]}) is True
                if blocked:break
                time.sleep(.02)
            assert blocked and not revoke_future.done(),'revocation did not wait on the actual current-Actor SHARE fence'
        finally:release.set()
        assert command_future.result(timeout=6)['decision']=='pending_retained'
        assert revoke_future.result(timeout=6)
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.kind==delivery.KIND))==1
        assert db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.workspace_id,m.Membership.user_id==s.actor_id)).role=='viewer'
