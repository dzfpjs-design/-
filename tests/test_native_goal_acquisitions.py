"""Native parent uses original localhost supplier requests; isolated PG, no hardware."""
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Job,Acquisition,ObservationPlan,SensorReservation,DataGap,CloudCall,Capability,now
from generic_agents.worker import process_once
from tests.test_native_goals import setup as native_setup,define,drain
from tests.test_sensing_selection import supplier,asset,monitor,dispatch,observe
from tests.test_vertical_slice import publish,manifest

def prepared(c,s,*,deadline=None,verification='unverified_extraction'):
    r,obj,task,base,body=native_setup(c,source='observations',observation_mode='allow_new');field=body['goal_spec']['required_fields'][0]
    src=publish(c,manifest('source',name='native_original_supplier_'+uuid4().hex[:8],allowed_agent_roles=['perception'],execution={'adapter':'http','endpoint':s['url']+'/observe','receipt_endpoint':s['url']+'/receipt','side_effect':True},specification={'timezone':'UTC','observation_capability':{'fields':[field],'modalities':['numeric'],'actions':['observe']}}))
    a=asset(c,obj,src,fields=[field]);m=monitor(c,obj,src,[a],fields=[field]);body['goal_spec']['evidence']['minimum_verification']=verification
    if deadline:body['goal_spec']['deadline']=deadline.isoformat()
    define(c,base,body);drain(task)
    with Session(engine()) as db:
        checked=[j for j in db.scalars(select(Job).where(Job.kind=='check_native_business_goal',Job.data['task_id'].as_string()==task['id'],Job.status!='queued')) if not j.data.get('time_trigger')];assert checked and all(j.status=='succeeded' for j in checked),[(j.status,j.data.get('error')) for j in checked]
    with Session(engine()) as db:
        acq=db.scalars(select(Acquisition)).one();identifier=acq.id;plan=db.get(ObservationPlan,acq.data['observation_plan_id']);assert plan.data['observation_request']['goal_delegation']['task_id']==task['id']
    return {'r':r,'obj':obj,'task':task,'base':base,'field':field,'source':src,'asset':a,'monitor':m,'acquisition_id':identifier}

def stopped(c,p):
    current=c.get(p['base']).json();response=c.post(p['base']+'/stop',json={'request_id':str(uuid4()),'expected_version':current['version'],'reason':'停止原目标，仍核对已下发设备真实回执'});assert response.status_code==200,response.text

def rows(c,p):
    response=c.get(p['base']+'/acquisitions');assert response.status_code==200,response.text;return response.json()

def receipt(c,p,row=None):
    row=row or rows(c,p)['items'][0]
    return c.post(p['base']+'/acquisitions/'+row['id']+'/receipt',json={'expected_version':row['version'],'reason':'只查询原设备请求回执，不重发动作'})

def test_native_goal_lists_only_original_plans_and_exports_safe_evidence(client,supplier):
    p=prepared(client,supplier);result=rows(client,p);assert len(result['items'])==1 and not result['truncated']
    item=result['items'][0];assert item['id']==p['acquisition_id'] and item['status']=='authorized' and not item['can_reconcile']
    assert item['required_fields']==[p['field']] and item['asset_title'] and item['source_name'] and supplier['calls']==[]
    export=client.get(p['base']+'/export').json();assert export['original_acquisitions']['items']==result['items']
    assert 'http://' not in str(result) and 'secret_ref' not in str(export['original_acquisitions'])

def test_native_stop_before_dispatch_prevents_original_post(client,supplier):
    p=prepared(client,supplier);stopped(client,p);dispatch(p['monitor'],p['acquisition_id'])
    assert supplier['calls']==[] and rows(client,p)['items'][0]['status']=='cancelled'

def test_unknown_receipt_after_native_stop_preserves_goal_and_never_reposts(client,supplier):
    p=prepared(client,supplier);supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id']);stopped(client,p)
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='accepted',terminal=False,no_future_device_activity=False)
    before=client.get('/api/workspace/tasks/'+p['task']['id']).json();response=receipt(client,p);assert response.status_code==200,response.text
    item=rows(client,p)['items'][0];assert item['status']=='awaiting_observation' and not item['device_action_terminal'] and item['reservation_held']
    assert client.get('/api/workspace/tasks/'+p['task']['id']).json()==before and client.get(p['base']).json()['cancelled']
    assert len(supplier['calls'])==1 and len(supplier['read_calls'])==1
    with Session(engine()) as db:assert db.scalars(select(DataGap)).one().status=='cancelled'

def test_bad_terminal_receipt_after_stop_cannot_release_original_hold(client,supplier):
    p=prepared(client,supplier);supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id']);stopped(client,p)
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='completed',terminal=False,no_future_device_activity=False)
    response=receipt(client,p);assert response.status_code==409
    item=rows(client,p)['items'][0];assert item['status']=='unknown' and item['reservation_held'] and not item['device_action_terminal'];assert len(supplier['calls'])==1

def test_late_original_data_closes_request_but_cannot_revive_stopped_goal(client,supplier):
    p=prepared(client,supplier);supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id']);stopped(client,p)
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='completed',terminal=True,no_future_device_activity=True)
    assert receipt(client,p).status_code==200
    obs=observe(client,p['monitor'],p['obj'],p['source'],p['acquisition_id'],{p['field']:p['obj']['attributes'][p['field']]})
    item=rows(client,p)['items'][0];assert item['status']=='succeeded' and item['closing_observation_id']==obs['id'] and not item['reservation_held']
    assert not item['can_reconcile'] and receipt(client,p).status_code==200 and len(supplier['calls'])==1 and len(supplier['read_calls'])==1
    goal=client.get(p['base']).json();assert goal['cancelled'] and not goal['acceptance']
    with Session(engine()) as db:assert len(list(db.scalars(select(Task))))==1 and not list(db.scalars(select(CloudCall)))

def test_wrong_original_link_blocks_before_receipt_http(client,supplier):
    p=prepared(client,supplier);supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id'])
    item=rows(client,p)['items'][0]
    with Session(engine()) as db:
        acq=db.get(Acquisition,item['id']);plan=db.get(ObservationPlan,acq.data['observation_plan_id']);origin=plan.data['observation_request'];plan.data={**plan.data,'observation_request':{**origin,'query_request_hash':'changed'}};db.commit()
    response=receipt(client,p,item);assert response.status_code==409 and len(supplier['calls'])==1 and not supplier.get('read_calls')

def test_foreign_acquisition_and_viewer_cannot_read_supplier(client,supplier):
    from generic_agents.models import Membership
    p=prepared(client,supplier)
    foreign=client.post(p['base']+'/acquisitions/'+str(uuid4())+'/receipt',json={'expected_version':1,'reason':'错误原请求'});assert foreign.status_code==404
    with Session(engine()) as db:
        membership=db.scalars(select(Membership)).one();membership.role='viewer';db.commit()
    response=receipt(client,p,rows(client,p)['items'][0]);assert response.status_code==403 and not supplier['calls']

def test_deadline_allows_original_receipt_without_new_dispatch(client,supplier,monkeypatch):
    from tests.test_native_goal_time import clock,scheduled
    at=now();p=prepared(client,supplier,deadline=at+timedelta(seconds=20));supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id'])
    timer=scheduled(p['task']);clock(monkeypatch,at+timedelta(seconds=21));assert process_once(timer['job_id'],deliver=False)
    assert client.get(p['base']).json()['timed_out'];assert receipt(client,p).status_code==200 and len(supplier['calls'])==1

def test_late_data_needing_review_remains_reviewable_after_original_goal_stops(client,supplier):
    p=prepared(client,supplier,verification='confirmed');supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id']);stopped(client,p)
    assert receipt(client,p).status_code==200
    obs=observe(client,p['monitor'],p['obj'],p['source'],p['acquisition_id'],{p['field']:p['obj']['attributes'][p['field']]})
    item=rows(client,p)['items'][0];assert item['status']=='awaiting_observation' and item['reservation_held']
    record=item['observations']['items'][0];assert record['id']==obs['id'] and record['can_verify'] and record['review']!='confirmed'
    response=client.post('/api/observations/'+record['id']+'/verify',json={'request_id':str(uuid4()),'expected_version':record['version'],'expected_input_hash':record['input_hash'],'decision':'confirmed','reason':'核对原输入与处理回执，只关闭原采集，不恢复已停止业务目标'})
    assert response.status_code==200,response.text
    item=rows(client,p)['items'][0];assert item['status']=='succeeded' and not item['reservation_held'] and item['observations']['items'][0]['review']=='confirmed'
    assert client.get(p['base']).json()['cancelled'] and not client.get(p['base']).json()['acceptance'] and len(supplier['calls'])==1

def test_valid_acquisition_id_cannot_be_reconciled_from_another_native_task(client,supplier):
    from tests.test_instance_workspace import task_row
    p=prepared(client,supplier);other,_=task_row(client,p['r'],p['obj'])
    response=client.post('/api/workspace/tasks/'+other['id']+'/goal/acquisitions/'+p['acquisition_id']+'/receipt',json={'expected_version':1,'reason':'不属于此原任务'})
    assert response.status_code in {404,409} and not supplier.get('read_calls') and not supplier['calls']

def test_native_stop_and_dispatch_use_consistent_parent_gap_lock_order(client,supplier,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from generic_agents import native_goal_acquisitions,observation_requests
    p=prepared(client,supplier);parent_locked=Event();dispatch_entered=Event()
    original_stop=native_goal_acquisitions.stop_coordination;original_lock=observation_requests.lock_acquisition_origin
    def stop_with_barrier(db,principal,task,reason):
        parent_locked.set();assert dispatch_entered.wait(5)
        return original_stop(db,principal,task,reason)
    def lock_with_barrier(db,principal,acq):
        dispatch_entered.set();return original_lock(db,principal,acq)
    monkeypatch.setattr(native_goal_acquisitions,'stop_coordination',stop_with_barrier)
    monkeypatch.setattr(observation_requests,'lock_acquisition_origin',lock_with_barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stop=pool.submit(stopped,client,p);assert parent_locked.wait(5)
        action=pool.submit(dispatch,p['monitor'],p['acquisition_id']);stop.result(timeout=10);action.result(timeout=10)
    assert not supplier['calls'] and rows(client,p)['items'][0]['status']=='cancelled'
    with Session(engine()) as db:assert db.scalars(select(DataGap)).one().status=='cancelled'

import pytest
@pytest.mark.parametrize('blocked', ['package_withdrawn','instance_paused'])
def test_original_native_receipt_and_stop_survive_configuration_block(client,supplier,blocked):
    p=prepared(client,supplier);supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id'])
    original=client.get('/api/workspace/tasks/'+p['task']['id']).json()
    with Session(engine()) as db:
        from generic_agents.models import DomainInstance
        if blocked=='package_withdrawn':db.get(Capability,db.get(Task,p['task']['id']).package_id).status='revoked'
        else:db.get(DomainInstance,p['r']['instance_id']).status='paused'
        db.commit()
    detail=client.get('/api/workspace/tasks/'+p['task']['id']);assert detail.status_code==200,detail.text
    goal=client.get(p['base']);assert goal.status_code==200,goal.text
    assert goal.json()['scope_error'] and goal.json()['can_stop']
    assert client.get(p['base']+'/export').status_code==200
    assert len(rows(client,p)['items'])==1
    body={'request_id':str(uuid4()),'expected_version':goal.json()['version'],'reason':'配置已停止，只核对原请求，不恢复执行'}
    assert client.post(p['base']+'/check',json=body).status_code==409
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='accepted',terminal=False,no_future_device_activity=False)
    assert receipt(client,p).status_code==200
    stopped(client,p)
    saved=client.get('/api/workspace/tasks/'+p['task']['id']).json()
    assert saved['workflow_runtime']==original['workflow_runtime']
    assert len(supplier['calls'])==1 and len(supplier['read_calls'])==1

def test_saved_workflow_integrity_is_required_for_original_read_and_stop(client,supplier):
    from generic_agents.models import InstanceRelease
    p=prepared(client,supplier)
    with Session(engine()) as db:
        task=db.get(Task,p['task']['id']);release=db.get(InstanceRelease,task.data['instance_release_id'])
        release.data={**release.data,'snapshot':{**release.data['snapshot'],'profile':{'name':'被改写的原版本'}}};db.commit()
    assert client.get('/api/workspace/tasks/'+p['task']['id']).status_code==409
    assert client.get(p['base']).status_code==409
    assert client.get(p['base']+'/acquisitions').status_code==409
    assert client.post(p['base']+'/stop',json={'request_id':str(uuid4()),'expected_version':p['task']['version'],'reason':'不能绕过原版本摘要'}).status_code==409
    assert not supplier.get('calls')

@pytest.mark.parametrize('blocked',['package_withdrawn','instance_paused'])
def test_original_task_can_cancel_while_configuration_blocked_without_device_cancel(client,supplier,blocked):
    from generic_agents.models import DomainInstance
    p=prepared(client,supplier);supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id'])
    with Session(engine()) as db:
        task=db.get(Task,p['task']['id'])
        if blocked=='package_withdrawn':db.get(Capability,task.package_id).status='revoked'
        else:db.get(DomainInstance,p['r']['instance_id']).status='paused'
        db.commit()
    original=client.get('/api/workspace/tasks/'+p['task']['id']).json()
    response=client.post('/api/workspace/tasks/'+p['task']['id']+'/cancel',json={'version':original['version'],'reason':'取消原任务，保留未知设备回执','retained_note':'人工核对说明仍保留'})
    assert response.status_code==200,response.text
    assert response.json()['status']=='workflow_cancelled' and response.json()['cancellation']['retained_note']=='人工核对说明仍保留'
    assert rows(client,p)['items'][0]['reservation_held']
    with Session(engine()) as db:
        assert db.scalars(select(DataGap)).one().status=='cancelled'
        assert not list(db.scalars(select(Job).where(Job.kind=='check_native_business_goal',Job.status=='queued')))
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')

def test_withdrawn_business_preserves_late_original_data_without_new_goal(client,supplier):
    p=prepared(client,supplier,verification='confirmed');supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id'])
    with Session(engine()) as db:
        db.get(Capability,db.get(Task,p['task']['id']).package_id).status='revoked';db.commit()
    stopped(client,p)
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='completed',terminal=True,no_future_device_activity=True)
    assert receipt(client,p).status_code==200
    observation=observe(client,p['monitor'],p['obj'],p['source'],p['acquisition_id'],{p['field']:p['obj']['attributes'][p['field']]})
    item=rows(client,p)['items'][0];assert item['status']=='awaiting_observation' and item['reservation_held']
    saved=item['observations']['items'][0];assert saved['id']==observation['id'] and saved['can_verify'] and saved['review'] is None
    verified=client.post('/api/observations/'+saved['id']+'/verify',json={'request_id':str(uuid4()),'expected_version':saved['version'],'expected_input_hash':saved['input_hash'],'decision':'confirmed','reason':'仅核对已派发原请求的迟到资料，不恢复撤回配置'})
    assert verified.status_code==200,verified.text
    assert rows(client,p)['items'][0]['status']=='succeeded'
    assert client.get(p['base']).json()['cancelled'] and not client.get(p['base']).json()['acceptance']
    with Session(engine()) as db:assert db.scalars(select(Task)).all().__len__()==1 and not list(db.scalars(select(CloudCall)))
    assert len(supplier['calls'])==1 and len(supplier['read_calls'])==1
