"""Zero-call observations of an OPEN object-identity design gap.

These passing tests deliberately establish the current limitation: an explicit
display-only optional definition is rejected by the old object models, while
changing its required flag satisfies their structural gate. They are not a fix,
a new semantic rubric, or evidence that an AI understood a real report. No old
answer/score is loaded, and no DB, gateway, worker, or model call is performed.
The registration checks cover actual DTOs and the actual route AST only; they
do not claim an Entity was durably registered.
"""
import ast
from copy import deepcopy
import inspect
import textwrap

from pydantic import ValidationError
import pytest

from generic_agents.contracts import EntityCreate
from generic_agents.instance_api import ObjectCreate, add_object
from generic_agents.instance_compiler import ObjectType
from generic_agents.report_full_reading_semantics import FullObject
from generic_agents.setup_object_proposals import ProposedObject, apply_objects, validate_objects


TEXT = '本合成资料维护巡护区记录；展示备注是选填文本，仅供阅读，不作为业务编号。'
BLOCK = {'material_id': 'synthetic-identity-observation', 'block_id': 'body:1', 'text': TEXT}
MODELS = [FullObject, ProposedObject, ObjectType]
MODEL_IDS = ['full-private-candidate', 'grounded-proposal', 'compiled-object-definition']


def definition(model, *, required=False):
    field = {'label': '展示备注', 'kind': '文本', 'required': required, 'unit': None}
    value = {'name': '巡护区记录', 'fields': [field]}
    if model is not ObjectType:
        citation = ({'source_ref': 'w:p1', 'quote': TEXT} if model is FullObject else
            {'material_id': BLOCK['material_id'], 'block_id': BLOCK['block_id'], 'quote': TEXT})
        field.update(citations=[deepcopy(citation)], inference_reason='')
        value.update(citations=[deepcopy(citation)], inference_reason='')
    return value


@pytest.mark.parametrize('model', MODELS, ids=MODEL_IDS)
def test_current_models_reject_explicitly_optional_display_only_object(model):
    """GREEN means the old gap still exists, not that the definition is wrong."""
    value = definition(model); before = deepcopy(value)
    with pytest.raises(ValidationError) as error:
        model.model_validate(value)
    errors = error.value.errors(include_url=False)
    assert any(item['loc'] == () and item['type'] == 'value_error'
        and '必填文本' in item['msg'] for item in errors)
    assert value == before and value['fields'][0]['required'] is False
    assert [field['label'] for field in value['fields']] == ['展示备注']


@pytest.mark.parametrize('model', MODELS, ids=MODEL_IDS)
def test_current_required_flag_gate_is_not_proof_of_business_identity(model):
    """The unsupported promotion passes structure; this is NOT a good answer."""
    value = definition(model, required=True); before = deepcopy(value)
    accepted = model.model_validate(value).model_dump()
    assert accepted['fields'][0]['required'] is True
    assert accepted['fields'][0]['label'] == '展示备注'
    assert value == before
    if model is not ObjectType:
        assert accepted['fields'][0]['citations'][0]['quote'] == TEXT


def test_actual_normalizer_does_not_repair_or_silently_change_the_required_flag():
    optional = definition(ProposedObject); original = deepcopy(optional)
    with pytest.raises(ValidationError):
        validate_objects([optional], [BLOCK])
    assert optional == original
    promoted = definition(ProposedObject, required=True); before = deepcopy(promoted)
    # Literal citation checks prove the quote exists, not the requiredness or
    # identity interpretation. Keep that existing distinction observable.
    assert validate_objects([promoted], [BLOCK]) == [promoted]
    assert promoted == before


def test_independent_declared_identifier_preserves_optional_display_through_application():
    text = '区域编号为必填文本。展示备注为选填文本，不是业务标识。'
    citation = {'material_id': BLOCK['material_id'], 'block_id': 'body:2', 'quote': text}
    value = {'name': '巡护区记录', 'citations': [citation], 'inference_reason': '', 'fields': [
        {'label': '区域编号', 'kind': '文本', 'required': True, 'unit': None,
            'citations': [citation], 'inference_reason': ''},
        {'label': '展示备注', 'kind': '文本', 'required': False, 'unit': None,
            'citations': [citation], 'inference_reason': ''}]}
    normalized = validate_objects([value], [{**BLOCK, 'block_id': 'body:2', 'text': text}])
    config = {'instanceObjects': []}; before = deepcopy(config)
    applied = apply_objects(config, normalized, [value['name']], analysis_id='synthetic-analysis',
        result_hash='synthetic-result-not-a-durable-receipt')
    compiled = ObjectType.model_validate(applied['instanceObjects'][0]).model_dump()
    assert config == before and value['fields'][1]['required'] is False
    assert [(field['label'], field['required']) for field in compiled['fields']] == [
        ('区域编号', True), ('展示备注', False)]
    assert applied['instanceObjectOrigins'][value['name']]['object'] == normalized[0]


@pytest.mark.parametrize('attributes', [{}, {'display_note': '仅供显示，不是编号'}], ids=['absent-display', 'present-display'])
def test_registration_dtos_keep_record_identity_separate_from_optional_attributes(attributes):
    original = deepcopy(attributes)
    body = ObjectCreate(release_id='synthetic-release', object_type='synthetic-object', attributes=attributes)
    assert body.reference == '' and body.attributes == original
    entity = EntityCreate(package_id='synthetic-package', entity_type=body.object_type,
        external_id='auto_synthetic_technical_record', attributes=body.attributes)
    assert entity.external_id == 'auto_synthetic_technical_record'
    assert entity.attributes == original and 'external_id' not in entity.attributes
    assert attributes == original


def test_actual_registration_route_uses_explicit_reference_or_server_generated_record_identity():
    """Source-path observation; no replacement or execution of route validators."""
    function = ast.parse(textwrap.dedent(inspect.getsource(add_object))).body[0]
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'EntityCreate']
    assert len(calls) == 1
    keywords = {item.arg: item.value for item in calls[0].keywords}
    canonical = lambda node: ast.dump(node, include_attributes=False)
    expression = lambda value: canonical(ast.parse(value, mode='eval').body)
    assert canonical(keywords['attributes']) == expression('body.attributes')
    assert canonical(keywords['external_id']) == expression("body.reference.strip() or 'auto_'+uid()")
    # This is independent of any field value in attributes. The source does
    # not need to pretend the display text is a business reference.
    assert not any(isinstance(node, ast.Subscript) for node in ast.walk(keywords['external_id']))
