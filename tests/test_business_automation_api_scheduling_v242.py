"""HTTP scheduling and operation-local pure parsing, not IAM/DB/IO proof.

The slow service and dependencies are explicit stubs in this isolated ASGI app.
Root must additionally repeat real large-report requests in the actual server.
"""
import asyncio
import threading
from types import SimpleNamespace as NS
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from generic_agents import business_automation_api as api
from generic_agents.security import Principal
from generic_agents.report_text_parse_scope import read_docx_cached
from tests.test_business_automation_contracts_v242 import selection


def test_health_can_respond_while_valid_preview_is_still_running_in_a_thread(monkeypatch):
    entered=threading.Event();release=threading.Event();observed=[]
    instance_id=str(uuid4());principal=Principal(str(uuid4()),str(uuid4()),'admin','test-asgi-scheduling')
    db=object();main_thread=threading.get_ident()
    def slow(actual_db,actual_p,identity,body):
        assert actual_db is db and actual_p is principal and identity==instance_id
        observed.append(threading.get_ident());entered.set()
        release.wait(3)
        return {'isolated_preview':'finished'}
    monkeypatch.setattr(api.service,'preview',slow)
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[api.authenticated]=lambda:principal
    app.dependency_overrides[api.session]=lambda:db
    @app.get('/health')
    async def health():return {'status':'ready'}
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated.test') as client:
            body={'schema':'business-automation-preview-request-v1','selection':selection(),'expected_options_hash':'a'*64}
            preview=asyncio.create_task(client.post('/api/v2/instances/'+instance_id+'/business-automations/preview',json=body))
            try:
                deadline=asyncio.get_running_loop().time()+1
                while not entered.is_set():
                    assert not preview.done()
                    assert asyncio.get_running_loop().time()<deadline
                    await asyncio.sleep(.01)
                assert not preview.done(),'synchronous preview monopolized the ASGI event loop'
                reply=await asyncio.wait_for(client.get('/health'),timeout=1)
                assert reply.status_code==200 and reply.json()=={'status':'ready'}
                assert not preview.done(),'health was only served after the preview completed'
            finally:release.set()
            reply=await asyncio.wait_for(preview,timeout=2)
            assert reply.status_code==200 and reply.json()=={'isolated_preview':'finished'}
    asyncio.run(scenario())
    assert len(observed)==1 and observed[0]!=main_thread


@pytest.mark.parametrize('operation',['options','preview','transaction'])
def test_each_sync_operation_reuses_exact_byte_parse_but_repeats_service_and_expires_after_return(monkeypatch,operation):
    parses=[];checks=[];content=b'explicit-pure-parser-fixture-not-an-actual-report'
    def parse_original(raw):parses.append(raw);return {'blocks':[{'text':'same bytes'}]}
    def read():return read_docx_cached(content,policy='api-scope-test',max_blocks=20,max_characters=100,parse=parse_original)
    def run_service(*args):
        checks.append(operation)
        one,two=read(),read();assert one==two and one is not two
        return {'checked':len(checks)}
    identity=str(uuid4());release=str(uuid4());db=NS(commit=lambda:None,rollback=lambda:None)
    if operation=='options':
        monkeypatch.setattr(api.service,'options',run_service)
        call=lambda:api.options(identity,release,'opaque-business',None,db)
    elif operation=='preview':
        monkeypatch.setattr(api.service,'preview',run_service)
        call=lambda:api.preview_operation(db,None,identity,{})
    else:
        monkeypatch.setattr(api.service,'command',run_service)
        call=lambda:api.transaction(db,None,identity,b'{}','enable')
    assert call()=={'checked':1}
    assert len(parses)==1 and checks==[operation]
    assert call()=={'checked':2}
    assert len(parses)==2 and checks==[operation,operation]
    read();read()
    assert len(parses)==4  # No scope leaks into subsequent calls/requests.


def test_preview_permission_error_is_not_cached_or_converted_to_a_saved_success(monkeypatch):
    calls=[]
    def changed(*args):
        calls.append(1)
        if len(calls)>1:raise HTTPException(403,'当前权限已撤销')
        return {'preview_only':True}
    monkeypatch.setattr(api.service,'preview',changed)
    identity=str(uuid4())
    assert api.preview_operation(None,None,identity,{})=={'preview_only':True}
    with pytest.raises(HTTPException) as failure:api.preview_operation(None,None,identity,{})
    assert failure.value.status_code==403 and len(calls)==2
