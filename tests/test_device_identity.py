"""Cross-source identity and actual dispatch exclusion in isolated PG / localhost HTTP."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation,SensorDevice,DeviceReservation,AssetDeviceBinding,Acquisition,Membership,MonitorPolicy,Capability,now
from generic_agents.perception import dispatch_acquisition,propose_acquisitions
from generic_agents.device_identity import DeviceCreate,release
from .test_sensing_selection import supplier,setup,asset,monitor,scan,current,dispatch,principal,observe
from .test_vertical_slice import manifest,publish

def device_of(c,a):return c.get('/api/sensing/assets/'+a['id']+'/device').json()['device']
def rebind(c,a,d):
    return c.post('/api/sensing/assets/'+a['id']+'/device',json={'expected_version':a['version'],
        'device_id':d['id'],'device_expected_version':d['version'],'reason':'明确核对这两个供应商接口属于同一台合成设备'})
def shared(c,s):
    obj,src=setup(c,s);a=asset(c,obj,src)
    bsrc=publish(c,manifest('source',name='alias_source_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution=src['data']['execution'],specification=src['data']['specification']))
    b=asset(c,obj,bsrc);dev=device_of(c,a);r=rebind(c,b,dev);assert r.status_code==200,r.text
    b=next(r for r in c.get('/api/sensing').json()['assets'] if r['id']==b['id'])
    return obj,src,bsrc,a,b,dev

def test_unknown_excludes_other_source_and_holds_shared_device(client,supplier):
    obj,src,bsrc,a,b,dev=shared(client,supplier);supplier['modes'][a['external_id']]='unknown'
    m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,bsrc,[b]);identifier=scan(client,m1)[0];dispatch(m1,identifier)
    assert scan(client,m2)==[] and len(supplier['calls'])==1
    plans=client.get('/api/sensing').json()['plans'];waiting=next(p for p in plans if p['policy_id']==m2['id'])
    assert waiting['status']=='waiting_resource' and '统一设备' in waiting['data']['candidates'][0]['reasons'][0]
    with Session(engine()) as db:
        assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id==identifier
        assert db.get(Acquisition,identifier).data['sensor_device_id']==dev['id']

def test_two_source_concurrent_dispatches_send_only_one_request(client,supplier):
    obj,src,bsrc,a,b,dev=shared(client,supplier);m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,bsrc,[b])
    first=scan(client,m1)[0];second=scan(client,m2)[0]
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda args:dispatch(*args),[(m1,first),(m2,second)]))
    assert len(supplier['calls'])==1
    with Session(engine()) as db:
        rows=[db.get(Acquisition,id) for id in [first,second]]
        assert sorted(a.status for a in rows)==['awaiting_observation','blocked_resource']
        assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id in [first,second]

def test_unrelated_devices_can_both_dispatch(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);b=asset(client,obj,src)
    assert device_of(client,a)['id']!=device_of(client,b)['id']
    m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,src,[b]);first=scan(client,m1)[0];second=scan(client,m2)[0]
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda args:dispatch(*args),[(m1,first),(m2,second)]))
    assert len(supplier['calls'])==2

def test_unknown_binding_cannot_move_or_add_alias_until_permanent_end(client,supplier):
    obj,src,bsrc,a,b,dev=shared(client,supplier);other=asset(client,obj,src);different=device_of(client,other)
    supplier['modes'][a['external_id']]='unknown';m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0];dispatch(m,identifier)
    assert rebind(client,a,different).status_code==409 and rebind(client,other,dev).status_code==409
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='cancelled',terminal=True,no_future_observation=True)
    assert client.post('/api/acquisitions/'+identifier+'/reconcile').status_code==200
    assert rebind(client,a,different).status_code==200 and len(supplier['calls'])==1

def test_pause_keeps_unknown_hold_and_only_terminal_receipt_releases(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);dev=device_of(client,a)
    supplier['modes'][a['external_id']]='unknown';m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0];dispatch(m,identifier)
    r=client.post('/api/sensing/devices/'+dev['id']+'/pause',json={'expected_version':dev['version'],'reason':'停用后续派发，原动作需核对'})
    assert r.status_code==200;paused=r.json()
    with Session(engine()) as db:assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id==identifier
    assert client.post('/api/acquisitions/'+identifier+'/reconcile').status_code==200
    assert scan(client,m)==[] and len(supplier['calls'])==1
    assert client.post('/api/sensing/devices/'+dev['id']+'/resume',json={'expected_version':paused['version'],'reason':'核查后恢复未来派发，未解除旧占用'}).status_code==200
    with Session(engine()) as db:assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id==identifier

def test_device_change_after_selection_denies_before_http(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);dev=device_of(client,a);m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0]
    r=client.post('/api/sensing/devices/'+dev['id']+'/pause',json={'expected_version':dev['version'],'reason':'派发前暂停设备'});assert r.status_code==200
    dispatch(m,identifier);assert supplier['calls']==[]
    with Session(engine()) as db:assert db.get(Acquisition,identifier).status=='blocked_connector'

def test_confirmation_and_binding_require_admin_and_scoped_identity(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);dev=device_of(client,a)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==a['workspace_id']));membership.role='operator';db.commit()
    r=client.post('/api/sensing/devices',json={'title':'操作员设备草稿','identity_scheme':'inventory_tag','identity_issuer':'测试台账','identity_reference':uuid4().hex,'reason':'提交核查'})
    assert r.status_code==201;draft=r.json()
    assert client.post('/api/sensing/devices/'+draft['id']+'/approve',json={'expected_version':draft['version'],'reason':'越权'}).status_code==403
    assert rebind(client,a,dev).status_code==403
    with Session(engine()) as db:
        membership=db.scalar(select(Membership));membership.role='admin';db.commit()
        from generic_agents.models import Workspace
        other=Workspace(name='其他隔离工作区');db.add(other);db.flush()
        foreign=SensorDevice(workspace_id=other.id,title='外部设备',identity_scheme='inventory_tag',identity_issuer='别处',identity_reference='001',status='enabled',data={});db.add(foreign);db.commit();foreign_id=foreign.id
    assert client.get('/api/sensing/devices/'+foreign_id).status_code==404
    assert rebind(client,a,{'id':foreign_id,'version':1}).status_code==404

def test_identity_duplicate_is_exact_normalized_not_name_guessing(client,supplier):
    body={'title':'同名设备','identity_scheme':'inventory_tag','identity_issuer':'  合成台账  ','identity_reference':'ＡＢＣ-001','reason':'明确原始台账号'}
    r=client.post('/api/sensing/devices',json=body);assert r.status_code==201;row=r.json()
    assert row['identity_reference']=='ABC-001' and row['identity_issuer']=='合成台账'
    r=client.post('/api/sensing/devices',json={**body,'identity_reference':'ABC-001'});assert r.status_code==409
    assert client.post('/api/sensing/devices',json={**body,'identity_reference':'ABC-002'}).status_code==201
    assert client.post('/api/sensing/devices',json={**body,'identity_reference':'abc-001'}).status_code==201
    assert client.post('/api/sensing/devices',json={**body,'identity_reference':'\n\r'}).status_code==422

def test_unbound_asset_cannot_authorize_control(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src)
    with Session(engine()) as db:
        link=db.scalar(select(AssetDeviceBinding).where(AssetDeviceBinding.asset_id==a['id']));db.delete(link);db.commit()
    r=client.post('/api/monitor-policies',json={'title':'无身份不控制','entity_ids':[obj['id']],'source_ids':[src['id']],
        'sensor_asset_ids':[a['id']],'selection_requirement':{'allowed_modalities':['numeric']},'fields':['temperature'],'valid_until':(now()+timedelta(days=1)).isoformat()})
    assert r.status_code==409 and supplier['calls']==[]

def test_unidentified_legacy_control_blocks_until_original_receipt_closed(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a])
    with Session(engine()) as db:
        old=Acquisition(workspace_id=m['workspace_id'],entity_id=obj['id'],capability_id=src['id'],idempotency_key='legacy-unknown',status='unknown',data={'policy_id':m['id']})
        db.add(old);db.commit();old_id=old.id
    assert scan(client,m)==[];assert current(client)['status']=='escalated'
    assert '未归属统一设备' in current(client)['data']['candidates'][0]['reasons'][0]
    # Changing status here is a test fixture for a separately verified terminal supplier guarantee.
    with Session(engine()) as db:db.get(Acquisition,old_id).status='rejected';db.commit()
    plan=current(client);r=client.post('/api/sensing/plans/'+plan['id']+'/retry',json={'expected_version':plan['version'],'reason':'原旧动作已通过另行永久结束核查'})
    assert r.status_code==200 and len(r.json()['acquisition_ids'])==1

def test_confirmed_collection_releases_shared_device_and_stale_release_cannot_clear_new_owner(client,supplier):
    obj,src,bsrc,a,b,dev=shared(client,supplier);m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,bsrc,[b]);first=scan(client,m1)[0]
    dispatch(m1,first);observe(client,m1,obj,src,first,{'temperature':27})
    with Session(engine()) as db:
        acq=db.get(Acquisition,first);hold=db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id']));assert hold.acquisition_id is None
        row=db.scalar(select(Observation));assert row.data['sensor_evidence']['device_id']==dev['id']
        # Another legitimate request has obtained the shared resource. A delayed old release must be fenced by request ID.
        next_acq=Acquisition(workspace_id=m1['workspace_id'],entity_id=obj['id'],capability_id=bsrc['id'],idempotency_key='new-owner',status='unknown',data={'sensor_device_id':dev['id']});db.add(next_acq);db.flush()
        hold.acquisition_id=next_acq.id;db.flush();release(db,principal(m1),acq);assert hold.acquisition_id==next_acq.id
        db.commit()

def test_approved_device_scope_cannot_follow_rebinding(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);b=asset(client,obj,src);m=monitor(client,obj,src,[a])
    assert rebind(client,a,device_of(client,b)).status_code==200
    assert scan(client,m)==[] and current(client)['status']=='escalated'
    assert '超出原监测批准范围' in ' '.join(current(client)['data']['candidates'][0]['reasons'])
    assert supplier['calls']==[]

def test_same_device_reconfirmation_restores_review_permission_without_scope_expansion(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);dev=device_of(client,a);m=monitor(client,obj,src,[a])
    r=client.post('/api/sensing/assets/'+a['id']+'/device',json={'expected_version':a['version'],'device_id':dev['id'],
        'device_expected_version':dev['version'],'reason':'重新核查明确原设备归属','reconfirm':True});assert r.status_code==200,r.text
    assert r.json()['device_id']==dev['id'];assert len(scan(client,m))==1

def test_identity_scope_edited_after_draft_cannot_be_approved(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);b=asset(client,obj,src)
    r=client.post('/api/monitor-policies',json={'title':'设备范围固定','entity_ids':[obj['id']],'source_ids':[src['id']],
        'sensor_asset_ids':[a['id']],'selection_requirement':{'allowed_modalities':['numeric']},'fields':['temperature'],'valid_until':(now()+timedelta(days=1)).isoformat()});assert r.status_code==201
    draft=r.json();assert rebind(client,a,device_of(client,b)).status_code==200
    r=client.post('/api/monitor-policies/'+draft['id']+'/approve',json={'expected_version':draft['version'],'reason':'旧范围不能自动改成新设备'})
    assert r.status_code==409 and supplier['calls']==[]

def test_explicit_retry_of_unissued_request_after_device_review_keeps_same_budget_and_request(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);dev=device_of(client,a);m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0]
    paused=client.post('/api/sensing/devices/'+dev['id']+'/pause',json={'expected_version':dev['version'],'reason':'暂时停用'}).json()
    dispatch(m,identifier);assert supplier['calls']==[]
    assert client.post('/api/sensing/devices/'+dev['id']+'/resume',json={'expected_version':paused['version'],'reason':'已核查恢复同一设备'}).status_code==200
    plan=current(client);r=client.post('/api/sensing/plans/'+plan['id']+'/retry',json={'expected_version':plan['version'],'reason':'明确核查恢复原未下发请求'})
    assert r.status_code==200,r.text;assert r.json()['acquisition_ids']==[identifier]
    dispatch(m,identifier);assert len(supplier['calls'])==1
    with Session(engine()) as db:assert len(db.scalars(select(Acquisition)).all())==1

def test_unissued_retry_cannot_increase_reserved_quote(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);dev=device_of(client,a);m=monitor(client,obj,src,[a],max_total_cost_minor=10)
    client.patch('/api/sensing/assets/'+a['id'],json={'expected_version':a['version'],'title':a['title'],'specification':{**a['data'],'quote_ceiling_minor':1},'reason':'报价改变'})
    identifier=scan(client,m)[0]
    # An asset update between selection and dispatch blocks the unissued request.
    fresh=next(r for r in client.get('/api/sensing').json()['assets'] if r['id']==a['id'])
    client.patch('/api/sensing/assets/'+a['id'],json={'expected_version':fresh['version'],'title':a['title'],'specification':{**a['data'],'quote_ceiling_minor':2},'reason':'再次报价改变'})
    dispatch(m,identifier);plan=current(client);r=client.post('/api/sensing/plans/'+plan['id']+'/retry',json={'expected_version':plan['version'],'reason':'不允许借重试扩大报价'})
    assert r.status_code==200 and r.json()['acquisition_ids']==[] and supplier['calls']==[]

def test_quality_data_does_not_release_running_device_before_bound_terminal_receipt(client,supplier):
    obj,src,bsrc,a,b,dev=shared(client,supplier);supplier['modes'][a['external_id']]='running'
    m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0];dispatch(m,identifier)
    observe(client,m,obj,src,identifier,{'temperature':27})
    with Session(engine()) as db:
        assert db.get(Acquisition,identifier).status=='awaiting_observation'
        assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id==identifier
    assert current(client)['status']=='awaiting_observation'
    supplier['receipts'][supplier['calls'][0]['request_key']].update(status='completed',terminal=True,no_future_device_activity=True)
    r=client.post('/api/acquisitions/'+identifier+'/reconcile');assert r.status_code==200,r.text
    assert r.json()['status']=='succeeded' and current(client)['status']=='completed'
    with Session(engine()) as db:assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id is None

def test_legacy_weak_reject_cannot_release_unknown_identity_hold(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);key='legacy-guarantee'
    with Session(engine()) as db:
        from generic_agents.models import SourceReservation
        old=Acquisition(workspace_id=m['workspace_id'],entity_id=obj['id'],capability_id=src['id'],idempotency_key=key,status='unknown',data={'policy_id':m['id']})
        db.add(old);db.flush();db.add(SourceReservation(workspace_id=m['workspace_id'],source_id=src['id'],acquisition_id=old.id));db.commit();identifier=old.id
    supplier['receipts'][key]={'receipt_id':'legacy-receipt','request_key':key,'status':'rejected'}
    assert client.post('/api/acquisitions/'+identifier+'/reconcile').status_code==409
    with Session(engine()) as db:assert db.get(Acquisition,identifier).status=='unknown'
    supplier['receipts'][key].update(terminal=True,no_future_observation=True)
    assert client.post('/api/acquisitions/'+identifier+'/reconcile').status_code==200


def test_identity_script_is_served(client):
    response=client.get('/assets/device-identity.js');assert response.status_code==200
    assert 'javascript' in response.headers['content-type'] and 'function augmentDeviceIdentity' in response.text
    assert '/assets/device-identity.js' in client.get('/').text


def test_worker_unknown_shared_hold_survives_new_session_and_never_replays(client,supplier):
    from generic_agents.models import Job,ObservationPlan
    from generic_agents.worker import process_once
    obj,src,bsrc,a,b,dev=shared(client,supplier)
    supplier['modes'][a['external_id']]='unknown'
    m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,bsrc,[b]);identifier=scan(client,m1)[0]
    with Session(engine()) as db:
        job_id=db.scalar(select(Job.id).where(Job.data['acquisition_id'].as_string()==identifier))
    assert process_once(job_id) and not process_once(job_id)
    assert scan(client,m2)==[] and len(supplier['calls'])==1
    with Session(engine()) as db:
        assert db.get(Acquisition,identifier).status=='unknown'
        assert db.get(Job,job_id).status=='failed'
        assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id==identifier
        second=db.scalar(select(ObservationPlan).where(ObservationPlan.policy_id==m2['id']))
        assert second.status=='waiting_resource'
    # Restart-like new sessions retain the original action; only GET reconciles it.
    assert client.post('/api/acquisitions/'+identifier+'/reconcile').status_code==200
    assert scan(client,m2)==[] and len(supplier['calls'])==1
    with Session(engine()) as db:
        assert db.get(Acquisition,identifier).status=='awaiting_observation'
        assert db.scalar(select(DeviceReservation).where(DeviceReservation.device_id==dev['id'])).acquisition_id==identifier


@pytest.mark.parametrize('changes',[
    {'title':'   '},{'identity_issuer':'\ufdfa'*6},{'identity_reference':'\ufdfa'*12},
    {'identity_reference':'serial\x00'}])
def test_identity_rejects_empty_control_or_normalized_overflow(changes):
    value={'title':'设备','identity_scheme':'inventory_tag','identity_issuer':'台账',
        'identity_reference':'serial','reason':'核查依据',**changes}
    with pytest.raises(ValueError):DeviceCreate.model_validate(value)
