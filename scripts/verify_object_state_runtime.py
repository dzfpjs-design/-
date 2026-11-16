"""Read-only scoped checks around browser state assessment; no media/model calls."""
import argparse,json
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import ObjectState,CloudCall
from verify_changer_install_runtime import existing
from verify_local_observation_runtime import extended,originals
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/object-state-v06'

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())

def main(phase):
 OUT.mkdir(exist_ok=True);access=json.loads((ROOT/'.runtime/wms-access.json').read_text());workspace=access['workspace_id']
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as client:
  r=client.post('/login',json={k:access[k] for k in ['username','password']});r.raise_for_status()
  client.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':workspace})
  try:
   def get(path):
    r=client.get(path);r.raise_for_status();return r.json()
   instance=next(r for r in get('/instances') if r['name']=='地图影像区域核查')
   obj=next(r for r in get('/instances/'+instance['id']+'/objects') if '公开合成地图影像资料' in str(r['attributes']))
   with Session(engine()) as db:cloud=len(db.scalars(select(CloudCall.id).where(CloudCall.workspace_id==workspace)).all())
   if phase=='prepare':
    save('before',{'other':existing(workspace),'own':extended(workspace),'originals':originals(workspace),'cloud':cloud})
    save('target',{'instance':instance['id'],'entity':obj['id']});print(json.dumps({'baseline_saved':True}));return
   route='/workspace/instances/'+instance['id']+'/objects/'+obj['id']+'/state'
   result=get(route);state=result['state'];first=get(route)['state']
   assert state['state_hash']==first['state_hash'] and state['state_version']==first['state_version']
   assert state['fields']['公开登记值']['value']==27.5
   assert state['physical_state']=='unknown' and not state['current_state_verified']
   assert set(state['fields'])=={'公开登记值','记录说明'},'native/local candidates must not populate state'
   assert state['requirements_basis']=='available_report_fields'
   browser=read('browser-state-export')
   # checked_at is the read time; persisted assessment/export contents are otherwise exact.
   def stable(v):return {**v,'state':{k:x for k,x in v['state'].items() if k!='checked_at'}}
   assert stable(result)==stable(browser),'UI export differs from saved server assessment'
   before=read('before');other=existing(workspace);own=extended(workspace);raw=originals(workspace)
   target='ObjectState:'
   changed=[k for k,v in before['own'].items() if own.get(k)!=v]
   assert all(k.startswith(target) for k in changed),'prior non-state records changed'
   assert other==before['other'] and raw==before['originals'] and cloud==before['cloud']
   if phase=='check':save('before-restart',{'own':own,'other':other,'originals':raw,'state':stable(result),'cloud':cloud})
   else:
    restart=read('before-restart')
    assert restart=={'own':own,'other':other,'originals':raw,'state':stable(result),'cloud':cloud}
   save('verification' if phase=='check' else 'restart-verification',{'passed':True,'own_records':len(own),
       'old_non_state_unchanged':len(before['own'])-len(changed),'old_state_reassessed':len(changed),
       'other_records':len(other['records']),'own_originals':len(raw),'other_originals':len(other['originals']),
       'cloud_calls_added':cloud-before['cloud'],'state_version':state['state_version'],'physical_state':'unknown'})
   print(json.dumps(read('verification' if phase=='check' else 'restart-verification')))
  finally:client.post('/logout')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','check','restart-check']);main(p.parse_args().phase)
