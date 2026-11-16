"""Explicit non-live provider transport; real normal retry, ledger, audit and apply receipts."""
import copy,json
from uuid import uuid4
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import SetupAnalysis,CloudCall,Audit
from tests.test_setup_understanding import prepared,start,process,proposal
from tests.test_setup_understanding_v3 import get,process_latest

def wire(monkeypatch,*,agent='业务驱动智能体',bad_citation=False):
    sent=[];base=httpx.Client
    def handler(request):
        body=json.loads(request.content);sent.append(body)
        value=proposal(json.loads(body['messages'][1]['content']))
        value.update(objects=[],relationships=[])
        value['businesses'][0]['steps'][0].update(agent=agent,role='')
        if bad_citation:value['businesses'][0]['citations'][0]['quote']='不属于授权材料的句子'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)}}],
            'usage':{'total_tokens':1234}},headers={'x-request-id':'synthetic-alias-v261'})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client',lambda **kw:base(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    monkeypatch.setattr(settings(),'dashscope_api_key',SecretStr('synthetic-nonlive-alias'))
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',10)
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',1000000)
    return sent

def frozen(row_id):
    with Session(engine()) as db:
        row=db.get(SetupAnalysis,row_id);call=db.scalar(select(CloudCall))
        return copy.deepcopy(row.data['raw_response']),(call.id,call.status,call.reserved_tokens,call.charged_tokens,copy.deepcopy(call.data))

def test_v5_original_schema_failure_recovers_locally_with_visible_trace_and_normal_apply_receipt(client,monkeypatch):
    body,_=prepared(client);body.update(contract_version=5,request_id=str(uuid4()))
    sent=wire(monkeypatch);row,_=start(client,body);process(row);failed=get(client,row)
    assert failed['status']=='failed' and failed['recovery']['mode']=='local_only'
    raw,ledger=frozen(row['id']);assert raw['value']['businesses'][0]['steps'][0]['agent']=='业务驱动智能体'
    assert ledger[1]=='failed'
    retry=client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':failed['version']});assert retry.status_code==200
    process_latest(retry.json());reviewed=get(client,row)
    assert reviewed['status']=='review',reviewed['error']
    trace=[{'policy':'setup-v5-agent-aliases-v261','path':'/businesses/0/steps/0/agent','original':'业务驱动智能体','canonical':'业务智能体'}]
    assert reviewed['agent_alias_normalizations']==trace
    assert reviewed['result']['businesses'][0]['steps'][0]['agent']=='业务智能体'
    assert frozen(row['id'])==(raw,ledger) and len(sent)==1
    with Session(engine()) as db:
        audits=list(db.scalars(select(Audit).where(Audit.object_id==row['id'],Audit.action=='setup.understanding.normalize_agent_aliases')))
        assert len(audits)==1 and audits[0].data['normalizations']==trace
        basis=audits[0].data['basis'];assert basis['raw_response_hash']==raw['hash']
        assert basis['original_value_hash']==digest(raw['value']) and basis['canonical_value_hash']==digest(reviewed['result'])
        assert basis['policy']=='setup-v5-agent-aliases-v261' and len(basis['policy_hash'])==64
    request_id=str(uuid4());names=[reviewed['result']['businesses'][0]['name']]
    review=client.post('/api/setup/understanding/'+row['id']+'/review',json={'version':reviewed['version'],'names':names,'object_names':[],'accept_domain':True});assert review.status_code==200
    current=get(client,row);draft=client.get('/api/setup/draft').json()
    apply_body={'version':current['version'],'revision':draft['revision'],'names':names,'object_names':[],'accept_domain':True,'request_id':request_id,'result_hash':current['result_hash'],'configuration_hash':digest(draft['configuration'])}
    applied=client.post('/api/setup/understanding/'+row['id']+'/apply',json=apply_body);assert applied.status_code==200,applied.text
    receipt=client.get('/api/setup/understanding/'+row['id']+'/apply-receipts/'+request_id)
    assert receipt.status_code==200 and receipt.json()['receipt']==applied.json()['receipt']
    assert receipt.json()['current_matches'] is True
    assert json.loads(receipt.json()['configuration_json'])==applied.json()['configuration']
    cold=get(client,row);assert cold['agent_alias_normalizations']==trace and cold['result']==reviewed['result']
    assert frozen(row['id'])==(raw,ledger) and len(sent)==1
    undone=client.post('/api/setup/understanding/'+row['id']+'/undo',json={'version':cold['version'],'revision':applied.json()['revision'],'names':names,'object_names':[]})
    assert undone.status_code==200
    reused=client.post('/api/setup/understanding/'+row['id']+'/reuse',json={'version':undone.json()['analysis']['version'],'revision':undone.json()['revision']})
    assert reused.status_code==201 and reused.json()['agent_alias_normalizations']==trace
    reused_row=reused.json()
    reapplied=client.post('/api/setup/understanding/'+reused_row['id']+'/apply',json={'version':reused_row['version'],'revision':undone.json()['revision'],'names':names,'object_names':[],'accept_domain':True})
    assert reapplied.status_code==200,reapplied.text
    assert frozen(row['id'])==(raw,ledger) and len(sent)==1
    # The child must not hide its inherited alias by losing both local fields.
    with Session(engine()) as db:
        saved=db.get(SetupAnalysis,reused_row['id']);data=copy.deepcopy(saved.data)
        data.pop('agent_alias_normalizations');data.pop('agent_alias_normalization_basis');saved.data=data;db.commit()
    blocked=client.post('/api/setup/understanding/'+reused_row['id']+'/apply',json={'version':reapplied.json()['analysis']['version'],'revision':reapplied.json()['revision'],'names':names,'object_names':[]})
    assert blocked.status_code==409 and '独立依据不一致' in blocked.json()['detail']
    stable_draft=client.get('/api/setup/draft').json()
    for changed_parent in (None,str(uuid4())):
        with Session(engine()) as db:
            saved=db.get(SetupAnalysis,reused_row['id']);data=copy.deepcopy(saved.data)
            if changed_parent is None:data.pop('reused_from')
            else:data['reused_from']=changed_parent
            saved.data=data;db.commit()
        blocked=client.post('/api/setup/understanding/'+reused_row['id']+'/apply',json={'version':reapplied.json()['analysis']['version'],'revision':reapplied.json()['revision'],'names':names,'object_names':[]})
        assert blocked.status_code==409 and '复用依据不完整' in blocked.json()['detail']
        assert client.get('/api/setup/draft').json()==stable_draft
    assert frozen(row['id'])==(raw,ledger) and len(sent)==1

@pytest.mark.parametrize('agent,bad_citation',[('业务智能助手',False),(' 业务驱动智能体',False),('业务驱动智能体',True)])
def test_local_alias_never_repairs_unknown_roles_or_invalid_evidence(client,monkeypatch,agent,bad_citation):
    body,_=prepared(client);body.update(contract_version=5,request_id=str(uuid4()))
    sent=wire(monkeypatch,agent=agent,bad_citation=bad_citation)
    row,_=start(client,body);process(row);first=get(client,row);assert first['status']=='failed'
    original=frozen(row['id'])
    retry=client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':first['version']});assert retry.status_code==200
    process_latest(retry.json());last=get(client,row)
    assert last['status']=='failed' and last['result'] is None
    assert not last.get('agent_alias_normalizations') and frozen(row['id'])==original and len(sent)==1

@pytest.mark.parametrize('damage',['trace','basis','audit','duplicate_audit'])
def test_partial_or_duplicate_normalization_proof_blocks_apply_without_reconstruction(client,monkeypatch,damage):
    body,_=prepared(client);body.update(contract_version=5,request_id=str(uuid4()))
    sent=wire(monkeypatch);row,_=start(client,body);process(row);failed=get(client,row)
    retry=client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':failed['version']});assert retry.status_code==200
    process_latest(retry.json());reviewed=get(client,row);assert reviewed['status']=='review'
    original=frozen(row['id']);draft=client.get('/api/setup/draft').json()
    # Deliberate disposable-database corruption, not a production repair path.
    with Session(engine()) as db:
        saved=db.get(SetupAnalysis,row['id'])
        proof=db.scalar(select(Audit).where(Audit.object_id==row['id'],Audit.action=='setup.understanding.normalize_agent_aliases'))
        if damage in {'trace','basis'}:
            data=copy.deepcopy(saved.data);data.pop('agent_alias_normalizations' if damage=='trace' else 'agent_alias_normalization_basis');saved.data=data
        elif damage=='audit':db.delete(proof)
        else:db.add(Audit(workspace_id=proof.workspace_id,actor_id=proof.actor_id,action=proof.action,object_id=proof.object_id,data=copy.deepcopy(proof.data)))
        db.commit()
    rejected=client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':reviewed['version'],'revision':draft['revision'],'names':[reviewed['result']['businesses'][0]['name']],'object_names':[]})
    assert rejected.status_code==409 and '独立依据不一致' in rejected.json()['detail']
    assert client.get('/api/setup/draft').json()==draft and frozen(row['id'])==original and len(sent)==1
