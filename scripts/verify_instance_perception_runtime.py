"""Public park-observation fixture; actual local CPU/RTSP, no cloud or physical devices."""
import os,json,time,sys,hashlib
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import User,Workspace,Membership,CloudCall,Task,Entity,Memory,Observation,VideoRun,StreamSession
from generic_agents.db import engine
from generic_agents.security import password_hash
from generic_agents.instance_compiler import signature_value
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
ACCESS=ROOT/'.runtime/instance-perception-access.json'
OUT=ROOT/'docs/evidence/instance-perception-v06';OUT.mkdir(parents=True,exist_ok=True)

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))
def access():return json.loads(ACCESS.read_text())
@contextmanager
def connection():
    a=access();c=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=20)
    for attempt in range(30):
        try:
            r=c.post('/api/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();break
        except httpx.ConnectError:
            if attempt==29:c.close();raise
            time.sleep(.2)
    c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
    try:yield c
    finally:c.close()
def call(c,method,path,body=None):
    r=c.request(method,'/api'+path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
def wait_for(read,terminal,timeout=150):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        row=read();progress=(row['status'],row.get('stage'),row.get('processing_status'))
        if progress!=last:print('进度：'+str(progress),flush=True);last=progress
        if terminal(row):return row
        time.sleep(.3)
    raise RuntimeError('Operation still live; checkpoint preserved, inspect before resuming')

phase=sys.argv[1]
if phase=='prepare':
    if not ACCESS.exists():
        a={'username':'park_perception_'+uuid4().hex[:8],'password':uuid4().hex}
        with Session(engine()) as db:
            u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开园区感知关联验证')
            db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id
        fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(a,f)
        with Session(engine()) as db:save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all()),'workspace_id':a['workspace_id'],'cloud_calls_created':0,'physical_device_actions':0})
    with connection() as c:
        cfg=call(c,'GET','/setup/draft')
        if not cfg['configuration']:
            raw='公开园区观测规则\n对照视频和保存画面核查动物持续出现的候选线索。\n明确记录业务异常、非异常或证据不足的判断和依据。\n拍摄时间未校准时，不认定当前对象状态；不自动执行设备动作。'.encode()
            response=c.post('/api/setup/materials',files={'file':('公开园区观测规则.txt',raw,'text/plain')});response.raise_for_status();m=response.json()
            m=wait_for(lambda:call(c,'GET','/setup/materials/'+m['id']),lambda x:x['status'] in ['parsed','failed'])
            assert m['status']=='parsed'
            flow={'trigger':'收到设备报告的候选线索或人工发起','output':'视频核查结论与原件依据','steps':[
                {'id':'check','name':'对照视频核查','purpose':'核对候选轨迹是否与视频一致','agent':'人工确认','role':'核查负责人','output':'画面核查意见',
                 'sourceId':m['id'],'citation':m['blocks'][1]['text'],'citations':[{'material_id':m['id'],'block_id':m['blocks'][1]['id'],'quote':m['blocks'][1]['text']}],
                 'nextId':'record','condition':'','elseId':'end'},
                {'id':'record','name':'记录业务判断','purpose':'按照业务规则判断，证据不足时明确保留不确定性','agent':'人工确认','role':'核查负责人',
                 'output':'业务异常判断与核查结论','sourceId':m['id'],'citation':m['blocks'][2]['text'],'nextId':'end','condition':'','elseId':'end'}],
                'confirmedAt':'2026-10-08T12:00:00+00:00'}
            value={'version':1,'connections':[],'resources':[],'materials':[m],
                'instanceProfile':{'name':'公开园区观测工作区','domain':'园区巡查','goal':'核查设备线索并保留视频证据及不确定性','objectCandidates':['观测区域']},
                'businesses':['视频线索核查'],'flows':{'视频线索核查':flow},'automation':{'version':1,'policies':{'视频线索核查':{'rules':{'write':{'mode':'仅给出建议'}}}}}}
            flow['confirmedSignature']=json.dumps(signature_value(value,flow),ensure_ascii=False)
            call(c,'PUT','/setup/draft',{'revision':cfg['revision'],'configuration':value})
        installations=call(c,'GET','/specialists/installations')
        installation=installations[0] if installations else call(c,'POST','/specialists/installations',{'pack_id':'rtdetr-v2-r18','source':'official','backend':'cpu'})
        if installation['status'] in ['queued','installing']:
            installation=wait_for(lambda:next(x for x in call(c,'GET','/specialists/installations') if x['id']==installation['id']),lambda x:x['status'] in ['ready','failed','cancelled'])
        assert installation['status']=='ready',installation.get('error')
        if not installation.get('capability_id'):installation=call(c,'POST','/specialists/installations/'+installation['id']+'/register',{'version':installation['version']})
        gateways=call(c,'GET','/media/gateways')
        gateway=gateways[0] if gateways else call(c,'POST','/media/gateways',{'name':'本机园区公开视频验证','api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554'})
        if gateway['status']!='ready':gateway=call(c,'POST','/media/gateways/'+gateway['id']+'/discover',{'version':gateway['version']})
        channels=call(c,'GET','/media/channels')
        channel=channels[0] if channels else call(c,'POST','/media/channels',{'gateway_id':gateway['id'],'gateway_version':gateway['version'],'path':'validation','name':'公开猫观察场景','read_authorized':True})
        if not channel.get('validation_current'):
            sessions=call(c,'GET','/media/sessions')
            live=next((s for s in sessions if s['status'] in ['queued','connecting','capturing'] or s.get('processing_status') in ['queued','running']),None)
            session=live or call(c,'POST','/media/channels/'+channel['id']+'/capture',{'channel_version':channel['version'],'installation_id':installation['id'],'seconds':4,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':None}})
            session=wait_for(lambda:next(x for x in call(c,'GET','/media/sessions') if x['id']==session['id']),lambda x:x.get('processing_status') in ['succeeded','failed'] or x['status'] in ['failed','cancelled'])
            assert session['status']=='captured' and session['processing_status']=='succeeded',session.get('error')
            channel=next(x for x in call(c,'GET','/media/channels') if x['id']==channel['id'])
        channel=call(c,'POST','/media/channels/'+channel['id']+'/register',{'version':channel['version']})
        save('selection',{'workspace_id':access()['workspace_id'],'installation_id':installation['id'],'channel_id':channel['id'],'gateway_id':gateway['id'],
            'source_capability_id':channel['capability_id'],'model_capability_id':installation['capability_id'],'model_backend':'cpu',
            'source':'public COCO cats panning fixture via owned loopback MediaMTX','physical_device_identity':'not_verified'})
    print('公开规则解析、本地模型缓存校验/CPU加载、RTSP读取/本地分析及来源登记已准备；等待真实界面生成对象和业务关联。')
elif phase in ['collect','snapshot','restart-check']:
    with connection() as c:
        instances=call(c,'GET','/instances');assert len(instances)==1;i=instances[0]
        objects=call(c,'GET','/instances/'+i['id']+'/objects');tasks=call(c,'GET','/workspace/instances/'+i['id']+'/tasks')
        details=[call(c,'GET','/workspace/tasks/'+t['id']) for t in tasks]
        observations=call(c,'GET','/workspace/instances/'+i['id']+'/observations')
        if phase=='collect':call(c,'POST','/media/daily/reconcile')
        daily=call(c,'GET','/workspace/instances/'+i['id']+'/daily');sessions=call(c,'GET','/media/sessions')
        videos=[call(c,'GET','/videos/'+s['video_run_id']) for s in sessions if s['video_run_id']]
        with Session(engine()) as db:
            cloud=sorted(db.scalars(select(CloudCall.id)).all());assert cloud==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
            memories=[{'id':x.id,'status':x.status,'version':x.version,'data':x.data,'text':x.text} for x in db.scalars(select(Memory).where(Memory.workspace_id==access()['workspace_id']).order_by(Memory.id))]
        record={'instances':instances,'releases':call(c,'GET','/instances/'+i['id']+'/releases'),'objects':objects,'tasks':details,
            'observations':observations,'daily':daily,'sessions':sessions,'videos':videos,'bindings':call(c,'GET','/media/domain-bindings'),'memories':memories,'cloud_ids':cloud}
        if phase=='collect':
            assert tasks and observations and daily
            assert all(t['instance_id']==i['id'] and t['business_name']=='视频线索核查' for t in details)
            assert all(v['status']=='succeeded' for v in videos)
            assert all(x['source_clock_utc']=='unknown' for x in observations)
            save('delivered',record)
        elif phase=='snapshot':
            assert any(t['status']=='workflow_completed' and t['perception_review']['decision']=='not_abnormal' for t in details)
            assert any(t['workflow_runtime']['drafts'] for t in details)
            for video in videos:
                original=c.get('/api/videos/'+video['id']+'/original');original.raise_for_status();assert hashlib.sha256(original.content).hexdigest()==video['input_sha256']
            assert objects[0]['version']==1
            save('before-restart',record)
        else:
            assert record==json.loads((OUT/'before-restart.json').read_text()),'Restart changed receipt or business data'
            save('after-restart',record);save('runtime',{'ordinary_setup_services_restart':True,'records_identical':True,'instances':len(instances),'objects':len(objects),
                'business_tasks':len(tasks),'observations':len(observations),'daily_receipts':len(daily),'source_clocks_unknown':True,
                'entity_state_changed':False,'cloud_calls_created':0,'model_backend':'cpu','physical_devices_verified':False,'domain_quality_verified':False,'postgres_restart':False})
    print('实例、视频线索、明确业务任务、每日收件及保存记录已核对。')
else:raise SystemExit('Unknown phase')
