"""Own public stock-rule fixture and real builtin workflow, never cloud/model mocks."""
import os,json,time,sys,hashlib
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import User,Workspace,Membership,CloudCall,Memory,Job,Invocation
from generic_agents.db import engine
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
ACCESS=ROOT/'.runtime/workflow-abilities-access.json';OUT=ROOT/'docs/evidence/workflow-abilities-v06';OUT.mkdir(parents=True,exist_ok=True)
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def access():return json.loads(ACCESS.read_text())
@contextmanager
def connection():
    a=access()
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=20) as c:
        for n in range(30):
            try:r=c.post('/api/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();break
            except httpx.ConnectError:
                if n==29:raise
                time.sleep(.2)
        c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']});yield c
def call(c,method,path,body=None):
    r=c.request(method,'/api'+path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
phase=sys.argv[1]
if phase=='prepare':
    if not ACCESS.exists():
        a={'username':'stock_workflow_'+uuid4().hex[:8],'password':uuid4().hex}
        with Session(engine()) as db:
            u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开库存自动节点验证');db.add_all([u,w]);db.flush()
            db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id
            save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all()),'workspace_id':w.id})
        fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(a,f)
    with connection() as c:
        caps=call(c,'GET','/capabilities');cap=next((x for x in caps if x['name']=='stock_average'),None)
        if not cap:
            value={'kind':'tool','namespace':'public_stock','name':'stock_average','version':'1.0.0','owner':'公开库存验证','description':'计算物料库存平均值',
                'allowed_agent_roles':['data'],'execution':{'adapter':'builtin','handler':'aggregate_values','input_bindings':{'records':'archive_records'}},
                'data_policy':{'cloud_allowed':False,'external_allowed':False},'specification':{},
                'input_schema':{'type':'object','required':['records','field','operation'],'additionalProperties':False,'properties':{
                    'records':{'type':'array','title':'参与处理的物料记录','items':{'type':'object'}},'field':{'type':'string','title':'库存数量字段名称'},
                    'operation':{'enum':['mean','sum','count'],'title':'汇总方式'}}},
                'output_schema':{'type':'object','required':['value','sample_count','provenance'],'properties':{
                    'value':{'type':['number','null'],'title':'库存数量平均值'},'sample_count':{'type':'integer','title':'有效物料数'},
                    'missing_count':{'type':'integer','title':'缺少数量的物料数'},'provenance':{'type':'array','title':'对象版本依据'},
                    'field':{'type':'string','title':'库存字段'},'operation':{'type':'string','title':'汇总方式'},'method':{'type':'string','title':'处理方法'}}}}
            cap=call(c,'POST','/capabilities',value);cap=call(c,'POST','/capabilities/'+cap['id']+'/validate',{'expected_version':cap['version'],'reason':'登记本机只读公开库存统计能力'})
            cap=call(c,'POST','/capabilities/'+cap['id']+'/publish',{'expected_version':cap['version'],'reason':'只读、保留对象依据，不写档案或外发数据'})
        draft=call(c,'GET','/setup/draft')
        if not draft['configuration']:
            raw='公开库存核查规则\n以任务发起时保存的库存数量字段计算平均值。\n负责人对照对象记录与规则核对处理结果，确认后保存候选经验。\n不自动更新物料档案，不推断未提供的观测时间。'.encode()
            r=c.post('/api/setup/materials',files={'file':('公开库存核查规则.txt',raw,'text/plain')});r.raise_for_status();m=r.json();deadline=time.monotonic()+90
            while m['status'] not in {'parsed','failed'} and time.monotonic()<deadline:
                time.sleep(.3);m=call(c,'GET','/setup/materials/'+m['id'])
            assert m['status']=='parsed','Original parse incomplete; saved operation retained'
            steps=[]
            for sid,name,agent,role,output,index in [('calculate','汇总库存数量','数据管理智能体','','库存数量平均值',1),('review','核对库存汇总结果','人工确认','库存负责人','库存核查结论',2)]:
                steps.append({'id':sid,'name':name,'purpose':m['blocks'][index]['text'],'agent':agent,'role':role,'output':output,'sourceId':m['id'],
                    'citation':m['blocks'][index]['text'],'citations':[{'material_id':m['id'],'block_id':m['blocks'][index]['id'],'quote':m['blocks'][index]['text']}],
                    'nextId':'review' if sid=='calculate' else 'end','condition':'','elseId':'end'})
            config={'version':1,'connections':[],'resources':[],'materials':[m],'businesses':['库存数量核查'],
                'instanceProfile':{'name':'公开库存工作区','domain':'物料管理','goal':'核查库存平均值并保留源对象、规则和核查记录','objectCandidates':['物料']},
                'flows':{'库存数量核查':{'trigger':'人工启动库存核查','output':'库存核查结论','steps':steps}},
                'automation':{'version':1,'policies':{'库存数量核查':{'triggers':['人工发起'],'rules':{a:{'mode':'自动运行' if a=='data' else '仅给出建议'} for a in ['observe','state','data','supplement','write','learn']}}}}}
            call(c,'PUT','/setup/draft',{'revision':draft['revision'],'configuration':config})
        save('selection',{'capability_id':cap['id'],'workspace_id':access()['workspace_id'],'tool':'local registered archive aggregate','not_ai_quality_evaluation':True})
    print('公开规则解析与本地统计能力就绪；等待真实界面完成能力、字段和流程确认。')
elif phase in {'snapshot','restart-check'}:
    with connection() as c:
        instances=call(c,'GET','/instances');assert len(instances)==1;i=instances[0]
        objects=call(c,'GET','/instances/'+i['id']+'/objects');tasks=call(c,'GET','/workspace/instances/'+i['id']+'/tasks');details=[call(c,'GET','/workspace/tasks/'+t['id']) for t in tasks]
        assert any(t['status']=='workflow_completed' and t['workflow_runtime']['receipts'][0]['output']['value']==20 for t in details)
        assert len(objects)==2 and all(o['version']==1 for o in objects)
        assert any(t['workflow_runtime']['drafts'] for t in details),'Keep a saved unfinished human review'
        with Session(engine()) as db:
            cloud=sorted(db.scalars(select(CloudCall.id)).all());assert cloud==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
            def rows(cls):return [{'id':x.id,'version':x.version,'status':x.status,'data':x.data} for x in db.scalars(select(cls).where(cls.workspace_id==access()['workspace_id']).order_by(cls.id))]
            record={'instances':instances,'releases':call(c,'GET','/instances/'+i['id']+'/releases'),'objects':objects,'tasks':details,
                'memories':rows(Memory),'jobs':rows(Job),'invocations':rows(Invocation),'cloud_ids':cloud}
        for m in call(c,'GET','/setup/materials'):
            original=c.get('/api/setup/materials/'+m['id']+'/content');original.raise_for_status();assert hashlib.sha256(original.content).hexdigest()==m['hash']
        if phase=='snapshot':save('before-restart',record)
        else:
            assert record==json.loads((OUT/'before-restart.json').read_text()),'Saved records changed after ordinary services restart'
            save('after-restart',record);save('runtime',{'ordinary_services_restart':True,'records_identical':True,'objects':len(objects),'tasks':len(tasks),'registered_invocations':len(record['invocations']),
                'cloud_calls_created':0,'entity_state_modified':False,'industry_model_quality_verified':False,'physical_devices_verified':False,'postgres_restart':False})
    print('自动节点调用、源对象、人工结果、候选经验与重启保存记录已核对。')
else:raise SystemExit('Unknown phase')
