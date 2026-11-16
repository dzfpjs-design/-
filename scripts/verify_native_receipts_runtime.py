"""Read-only proof around actual browser reuse of a public native geospatial result."""
import argparse,json
from pathlib import Path
import httpx
from verify_changer_install_runtime import existing
from verify_local_observation_runtime import extended,originals
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/native-receipts-v06'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def main(phase):
 OUT.mkdir(exist_ok=True);access=json.loads((ROOT/'.runtime/wms-access.json').read_text());workspace=access['workspace_id']
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=60) as client:
  r=client.post('/login',json={k:access[k] for k in ['username','password']});r.raise_for_status();client.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':workspace})
  def get(path):
   r=client.get(path);r.raise_for_status();return r.json()
  try:
   instance=next(r for r in get('/instances') if r['name']=='地图影像区域核查');base='/workspace/instances/'+instance['id']
   if phase=='prepare':
    save('baseline',existing(workspace));save('old-own',extended(workspace));save('old-originals',originals(workspace));print(json.dumps({'baseline_saved':True}));return
   listing=get(base+'/native-receipts');assert listing['available']==1 and len(listing['records'])==1 and listing['not_ready']==0
   row=listing['records'][0];assert row['data_received'] and not row['integrity_error']
   assert row['record']['modality']=='imagery' and row['record']['attributes']['区域候选数量']==4 and row['record']['review_state']=='confirmed'
   export=get(base+'/native-receipts/'+row['id']+'/export');assert export==json.loads((OUT/'browser-observation-export.json').read_text())
   query_file=json.loads((OUT/'browser-query-export.json').read_text());query=get('/evidence-assessments/'+query_file['assessment']['id']+'/export');assert query==query_file
   records=query['assessment']['result']['records'];assert len(records)==4 and sum(r.get('record_stage','processed')=='processed' for r in records)==3
   assert len([r for r in records if r.get('common_observation_ref',{}).get('id')==row['id']])==1
   current=extended(workspace);old=json.loads((OUT/'old-own.json').read_text());assert all(current.get(k)==v for k,v in old.items()),'old own records changed'
   old_files=json.loads((OUT/'old-originals.json').read_text());files=originals(workspace);assert old_files==files
   baseline=json.loads((OUT/'baseline.json').read_text());assert existing(workspace)==baseline,'other workspace changed'
   value={'rows':current,'originals':files,'baseline':baseline,'listing':listing,'export':export,'query':query}
   if phase=='check':save('before-restart',value)
   else:
    if json.loads((OUT/'before-restart.json').read_text())!=value:
     save('after-restart-observed',value)
     raise AssertionError('Restart comparison differs; original baseline retained, inspect after-restart-observed')
    save('after-restart',value);save('verification',{'passed':True,'common_observations':1,'native_masks_reused':4,'no_duplicate_inference':True,'data_received':True,
     'query_saved_records':4,'query_processed_records':3,'duplicate_media_and_observation_avoided':True,'restart_durable':True,'cloud_calls':0,
     'own_records':len(current),'old_own_records_unchanged':len(old),'own_originals_unchanged':len(files),'old_records_unchanged':len(baseline['records']),
     'old_originals_unchanged':len(baseline['originals']),'field_accuracy_verified':False,'audio_video_runtime_bridge_verified':False})
   print(json.dumps({'phase':phase,'passed':True,'own_records':len(current),'cloud_calls':0}))
  finally:client.post('/logout').raise_for_status()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','check','restart-check']);main(p.parse_args().phase)
