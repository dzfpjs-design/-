"""Public synthetic structured-plan request/capture/family checks, not AI quality."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import cloud_evidence_profiles as policy
from generic_agents import report_business_flow_native_v184 as native
from generic_agents import report_business_flow_preparation_v184 as prep
from generic_agents import report_business_flow_wire_v184 as wire
from generic_agents import report_business_flow_preparation_v183 as old_prep
from generic_agents import report_business_flow_native_v183 as old_native
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents import report_scoped_design_consumer_v181 as consumer
from generic_agents.contracts import digest
from generic_agents.gateway import prepare_cloud_json, cloud_json_options
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_report_business_flow_protocol_v182 as original
from tests.test_report_business_flow_preparation_v183 import scene as old_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('Pure V184 test attempted external I/O')
    for name in ('connect','connect_ex'): monkeypatch.setattr(socket.socket,name,blocked)
    monkeypatch.setattr(socket,'getaddrinfo',blocked)
    monkeypatch.setattr(subprocess,'Popen',blocked)


def scene():
    cap, source, plan = old_scene()
    cap.data['execution']['report_evidence_output'] = {'schema':native.OUTPUT_DECLARATION,
        'max_tokens':32768,'reduce_transport':native.DELIVERY,'endpoint':native.ENDPOINT}
    return cap,source,plan


def prepared():
    cap,source,plan=scene()
    value=prep.build_preparation(cap,source,plan,'custom:alpha',note='逐字保留输入＋🙂')
    rebuilt=prep.rebuild_request(value,cap,source,plan,'custom:alpha',note='逐字保留输入＋🙂')
    return cap,source,plan,value,rebuilt


def answer(value):
    prior=original.answer(value);flow=prior['business']['flows'][0]
    actions=[{k:copy.deepcopy(v) for k,v in node.items() if k!='node_id'} for node in flow['nodes']]
    return {'schema':wire.WIRE,'binding_hash':digest(value['scope_binding']),
        'business':{'name':value['scope_binding']['name'],'flows':[{'name':flow['name'],
            'trigger':flow['trigger'],'phase_order':copy.deepcopy(actions[0]['attribution']),
            'phases':[{'kind':'steps','attribution':copy.deepcopy(actions[0]['attribution']),
                'steps':[actions[0]]}],'completion':actions[1]}],'questions':[]}}


def raw(value): return canonical_json(value).encode('utf-8')


def test_exact_preparation_keeps_source_selection_and_new_compiler_contract_separate():
    cap,source,plan,value,rebuilt=prepared()
    assert value['schema']==prep.PREPARATION and profiles.selected(value).id==profiles.FLOW_PLAN
    assert value['scope_binding']['selected_global_positions']==[0,2]
    assert value['not_sent_global_positions']==[1,3]
    payload=json.loads(rebuilt['messages'][-1]['content'])
    assert payload==value['payload'] and payload['input_claims']==value['input_claims']
    assert payload['design_note']=='逐字保留输入＋🙂'
    assert rebuilt['request_body']==raw(rebuilt['request'])
    assert hashlib.sha256(rebuilt['request_body']).hexdigest()==value['request_hash']
    assert prep.stage_spec(value)==value['generation_contract']
    assert prep.TASK!=old_prep.TASK and 'citations' in prep.TASK
    assert all(value[k] is False for k in ('can_apply','can_publish','semantic_quality_verified','aggregate_verified'))
    schema=value['generation_contract']['output_schema'];Draft202012Validator(schema).validate(answer(value))
    rendered=canonical_json(schema)
    assert 'QuoteV182' not in rendered and 'node_id' not in rendered and 'from_node' not in rendered
    assert 'evidence' not in schema['properties']
    assert native.input_policy(cap.data['execution'])==policy.evidence_policy(cap.data['execution'],native.STAGE)
    policy.validate_evidence_output(cap.data['execution'])
    with pytest.raises(ValueError):policy.declared_map_transport(cap.data['execution'])


def test_actual_native_body_and_envelope_decode_plan_before_independent_source_compilation():
    cap,source,plan,value,rebuilt=prepared();schema=value['generation_contract']['output_schema']
    messages,_,_=prepare_cloud_json(cap,rebuilt['messages'][1:],schema)
    options=native.dispatch_options(cap.data['execution'],cloud_json_options(cap.data['execution']))
    actual=native.prepare_request(messages,schema,options,native.STAGE,scope_binding=value['scope_binding'])
    assert actual['body']==rebuilt['request_body'] and actual['schema_proof']==value['generation_contract']['schema_proof']
    authored=answer(value);body=original.response(json.dumps(authored,ensure_ascii=False))
    parsed=native.parse_arguments(body,200,native.STAGE,response_complete=True,
        http_content_encoding=None,scope_binding=value['scope_binding'])
    assert parsed==authored
    projected=wire.build_projection(raw(authored),scope_binding=value['scope_binding'],
        catalog=source['catalog'],allowed_span_refs=value['allowed_span_refs'],
        context=source['context'],input_claims=value['input_claims'])
    assert projected['wire']['schema']=='report-business-flow-wire-v182'
    assert len(projected['wire']['business']['flows'][0]['nodes'])==2
    manual=wire.new_manual(projected)
    assert wire.validate_value(manual,scope_binding=value['scope_binding'],catalog=source['catalog'],
        allowed_span_refs=value['allowed_span_refs'],context=source['context'],input_claims=value['input_claims'])==manual


def test_old_preparations_requests_and_decoders_never_use_the_new_plan_compiler():
    cap,source,plan=old_scene();before=old_prep.build_preparation(cap,source,plan,'custom:alpha')
    request=old_prep.rebuild_request(before,cap,source,plan,'custom:alpha')
    _,_,_,new,_=prepared()
    assert profiles.selected(before).id==profiles.FLOW_COMPAT
    assert old_prep.build_preparation(cap,source,plan,'custom:alpha')==before
    assert old_prep.rebuild_request(before,cap,source,plan,'custom:alpha')==request
    with pytest.raises(ValueError):prep.check_preparation(before,cap,source,plan,'custom:alpha')
    with pytest.raises(ValueError):old_native.parse_arguments(original.response(json.dumps(answer(new))),200,
        old_native.STAGE,response_complete=True,http_content_encoding=None,scope_binding=new['scope_binding'])
    with pytest.raises(ValueError):native.input_policy(cap.data['execution'])


def test_closed_new_consumer_bodies_route_exact_family_and_original_nonce():
    from uuid import uuid4
    nonce=str(uuid4());body={'plan_id':str(uuid4()),'expected_plan_version':2,'plan_hash':'a'*64,
        'scope_id':'custom:alpha','revision':19,'model_id':str(uuid4()),'note':'','activity_id':nonce,
        'generation_profile':'flow-v184'}
    preview=consumer.PreviewFlowPlanBody.model_validate(body)
    family=consumer._body_family(preview)
    assert family.id==profiles.FLOW_PLAN and consumer._start_path(family).endswith('/start-flow-plan')
    start=consumer.StartFlowPlanBody.model_validate({**body,'request_id':nonce,'consent':True,
        'source_snapshot_hash':'b'*64,'preview_hash':'c'*64})
    assert consumer._body_family(start).id==profiles.FLOW_PLAN
    with pytest.raises(ValueError):consumer.StartFlowCompatBody.model_validate(start.model_dump())
    with pytest.raises(ValueError):consumer.StartFlowPlanBody.model_validate({**start.model_dump(),'request_id':str(uuid4())})
    paths={r.path for r in consumer.router.routes}
    assert consumer.PREFIX+'/preview-flow-plan' in paths and consumer.PREFIX+'/start-flow-plan' in paths


@pytest.mark.parametrize('tamper',[None,'compiler','projection','raw','missing_projection'])
def test_human_review_cold_compiles_only_new_family_and_checks_saved_projection(monkeypatch,tamper):
    from types import SimpleNamespace as NS
    from uuid import uuid4
    from fastapi import HTTPException
    from generic_agents import business_flow_review_source as source_api
    _,source,_,prepared_value,_=prepared();authored=answer(prepared_value)
    kwargs={'scope_binding':prepared_value['scope_binding'],'catalog':source['catalog'],
        'allowed_span_refs':prepared_value['allowed_span_refs'],'context':source['context'],
        'input_claims':prepared_value['input_claims']}
    projection=wire.build_projection(raw(authored),**kwargs)
    row=NS(id=str(uuid4()),producer_id=source['snapshot']['producer']['id'],model_id=str(uuid4()),
        revision=19,status='review',reserved_call_id=str(uuid4()),
        data={'preview':{'preparation':prepared_value,'source_context':source},'projection':copy.deepcopy(projection)})
    p=NS(user_id=source['snapshot']['actor_id'],workspace_id=source['snapshot']['workspace_id'],role='operator')
    saved={'body':original.response(canonical_json(authored)),
        'receipt':{'call_id':row.reserved_call_id},'capture':{'http_status':200,'capture_hash':'d'*64,
            'response_complete':True,'ready':True,'http_content_encoding':None}}
    if tamper=='compiler':row.data['projection']['compilation_manifest']['algorithm']='other-compiler'
    elif tamper=='projection':row.data['projection']['wire']['business']['flows'][0]['nodes'][0]['role']='被替换的岗位'
    elif tamper=='raw':
        changed=copy.deepcopy(authored);changed['business']['flows'][0]['completion']['responsibility']+='新版本'
        saved['body']=original.response(json.dumps(changed,ensure_ascii=False))
    elif tamper=='missing_projection':row.data['projection']=None
    before=copy.deepcopy(row.data);reads=[]
    monkeypatch.setattr(source_api.attempts,'checked_attempt',lambda *args:row)
    def read(*args,recover_saved):
        assert recover_saved is False;reads.append(1);return copy.deepcopy(saved)
    monkeypatch.setattr(source_api.runtime,'read_response',read)
    def forbidden(*args,**kwargs):raise AssertionError('Review must never reserve, recover or resend')
    monkeypatch.setattr(source_api.runtime,'admit',forbidden)
    monkeypatch.setattr(source_api.runtime,'dispatch',forbidden)
    if tamper is not None:
        with pytest.raises(HTTPException) as error:source_api.load(object(),p,row.id)
        assert error.value.status_code==409
    else:
        loaded=source_api.load(object(),p,row.id)
        assert loaded['baseline_value']==projection['wire'] and loaded['baseline_value']!=authored
        assert loaded['source_binding']['generation_profile']=='flow-v184'
        args=json.loads(saved['body'])['choices'][0]['message']['content'].encode('utf-8')
        assert loaded['source_binding']['arguments_sha256']==hashlib.sha256(args).hexdigest()
        assert source_api.diagnose(loaded,loaded['baseline_value'])['source_valid'] is True
    assert reads==[1] and row.data==before


def test_model_picker_exposes_only_explicit_public_registration_metadata(monkeypatch):
    from types import SimpleNamespace as NS
    # Match production bootstrap: setup_understanding registers on setup_api's
    # shared router while api imports it. Directly importing it first creates
    # a historical import cycle before its route decorators have run.
    from generic_agents import api as application
    from generic_agents import setup_understanding as ui_api
    cap,_,_=scene()
    cap.id='00000000-0000-4000-8000-000000000184';cap.status='published';cap.name='公开云端流程规划';cap.release_version='1.0.0'
    cap.data['execution']['private_for_test']='must-not-leak'
    db=NS(scalars=lambda query:NS(all=lambda:[cap]))
    monkeypatch.setattr(ui_api,'model',lambda *args:cap)
    p=NS(workspace_id='synthetic-workspace')
    [item]=ui_api.available_models(p,db)
    assert item['generation_profile']=='flow-v184' and item['status']=='published'
    assert item['execution']=={'provider':'qwen','model':native.MODEL,
        'report_evidence_output':cap.data['execution']['report_evidence_output']}
    assert 'secret_ref' not in item['execution'] and 'private_for_test' not in canonical_json(item)
