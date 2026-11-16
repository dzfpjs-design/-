"""Actual app WMS QA. Human browser creates/authorizes/reads; verifier only proves it."""
import argparse,json,os,subprocess,time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import httpx
from dotenv import dotenv_values,set_key
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.security import password_hash
from generic_agents.geo_codec import file_digest
from verify_changer_install_runtime import existing

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/wms-v06'
ACCESS=ROOT/'.runtime/wms-access.json'
CLASSES=('WMSConnection','WMSRead','GeoScene','GeoRun','GeoDaily','Evidence','Job','Memory','WorkflowScene','Task')

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def prepare():
    if ACCESS.exists():access=json.loads(ACCESS.read_text())
    else:
        access={'username':'wms_validation_'+uuid4().hex[:8],'password':uuid4().hex}
        with Session(engine()) as db:
            user=models.User(username=access['username'],password_hash=password_hash(access['password']))
            workspace=models.Workspace(name='公开合成WMS接入核查（不是现场验收）');db.add_all([user,workspace]);db.flush()
            db.add(models.Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit();access['workspace_id']=workspace.id
        with os.fdopen(os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as target:json.dump(access,target)
    if not (OUT/'baseline.json').exists():save('baseline',existing(access['workspace_id']))
    checkpoint=ROOT/'.runtime/wms-reference.json';live=False
    if checkpoint.exists():
        record=json.loads(checkpoint.read_text())
        command=subprocess.run(['ps','-p',str(record['pid']),'-o','command='],capture_output=True,text=True).stdout
        if str(ROOT/'scripts/wms_reference_server.py') in command:
            r=httpx.get(record['endpoint'],params={'SERVICE':'WMS','REQUEST':'GetCapabilities','VERSION':'1.3.0'},trust_env=False,timeout=3)
            r.raise_for_status();live=True
    if not live:
        with (ROOT/'.runtime/wms-reference.log').open('ab') as log:
            subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/wms_reference_server.py')],cwd=ROOT,stdout=log,stderr=log,start_new_session=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if checkpoint.exists():
                record=json.loads(checkpoint.read_text())
                command=subprocess.run(['ps','-p',str(record['pid']),'-o','command='],capture_output=True,text=True).stdout
                if str(ROOT/'scripts/wms_reference_server.py') in command:break
            time.sleep(.1)
        else:raise RuntimeError('reference publisher did not start; preserve original access')
    env=dotenv_values(ROOT/'.env');endpoint=record['endpoint']
    allowed=[v.strip() for v in (env.get('GA_WMS_ALLOWED_ENDPOINTS') or '').split(',') if v.strip()]
    if endpoint not in allowed:allowed.append(endpoint)
    grants=json.loads(env.get('GA_WMS_ACCESS_GRANTS') or '{}')
    grant={'endpoint':endpoint,'secret_ref':''};rows=grants.setdefault(access['workspace_id'],[])
    if grant not in rows:rows.append(grant)
    set_key(ROOT/'.env','GA_WMS_ALLOWED_ENDPOINTS',','.join(allowed))
    set_key(ROOT/'.env','GA_WMS_ACCESS_GRANTS',json.dumps(grants));os.chmod(ROOT/'.env',0o600)
    save('source',{'endpoint':endpoint,'synthetic_only':True,'grant_has_credentials':False,'workspace_id':access['workspace_id']})
    print(json.dumps({'prepared':True,'endpoint':endpoint,'existing_records':len(json.loads((OUT/'baseline.json').read_text())['records'])}))

def inspect(phase):
    access=json.loads(ACCESS.read_text());rows={};artifacts={}
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
        login=c.post('/login',json={k:access[k] for k in ('username','password')});login.raise_for_status()
        c.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
        try:
            connections=c.get('/wms/connections');connections.raise_for_status();reads=c.get('/wms/reads');reads.raise_for_status()
            completed=[r for r in reads.json() if r['status']=='succeeded']
            if len(completed)!=1:raise ValueError('real UI must save exactly one successful WMS read')
            read=completed[0];scene=c.get('/geo/scenes/'+read['scene_id']);scene.raise_for_status();scene=scene.json()
            if scene['status']!='ready':raise ValueError('real UI must confirm RGB and save its preview')
            export=c.get('/wms/reads/'+read['id']+'/export');export.raise_for_status();export=export.json()
            assert export['cloud_calls']==0 and export['raw_satellite_bands'] is False and export['source_capture_time_known'] is False
            daily=c.get('/imagery-daily');daily.raise_for_status();save('latest-daily',daily.json())
            collection=next(d for d in daily.json()['rows'] if any(r['id']==scene['id'] for r in d['records']))
            record=next(r for r in collection['records'] if r['id']==scene['id'])
            assert record['integrity_verified'] and record['date_basis']=='WMS_map_read_request' and collection['data_memory_id']
            assert 'rendered_map_not_raw_bands' in record['quality_gaps'] and 'capture_time_unknown' in record['quality_gaps']
            assert record['sources'][0]['wms_origin']==export['provenance']
            assert file_digest(OUT/'browser-map.png')==export['provenance']['png_sha256']
            assert file_digest(OUT/'browser-derived.tif')==export['provenance']['derived_sha256']
            with Session(engine()) as db:
                for name in CLASSES:
                    cls=getattr(models,name)
                    for row in db.scalars(select(cls).where(cls.workspace_id==access['workspace_id'])):
                        value={col.name:(getattr(row,col.name).isoformat() if isinstance(getattr(row,col.name),datetime) else getattr(row,col.name)) for col in cls.__table__.columns}
                        rows[name+':'+row.id]=digest(value)
                        if name=='Evidence':
                            actual=file_digest(settings().storage_dir/row.storage_key)
                            if actual!=row.sha256:raise ValueError('saved WMS evidence SHA mismatch')
                            artifacts[row.id]=actual
                job=db.get(models.Job,read['job_id']);assert job.status=='succeeded'
            requests=[json.loads(line) for line in (ROOT/'.runtime/wms-reference-requests.jsonl').read_text().splitlines()]
            maps=[r['request'] for r in requests if r['request'].get('REQUEST')==['GetMap']]
            assert len(maps)==1,'one explicit browser read must issue exactly one GetMap'
            assert [float(v) for v in maps[0]['BBOX'][0].split(',')]==[23,104,27,116]
            assert maps[0]['WIDTH']==['128'] and maps[0]['HEIGHT']==['96'] and maps[0]['CRS']==['EPSG:4326']
            snapshot={'own_records':rows,'own_originals':artifacts,'read':read,'scene':scene,'export':export,
                'reference_map_count':len(maps),'browser_png_sha256':file_digest(OUT/'browser-map.png'),
                'browser_tiff_sha256':file_digest(OUT/'browser-derived.tif'),'existing':existing(access['workspace_id'])}
            baseline=json.loads((OUT/'baseline.json').read_text());assert snapshot['existing']==baseline,'old data/cloud set changed'
            if phase=='check':save('before-restart',snapshot)
            else:
                before=json.loads((OUT/'before-restart.json').read_text());assert snapshot==before,'WMS saved data changed after ordinary restart'
                save('after-restart',snapshot);save('verification',{'passed':True,'actual_http_getmap':True,'local_raster_conversion':True,
                    'restart_durable':True,'own_records':len(rows),'original_sha_verified':len(artifacts),'old_records_unchanged':len(baseline['records']),
                    'old_originals_unchanged':len(baseline['originals']),'new_cloud_calls':0,'field_quality_verified':False,'vendor_server_verified':False})
            print(json.dumps({'phase':phase,'passed':True,'own_records':len(rows),'artifact_sha':len(artifacts),'old_records':len(baseline['records']),'new_cloud_calls':0}))
        finally:
            response=c.post('/logout');assert response.status_code in (200,204),'own QA logout failed; do not bulk revoke sessions'

def main():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','check','restart-check']);args=p.parse_args();OUT.mkdir(exist_ok=True)
    prepare() if args.phase=='prepare' else inspect(args.phase)

if __name__=='__main__':main()
