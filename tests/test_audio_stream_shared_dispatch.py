"""Narrow candidate/declaration seams; fixtures are not a live gateway proof."""
import copy
from types import SimpleNamespace as N
import pytest
from fastapi import HTTPException
from generic_agents.contracts import digest
from generic_agents import observation_candidate_dispatch as candidates
from generic_agents import instance_data_receipts as receipts


def test_audio_origin_cannot_select_image_verification_by_removing_flag(monkeypatch):
    from generic_agents import audio_observation_model as audio,local_observations
    calls=[];row=N(data={'candidate_only':False,'extractor_id':'model'})
    monkeypatch.setattr(candidates,'scoped',lambda *a:N(data={'execution':{'adapter':'builtin'}}))
    monkeypatch.setattr(audio,'observation_marker',lambda *a:True)
    monkeypatch.setattr(audio,'verify_observation',lambda *a,**k:calls.append(k))
    monkeypatch.setattr(local_observations,'verify',lambda *a,**k:pytest.fail('wrong origin verifier'))
    assert candidates.verify_candidate(None,None,row,'model',execution=False) is True
    assert calls==[{'execution':False}]


def test_registered_audio_model_cannot_select_legacy_by_removing_origin_marker(monkeypatch):
    from generic_agents import audio_observation_model as audio
    row=N(data={'extractor_id':'model'});calls=[]
    monkeypatch.setattr(candidates,'scoped',lambda *a:N(data={'execution':{'adapter':audio.ADAPTER}}))
    monkeypatch.setattr(audio,'observation_marker',lambda *a:False)
    monkeypatch.setattr(audio,'verify_observation',lambda *a,**k:calls.append(a[-1]))
    assert candidates.verify_candidate(None,None,row,'model') is True
    assert calls==['model']


@pytest.fixture
def declared():
    schema={'type':'object','additionalProperties':False,'properties':{'environment_sound':{'type':'object'}},'required':['environment_sound']}
    cap=N(id='model',content_hash='a'*64,release_version='1.0.0',data={'execution':{'adapter':'local_sound_observation'},'output_schema':schema})
    binding={'business_id':'fire','model_capability_id':cap.id,'model_capability_hash':cap.content_hash,
        'model_release_version':cap.release_version,'output_schema':schema,'output_schema_hash':digest(schema),
        'step_name':'资源自动音轨','resource_id':'channel-resource','source_family':'gateway_audio_observation_candidate_v1','binding_hash':'b'*64}
    return cap,binding,{'schema':'instance-data-requirements-v1','businesses':[],'audio_sources':[binding]}


def test_resource_declaration_is_exact_not_a_synthetic_workflow_step(declared):
    cap,binding,plan=declared
    row=receipts._declarations(plan,cap,{'environment_sound':{}},audio_binding=binding)[0]
    assert row['business_id']=='fire' and row['resource_id']=='channel-resource'
    assert row['step_id'] is None and row['declaration_kind']=='resource_model'
    assert row['binding_hash']==binding['binding_hash']


@pytest.mark.parametrize('change',['missing_binding','other_business','other_resource','other_model','duplicate','removed_plan'])
def test_audio_receipt_never_falls_back_to_not_declared_or_other_scope(declared,change):
    cap,binding,plan=copy.deepcopy(declared)
    binding=copy.deepcopy(binding)
    if change=='missing_binding':binding=None
    elif change=='other_business':binding['business_id']='other'
    elif change=='other_resource':binding['resource_id']='other'
    elif change=='other_model':cap.content_hash='c'*64
    elif change=='duplicate':plan['audio_sources'].append(copy.deepcopy(binding))
    else:plan.pop('audio_sources')
    with pytest.raises(HTTPException):receipts._declarations(plan,cap,{'environment_sound':{}},audio_binding=binding)


def test_audio_destination_uses_captured_release_for_compatible_older_entity(monkeypatch):
    from generic_agents import audio_streams
    entity=N(id='object',schema_capability_id='older-package')
    release=N(id='captured-new-release',instance_id='instance')
    stream=N(id='stream',instance_id='instance',entity_id=entity.id,
        data={'definition':{'entity_definition_hash':'frozen-lineage'}})
    binding={'binding_hash':'binding','source_capability_hash':'source','model_capability_hash':'model'}
    capture={'release_id':release.id,'instance_id':stream.instance_id,'entity_id':entity.id}
    monkeypatch.setattr(receipts,'scoped',lambda db,cls,identity,p:entity if cls is receipts.Entity else release)
    monkeypatch.setattr(receipts,'_source_release',lambda *a:pytest.fail('entity origin cannot choose audio destination'))
    monkeypatch.setattr(audio_streams,'observation_marker',lambda *a:True)
    monkeypatch.setattr(audio_streams,'observation_context',lambda *a,**k:{'stream':stream,'binding':binding,'capture':capture,'evidence':N()})
    chosen,obj,contexts=receipts._destination(None,None,N(entity_id=entity.id,data={}),simulation=None)
    assert chosen is release and obj is entity
    assert contexts[0]['release_id']==release.id and contexts[0]['entity_source_package_id']=='older-package'
    assert contexts[0]['entity_definition_hash']=='frozen-lineage'
