"""Approved proactive diagnosis and authoritative human verification, real isolated PG."""
from datetime import timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.db import engine
from generic_agents.models import Entity,QualityPolicy,Job,Membership,Relation,now
from generic_agents.worker import process_once
from generic_agents.quality import schedule_once
from .test_relations import package,save,claim,review,query
from .test_vertical_slice import entity

def post(client,path,value):
    r=client.post(path,json=value);assert r.status_code in {200,201},r.text;return r.json()

def policy(client,pkg):
    return post(client,'/api/quality/policies',{'name':'合成来源质量策略','package_ids':[pkg['id']],
        'interval_seconds':60,'reason':'批准登记变更及周期确定性检查，不修改事实'})

def setup(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);r=save(client,pkg,a,b);p=policy(client,pkg)
    assert process_once();return pkg,a,b,r,p

def update(client,a):
    r=client.patch('/api/entities/'+a['id'],json={'expected_version':a['version'],
        'attributes':{**a['data'],'temperature':27},'reason':'有依据的合成来源版本更新'});assert r.status_code==200,r.text;return r.json()

def issue(client):return next(i for i in client.get('/api/overview').json()['issues'] if i['kind']=='relation_source')
def verify(client,i):return post(client,'/api/quality/issues/'+i['id']+'/verify',{'expected_version':i['version'],'reason':'核查当前关系来源与权威版本'})

def test_change_auto_detect_failure_saved_correction_confirm_verify_history(client):
    pkg,a,b,r,p=setup(client);historical=query(client,pkg,a,[a,b]);a=update(client,a);assert process_once()
    i=issue(client);assert i['data']['diagnosis']['current_endpoint_versions'][a['id']]==2
    assert i['data']['impact']['queries'][0]['id']==historical['id']
    failed=verify(client,i);assert not failed['verified'] and failed['issue']['status']=='open'
    assert len(client.get('/api/quality/issues/'+i['id']+'/history').json())==2
    correction=client.patch('/api/relations/'+r['id'],json={**claim(pkg,a,b),'expected_version':r['version']});assert correction.status_code==200,correction.text
    failed=verify(client,failed['issue']);assert not failed['verified'] and '未重新确认' in failed['issue']['data']['last_verification']['diagnosis']['problem']
    r=review(client,correction.json(),'confirmed');passed=verify(client,failed['issue']);assert passed['verified'] and passed['issue']['status']=='resolved'
    history=client.get('/api/quality/issues/'+i['id']+'/history').json();assert len(history)==4 and history[0]['data']['operation']=='verified'
    assert client.get('/api/data/queries/'+historical['id']).json()['data']==historical['data']
    assert client.post('/api/quality/issues/'+i['id']+'/verify',json={'expected_version':passed['issue']['version'],'reason':'不能重复改写结案'}).status_code==409

def test_repeated_diagnosis_deduplicates_and_recurrence_reopens_history(client):
    pkg,a,b,r,p=setup(client);a=update(client,a);process_once();i=issue(client)
    client.post('/api/data/diagnose');client.post('/api/data/diagnose')
    assert len(client.get('/api/quality/issues/'+i['id']+'/history').json())==1
    r=review(client,r,'withdrawn');assert verify(client,i)['verified']
    # A fresh confirmed claim, then another source change, reopens the same logical issue.
    rr=client.patch('/api/relations/'+r['id'],json={**claim(pkg,a,b),'expected_version':r['version']}).json();review(client,rr,'confirmed')
    update(client,a);client.post('/api/data/diagnose');reopened=issue(client)
    assert reopened['id']==i['id'] and reopened['status']=='open' and reopened['data']['last_verification'] is None
    assert len(client.get('/api/quality/issues/'+i['id']+'/history').json())==3

def test_withdrawal_contains_invalid_claim_without_claiming_sufficient_data(client):
    pkg,a,b,r,p=setup(client);a=update(client,a);process_once();i=issue(client);review(client,r,'withdrawn')
    result=verify(client,i);assert result['verified'] and '仍需补充' in result['issue']['data']['last_verification']['disposition']
    assert query(client,pkg,a,[a,b])['status']=='insufficient'

def test_pause_fences_queued_policy_work_and_no_mutation_trigger(client):
    pkg,a,b,r,p=setup(client);a=update(client,a)
    post(client,'/api/quality/policies/'+p['id']+'/pause',{'expected_version':p['version'],'reason':'暂停合成范围检查'})
    assert process_once();assert not client.get('/api/overview').json()['issues']
    update(client,a)
    with Session(engine()) as db:assert not db.scalar(select(Job.id).where(Job.kind=='diagnose_data',Job.status=='queued'))

def test_periodic_detects_expiration_and_recovers_stored_schedule(client):
    pkg,a,b,r,p=setup(client)
    with Session(engine()) as db:
        rel=db.get(Relation,r['id']);rel.valid_until=now()-timedelta(seconds=1)
        from generic_agents.relations import claim_hash
        rel.data={**rel.data,'claim_hash':claim_hash(rel)}
        row=db.get(QualityPolicy,p['id']);row.next_run_at=now()-timedelta(seconds=1);db.commit()
    assert schedule_once()==1;assert schedule_once()==0;assert process_once()
    assert '有效期' in issue(client)['data']['diagnosis']['problem']
    assert client.get('/api/quality').json()['policies'][0]['data']['last_result']['trigger'].startswith('periodic:')

def test_revoke_admin_stops_policy_and_viewer_cannot_change_or_verify(client,account):
    pkg,a,b,r,p=setup(client);a=update(client,a)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));membership.role='viewer'
        policyrow=db.get(QualityPolicy,p['id']);policyrow.next_run_at=now()-timedelta(seconds=1);db.commit()
    assert process_once();assert not client.get('/api/overview').json()['issues'];schedule_once()
    assert client.get('/api/quality').json()['policies'][0]['status']=='blocked_authorization'
    assert client.post('/api/quality/policies',json={'name':'非法','package_ids':[pkg['id']],'interval_seconds':60,'reason':'越权'}).status_code==403

def test_full_policy_scan_continues_after_read_limit_and_finds_later_record(client):
    pkg=package(client)
    with Session(engine()) as db:
        for n in range(1005):
            db.add(Entity(id=f'00000000-0000-0000-0000-{n:012d}',workspace_id=pkg['workspace_id'],namespace='test',
                entity_type='site',external_id='large-'+str(n),schema_capability_id=pkg['id'],
                data={'name':'合成批次档案','temperature':'损坏' if n==1004 else 24}))
        db.commit()
    p=policy(client,pkg);assert process_once();first=client.get('/api/quality').json()['policies'][0]['data']['last_result']
    assert first['truncated'] and not first['continuation']['entities_done']
    for _ in range(10):assert process_once()
    last=client.get('/api/quality').json()['policies'][0]['data']['last_result'];assert last['scanned']==5 and not last['truncated']
    issues=client.get('/api/overview').json()['issues'];assert len(issues)==1 and issues[0]['kind']=='invalid_schema'

def test_no_auto_diagnosis_without_approved_policy_and_assets_available(client):
    pkg=package(client);a=entity(client,pkg);b=entity(client,pkg);save(client,pkg,a,b);update(client,a)
    with Session(engine()) as db:assert not db.scalar(select(Job.id).where(Job.kind=='diagnose_data'))
    script=client.get('/assets/quality.js');assert script.status_code==200 and script.headers['cache-control']=='private, no-store'

def test_pause_resume_persists_reason_and_invalidates_old_jobs(client):
    pkg,a,b,r,p=setup(client);a=update(client,a)
    paused=post(client,'/api/quality/policies/'+p['id']+'/pause',{'expected_version':p['version'],'reason':'暂停以便核查'})
    resumed=post(client,'/api/quality/policies/'+p['id']+'/resume',{'expected_version':paused['version'],'reason':'管理员核对原范围后重新批准'})
    assert resumed['data']['resume_reason']=='管理员核对原范围后重新批准'
    assert process_once();assert not client.get('/api/overview').json()['issues']
    assert process_once();assert issue(client)['status']=='open'
