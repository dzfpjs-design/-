import copy
import json
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import SetupAnalysis, SetupMaterial, Job, now
from generic_agents.db import engine
from generic_agents.worker import process_once
from generic_agents.leases import LeaseLost
import generic_agents.setup_understanding as understanding
from tests.test_setup import upload, config
from tests.test_vertical_slice import publish


def prepared(client, text='收到设备新数据，检查异常，由值班人员复核后归档。', initial=None):
    m=upload(client,text.encode());assert process_once(m['job_id'],deliver=False)
    m=client.get('/api/setup/materials/'+m['id']).json()
    value=initial or config([m]);value['materials']=[m]
    r=client.get('/api/setup/draft').json()
    saved=client.put('/api/setup/draft',json={'revision':r['revision'],'configuration':value});assert saved.status_code==200
    cap=publish(client,json.loads(Path('examples/qwen.json').read_text()))
    body={'revision':saved.json()['revision'],'model_id':cap['id'],'material_ids':[m['id']],'instruction':''}
    return body,m


def start(client,body):
    preview=client.post('/api/setup/understanding/preview',json=body);assert preview.status_code==200,preview.text
    response=client.post('/api/setup/understanding',json={**body,'preview_hash':preview.json()['preview_hash'],'consent':True})
    assert response.status_code==202,response.text
    return response.json(),preview.json()


def proposal(payload,original='',name='异常复核'):
    source=payload['source_blocks'][0]
    cite={'material_id':source['material_id'],'block_id':source['block_id'],'quote':source['text'][:80]}
    return {'domain':'设备管理','objects':['设备'],'questions':[], 'businesses':[{'operation':'upsert',
        'original_name':original,'name':name,'trigger':'收到数据','output':'已核查记录','citations':[cite],'inference_reason':'',
        'steps':[{'key':'check','name':'核查异常','purpose':'核对来源','agent':'人工确认','role':'值班人员','output':'核查结论',
        'next_key':'end','condition':'','else_key':'end','citations':[cite],'inference_reason':''}]}]}


def fake(monkeypatch,mutate=None):
    calls=[]
    def cloud(cap,messages,schema,p):
        payload=json.loads(messages[0]['content']);calls.append(payload);value=proposal(payload)
        if mutate:mutate(value,payload)
        return value,{'provider':'test-only','model':'deterministic-test','usage':{'total_tokens':10}}
    monkeypatch.setattr(understanding,'cloud_json',cloud);return calls


def process(row):
    with Session(engine()) as db:job=db.scalar(select(Job).where(Job.data['analysis_id'].astext==row['id']))
    assert process_once(job.id,deliver=False)


def test_consent_hash_and_unparsed_inputs_never_queue(client):
    body,m=prepared(client)
    assert client.get('/api/setup/understanding/models').json()[0]['provider']=='qwen'
    preview=client.post('/api/setup/understanding/preview',json=body).json()
    assert client.post('/api/setup/understanding',json={**body,'preview_hash':preview['preview_hash'],'consent':False}).status_code==422
    assert client.post('/api/setup/understanding',json={**body,'preview_hash':'wrong','consent':True}).status_code==409
    assert not client.get('/api/setup/understanding').json()
    pending=upload(client,b'unparsed','pending.txt')
    assert client.post('/api/setup/understanding/preview',json={**body,'material_ids':[pending['id']]}).status_code==409


def test_preview_filters_patterns_without_destroying_structure(client):
    initial=config();initial['instanceProfile']['goal']='联系人13812345678，a@example.com，password: sss'
    body,m=prepared(client,text='手机号13912345678 api_key: sk-abcdefgh123456',initial=initial)
    preview=client.post('/api/setup/understanding/preview',json=body);assert preview.status_code==200
    encoded=json.dumps(preview.json(),ensure_ascii=False)
    assert all(s not in encoded for s in ['13812345678','13912345678','a@example.com','sk-abcdefgh123456'])
    assert '已移除' in encoded


def test_generation_review_application_undo_preserve_human_work(client,monkeypatch):
    initial=config();initial['businesses']=['已有人工业务'];initial['flows']={'已有人工业务':{'trigger':'人定','steps':[]}}
    body,m=prepared(client,initial=initial);before=client.get('/api/setup/draft').json();calls=fake(monkeypatch)
    row,_=start(client,body);process(row)
    reviewed=client.get('/api/setup/understanding/'+row['id']).json();assert reviewed['status']=='review' and len(calls)==1
    assert client.get('/api/setup/draft').json()==before
    response=client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':reviewed['version'],
        'revision':body['revision'],'names':['异常复核']});assert response.status_code==200,response.text
    cfg=response.json()['configuration'];assert cfg['flows']['已有人工业务']==initial['flows']['已有人工业务']
    assert cfg['businesses']==['已有人工业务','异常复核'];assert cfg['flows']['异常复核']['confirmedSignature'] is None
    assert cfg['flows']['异常复核']['steps'][0]['sourceId']==m['id']
    applied=response.json()
    undo=client.post('/api/setup/understanding/'+row['id']+'/undo',json={'version':applied['analysis']['version'],
        'revision':applied['revision'],'names':['异常复核']});assert undo.status_code==200
    assert undo.json()['configuration']==before['configuration']
    assert len(client.get('/api/setup/draft/history').json())>=3


def test_invalid_quote_rejects_whole_result_and_leaves_configuration(client,monkeypatch):
    body,m=prepared(client);before=client.get('/api/setup/draft').json()
    fake(monkeypatch,lambda v,p:v['businesses'][0]['steps'][0]['citations'][0].update(quote='不存在的原文'))
    row,_=start(client,body);process(row);record=client.get('/api/setup/understanding/'+row['id']).json()
    assert record['status']=='failed' and record['receipts']
    assert client.get('/api/setup/draft').json()==before
    assert client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':record['version'],
        'revision':body['revision'],'names':['异常复核']}).status_code==409


def test_changed_config_during_model_call_blocks_late_application(client,monkeypatch):
    body,m=prepared(client)
    def change(v,p):
        cfg=client.get('/api/setup/draft').json();cfg['configuration']['instanceProfile']['goal']='人工的新目标'
        assert client.put('/api/setup/draft',json={'revision':cfg['revision'],'configuration':cfg['configuration']}).status_code==200
    fake(monkeypatch,change);row,_=start(client,body);process(row)
    record=client.get('/api/setup/understanding/'+row['id']).json();assert record['status']=='failed' and record['receipts']
    assert '配置已修改' in record['error']
    assert client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':record['version']}).status_code==409


def test_cancel_during_network_keeps_receipt_without_late_result(client,monkeypatch):
    body,m=prepared(client);holder={}
    def cancel(v,p):
        current=client.get('/api/setup/understanding/'+holder['id']).json()
        assert client.post('/api/setup/understanding/'+holder['id']+'/cancel',json={'version':current['version']}).status_code==200
    fake(monkeypatch,cancel);row,_=start(client,body);holder.update(row);process(row)
    after=client.get('/api/setup/understanding/'+row['id']).json()
    assert after['status']=='cancelled' and after['receipts'] and not after['result']


def test_crash_after_paid_response_resumes_without_another_cloud_call(client,monkeypatch):
    body,m=prepared(client);calls=fake(monkeypatch);row,_=start(client,body)
    original=understanding.checkpoint
    def crash(db):
        original(db)
        record=db.get(SetupAnalysis,row['id'])
        if record.data.get('raw_response') and not record.data['completed']:raise LeaseLost('simulated crash after durable response')
    monkeypatch.setattr(understanding,'checkpoint',crash);process(row)
    monkeypatch.setattr(understanding,'checkpoint',original)
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['analysis_id'].astext==row['id']))
        job.lease_until=now()-timedelta(seconds=10);db.commit()
    process(row);assert len(calls)==1
    assert client.get('/api/setup/understanding/'+row['id']).json()['status']=='review'


def test_inflight_first_batch_without_response_blocks_automatic_replay(client,monkeypatch):
    body,m=prepared(client);calls=fake(monkeypatch);row,_=start(client,body)
    with Session(engine()) as db:
        analysis=db.get(SetupAnalysis,row['id']);analysis.data={**analysis.data,'inflight':0};db.commit()
    process(row);record=client.get('/api/setup/understanding/'+row['id']).json()
    assert record['status']=='failed' and not calls and '可能已计费' in record['error']
    retry=client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':record['version']});assert retry.status_code==200
    with Session(engine()) as db:job=db.get(Job,retry.json().get('job_id')) if retry.json().get('job_id') else db.scalar(select(Job).where(Job.data['analysis_id'].astext==row['id'],Job.status=='queued'))
    assert process_once(job.id,deliver=False);assert len(calls)==1


def test_material_change_after_preview_rejects_consent_and_retry(client,monkeypatch):
    body,m=prepared(client);preview=client.post('/api/setup/understanding/preview',json=body).json()
    with Session(engine()) as db:material=db.get(SetupMaterial,m['id']);material.version+=1;db.commit()
    assert client.post('/api/setup/understanding',json={**body,'preview_hash':preview['preview_hash'],'consent':True}).status_code==409
    row,_=start(client,body)
    with Session(engine()) as db:material=db.get(SetupMaterial,m['id']);material.archived=True;db.commit()
    calls=fake(monkeypatch);process(row)
    assert not calls and client.get('/api/setup/understanding/'+row['id']).json()['status']=='failed'


def test_whole_document_batches_include_tail_and_checkpointed_previous_result(client,monkeypatch):
    text='\n'.join('第'+str(i)+'段：'+('设备运行正常。'*400) for i in range(4))+'\n结尾必须核对。'
    body,m=prepared(client,text=text);calls=fake(monkeypatch);row,preview=start(client,body)
    assert len(preview['chunks'])>1;process(row)
    assert len(calls)==len(preview['chunks'])
    assert any('结尾必须核对' in b['text'] for call in calls for b in call['source_blocks'])
    assert all(call['previous_proposal'] for call in calls[1:])


def test_cross_workspace_analysis_isolation(client,account):
    body,m=prepared(client);row,_=start(client,body)
    from generic_agents.models import Workspace,Membership
    with Session(engine()) as db:
        user_id=db.scalar(select(Membership.user_id).where(Membership.workspace_id==account[2]))
        w=Workspace(name='另一工作区');db.add(w);db.flush();db.add(Membership(workspace_id=w.id,user_id=user_id,role='admin'));db.commit();wid=w.id
    client.headers['X-Workspace-ID']=wid
    assert client.get('/api/setup/understanding').json()==[]
    assert client.get('/api/setup/understanding/'+row['id']).status_code==404


def test_undo_will_not_overwrite_subsequent_human_changes(client,monkeypatch):
    body,m=prepared(client);fake(monkeypatch);row,_=start(client,body);process(row)
    reviewed=client.get('/api/setup/understanding/'+row['id']).json()
    applied=client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':reviewed['version'],
        'revision':body['revision'],'names':['异常复核']}).json()
    cfg=client.get('/api/setup/draft').json();cfg['configuration']['instanceProfile']['goal']='应用后人工修改'
    saved=client.put('/api/setup/draft',json={'revision':cfg['revision'],'configuration':cfg['configuration']}).json()
    assert client.post('/api/setup/understanding/'+row['id']+'/undo',json={'version':applied['analysis']['version'],
        'revision':saved['revision'],'names':['异常复核']}).status_code==409


def test_noop_save_does_not_supersede_a_pending_proposal(client,monkeypatch):
    body,m=prepared(client);fake(monkeypatch);row,_=start(client,body)
    draft=client.get('/api/setup/draft').json()
    saved=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':draft['configuration']})
    assert saved.status_code==200 and saved.json()['revision']==draft['revision']
    process(row);assert client.get('/api/setup/understanding/'+row['id']).json()['status']=='review'


def test_hidden_manual_fields_are_not_sent_to_cloud(client):
    initial=config();initial['businesses']=['已有业务'];initial['flows']={'已有业务':{'private_note':'不可外发私有备注',
        'steps':[{'name':'核查','purpose':'验证','output':'结论','private_location':'不可外发隐藏位置'}]}}
    body,m=prepared(client,initial=initial)
    preview=client.post('/api/setup/understanding/preview',json=body).json()
    assert '不可外发' not in json.dumps(preview['context'],ensure_ascii=False)


def test_review_choices_are_durable_without_changing_configuration(client,monkeypatch):
    body,m=prepared(client);fake(monkeypatch);row,_=start(client,body);process(row)
    record=client.get('/api/setup/understanding/'+row['id']).json();before=client.get('/api/setup/draft').json()
    saved=client.post('/api/setup/understanding/'+row['id']+'/review',json={'version':record['version'],
        'names':['异常复核'],'accept_domain':True});assert saved.status_code==200
    reopened=client.get('/api/setup/understanding/'+row['id']).json()
    assert reopened['review_selection']['names']==['异常复核'] and reopened['review_selection']['accept_domain']
    assert client.get('/api/setup/draft').json()==before
    assert client.post('/api/setup/understanding/'+row['id']+'/review',json={'version':record['version'],
        'names':[]}).status_code==409


def test_long_paragraph_fragments_preserve_every_character_and_prior_citations(client,monkeypatch):
    text='设备运行正常。'*3000+'结尾检查。'
    body,m=prepared(client,text=text);calls=[]
    def cloud(cap,messages,schema,p):
        payload=json.loads(messages[0]['content']);calls.append(payload)
        return payload['previous_proposal'] or proposal(payload),{'provider':'test-only','model':'fixture'}
    monkeypatch.setattr(understanding,'cloud_json',cloud)
    row,preview=start(client,body)
    assert ''.join(b['text'] for chunk in preview['chunks'] for b in chunk)==text
    process(row)
    record=client.get('/api/setup/understanding/'+row['id']).json()
    assert record['status']=='review' and len(calls)>1


def test_rename_remove_preserve_recovery_and_resource_business_links():
    value=config();value.update(businesses=['原业务','保留业务'],flows={'原业务':{'steps':[{'name':'人工步骤'}]},'保留业务':{'trigger':'保留'}},
        resources=[{'id':'physical','businesses':['原业务','保留业务']}])
    b={'operation':'upsert','original_name':'原业务','name':'新业务','trigger':'人工','output':'结论',
        'citations':[],'inference_reason':'人工配置','steps':[]}
    result={'businesses':[b],'domain':'领域'}
    renamed=understanding.apply_configuration(value,result,['新业务'])
    assert renamed['resources'][0]['businesses']==['新业务','保留业务']
    assert renamed['flowRecovery']['原业务']==value['flows']['原业务']
    assert renamed['flows']['保留业务']==value['flows']['保留业务']
    removed=understanding.apply_configuration(renamed,{'businesses':[{'operation':'remove','original_name':'新业务','name':'新业务'}]},['新业务'])
    assert removed['businesses']==['保留业务'] and removed['resources'][0]['businesses']==['保留业务']
    assert value['businesses']==['原业务','保留业务']
