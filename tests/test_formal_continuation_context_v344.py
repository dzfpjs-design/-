"""A formal continuation must list only the original ordered human steps."""
import copy
import pytest
from fastapi import HTTPException
from generic_agents.formal_continuation_context_v344 import _steps

def binding():
    flow=[{'id':'one','name':'核对材料','role':'经办人','output':'核对意见'},
          {'id':'two','name':'审批结果','role':'负责人','output':'审批意见'}]
    mapping=[{'source_step_id':s['id'],'node_id':'human_'+s['id'],'name':s['name'],'role':s['role'],'output':s['output']} for s in flow]
    nodes=[{'id':'reasoning'},{'id':'review_decision','kind':'human','source_step_id':None,'role':'业务审核','actor_id':'reviewer'},
           *[{'id':m['node_id'],'kind':'human','source_step_id':m['source_step_id'],'role':m['role'],'actor_id':'actor_'+str(i)} for i,m in enumerate(mapping)],{'id':'end'}]
    route=['reasoning','review_decision',*[m['node_id'] for m in mapping],'end']
    edges=[{'from':a,'to':b,'on':'success','branch_id':None,'loop_id':None} for a,b in zip(route,route[1:])]
    return {'flow':{'steps':flow},'step_mapping':mapping,'graph':{'nodes':nodes,'edges':edges},
            'role_bindings':{'经办人':{'user_id':'actor_0'},'负责人':{'user_id':'actor_1'}}}

def test_original_steps_order_and_actors_are_displayed():
    result=_steps(binding())
    assert [(x['name'],x['role'],x['actor_id']) for x in result['steps']]==[('核对材料','经办人','actor_0'),('审批结果','负责人','actor_1')]
    assert result['review']=={'role':'业务审核','actor_id':'reviewer'}

@pytest.mark.parametrize('mutation',[
    lambda b:b['step_mapping'].reverse(),
    lambda b:b['step_mapping'][0].update(name='伪造'),
    lambda b:b['graph']['nodes'][2].update(actor_id='其他人'),
    lambda b:b['graph']['edges'].pop(),
    lambda b:b['graph']['edges'].append(copy.deepcopy(b['graph']['edges'][0])),
    lambda b:b['graph']['nodes'][2].update(kind='ability'),
    lambda b:b['graph']['nodes'][1].update(source_step_id='伪造审核'),
])
def test_changed_steps_or_graph_cannot_be_used(mutation):
    value=binding();mutation(value)
    with pytest.raises(HTTPException) as error:_steps(value)
    assert error.value.status_code==409

def test_http_scopes_task_and_never_writes(client,account,monkeypatch):
    from sqlalchemy import func,select
    from sqlalchemy.orm import Session
    from generic_agents import models as m
    from generic_agents.db import engine
    from generic_agents import formal_continuation_api_v344 as api
    user=client.get('/api/me').json()['user_id'];workspace=account[2]
    with Session(engine()) as db:
        other=m.Workspace(name='其他工作区');db.add(other);db.flush()
        def make(w):
            cap=m.Capability(workspace_id=w,namespace='test',name='package',release_version='1',kind='domain_package',content_hash='a'*64,data={});db.add(cap);db.flush()
            task=m.Task(workspace_id=w,owner_id=user,package_id=cap.id,title='测试',status='workflow_waiting',data={});db.add(task);db.flush();return task.id
        own,foreign=make(workspace),make(other.id);db.commit()
        before=db.scalar(select(func.count()).select_from(m.Audit))
    calls=[]
    def read(db,p,task,advice):
        calls.append((p.workspace_id,task.id,advice));return {'schema':'checked-read','available':False,'task_completed':False}
    monkeypatch.setattr(api,'read_context',read)
    base='/api/workspace/tasks/{}/business-advice/preparations/advice/formal-continuation/context'
    response=client.get(base.format(own));assert response.status_code==200 and response.json()['task_completed'] is False
    assert client.get(base.format(foreign)).status_code==404 and calls==[(workspace,own,'advice')]
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(m.Audit))==before


def test_inactive_removed_or_viewer_actor_is_not_shown_as_available():
    from types import SimpleNamespace as NS
    from generic_agents.formal_continuation_context_v344 import _person
    entry={'actor_id':'person','role':'审核员'}
    users={'person':NS(username='测试账号',active=True)}
    assert _person(entry,users,{'person':NS(role='operator')})['available']
    assert not _person(entry,users,{'person':NS(role='viewer')})['available']
    assert not _person(entry,users,{})['available']
    assert not _person(entry,{'person':NS(username='测试账号',active=False)},{'person':NS(role='admin')})['available']
