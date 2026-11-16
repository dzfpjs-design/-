"""Real local rules and persisted scoped records; no cloud or physical sensor claim."""
import copy
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation,Entity
from generic_agents.worker import process_once
from generic_agents.evidence_question import normalize
from generic_agents.gateway import ModelBlocked
from tests.test_vertical_slice import manifest,publish
from tests.test_instance_workspace import prepared,enable,object_row
from tests.test_evidence_assessment import request,run

def setup(client,process=True):
    release,_,_=prepared(client);enable(client,release);obj=object_row(client,release)
    source=publish(client,manifest('source',name='declared_manual_source',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[],'timezone':'Asia/Shanghai'},
        input_schema={'type':'object'},output_schema={'type':'object'},data_policy={'cloud_allowed':False}))
    extractor=publish(client,manifest('tool',name='local_value_rules',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[]},
        input_schema={'type':'object'},output_schema={'type':'object'},data_policy={'cloud_allowed':False}))
    clock=datetime.now(timezone.utc)-timedelta(minutes=1)
    body={'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'observed_at':clock.isoformat(),
        'business_day':str((clock+timedelta(hours=8)).date()),'attributes':{'公开登记值':27.5},'extractor_id':extractor['id'],'startup_mode':'manual'}
    response=client.post('/api/observations',json=body);assert response.status_code==201,response.text;obs=response.json()
    if process:
        from sqlalchemy import select
        from generic_agents.models import Job
        with Session(engine()) as db:job=db.scalar(select(Job).where(Job.kind=='process_observation',Job.data['observation_id'].as_string()==obs['id']));identity=job.id
        assert process_once(identity,deliver=False)
    return release,obj,obs,body

def assess(client,release,obj,**kw):return run(client,request(release,[obj['id']],goal='available_evidence',required_modalities=['archive','observations'],**kw))

def test_saved_archive_and_real_local_rule_values_are_scoped_without_fake_current_state(client):
    r,obj,obs,_=setup(client);result=assess(client,r,obj);value=result['result']
    assert result['request']['source_catalog_version']==2 and value['rule_version']=='mixed-evidence-v2'
    counts=value['objects'][0];assert counts['saved_records']==2 and counts['processing_records']==1 and counts['verified_records']==2
    archive,reading=value['records'];assert archive['reported_fields'][0]=={'name':'名称','value':'公开资料场景'}
    assert reading['reported_fields']==[{'name':'公开登记值','value':27.5}] and reading['integrity_verified'] and not reading['source_time_verified']
    assert not counts['current_state_sufficient'] and value['cloud_calls']==0 and not value['entity_state_updated']
    assert client.get('/api/evidence-assessments/'+result['id']+'/freshness').json()['metadata_versions_unchanged']
    assert client.get('/api/evidence-assessments/'+result['id']+'/export').json()['assessment']['result']==value

def test_corrupt_extracted_values_are_not_displayed_and_old_receipt_is_historical(client):
    r,obj,obs,_=setup(client);old=assess(client,r,obj)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);value=copy.deepcopy(row.data);value['extracted']['attributes']['公开登记值']=999;row.data=value;db.commit()
    assert not client.get('/api/evidence-assessments/'+old['id']+'/freshness').json()['metadata_versions_unchanged']
    new=assess(client,r,obj);record=new['result']['records'][1]
    assert not record['integrity_verified'] and record['reported_fields']==[] and record['source_sha256']==[]
    assert client.get('/api/evidence-assessments/'+old['id']).json()['result']['records'][1]['reported_fields'][0]['value']==27.5

def test_scalar_clock_and_assignment_are_part_of_v2_source_receipt(client):
    r,obj,obs,_=setup(client);old=assess(client,r,obj)
    with Session(engine()) as db:row=db.get(Observation,obs['id']);row.observed_at+=timedelta(hours=1);db.commit()
    assert not client.get('/api/evidence-assessments/'+old['id']+'/freshness').json()['metadata_versions_unchanged']
    assert not assess(client,r,obj)['result']['records'][1]['integrity_verified']

def test_pending_original_is_visible_as_unprocessed_record_and_media_compatibility(client):
    r,obj,obs,_=setup(client,process=False);value=assess(client,r,obj)['result']
    assert value['objects'][0]['saved_records']==2 and value['objects'][0]['processing_records']==0
    assert value['records'][1]['record_stage']=='saved_record' and value['records'][1]['reported_fields'][0]['value']==27.5
    assert any(g['kind']=='processing' for g in value['objects'][0]['gaps'])
    old=run(client,request(r,[obj['id']],required_modalities=['video']))
    assert 'source_catalog_version' not in old['request'] and old['result']['rule_version']=='media-evidence-v1' and old['result']['records']==[]

def test_other_object_readings_and_invalid_archive_do_not_leak_as_verified_values(client):
    r,obj,obs,_=setup(client);other=object_row(client,r,index=1)
    result=assess(client,r,other);assert len(result['result']['records'])==1
    assert any(g['kind']=='missing_saved_processing' for g in result['result']['objects'][0]['gaps'])
    with Session(engine()) as db:row=db.get(Entity,other['id']);row.data={};db.commit()
    # Current object-definition validation rejects the malformed archive
    # before assessment creation; no weak "verified" projection is returned.
    rejected=client.post('/api/evidence-assessments',json=request(r,[other['id']],goal='available_evidence',required_modalities=['archive','observations']))
    assert rejected.status_code==409 and 'result' not in rejected.json()

def test_old_cloud_preview_cannot_expand_to_new_catalog_and_new_preview_can():
    value={'entity_ids':['chosen'],'goal':'available_evidence','required_modalities':['archive','observations'],'lookback_hours':24,
        'purpose':'列出保存值与待核事项','explanation':'只查询所选对象','clarifications':[],'unsupported_parts':[]}
    with pytest.raises(ModelBlocked,match='原发送预览'):normalize(value,{'entity_ids':['chosen'],'cloud_input':{'allowed_modalities':['imagery','audio','video']}})
    assert normalize(value,{'entity_ids':['chosen'],'cloud_input':{'allowed_modalities':['archive','observations']}})[1]=='needs_confirmation'

def test_projection_is_bounded_without_modifying_original_values():
    from generic_agents.mixed_evidence import fields
    original={'公开文字':'字'*3000,'结构化字段':{'large':'字'*9000}}
    before=copy.deepcopy(original);projected,truncated=fields(original)
    assert truncated and len(projected[0]['value'])==2000 and isinstance(projected[1]['value'],str) and original==before

def test_changed_original_values_and_foreign_source_are_not_returned(client):
    from generic_agents.models import Capability,Workspace
    from sqlalchemy.exc import IntegrityError
    r,obj,obs,body=setup(client)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);value=copy.deepcopy(row.data)
        value['attributes']['公开登记值']=999;row.data=value;db.commit()
    assert assess(client,r,obj)['result']['records'][1]['reported_fields']==[]
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);value=copy.deepcopy(row.data)
        value['attributes']=body['attributes'];row.data=value
        foreign=Workspace(name='隔离测试来源');db.add(foreign);db.flush()
        source=db.get(Capability,row.source_id);original_workspace=source.workspace_id
        source.workspace_id=foreign.id
        # Typed-payload ownership is now protected at the database boundary.
        # The attempted cross-workspace source mutation must fail atomically.
        with pytest.raises(IntegrityError) as rejected:db.commit()
        assert rejected.value.orig.diag.constraint_name=='ck_capabilities_typed_payload_v131'
        db.rollback()
        assert db.get(Capability,row.source_id).workspace_id==original_workspace
    record=assess(client,r,obj)['result']['records'][1]
    assert not record['integrity_verified'] and record['reported_fields']==[]

def test_scoped_event_delivery_preserves_other_workspace_pending_work(account):
    from sqlalchemy import select
    from generic_agents.models import Event,Memory,Workspace
    from generic_agents.worker import deliver_events
    with Session(engine()) as db:
        foreign=Workspace(name='另一个隔离测试工作区');db.add(foreign);db.flush()
        chosen=Event(workspace_id=account[2],kind='perception.daily',idempotency_key='chosen',status='pending_delivery',data={'public':'chosen'})
        untouched=Event(workspace_id=foreign.id,kind='perception.daily',idempotency_key='other',status='pending_delivery',data={'public':'other'})
        db.add_all([chosen,untouched]);db.commit();chosen_id,other_id=chosen.id,untouched.id
    deliver_events(workspace_id=account[2])
    with Session(engine()) as db:
        assert db.get(Event,chosen_id).status=='received' and db.get(Event,other_id).status=='pending_delivery'
        assert len(db.scalars(select(Memory)).all())==1
    deliver_events()
    with Session(engine()) as db:assert db.get(Event,other_id).status=='received'

def test_changed_original_file_blocks_pending_reading_values_without_erasing_history(client):
    from generic_agents.models import Evidence,CloudCall
    from sqlalchemy import select
    from generic_agents.config import settings
    from tests.test_file_perception import setup as file_setup
    uploaded=client.post('/api/evidence',content=b'public manually reported QA value',headers={'Content-Type':'text/plain'})
    assert uploaded.status_code==201
    ev=uploaded.json();r,obj,_,body=setup(client)
    _,source,_,extractor=file_setup(client)
    # Existing explicit file adapter is used only to save a pending input.
    # No job for this cloud extractor is executed; no cloud inference is claimed.
    body={**body,'source_id':source['id'],'extractor_id':extractor['id'],
        'business_day':str(datetime.fromisoformat(body['observed_at']).date()),'evidence_ids':[ev['id']]}
    response=client.post('/api/observations',json=body);assert response.status_code==201,response.text
    old=assess(client,r,obj)
    old_record=next(v for v in old['result']['records'] if v['source_ref']['id']==response.json()['id'])
    assert old_record['source_sha256']==[ev['sha256']] and old_record['record_stage']=='saved_record'
    with Session(engine()) as db:path=settings().storage_dir/db.get(Evidence,ev['id']).storage_key
    original=path.read_bytes()
    try:
        path.write_bytes(b'changed test original')
        damaged=next(v for v in assess(client,r,obj)['result']['records'] if v['source_ref']['id']==response.json()['id'])
        assert not damaged['integrity_verified'] and damaged['reported_fields']==[] and damaged['source_sha256']==[]
        history=client.get('/api/evidence-assessments/'+old['id']).json()['result']['records']
        assert next(v for v in history if v['source_ref']['id']==response.json()['id'])['reported_fields'][0]['value']==27.5
    finally:path.write_bytes(original)
    assert next(v for v in assess(client,r,obj)['result']['records'] if v['source_ref']['id']==response.json()['id'])['integrity_verified']
    with Session(engine()) as db:assert db.scalar(select(CloudCall.id)) is None

def test_unclocked_video_candidates_are_not_counted_as_registered_observations(client):
    r,obj,obs,_=setup(client)
    with Session(engine()) as db:db.get(Observation,obs['id']).status='extracted_unclocked';db.commit()
    value=assess(client,r,obj)['result']
    assert len(value['records'])==1 and value['records'][0]['modality']=='archive'
    assert value['objects'][0]['saved_records']==1 and value['objects'][0]['processing_records']==0
