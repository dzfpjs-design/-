"""Actual local model/synthetic RTSP association proof; never private business data."""
import argparse
import hashlib
import json
import time
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall, Observation, Entity, Task, Memory, VideoDomainBinding

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/local-domain-v06';OUT.mkdir(parents=True,exist_ok=True)
STATE=OUT/'selection.json'


def save(name,value):
    path=OUT/(name+'.json');temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)


def call(c,method,path,body=None):
    r=c.request(method,'/api'+path,json=body);r.raise_for_status();return r.json()


def run(phase):
    access=json.loads((ROOT/'.runtime/understanding-validation-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for _ in range(100):
            try:c.get('/health').raise_for_status();break
            except httpx.HTTPError:time.sleep(.1)
        else:raise AssertionError('API health timeout')
        r=c.post('/api/login',json={k:access[k] for k in ['username','password']});r.raise_for_status()
        c.headers.update({'X-Workspace-ID':access['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        channels=lambda:call(c,'GET','/media/channels')
        def read(channel,install):
            row=call(c,'POST','/media/channels/'+channel['id']+'/capture',{'channel_version':channel['version'],
                'installation_id':install['id'],'seconds':6,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}})
            return row['id']
        def wait(identity):
            deadline=time.monotonic()+120;previous=None
            while time.monotonic()<deadline:
                row=next(x for x in call(c,'GET','/media/sessions') if x['id']==identity)
                state=(row['status'],row['processing_status'])
                if state!=previous:print(json.dumps({'capture':state}),flush=True);previous=state
                if row['status']=='failed' or row['processing_status']=='failed':raise AssertionError(row.get('error'))
                if row['processing_status']=='succeeded':return row
                time.sleep(1)
            raise AssertionError('actual local video processing deadline exceeded')
        if phase=='prepare':
            if STATE.exists():raise AssertionError('existing checkpoint: inspect and continue it rather than duplicate setup')
            package=call(c,'POST','/capabilities',{'kind':'domain_package','namespace':'video_validation','name':'observed_scene',
                'version':'1.0.0','owner':'合成视频验收','description':'非现场业务：仅登记公开合成视频观察区域，不生成行业事实',
                'specification':{'entity_schemas':{'scene':{'type':'object','required':['name','description'],'additionalProperties':False,
                    'properties':{'name':{'type':'string'},'description':{'type':'string'}}}}}})
            package=call(c,'POST','/capabilities/'+package['id']+'/validate',{'expected_version':package['version'],'reason':'合成区域契约验证'})
            package=call(c,'POST','/capabilities/'+package['id']+'/publish',{'expected_version':package['version'],'reason':'隔离合成验证：不作为现场领域发布'})
            obj=call(c,'POST','/entities',{'package_id':package['id'],'entity_type':'scene','external_id':'public-synthetic-scene',
                'attributes':{'name':'公开图片合成视频观察区域','description':'公开COCO静态图片合成；不是物理相机、现场对象或模型准确率证明'}})
            source=json.loads((ROOT/'docs/evidence/local-rtsp-v06/selection.json').read_text())
            ch=next(x for x in channels() if x['id']==source['channel_id'])
            install=next(x for x in call(c,'GET','/specialists/installations') if x['status']=='ready')
            identity=read(ch,install)
            save('selection',{'channel_id':ch['id'],'gateway_id':ch['gateway_id'],'entity_id':obj['id'],
                'package_id':package['id'],'installation_id':install['id'],'proof_session_id':identity,'entity_before':obj})
            wait(identity);print('Synthetic declared object and current actual capture proof saved; no association yet.',flush=True)
            return
        selection=json.loads(STATE.read_text());obj_id=selection['entity_id']
        if phase=='capture':
            bindings=call(c,'GET','/media/domain-bindings');binding=next(x for x in bindings if x['channel_id']==selection['channel_id'])
            assert binding['entity_id']==obj_id and binding['status']=='active'
            install=next(x for x in call(c,'GET','/specialists/installations') if x['id']==selection['installation_id'])
            ch=next(x for x in channels() if x['id']==selection['channel_id'])
            saved=next((x for x in call(c,'GET','/media/sessions') if (x.get('domain_binding') or {}).get('binding_id')==binding['id']
                and x['domain_binding']['binding_generation']==binding['generation']),None)
            identity=saved['id'] if saved else read(ch,install)
            save('selection',{**selection,'associated_session_id':identity,'binding_id':binding['id']});wait(identity)
        else:
            state=call(c,'GET','/media/domain-state/'+obj_id)
            daily=call(c,'POST','/media/daily/reconcile') if phase=='snapshot' else call(c,'GET','/media/daily')
            daily=[x for x in daily if x['source_key']=='channel:'+selection['channel_id']]
            assert len(state['observations'])==7 and len(state['tasks'])==2
            assert daily and daily[0]['business_tasks']==2 and daily[0]['data_memory_id']
            session=next(x for x in call(c,'GET','/media/sessions') if x['id']==selection['associated_session_id'])
            video=call(c,'GET','/videos/'+session['video_run_id']);original=c.get('/api/videos/'+video['id']+'/original');original.raise_for_status()
            assert hashlib.sha256(original.content).hexdigest()==video['input_sha256']
            with Session(engine()) as db:
                observations=db.scalars(select(Observation).where(Observation.workspace_id==access['workspace_id'],Observation.entity_id==obj_id)).all()
                assert len(observations)==7 and all(o.observed_at is None and o.business_day is None and o.status=='extracted_unclocked' for o in observations)
                entity=db.get(Entity,obj_id);assert entity.data==selection['entity_before']['data'] and entity.version==selection['entity_before']['version']
                tasks=db.scalars(select(Task).where(Task.workspace_id==access['workspace_id'],Task.data['domain_binding']['entity_id'].as_string()==obj_id)).all()
                assert len(tasks)==2 and all(t.status=='draft' and not t.data['evidence']['device_action_authorized'] for t in tasks)
                for record in daily:assert db.get(Memory,record['data_memory_id']).data['receipt_hash']==record['receipt_hash']
                calls=db.scalars(select(CloudCall).where(CloudCall.workspace_id==access['workspace_id'])).all()
                assert sorted(x.id for x in calls)==sorted(json.loads((ROOT/'.runtime/setup-recovery-baseline.json').read_text())['cloud_ids'])
            evidence={'state':state,'daily':daily,'session':session,'video':video,'original_sha256':video['input_sha256'],'new_cloud_calls':0,
                'scope':'actual synthetic RTSP/local CPU inference; no physical device/domain quality/continuous monitoring proof'}
            if phase=='snapshot':save('before-restart',evidence)
            else:
                assert evidence==json.loads((OUT/'before-restart.json').read_text());save('after-restart',evidence)
            print(json.dumps({'observations':7,'business_drafts':2,'same_entity_version':True,'new_cloud_calls':0,'phase':phase}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','capture','snapshot','restart-check']);run(parser.parse_args().phase)
