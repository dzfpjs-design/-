"""Manual draft rename: real HTTP and disposable PostgreSQL, no cloud/device calls."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import AudioRun, Evidence, SetupRevision
from tests.test_instance_workspace import configuration, prepared, enable, object_row, task_row


def original_config():
    value=configuration()
    value['automationInstructions']={'资料核验':'保留已经保存的自动处理说明'}
    value['businessAliases']={}
    value['resources']=[{'businesses':['资料核验'],'name':'资料来源'}]
    value['gaps']=[{'business':'资料核验','description':'待补资料'}]
    return value


def renamed(saved, target='业务资料核验'):
    value=copy.deepcopy(saved['configuration'])
    value['businesses']=[target]
    value['businessAliases'][target]='资料核验'
    value['resources'][0]['businesses']=[target]
    value['gaps'][0]['business']=target
    value['automationInstructions'][target]=value['automationInstructions']['资料核验']
    value['_businessRenameIntent']={'from':'资料核验','to':target}
    return value


def baseline(client):
    initial=client.get('/api/setup/draft').json()
    result=client.put('/api/setup/draft',json={'revision':initial['revision'],'configuration':original_config()})
    assert result.status_code==200,result.text
    return result.json()


def test_rename_persists_reopens_history_and_invalidates_automation_confirmation(client):
    old=baseline(client)
    response=client.put('/api/setup/draft',json={'revision':old['revision'],'configuration':renamed(old)})
    assert response.status_code==200,response.text
    saved=response.json();value=saved['configuration']
    assert '_businessRenameIntent' not in value
    assert value['automationInstructions']=={'资料核验':'保留已经保存的自动处理说明','业务资料核验':'保留已经保存的自动处理说明'}
    assert value['resources'][0]['businesses']==['业务资料核验']
    assert value['gaps'][0]['business']=='业务资料核验'
    assert value['businessAliases']['业务资料核验']=='资料核验'
    assert value['flows']['资料核验']==old['configuration']['flows']['资料核验']
    assert next(x for x in saved['automation_review']['businesses'] if x['business_name']=='业务资料核验')['status']=='needs_review'
    token=client.post('/api/tokens').json()['token']
    with TestClient(app) as reopened:
        reopened.headers['Authorization']='Bearer '+token
        again=reopened.get('/api/setup/draft')
        assert again.status_code==200 and again.json()['configuration']==value
        assert any(h['revision']==old['revision'] and h['configuration']['automationInstructions']=={'资料核验':'保留已经保存的自动处理说明'} for h in reopened.get('/api/setup/draft/history').json())
    with Session(engine()) as db:
        previous=db.scalar(select(SetupRevision).where(SetupRevision.draft_id==saved['id'],SetupRevision.version==old['revision']))
        assert previous.data['automationInstructions']=={'资料核验':'保留已经保存的自动处理说明'}


def test_tampering_missing_intent_and_old_name_reuse_do_not_change_saved_draft(client):
    old=baseline(client);candidate=renamed(old)
    for change in ('missing_intent','modified_note','mixed_delete','voice_change'):
        value=copy.deepcopy(candidate)
        if change=='missing_intent':value.pop('_businessRenameIntent')
        if change=='modified_note':value['automationInstructions']['业务资料核验']='不同说明'
        if change=='mixed_delete':value['businesses']=[]
        if change=='voice_change':value['voiceInputs']=[{'business':'资料核验'}]
        assert client.put('/api/setup/draft',json={'revision':old['revision'],'configuration':value}).status_code in (409,422)
        assert client.get('/api/setup/draft').json()['revision']==old['revision']
    saved=client.put('/api/setup/draft',json={'revision':old['revision'],'configuration':candidate}).json()
    reuse=copy.deepcopy(saved['configuration']);reuse['businesses'].append('资料核验')
    assert client.put('/api/setup/draft',json={'revision':saved['revision'],'configuration':reuse}).status_code==409


def test_two_rename_saves_with_same_revision_never_overwrite_each_other(client):
    old=baseline(client);barrier=threading.Barrier(2)
    def save(target):
        barrier.wait(timeout=5)
        return client.put('/api/setup/draft',json={'revision':old['revision'],'configuration':renamed(old,target)})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(save,'业务一');b=pool.submit(save,'业务二');out=[a.result(timeout=20),b.result(timeout=20)]
    assert sorted(x.status_code for x in out)==[200,409]
    saved=client.get('/api/setup/draft').json()['configuration'];name=saved['businesses'][0]
    assert name in {'业务一','业务二'} and saved['automationInstructions'][name]=='保留已经保存的自动处理说明'


def test_existing_published_release_and_task_remain_unchanged(client):
    config=configuration();config['automationInstructions']={'资料核验':'原有自动化说明'};config['businessAliases']={}
    release,_,_=prepared(client,config);enable(client,release)
    task,_=task_row(client,release,object_row(client,release))
    old_task=client.get('/api/workspace/tasks/'+task['id']).json()
    old_releases=client.get('/api/instances/'+release['instance_id']+'/releases').json()
    old=client.get('/api/setup/draft').json();value=copy.deepcopy(old['configuration'])
    value['businesses']=['业务资料核验'];value['businessAliases']['业务资料核验']='资料核验'
    value['automationInstructions']['业务资料核验']='原有自动化说明'
    value['_businessRenameIntent']={'from':'资料核验','to':'业务资料核验'}
    saved=client.put('/api/setup/draft',json={'revision':old['revision'],'configuration':value})
    assert saved.status_code==200,saved.text
    assert client.get('/api/workspace/tasks/'+task['id']).json()==old_task
    assert client.get('/api/instances/'+release['instance_id']+'/releases').json()==old_releases


def test_real_voice_application_receipt_survives_rename_and_reopen(client,account,monkeypatch):
    # Synthetic completed audio; the voice application and its durable receipt
    # go through the real HTTP/save paths without running an ASR model.
    from generic_agents import local_audio
    monkeypatch.setattr(local_audio,'checked',lambda *args:None)
    workspace_id=account[2]
    with Session(engine()) as db:
        ev=Evidence(workspace_id=workspace_id,sha256='a'*64,media_type='audio/wav',size_bytes=4,storage_key='synthetic.wav',data={})
        db.add(ev);db.flush()
        audio=AudioRun(workspace_id=workspace_id,evidence_id=ev.id,request_id=str(uuid4()),status='succeeded',
            data={'name':'合成语音','input_sha256':'a'*64,'result_hash':'b'*64,'recipe_hash':'c'*64,'review':None,'review_history':[]})
        db.add(audio);db.commit();audio_id=audio.id;audio_version=audio.version
    initial=client.get('/api/setup/draft').json();config=configuration();config['businessAliases']={}
    saved=client.put('/api/setup/draft',json={'revision':initial['revision'],'configuration':config}).json()
    voice=client.post('/api/setup/voice/apply',json={'revision':saved['revision'],'audio_version':audio_version,
        'audio_run_id':audio_id,'request_id':str(uuid4()),'text':'需要人工确认','target':'automation_instruction',
        'business':'资料核验','mode':'append'})
    assert voice.status_code==200,voice.text
    old=voice.json();reference=copy.deepcopy(old['configuration']['voiceInputs'])
    value=copy.deepcopy(old['configuration']);value['businesses']=['业务资料核验'];value['businessAliases']['业务资料核验']='资料核验'
    value['automationInstructions']['业务资料核验']=value['automationInstructions']['资料核验']
    value['_businessRenameIntent']={'from':'资料核验','to':'业务资料核验'}
    result=client.put('/api/setup/draft',json={'revision':old['revision'],'configuration':value})
    assert result.status_code==200,result.text
    assert result.json()['configuration']['voiceInputs']==reference
    assert client.get('/api/setup/draft').json()['configuration']['voiceInputs']==reference
    with Session(engine()) as db:
        run=db.get(AudioRun,audio_id)
        assert run.data['setup_applications'][reference[0]['request_id']]['reference']==reference[0]
