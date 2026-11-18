"""Synthetic streaming tests. No external connection, DB or real credentials."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID

import httpx
import pytest

from generic_agents import cloud_budget, cloud_provider_policy, gateway
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents.cloud_evidence_profiles import evidence_options, evidence_policy
from generic_agents.cloud_input_profiles import measure_input
from generic_agents.contracts import digest
from generic_agents.report_evidence_delegated_wire_v1 import DELIVERY, ENDPOINT


def uid(n):
    return str(UUID('10000000-0000-4000-8000-' + f'{n:012x}'))


MESSAGES = [{'role':'user','content':'synthetic evidence'}]
SCHEMA = {'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}


@pytest.fixture
def run(monkeypatch, tmp_path):
    # Exercise streaming with a synthetic provider, never enable the real gate.
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    monkeypatch.setattr(gate, 'ready', lambda: True)
    execution={'provider':'deepseek','model':'deepseek-v4-pro','secret_ref':'DEEPSEEK_API_KEY',
        'max_tokens':8192,'thinking_mode':'disabled'}
    principal=NS(user_id=uid(1),workspace_id=uid(2))
    cap=NS(id=uid(3),workspace_id=uid(2),data={'execution':execution,
        'data_policy':{'cloud_allowed':True,'redact_fields':[]}})
    secret=NS(get_secret_value=lambda:'synthetic-key')
    monkeypatch.setenv('DEEPSEEK_API_KEY','')
    monkeypatch.setattr(gateway,'settings',lambda:NS(deepseek_api_key=secret,dashscope_api_key=None))
    monkeypatch.setattr(cloud_provider_policy,'provider_enabled',lambda provider:True)
    state=NS(call=None,finish=[],callbacks=[],sink=None,payload=None,status=200,headers={},stream=None)
    def reserve(p, c, bound, request_hash):
        state.call=NS(status='reserved',reserved_tokens=bound,data={'request_hash':request_hash});return uid(5)
    def finish(call_id,receipt=None,status='failed'):
        if state.call.status=='reserved':
            state.finish.append((copy.deepcopy(receipt),status));state.call.status=status
    monkeypatch.setattr(cloud_budget,'reserve',reserve);monkeypatch.setattr(cloud_budget,'finish',finish)
    from generic_agents import db
    marker=object();monkeypatch.setattr(db,'engine',lambda:marker)
    import sqlalchemy.orm
    class Ledger:
        def __init__(self,engine):assert engine is marker
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def scalar(self,_):return state.call
        def commit(self):pass
    monkeypatch.setattr(sqlalchemy.orm,'Session',Ledger)
    original_client=httpx.Client
    class BodyStream(httpx.SyncByteStream):
        def __init__(self, value):self.value=value
        def __iter__(self):yield self.value
    def handler(request):
        assert str(request.url)==ENDPOINT and request.headers['accept-encoding']=='identity'
        if state.stream is not None:return httpx.Response(state.status,headers=state.headers,stream=state.stream)
        return httpx.Response(state.status,headers=state.headers,stream=BodyStream(state.payload))
    monkeypatch.setattr(gateway.httpx,'Client',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
    def execute(payload,*, stage='map-v3',headers=None,stream=None):
        state.payload=payload;state.headers=headers or {};state.stream=stream
        prepared,_,_=gateway.prepare_cloud_json(cap,MESSAGES,SCHEMA)
        policy=evidence_policy(execution,stage);options=evidence_options(execution,gateway.cloud_json_options(execution),stage)
        bound=measure_input(prepared,policy,options['max_tokens'])['token_bound']
        key='reduce' if stage=='reduce-v2' else 'map-1'
        binding={'schema':'report-evidence-journal-binding-v2' if key=='reduce' else 'report-evidence-journal-binding-v1',
          'actor_id':principal.user_id,'workspace_id':principal.workspace_id,'draft_id':uid(4),'stage':key,
          'reserved_call_id':uid(5),'source_hash':'a'*64,'catalog_hash':'b'*64,'generation_hash':'c'*64,
          'dispatch_hash':'d'*64,'messages_hash':digest(prepared)}
        ctx={'binding':binding,'request_hash':digest({**options,'messages':prepared}),'endpoint':ENDPOINT,
          'delivery':DELIVERY,'schema_proof_hash':digest(SCHEMA),'profile_hash':digest(policy),'token_bound':bound,
          'max_bytes':capture.REDUCE_LIMIT if key=='reduce' else capture.MAP_LIMIT}
        root=tmp_path/('capture-'+key)
        def reserved(call_id):
            assert call_id==uid(5)
        from generic_agents import report_evidence_prebound_gateway as prebound_module
        class FakePrebound:
            call_id=uid(5)
            def acquire(self,p,cap_id,messages,opts):
                assert p is principal and cap_id==cap.id
                assert digest(messages)==digest(prepared) and digest(opts)==digest(options)
                state.sink=capture.prepare(root,ctx)
                return {'sink':state.sink,'messages':copy.deepcopy(messages),'options':copy.deepcopy(opts)}
        monkeypatch.setattr(prebound_module,'PreboundGatewayCall',FakePrebound)
        state.call=NS(status='reserved',reserved_tokens=bound,data={'request_hash':digest(prepared)})
        def received(content,receipt):
            record=capture.load(root,ctx)
            assert record['response_complete'] is True
            assert capture.read_body(root,ctx,record['capture_hash'])==payload
            state.callbacks.append((content,copy.deepcopy(receipt)))
        try:
            return gateway.cloud_json(cap,MESSAGES,SCHEMA,principal,full_report=True,evidence_stage=stage,
                on_reserved=reserved,on_response=received,delegated_evidence_wire_guard=True,
                delegated_prebound=FakePrebound())
        finally:
            state.capture=capture.load(root,ctx) if state.sink else None
            state.ctx=ctx;state.root=root
    return state,execute


def body(reason='stop',count=1,content='{"value":"ok"}'):
    return json.dumps({'usage':{'total_tokens':17},'choices':[{'finish_reason':reason,'message':{'content':content}} for _ in range(count)]}).encode()


def test_complete_provider_wire_fsynced_before_callback(run):
    state,execute=run;value,receipt=execute(body())
    assert value=={'value':'ok'} and receipt['finish_reason']=='stop'
    assert state.capture['response_complete'] and state.callbacks and not state.finish
    assert state.callbacks[0][1]['usage']=={'total_tokens':17}


def test_reduce_stays_closed_without_its_own_plan(run):
    state,_=run
    with pytest.raises(gateway.ModelBlocked):
        gateway._cloud_json(NS(),[],SCHEMA,NS(),full_report=True,evidence_stage='reduce-v2',
            on_reserved=lambda *_:None,on_response=lambda *_:None,delegated_evidence_wire_guard=True)
    assert state.sink is None and not state.finish


@pytest.mark.parametrize('raw',[body('length'),body(count=2),body(content='{broken')])
def test_nonstop_multichoice_or_invalid_content_preserve_wire_and_never_succeed(run,raw):
    state,execute=run
    with pytest.raises(gateway.ModelBlocked):execute(raw)
    assert state.capture['response_complete'] and len(state.callbacks)==1
    assert not state.finish and state.callbacks[0][1]['usage']=={'total_tokens':17}


def test_invalid_outer_json_is_captured_before_parse_and_keeps_original_reservation(run):
    state,execute=run
    with pytest.raises(gateway.ModelBlocked):execute(b'{invalid outer')
    assert state.capture['response_complete'] and not state.callbacks
    assert not state.finish
    assert capture.read_body(state.root,state.ctx,state.capture['capture_hash'])==b'{invalid outer'


def test_oversize_and_interrupted_streams_never_look_complete(run):
    state,execute=run
    with pytest.raises(gateway.ModelBlocked):execute(b'a'*(capture.MAP_LIMIT+1))
    assert state.capture['response_complete'] is False and not state.callbacks


def test_broken_transport_persists_partial_capture_and_never_callbacks(run):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"choices":'
            raise httpx.ReadError('synthetic disconnect')
    state,execute=run
    with pytest.raises(gateway.ModelBlocked):execute(b'',stream=BrokenStream())
    assert state.capture['response_complete'] is False and state.capture['body_bytes'] > 0
    assert not state.callbacks and not state.finish


def test_unsupported_content_encoding_is_recorded_but_not_consumed(run):
    state,execute=run
    with pytest.raises(gateway.ModelBlocked):execute(body(),headers={'content-encoding':'gzip'})
    assert state.capture['ready'] is False and state.capture['http_content_encoding']=='gzip' and not state.callbacks
    assert not state.finish


def test_invalid_guard_combinations_fail_before_reservation(run):
    state,_=run
    cap=NS(data={'execution':{'provider':'deepseek'},'data_policy':{'cloud_allowed':True}})
    with pytest.raises(gateway.ModelBlocked):
        gateway._cloud_json(cap,[],SCHEMA,None,delegated_evidence_wire_guard=True)
    assert state.call is None
