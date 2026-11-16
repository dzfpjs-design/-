"""Root-owned, real API/PG + frontend contract bridge; no browser/quality claim."""
import copy
import json
import os
from pathlib import Path
import subprocess
import time,re
from uuid import uuid4

from generic_agents.worker import process_once
from tests.test_full_semantic_graph_journey_v114 import graph_seed, graph_command, human, v7_scene, reading_scene, local_runtime, full_source_scene
from tests.test_full_semantic_instance_journey_v113 import configured, generate, create_object
from tests.test_workflow_abilities import tool
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_graph_revision import activate, task


def test_real_editor_canonical_transport_revision_execution_and_checkpoint(graph_seed, account):
    seed=graph_seed;client=seed['client'];timings=[];original_request=client.request
    def measured(method,url,*args,**kwargs):
        start=time.perf_counter();response=original_request(method,url,*args,**kwargs)
        timings.append({'method':method,'path':re.sub(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}','{id}',str(url)),
            'status':response.status_code,'seconds':round(time.perf_counter()-start,4)})
        return response
    client.request=measured
    view,member,compilation=configured(seed)
    name=seed['record']['design']['result']['businesses'][0]['name']
    configuration=copy.deepcopy(view['configuration'])
    configuration['automation']['policies'][name]['rules']['data']={'mode':'自动运行'}
    save(client,configuration,review_automation=True)
    view=reply(client.get('/api/setup/draft'));cap=tool(client,agent='business')
    body=graph_command(seed,member,view['configuration'],cap)
    scope={'actor_id':member['id'],'workspace_id':seed['scene']['p'].workspace_id,'handoff_id':seed['record']['id']}
    path='/api/setup/full-semantic-graph-wire/'+scope['handoff_id']
    members=reply(client.get('/api/instances/members'));abilities=reply(client.get('/api/workspace/ability-options'))
    bridge=Path(os.environ['GA_V115_GRAPH_BRIDGE']);node=os.environ['GA_V115_NODE']
    def build(command=None,threshold=None,preview=None):
        payload={'scope':scope,'context':reply(client.get(path+'/editor-context')),
            'business_name':name,'members':members,'abilities':abilities}
        if command is not None:payload['command']=command
        if threshold is not None:payload['threshold']=threshold
        if preview is not None:payload['preview']=preview
        result=subprocess.run([node,str(bridge)],input=json.dumps(payload,ensure_ascii=False),text=True,capture_output=True,timeout=60)
        assert result.returncode==0,result.stderr[-3500:]
        return json.loads(result.stdout)
    command=build(body,-0.5)['command']
    assert command['graph']['predicates']['p_after']['right']['value']==-0.5
    preview=reply(client.post(path+'/preview',json=command));pin=build(body,-0.5,preview)['pin']
    adopt={**command,'schema':'report-full-semantic-graph-command-v3','request_id':str(uuid4()),
        'preview_hash':pin['preview_hash'],'consent':True,'semantic_mode':'workspace_workflow_design'}
    command_hash=reply(client.post(path+'/command-hash',json={'kind':'root','command':adopt}))['value']['command_hash']
    accepted=reply(client.post(path+'/adopt',json=adopt))['value']
    assert accepted['receipt']['command_hash']==command_hash
    original=copy.deepcopy(accepted)
    compilation.update(revision=accepted['configuration']['revision'],role_bindings=command['role_bindings'])
    checked=reply(client.post('/api/instances/check',json=compilation));release=generate(client,compilation,checked);activate(client,release)
    obj,_=create_object(client,release,release['snapshot']['object_types'][0]);old=task(client,release,obj)
    # Editing the actual current adopted design must derive the real receipt parent.
    revised=build(threshold=0.5)['command']
    assert revised['parent']=={'id':accepted['receipt']['id'],'receipt_hash':accepted['receipt']['receipt_hash']}
    revision_preview=reply(client.post(path+'/revisions/preview',json=revised));revision_pin=build(threshold=0.5,preview=revision_preview)['pin']
    revision_body={**revised,'schema':'report-full-semantic-graph-revision-command-v1','request_id':str(uuid4()),
        'preview_hash':revision_pin['preview_hash'],'consent':True,'semantic_mode':'workspace_workflow_design'}
    revised_reply=reply(client.post(path+'/revisions/adopt',json=revision_body))['value']
    assert revised_reply['effect_scope']=='future_releases_only'
    assert reply(client.get(path+'/requests/'+adopt['request_id']))['value']['receipt']==original['receipt']
    current=reply(client.get('/api/setup/draft'));compilation['revision']=current['revision']
    checked=reply(client.post('/api/instances/check',json=compilation));new_release=generate(client,compilation,checked,instance_id=release['instance_id']);activate(client,new_release)
    new=task(client,new_release,obj)
    for existing in [old,new]:
        job=next(iter(existing['graph_jobs'].values()))['job_id'];assert process_once(job,deliver=False)
        existing=reply(client.get('/api/workspace/tasks/'+existing['id']))
        existing,_=human(client,existing,'a',draft=True);existing,_=human(client,existing,'a')
        if existing['id']==old['id']:
            existing,_=human(client,existing,'c')
        else:
            assert not any(a['node_id']=='c' for a in existing['graph_runtime']['activations'].values())
            assert any(a['node_id']=='d' and a['output']=={'truth':'false'} for a in existing['graph_runtime']['activations'].values())
        assert existing['completed']
    timing_path=os.environ.get('GA_V115_EDITOR_TIMINGS')
    if timing_path:
        output=Path(timing_path);output.touch(mode=0o600,exist_ok=True);output.chmod(0o600)
        output.write_text(json.dumps({'schema':'synthetic-graph-editor-http-timings-v115','requests':timings},ensure_ascii=False));output.chmod(0o600)
    checkpoint_path=os.environ.get('GA_V115_EDITOR_CHECKPOINT')
    if checkpoint_path:
        username,password,workspace=account
        output=Path(checkpoint_path);output.touch(mode=0o600,exist_ok=True);output.chmod(0o600)
        output.write_text(json.dumps({'account':{'username':username,'password':password,'workspace_id':workspace},
            'handoff_id':scope['handoff_id'],'root_request_id':adopt['request_id'],'revision_request_id':revision_body['request_id'],
            'root_receipt':original['receipt'],'revision_receipt':revised_reply['receipt'],
            'release_id':release['id'],'new_release_id':new_release['id'],'instance_id':release['instance_id'],
            'old_task_id':old['id'],'new_task_id':new['id'],'object_id':obj['id']},ensure_ascii=False))
        output.chmod(0o600)
