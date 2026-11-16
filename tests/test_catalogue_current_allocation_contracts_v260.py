"""Synthetic pure M2 envelopes, NOT original-source or paid acceptance.

The existing V213 scene supplies protocol-shaped data only. These tests keep
the old native request byte-for-byte and exercise the new authority wrapper;
they do not create a Binding, spend, call a provider or prove report semantics.
Root executes the tests. The separate PG file covers real M1/Command history.
"""
import copy
import hashlib
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import catalogue_current_allocation_v260 as current
from generic_agents import report_business_catalogue_allocation_preparation_v213 as old
from generic_agents.contracts import digest
from tests.test_report_business_catalogue_allocation_native_v213 import scene


def sealed(value, key):
    return {**value, key: digest({k: v for k, v in value.items() if k != key})}


def envelope(partial=False):
    cap, source, arguments, _ = scene(partial)
    origin = arguments['draft_origin']; metadata = origin['metadata']; snapshot = source['snapshot']
    cap.id = str(uuid4()); cap.workspace_id = snapshot['workspace_id']
    cap.content_hash = digest(cap.data)
    ref = arguments['draft_ref']
    pins = {'producer_id': metadata['producer_id'], 'consumer_id': metadata['consumer_id'],
        'source_revision': metadata['revision'], 'origin_hash': digest(origin),
        'source_context_hash': digest(source), 'source_snapshot_hash': snapshot['snapshot_hash'],
        'indexed_inputs_hash': metadata['indexed_inputs_hash'],
        'input_claims_hash': metadata['input_claims_hash'], 'input_count': metadata['input_count'],
        'materials_hash': digest(snapshot['materials']),
        'map_proofs_hash': digest({k: snapshot[k] for k in ('map_proofs', 'map_calls', 'map_job')}),
        'capture_proof_hash': digest(origin['capture_proof'])}
    admission = sealed({'schema': current.ADMISSION, 'binding_id': str(uuid4()),
        'binding_record_hash': '5' * 64,
        'source_draft_ref': {**copy.deepcopy(ref), 'id': str(uuid4()), 'version': 5},
        'source_pins': pins,
        'target_configuration': {'revision': 35, 'configuration_hash': '6' * 64},
        'working_draft_ref': copy.deepcopy(ref)}, 'admission_hash')
    value = current.build_preparation(cap, source, admission_basis=admission, **arguments)
    return cap, source, arguments, value


@pytest.mark.parametrize('partial', [False, True], ids=['complete-origin', 'partial-name-diagnostics'])
def test_outer_keeps_the_actual_native_golden_bytes_and_never_asserts_old_revision_is_current(partial):
    cap, source, arguments, value = envelope(partial)
    original = old.build_preparation(cap, source, **arguments)
    native_before = copy.deepcopy(original)
    assert current.check_preparation(value, cap, source, **arguments) == value
    assert current.native_preparation(value) == original == native_before
    expected = old.rebuild_request(original, cap, source, **arguments)
    actual = current.rebuild_request(value, cap, source, **arguments)
    assert actual == expected and actual['request_body'] == expected['request_body']
    assert actual['request_hash'] == hashlib.sha256(actual['request_body']).hexdigest()
    assert current.stage_spec(value) == old.stage_spec(original)
    assert value['current_scope']['native_request_hash'] == original['request_hash']
    assert value['current_scope']['current_scope_hash'] != original['request_hash']
    assert value['admission_basis']['target_configuration']['revision'] == 35
    assert original['producer_revision'] == source['snapshot']['producer']['revision']
    assert set(value) == {'schema', 'native_preparation', 'admission_basis',
        'current_scope', 'preparation_hash'}
    assert not {'admission_basis', 'current_scope', 'binding_id'}.intersection(original)


@pytest.mark.parametrize('fault', ['target-rebind', 'native-request', 'resealed-working-ref'])
def test_resealing_an_outer_hash_cannot_substitute_the_native_or_working_scope(fault):
    cap, source, arguments, value = envelope()
    bad = copy.deepcopy(value)
    if fault == 'target-rebind':
        bad['admission_basis']['target_configuration']['configuration_hash'] = '7' * 64
        bad['admission_basis'] = sealed(bad['admission_basis'], 'admission_hash')
    elif fault == 'native-request':
        bad['native_preparation']['request_hash'] = '8' * 64
        bad['native_preparation'] = sealed(bad['native_preparation'], 'preparation_hash')
    else:
        bad['admission_basis']['working_draft_ref']['document_hash'] = '9' * 64
        bad['admission_basis'] = sealed(bad['admission_basis'], 'admission_hash')
        bad['current_scope']['working_draft_ref'] = copy.deepcopy(bad['admission_basis']['working_draft_ref'])
        bad['current_scope']['admission_hash'] = bad['admission_basis']['admission_hash']
        bad['current_scope'] = sealed(bad['current_scope'], 'current_scope_hash')
    bad = sealed(bad, 'preparation_hash')
    with pytest.raises((HTTPException, ValueError)):
        current.check_preparation(bad, cap, source, **arguments)


def test_start_requires_one_explicit_original_activity_and_closed_sorted_scope():
    cap, _, arguments, value = envelope()
    admission = value['admission_basis']; prep = value['native_preparation']; ref = arguments['draft_ref']
    activity = str(uuid4())
    body = {'generation_profile': current.PROFILE, 'activity_id': activity,
        'draft_id': ref['id'], 'expected_draft_version': ref['version'],
        'expected_document_hash': ref['document_hash'], 'model_id': cap.id,
        'selection': {'mode': 'explicit', 'positions': [0]}, 'batch_ordinal': 0,
        'max_batch_items': 1, 'catalogue_review': {'accepted': True,
            'expected_catalogue_review_hash': prep['catalogue_review_hash']},
        'note': arguments['note'], 'binding_id': admission['binding_id'],
        'binding_record_hash': admission['binding_record_hash'],
        'expected_configuration_hash': admission['target_configuration']['configuration_hash'],
        'request_id': activity, 'preview_hash': 'a' * 64,
        'request_hash': prep['request_hash'], 'plan_hash': prep['plan_hash'],
        'batch_hash': prep['batch_hash'],
        'current_scope_hash': value['current_scope']['current_scope_hash'], 'consent': True}
    assert current.StartBody.model_validate(body).model_dump() == body
    cases = [{**body, 'request_id': str(uuid4())}, {**body, 'consent': 1},
        {**body, 'consent': False}, {**body, 'current_root': True},
        {**body, 'selection': {'mode': 'explicit', 'positions': [0, 0]}},
        {**body, 'selection': {'mode': 'explicit', 'positions': [True]}},
        {**body, 'expected_draft_version': True}]
    for bad in cases:
        with pytest.raises(ValidationError):
            current.StartBody.model_validate(bad)


def test_a_correct_looking_task_id_and_outer_envelope_are_not_a_dispatch_capability():
    cap, source, arguments, value = envelope()
    p = type('Actor', (), {'workspace_id': source['snapshot']['workspace_id'],
        'user_id': source['snapshot']['actor_id']})()
    identity = {'suggestion_id': str(uuid4()), 'job_id': str(uuid4()),
        'task_id': current.TASK_PREFIX + value['current_scope']['current_scope_hash'],
        'request_hash': value['native_preparation']['request_hash'],
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'capability_id': cap.id}
    with pytest.raises(HTTPException) as denied:
        current.require_dispatch(identity, p, cap, value['native_preparation']['allocation_evidence'])
    assert denied.value.status_code == 409
    # A denied direct call must not install an ambient proof for its next call.
    with pytest.raises(HTTPException):
        current.require_dispatch(identity, p, cap, value['native_preparation']['allocation_evidence'])
