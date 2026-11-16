"""Explicit human-task routing contracts; no runtime/provider execution."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import signal_routing as r
from generic_agents.signal_routing_api import Enable, Pause
from generic_agents.contracts import digest


def identity(): return str(uuid4())


def command():
    return dict(request_id=identity(), expected_version=3, release_id=identity(), source_id=identity(),
        extractor_id=identity(), entity_ids=[identity()], business_id='business_'+'a'*16,
        max_tasks=10, reason='仅把登记异常交给人工核查', confirm_human_review_only=True,options_hash='a'*64)


@pytest.mark.parametrize('field,value', [('expected_version',True), ('max_tasks',True), ('max_tasks',101),
    ('max_tasks',0), ('confirm_human_review_only',False), ('confirm_human_review_only',1),
    ('reason',' '), ('reason','bad\x00'), ('reason','\ud800')])
def test_enable_rejects_ambiguous_or_unsafe_authorization(field,value):
    body=command();body[field]=value
    with pytest.raises(ValidationError):Enable.model_validate(body)


def test_enable_rejects_duplicate_and_unbounded_objects_or_target_override():
    for change in [lambda b:b.update(entity_ids=b['entity_ids']*2),
                   lambda b:b.update(entity_ids=[identity() for _ in range(51)]),
                   lambda b:b.update(device_action='take_photo')]:
        body=command();change(body)
        with pytest.raises(ValidationError):Enable.model_validate(body)


def test_pause_is_instance_cas_and_same_strict_reason_contract():
    b=Pause.model_validate(dict(request_id=identity(),expected_version=5,reason='暂停后不重放旧通知'))
    assert b.expected_version==5
    with pytest.raises(ValidationError):Pause.model_validate({**b.model_dump(mode='json'),'expected_version':True})


def test_command_hash_binds_actor_workspace_instance_action_path_and_exact_body():
    p=SimpleNamespace(user_id=identity(),workspace_id=identity());instance=identity();body=command()
    value=r.request_hash(p,instance,'enable',None,body)
    assert value==digest(dict(schema=r.REQUEST,workspace_id=p.workspace_id,actor_id=p.user_id,
        instance_id=instance,action='enable',route_id=None,body=body))
    for actor,workspace,target,action,route in [(identity(),p.workspace_id,instance,'enable',None),
        (p.user_id,identity(),instance,'enable',None),(p.user_id,p.workspace_id,identity(),'enable',None),
        (p.user_id,p.workspace_id,instance,'pause',identity())]:
        assert value!=r.request_hash(SimpleNamespace(user_id=actor,workspace_id=workspace),target,action,route,body)


def grant(clock):
    value=dict(id=identity(),enabled_at=clock.isoformat(),expires_at=(clock+timedelta(hours=24)).isoformat(),
        actor_id=identity(),actor_role='operator',workspace_id=identity(),instance_id=identity(),release_id=identity(),
        source_id=identity(),extractor_id=identity(),entity_ids=[identity()],business_id='business_'+'a'*16,
        max_tasks=10,reason='登记候选的人工核查',binding={})
    value['grant_hash']=digest(value)
    return value


def test_route_status_has_fixed_expiry_budget_and_pause_without_mutating_grant():
    clock=datetime(2026,10,17,tzinfo=timezone.utc);g=grant(clock)
    entry=dict(grant=g,paused_at=None,paused_by=None,pause_reason=None,pause_request_id=None,task_ids=[])
    original=copy.deepcopy(g)
    assert r.route_status(entry,clock)=='active'
    assert r.route_status(entry,clock+timedelta(hours=24))=='expired'
    entry['task_ids']=[identity() for _ in range(10)]
    assert r.route_status(entry,clock)=='exhausted'
    entry['paused_at']=clock.isoformat()
    assert r.route_status(entry,clock)=='paused' and entry['grant']==original


def test_source_identity_deduplicates_processing_versions_not_only_signal_uuid():
    row=SimpleNamespace(source_id=identity(),source_record_id='same-input',entity_id=identity(),processing_version='v1')
    first=r.source_identity(row)
    row.processing_version='v2'
    assert r.source_identity(row)==first
    row.source_record_id='different-input'
    assert r.source_identity(row)!=first


def test_original_registration_boundary_is_server_clock_not_declared_measurement_time(monkeypatch):
    clock=datetime(2026,10,17,tzinfo=timezone.utc);g=grant(clock)
    body=dict(source_id=g['source_id'],source_record_id='new',processing_version='v1',entity_id=g['entity_ids'][0],
        observed_at=(clock-timedelta(days=5)).isoformat(),business_day='2026-10-12',extractor_id=g['extractor_id'],attributes={'value':65})
    observation=SimpleNamespace(id=identity(),workspace_id=g['workspace_id'],source_id=g['source_id'],
        entity_id=g['entity_ids'][0],source_record_id='new',processing_version='v1',created_at=clock+timedelta(seconds=1),
        data=dict(original_request=body,input_hash=digest(body),extractor_id=g['extractor_id'],
            reported_rule_contract=dict(instance_id=g['instance_id'],release_id=g['release_id'])))
    event=SimpleNamespace(id=identity(),workspace_id=g['workspace_id'],kind='perception.anomaly',
        created_at=clock+timedelta(seconds=2),data=dict(observation_id=observation.id,entity_id=observation.entity_id))
    monkeypatch.setattr(r,'first_registration',lambda *a:observation.created_at)
    assert r.registration_matches(None,g,event,observation,clock+timedelta(seconds=3)) is True
    # Reprocessing an old registration after activation never becomes new input.
    monkeypatch.setattr(r,'first_registration',lambda *a:clock-timedelta(seconds=1))
    assert r.registration_matches(None,g,event,observation,clock+timedelta(seconds=3)) is False
    observation.created_at=clock-timedelta(seconds=1)
    assert r.registration_matches(None,g,event,observation,clock+timedelta(seconds=3)) is False


def test_intent_and_receipt_tampering_fail_closed():
    with pytest.raises(HTTPException):r.checked_intent({'schema':r.INTENT,'intent_hash':'a'*64})
    with pytest.raises(HTTPException):r.checked_receipt({'schema':r.DELIVERY,'receipt_hash':'a'*64})


def test_source_does_not_call_endpoint_with_mid_transaction_commit():
    import ast
    from pathlib import Path
    tree=ast.parse(Path(r.__file__).read_text())
    names=[getattr(n.func,'attr',getattr(n.func,'id','')) for n in ast.walk(tree) if isinstance(n,ast.Call)]
    assert 'create_task' in names and 'cloud_json' not in names and 'dispatch_acquisition' not in names


def test_rule_summary_is_allowlisted_and_cross_language_hash_safe():
    cap=SimpleNamespace(id=identity(),name='rules',version=2,content_hash='a'*64,data={'specification':{'rules':[
        {'id':'high','field':'公开登记值','operator':'gt','threshold':28.0,'severity':'warning','private_note':'not_shared'},
        {'id':'small','field':'登记误差','operator':'lt','threshold':1e-7}]}})
    result=r.extractor_ref(cap)
    assert result['rules']==[{'id':'high','field':'公开登记值','operator':'gt','threshold':'28.0'},
        {'id':'small','field':'登记误差','operator':'lt','threshold':'1e-07'}]
    cap.data['specification']['rules'][0]['threshold']=float('nan')
    with pytest.raises(HTTPException):r.extractor_ref(cap)


def test_resealed_incomplete_intent_cannot_escape_quarantine_validation():
    bad=r.sealed({'schema':r.INTENT,'actor_id':identity()},'intent_hash')
    with pytest.raises(HTTPException):r.checked_intent(bad)
    for damaged in [[],{'x':'bad'},'bad']:
        signal=SimpleNamespace(data={'route_failure':damaged})
        assert r.public_state(signal)['route_failure']['code']=='routing_integrity'


def test_prepare_locks_object_only_material_before_current_snapshot_check(monkeypatch):
    from generic_agents import instance_api,instance_compiler,entity_release_compat,perception_signals
    from generic_agents.models import SetupMaterial
    p=SimpleNamespace(user_id=identity(),workspace_id=identity(),role='operator')
    material_id=identity();source_id=identity();extractor_id=identity();entity_id=identity()
    instance=SimpleNamespace(id=identity(),data={'enabled_business_ids':['business_'+'a'*16]})
    business={'id':'business_'+'a'*16,'name':'人工核对','available':True,'flow':{'steps':[{'agent':'人工确认','role':'核对人'}]},'execution_mode':'confirmed_human_workflow'}
    release=SimpleNamespace(id=identity(),data={'snapshot':{'businesses':[business],'roles':{'核对人':{'user_id':p.user_id}},
        'materials':[{'id':material_id,'version':3,'hash':'b'*64}], 'object_material_ids':[material_id],'relation_material_ids':[]}})
    source=SimpleNamespace(id=source_id,version=1,name='source',content_hash='c'*64,data={})
    extractor=SimpleNamespace(id=extractor_id,version=1,name='extractor',content_hash='d'*64,
        data={'allowed_agent_roles':['perception'],'specification':{'rules':[{'id':'hi','field':'v','operator':'gt','threshold':1}]}})
    obj=SimpleNamespace(id=entity_id,version=1,data={'name':'对象'},external_id='object')
    order=[]
    def scoped(db,model,key,principal,lock=False):
        if model is SetupMaterial:
            assert key==material_id and lock is True;order.append('material-lock');return SimpleNamespace(id=key)
        return obj
    def verify(*args):
        assert order==['material-lock'];order.append('fresh-verify')
        raise HTTPException(409,'对象唯一出处已归档')
    monkeypatch.setattr(instance_api,'active',lambda *args:(instance,release))
    monkeypatch.setattr(instance_compiler,'material_ids_for_flow',lambda *args:set())
    monkeypatch.setattr(instance_compiler,'verify_material_snapshots',verify)
    monkeypatch.setattr(entity_release_compat,'require_entity',lambda *args:{'definition':'complete'})
    monkeypatch.setattr(perception_signals,'rule_contract',lambda *args:{})
    monkeypatch.setattr(r,'actor',lambda *args:p)
    monkeypatch.setattr(r,'published',lambda db,p,key,kind=None:source if key==source_id else extractor)
    monkeypatch.setattr(r,'scoped',scoped)
    with pytest.raises(HTTPException):r.prepare(None,p,instance,release.id,source_id,extractor_id,[entity_id],business['id'])
    assert order==['material-lock','fresh-verify']


def test_self_consistent_receipt_must_still_bind_its_exact_signal_intent():
    intent={'route_id':identity(),'intent_hash':'a'*64,'source_identity_hash':'b'*64,'dedup_key':'c'*64}
    signal_id=identity()
    value=r.sealed(dict(schema=r.DELIVERY,**intent,status='blocked',code='routing_not_active',message='已暂停',task_id=None,
        request_id='route_'+digest(intent['route_id']+signal_id),completed_at=datetime.now(timezone.utc).isoformat(),
        cloud_calls=0,device_actions=False,source_time_verified=False),'receipt_hash')
    assert r.checked_delivery(value,intent,signal_id)==value
    for key in ('route_id','intent_hash','source_identity_hash','dedup_key','request_id'):
        changed={k:v for k,v in value.items() if k!='receipt_hash'}
        changed[key]=identity() if key=='route_id' else 'd'*64
        wrong=r.sealed(changed,'receipt_hash')
        assert r.checked_receipt(wrong)==wrong
        with pytest.raises(HTTPException):r.checked_delivery(wrong,intent,signal_id)


def test_projection_origin_rows_are_locked_without_requiring_word_publishable(monkeypatch):
    from generic_agents import instance_api,instance_compiler,entity_release_compat,perception_signals,report_configuration_handoff
    from generic_agents.models import SetupMaterial
    p=SimpleNamespace(user_id=identity(),workspace_id=identity(),role='operator')
    origin_id,copy_id=sorted([identity(),identity()]);source_id=identity();extractor_id=identity();entity_id=identity()
    proof_id=identity();instance=SimpleNamespace(id=identity(),data={'enabled_business_ids':['business_'+'a'*16]})
    business={'id':'business_'+'a'*16,'name':'人工核对','available':True,'flow':{'steps':[{'agent':'人工确认','role':'核对人'}]},'execution_mode':'confirmed_human_workflow'}
    material={'id':copy_id,'version':3,'hash':'b'*64,'report_sources':[{'source_review_id':proof_id}]}
    release=SimpleNamespace(id=identity(),data={'snapshot':{'businesses':[business],'roles':{'核对人':{'user_id':p.user_id}},
        'materials':[material],'object_material_ids':[],'relation_material_ids':[]}})
    caps={source_id:SimpleNamespace(id=source_id,version=1,name='source',content_hash='c'*64,data={}),
        extractor_id:SimpleNamespace(id=extractor_id,version=1,name='extractor',content_hash='d'*64,data={'allowed_agent_roles':['perception'],
            'specification':{'rules':[{'id':'hi','field':'v','operator':'gt','threshold':1}]}})}
    obj=SimpleNamespace(id=entity_id,version=1,data={'name':'对象'},external_id='object');locks=[]
    def scoped(db,model,key,principal,lock=False):
        if model is SetupMaterial:assert lock is True;locks.append(key);return SimpleNamespace(id=key,status='needs_input' if key==origin_id else 'parsed')
        return obj
    def verify(db,principal,materials):
        assert locks==sorted([origin_id,copy_id]) and materials==[material]
    monkeypatch.setattr(instance_api,'active',lambda *args:(instance,release))
    monkeypatch.setattr(instance_compiler,'material_ids_for_flow',lambda *args:{copy_id})
    monkeypatch.setattr(instance_compiler,'verify_material_snapshots',verify)
    monkeypatch.setattr(entity_release_compat,'require_entity',lambda *args:{'definition':'complete'})
    monkeypatch.setattr(perception_signals,'rule_contract',lambda *args:{})
    monkeypatch.setattr(r,'actor',lambda *args:p);monkeypatch.setattr(r,'scoped',scoped)
    monkeypatch.setattr(r,'published',lambda db,p,key,kind=None:caps[key])
    def owned(db,principal,key,*,kind):
        assert key==proof_id and kind=='source_review'
        return SimpleNamespace(data={'record':{'materials':[{'id':origin_id,'role':'origin'},{'id':copy_id,'role':'confirmed_view'}]}})
    monkeypatch.setattr(report_configuration_handoff,'owned_record',owned)
    result=r.prepare(None,p,instance,release.id,source_id,extractor_id,[entity_id],business['id'])
    assert result['materials_hash']==digest([material]) and locks==sorted([origin_id,copy_id])
