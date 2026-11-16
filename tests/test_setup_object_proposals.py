"""Grounded initialization; existing human schemas and v1 paid contracts stay intact."""
import copy
import pytest
from generic_agents.contracts import digest
from generic_agents.setup_understanding import Result, validate_result
from generic_agents.setup_object_proposals import validate_objects, apply_objects, unit_declared

BLOCKS=[{'material_id':'report','block_id':'table:1','text':'设备编号：文本，必填。温度：数值，单位℃。振动：数值，未说明单位。'}]
def citation():return {'material_id':'report','block_id':'table:1','quote':BLOCKS[0]['text']}
def objects():
    return [{'name':'设备','citations':[citation()],'inference_reason':'', 'fields':[
        {'label':'设备编号','kind':'文本','required':True,'unit':None,'citations':[citation()],'inference_reason':''},
        {'label':'温度','kind':'数值','required':False,'unit':'℃','citations':[citation()],'inference_reason':''},
        {'label':'振动','kind':'数值','required':False,'unit':None,'citations':[citation()],'inference_reason':''}]}]

def test_legacy_result_schema_hash_is_frozen():
    assert digest(Result.model_json_schema()) == 'cbcd8e831e440b842684216f0835bbb7652e93dff29590858f40ea6282839ce6'

def test_object_fields_validate_known_and_unknown_units():
    value=validate_objects(objects(),BLOCKS)
    assert value[0]['fields'][1]['unit']=='℃' and value[0]['fields'][2]['unit'] is None

@pytest.mark.parametrize('change', ['invented_unit','unrelated_unit','bad_quote','no_basis','duplicate','no_identifier','text_unit'])
def test_invalid_grounding_never_becomes_schema(change):
    value=objects();field=value[0]['fields'][2]
    if change=='invented_unit':field['unit']='mm'
    if change=='unrelated_unit':field.update(unit='℃',citations=[{**citation(),'quote':'温度：数值，单位℃。'}])
    if change=='bad_quote':field['citations'][0]['quote']='不存在的引用'
    if change=='no_basis':field.update(citations=[],inference_reason='')
    if change=='duplicate':value[0]['fields'].append(copy.deepcopy(field))
    if change=='no_identifier':value[0]['fields'][0]['required']=False
    if change=='text_unit':value[0]['fields'][0]['unit']='℃'
    with pytest.raises(ValueError):validate_objects(value,BLOCKS)

def test_cumulative_batches_cannot_drop_or_silently_change_declared_fields():
    old=objects();new=objects();new[0]['fields'].pop()
    with pytest.raises(ValueError):validate_objects(new,BLOCKS,old)
    new=objects();new[0]['fields'][1]['unit']=None
    with pytest.raises(ValueError):validate_objects(new,BLOCKS,old)

def test_new_source_may_resolve_previously_unknown_unit():
    old=objects();new=objects();new[0]['fields'][2].update(unit='mm',citations=[{'material_id':'report','block_id':'table:2','quote':'振动：单位mm'}])
    assert validate_objects(new,[*BLOCKS,{'material_id':'report','block_id':'table:2','text':'振动：单位mm'}],old)[0]['fields'][2]['unit']=='mm'

def test_explicit_object_application_preserves_manual_objects_and_original_proposal():
    config={'instanceObjects':[{'name':'人工对象','fields':[{'label':'人工编号','kind':'文本','required':True}]}]}
    before=copy.deepcopy(config);suggestions=objects()
    out=apply_objects(config,suggestions,['设备'],analysis_id='analysis',result_hash='result')
    assert config==before and out['instanceObjects'][0]==before['instanceObjects'][0]
    assert out['instanceObjects'][1]['fields'][1]=={'label':'温度','kind':'数值','required':False,'unit':'℃'}
    assert out['instanceObjectOrigins']['设备']['object']==suggestions[0]
    suggestions[0]['fields'][1]['unit']=None
    assert out['instanceObjectOrigins']['设备']['object']['fields'][1]['unit']=='℃'

def test_same_named_manual_object_or_legacy_candidate_cannot_be_overwritten():
    for config in [{'instanceObjects':[{'name':'设备','fields':[{'label':'人工编号','kind':'文本','required':True}]}]},
                   {'instanceProfile':{'objectCandidates':['设备']}}]:
        before=copy.deepcopy(config)
        with pytest.raises(ValueError):apply_objects(config,objects(),['设备'],analysis_id='a',result_hash='h')
        assert config==before

def test_nonselected_objects_are_not_applied():
    assert apply_objects({},objects(),[],analysis_id='a',result_hash='h')=={}

@pytest.mark.parametrize('quote',[
    '温度：数值，单位℃。振动：数值，单位未明确。',
    '振动单位不是℃', '振动单位可能为℃', '振动单位℃或K',
    '环境振动单位℃', '振动：23℃', '振动参考温度单位℃', '振动单位℃/min',
    '振动：单位为℃，但尚未确定','振动：单位为℃，尚未核定','振动：单位为℃，不应采用',
    '振动单位℃。振动单位尚未明确','振动单位℃\n振动单位K',
])
def test_substring_negation_example_other_field_and_compound_unit_are_not_declarations(quote):
    assert not unit_declared('振动','℃',quote)

@pytest.mark.parametrize('quote',['温度：数值，非必填，单位℃。','温度（℃）','温度\t数值\t否\t℃','温度 | 数值 | 否 | ℃',
    '温度 | 数值 | 非必填 | 温度单位为℃ | 单位明确为℃，不转换单位'])
def test_positive_field_metadata_and_table_cell_declarations(quote):
    assert unit_declared('温度','℃',quote)
