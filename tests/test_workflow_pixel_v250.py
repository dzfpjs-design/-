"""Closed extension contracts and independent inputs, without native/DB claims."""
import copy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from generic_agents.contracts import digest, Manifest
from generic_agents.workflow_pixel_contracts_v250 import PixelPairSelection
from generic_agents.workflow_pixel_manifest_v250 import manifest, validate_manifest, RUNTIME_HASH
from generic_agents.local_changer_install import PACK, RECIPE_HASH, PIPELINE_HASH, RECIPE
from generic_agents.workflow_pixel_sources_v250 import legacy_images_allowed


def trace():
    return {'installation_id': str(uuid4()), 'pack_id': PACK, 'generation': 1, 'recipe_hash': RECIPE_HASH,
        'pipeline_hash': PIPELINE_HASH, 'weights_sha256': RECIPE['files'][0]['sha256'], 'backend': 'cpu'}


@pytest.mark.parametrize('field,value', [('pixel_correspondence_confirmed', 1), ('pixel_correspondence_confirmed', False),
    ('full_image_present_confirmed', 'true'), ('full_image_present_confirmed', False), ('before_image_id', 'not-an-original')])
def test_two_originals_require_exact_id_and_explicit_noncoerced_confirmations(field, value):
    body = {'before_image_id': str(uuid4()), 'after_image_id': str(uuid4()),
        'pixel_correspondence_confirmed': True, 'full_image_present_confirmed': True}
    with pytest.raises(ValidationError): PixelPairSelection.model_validate({**body, field: value})


@pytest.mark.parametrize('threshold', [.5, '0', '0.0', '1.0', 'NaN', '0.1234567'])
def test_model_registration_does_not_guess_threshold_or_replace_typed_token(threshold):
    with pytest.raises(HTTPException): manifest(trace(), threshold)


@pytest.mark.parametrize('damage', ['cloud', 'side_effect', 'provider', 'domain_fact', 'model_generation', 'runtime'])
def test_fixed_extension_cannot_expand_authority_or_change_actual_handler(damage):
    value = manifest(trace(), '0.5').model_dump()
    if damage == 'cloud': value['data_policy']['cloud_allowed'] = True
    elif damage == 'side_effect': value['execution']['side_effect'] = True
    elif damage == 'provider': value['execution']['input_bindings']['pair_ref'] = 'task_comparison_refs'
    elif damage == 'domain_fact': value['output_schema']['properties']['has_change'] = {'type': 'boolean'}
    elif damage == 'model_generation': value['execution']['model_trace']['generation'] = True
    elif damage == 'runtime': value['execution']['runtime_hash'] = 'f' * 64
    with pytest.raises(HTTPException): validate_manifest(None, None, Manifest.model_validate(value), live=False)


def test_registry_contract_is_general_task_provider_and_has_no_business_or_object_field_names():
    fixed = manifest(trace(), '0.500')
    validate_manifest(None, None, fixed, live=False)
    assert fixed.execution['input_bindings'] == {'task_id': 'task_identifier', 'pair_ref': 'task_pixel_pair_refs'}
    assert fixed.execution['runtime_hash'] == RUNTIME_HASH and fixed.execution['threshold_text'] == '0.500'
    assert fixed.output_schema['properties']['anomalies']['maxItems'] == 0
    assert set(fixed.output_schema['properties']['attributes']['properties']) == {
        'candidate_pixels', 'common_valid_pixels', 'unknown_pixels', 'not_authoritative'}
    assert fixed.specification['automatic_domain_use'] is False


def test_explicit_single_image_and_pair_providers_are_distinct_and_composable():
    pixel = {'execution': {'adapter': 'local_pixel_change', 'bindings': {'pair_ref': {'source': 'task_pixel_pair_refs'}}}}
    image = {'execution': {'adapter': 'local_specialist', 'bindings': {'evidence_ids': {'source': 'task_image_ids'}}}}
    release = SimpleNamespace()
    assert not legacy_images_allowed(release, {'flow': {'steps': [pixel]}})
    assert legacy_images_allowed(release, {'flow': {'steps': [pixel, image]}})
    assert not legacy_images_allowed(release, {'flow': {'steps': [{'execution': {
        'adapter': 'local_specialist', 'bindings': {'evidence_ids': {'source': 'perception_frame_ids'}}}}, pixel]}})


def test_approval_context_includes_only_explicit_pair_and_ignores_ordinary_task_version_changes():
    from generic_agents.layered_approval_store_v175 import context
    task = SimpleNamespace(id=str(uuid4()), package_id=str(uuid4()), version=1,
        data={'instance_id': str(uuid4()), 'creation_hash': 'a' * 64})
    old = context(task)
    assert 'pixel_pair_snapshot' not in old
    task.data['pixel_pair_snapshot'] = {'before': {'id': str(uuid4())}, 'after': {'id': str(uuid4())}, 'pair_hash': 'b' * 64}
    frozen = context(task); assert digest(frozen) != digest(old)
    task.version += 1
    assert context(task) == frozen
    task.data['pixel_pair_snapshot']['pair_hash'] = 'c' * 64
    assert digest(context(task)) != digest(frozen)
