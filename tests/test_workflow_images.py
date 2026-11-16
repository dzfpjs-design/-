"""Contract/authorization tests; specialist fixture is mocked, real inference is separate runtime evidence."""
import io,json,copy
from uuid import uuid4
from PIL import Image
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.models import Evidence,Task,Invocation,CloudCall
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.instance_compiler import signature_value
from tests.test_instance_workspace import prepared,configuration,complete,object_row
from tests.test_workflow_abilities import enable,finish_job
from tests.test_local_specialists import mocked_runtime,ready


def setup(client,source='task_image_ids'):
    row=ready(client);r=client.post('/api/specialists/installations/'+row['id']+'/register',json={'version':row['version']});assert r.status_code==200,r.text
    c=configuration();s=c['flows']['资料核验']['steps'][0]
    s.update(agent='多模感知智能体',role='',name='本地候选检测',execution={'capability_id':r.json()['capability_id'],'automation_action':'observe',
        'arguments':{},'bindings':{'evidence_ids':{'source':source}}})
    c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'}
    f=c['flows']['资料核验'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c);return r,c,check


def raw():
    b=io.BytesIO();Image.new('RGB',(20,20),'red').save(b,format='PNG');return b.getvalue()


def upload(client,r,data=None,media='image/png'):
    response=client.post('/api/workspace/instances/'+r['instance_id']+'/input-images',files={'file':('公开图.png',raw() if data is None else data,media)})
    return response


def create(client,r,obj,ids):
    return client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json={'release_id':r['id'],'business_id':r['available_business_ids'][0],
        'entity_ids':[obj['id']],'image_ids':ids,'title':'核对原图候选','request_id':str(uuid4())})


def test_selected_image_node_keeps_original_and_reaches_human_review(client,mocked_runtime):
    r,c,check=setup(client);assert r['available_business_ids'],check;enable(client,r);obj=object_row(client,r)
    image=upload(client,r).json();assert image['name']=='公开图.png'
    path='/api/workspace/instances/'+r['instance_id']+'/input-images'
    assert client.get(path).json()==[image]
    assert client.get(path+'/'+image['id']+'/content').content==raw()
    response=create(client,r,obj,[image['id']]);assert response.status_code==201,response.text;t=finish_job(client,response.json())
    assert t['status']=='workflow_waiting',t
    receipt=t['workflow_runtime']['receipts'][0]
    assert receipt['input_snapshot']['evidence_ids']==[image['id']] and receipt['candidate_only']
    assert receipt['output']['detection']['image_sha256']==image['sha256'] and receipt['image_snapshots']==[image]
    assert client.get('/api/workspace/tasks/'+t['id']+'/images/'+image['id']+'/content').content==raw()
    assert complete(client,t).status_code==200
    assert client.get('/api/instances/'+r['instance_id']+'/objects').json()[0]['version']==1
    with Session(engine()) as db:assert not db.scalars(select(CloudCall)).all();assert db.scalar(select(Invocation)).status=='succeeded'


def test_local_model_refuses_all_materials_as_image_source(client,mocked_runtime):
    r,c,check=setup(client,'original_evidence_ids')
    assert not r['available_business_ids'] and any('明确使用' in e for b in check['businesses'] for e in b['issues'])


def test_upload_invalid_and_wrong_instance_originals_are_not_accepted(client,mocked_runtime):
    r,c,_=setup(client);enable(client,r);obj=object_row(client,r)
    assert upload(client,r,b'plain document','text/plain').status_code==415
    assert upload(client,r,b'broken image').status_code==415
    image=upload(client,r).json()
    assert create(client,r,obj,[image['id'],image['id']]).status_code==422
    c['instanceProfile']['name']='第二个图片核查实例'
    flow=c['flows']['资料核验'];flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    other,_,_=prepared(client,c);assert other['instance_id']!=r['instance_id'];enable(client,other);other_obj=object_row(client,other)
    assert client.get('/api/workspace/instances/'+other['instance_id']+'/input-images/'+image['id']+'/content').status_code==409
    assert create(client,other,other_obj,[image['id']]).status_code==409


def test_image_snapshot_tamper_stops_queued_invocation_and_keeps_task(client,mocked_runtime):
    r,_,_=setup(client);enable(client,r);obj=object_row(client,r);image=upload(client,r).json()
    t=create(client,r,obj,[image['id']]).json()
    with Session(engine()) as db:
        ev=db.get(Evidence,image['id']);ev.data={**ev.data,'name':'changed.png'};db.commit()
    t=finish_job(client,t);assert t['status']=='workflow_blocked' and t['workflow_runtime']['receipts']==[]
    assert '摘要不一致' in t['workflow_runtime']['automatic']['error']
    with Session(engine()) as db:assert not db.scalars(select(Invocation)).all()


def test_missing_image_is_saved_blocked_and_wrong_task_image_is_hidden(client,mocked_runtime):
    r,_,_=setup(client);enable(client,r);obj=object_row(client,r);image=upload(client,r).json()
    t=create(client,r,obj,[]).json();assert t['status']=='workflow_blocked'
    assert client.get('/api/workspace/tasks/'+t['id']+'/images/'+image['id']+'/content').status_code==404
    with Session(engine()) as db:assert not db.scalars(select(Invocation)).all()


def test_model_output_for_other_image_cannot_be_accepted(client,mocked_runtime,monkeypatch):
    r,_,_=setup(client);enable(client,r);obj=object_row(client,r);image=upload(client,r).json();t=create(client,r,obj,[image['id']]).json()
    import generic_agents.gateway as gateway
    original=gateway.invoke
    def different(*args,**kwargs):
        call=original(*args,**kwargs);call.data={**call.data,'output':{**call.data['output'],'detection':{**call.data['output']['detection'],'image_sha256':'0'*64}}};args[0].commit();return call
    monkeypatch.setattr(gateway,'invoke',different);t=finish_job(client,t)
    assert t['status']=='workflow_blocked' and t['workflow_runtime']['receipts']==[]
    assert '结果与任务原图不一致' in t['workflow_runtime']['automatic']['error']


def test_other_workspace_upload_not_readable(client,account,mocked_runtime):
    r,_,_=setup(client);enable(client,r);image=upload(client,r).json()
    from generic_agents.models import Workspace,User,Membership
    from generic_agents.security import Principal,password_hash
    from generic_agents.evidence import save_evidence,evidence_bytes
    with Session(engine()) as db:
        other=Workspace(name='另一个工作区');db.add(other);db.flush();other_id=other.id;db.commit()
    assert client.get('/api/workspace/instances/'+r['instance_id']+'/input-images',headers={'X-Workspace-ID':other_id}).status_code==403
    with Session(engine()) as db:
        # A genuine foreign owner/upload preserves the workspace-bound payload
        # encoding. Moving an existing Evidence row would correctly fail its
        # database seal before testing any application authorization.
        owner=User(username='foreign_image_'+uuid4().hex[:12],password_hash=password_hash(uuid4().hex))
        db.add(owner);db.flush()
        db.add(Membership(user_id=owner.id,workspace_id=other_id,role='admin'));db.flush()
        foreign_actor=Principal(owner.id,other_id,'admin','foreign-image-test')
        foreign=save_evidence(db,foreign_actor,raw(),'image/png')
        foreign_id=foreign.id
        verified,content=evidence_bytes(db,foreign_actor,foreign_id)
        assert verified.workspace_id==other_id and content==raw()
        db.commit()
    original='/api/workspace/instances/'+r['instance_id']+'/input-images/'
    assert client.get(original+image['id']+'/content').content==raw()
    assert client.get(original+foreign_id+'/content').status_code==404
    obj=object_row(client,r);assert create(client,r,obj,[foreign_id]).status_code==404
    with Session(engine()) as db:
        assert db.get(Evidence,image['id']).workspace_id==account[2]
        assert db.get(Evidence,foreign_id).workspace_id==other_id
