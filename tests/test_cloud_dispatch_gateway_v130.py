"""Real four gateway branches; synthetic ledger/transport/capture control flow.

No DB authority, native fsync, provider quality or paid-model proof is claimed.
The separate API suite uses real budget/Worker/PostgreSQL transactions.
"""
import copy
from types import SimpleNamespace as NS

import httpx
import pytest

from generic_agents import gateway, cloud_budget
from generic_agents.contracts import digest
from generic_agents.cloud_evidence_profiles import evidence_policy, evidence_options
from generic_agents.cloud_input_profiles import measure_request_body
from generic_agents import report_evidence_qwen_json_schema_v1 as native
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from tests.test_cloud_evidence_strict_gateway_pure import transport, no_external_execution, execution as strict_execution, SCHEMA, MESSAGES, CALL
from tests.test_cloud_evidence_structured_gateway_pure import execution as structured_execution
from tests.test_report_evidence_v7_protocol_pure import capability as native_capability
from generic_agents.report_evidence_map_slots_v5 import MapSlotsWireV5

IDENTITY={'suggestion_id':'synthetic-suggestion','job_id':'synthetic-job',
    'task_id':'synthetic-task','request_hash':'a'*64}


def _call(state, monkeypatch, stage, mode):
    execution = strict_execution() if stage=='map-v4' else structured_execution() if stage=='map-v5' else copy.deepcopy(native_capability().data['execution']) if stage=='map-v7' else {
        'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY','max_tokens':2048}
    cap=NS(id='synthetic-capability',workspace_id='synthetic-workspace',
        data={'execution':execution,'data_policy':{'cloud_allowed':True,'redact_fields':[]}})
    p=NS(user_id='synthetic-actor',workspace_id=cap.workspace_id)
    schema=MapSlotsWireV5.model_json_schema() if stage in {'map-v5','map-v7'} else SCHEMA
    calls=[]
    synthetic_settings=gateway.settings()
    synthetic_settings.qwen_tokenplan_api_key=None
    monkeypatch.setattr(gateway,'settings',lambda:synthetic_settings)
    if mode in {'denied','commit_unknown'}:
        def blocked(*args):
            calls.append('reserve')
            if mode=='denied':raise cloud_budget.BudgetDenied('synthetic quota denial')
            raise OSError('synthetic reservation commit uncertainty')
        monkeypatch.setattr(cloud_budget,'reserve',blocked)
    if mode=='finish_failure':
        def finish(*args,**kwargs):raise OSError('synthetic finish failure')
        monkeypatch.setattr(cloud_budget,'finish',finish)
    if mode=='client_init':
        def client(*args,**kwargs):raise OSError('synthetic client initialization failure')
        monkeypatch.setattr(gateway.httpx,'Client',client)
    else:
        class Client:
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def post(self,*args,**kwargs):
                calls.append('post')
                raise httpx.ReadTimeout('synthetic post entered')
            def stream(self,*args,**kwargs):
                calls.append('stream')
                raise httpx.ReadTimeout('synthetic stream entered')
        monkeypatch.setattr(gateway.httpx,'Client',Client)
    class SyntheticCapture(capture.NativeHttpCapture):
        def __init__(self,ctx):self._context=copy.deepcopy(ctx)
        def mark_request_started(self):calls.append('capture_started')
        def abort(self,*args):calls.append('capture_abort')
        def close(self):calls.append('capture_close')
    def no_response(*args):raise ValueError('no synthetic capture response')
    monkeypatch.setattr(bridge,'response_context',no_response)
    monkeypatch.setenv('QWEN_TOKENPLAN_API_KEY','synthetic-no-provider-key')
    def authorized(call_id):
        assert call_id==CALL
        calls.append('authorized')
        if mode=='authorization':raise gateway.ModelBlocked('synthetic dispatch authorization revoked')
        if stage!='map-v7':return None
        policy=evidence_policy(execution,stage)
        messages,_,_=gateway.prepare_cloud_json(cap,MESSAGES,schema)
        prepared=native.prepare_request(messages,schema,evidence_options(execution,gateway.cloud_json_options(execution),stage),stage)
        measured=measure_request_body(prepared['body'],policy,prepared['request']['max_tokens'])
        ctx={'binding':{'actor_id':p.user_id,'workspace_id':p.workspace_id,
                'reserved_call_id':call_id,'messages_hash':digest(prepared['request']['messages']),'stage':'map-1'},
            'request_hash':prepared['body_sha256'],'endpoint':prepared['endpoint'],'delivery':native.DELIVERY,
            'schema_proof_hash':digest(prepared['schema_proof']),'profile_hash':digest(policy),
            'token_bound':measured['token_bound'],'max_bytes':native.stage_policy(stage)['max_response_bytes']}
        return SyntheticCapture(ctx)
    try:
        gateway.cloud_json(cap,MESSAGES,schema,p,on_reserved=authorized,
            on_response=lambda *args:None,full_report=stage is not None,
            evidence_stage=stage,dispatch_identity=IDENTITY)
    except Exception as error:return error,calls
    raise AssertionError('Synthetic failure was not reached')


@pytest.mark.parametrize('stage',[None,'map-v4','map-v5','map-v7'])
@pytest.mark.parametrize('mode',['denied','commit_unknown','authorization','client_init','timeout','finish_failure'])
def test_each_real_branch_distinguishes_pre_http_from_entered_transport(transport,monkeypatch,stage,mode):
    error,calls=_call(transport,monkeypatch,stage,mode)
    if mode in {'timeout','finish_failure'}:
        assert not isinstance(error,gateway.CloudNotSent)
        assert calls.count('stream' if stage=='map-v7' else 'post')==1
    else:
        assert isinstance(error,gateway.CloudNotSent),(type(error).__name__,str(error))
        proof=error.dispatch_proof
        assert proof['transport']=='not_started'
        assert proof['reservation_state']=={'denied':'denied','commit_unknown':'unknown','authorization':'confirmed','client_init':'confirmed'}[mode]
        assert not any(name in calls for name in ('post','stream'))
        assert proof['call_id']==(CALL if mode in {'authorization','client_init'} else None)
