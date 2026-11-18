"""Owned disposable PG + synthetic HTTP; no user data or cloud requests."""
import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import gateway
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job, SetupReportDraft
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_origin import owned_disposable_database, launch
from tests.test_report_full_second_batch_preview import scene_with_candidate
from tests.test_vertical_slice import publish


@pytest.fixture(autouse=True)
def owned_capture_roots(monkeypatch,tmp_path):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    base=tmp_path.resolve()
    monkeypatch.setattr(settings(),'storage_dir',base)
    monkeypatch.setattr(delegated,'root',lambda:base/'report-evidence-delegated-http-v1')
    monkeypatch.setattr(pipeline,'_journal_root',lambda *args:base/'report-evidence-journal')


def start_delegated(scene,monkeypatch):
    from generic_agents import report_evidence_delegated_budget_start as budget_start
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview
    monkeypatch.setattr(budget_start,'_enabled',lambda:True)
    monkeypatch.setattr(gate,'ready',lambda:True)
    p,request,command,preview,old=scene_and_preview(scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        result=budget_start.start_tx(db,p,request,command,preview_id=preview['preview_id'],
            allowance_preview=preview['allowance'])
        db.commit()
    return None,old,result['result']['draft']


def install_http(monkeypatch, requests, mode='stop'):
    monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-test-only')
    original=httpx.Client
    class Body(httpx.SyncByteStream):
        def __init__(self,data):self.data=data
        def __iter__(self):
            if mode=='disconnect':
                yield self.data[:10]
                raise httpx.ReadError('synthetic disconnect')
            yield self.data
    def handler(request):
        assert str(request.url)=='https://api.deepseek.com/chat/completions'
        sent=json.loads(request.content);payload=json.loads(sent['messages'][-1]['content'])
        if payload['schema']=='report-evidence-read-payload-v1':
            value={'schema':'report-evidence-map-v3','claims':[{'kind':'职责',
                'statement':'合成声明','span_refs':[payload['spans'][0][0]],'status':'explicit','explanation':''}]}
        else:
            assert payload['schema']=='report-evidence-reduction-payload-v2'
            from tests.test_report_evidence_pipeline_v3 import candidate
            value=candidate(payload)
        content='{invalid' if mode=='inner_invalid' else json.dumps(value,ensure_ascii=False)
        choices=[{'message':{'content':content},'finish_reason':'length' if mode=='length' else 'stop'}]
        if mode=='multiple':choices.append(copy.deepcopy(choices[0]))
        result={'choices':choices,
                'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}}
        requests.append(sent)
        encoded=b'{invalid outer' if mode=='outer_invalid' else json.dumps(result,ensure_ascii=False).encode()
        return httpx.Response(200,headers={'x-request-id':'synthetic'},stream=Body(encoded))
    monkeypatch.setattr(gateway.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    def traced(*args,**kwargs):
        try:return gateway.cloud_json(*args,**kwargs)
        except Exception as error:
            cause=error.__cause__
            requests.append(('synthetic_error',type(error).__name__,str(error),type(cause).__name__,str(cause)))
            raise
    monkeypatch.setattr(pipeline,'cloud_json',traced)


def test_real_worker_records_every_delegated_map_wire(reading_scene,monkeypatch):
    scene=reading_scene;monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    old_provider,old,created=start_delegated(scene,monkeypatch);requests=[];install_http(monkeypatch,requests)
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],created['id'])
        assert row.status=='awaiting_merge' and len(requests)==len(row.data['maps']), ([x for x in requests if isinstance(x,tuple)],row.data.get('error_code'))
        assert all(item['status']=='succeeded' for item in row.data['maps'])
        for stage in row.data['maps']:
            from generic_agents import report_evidence_delegated_wire_v1 as delegated
            _,receipt,state=delegated.response(pipeline,row,stage['chunk_id'])
            assert state['ready'] and receipt['finish_reason']=='stop' and receipt['usage']['total_tokens']==20
        original=db.get(SetupReportDraft,old['id'])
        assert original.status=='failed' and original.data['extractions'][1]['status']=='pending'
        version=row.version
    base='/api/setup/report-evidence-readings/'+created['id'];body={'revision':version,'request_id':'fer_'+str(uuid4())}
    response=scene['client'].post(base+'/reduce-preview',json=body);assert response.status_code==200,response.text
    assert response.json()['can_start'] is False
    response=scene['client'].post(base+'/reduce',json={**body,'preview_hash':response.json()['preview_hash'],'consent':True})
    assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='delegated_reduce_authorization_unavailable'
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],created['id'])
        assert row.status=='awaiting_merge' and row.data['reduction']['status']=='not_started'
    assert len(requests)==len(row.data['maps'])


def test_complete_wire_survives_callback_failure_and_free_recovery(reading_scene,monkeypatch):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene=reading_scene;monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch);requests=[];install_http(monkeypatch,requests)
    original=delegated.response_context;once={'fail':True}
    def fail_once(*args,**kwargs):
        if once['fail']:
            once['fail']=False;raise ValueError('synthetic interruption after durable HTTP')
        return original(*args,**kwargs)
    monkeypatch.setattr(delegated,'response_context',fail_once)
    assert process_once(created['job_id'])
    assert sum(isinstance(item,dict) for item in requests)==1
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);assert row.status=='response_unknown'
        version=row.version;before=db.scalar(select(func.count()).select_from(CloudCall))
    response=scene['client'].post('/api/setup/report-evidence-readings/'+created['id']+'/recover-saved',
        json={'revision':version,'request_id':str(uuid4())})
    assert response.status_code==200,response.text
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id'])
        assert row.status=='failed' and row.data['maps'][0]['status']=='succeeded'
        assert all(item['status']=='pending' for item in row.data['maps'][1:])
        assert db.scalar(select(func.count()).select_from(CloudCall))==before
    assert sum(isinstance(item,dict) for item in requests)==1


@pytest.mark.parametrize('mode',['length','multiple','inner_invalid','outer_invalid','disconnect'])
def test_incomplete_or_invalid_results_remain_recoverable_evidence_without_repayment(reading_scene,monkeypatch,mode):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene=reading_scene;monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch);requests=[];install_http(monkeypatch,requests,mode)
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],created['id']);assert row.status in {'failed','response_unknown'}
        stage=row.data['maps'][0];assert stage['status']!='succeeded'
        _,state=delegated.load(pipeline,row,stage['chunk_id'])
        assert state['response_complete'] is (mode!='disconnect')
        version=row.version;before=db.scalar(select(func.count()).select_from(CloudCall))
    response=scene['client'].post('/api/setup/report-evidence-readings/'+created['id']+'/recover-saved',
        json={'revision':version,'request_id':str(uuid4())})
    assert response.status_code in {409,422}
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);assert row.version==version and row.data['maps'][0]['status']!='succeeded'
        assert db.scalar(select(func.count()).select_from(CloudCall))==before
    assert sum(isinstance(item,dict) for item in requests)==1


def test_real_worker_kill_after_wire_fsync_recovers_without_new_call(reading_scene,monkeypatch,tmp_path):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene=reading_scene;monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,old,created=start_delegated(scene,monkeypatch)
    script=tmp_path/'owned_worker.py'
    script.write_text('''import json,os,signal,sys,httpx
from generic_agents import gateway,report_evidence_delegated_wire_v1 as delegated
from generic_agents import report_full_reading_pipeline as full
from generic_agents.config import settings
from generic_agents.worker import process_once
from generic_agents import report_evidence_delegated_dispatch_gate as gate
gate.ready=lambda:True  # isolated synthetic child-process test only
settings().cloud_daily_call_limit=20
settings().cloud_daily_token_limit=4000000
measure=full.measure_input
def stricter(messages,policy,max_tokens):
 payload=json.loads(messages[-1]["content"])
 chosen={**policy,"max_input_bytes":min(policy["max_input_bytes"],int(sys.argv[3]))} if payload.get("schema")=="report-full-reading-extract-evidence-v1" else policy
 return measure(messages,chosen,max_tokens)
full.measure_input=stricter
original=httpx.Client
class Body(httpx.SyncByteStream):
 def __init__(self,data):self.data=data
 def __iter__(self):yield self.data
def handler(request):
 data=json.loads(request.content); payload=json.loads(data["messages"][-1]["content"])
 value={"schema":"report-evidence-map-v3","claims":[{"kind":"职责","statement":"合成声明","span_refs":[payload["spans"][0][0]],"status":"explicit","explanation":""}]}
 result={"choices":[{"message":{"content":json.dumps(value,ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}
 return httpx.Response(200,headers={"x-request-id":"synthetic"},stream=Body(json.dumps(result,ensure_ascii=False).encode()))
gateway.httpx.Client=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)
def terminate(*args,**kwargs):os.kill(os.getpid(),signal.SIGKILL)
delegated.response_context=terminate
process_once(sys.argv[1],kinds=["read_report_draft"],deliver=False,workspace_id=sys.argv[2])
''')
    env=os.environ.copy();env['GA_DATABASE_URL']=engine().url.render_as_string(hide_password=False)
    env['GA_STORAGE_DIR']=str(settings().storage_dir);env['DEEPSEEK_API_KEY']='synthetic-test-only'
    bound=old['preview']['batches'][0]['input_summary']['max_input_bytes']
    result=subprocess.run([str(Path(__file__).parents[1]/'.venv/bin/python'),str(script),created['job_id'],scene['p'].workspace_id,str(bound)],
        cwd=Path(__file__).parents[1],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    assert result.returncode==-signal.SIGKILL,result.stderr.decode(errors='replace')[-1000:]
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],created['id']);assert row.status=='running'
        stage=row.data['maps'][0];assert stage['reserved_call_id'] and stage['journal'] is None
        _,state=delegated.load(pipeline,row,stage['chunk_id']);assert state['ready'] and state['response_complete']
        before=db.scalar(select(func.count()).select_from(CloudCall))
        job=db.get(Job,created['job_id']);job.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    # The reclaimed worker must only record unknown state; it cannot send.
    requests=[];install_http(monkeypatch,requests)
    assert process_once(created['job_id'],kinds=['read_report_draft'],deliver=False,workspace_id=scene['p'].workspace_id)
    assert not requests
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);assert row.status=='response_unknown';version=row.version
    response=scene['client'].post('/api/setup/report-evidence-readings/'+created['id']+'/recover-saved',
        json={'revision':version,'request_id':str(uuid4())})
    assert response.status_code==200,response.text
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);assert row.data['maps'][0]['status']=='succeeded'
        assert db.scalar(select(func.count()).select_from(CloudCall))==before
    assert not requests
