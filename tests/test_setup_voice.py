"""Real local ASR to durable setup text, CAS and immutable human/source lineage."""
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import AudioRun,Memory,SetupRevision
from generic_agents.worker import process_once
from tests.test_audio_installation import queued,direct_upload
from tests.test_setup import config

def prepare(client):
    install=queued(client);assert process_once(install['job_id'],deliver=False)
    r=direct_upload(client);assert r.status_code==201,r.text;audio=r.json();assert process_once(audio['job_id'],deliver=False)
    audio=client.get('/api/audio/'+audio['id']).json();assert audio['status']=='succeeded'
    draft=client.get('/api/setup/draft').json();value=config();value['instanceProfile']['goal']='已有目标';value['businesses']=['自定义业务'];value['setupNotes']={'自定义业务':'原修改说明'}
    draft=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':value}).json()
    return audio,draft

def body(audio,draft):return {'revision':draft['revision'],'audio_version':audio['version'],'audio_run_id':audio['id'],'request_id':str(uuid4()),'text':'已核对：开放时间早上9点至下午5点。','target':'goal','mode':'append'}

def test_real_audio_apply_atomic_idempotent_and_history(client):
    audio,draft=prepare(client);b=body(audio,draft)
    r=client.post('/api/setup/voice/apply',json=b);assert r.status_code==200,r.text;saved=r.json()
    assert saved['revision']==draft['revision']+1 and saved['configuration']['instanceProfile']['goal']=='已有目标\n'+b['text']
    ref=saved['configuration']['voiceInputs'][0];assert ref['deployment']=='local' and ref['cloud_calls']==0 and ref['input_sha256']==audio['input_sha256'] and ref['result_hash']==audio['result_hash']
    assert client.post('/api/setup/voice/apply',json=b).json()==saved
    assert client.post('/api/setup/voice/apply',json={**b,'text':'替换文字'}).status_code==409
    reread=client.get('/api/audio/'+audio['id']).json();assert reread['review']['text']==b['text'] and reread['result']==audio['result']
    with Session(engine()) as db:
        memory=db.get(Memory,reread['review']['candidate_receipt_id']);assert memory.status=='candidate' and not memory.data['entity_state_updated']
        assert len(list(db.scalars(select(SetupRevision).where(SetupRevision.draft_id==saved['id']))))==3
    assert client.get('/api/setup/draft').json()==saved
    damaged={**saved['configuration'],'voiceInputs':[{**ref,'text':'伪造核对文字'}]}
    assert client.put('/api/setup/draft',json={'revision':saved['revision'],'configuration':damaged}).status_code==409
    assert client.get('/api/setup/draft').json()==saved

def test_voice_cas_and_dynamic_business_no_partial_review(client):
    audio,draft=prepare(client);b=body(audio,draft)
    assert client.post('/api/setup/voice/apply',json={**b,'revision':draft['revision']+10}).status_code==409
    assert client.post('/api/setup/voice/apply',json={**b,'audio_version':audio['version']+1}).status_code==409
    assert client.post('/api/setup/voice/apply',json={**b,'text':'   '}).status_code==422
    assert client.post('/api/setup/voice/apply',json={**b,'target':'business_instruction','business':'不存在'}).status_code==422
    assert client.get('/api/audio/'+audio['id']).json()['review'] is None
    r=client.post('/api/setup/voice/apply',json={**b,'target':'business_instruction','business':'自定义业务','mode':'replace'});assert r.status_code==200,r.text
    assert r.json()['configuration']['setupNotes']['自定义业务']==b['text']
    assert r.json()['configuration']['instanceProfile']['goal']=='已有目标' and not r.json()['configuration'].get('confirmedBusinesses')

def test_server_voice_review_draft_cas_and_restore(client):
    audio,draft=prepare(client);path='/api/audio/'+audio['id'];b={'version':audio['version'],'text':'待核对：领域术语'}
    r=client.put(path+'/review-draft',json=b);assert r.status_code==200,r.text;saved=r.json()
    assert saved['review'] is None and saved['review_draft']['text']==b['text']
    assert client.put(path+'/review-draft',json=b).status_code==409
    assert client.get(path).json()==saved
    applied=client.post('/api/setup/voice/apply',json=body(saved,draft));assert applied.status_code==200,applied.text
    assert client.get(path).json()['review_draft'] is None

def test_voice_access_scope_readonly_and_receipt_lookup(client,account):
    from generic_agents.models import User,Workspace,Membership
    audio,draft=prepare(client);b=body(audio,draft)
    path='/api/setup/voice/requests/'+b['request_id'];params={'audio_run_id':audio['id']}
    assert client.get(path,params=params).status_code==404
    with Session(engine()) as db:
        u=db.scalar(select(User).where(User.username==account[0]));m=db.scalar(select(Membership).where(Membership.user_id==u.id));m.role='viewer';db.commit();user_id=u.id
    assert client.post('/api/setup/voice/apply',json=b).status_code==403
    assert client.put('/api/audio/'+audio['id']+'/review-draft',json={'version':audio['version'],'text':'不应保存'}).status_code==403
    with Session(engine()) as db:
        m=db.scalar(select(Membership).where(Membership.user_id==user_id));m.role='admin';other=Workspace(name='别的工作区');db.add(other);db.flush();db.add(Membership(user_id=user_id,workspace_id=other.id,role='admin'));db.commit();wid=other.id
    client.headers['X-Workspace-ID']=wid
    assert client.post('/api/setup/voice/apply',json=b).status_code==404
    assert client.get(path,params=params).status_code==404
    client.headers['X-Workspace-ID']=account[2]
    r=client.post('/api/setup/voice/apply',json=b);assert r.status_code==200,r.text
    assert client.get(path,params=params).json()['draft']==r.json()

def test_voice_configuration_validation_rolls_back_human_review(client):
    audio,draft=prepare(client)
    from generic_agents.models import SetupDraft
    with Session(engine()) as db:
        d=db.get(SetupDraft,draft['id']);d.data={**d.data,'materials':[{'id':str(uuid4()),'hash':'fake'}]};db.commit()
    r=client.post('/api/setup/voice/apply',json=body(audio,draft));assert r.status_code==404,r.text
    reread=client.get('/api/audio/'+audio['id']).json();assert reread['review'] is None and not reread.get('setup_applications')
    with Session(engine()) as db:assert not list(db.scalars(select(Memory)))
