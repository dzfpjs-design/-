"""Owned public-data fixture and restart proof; never invokes cloud or physical devices."""
import os,json,time,hashlib,sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,Entity,Task,Memory
from generic_agents.security import password_hash
from generic_agents.instance_compiler import signature_value
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
ACCESS=ROOT/'.runtime/instance-validation-access.json'
OUT=ROOT/'docs/evidence/instance-workspace-v06';OUT.mkdir(parents=True,exist_ok=True)

def save(name,value):
    (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))

def access():return json.loads(ACCESS.read_text())

@contextmanager
def connect():
    a=access();c=httpx.Client(base_url='http://127.0.0.1:8765',timeout=20,trust_env=False)
    r=c.post('/api/login',json={k:a[k] for k in ['username','password']});r.raise_for_status()
    c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
    try:yield c
    finally:c.close()

def call(c,method,path,**kwargs):
    r=c.request(method,'/api'+path,**kwargs);r.raise_for_status();return r.json()

phase=sys.argv[1]
if phase=='prepare':
    if not ACCESS.exists():
        a={'username':'instance_validation_'+uuid4().hex[:8],'password':uuid4().hex}
        with Session(engine()) as db:
            u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开资料业务工作区验证')
            db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id
        fd=os.open(ACCESS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(a,f)
    with connect() as c:
        existing=call(c,'GET','/setup/draft')
        if existing['configuration']:
            print('已有公开验证配置保留；使用当前记录继续。');sys.exit(0)
        raw='公开资料核验规则\n核对名称、数量和登记日期。\n资料齐全后记录结论；资料不全时记录缺项并结束。'.encode()
        r=c.post('/api/setup/materials',files={'file':('公开核验规则.txt',raw,'text/plain')});r.raise_for_status();m=r.json()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            m=call(c,'GET','/setup/materials/'+m['id'])
            if m['status'] in ['parsed','failed']:break
            time.sleep(.2)
        assert m['status']=='parsed'
        flow={'trigger':'由核验负责人发起','output':'核验结论与原文依据','steps':[
            {'id':'check','name':'核对资料','purpose':'对照资料核对登记信息','agent':'人工确认','role':'核验负责人','output':'核对意见',
             'sourceId':m['id'],'citation':m['blocks'][2]['text'],'citations':[{'material_id':m['id'],'block_id':m['blocks'][2]['id'],'quote':m['blocks'][2]['text']}],
             'nextId':'record','condition':'资料是否齐全','elseId':'end'},
            {'id':'record','name':'保存结论','purpose':'保存核验结论','agent':'人工确认','role':'核验负责人','output':'核验结论','sourceId':m['id'],
             'citation':m['blocks'][1]['text'],'nextId':'end','condition':'','elseId':'end'}], 'confirmedAt':'2026-10-08T12:00:00+00:00'}
        cfg={'version':1,'connections':[],'resources':[],'materials':[m],
            'instanceProfile':{'name':'公开资料核验工作区','domain':'档案管理','goal':'核对对象档案并保留核验依据','objectCandidates':['档案条目']},
            'businesses':['资料核验'],'flows':{'资料核验':flow},'automation':{'version':1,'policies':{'资料核验':{'rules':{'write':{'mode':'仅给出建议'}}}}}}
        flow['confirmedSignature']=json.dumps(signature_value(cfg,flow),ensure_ascii=False)
        call(c,'PUT','/setup/draft',json={'revision':existing['revision'],'configuration':cfg})
        with Session(engine()) as db:cloud=sorted(db.scalars(select(CloudCall.id)).all())
        save('baseline',{'workspace_id':access()['workspace_id'],'cloud_ids':cloud,'public_original_sha256':hashlib.sha256(raw).hexdigest(),
             'source':'owned synthetic public manual-workflow fixture','cloud_calls_created':0,'physical_device_actions':0})
    print('公开原件经真实解析worker处理；独立验证配置已保存，等待浏览器生成/执行。')
elif phase in ['snapshot','restart-check']:
    with connect() as c:
        instances=call(c,'GET','/instances');assert len(instances)==1
        i=instances[0];releases=call(c,'GET','/instances/'+i['id']+'/releases');assert releases[0]['snapshot']['deployment']['specialists']=='local'
        objects=call(c,'GET','/instances/'+i['id']+'/objects');tasks=call(c,'GET','/workspace/instances/'+i['id']+'/tasks')
        assert objects and tasks
        details=[call(c,'GET','/workspace/tasks/'+t['id']) for t in tasks]
        with Session(engine()) as db:
            mem=[{'id':m.id,'version':m.version,'status':m.status,'text':m.text,'data':m.data} for m in db.scalars(select(Memory).where(Memory.workspace_id==access()['workspace_id']).order_by(Memory.id))]
            cloud=sorted(db.scalars(select(CloudCall.id)).all())
        assert cloud==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
        materials=call(c,'GET','/setup/materials');originals=[]
        for m in materials:
            r=c.get('/api/setup/materials/'+m['id']+'/content');r.raise_for_status()
            assert hashlib.sha256(r.content).hexdigest()==m['hash'];originals.append({'id':m['id'],'sha256':m['hash']})
        record={'instances':instances,'releases':releases,'objects':objects,'tasks':details,'memories':mem,'originals':originals,
            'configuration':call(c,'GET','/setup/draft'),'cloud_ids':cloud}
        if phase=='snapshot':
            assert any(t['status']=='workflow_completed' for t in details) and mem
            assert any(t['workflow_runtime']['drafts'] for t in details)
            save('before-restart',record)
        else:
            before=json.loads((OUT/'before-restart.json').read_text());assert record==before,'Durable records differ after restart'
            save('after-restart',record);save('runtime',{'ordinary_setup_service_restart':True,'records_identical':True,'cloud_calls_created':0,
                'instances':len(instances),'objects':len(objects),'tasks':len(tasks),'completed':sum(t['status']=='workflow_completed' for t in details),
                'saved_step_notes':sum(len(t['workflow_runtime']['drafts']) for t in details),'memories':len(mem),'source_originals':len(originals),
                'specialists_deployment':'local','postgres_restart':False,'automatic_nodes_verified':False,'physical_devices_verified':False})
    print('真实业务版本/对象/任务/说明/结果/经验/原件已核对。')
else:raise SystemExit('Unknown phase')
