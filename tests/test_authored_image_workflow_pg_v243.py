"""Normal registry/config/adoption/compiler API against Root's disposable PG.

No paid report qualification, fake graph adoption, Task, ACK or authority is
inserted. These tests establish publication preparation, not model accuracy.
"""
import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session
from generic_agents import models as m,authored_image_tools_v243 as tools
from generic_agents import authored_workflow_store_v208 as old
from generic_agents.authored_workflow_models_v208 import AuthoredWorkflowAdoption as Adoption,AuthoredWorkflowCommand as Command
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from tests.test_instance_workspace import configuration,save,OBJECTS
from tests.test_authored_workflow_pg_v208 import published_packet,preview_body as document_preview

BASE='/api/setup/authored-image-workflow-graphs'


def good(response,status=200):
    assert response.status_code==status,response.text[:700]
    return response.json()


def exact(value):return ('  '+json.dumps(value,ensure_ascii=False)+'\n').encode()


def counts():
    with Session(engine()) as db:return {model.__tablename__:db.scalar(select(func.count()).select_from(model))
        for model in (Adoption,Command,m.SetupRevision,m.Capability,m.Job,m.Invocation,m.CloudCall,m.Audit)}


def prepare_tools(client):
    body={'schema':'authored-image-tools-prepare-command-v243','request_id':str(uuid4()),'confirm_local':True}
    raw=exact(body);result=good(client.post(BASE+'/tools/prepare',content=raw))
    return body,raw,result


def configured(client,*,triggers=None):
    value=configuration();flow=value['flows']['资料核验']
    flow['steps']=[copy.deepcopy(flow['steps'][0]) for _ in range(3)]
    for index,step in enumerate(flow['steps']):
        step.update(id='stage_'+str(index),name=['初核原件','复核技术结果','确认处理意见'][index],
            role=['初核人','复核人','确认人'][index],nextId='stage_'+str(index+1) if index<2 else 'end',condition='',elseId='end')
    policy=value['automation']['policies']['资料核验']
    policy.update(triggers=triggers or ['人工发起','新观测到达','定时运行'],time='08:00')
    policy['rules']['data']={'mode':'自动运行'}
    flow['confirmedSignature']=json.dumps(signature_value(value,flow),ensure_ascii=False)
    save(client,value,review_automation=True)
    return good(client.get('/api/setup/draft'))


def body(client):
    context=good(client.get(BASE+'/context',params={'business_name':'资料核验'}))
    assert context['can_preview'],context['reason']
    actor=good(client.get('/api/me'))['user_id']
    caps={item['operation']:item for item in context['capabilities']}
    return {'schema':'authored-image-workflow-preview-command-v243',
        'configuration_revision':context['configuration_revision'],'configuration_hash':context['configuration_hash'],
        'business_name':'资料核验','tool_bindings':{operation:{'capability_id':caps[operation]['id'],
            'capability_hash':caps[operation]['hash'],'actor_id':actor} for operation in ('quality','dedup','report')},
        'role_bindings':{role:actor for role in context['human_roles']},
        'node_actions':{'quality':'data','dedup':'data','report':'data'},'external_node_ids':[],'prior_origin':context['prior_origin']}


def adopt(client):
    prepare_tools(client);configured(client);value=body(client)
    preview=good(client.post(BASE+'/preview',content=exact(value)))
    command={**value,'schema':'authored-image-workflow-adopt-command-v243','request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    raw=exact(command);result=good(client.post(BASE+'/adopt',content=raw),201)
    return value,preview,command,raw,result


def test_local_tools_are_real_published_registry_entries_and_sync_terminal_job_with_original_recovery(client):
    before=counts();body,raw,result=prepare_tools(client)
    assert [row['operation'] for row in result['capabilities']]==['quality','dedup','report']
    assert result['can_execute'] is False and result['cloud_authorized'] is False
    with Session(engine()) as db:
        job=db.scalar(select(m.Job).where(m.Job.kind==tools.KIND))
        assert job.status=='succeeded' and job.attempts==0 and job.data['exact_body'].encode()==raw
        for pin in result['capabilities']:
            cap=db.get(m.Capability,pin['id']);tools.validate(cap,pin['operation'])
            assert cap.content_hash==pin['hash'] and cap.status=='published'
    saved=counts();assert saved[m.Capability.__tablename__]==before[m.Capability.__tablename__]+3
    assert saved[m.CloudCall.__tablename__]==before[m.CloudCall.__tablename__]
    assert good(client.post(BASE+'/tools/prepare',content=raw))==result and counts()==saved
    recovered=good(client.get(BASE+'/tools/requests/'+body['request_id'],params={'expected_hash':hashlib.sha256(raw).hexdigest()}))
    assert recovered['found'] and recovered['result']==result and recovered['body']==body
    new_body,new_raw,new=prepare_tools(client)
    assert new['capabilities']==result['capabilities']
    assert counts()[m.Capability.__tablename__]==saved[m.Capability.__tablename__]


def test_new_image_preview_adopt_original_recovery_and_normal_compile_have_no_paid_or_task_qualification(client):
    prepare_tools(client);current=configured(client);value=body(client);before=counts()
    preview=good(client.post(BASE+'/preview',content=exact(value)))
    assert counts()==before
    assert len([row for row in preview['projection']['steps'] if row['kind']=='human'])==3
    assert preview['binding']['schema']=='authored-image-workflow-binding-v243'
    command={**value,'schema':'authored-image-workflow-adopt-command-v243','request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    raw=exact(command);result=good(client.post(BASE+'/adopt',content=raw),201)
    assert good(client.post(BASE+'/adopt',content=raw),201)==result
    recovered=good(client.get(BASE+'/requests/'+command['request_id'],params={'expected_hash':hashlib.sha256(raw).hexdigest()}))
    assert recovered['body']==command and recovered['result']==result
    view=good(client.get(BASE+'/adoptions/'+result['id']))
    assert view['schema']=='authored-image-workflow-adoption-view-v243'
    assert client.get(old.PREFIX+'/adoptions/'+result['id']).status_code==409
    assert client.get(old.PREFIX+'/requests/'+command['request_id'],params={'expected_hash':hashlib.sha256(raw).hexdigest()}).status_code==409
    updated=good(client.get('/api/setup/draft'))
    assert updated['configuration']['flows']==current['configuration']['flows']
    assert updated['configuration'][old.CONFIG_KEY]['资料核验']==result['origin']
    compilation={'revision':updated['revision'],'objects':OBJECTS,'role_bindings':{key:actor['user_id'] for key,actor in view['role_bindings'].items()}}
    checked=good(client.post('/api/instances/check',json=compilation))
    assert not checked['general_issues'],checked['general_issues']
    release=good(client.post('/api/instances',json={**compilation,'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True}),201)
    assert release['available_business_ids']
    assert release['snapshot']['business_automation_admission_v1']['businesses'][0]['provider_ids']==['received_camera_frame_v1']
    assert counts()[m.CloudCall.__tablename__]==before[m.CloudCall.__tablename__]


def test_corrupt_preexisting_named_tool_is_not_overwritten_and_whole_preparation_rolls_back(client):
    manifest=tools.manifests()['dedup'].model_dump(mode='json')
    draft=good(client.post('/api/capabilities',json=manifest),201);before=counts()
    command={'schema':'authored-image-tools-prepare-command-v243','request_id':str(uuid4()),'confirm_local':True}
    assert client.post(BASE+'/tools/prepare',content=exact(command)).status_code==409
    assert counts()==before
    with Session(engine()) as db:assert db.get(m.Capability,draft['id']).status=='draft'


def test_adoption_nonce_cannot_change_body_and_saved_history_survives_current_capability_retirement(client):
    value,preview,command,raw,result=adopt(client);before=counts()
    changed={**command,'consent':False}
    assert client.post(BASE+'/adopt',content=exact(changed)).status_code==422
    changed={**command,'preview_hash':'f'*64}
    assert client.post(BASE+'/adopt',content=exact(changed)).status_code==409
    with Session(engine()) as db:
        cap=db.get(m.Capability,value['tool_bindings']['quality']['capability_id']);cap.status='retired';db.commit()
    recovered=good(client.get(BASE+'/requests/'+command['request_id'],params={'expected_hash':hashlib.sha256(raw).hexdigest()}))
    assert recovered['result']==result and recovered['found']
    assert good(client.post(BASE+'/adopt',content=raw),201)==result
    assert counts()==before


def test_missing_independent_adoption_audit_is_conflict_not_found_false_or_rebuilt_success(client):
    _,_,command,raw,result=adopt(client)
    with Session(engine()) as db:
        event=db.scalar(select(m.Audit).where(m.Audit.action==old.ADOPTED_AUDIT,m.Audit.object_id==result['id']))
        db.delete(event);db.commit()
    assert client.get(BASE+'/requests/'+command['request_id'],params={'expected_hash':hashlib.sha256(raw).hexdigest()}).status_code==409
    assert client.get(BASE+'/adoptions/'+result['id']).status_code==409


@pytest.mark.parametrize('field',['namespace','name','release_version'])
def test_registry_row_metadata_drift_blocks_new_preview_without_rewriting_historical_tools_receipt(client,field):
    command,raw,result=prepare_tools(client);configured(client);value=body(client)
    selected=value['tool_bindings']['quality']['capability_id']
    with Session(engine()) as db:
        cap=db.get(m.Capability,selected)
        original_manifest=copy.deepcopy(cap.data)
        setattr(cap,field,getattr(cap,field)+'_fault')
        db.commit()
    before=counts()
    assert client.post(BASE+'/preview',content=exact(value)).status_code==409
    context=good(client.get(BASE+'/context',params={'business_name':'资料核验'}))
    assert not context['can_preview'] and not any(item['id']==selected for item in context['capabilities'])
    # Original preparation remains a fixed historical receipt. Reading or
    # replaying that nonce neither repairs registry metadata nor republishes.
    recovered=good(client.get(BASE+'/tools/requests/'+command['request_id'],params={'expected_hash':hashlib.sha256(raw).hexdigest()}))
    assert recovered['found'] and recovered['result']==result
    assert good(client.post(BASE+'/tools/prepare',content=raw))==result
    assert counts()==before
    with Session(engine()) as db:
        cap=db.get(m.Capability,selected)
        assert cap.data==original_manifest and getattr(cap,field).endswith('_fault')


def test_concurrent_tools_preparation_uses_one_registry_set_and_original_nonce(client):
    command={'schema':'authored-image-tools-prepare-command-v243','request_id':str(uuid4()),'confirm_local':True};raw=exact(command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(client.post,BASE+'/tools/prepare',content=raw) for _ in range(2)]
        values=[good(future.result(timeout=20)) for future in futures]
    assert values[0]==values[1]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.kind==tools.KIND))==1
        assert len({entry['id'] for entry in values[0]['capabilities']})==3


def test_selected_tool_actor_is_locked_before_configuration_and_revocation_wins_without_adoption(client):
    from generic_agents.security import password_hash
    prepare_tools(client);configured(client)
    me=good(client.get('/api/me'))
    with Session(engine()) as db:
        user=m.User(username='image_actor_'+uuid4().hex,password_hash=password_hash(uuid4().hex))
        db.add(user);db.flush();actor_id=user.id
        db.add(m.Membership(user_id=user.id,workspace_id=me['workspace_id'],role='operator'));db.commit()
    value=body(client);value['tool_bindings']['quality']['actor_id']=actor_id
    preview=good(client.post(BASE+'/preview',content=exact(value)))
    command={**value,'schema':'authored-image-workflow-adopt-command-v243','request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    before=counts()
    with Session(engine()) as barrier,ThreadPoolExecutor(max_workers=1) as pool:
        user=barrier.get(m.User,actor_id,with_for_update=True)
        future=pool.submit(client.post,BASE+'/adopt',content=exact(command))
        try:
            deadline=time.monotonic()+5
            while True:
                with engine().connect() as conn:
                    waiting=conn.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE '%users%'"))
                if waiting:break
                assert not future.done(),'adoption did not lock the selected real tool actor'
                assert time.monotonic()<deadline
                time.sleep(.02)
            with Session(engine()) as probe:
                probe.execute(text("SET LOCAL lock_timeout='1s'"))
                probe.scalar(select(m.SetupDraft).where(m.SetupDraft.workspace_id==me['workspace_id']).with_for_update(nowait=True))
            # This is a real User revocation, not a stale Principal projection.
            user.active=False;barrier.commit()
        finally:barrier.rollback()
        response=future.result(timeout=10)
    assert response.status_code in {403,409},response.text[:300]
    assert counts()==before
