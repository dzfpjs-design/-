"""Public native input; browser owns upload, submission, review and export."""
import argparse,json
from datetime import datetime
from pathlib import Path
import httpx
from PIL import Image,ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models
from generic_agents.contracts import digest
from generic_agents.geo_codec import file_digest
from generic_agents.config import settings
from verify_changer_install_runtime import existing
from verify_mixed_evidence_runtime import snapshot,originals

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/local-observation-v06'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def extended(workspace):
    rows=snapshot(workspace)
    with Session(engine()) as db:
        for cls in [models.SpecialistInstallation,models.SpecialistRun,models.ObjectState]:
            for row in db.scalars(select(cls).where(cls.workspace_id==workspace)):
                rows[cls.__name__+':'+row.id]=digest({c.name:getattr(row,c.name).isoformat() if isinstance(getattr(row,c.name),datetime) else getattr(row,c.name) for c in cls.__table__.columns})
    return rows
def main(phase):
    OUT.mkdir(exist_ok=True);access=json.loads((ROOT/'.runtime/wms-access.json').read_text());workspace=access['workspace_id']
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
        login=c.post('/login',json={k:access[k] for k in ['username','password']});login.raise_for_status()
        c.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':workspace})
        def call(method,path,body=None):
            r=c.request(method,path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
        def get(path):return call('GET',path)
        try:
            instance=next(r for r in get('/instances') if r['name']=='地图影像区域核查')
            base='/workspace/instances/'+instance['id'];obj=next(r for r in get('/instances/'+instance['id']+'/objects') if '公开合成地图影像资料' in str(r['attributes']))
            if phase=='prepare':
                caps=get('/capabilities');cap=next((r for r in caps if r['status']=='published' and r['data']['execution'].get('adapter')=='local_specialist'),None)
                if not cap:
                    installation=next(r for r in get('/specialists/installations') if r['pack_id']=='sam2-1-tiny' and r['status']=='ready')
                    registered=call('POST','/specialists/installations/'+installation['id']+'/register',{'version':installation['version']})
                    cap_id=registered['capability_id']
                    save('registration',{'installation_id':installation['id'],'old_version':installation['version'],'new_version':registered['version'],'change':'Register existing approved SAM installation; no download or external/model permission expansion'})
                else:cap_id=cap['id']
                if not (OUT/'baseline.json').exists():
                    save('baseline',existing(workspace));save('old-own',extended(workspace));save('old-own-originals',originals(workspace))
                image=Image.new('RGB',(177,137),'#17243b');draw=ImageDraw.Draw(image)
                draw.rectangle((14,18,85,101),fill='#2cac89');draw.ellipse((96,39,160,114),fill='#f19a35');draw.line((0,8,176,130),fill='#ffffff',width=3)
                path=ROOT/'.runtime/public-local-observation.png';image.save(path)
                save('prepared',{'instance_id':instance['id'],'entity_id':obj['id'],'capability_id':cap_id,'path':str(path),
                    'sha256':file_digest(path),'public_synthetic_input':True,'physical_device':False,'cloud_calls':0})
                print(json.dumps({'public_input_prepared':True,'registered_local_model':True,'cloud_calls':0}));return
            items=get(base+'/local-observations')['records'];row=next(get(base+'/local-observations/'+r['id']) for r in items if r['status']=='processed')
            assert row['review']['reason']=='已核对公开合成图片和本地通用区域候选；采集时间未知，不作为现场异常或对象状态结论。'
            assert row['review']['decision']=='confirmed' and row['source_clock'] is None and row['data_received'] and not row['integrity_error']
            native=row['output']['segmentation'];assert native['cloud_calls']==0 and native['backend']=='cpu'
            assert row['source_sha256']==json.loads((OUT/'prepared.json').read_text())['sha256']
            export=get(base+'/local-observations/'+row['id']+'/export');assert export==json.loads((OUT/'browser-observation-export.json').read_text())
            daily=get(base+'/local-observation-daily');assert daily[0]['processed']==1 and daily[0]['reviewed']==1 and daily[0]['not_authoritative']
            browser_query=json.loads((OUT/'browser-query-export.json').read_text())
            query=get('/evidence-assessments/'+browser_query['assessment']['id']+'/export');assert query==browser_query
            records=query['assessment']['result']['records'];assert len(records)==3 and sum(r['record_stage']=='processed' for r in records)==2
            linked=next(r for r in records if r['key']=='Observation:'+row['id'])
            assert linked['review_state']=='confirmed' and linked['integrity_verified'] and linked['reported_fields']==[{'name':'segmentation_candidate_count','value':3}]
            current=extended(workspace);old=json.loads((OUT/'old-own.json').read_text());assert all(current.get(k)==v for k,v in old.items()),'old own records changed'
            baseline=json.loads((OUT/'baseline.json').read_text());assert existing(workspace)==baseline,'other workspace baseline changed'
            files=originals(workspace);old_files=json.loads((OUT/'old-own-originals.json').read_text());assert all(files.get(k)==v for k,v in old_files.items())
            value={'rows':current,'export':export,'daily':daily,'query':query,'originals':files,'baseline':baseline}
            if phase=='check':save('before-restart',value)
            else:
                assert json.loads((OUT/'before-restart.json').read_text())==value
                save('after-restart',value);save('verification',{'passed':True,'native_cpu':True,'masks':len(native['masks']),
                    'inference_ms':native['inference_ms'],'common_observation':True,'data_received':True,'human_review_saved':True,
                    'daily_processed':daily[0]['processed'],'daily_reviewed':daily[0]['reviewed'],'old_own_records_unchanged':len(old),
                    'business_query_saved_records':len(records),'business_query_processed_records':2,'business_query_export_matches':True,
                    'old_own_originals_unchanged':len(old_files),'own_records':len(current),'own_originals':len(files),
                    'old_records_unchanged':len(baseline['records']),'old_originals_unchanged':len(baseline['originals']),
                    'restart_durable':True,'cloud_calls':0,'field_accuracy_verified':False})
            print(json.dumps({'phase':phase,'passed':True,'masks':len(native['masks']),'own_records':len(current),'cloud_calls':0}))
        finally:call('POST','/logout')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','check','restart-check']);main(p.parse_args().phase)
