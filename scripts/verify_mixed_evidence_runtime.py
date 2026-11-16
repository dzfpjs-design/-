"""Own public manual input; actual browser performs the mixed evidence query."""
import argparse,json,time
from datetime import datetime,UTC,timedelta
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models
from generic_agents.contracts import digest
from generic_agents.worker import process_once,deliver_events
from generic_agents.geo_codec import file_digest
from generic_agents.config import settings
from verify_changer_install_runtime import existing

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/mixed-evidence-v06'
CLASSES=('Entity','Observation','Capability','Invocation','Job','Event','Memory','Evidence','EvidenceAssessment','EvidenceQuestion','Task','WorkflowScene','WMSConnection','WMSRead','GeoScene','GeoRun')
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def snapshot(workspace):
    rows={}
    with Session(engine()) as db:
        for name in CLASSES:
            cls=getattr(models,name)
            for row in db.scalars(select(cls).where(cls.workspace_id==workspace)):
                rows[name+':'+row.id]=digest({col.name:getattr(row,col.name).isoformat() if isinstance(getattr(row,col.name),datetime) else getattr(row,col.name) for col in cls.__table__.columns})
    return rows
def originals(workspace):
    result={}
    with Session(engine()) as db:
        for row in db.scalars(select(models.Evidence).where(models.Evidence.workspace_id==workspace)):
            actual=file_digest(settings().storage_dir/row.storage_key)
            assert actual==row.sha256,'own original SHA changed'
            result[row.id]=actual
    return result
def main(phase):
    OUT.mkdir(exist_ok=True);access=json.loads((ROOT/'.runtime/wms-access.json').read_text());workspace=access['workspace_id']
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
        login=c.post('/login',json={k:access[k] for k in ['username','password']});login.raise_for_status()
        c.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':workspace})
        def call(method,path,body=None):
            result=c.request(method,path,**({'json':body} if body is not None else {}));result.raise_for_status();return result.json()
        def get(path):return call('GET',path)
        def publish(kind,name,description):
            previous=next((r for r in get('/capabilities') if r['name']==name and r['namespace']=='public_manual_qa'),None)
            if previous:return previous
            data={'kind':kind,'name':name,'namespace':'public_manual_qa','version':'1.0.0','owner':'公开工程核验',
                'description':description,'allowed_agent_roles':['perception'],'specification':{'rules':[],'timezone':'Asia/Shanghai'},
                'execution':{'adapter':'builtin','handler':'numeric_rules'},'input_schema':{'type':'object'},'output_schema':{'type':'object'},
                'data_policy':{'cloud_allowed':False,'external_allowed':False,'deployment':'local'}}
            row=call('POST','/capabilities',data)
            for verb in ['validate','publish']:row=call('POST','/capabilities/'+row['id']+'/'+verb,{'expected_version':row['version'],'reason':description})
            return row
        try:
            instance=next(r for r in get('/instances') if r['name']=='地图影像区域核查')
            obj=next(r for r in get('/instances/'+instance['id']+'/objects') if '公开合成地图影像资料' in str(r['attributes']))
            if phase=='prepare':
                if not (OUT/'baseline.json').exists():
                    save('baseline',existing(workspace));save('old-own',snapshot(workspace));save('old-own-originals',originals(workspace))
                source=publish('source','declared_manual_values','公开人工登记观测来源，不是真实传感设备，不发网络请求')
                extractor=publish('tool','local_manual_value_rules','本机字段规则，不是机器学习模型，不判断现场业务异常')
                with Session(engine()) as db:old=db.scalar(select(models.Observation).where(models.Observation.workspace_id==workspace,models.Observation.source_record_id=='public-m17-manual-reading'))
                if old:identity=old.id
                else:
                    clock=datetime.now(UTC)-timedelta(minutes=1)
                    body={'source_id':source['id'],'source_record_id':'public-m17-manual-reading','entity_id':obj['id'],
                        'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),
                        'attributes':{'公开登记值':27.5,'记录说明':'公开人工登记工程样例，非现场测量'},'extractor_id':extractor['id'],'startup_mode':'manual'}
                    identity=call('POST','/observations',body)['id']
                with Session(engine()) as db:
                    observation=db.get(models.Observation,identity)
                    job=db.scalar(select(models.Job).where(models.Job.workspace_id==workspace,models.Job.kind=='process_observation',models.Job.data['observation_id'].as_string()==identity))
                    completed=observation.status=='processed';job_id=job.id
                if not completed:assert process_once(job_id,deliver=False)
                deliver_events(workspace_id=workspace)
                save('prepared',{'instance_id':instance['id'],'entity_id':obj['id'],'observation_id':identity,'public_manual_input':True,'physical_device':False,'cloud_calls':0})
                print(json.dumps({'manual_input_processed_locally':True,'instance':instance['id'],'cloud_calls':0}));return
            rows=get('/evidence-assessments?instance_id='+instance['id'])
            row=next(r for r in rows if r['request']['purpose']=='联合核查公开档案和登记观测' and r['status']=='completed')
            exported=get('/evidence-assessments/'+row['id']+'/export');result=row['result']
            record=next(r for r in result['records'] if r['modality']=='observations')
            assert record['integrity_verified'] and {'name':'公开登记值','value':27.5} in record['reported_fields']
            assert result['objects'][0]['saved_records']==3 and result['objects'][0]['processing_records']==2
            assert not result['objects'][0]['current_state_sufficient'] and result['cloud_calls']==0 and not result['entity_state_updated']
            assert json.loads((OUT/'browser-query-export.json').read_text())==exported
            current=snapshot(workspace);old=json.loads((OUT/'old-own.json').read_text());assert all(current.get(k)==v for k,v in old.items()),'old own data changed'
            baseline=json.loads((OUT/'baseline.json').read_text());assert existing(workspace)==baseline,'other workspace data changed'
            own_originals=originals(workspace)
            assert own_originals==json.loads((OUT/'old-own-originals.json').read_text())
            value={'rows':current,'export':exported,'baseline':baseline,'originals':own_originals}
            if phase=='check':save('before-restart',value)
            else:
                assert json.loads((OUT/'before-restart.json').read_text())==value
                save('after-restart',value);save('verification',{'passed':True,'mixed_query_saved':True,'restart_durable':True,
                    'own_records':len(current),'own_originals_unchanged':len(own_originals),'old_own_records_unchanged':len(old),'old_records_unchanged':len(baseline['records']),
                    'old_originals_unchanged':len(baseline['originals']),'cloud_calls':0,'physical_measurement_verified':False})
            print(json.dumps({'phase':phase,'passed':True,'own_records':len(current),'cloud_calls':0}))
        finally:call('POST','/logout')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','check','restart-check']);main(parser.parse_args().phase)
