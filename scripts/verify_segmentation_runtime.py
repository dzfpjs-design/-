"""Own public image fixture; real pinned CPU specialist execution through the domain workflow."""
import os,json,time,sys,hashlib
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import User,Workspace,Membership,CloudCall,Memory,Job,Invocation
from generic_agents.db import engine
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
ACCESS=ROOT/'.runtime/segmentation-access.json';OUT=ROOT/'docs/evidence/segmentation-v06';OUT.mkdir(parents=True,exist_ok=True)
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def access():return json.loads(ACCESS.read_text())
@contextmanager
def connection():
    a=access()
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for attempt in range(30):
            try:r=c.post('/api/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();break
            except httpx.ConnectError:
                if attempt==29:raise
                time.sleep(.2)
        c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']});yield c
def call(c,method,path,body=None):
    r=c.request(method,'/api'+path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
def wait(read,done):
    deadline=time.monotonic()+120
    while True:
        result=read()
        if done(result):return result
        if time.monotonic()>deadline:raise TimeoutError('实际后台尚未完成，记录保留')
        time.sleep(.3)
phase=sys.argv[1]
if phase=='prepare':
    if not ACCESS.exists():
        a={'username':'segmentation_'+uuid4().hex[:8],'password':uuid4().hex}
        with Session(engine()) as db:
            user=User(username=a['username'],password_hash=password_hash(a['password']));workspace=Workspace(name='公开实例掩膜验证');db.add_all([user,workspace]);db.flush()
            db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit();a['workspace_id']=workspace.id
            save('baseline',{'workspace_id':workspace.id,'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
        fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(a,f)
    with connection() as c:
        installs=call(c,'GET','/specialists/installations')
        row=installs[0] if installs else call(c,'POST','/specialists/installations',{'pack_id':'sam2-1-tiny','source':'mirror','backend':'cpu'})
        if row['status'] in {'failed','cancelled'}:
            row=call(c,'POST','/specialists/installations',{'pack_id':'sam2-1-tiny','source':'mirror','backend':'cpu','version':row['version']})
        row=wait(lambda:next(x for x in call(c,'GET','/specialists/installations') if x['id']==row['id']),lambda x:x['status'] in {'ready','failed','cancelled'})
        assert row['status']=='ready',row.get('error')
        if not row.get('capability_id'):row=call(c,'POST','/specialists/installations/'+row['id']+'/register',{'version':row['version']})
        draft=call(c,'GET','/setup/draft')
        if not draft['configuration']:
            raw='公开图片核查规则\n本地模型从任务选中的一张原图提取通用候选区域，保留原图和结果供人工核对。\n负责人对照原图与候选区域保存核查结论。\n候选目标不证明身份或业务异常，不更新场景状态，不发布经验。'.encode()
            res=c.post('/api/setup/materials',files={'file':('公开图片核查规则.txt',raw,'text/plain')});res.raise_for_status();m=res.json()
            m=wait(lambda:call(c,'GET','/setup/materials/'+m['id']),lambda x:x['status'] in {'parsed','failed'});assert m['status']=='parsed'
            steps=[]
            for sid,name,agent,role,output,index in [('detect','提取原图候选目标','多模感知智能体','','通用候选区域',1),('review','核对原图与候选结果','人工确认','图片核查负责人','图片核查结论',2)]:
                steps.append({'id':sid,'name':name,'purpose':m['blocks'][index]['text'],'agent':agent,'role':role,'output':output,'sourceId':m['id'],
                    'citation':m['blocks'][index]['text'],'citations':[{'material_id':m['id'],'block_id':m['blocks'][index]['id'],'quote':m['blocks'][index]['text']}],
                    'nextId':'review' if sid=='detect' else 'end','condition':'','elseId':'end'})
            config={'version':1,'connections':[],'resources':[],'materials':[m],'businesses':['图片候选核查'],
                'instanceProfile':{'name':'公开实例掩膜核查工作区','domain':'场景资料管理','goal':'核对本地候选与原图并保留核查记录','objectCandidates':['观察场景']},
                'flows':{'图片候选核查':{'trigger':'人工提交原图','output':'图片核查结论','steps':steps}},
                'automation':{'version':1,'policies':{'图片候选核查':{'triggers':['人工发起'],'rules':{a:{'mode':'自动运行' if a=='observe' else '仅给出建议'} for a in ['observe','state','data','supplement','write','learn']}}}}}
            call(c,'PUT','/setup/draft',{'revision':draft['revision'],'configuration':config})
        image=ROOT/'.runtime/rtdetr-bootstrap/coco-cats.jpg';assert image.is_file()
        save('selection',{'installation_id':row['id'],'capability_id':row['capability_id'],'backend':'cpu','image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
            'image_path':str(image),'source':'previously downloaded public COCO example, private reports excluded','domain_quality_verified':False})
    print('固定权重本机CPU加载与登记、公开规则解析已就绪，等待真实界面确认能力并提交原图任务。')
elif phase=='inputs':
    with connection() as c:
        i=call(c,'GET','/instances')[0];assert i['name']=='公开实例掩膜核查工作区'
        raw=(ROOT/'.runtime/rtdetr-bootstrap/coco-cats.jpg').read_bytes()
        images=call(c,'GET','/workspace/instances/'+i['id']+'/input-images')
        if not images:
            r=c.post('/api/workspace/instances/'+i['id']+'/input-images',files={'file':('公开COCO区域样例.jpg',raw,'image/jpeg')});r.raise_for_status();images=[r.json()]
        assert images[0]['sha256']==hashlib.sha256(raw).hexdigest()
        row=call(c,'GET','/specialists/installations')[0];runs=call(c,'GET','/specialists/runs')
        if not runs:
            r=c.post('/api/specialists/installations/'+row['id']+'/samples',files={'file':('公开COCO区域样例.jpg',raw,'image/jpeg')});r.raise_for_status();runs=[r.json()]
        run=wait(lambda:next(x for x in call(c,'GET','/specialists/runs') if x['id']==runs[0]['id']),lambda x:x['status'] in {'succeeded','failed','cancelled'})
        assert run['status']=='succeeded',run.get('error')
        assert run['result']['masks'] and run['result']['cloud_calls']==0
        for mask in run['result']['masks']:
            r=c.get('/api/specialists/runs/'+run['id']+'/masks/'+mask['mask_evidence_id']);r.raise_for_status();assert hashlib.sha256(r.content).hexdigest()==mask['mask_sha256']
        save('inputs',{'instance_id':i['id'],'image':images[0],'sample':run,'upload_surface':'API public fixture; browser upload already verified in prior detection slice',
            'quality_scope':'public COCO RGB; not aerial domain accuracy'})
    print('公开RGB样例实际CPU掩膜与所有透明PNG原件已核对；供真实界面复用原图。')
elif phase=='tile':
    import io
    from PIL import Image
    with connection() as c:
        runs=call(c,'GET','/specialists/runs');run=next((x for x in runs if x['name']=='公开COCO放大窗口验证.png'),None)
        if not run:
            with Image.open(ROOT/'.runtime/rtdetr-bootstrap/coco-cats.jpg') as im:
                buf=io.BytesIO();im.resize((1280,960)).save(buf,format='PNG');raw=buf.getvalue()
            row=call(c,'GET','/specialists/installations')[0]
            r=c.post('/api/specialists/installations/'+row['id']+'/samples',files={'file':('公开COCO放大窗口验证.png',raw,'image/png')});r.raise_for_status();run=r.json()
        run=wait(lambda:next(x for x in call(c,'GET','/specialists/runs') if x['id']==run['id']),lambda x:x['status'] in {'succeeded','failed','cancelled'})
        assert run['status']=='succeeded',run.get('error');r=run['result'];assert len(r['tile_manifest'])==2 and r['crs'] is None
        assert all(0<=m['bbox'][0]<m['bbox'][2]<=1280 and 0<=m['bbox'][1]<m['bbox'][3]<=960 for m in r['masks'])
        for mask in r['masks']:
            response=c.get('/api/specialists/runs/'+run['id']+'/masks/'+mask['mask_evidence_id']);response.raise_for_status();assert hashlib.sha256(response.content).hexdigest()==mask['mask_sha256']
        save('tile-runtime',{'run':run,'fixture':'public COCO resized locally to 1280x960 for window mechanics, not remote sensing data',
            'domain_quality_verified':False,'cloud_calls':0,'mask_artifacts_verified':len(r['masks'])})
    print('公开RGB放大输入：实际CPU两窗口分割、原像素坐标和全部掩膜原件已核对。')
elif phase in {'snapshot','restart-check'}:
    with connection() as c:
        instances=call(c,'GET','/instances');assert len(instances)==1;i=instances[0]
        objects=call(c,'GET','/instances/'+i['id']+'/objects');tasks=call(c,'GET','/workspace/instances/'+i['id']+'/tasks');details=[call(c,'GET','/workspace/tasks/'+t['id']) for t in tasks]
        assert any(t['status']=='workflow_completed' for t in details) and any(t['workflow_runtime']['drafts'] for t in details)
        assert len(objects)==1 and objects[0]['version']==1
        for t in details:
            assert len(t['image_snapshots'])==1
            image=t['image_snapshots'][0];res=c.get('/api/workspace/tasks/'+t['id']+'/images/'+image['id']+'/content');res.raise_for_status();assert hashlib.sha256(res.content).hexdigest()==image['sha256']
            receipt=t['workflow_runtime']['receipts'][0];d=receipt['output']['segmentation'];assert receipt['candidate_only'] and d['image_sha256']==image['sha256'] and d['cloud_calls']==0 and d['backend']=='cpu'
            assert d['masks'] and not receipt['entity_state_updated']
            for mask in d['masks']:
                res=c.get('/api/workspace/tasks/'+t['id']+'/masks/'+mask['mask_evidence_id']+'/content');res.raise_for_status();assert hashlib.sha256(res.content).hexdigest()==mask['mask_sha256']
        samples=call(c,'GET','/specialists/runs')
        for sample in samples:
            response=c.get('/api/specialists/runs/'+sample['id']+'/original');response.raise_for_status();assert hashlib.sha256(response.content).hexdigest()==sample['input_sha256']
            if sample['status']=='succeeded':
                for mask in sample['result']['masks']:
                    response=c.get('/api/specialists/runs/'+sample['id']+'/masks/'+mask['mask_evidence_id']);response.raise_for_status();assert hashlib.sha256(response.content).hexdigest()==mask['mask_sha256']
        with Session(engine()) as db:
            cloud=sorted(db.scalars(select(CloudCall.id)).all());assert cloud==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
            def rows(cls):return [{'id':x.id,'version':x.version,'status':x.status,'data':x.data} for x in db.scalars(select(cls).where(cls.workspace_id==access()['workspace_id']).order_by(cls.id))]
            record={'instances':instances,'releases':call(c,'GET','/instances/'+i['id']+'/releases'),'objects':objects,'tasks':details,'images':call(c,'GET','/workspace/instances/'+i['id']+'/input-images'),
                'installations':call(c,'GET','/specialists/installations'),'samples':call(c,'GET','/specialists/runs'),'jobs':rows(Job),'invocations':rows(Invocation),'memories':rows(Memory),'cloud_ids':cloud}
        if phase=='snapshot':save('before-restart',record)
        else:
            assert record==json.loads((OUT/'before-restart.json').read_text()),'服务重启后记录变化'
            save('after-restart',record);save('runtime',{'records_identical':True,'ordinary_services_restart':True,'actual_cpu_invocations':len(record['invocations']),
                'objects':len(objects),'tasks':len(tasks),'cloud_calls_created':0,'entity_state_modified':False,'domain_quality_verified':False,'production_verified':False})
    print('真实本机实例掩膜结果、任务原图、已完成核查及未完成说明的保存记录已核对。')
else:raise SystemExit('Unknown phase')
