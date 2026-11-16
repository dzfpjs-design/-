"""A stale formal source blocks execution, never existing human work or cancel."""
import copy
from uuid import uuid4
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SetupMaterial,Invocation
from tests.test_full_semantic_graph_journey_v114 import graph_seed,graph_command,human
from tests.test_report_full_semantic_source_review_postgres import v7_scene,reading_scene,local_runtime
from tests.test_report_full_semantic_region_source_postgres import full_source_scene
from tests.test_full_semantic_instance_journey_v113 import configured,generate,create_object
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply
from tests.test_workflow_abilities import tool


def test_current_source_loss_keeps_saved_graph_draft_history_and_cancel_usable(graph_seed):
    seed=graph_seed;client=seed['client'];view,member,compilation=configured(seed)
    name=seed['record']['design']['result']['businesses'][0]['name']
    config=copy.deepcopy(view['configuration']);config['automation']['policies'][name]['rules']['data']={'mode':'自动运行'}
    save(client,config,review_automation=True);view=reply(client.get('/api/setup/draft'))
    cap=tool(client,agent='business');command=graph_command(seed,member,view['configuration'],cap)
    path='/api/setup/full-semantic-graphs/'+seed['record']['id']
    preview=reply(client.post(path+'/preview',json=command))
    accepted=reply(client.post(path+'/adopt',json={**command,'schema':'report-full-semantic-graph-command-v3',
        'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True,'semantic_mode':'workspace_workflow_design'}))
    current=reply(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'],role_bindings={'职责甲':member['id'],'职责乙':member['id']})
    checked=reply(client.post('/api/instances/check',json=compilation));release=generate(client,compilation,checked)
    instance=next(i for i in reply(client.get('/api/instances')) if i['id']==release['instance_id'])
    reply(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
    obj,_=create_object(client,release,release['snapshot']['object_types'][0])
    task=reply(client.post('/api/workspace/instances/'+instance['id']+'/tasks',json={'release_id':release['id'],
        'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],
        'title':'来源失效仍能保存人稿和取消','request_id':str(uuid4())}),status=201)
    task=reply(client.get('/api/workspace/tasks/'+task['id']))
    task,_=human(client,task,'a',draft=True)
    # Failure injection changes only the disposable synthetic source, never the
    # issued graph/receipt, package, task state, worker or execution authority.
    with Session(engine()) as db:
        db.get(SetupMaterial,seed['scene']['copy']['id']).archived=True;db.commit()
    historical_objects=reply(client.get('/api/instances/'+instance['id']+'/objects'))
    historical_object=next(item for item in historical_objects if item['id']==obj['id'])
    assert historical_object['attributes']==obj['attributes']
    proof=reply(client.get('/api/setup/full-semantic-region-mappings/'+seed['record']['id']+'/proofs/'+seed['proof_id']))
    assert proof['current_source_verified'] is False
    assert client.post('/api/instances/check',json=compilation).status_code==409
    assert any(t['id']==task['id'] for t in reply(client.get('/api/workspace/instances/'+instance['id']+'/tasks')))
    reopened=reply(client.get('/api/workspace/tasks/'+task['id']))
    assert reopened['graph_pending_humans'][0]['draft']=={'ok':True}
    pending=reopened['graph_pending_humans'][0]
    saved={'request_id':str(uuid4()),'activation_id':pending['activation_id'],'version':pending['version'],
        'cancel_epoch':reopened['graph_runtime']['cancel_epoch'],'draft':{'ok':False}}
    reply(client.put('/api/workspace/tasks/'+task['id']+'/graph/draft',json=saved))
    reopened=reply(client.get('/api/workspace/tasks/'+task['id']))
    assert reopened['graph_runtime']['activations'][pending['activation_id']]['draft']=={'ok':False}
    assert reopened['graph_runtime']['activations'][pending['activation_id']]['version']==pending['version']+1
    assert reply(client.get('/api/workspace/tasks/'+task['id']+'/graph/requests/'+saved['request_id']))['found']
    pending=reopened['graph_pending_humans'][0]
    completion={'request_id':str(uuid4()),'activation_id':pending['activation_id'],'version':pending['version'],
        'cancel_epoch':reopened['graph_runtime']['cancel_epoch'],'output':{'ok':True}}
    assert client.post('/api/workspace/tasks/'+task['id']+'/graph/complete',json=completion).status_code==409
    cancelled=reply(client.post('/api/workspace/tasks/'+task['id']+'/graph/cancel',json={'request_id':str(uuid4()),
        'version':reopened['graph_runtime']['version'],'cancel_epoch':reopened['graph_runtime']['cancel_epoch'],
        'reason':'来源不可用，保留既有说明并停止后续处理'}))
    assert cancelled['status']=='workflow_cancelled'
    closed=reply(client.get('/api/workspace/tasks/'+task['id']))
    assert closed['graph_runtime']['activations'][pending['activation_id']]['draft']=={'ok':False}
    with Session(engine()) as db:assert db.query(Invocation).filter(Invocation.capability_id==cap['id']).count()==0
