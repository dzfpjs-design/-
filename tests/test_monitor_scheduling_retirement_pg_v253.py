"""Real PG/HTTP retirement of original Mode requests; no mode/dispatch substitute.

Only explicit revocation/corruption fixtures alter DB rows in negative cases.
Concurrency wrappers observe the real nonce lock without bypassing it.
"""
import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event,Lock
from uuid import uuid4

import pytest
from sqlalchemy import select,func,text,event
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents import monitor_scheduling_service_v253 as store
from generic_agents.monitor_scheduling_models_v253 import (MonitorSchedulingHeadV253 as Head,
    MonitorSchedulingCommandV253 as Command,MonitorSchedulingRetiredRequestV253 as Retired)
from tests.test_anomaly_goal_handoff_v253 import handoff_scene,simulated_supplier,good
from tests.test_monitor_scheduling_pg_v253 import path,choose_mode


def original(s,policy,mode='periodic'):
    view=good(s.client.get(path(s,policy),params={'instance_id':s.release['instance_id']}))
    body={'schema':'monitor-scheduling-command-v253','request_id':'保留/原号?'+uuid4().hex,
        'instance_id':s.release['instance_id'],'expected_version':view['control']['version'] if view['control'] else 0,
        'expected_policy_version':view['policy_version'],'approved_hash':view['approved_hash'],'mode':mode,'reason':'原申请不得偷偷换号或更新CAS'}
    raw=json.dumps(body,ensure_ascii=False,separators=(', ',': '))+'\n'
    return body,raw


def ending(body,raw):
    value={'schema':'monitor-scheduling-retire-v253','request_id':body['request_id'],'instance_id':body['instance_id'],
        'original_raw':raw,'raw_sha256':hashlib.sha256(raw.encode()).hexdigest(),'body_hash':digest(body),
        'reason':'明确保留原文并结束原申请；不更改当前监测方式'}
    return value,json.dumps(value,ensure_ascii=False,separators=(', ',': '))+'\n'


def post_raw(client,url,raw):return client.post(url,content=raw.encode('utf8'),headers={'Content-Type':'application/json'})


def lookup(s,policy,body,raw,client=None):
    return (client or s.client).get(path(s,policy)+'/requests',params={'request_id':body['request_id'],
        'raw_sha256':hashlib.sha256(raw.encode()).hexdigest()})


def inventory(s):
    with Session(s.bound) as db:
        return {cls.__name__:[(r.id,r.version,digest(r.data)) for r in db.scalars(select(cls).where(
            cls.workspace_id==s.workspace).order_by(cls.id))] for cls in
            (Head,Command,Retired,m.MonitorPolicy,m.Audit,m.Job,m.ObservationPlan,m.Acquisition,m.Observation,m.Memory)}


def test_stale_cas_can_be_ended_without_changing_head_and_original_history_survives_role_revocation(handoff_scene):
    s=handoff_scene;policy=s.monitor_b
    body,raw=original(s,policy)
    choose_mode(s,policy,'goal_only')
    assert post_raw(s.client,path(s,policy),raw).status_code==409
    unknown=good(lookup(s,policy,body,raw));assert not unknown['found'] and unknown['outcome'] is None
    end,end_raw=ending(body,raw);before=inventory(s)
    saved=good(post_raw(s.client,path(s,policy)+'/retire',end_raw),201)
    assert saved['outcome']=='retired' and saved['result'] is None
    proof=saved['retirement'];assert proof['body_hash']==digest(body) and proof['raw_sha256']==end['raw_sha256']
    assert not proof['mode_changed'] and not proof['epoch_changed']
    after=inventory(s)
    for cls in (Head,Command,m.MonitorPolicy,m.Job,m.ObservationPlan,m.Acquisition,m.Observation,m.Memory):
        assert after[cls.__name__]==before[cls.__name__]
    assert len(after[Retired.__name__])==len(before[Retired.__name__])+1
    assert good(post_raw(s.client,path(s,policy)+'/retire',end_raw))==saved
    assert post_raw(s.client,path(s,policy),raw).status_code==409
    recovered=good(lookup(s,policy,body,raw))
    assert recovered['found'] and recovered['outcome']=='retired' and recovered['result'] is None
    assert recovered['raw']==raw and recovered['retirement']==proof
    changed={**end,'reason':'不能改写已结束操作'}
    assert s.client.post(path(s,policy)+'/retire',json=changed).status_code==409
    assert inventory(s)==after
    assert lookup(s,policy,body,raw,s.reviewers[0][1]).status_code==404
    # A separate explicit new draft can change the mode; the ended raw never can.
    _,_,fresh=choose_mode(s,policy,'periodic');assert fresh['epoch']==2
    a_body,a_raw=original(s,s.monitor_a,'goal_only')
    committed=good(post_raw(s.client,path(s,s.monitor_a),a_raw),201)
    a_end,a_end_raw=ending(a_body,a_raw);snapshot=inventory(s)
    prior=good(post_raw(s.client,path(s,s.monitor_a)+'/retire',a_end_raw))
    assert prior['outcome']=='committed' and prior['result']==committed and prior['retirement'] is None
    assert inventory(s)==snapshot
    # Explicit external role revocation fault, not a claimed normal user-edit
    # API. Existing authenticated HTTP sessions now resolve the actual viewer role.
    with Session(s.bound) as db:
        member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.workspace,m.Membership.user_id==s.p.user_id))
        member.role='viewer';db.commit()
    assert good(s.client.get('/api/me'))['role']=='viewer'
    frozen=inventory(s)
    assert good(lookup(s,policy,body,raw))['retirement']==proof
    assert good(post_raw(s.client,path(s,policy)+'/retire',end_raw))==saved
    assert good(post_raw(s.client,path(s,s.monitor_a),a_raw))==committed
    assert post_raw(s.client,path(s,policy),raw).status_code==409
    new_body={**body,'request_id':'不能自认旧主-'+uuid4().hex}
    new_raw=json.dumps(new_body,ensure_ascii=False);_,new_end_raw=ending(new_body,new_raw)
    assert post_raw(s.client,path(s,policy)+'/retire',new_end_raw).status_code==403
    assert inventory(s)==frozen
    assert s.sim.calls==[('POST','/acquisitions')]


def test_unexecuted_retirement_keeps_real_legacy_mode_and_cannot_change_actor_scope_or_raw(handoff_scene):
    s=handoff_scene;policy=s.monitor_b;body,raw=original(s,policy);end,end_raw=ending(body,raw)
    assert post_raw(s.reviewers[0][1],path(s,policy)+'/retire',end_raw).status_code==403
    bad={**end,'body_hash':'0'*64}
    assert s.client.post(path(s,policy)+'/retire',json=bad).status_code==422
    before=inventory(s)
    saved=good(post_raw(s.client,path(s,policy)+'/retire',end_raw),201)
    view=good(s.client.get(path(s,policy),params={'instance_id':s.release['instance_id']}))
    assert view['effective_mode']=='legacy' and view['control'] is None
    after=inventory(s)
    for cls in (Head,Command,m.MonitorPolicy,m.Job,m.ObservationPlan,m.Acquisition,m.Observation,m.Memory):
        assert after[cls.__name__]==before[cls.__name__]
    assert post_raw(s.client,path(s,s.monitor_a)+'/retire',end_raw).status_code==409
    assert post_raw(s.client,path(s,policy),raw+' ').status_code==409
    assert good(post_raw(s.client,path(s,policy)+'/retire',end_raw))==saved
    assert inventory(s)==after


@pytest.mark.parametrize('damage',['missing_row','missing_audit','changed_saved_raw'])
def test_retirement_evidence_loss_never_rebuilds_or_resurrects_original_request(handoff_scene,damage):
    s=handoff_scene;policy=s.monitor_b;body,raw=original(s,policy);_,end_raw=ending(body,raw)
    proof=good(post_raw(s.client,path(s,policy)+'/retire',end_raw),201)['retirement']
    with Session(s.bound) as db:
        row=db.get(Retired,proof['id'])
        if damage=='missing_row':db.delete(row)
        elif damage=='missing_audit':
            audit=db.scalar(select(m.Audit).where(m.Audit.workspace_id==s.workspace,m.Audit.action==store.RETIRED_ACTION,m.Audit.object_id==row.id));db.delete(audit)
        else:
            value=copy.deepcopy(row.data);value['raw']+=' ';row.data=value
        db.commit()
    damaged=inventory(s)
    assert lookup(s,policy,body,raw).status_code==409
    assert post_raw(s.client,path(s,policy),raw).status_code==409
    assert post_raw(s.client,path(s,policy)+'/retire',end_raw).status_code==409
    assert inventory(s)==damaged
    with Session(s.bound) as db:
        assert not db.scalar(select(Head.id).where(Head.workspace_id==s.workspace,Head.policy_id==policy['id']))
    assert s.sim.calls==[('POST','/acquisitions')]


@pytest.mark.parametrize('winner',['mode','retire'])
def test_late_original_post_and_retirement_serialize_on_the_same_real_nonce(handoff_scene,monkeypatch,winner):
    s=handoff_scene;policy=s.monitor_b;body,raw=original(s,policy);_,end_raw=ending(body,raw)
    real_lock=store.lock_nonce;acquired=Event();release=Event();second=Event();mutex=Lock();calls=[0];owner_pid=[None]
    def observed(db,p,request_id):
        with mutex:
            calls[0]+=1;ordinal=calls[0]
        if request_id==body['request_id'] and ordinal==2:second.set()
        real_lock(db,p,request_id)
        if request_id==body['request_id'] and ordinal==1:
            owner_pid[0]=db.scalar(select(func.pg_backend_pid()));acquired.set()
            assert release.wait(15),'bounded first nonce observation timed out'
    def bounded(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout='20s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='30s'")
    monkeypatch.setattr(store,'lock_nonce',observed);event.listen(s.bound,'begin',bounded)
    try:
        first_url=path(s,policy) if winner=='mode' else path(s,policy)+'/retire'
        first_raw=raw if winner=='mode' else end_raw
        second_url=path(s,policy)+'/retire' if winner=='mode' else path(s,policy)
        second_raw=end_raw if winner=='mode' else raw
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(post_raw,s.client,first_url,first_raw)
            try:
                assert acquired.wait(8)
                late=pool.submit(post_raw,s.client,second_url,second_raw)
                assert second.wait(8)
                blocked=False;deadline=time.monotonic()+5
                while time.monotonic()<deadline and not late.done():
                    with Session(s.bound) as db:
                        blocked=db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity a WHERE a.wait_event_type='Lock' AND :pid = ANY(pg_blocking_pids(a.pid)))"),{'pid':owner_pid[0]})
                    if blocked:break
                    time.sleep(.02)
                assert blocked and not late.done(),'late original/end did not wait on the same real nonce'
            finally:release.set()
            first_response=first.result(timeout=40);late_response=late.result(timeout=40)
        assert first_response.status_code==201,first_response.text
        with Session(s.bound) as db:
            heads=list(db.scalars(select(Head).where(Head.workspace_id==s.workspace,Head.policy_id==policy['id'])))
            commands=list(db.scalars(select(Command).where(Command.workspace_id==s.workspace,Command.request_id==body['request_id'])))
            ended=list(db.scalars(select(Retired).where(Retired.workspace_id==s.workspace,Retired.request_id==body['request_id'])))
            if winner=='mode':
                assert late_response.status_code==200,late_response.text
                assert late_response.json()['outcome']=='committed' and late_response.json()['result']==first_response.json()
                assert len(heads)==len(commands)==1 and heads[0].epoch==1 and ended==[]
            else:
                assert late_response.status_code==409,late_response.text
                assert first_response.json()['outcome']=='retired' and heads==[] and commands==[] and len(ended)==1
        recovered=good(lookup(s,policy,body,raw))
        assert recovered['outcome']==('committed' if winner=='mode' else 'retired')
        assert s.sim.calls==[('POST','/acquisitions')]
    finally:event.remove(s.bound,'begin',bounded)
