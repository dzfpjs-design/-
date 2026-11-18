"""Full-reading completion transport guard; synthetic HTTP only."""
import base64
import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_full_reading_pipeline as pipeline
from tests.test_cloud_evidence_profiles_pure import transport, execution, MESSAGES, SCHEMA, USAGE


def invoke(transport, monkeypatch, *, guarded=True):
    original=gateway.httpx.Client
    class Client(original):
        def post(self,*args,**kwargs):
            response=super().post(*args,**kwargs)
            response.content=getattr(transport,'wire_override',None) or json.dumps(response.json(),ensure_ascii=False).encode('utf-8')
            return response
        @contextmanager
        def stream(self,method,endpoint,**kwargs):
            assert method=='POST' and kwargs['headers'].pop('Accept-Encoding')=='identity'
            response=self.post(endpoint,**kwargs)
            if getattr(transport,'content_encoding',None):response.headers['content-encoding']=transport.content_encoding
            def chunks():
                if getattr(transport,'interrupt_stream',False):
                    yield response.content[:10]
                    raise gateway.httpx.ReadError('synthetic interruption')
                yield response.content
            response.iter_raw=chunks
            yield response
    monkeypatch.setattr(gateway.httpx,'Client',Client)
    cap=NS(id='synthetic-cap',workspace_id='synthetic-workspace',data={'execution':execution(maximum=8192,nested=False),
           'data_policy':{'cloud_allowed':True,'redact_fields':[]}})
    p=NS(user_id='synthetic-actor',workspace_id=cap.workspace_id)
    def received(content,receipt): transport.callbacks.append((content,copy.deepcopy(receipt)))
    kwargs={'full_report_completion_guard':True} if guarded else {}
    return gateway.cloud_json(cap,MESSAGES,SCHEMA,p,full_report=True,on_reserved=lambda _:None,on_response=received,**kwargs)


@pytest.mark.parametrize('reason,extra', [('length',[]),(None,[]),('stop',[{'finish_reason':'stop','message':{'content':'{}'}}])])
def test_paid_response_is_delivered_before_incomplete_or_ambiguous_is_rejected(transport,monkeypatch,reason,extra):
    transport.reason=reason;transport.extra_choices=extra
    with pytest.raises(gateway.ModelBlocked):invoke(transport,monkeypatch)
    assert len(transport.callbacks)==1
    original,receipt=transport.callbacks[0]
    assert original==transport.content and receipt['usage']==USAGE
    assert receipt['finish_reason']==reason and receipt['response_choice_count']==1+len(extra)
    wire=json.loads(base64.b64decode(receipt['_full_report_wire_base64']).decode('utf-8'))
    assert wire['usage']==USAGE and len(wire['choices'])==1+len(extra)
    assert 'validate' not in transport.events


def test_stop_allows_new_full_reading_and_legacy_unguarded_path_is_unchanged(transport,monkeypatch):
    value,receipt=invoke(transport,monkeypatch)
    assert value=={'value':'新合成结果。'} and receipt['finish_reason']=='stop'
    assert receipt['response_choice_count']==1 and receipt['completion_guard']=='full-report-completion-v1'


def test_legacy_full_report_does_not_gain_new_gate(transport,monkeypatch):
    transport.reason='length'
    value,receipt=invoke(transport,monkeypatch,guarded=False)
    assert value=={'value':'新合成结果。'} and 'finish_reason' not in receipt


def test_zero_choices_preserves_wire_and_usage_and_never_claims_success(transport,monkeypatch):
    transport.payload_override={'usage':copy.deepcopy(USAGE),'choices':[]}
    with pytest.raises(gateway.ModelBlocked):invoke(transport,monkeypatch)
    assert len(transport.callbacks)==1
    content,receipt=transport.callbacks[0]
    assert content is None and receipt['response_choice_count']==0 and receipt['usage']==USAGE
    assert json.loads(base64.b64decode(receipt['_full_report_wire_base64']))['choices']==[]


def test_oversized_wire_does_not_claim_saved_body_or_charge_known_usage(transport,monkeypatch):
    transport.content='x'*(gateway.MAX_FULL_READING_WIRE_BYTES+1)
    with pytest.raises(gateway.ModelBlocked):invoke(transport,monkeypatch)
    content,receipt=transport.callbacks[0]
    assert content is None and receipt['response_body_status']=='oversize'
    assert receipt['_full_report_wire_base64'] is None and receipt['usage']=={}
    assert transport.finished[0][0]['usage']=={} and transport.finished[0][1]=='failed'


def test_deep_json_is_saved_as_wire_before_fail_closed(transport,monkeypatch):
    transport.wire_override=b'['*2000+b'0'+b']'*2000
    with pytest.raises(gateway.ModelBlocked):invoke(transport,monkeypatch)
    content,receipt=transport.callbacks[0]
    assert content is None and receipt['response_body_status']=='complete'
    assert base64.b64decode(receipt['_full_report_wire_base64'])==transport.wire_override
    assert receipt['usage']=={} and transport.finished[0][1]=='failed'


def test_unsupported_encoding_is_not_decompressed_or_misreported_as_saved(transport,monkeypatch):
    transport.content_encoding='gzip'
    with pytest.raises(gateway.ModelBlocked):invoke(transport,monkeypatch)
    content,receipt=transport.callbacks[0]
    assert content is None and receipt['response_body_status']=='unsupported_encoding'
    assert receipt['_full_report_wire_base64'] is None and receipt['usage']=={}


def test_interrupted_stream_keeps_paid_response_unknown_and_no_callback(transport,monkeypatch):
    transport.interrupt_stream=True
    with pytest.raises(gateway.ModelBlocked):invoke(transport,monkeypatch)
    assert transport.callbacks==[] and transport.finished==[(None,'failed')]
    assert len(transport.requests)==1


@pytest.mark.parametrize('change',['wire_finish','wire_content','wire_usage','wire_missing'])
def test_completion_cannot_be_forged_by_metadata_or_changed_wire(change):
    receipt={'completion_guard':'full-report-completion-v1','response_choice_count':1,
             'finish_reason':'stop','response_body_status':'complete','usage':{'total_tokens':4}}
    content='{"value":"ok"}'
    response={'choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':receipt['usage']}
    if change=='wire_finish':response['choices'][0]['finish_reason']='length'
    if change=='wire_content':response['choices'][0]['message']['content']='{}'
    if change=='wire_usage':response['usage']={'total_tokens':5}
    raw={'receipt':receipt,'content':content,'value':{'value':'ok'}}
    if change!='wire_missing':raw['provider_wire_base64']=base64.b64encode(json.dumps(response).encode()).decode()
    assert pipeline._complete_response(raw) is False
