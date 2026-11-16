"""Original native outputs, common ledger, deduplication, scope and recovery."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.worker import process_once,deliver_events
from generic_agents.config import settings
from tests.test_geo_codec import scenes,pytestmark
from tests.test_geo_pairs import pairs
from tests.test_workflow_geo_pairs import configured
from tests.test_workflow_abilities import finish_job
from tests.test_instance_workspace import complete
from tests.test_evidence_assessment import run,request

def setup_geo(client,pairs):
    a,b,cap,release,sources,body=configured(client,pairs)
    base='/api/workspace/instances/'+release['instance_id'];r=client.post(base+'/tasks',json=body);assert r.status_code==201,r.text
    task=finish_job(client,r.json());assert task['status']=='workflow_waiting'
    return a,b,release,body,base,task

def bridge(client,base):
    preview=client.get(base+'/native-receipts');assert preview.status_code==200,preview.text
    response=client.post(base+'/native-receipts/sync',json={'preview_hash':preview.json()['preview_hash']});assert response.status_code==200,response.text
    return response.json(),client.get(base+'/native-receipts').json()

def test_geo_common_ledger_deduplicates_media_and_observation_and_preserves_review(client,pairs):
    a,b,release,body,base,task=setup_geo(client,pairs)
    saved,listing=bridge(client,base);assert saved['created']==1 and saved['cloud_calls']==0
    row=listing['records'][0];assert row['data_received'] and not row['integrity_error'];assert row['record']['attributes']['差异候选像素数']==6
    assert len(row['record']['evidence_ids'])==2 and row['record']['date_basis']=='A_original_source_request'
    again,_=bridge(client,base);assert again['created']==0 and again['already_saved']==1
    with Session(engine()) as db:
        assert not db.scalars(select(m.CloudCall)).all();assert len(db.scalars(select(m.Invocation)).all())==1
        pending=db.scalar(select(m.Job).where(m.Job.kind=='sync_native_receipts'));assert pending is not None;identity=pending.id
    assert process_once(identity,deliver=False);deliver_events()
    assessed=run(client,request(release,body['entity_ids'],goal='available_evidence',required_modalities=['imagery','observations']))
    assert assessed['status']=='completed',assessed.get('error',assessed.get('progress'))
    result=assessed['result']
    assert len(result['records'])==1 and result['objects'][0]['saved_records']==1 and result['objects'][0]['processing_records']==1
    assert result['records'][0]['common_observation_ref']['id']==row['id'];assert not any(g['kind']=='missing_saved_processing' for g in result['objects'][0]['gaps'])
    assert client.post('/api/observations/'+row['id']+'/verify',json={'expected_version':1,'decision':'confirmed','reason':'不能绕过原流程'}).status_code==409
    confirmed=complete(client,task,note='只核对工程像素对照，未证明现场变化');assert confirmed.status_code==200,confirmed.text
    again,listing=bridge(client,base);assert again['created']==0 and listing['records'][0]['record']['review_state']=='confirmed'
    assert client.get(base+'/native-receipts/'+row['id']+'/export').status_code==200
    with Session(engine()) as db:assert db.get(m.Entity,body['entity_ids'][0]).version==1

def test_corrupt_original_hides_common_values_and_can_restore_without_reinference(client,pairs):
    a,b,release,body,base,task=setup_geo(client,pairs);_,listing=bridge(client,base);identity=listing['records'][0]['id']
    with Session(engine()) as db:
        ev=db.get(m.Evidence,a['evidence_id']);path=settings().storage_dir/ev.storage_key;content=path.read_bytes();path.write_bytes(b'damaged-test-only')
    try:
        listing=client.get(base+'/native-receipts').json();assert listing['records'][0]['integrity_error'] and listing['records'][0]['record'] is None
        assert client.get(base+'/native-receipts/'+identity+'/export').status_code==409
    finally:path.write_bytes(content)
    _,listing=bridge(client,base);assert not listing['records'][0]['integrity_error']
    with Session(engine()) as db:assert len(db.scalars(select(m.Invocation)).all())==1

def test_preview_rejects_changed_scope_and_does_not_write_partial_results(client,pairs):
    a,b,release,body,base,task=setup_geo(client,pairs)
    assert client.post(base+'/native-receipts/sync',json={'preview_hash':'0'*64}).status_code==409
    with Session(engine()) as db:assert not db.scalars(select(m.Observation).where(m.Observation.data['native_instance_id'].as_string()==release['instance_id'])).all()

def test_unrecognized_normalizer_cannot_relabel_a_verified_machine_receipt(client,pairs):
    from generic_agents.contracts import digest
    a,b,release,body,base,task=setup_geo(client,pairs);_,listing=bridge(client,base);identity=listing['records'][0]['id']
    with Session(engine()) as db:
        row=db.get(m.Observation,identity);value={**row.data['native_packet'],'schema':'unreviewed-normalizer'}
        row.processing_version=digest(value)[:40];row.data={**row.data,'native_packet':value,'native_packet_hash':digest(value)};db.commit()
    listing=client.get(base+'/native-receipts').json();assert listing['records'][0]['integrity_error'] and listing['records'][0]['record'] is None
    assert client.get(base+'/native-receipts/'+identity+'/export').status_code==409

def test_damaged_daily_receipt_is_visible_and_rebuilt_without_overwriting_old_human_work(client,pairs):
    import copy
    a,b,release,body,base,task=setup_geo(client,pairs);_,listing=bridge(client,base)
    with Session(engine()) as db:
        memory=db.scalar(select(m.Memory).where(m.Memory.data['schema'].as_string()=='native-multimodal-daily-v1').order_by(m.Memory.created_at.desc()))
        identity=memory.id;data=copy.deepcopy(memory.data);data['records'][0]['record']['attributes']={'伪造值':999};memory.data=data;db.commit()
    listing=client.get(base+'/native-receipts').json();assert listing['daily'][0]['integrity_error'] and 'saved' not in listing['daily'][0]
    _,listing=bridge(client,base);assert not listing['daily'][0].get('integrity_error') and listing['daily'][0]['saved']==1
    with Session(engine()) as db:assert db.get(m.Memory,identity).data==data and len(db.scalars(select(m.Invocation)).all())==1

def test_explicit_object_scope_is_used_for_preview_and_commit(client,pairs):
    from tests.test_instance_workspace import object_row
    a,b,release,body,base,task=setup_geo(client,pairs);other=object_row(client,release,index=1)
    full=client.get(base+'/native-receipts').json();empty=client.get(base+'/native-receipts',params={'entity_ids':other['id']}).json()
    assert full['available']==1 and empty['available']==0
    bad=client.post(base+'/native-receipts/sync',json={'preview_hash':full['preview_hash'],'entity_ids':[other['id']]});assert bad.status_code==409
    good=client.post(base+'/native-receipts/sync',json={'preview_hash':full['preview_hash'],'entity_ids':body['entity_ids']});assert good.status_code==200,good.text
    assert client.get(base+'/native-receipts',params={'entity_ids':other['id']}).json()['records']==[]
