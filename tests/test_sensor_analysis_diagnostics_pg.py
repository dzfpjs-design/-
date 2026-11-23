"""Pure failure classification and disposable-PG analysis recovery."""
import copy
import base64
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import sensor_native_analysis as svc
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from tests.test_sensor_native_analysis_pg import setup_analysis
from tests.test_sensor_unlimited_worker_restart_pg import device, reject_non_loopback_network

SECRET='PRIVATE-SYNTHETIC-CONTENT-DO-NOT-SHOW'
SCHEMA={'type':'object','required':['summary'],'properties':{'summary':{'type':'string'}}}

def envelope(content, finish='stop'):
    return json.dumps({'choices':[{'finish_reason':finish,'message':{'content':content}}]})

@pytest.mark.parametrize('raw,status,expected',[
    (SECRET,401,'provider_http_error'),
    (SECRET,200,'response_not_json'),
    ('{}',200,'response_envelope_invalid'),
    (envelope(SECRET,'length'),200,'response_truncated'),
    (envelope(SECRET,'content_filter'),200,'response_incomplete'),
    (envelope(SECRET),200,'content_not_json'),
    (envelope(json.dumps({'not_summary':SECRET})),200,'content_schema_invalid'),
    (envelope(json.dumps({'summary':SECRET})),200,'unknown'),
    (SECRET,None,'unknown'),
])
def test_diagnostics_only_returns_static_codes(raw,status,expected):
    receipt={'http_status':status,'secret':SECRET};before=copy.deepcopy(receipt)
    assert transport.diagnose_saved_response(raw,SCHEMA,receipt)==expected
    assert receipt==before
    assert SECRET not in json.dumps(svc.FAILURE_REASONS,ensure_ascii=False)

@pytest.mark.parametrize('status',[300,400,401,403,429,500,599])
def test_provider_http_status_uses_one_safe_explanation(status):
    assert transport.diagnose_saved_response(SECRET,SCHEMA,{'http_status':status})=='provider_http_error'

@pytest.mark.parametrize('mode,expected',[
    ('truncated','response_truncated'),('invalid_json','content_not_json'),
    ('invalid_schema','content_schema_invalid'),('http_error','provider_http_error'),
])
def test_saved_original_is_diagnosed_on_read_without_resend_or_mutation(client,account,device,monkeypatch,mode,expected):
    monkeypatch.setattr('generic_agents.modbus_readonly_transport.read_registers',lambda *args:[25])
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    p={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
    r=client.post(base+'/analyses/preview',json=p);assert r.status_code==200,r.text
    preview=r.json();body={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    calls=[]
    class Fake:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            calls.append(1)
            if mode=='http_error':return httpx.Response(401,text=SECRET)
            content=SECRET if mode in ('truncated','invalid_json') else __import__('json').dumps({'unexpected':SECRET})
            first={'finish_reason':'length' if mode=='truncated' else 'stop','message':{'content':content}}
            return httpx.Response(200,json={'choices':[first],'usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50}})
    monkeypatch.setattr('generic_agents.cloud_business_reasoning_v197.httpx.Client',Fake)
    r=client.post(base+'/analyses/'+preview['preview_id']+'/send',json=body);assert r.status_code==200,r.text
    identity=r.json()['analysis']['id'];assert svc.tick(account[2])==1 and calls==[1]
    with Session(engine()) as db:
        row=db.get(Job,identity);before=(row.version,digest(row.data))
        original_calls=[(c.id,c.version,digest(c.data)) for c in db.scalars(select(CloudCall))]
    for _ in range(2):
        result=client.get(base+'/analyses/requests/'+body['request_id']).json()
        assert result['state']=='response_invalid' and result['analysis']['failure_reason']['code']==expected
        assert SECRET not in json.dumps(result,ensure_ascii=False)
        assert client.get(base+'/analyses/history').json()['analyses'][0]['failure_reason']['code']==expected
    engine().dispose();assert client.get(base+'/analyses/requests/'+body['request_id']).json()['analysis']['failure_reason']['code']==expected
    assert svc.tick(account[2])==0 and calls==[1]
    with Session(engine()) as db:
        row=db.get(Job,identity);assert (row.version,digest(row.data))==before
        assert [(c.id,c.version,digest(c.data)) for c in db.scalars(select(CloudCall))]==original_calls


def test_unverifiable_old_capture_never_exposes_or_reinterprets_untrusted_text():
    raw=envelope(SECRET).encode();receipt={'http_status':200,'secret':SECRET}
    data={'call_id':'id','capture':{'call_id':'id','raw_base64':base64.b64encode(raw).decode(),
        'raw_sha256':hashlib.sha256(raw).hexdigest(),'receipt':receipt,'receipt_hash':digest(receipt)},
        'snapshot':{'output_schema':SCHEMA}}
    data['snapshot_hash']=digest(data['snapshot'])
    assert svc.failure_reason(data)['code']=='content_not_json'
    assert svc.failure_reason({'capture':None})['code']=='unknown'
    for changed in ('raw_sha256','receipt_hash','raw_base64','call_id'):
        damaged=copy.deepcopy(data);damaged['capture'][changed]=SECRET
        assert svc.failure_reason(damaged)['code']=='unknown'
    damaged=copy.deepcopy(data);damaged['snapshot']['output_schema']={}
    assert svc.failure_reason(damaged)['code']=='unknown'
    damaged=copy.deepcopy(data);del damaged['snapshot_hash']
    assert svc.failure_reason(damaged)['code']=='unknown'
    oversized=copy.deepcopy(data);oversized['capture']['raw_base64']='A'*(4*((transport.MAX_RESPONSE_BYTES+2)//3)+4)
    assert svc.failure_reason(oversized)['code']=='unknown'
    assert SECRET not in json.dumps(svc.failure_reason(data),ensure_ascii=False)
