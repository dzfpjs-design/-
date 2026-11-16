"""Own public QA material/setup; actual UI registers sources/models and runs tasks."""
import argparse,copy,json,time
from datetime import UTC,datetime
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models
from generic_agents.contracts import digest
from generic_agents.instance_compiler import signature_value
from generic_agents.geo_codec import file_digest
from generic_agents.config import settings
from verify_changer_install_runtime import existing

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/wms-business-v06'
ACCESS=ROOT/'.runtime/wms-access.json';BUSINESS='地图影像区域核查';ROLE='影像核查负责人'
CLASSES=('WMSConnection','WMSRead','GeoScene','GeoRun','SpecialistInstallation','Capability','Task','Memory','WorkflowScene','Job','Invocation','Evidence','GeoDaily','Entity')

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def main(phase):
    OUT.mkdir(exist_ok=True);access=json.loads(ACCESS.read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
        login=c.post('/login',json={k:access[k] for k in ('username','password')});login.raise_for_status()
        c.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
        def call(method,path,body=None):
            response=c.request(method,path,**({'json':body} if body is not None else {}));response.raise_for_status();return response.json()
        def get(path):return call('GET',path)
        try:
            if phase=='prepare':
                if not (OUT/'baseline.json').exists():save('baseline',existing(access['workspace_id']))
                if not (OUT/'old-own-map.json').exists():
                    with Session(engine()) as db:
                        values={}
                        for cls in (models.WMSRead,models.GeoScene):
                            for row in db.scalars(select(cls).where(cls.workspace_id==access['workspace_id'])):
                                values[cls.__name__+':'+row.id]=digest({col.name:getattr(row,col.name).isoformat() if isinstance(getattr(row,col.name),datetime) else getattr(row,col.name) for col in cls.__table__.columns})
                        save('old-own-map',values)
                material=next((m for m in get('/setup/materials') if m['name']=='公开地图影像本地核查规则.txt'),None)
                if not material:
                    raw=('公开地图影像本地核查规则\n'
                        'WMS来源是服务渲染图，不是卫星原始波段。只使用管理员已确认范围内明确读取的影像，未知采集时间不替换为当前时间。\n'
                        '本地SAM读取任务固定选择的RGB影像和像素范围，保存通用区域候选和未知无效位置，不判断林业异常或业务对象状态。\n'
                        '影像核查负责人对照来源与候选区域，检查未知位置，保存人工核查说明与依据。\n'
                        '数据管理保留原响应、派生影像、模型版本、固定输入、结果证据和人工说明，不自动发布经验。\n'
                        '公开合成地图只用于工程实现核查，不代表现场业务质量。').encode()
                    response=c.post('/setup/materials',files={'file':('公开地图影像本地核查规则.txt',raw,'text/plain')});response.raise_for_status();material=response.json()
                material=get('/setup/materials/'+material['id'])
                deadline=time.monotonic()+45
                while material['status'] not in ['parsed','failed']:
                    if time.monotonic()>deadline:raise TimeoutError('material job remains live; poll the same material')
                    time.sleep(.2);material=get('/setup/materials/'+material['id'])
                assert material['status']=='parsed','own material parsing failed'
                draft=get('/setup/draft');config=draft['configuration']
                if config.get('businesses') and config['businesses']!=[BUSINESS]:raise ValueError('own draft contains other work; preserve it')
                if not config.get('businesses'):
                    steps=[]
                    for sid,name,agent,role,substring,next_id in [('segment','本地模型提取地图区域候选','多模感知智能体','','本地SAM','review'),('review','保存地图区域人工核查','人工确认',ROLE,'影像核查负责人','end')]:
                        block=next(b for b in material['blocks'] if substring in b['text']);quote=block['text']
                        steps.append({'id':sid,'name':name,'purpose':quote,'agent':agent,'role':role,'output':'区域候选与未知位置' if sid=='segment' else '人工核查说明',
                            'sourceId':material['id'],'citation':quote,'citations':[{'material_id':material['id'],'block_id':block['id'],'quote':quote}],
                            'nextId':next_id,'condition':'','elseId':'end'})
                    config=copy.deepcopy(config);config.setdefault('resources',[]);config.setdefault('connections',[])
                    config.update(version=1,businesses=[BUSINESS],materials=[material],
                        instanceProfile={'name':'地图影像区域核查','domain':'影像资料管理','goal':'在本机核对明确选择的地图影像与区域候选，保存来源和人工依据','objectCandidates':['影像资料']},
                        flows={BUSINESS:{'trigger':'人工发起','output':'地图区域核查说明','steps':steps}},
                        automation={'version':1,'policies':{BUSINESS:{'triggers':['人工发起'],'rules':{k:{'mode':'自动运行' if k=='observe' else '仅给出建议'} for k in ['observe','state','data','supplement','write','learn']}}}})
                    flow=config['flows'][BUSINESS];flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False);flow['confirmedAt']=datetime.now(UTC).isoformat()
                    call('PUT','/setup/draft',{'revision':draft['revision'],'configuration':config})
                save('prepared-material',{'material_id':material['id'],'business':BUSINESS,'domain_quality_verified':False})
                print(json.dumps({'public_rule_and_draft_saved':True,'source_or_model_registered_by_script':False,'task_created':False}));return
            with Session(engine()) as db:
                found=[t for t in db.scalars(select(models.Task).where(models.Task.workspace_id==access['workspace_id']))
                       if t.data.get('scene_snapshots') and t.status=='workflow_completed']
                if len(found)!=1:raise ValueError('real UI must complete exactly one source-bound local-model business task')
                task=found[0];instance_id=task.data['instance_id'];task_id=task.id
            task=get('/workspace/tasks/'+task_id)
            assert task['business']['name']==BUSINESS
            runtime=task['workflow_runtime'];receipt=runtime['receipts'][0];run_id=receipt['output']['geo_run_id']
            assert receipt['execution_mode']=='registered_ability' and receipt['candidate_only'] and receipt['output']['anomalies']==[]
            review=next(r for r in runtime['receipts'] if r['step_id']=='review')
            assert review['role']==ROLE and review['note'].strip() and review['scene_snapshots']==task['scene_snapshots']
            result=receipt['output']['spatial_segmentation']
            assert file_digest(OUT/'browser-task-rgb.png')==result['image_sha256']
            export=get('/workspace/tasks/'+task_id+'/spatial/'+run_id+'/native-geojson')
            assert json.loads((OUT/'browser-task-boundaries.json').read_text())==export
            assert len(export['features'])==len(result['masks']) and export['candidate_only']
            assert export['business_review']['receipts'][-1]['note']==review['note']
            with Session(engine()) as db:
                memory=db.get(models.Memory,runtime['data_receipt_id']);run=db.get(models.GeoRun,run_id)
                assert memory.status=='candidate' and not memory.data['entity_state_updated']
                assert run.status=='succeeded' and run.data['workflow_task_id']==task_id and run.data.get('wms_origin')
                assert task['scene_snapshots'][0]['snapshot']['wms_origin']==run.data['wms_origin']
                assert file_digest(OUT/'browser-task-original.tif')==run.data['input_sha256']
                rows={};artifacts={}
                for name in CLASSES:
                    cls=getattr(models,name)
                    for row in db.scalars(select(cls).where(cls.workspace_id==access['workspace_id'])):
                        value={col.name:getattr(row,col.name).isoformat() if isinstance(getattr(row,col.name),datetime) else getattr(row,col.name) for col in cls.__table__.columns}
                        rows[name+':'+row.id]=digest(value)
                        if name=='Evidence':
                            actual=file_digest(settings().storage_dir/row.storage_key);assert actual==row.sha256;artifacts[row.id]=actual
                old=json.loads((OUT/'old-own-map.json').read_text());assert all(rows.get(k)==v for k,v in old.items()),'original own WMS read/scene changed'
                assert all(obj.version==1 for obj in db.scalars(select(models.Entity).where(models.Entity.workspace_id==access['workspace_id'])))
            current=existing(access['workspace_id']);baseline=json.loads((OUT/'baseline.json').read_text());assert current==baseline,'old data or cloud calls changed'
            snapshot={'rows':rows,'artifacts':artifacts,'task':task,'old_data':current,'run_id':run_id}
            if phase=='check':save('before-restart',snapshot)
            else:
                before=json.loads((OUT/'before-restart.json').read_text());assert snapshot==before,'business results changed after ordinary restart'
                save('after-restart',snapshot);save('verification',{'passed':True,'real_local_sam_business_task':True,'source_bound':True,'restart_durable':True,
                    'own_records':len(rows),'own_artifact_sha':len(artifacts),'old_records_unchanged':len(baseline['records']),'old_originals_unchanged':len(baseline['originals']),
                    'new_cloud_calls':0,'domain_quality_verified':False,'entity_state_updated':False,'experience_published':False})
            print(json.dumps({'phase':phase,'passed':True,'own_records':len(rows),'artifact_sha':len(artifacts),'old_records_unchanged':len(baseline['records']),'new_cloud_calls':0}))
        finally:
            response=c.post('/logout');assert response.status_code in [200,204],'own QA session logout failed'

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','check','restart-check']);main(p.parse_args().phase)
