"""Real local rasters, comparison, SAM and human receipts in disposable PostgreSQL."""
import copy,hashlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import GeoDaily,GeoScene,GeoPair,Memory,Evidence,Membership,Workspace,CloudCall,now
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.worker import process_once
from tests.test_geo_codec import scenes,pytestmark
from tests.test_geo_pairs import pairs,ready,body
from tests.test_geo_scenes import upload
from tests.test_workflow_geo_pairs import configured
from tests.test_workflow_abilities import finish_job
from tests.test_instance_workspace import complete,object_row

def collect(client):
 r=client.post('/api/imagery-daily/reconcile');assert r.status_code==200,r.text;return r.json()['rows']

def compared(client,pairs):
 a=ready(client,pairs/'rgb.tif','A.tif');b=ready(client,pairs/'after.tif','B.tif');r=client.post('/api/geo/pairs',json=body(a,b));assert r.status_code==201;r=r.json();assert process_once(r['job_id'],deliver=False);return a,b,client.get('/api/geo/pairs/'+r['id']).json()

def test_real_pair_receipt_provenance_quality_images_and_human_updates(client,pairs):
 a,b,pair=compared(client,pairs);r=collect(client)[0]
 assert r['unique_originals']==2 and r['processing_records']==3 and r['completed_comparisons']==1 and len(r['quality_gap_ids'])==3
 assert all(x['quality_gaps']==['capture_time_unknown'] for x in r['records']);assert collect(client)==[r]
 rec=next(x for x in r['records'] if x['kind']=='comparison');base='/api/imagery-daily/'+r['id']+'/records/'+rec['key'];detail=client.get(base).json()
 assert detail['integrity_verified'] and len(detail['images'])==4 and detail['machine']['unknown_pixels']==8
 for image in detail['images']:response=client.get('/api'+image['url']);assert response.status_code==200 and hashlib.sha256(response.content).hexdigest()==image['sha256']
 assert client.get(base+'/original/0').content==(pairs/'rgb.tif').read_bytes()
 review=client.put('/api/geo/pairs/'+pair['id']+'/review',json={'version':pair['version'],'decision':'uncertain','note':'坐标与拍摄时间待现场核验，保留像素差异。'});assert review.status_code==200
 after=collect(client)[0];assert after['version']==r['version']+1 and after['unique_originals']==2
 assert client.get(base).json()['review']['note'].startswith('坐标与拍摄时间')
 export=client.get('/api/imagery-daily/'+r['id']+'/export').json();assert not export['source_time_verified'] and not export['entity_state_updated'] and export['cloud_calls']==0
 with Session(engine()) as db:assert db.scalar(select(CloudCall.id)) is None

def test_cross_date_pair_anchors_A_and_keeps_B_upload_and_late_results(client,pairs):
 a=ready(client,pairs/'rgb.tif','A.tif');b=ready(client,pairs/'after.tif','B.tif')
 with Session(engine()) as db:db.get(GeoScene,a['id']).created_at-=timedelta(days=13);db.get(GeoScene,b['id']).created_at-=timedelta(days=12);db.commit()
 r=client.post('/api/geo/pairs',json=body(a,b)).json();assert process_once(r['job_id'],deliver=False)
 rows=collect(client);assert len(rows)==2;old=next(x for x in rows if x['completed_comparisons']);rec=next(x for x in old['records'] if x['kind']=='comparison')
 assert rec['origin_upload_day']==rec['sources'][0]['upload_day']!=rec['sources'][1]['upload_day'] and rec['late_processing'] and rec['processing_completed_day']
 assert old['unique_originals']==2 and next(x for x in rows if x['id']!=old['id'])['unique_originals']==1
 assert collect(client)==rows

def test_pending_cancelled_and_actual_bad_tiff_decode_failure_visible(client,scenes,tmp_path):
 pending=upload(client,scenes/'rgb.tif').json();cancelled=upload(client,scenes/'rgb.tif').json()
 assert client.post('/api/geo/scenes/'+cancelled['id']+'/cancel',json={'version':cancelled['version']}).status_code==200
 bad=tmp_path/'bad.tif';bad.write_bytes(b'II*\x00'+b'\x00'*20);broken=upload(client,bad).json();assert process_once(broken['job_id'],deliver=False)
 r=collect(client)[0];assert len(r['pending_ids'])==1 and len(r['cancelled_ids'])==1 and len(r['failed_ids'])==1 and r['unique_originals']==2
 assert r['status']=='requires_attention' and all(x['quality_gaps'] for x in r['records'])
 assert all('metadata_not_read' in x['quality_gaps'] and 'map_reference_unavailable' not in x['quality_gaps'] for x in r['records'])

def test_missing_map_rgb_and_user_declared_time_remain_explicit(client,scenes):
 raw=upload(client,scenes/'unknown.tif').json();assert process_once(raw['job_id'],deliver=False)
 r=collect(client)[0];assert 'map_reference_unavailable' in r['records'][0]['quality_gaps'] and 'rgb_not_configured' in r['records'][0]['quality_gaps']
 good=ready(client,scenes/'rgb.tif','known.tif');update=client.put('/api/geo/scenes/'+good['id']+'/configuration',json={'version':good['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True,'observed_at':'2026-10-01T10:00:00+08:00'});assert update.status_code==200;assert process_once(update.json()['job_id'],deliver=False)
 after=collect(client)[0];rec=next(x for x in after['records'] if x['id']==good['id']);assert 'capture_time_unknown' not in rec['quality_gaps'] and rec['sources'][0]['time_basis']=='user_declared_unverified'
 assert client.get('/api/imagery-daily/'+after['id']+'/export').json()['source_time_verified'] is False

def test_cache_daily_rechecks_and_old_damaged_receipt_source_backed_recovery(client,scenes,monkeypatch):
 from generic_agents import geo_daily
 original=ready(client,scenes/'rgb.tif','source.tif');first=collect(client)[0];calls=[];native=geo_daily.integrity
 def counted(*a):calls.append(1);return native(*a)
 monkeypatch.setattr(geo_daily,'integrity',counted)
 with Session(engine()) as db:geo_daily.reconcile(db,client.headers['X-Workspace-ID']);db.commit()
 assert not calls
 with Session(engine()) as db:ev=db.get(Evidence,original['evidence_id']);path=settings().storage_dir/ev.storage_key;raw=path.read_bytes();path.write_bytes(b'broken')
 base='/api/imagery-daily/'+first['id']+'/records/'+first['records'][0]['key'];detail=client.get(base).json();assert not detail['integrity_verified'] and not detail['images'] and detail['machine'] is None and client.get(base+'/original/0').status_code==409
 broken=collect(client)[0];assert broken['verified_originals']==0 and len(broken['integrity_issue_ids'])==1
 path.write_bytes(raw);restored=collect(client)[0];assert restored['verified_originals']==1
 clock=now()+timedelta(days=13);monkeypatch.setattr(geo_daily,'now',lambda:clock)
 with Session(engine()) as db:geo_daily.reconcile(db,client.headers['X-Workspace-ID']);db.commit()
 closed=client.get('/api/imagery-daily').json()['rows'][0];assert not closed['provisional']
 # A later physical source fault has no version bump; the old date still has recovery.
 path.write_bytes(b'broken-old-source')
 old_day='/api/imagery-daily/'+closed['id']+'/reconcile';bad=client.post(old_day);assert bad.status_code==200 and bad.json()['verified_originals']==0
 path.write_bytes(raw);r=client.post(old_day);assert r.status_code==200 and r.json()['verified_originals']==1;closed=r.json()
 with Session(engine()) as db:m=db.get(Memory,closed['data_memory_id']);d=copy.deepcopy(m.data);d['unique_originals']=999;m.data=d;db.commit()
 assert client.get('/api/imagery-daily').status_code==409
 calls.clear();recovered=collect(client)[0];assert calls and recovered['unique_originals']==1 and len(recovered['recovery_history'])==1
 with Session(engine()) as db:old=db.get(Memory,closed['data_memory_id']);assert old.status=='invalidated' and old.data['unique_originals']==999
 assert collect(client)==[recovered]

def test_actual_business_pair_instance_scope_and_review_receipt(client,pairs):
 a,b,registered,release,sources,value=configured(client,pairs);r=client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=value);assert r.status_code==201;task=finish_job(client,r.json());assert task['status']=='workflow_waiting'
 full=collect(client);scoped=client.get('/api/imagery-daily',params={'instance_id':release['instance_id']}).json()['rows'][0]
 assert scoped['scope']=='instance_part_of_workspace_receipt' and scoped['processing_records']==1 and scoped['unique_originals']==2 and len(scoped['unreviewed_ids'])==1
 rec=scoped['records'][0];assert rec['unknown_pixels']==54 and rec['task_id']==task['id'];base='/api/imagery-daily/'+scoped['id']+'/records/'+rec['key'];assert task['id'] in client.get(base).json()['task_url']
 r=complete(client,task,note='真实对照区域及未知像素已核对，业务变化待核。');assert r.status_code==200,r.text
 after=collect(client);detail=client.get(base).json();assert detail['business_reviews'][-1]['note'].startswith('真实对照区域') and detail['record']['review_state']=='confirmed'

def test_foreign_receipt_hidden_and_readonly_user_cannot_reconcile(client,account,scenes):
 ready(client,scenes/'rgb.tif','A.tif');r=collect(client)[0]
 with Session(engine()) as db:
  other=Workspace(name='其他工作区');db.add(other);db.flush();foreign=GeoDaily(workspace_id=other.id,received_day='2026-01-01',data={});db.add(foreign);db.flush();identity=foreign.id
  membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));membership.role='viewer';db.commit()
 assert client.get('/api/imagery-daily').status_code==200 and client.post('/api/imagery-daily/reconcile').status_code==403
 assert client.get('/api/imagery-daily/'+identity+'/export').status_code==404
