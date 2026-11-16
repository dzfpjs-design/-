"""Checkpointed real local/synthetic RTSP evidence. No physical device/cloud call."""
import argparse,json,time,hashlib
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
from generic_agents.db import engine
from generic_agents.models import CloudCall,Entity,Job,ContinuousStream,StreamSession,VideoRun,Observation,Task,Memory
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/local-continuous-v06';OUT.mkdir(parents=True,exist_ok=True)
SELECTION=OUT/'selection.json'
def save(name,value):
    target=OUT/(name+'.json');tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(target)
def run(phase):
    access=json.loads((ROOT/'.runtime/understanding-validation-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for _ in range(100):
            try:c.get('/health').raise_for_status();break
            except httpx.HTTPError:time.sleep(.1)
        else:raise AssertionError('API health timeout')
        r=c.post('/api/login',json={k:access[k] for k in ['username','password']});r.raise_for_status()
        c.headers.update({'X-Workspace-ID':access['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        def call(method,path,body=None):
            r=c.request(method,'/api'+path,json=body);r.raise_for_status();return r.json()
        if phase in ['prepare','revalidate']:
            if SELECTION.exists() and phase=='prepare':print('准备记录已存在，不重复读取');return
            if phase=='revalidate':
                for name in ['selection','runtime','progress']:
                    path=OUT/(name+'.json')
                    if path.exists() and not path.with_name(name+'-initial.json').exists():path.with_name(name+'-initial.json').write_bytes(path.read_bytes())
            previous=json.loads((ROOT/'docs/evidence/local-domain-v06/selection.json').read_text())
            ch=next(x for x in call('GET','/media/channels') if x['id']==previous['channel_id'])
            install=next(x for x in call('GET','/specialists/installations') if x['id']==previous['installation_id'])
            row=call('POST','/media/channels/'+ch['id']+'/capture',{'channel_version':ch['version'],'installation_id':install['id'],
                'seconds':6,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}})
            save('prepare-checkpoint',{'session_id':row['id']})
            deadline=time.monotonic()+100
            while time.monotonic()<deadline:
                row=next(x for x in call('GET','/media/sessions') if x['id']==row['id'])
                if row['processing_status'] in ['succeeded','failed'] or row['status'] in ['failed','cancelled']:break
                time.sleep(.5)
            assert row['status']=='captured' and row['processing_status']=='succeeded',row.get('error')
            ch=next(x for x in call('GET','/media/channels') if x['id']==ch['id'])
            call('POST','/media/channels/'+ch['id']+'/register',{'version':ch['version']})
            ch=next(x for x in call('GET','/media/channels') if x['id']==ch['id'])
            binding=next(x for x in call('GET','/media/domain-bindings') if x['channel_id']==ch['id'])
            entity=next(x for x in call('GET','/media/domain-options') if x['id']==previous['entity_id'])
            binding=call('PUT','/media/channels/'+ch['id']+'/domain-binding',{'version':binding['version'],'channel_version':ch['version'],
                'entity_id':entity['id'],'entity_version':entity['version'],'reason':'本机公开图片合成视频持续接收验证，关联原验证场景',
                'confirm_scene_association':True})
            with Session(engine()) as db:
                obj=db.get(Entity,entity['id']);cloud=db.scalars(select(CloudCall.id)).all()
                save('selection',{**previous,'binding_id':binding['id'],'proof_session_id':row['id'],'cloud_ids':sorted(cloud),
                    'entity_before':{'version':obj.version,'data':obj.data},'prepared_at':time.time()})
            print('当前处理版本读取、登记与场景关联已验证；等待从真实界面启动持续接收');return
        selection=json.loads(SELECTION.read_text());identity=selection.get('stream_id')
        if phase=='collect':
            if not identity:
                streams=call('GET','/media/continuous');row=next(x for x in streams if x['channel_id']==selection['channel_id'])
                assert row['created_at'] and row['max_segments']==3
                selection={**selection,'stream_id':row['id']};save('selection',selection);identity=row['id']
            evidence=[];deadline=time.monotonic()+150;previous=None;independent=False
            while time.monotonic()<deadline:
                row=next(x for x in call('GET','/media/continuous') if x['id']==identity)
                clips=[x for x in call('GET','/media/sessions') if x['id'] in row['session_ids']]
                state=(row['status'],row['saved_segments'],sum(x['processing_status']=='succeeded' for x in clips))
                if state!=previous:print(json.dumps({'recording_progress':state}),flush=True);previous=state;evidence.append({'at':time.time(),'state':state})
                if row['status']=='recording' and state[2]>0:independent=True
                if row['status'] in ['blocked','stopped','exhausted'] and all(x['processing_status'] in ['succeeded','failed'] for x in clips if x['video_run_id']):break
                time.sleep(.3)
            save('progress',{'samples':evidence,'analysis_completed_while_recording':independent})
            assert row['status']=='exhausted' and row['saved_segments']==3 and row['connections']==1
            assert all(x['status']=='captured' and x['processing_status']=='succeeded' for x in clips)
            assert len({x['connection_id'] for x in clips})==1
            clips.sort(key=lambda x:x['continuous_sequence'])
            for a,b in zip(clips,clips[1:]):assert a['receipt']['last_source_pts']==b['receipt']['first_source_pts']
            details=[call('GET','/videos/'+x['video_run_id']) for x in clips]
            assert [x['source_context']['continuous_with_previous'] for x in details]==[False,True,True]
            save('runtime',{'stream':row,'sessions':clips,'videos':details,'public_synthetic_only':True,'source_clock_utc':'unknown'})
            print(json.dumps({'saved_segments':3,'analysis_samples':[x['saved_samples'] for x in details],'same_connection':True,
                'analysis_completed_while_recording':independent}));return
        if phase in ['snapshot','restart-check']:
            with Session(engine()) as db:
                stream=db.get(ContinuousStream,identity);obj=db.get(Entity,selection['entity_id'])
                assert {'version':obj.version,'data':obj.data}==selection['entity_before']
                assert sorted(db.scalars(select(CloudCall.id)).all())==selection['cloud_ids']
                identities=[identity,*[json.loads((OUT/(name+'-start.json')).read_text())['id'] for name in ['pause','offline','crash']]]
                videos=db.scalars(select(VideoRun).where(VideoRun.workspace_id==access['workspace_id'],
                    VideoRun.data['source_context']['continuous_id'].as_string().in_(identities))).all()
                runs=[x.id for x in videos]
                obs=db.scalars(select(Observation).where(Observation.data['run_id'].as_string().in_([x.id for x in videos]))).all()
                tasks=db.scalars(select(Task).where(Task.workspace_id==access['workspace_id'],Task.data['run_id'].as_string().in_(runs))).all()
                memories=db.scalars(select(Memory).where(Memory.workspace_id==access['workspace_id'],Memory.data['run_id'].as_string().in_(runs))).all()
                assert all(x.observed_at is None and x.business_day is None and x.status=='extracted_unclocked' for x in obs)
                assert all(x.status=='draft' and not x.data['evidence']['device_action_authorized'] for x in tasks)
                def record(x):return {'id':x.id,'version':x.version,'status':x.status,'data':x.data}
                originals={}
                for video in videos:
                    response=c.get('/api/videos/'+video.id+'/original');response.raise_for_status()
                    actual=hashlib.sha256(response.content).hexdigest();assert actual==video.data['input_sha256'];originals[video.id]=actual
                streams=[db.get(ContinuousStream,x) for x in identities]
                snapshot={'stream':record(stream),'all_streams':sorted([record(x) for x in streams],key=lambda x:x['id']),
                    'sessions':sorted([record(db.get(StreamSession,x)) for m in streams for x in m.data['session_ids']],key=lambda x:x['id']),
                    'videos':sorted([record(x) for x in videos],key=lambda x:x['id']),
                    'observations':sorted([record(x) for x in obs],key=lambda x:x['id']),
                    'business_tasks':sorted([record(x) for x in tasks],key=lambda x:x['id']),
                    'memories':sorted([record(x) for x in memories],key=lambda x:x['id']),'original_sha256':originals,
                    'entity':{'version':obj.version,'data':obj.data},'cloud_ids':selection['cloud_ids']}
                if phase=='snapshot':save('before-restart',snapshot)
                else:
                    assert snapshot==json.loads((OUT/'before-restart.json').read_text());save('after-restart',snapshot)
                print(json.dumps({'phase':phase,'videos':len(videos),'candidates':len(obs),'task_drafts':len(tasks),
                    'durable_equality':phase=='restart-check','new_cloud_calls':0}));return
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','revalidate','collect','snapshot','restart-check']);run(parser.parse_args().phase)
