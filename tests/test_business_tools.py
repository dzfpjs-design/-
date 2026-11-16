from .planning_helpers import post_plan
"""Real PostgreSQL plus independent loopback HTTP/MCP protocol services.

Cloud planning alone uses an explicit fixture. These are not industry specialist models.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Capability
from generic_agents.worker import process_once
from generic_agents.contracts import digest
from .test_vertical_slice import manifest,publish,entity,explicit_goal

INPUT={'type':'object','additionalProperties':False,'required':['records','field'],
    'properties':{'records':{'type':'array','items':{'type':'object'}},'field':{'type':'string'}}}
OUTPUT={'type':'object','additionalProperties':False,'required':['value','samples','method'],
    'properties':{'value':{'type':'number'},'samples':{'type':'integer'},'method':{'type':'string'}}}


@pytest.fixture
def service(monkeypatch):
    state={'calls':0,'fail':False,'unknown':False,'mcp_headers':False,'input_schema':INPUT,'output_schema':OUTPUT,'receipt_status':'succeeded'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,value,status=200,extra=None):
            data=json.dumps(value).encode() if value is not None else b''
            sse=state.get('sse') and isinstance(value,dict) and value.get('jsonrpc')=='2.0'
            if sse:data=b'id: seed\ndata:\n\nevent: message\ndata: '+data+b'\n\n'
            self.send_response(status);self.send_header('Content-Type','text/event-stream' if sse else 'application/json')
            self.send_header('Content-Length',str(len(data)))
            for k,v in (extra or {}).items():self.send_header(k,v)
            self.end_headers();self.wfile.write(data)
        def do_DELETE(self):self.respond(None,204)
        def do_GET(self):
            inputs=state['last_inputs'];values=[r['attributes'][inputs['field']] for r in inputs['records']]
            output={'value':sum(values)/len(values),'samples':len(values),'method':'independent_synthetic_archive_mean'}
            response={'idempotency_key':state['request_key'],'input_hash':'wrong' if state.get('bad_receipt') else digest(inputs),'status':state['receipt_status']}
            if response['status']=='not_executed':
                response['terminal']=state.get('terminal_receipt',True)
                if response['terminal']:state.setdefault('closed_keys',set()).add(state['request_key'])
            if response['status']=='succeeded':response['output']=output
            self.respond(response)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if self.path=='/mcp':
                method=body['method'];identifier=body.get('id')
                if method=='initialize':
                    self.respond({'jsonrpc':'2.0','id':identifier,'result':{'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'synthetic-protocol-service','version':'1'}}},extra={'MCP-Session-Id':'synthetic-session'});return
                state['mcp_headers']=self.headers.get('MCP-Session-Id')=='synthetic-session' and self.headers.get('MCP-Protocol-Version')=='2025-11-25'
                if method=='notifications/initialized':self.respond(None,202);return
                if method=='tools/list':
                    self.respond({'jsonrpc':'2.0','id':identifier,'result':{'tools':[{'name':'archive_mean','inputSchema':state['input_schema'],'outputSchema':state['output_schema']}]}});return
                inputs=body['params']['arguments']
            else:inputs=body
            state['calls']+=1;state['last_inputs']=inputs
            state['request_key']=self.headers.get('Idempotency-Key')
            if state.get('entered'):
                state['entered'].set();state['release'].wait(5)
            if state.get('request_key') in state.get('closed_keys',set()):
                self.respond({'error':'request key permanently closed'},409);return
            if state['unknown']:self.connection.close();return
            if state['fail']:self.respond({'error':'synthetic temporary outage'},503);return
            values=[r['attributes'][inputs['field']] for r in inputs['records']]
            output={'value':sum(values)/len(values),'samples':len(values),'method':'independent_synthetic_archive_mean'}
            self.respond({'jsonrpc':'2.0','id':identifier,'result':{'structuredContent':output,'content':[]}}
                if self.path=='/mcp' else output)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1]);state['http']=base+'/analyze';state['mcp']=base+'/mcp';state['receipt']=base+'/receipts'
    monkeypatch.setattr(settings(),'development_connector_urls',state['http']+','+state['mcp']+','+state['receipt'])
    yield state
    server.shutdown();server.server_close();thread.join(timeout=2)


def setup(client,monkeypatch,service,adapter='http',side_effect=False,arguments=None,input_schema=INPUT,kind='tool'):
    execution={'adapter':adapter,'endpoint':service.get(adapter,''),'tool_name':'archive_mean',
        'input_bindings':{'records':'archive_records'},'side_effect':side_effect,'approval_policy':'admin',
        **({'receipt_endpoint':service['receipt']} if side_effect else {})}
    output_schema=OUTPUT
    if adapter=='builtin':
        execution={'adapter':'builtin','handler':'aggregate_values','input_bindings':{'records':'archive_records'}}
        input_schema={**INPUT,'required':[*INPUT['required'],'operation'],
            'properties':{**INPUT['properties'],'operation':{'enum':['mean','sum','min','max','count']}}}
        output_schema={'type':'object','required':['value','sample_count','provenance']}
        if arguments is None:arguments={'field':'temperature','operation':'mean'}
    tool=publish(client,manifest(kind,namespace='tool_validation',name='archive_analysis',specification={},
        input_schema=input_schema,output_schema=output_schema,allowed_agent_roles=['data'],data_policy={'external_allowed':True},execution=execution))
    package=publish(client,manifest(namespace='tool_validation',dependencies=[tool['id']],
        specification={'business_tool_ids':[tool['id']],'entity_schemas':manifest()['specification']['entity_schemas']}))
    obj=entity(client,package)
    model=publish(client,manifest('foundation_model',namespace='tool_validation',name='model',
        execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY'},data_policy={'cloud_allowed':True}))
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *args:({'summary':'使用登记分析服务计算档案均值',
        'steps':[{'title':'分析均值','agent':'data','action':'invoke_capability','capability_id':tool['id'],
            'arguments':{'field':'temperature'} if arguments is None else arguments},
            {'title':'报告','agent':'business','action':'report'}]}, {'provider':'explicit_test_fixture'}))
    task=client.post('/api/tasks',json={'title':'计算指定对象档案温度均值','package_id':package['id'],
        'entity_ids':[obj['id']],'fields':['temperature'],'observation_mode':'existing_only',
        'goal_spec':explicit_goal(obj,mode='existing_only',source='archive')}).json()
    plan=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],
        'reason':'协议验证','model_profile_id':model['id']}).json()
    return tool,package,obj,task,plan


def saved_task(client,identifier):
    return next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==identifier)


@pytest.mark.parametrize('adapter',['http','mcp'])
def test_real_registered_service_plan_execution_acceptance(client,monkeypatch,service,adapter):
    tool,package,obj,task,plan=setup(client,monkeypatch,service,adapter)
    assert plan['status']=='review'
    step=plan['data']['result']['plan']['steps'][0]
    assert step['input_snapshot']['records'][0]['entity_id']==obj['id']
    assert client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核对真实档案绑定'}).status_code==200
    assert process_once()
    saved=saved_task(client,task['id']);assert saved['status']=='awaiting_acceptance'
    receipts=saved['data']['step_receipts'];output=receipts[plan['id']+':0']['result']['output']
    assert output['value']==24 and output['samples']==1
    assert receipts[plan['id']+':1']['result']['tool_results'][0]['output']==output
    assert service['calls']==1
    if adapter=='mcp':assert service['mcp_headers']
    assert client.post('/api/tasks/'+task['id']+'/accept',json={'expected_version':saved['version'],'reason':'合成档案24均值24，人工核对协议'}).status_code==200
    memory=client.get('/api/overview').json()['memories'][0]
    assert memory['data']['tool_results'][0]['output']==output


def test_read_failure_visible_explicit_retry_preserves_history(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核查'})
    service['fail']=True;assert process_once()
    saved=saved_task(client,task['id']);assert saved['status']=='waiting_tool'
    assert len(saved['data']['step_receipts'])==1
    assert client.get('/api/overview').json()['jobs'][0]['status']=='failed'
    service['fail']=False
    response=client.post('/api/tasks/'+task['id']+'/retry_execution',json={'expected_version':saved['version'],'reason':'参考服务恢复，重试只读分析'})
    assert response.status_code==200,response.text
    assert process_once();saved=saved_task(client,task['id'])
    assert saved['status']=='awaiting_acceptance' and len(saved['data']['tool_failure_history'])==1
    assert service['calls']==2


def test_unknown_side_effect_never_replayed(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service,side_effect=True)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'仅合成协议动作'})
    service['unknown']=True;assert process_once()
    saved=saved_task(client,task['id']);assert saved['status']=='waiting_tool'
    assert saved['data']['step_receipts'][plan['id']+':0']['result']['status']=='unknown'
    assert client.post('/api/tasks/'+task['id']+'/retry_execution',json={'expected_version':saved['version'],'reason':'不能重放未知写'}).status_code==409
    assert post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':saved['version'],'reason':'不能新计划重放'}).status_code==409
    assert service['calls']==1


def test_model_cannot_fabricate_server_records(client,monkeypatch,service):
    *_,plan=setup(client,monkeypatch,service,arguments={'field':'temperature','records':[{'attributes':{'temperature':999}}]})
    assert plan['status']=='blocked_model' and service['calls']==0


def test_changed_capability_blocks_approved_execution(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核对原契约'})
    with Session(engine()) as db:
        cap=db.get(Capability,tool['id']);cap.data={**cap.data,'description':'篡改定义'};db.commit()
    assert process_once();saved=saved_task(client,task['id'])
    assert saved['status']=='waiting_tool' and service['calls']==0


def test_changed_evidence_blocks_plan_approval(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service)
    response=client.patch('/api/entities/'+obj['id'],json={'expected_version':obj['version'],'reason':'实际档案改变',
        'attributes':{'name':'修改后','temperature':30}})
    assert response.status_code==200,response.text
    assert client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'旧输入不能使用'}).status_code==409
    assert service['calls']==0


def test_mcp_contract_drift_rejected_before_tool_call(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service,'mcp')
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核查登记版本'})
    service['input_schema']={'type':'object','properties':{'different':{'type':'string'}}}
    assert process_once();saved=saved_task(client,task['id'])
    assert saved['status']=='waiting_tool' and service['calls']==0


def test_loopback_exception_exact_and_opt_in(monkeypatch):
    from generic_agents.gateway import validate_endpoint
    from fastapi import HTTPException
    monkeypatch.setattr(settings(),'development_connector_urls','http://127.0.0.1:9009/analyze')
    assert validate_endpoint({'endpoint':'http://127.0.0.1:9009/analyze'})=='http://127.0.0.1:9009/analyze'
    for url in ['http://127.0.0.1:9009/other','http://127.0.0.1:9010/analyze','http://localhost:9009/analyze',
                'http://127.0.0.1:invalid/analyze','https://example.com/analyze?api_key=ForbiddenPlaceholder']:
        with pytest.raises(HTTPException):validate_endpoint({'endpoint':url})


def test_cancel_during_network_call_is_not_overwritten(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'只读核查'})
    service['entered']=threading.Event();service['release']=threading.Event()
    worker=threading.Thread(target=process_once);worker.start()
    assert service['entered'].wait(5)
    current=saved_task(client,task['id'])
    response=client.post('/api/tasks/'+task['id']+'/cancel',json={'expected_version':current['version'],'reason':'用户取消，保留已发送调用的回执'})
    assert response.status_code==200,response.text
    service['release'].set();worker.join(timeout=5);assert not worker.is_alive()
    saved=saved_task(client,task['id'])
    assert saved['status']=='cancelled' and not saved['data'].get('final_evidence')
    assert len(saved['data']['step_receipts'])==1 and service['calls']==1


def test_unknown_write_recovers_only_from_matching_read_receipt(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service,side_effect=True)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'仅协议动作'})
    service['unknown']=True;assert process_once()
    saved=saved_task(client,task['id']);identifier=saved['data']['step_receipts'][plan['id']+':0']['result']['invocation_id']
    def check():
        row=client.get('/api/invocations/'+identifier).json()
        response=client.post('/api/invocations/'+identifier+'/reconcile',json={'expected_version':row['version'],'reason':'只读核对服务回执'})
        assert response.status_code==200,response.text
        return response.json()
    service['receipt_status']='pending';assert check()['status']=='unknown'
    service['receipt_status']='succeeded';service['bad_receipt']=True;assert check()['status']=='unknown'
    assert saved_task(client,task['id'])['status']=='waiting_tool' and service['calls']==1
    service['bad_receipt']=False;assert check()['status']=='succeeded'
    assert process_once();saved=saved_task(client,task['id'])
    assert saved['status']=='awaiting_acceptance' and service['calls']==1
    assert saved['data']['step_receipts'][plan['id']+':0']['result']['output']['value']==24


def test_confirmed_not_executed_allows_fresh_plan_not_blind_retry(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service,side_effect=True)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'协议'})
    service['unknown']=True;assert process_once();saved=saved_task(client,task['id'])
    identifier=saved['data']['step_receipts'][plan['id']+':0']['result']['invocation_id']
    row=client.get('/api/invocations/'+identifier).json();service['receipt_status']='not_executed'
    response=client.post('/api/invocations/'+identifier+'/reconcile',json={'expected_version':row['version'],'reason':'服务确认未执行，保留回执'})
    assert response.status_code==200,response.text
    saved=saved_task(client,task['id'])
    assert saved['data']['step_receipts'][plan['id']+':0']['result']['status']=='not_executed'
    assert client.post('/api/tasks/'+task['id']+'/retry_execution',json={'expected_version':saved['version'],'reason':'必须新批准'}).status_code==409
    model_id=next(c['id'] for c in client.get('/api/capabilities').json() if c['kind']=='foundation_model')
    assert post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':saved['version'],'reason':'新计划待批准','model_profile_id':model_id}).json()['status']=='review'
    assert service['calls']==1


def test_mcp_sse_with_empty_seed_event(client,monkeypatch,service):
    service['sse']=True
    tool,package,obj,task,plan=setup(client,monkeypatch,service,'mcp')
    assert plan['status']=='review'
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核对MCP流式响应'})
    assert process_once()
    assert saved_task(client,task['id'])['status']=='awaiting_acceptance' and service['calls']==1


def test_specialist_with_local_schema_refs_remains_valid_in_plan(client,monkeypatch,service):
    schema={**INPUT,'properties':{**INPUT['properties'],'field':{'$ref':'#/$defs/field_code'}},
        '$defs':{'field_code':{'type':'string','enum':['temperature']}}}
    tool,package,obj,task,plan=setup(client,monkeypatch,service,input_schema=schema,kind='specialist_model')
    assert plan['status']=='review'
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核对专业服务和本地Schema引用'})
    assert process_once()
    assert saved_task(client,task['id'])['status']=='awaiting_acceptance' and service['calls']==1


def test_registered_builtin_aggregate_uses_bound_source_records(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service,'builtin')
    assert plan['status']=='review'
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核对本地档案计算'})
    assert process_once();saved=saved_task(client,task['id'])
    output=saved['data']['step_receipts'][plan['id']+':0']['result']['output']
    assert saved['status']=='awaiting_acceptance' and output['value']==24 and output['sample_count']==1
    assert output['provenance']==[{'entity_id':obj['id'],'version':obj['version']}]
    assert service['calls']==0


def test_registered_retry_budget_is_enforced(client,monkeypatch,service):
    tool,package,obj,task,plan=setup(client,monkeypatch,service)
    client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'只读预算核查'})
    service['fail']=True;assert process_once();saved=saved_task(client,task['id'])
    assert client.post('/api/tasks/'+task['id']+'/retry_execution',json={'expected_version':saved['version'],'reason':'允许第二次'}).status_code==200
    assert process_once();saved=saved_task(client,task['id'])
    assert client.post('/api/tasks/'+task['id']+'/retry_execution',json={'expected_version':saved['version'],'reason':'超过登记预算'}).status_code==409
    assert service['calls']==2
