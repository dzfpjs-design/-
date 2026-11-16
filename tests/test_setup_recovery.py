import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SetupAnalysis,SetupMaterial,Job,Capability
from generic_agents.config import settings
from generic_agents.contracts import digest
from tests.test_setup import upload,config
from tests.test_setup_understanding import prepared,fake,start,process


def save(client,value):
    current=client.get('/api/setup/draft').json()
    r=client.put('/api/setup/draft',json={'revision':current['revision'],'configuration':value});assert r.status_code==200,r.text
    return r.json()


def preview(client,target):
    body={'revision':client.get('/api/setup/draft').json()['revision'],'target_revision':target}
    r=client.post('/api/setup/draft/restore/preview',json=body);assert r.status_code==200,r.text
    return {**body,'preview_hash':r.json()['preview_hash']},r.json()


def accepted(client,monkeypatch):
    body,m=prepared(client);calls=fake(monkeypatch);row,_=start(client,body);process(row)
    reviewed=client.get('/api/setup/understanding/'+row['id']).json()
    applied=client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':reviewed['version'],
        'revision':body['revision'],'names':['异常复核']});assert applied.status_code==200,applied.text
    return applied.json(),calls


def undo(client,applied):
    r=client.post('/api/setup/understanding/'+applied['analysis']['id']+'/undo',json={'version':applied['analysis']['version'],
        'revision':applied['revision'],'names':['异常复核']});assert r.status_code==200,r.text
    return r.json()


def test_restore_retains_later_versions_originals_and_invalidates_old_connector_checks(client):
    first=config();first.update(businesses=['原业务'],flows={'原业务':{'steps':[]}},
        connections=[{'id':'g','status':'validated'}],resources=[{'id':'r','status':'added','checks':{'read':'passed'}}])
    old=save(client,first)
    later=upload(client,b'newer original','later.txt')
    latest=save(client,{**config([later]),'businesses':['新业务']})
    body,plan=preview(client,old['revision'])
    assert plan['businesses']['removed']==['新业务'] and plan['businesses']['added']==['原业务']
    assert plan['retained_newer_materials'][0]['name']=='later.txt'
    response=client.post('/api/setup/draft/restore',json=body);assert response.status_code==200,response.text
    restored=response.json()['configuration']
    assert restored['businesses']==['原业务'] and restored['materials'][0]['archived']
    assert restored['connections'][0]['status']=='pending' and restored['resources'][0]['checks']=={}
    assert client.get('/api/setup/materials/'+later['id']+'/content').content==b'newer original'
    assert any(r['revision']==latest['revision'] and r['configuration']['businesses']==['新业务'] for r in client.get('/api/setup/draft/history').json())


def test_restore_cas_and_material_change_reject_a_previously_approved_preview(client):
    m=upload(client);old=save(client,config([m]));save(client,{**config([m]),'businesses':['later']})
    body,_=preview(client,old['revision'])
    with Session(engine()) as db:
        material=db.get(SetupMaterial,m['id']);material.version+=1;db.commit()
    assert client.post('/api/setup/draft/restore',json=body).status_code==409
    fresh,_=preview(client,old['revision']);save(client,{**config([m]),'businesses':['concurrent']})
    assert client.post('/api/setup/draft/restore',json=fresh).status_code==409
    assert client.get('/api/setup/draft').json()['configuration']['businesses']==['concurrent']


def test_corrupt_original_blocks_restore_before_human_review(client):
    m=upload(client);old=save(client,config([m]));save(client,{**config([m]),'businesses':['later']})
    with Session(engine()) as db:
        from generic_agents.models import Evidence
        row=db.get(SetupMaterial,m['id']);ev=db.get(Evidence,row.evidence_id);path=settings().storage_dir/ev.storage_key
    path.write_bytes(b'corrupted')
    r=client.post('/api/setup/draft/restore/preview',json={'revision':client.get('/api/setup/draft').json()['revision'],
        'target_revision':old['revision']});assert r.status_code==409


def test_material_metadata_recovery_reconfirms_affected_flow(client):
    import json
    from generic_agents.instance_compiler import signature_value

    m=upload(client)
    value=config([m]);value.update(businesses=['核对'],flows={'核对':{'confirmedAt':None,'confirmedSignature':None,
        'steps':[{'sourceId':m['id'],'name':'来源核对'}]}})
    initial=save(client,value)
    value=copy.deepcopy(initial['configuration']);flow=value['flows']['核对']
    assert flow['confirmedSignature'] is None
    flow['confirmedAt']='2026-10-18T00:00:00+00:00'
    flow['confirmedSignature']=json.dumps(signature_value(value,flow),ensure_ascii=False)
    old=save(client,value)
    assert old['configuration']['flows']['核对']['confirmedSignature']==flow['confirmedSignature']
    assert old['configuration']['flows']['核对']['confirmedAt']==flow['confirmedAt']
    changed=copy.deepcopy(old['configuration']);changed['materials'][0]['archived']=True;changed['materials'][0]['note']='新注释'
    save(client,changed)
    body,plan=preview(client,old['revision'])
    assert plan['flows_requiring_confirmation']==['核对'] and plan['material_changes'][0]['after']['archived'] is False
    restored=client.post('/api/setup/draft/restore',json=body).json()['configuration']
    assert restored['flows']['核对']['confirmedSignature'] is None
    row=client.get('/api/setup/materials/'+m['id']).json();assert not row['archived'] and row['note']==''


def test_paid_proposal_reuse_after_undo_creates_no_cloud_call_or_job(client,monkeypatch):
    applied,calls=accepted(client,monkeypatch);undone=undo(client,applied)
    with Session(engine()) as db:count=len(db.scalars(select(Job)).all())
    r=client.post('/api/setup/understanding/'+undone['analysis']['id']+'/reuse',json={'version':undone['analysis']['version'],
        'revision':undone['revision']});assert r.status_code==201,r.text
    reused=r.json();assert reused['status']=='review' and reused['reused_from']==applied['analysis']['id'] and not reused['receipts']
    assert reused['result']==applied['analysis']['result']
    saved=client.post('/api/setup/understanding/'+reused['id']+'/apply',json={'version':reused['version'],
        'revision':undone['revision'],'names':['异常复核']});assert saved.status_code==200,saved.text
    assert saved.json()['configuration']['businesses']==['异常复核'] and len(calls)==1
    with Session(engine()) as db:assert count==len(db.scalars(select(Job)).all())


def test_local_reuse_preserves_current_unrelated_connections_and_rejects_changed_business(client,monkeypatch):
    applied,calls=accepted(client,monkeypatch);undone=undo(client,applied)
    cfg=copy.deepcopy(undone['configuration']);cfg['connections']=[{'id':'new-local-gateway'}]
    saved=save(client,cfg)
    reuse=client.post('/api/setup/understanding/'+undone['analysis']['id']+'/reuse',json={'version':undone['analysis']['version'],
        'revision':saved['revision']});assert reuse.status_code==201
    r=reuse.json();result=client.post('/api/setup/understanding/'+r['id']+'/apply',json={'version':r['version'],
        'revision':saved['revision'],'names':['异常复核']});assert result.status_code==200
    assert result.json()['configuration']['connections']==cfg['connections']
    changed=copy.deepcopy(undone['configuration']);changed['instanceProfile']['domain']='另一个领域';save(client,changed)
    assert client.post('/api/setup/understanding/'+undone['analysis']['id']+'/reuse',json={'version':undone['analysis']['version'],
        'revision':client.get('/api/setup/draft').json()['revision']}).status_code==409
    assert len(calls)==1


def test_reuse_blocks_archived_source_model_changes_and_forged_checkpoint(client,monkeypatch):
    applied,calls=accepted(client,monkeypatch);undone=undo(client,applied)
    path='/api/setup/understanding/'+undone['analysis']['id']+'/reuse'
    body={'version':undone['analysis']['version'],'revision':undone['revision']}
    with Session(engine()) as db:
        source=db.get(SetupAnalysis,undone['analysis']['id']);material=db.get(SetupMaterial,source.data['request']['materials'][0]['id'])
        material_id=material.id;material.archived=True;db.commit()
    assert client.post(path,json=body).status_code==409
    with Session(engine()) as db:
        material=db.get(SetupMaterial,material_id);material.archived=False;source=db.get(SetupAnalysis,undone['analysis']['id'])
        cap=db.get(Capability,source.data['request']['model_id']);cap_id=cap.id;cap.status='disabled';db.commit()
    assert client.post(path,json=body).status_code==409
    with Session(engine()) as db:
        cap=db.get(Capability,cap_id);cap.status='published';source=db.get(SetupAnalysis,undone['analysis']['id'])
        source.data={**source.data,'result_hash':'corrupted'};db.commit()
    assert client.post(path,json=body).status_code==409 and len(calls)==1


def test_reuse_rebased_guard_prevents_later_human_edits_being_overwritten(client,monkeypatch):
    applied,_=accepted(client,monkeypatch);undone=undo(client,applied)
    r=client.post('/api/setup/understanding/'+undone['analysis']['id']+'/reuse',json={'version':undone['analysis']['version'],
        'revision':undone['revision']}).json()
    changed=copy.deepcopy(undone['configuration']);changed['setupNotes']={'manual':'新修改'};saved=save(client,changed)
    assert client.post('/api/setup/understanding/'+r['id']+'/apply',json={'version':r['version'],
        'revision':saved['revision'],'names':['异常复核']}).status_code==409


def test_history_keyset_pagination_ignores_newer_concurrent_saves(client):
    for i in range(6):save(client,{**config(),'businesses':[str(i)]})
    first=client.get('/api/setup/draft/history?limit=3').json();cut=min(r['revision'] for r in first)
    save(client,{**config(),'businesses':['concurrent']})
    page=client.get(f'/api/setup/draft/history?limit=3&before_revision={cut}').json()
    assert all(r['revision']<cut for r in page) and len(page)==3
