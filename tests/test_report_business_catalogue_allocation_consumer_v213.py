"""Pure DTO/frozen history checks, never real paid/source/IAM acceptance.

Public synthetic protocol values reuse the native pure fixture. No successful
runtime, database permission, capture or model transport is stubbed here.
Root owns execution and real H source/paid/persistence acceptance.
"""
import base64
import copy
import hashlib
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import report_design_profiles_v178 as profiles
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_saved_map_design_runtime_v176 as runtime
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_preparation_v213 as preparation
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents.contracts import digest
from tests.test_report_business_catalogue_allocation_native_v213 import scene, candidate, raw, no_io


def frozen(partial=False):
    cap, source, kwargs, evidence = scene(partial)
    cap.id = str(uuid4()); cap.workspace_id = source['snapshot']['workspace_id']
    cap.status = 'published'; cap.content_hash = digest(cap.data)
    p = SimpleNamespace(user_id=source['snapshot']['actor_id'], workspace_id=cap.workspace_id, role='operator')
    body = {'generation_profile': consumer.ALLOCATION, 'activity_id': str(uuid4()),
        'draft_id': kwargs['draft_ref']['id'], 'expected_draft_version': kwargs['draft_ref']['version'],
        'expected_document_hash': kwargs['draft_ref']['document_hash'], 'model_id': cap.id,
        'selection': copy.deepcopy(kwargs['plan']['selection']), 'batch_ordinal': kwargs['batch_ordinal'],
        'max_batch_items': kwargs['plan']['max_batch_items'],
        'catalogue_review': {'accepted': True,
            'expected_catalogue_review_hash': kwargs['plan']['catalogue_review_hash']}, 'note': kwargs['note']}
    prep = preparation.build_preparation(cap, source, **kwargs)
    value = {'schema': consumer.ALLOCATION_PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body, 'source_context': source, 'capability': consumer._cap_value(cap),
        'revision': kwargs['draft_origin']['metadata']['revision'], 'allocation_evidence': evidence,
        'preparation': prep, 'budget': {'schema': 'report-saved-map-design-budget-v176',
            'call_limit': 1, 'token_bound': prep['input_summary']['token_bound'],
            'maximum_cost_usd': 3, 'pricing_basis_hash': 'e'*64, 'grant_hash': 'f'*64},
        **consumer.FALSE_FLAGS, 'generation_profile': consumer.ALLOCATION, 'activity_id': body['activity_id']}
    value['preview_hash'] = consumer.h(value)
    return p, value


def reseal(value):
    value['preview_hash'] = consumer.h({key: item for key, item in value.items() if key != 'preview_hash'})
    return value


@pytest.mark.parametrize('partial', [False, True])
def test_frozen_allocation_preview_and_view_use_only_original_evidence(partial, monkeypatch):
    p, value = frozen(partial)
    def forbidden(*args, **kwargs):
        raise AssertionError('Frozen history must not read current source or grant')
    monkeypatch.setattr(runtime, 'admit_allocation', forbidden)
    from generic_agents import report_business_catalogue_allocation_source_v213 as source
    monkeypatch.setattr(source, 'prepare_source', forbidden)
    assert consumer.check_preview(value, p) is value
    public = consumer.preview_view(value)
    assert public['revision'] == value['allocation_evidence']['draft_origin']['metadata']['revision']
    assert public['draft_ref']['version'] != public['revision']
    assert public['generation_profile'] == consumer.ALLOCATION
    assert public['decision_count'] == len(public['decision_keys']) == 1
    assert set(public['sent_payload']) == {'schema', 'disclosure', 'note'}
    assert not {'source_context', 'catalog', 'input_order', 'document'} & set(public['sent_payload']['disclosure'])
    assert public['call_count'] == 1 and public['can_apply'] is False
    assert public['requires_separate_authorization'] is True


@pytest.mark.parametrize(('path', 'replacement'), [
    (('body', 'expected_draft_version'), 5),
    (('body', 'expected_document_hash'), '0'*64),
    (('body', 'catalogue_review', 'expected_catalogue_review_hash'), '0'*64),
    (('body', 'catalogue_review', 'accepted'), False),
    (('body', 'batch_ordinal'), 1),
    (('body', 'max_batch_items'), 2),
    (('body', 'note'), 'changed unconsented note'),
    (('revision',), 79),
    (('activity_id',), str(uuid4())),
    (('allocation_evidence', 'name_origin'), None),
])
def test_resealed_preview_cannot_substitute_fixed_draft_batch_review_origin_or_revision(path, replacement):
    p, value = frozen(True)
    at = value
    for key in path[:-1]: at = at[key]
    at[path[-1]] = replacement
    with pytest.raises((HTTPException, ValidationError, ValueError)):
        consumer.check_preview(reseal(value), p)


@pytest.mark.parametrize('change', [
    {'expected_draft_version': True}, {'expected_draft_version': '4'},
    {'batch_ordinal': 0.0}, {'batch_ordinal': -1}, {'max_batch_items': 81},
    {'selection': {'mode': 'explicit', 'positions': [True]}},
    {'selection': {'mode': 'explicit', 'positions': [1, 0]}},
    {'selection': {'mode': 'explicit', 'positions': [0, 0]}},
    {'selection': {'mode': 'explicit', 'positions': []}},
    {'selection': {'mode': 'unassigned_and_pending', 'positions': [0]}},
    {'catalogue_review': {'accepted': 1, 'expected_catalogue_review_hash': 'a'*64}},
    {'producer_id': str(uuid4())}, {'revision': 79}, {'source_context': {}},
    {'allocation_evidence': {}}, {'current_consumer_id': str(uuid4())},
    {'generation_profile': 'catalogue-v211'}, {'note': 'x\x00y'},
])
def test_allocation_dto_is_independent_closed_and_strict(change):
    _, value = frozen()
    body = {**value['body'], **change}
    with pytest.raises(ValidationError): consumer.PreviewAllocationBody.model_validate(body)


def test_start_exact_nonce_consents_and_old_body_never_adopts_new_family():
    _, value = frozen(); body = value['body']
    start = {**body, 'request_id': body['activity_id'], 'preview_hash': value['preview_hash'],
        'plan_hash': value['preparation']['plan_hash'], 'batch_hash': value['preparation']['batch_hash'], 'consent': True}
    parsed, exact, sha = consumer.read_wire(raw(start), consumer.StartAllocationBody)
    assert parsed.request_id == parsed.activity_id
    assert exact.encode('utf-8') == raw(start) and sha == hashlib.sha256(raw(start)).hexdigest()
    for change in ({'request_id': str(uuid4())}, {'consent': 1}, {'consent': False}, {'consent': 'true'}):
        with pytest.raises(HTTPException) as error:
            consumer.read_wire(raw({**start, **change}), consumer.StartAllocationBody)
        assert error.value.status_code == 422
    with pytest.raises(ValidationError): consumer.StartIndexedBody.model_validate(start)
    assert consumer._start_path(consumer._body_family(parsed)).endswith('/start-allocation')


def test_viewer_cannot_reach_new_admission_and_missing_adapter_is_explicit_503(monkeypatch):
    p, value = frozen(); body = consumer.PreviewAllocationBody.model_validate(value['body'])
    def forbidden(*args, **kwargs): raise AssertionError('No admission may be reached')
    monkeypatch.setattr(runtime, 'admit_allocation', forbidden)
    p.role = 'viewer'
    with pytest.raises(HTTPException) as error: consumer.prepare_allocation(None, p, body)
    assert error.value.status_code == 403
    p.role = 'operator'; monkeypatch.setattr(consumer, 'runtime', lambda: SimpleNamespace())
    with pytest.raises(HTTPException) as error: consumer.prepare_allocation(None, p, body)
    assert error.value.status_code == 503


def answer_row():
    p, preview = frozen(); evidence = preview['allocation_evidence']; arguments = raw(candidate(evidence))
    call = str(uuid4()); sha = hashlib.sha256(arguments).hexdigest()
    projection = wire.build_projection(arguments, plan=evidence['plan'], batch_ordinal=evidence['batch_ordinal'])
    return SimpleNamespace(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id,
        job_id=str(uuid4()), model_id=preview['body']['model_id'], reserved_call_id=call,
        data={'preview': preview, 'projection': projection, 'manual_value': None, 'allocation_apply': None,
            'allocation_raw_arguments_b64': base64.b64encode(arguments).decode('ascii'),
            'response': {'arguments_sha256': sha, 'receipt': {'call_id': call, 'arguments_sha256': sha}}})


def test_fixed_original_utf8_projection_is_not_current_source_or_an_envelope_task_id():
    row = answer_row(); consumer._check_allocation_answer(row)
    dispatch = consumer.dispatch_identity(row)
    assert dispatch['task_id'] == 'report-business-catalogue-allocation-v213'
    assert native.TASK_ID == 'report-business-catalogue-allocation-task-v213'
    assert profiles.modules(profiles.ALLOCATION).preparation is preparation


def test_machine_view_never_becomes_a_full_design_or_exposes_server_evidence():
    row = answer_row(); row.version = 6; row.status = 'review'
    row.producer_id = row.data['preview']['source_context']['snapshot']['producer']['id']
    row.revision = row.data['preview']['revision']; row.data['error'] = None
    public = consumer.view(row)
    assert public['allocation'] == row.data['projection'] and public['allocation_apply'] is None
    assert public['result'] is None and public['manual_value'] is None and public['catalogue'] is None
    assert public['can_apply'] is False and public['semantic_quality_verified'] is False
    assert not {'allocation_evidence', 'allocation_raw_arguments_b64', 'source_context'} & set(public)


def test_history_view_keeps_frozen_business_name_after_current_draft_was_renamed(monkeypatch):
    row = answer_row(); row.version = 6; row.status = 'review'; row.data['error'] = None
    row.producer_id = row.data['preview']['source_context']['snapshot']['producer']['id']
    row.revision = row.data['preview']['revision']
    evidence = row.data['preview']['allocation_evidence']
    original = copy.deepcopy(evidence['plan']['businesses'])
    current_document = copy.deepcopy(evidence['document'])
    current_document['businesses'][0].update(name='后来已改名的新业务', purpose='后来已修改的目的')
    from generic_agents import report_business_catalogue_allocation_source_v213 as source
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical labels cannot read the current Draft')
    monkeypatch.setattr(source, 'context', forbidden)
    monkeypatch.setattr(source, 'prepare_source', forbidden)
    public = consumer.view(row)
    assert public['base_businesses'] == original
    assert public['base_businesses'] != current_document['businesses']
    assert consumer.view(row)['base_businesses'][0]['name'] == original[0]['name']
    public['base_businesses'][0]['name'] = '调用者修改返回值'
    assert evidence['plan']['businesses'] == original


@pytest.mark.parametrize(('row', 'expected'), [
    (None, False),
    (SimpleNamespace(data={'dispatch_started': False}, status='queued'), True),
    (SimpleNamespace(data={'dispatch_started': True}, status='running'), False),
    (SimpleNamespace(data={'dispatch_started': True}, status='response_unknown'), False),
    (SimpleNamespace(data={'dispatch_started': False}, status='failed'), False),
    (SimpleNamespace(data={'dispatch_started': 'false'}, status='queued'), False),
])
def test_untrusted_lock_hint_is_owner_scoped_and_skips_new_gates_for_original_calls(row, expected):
    # This deliberately unverified row is only a routing hint, not a successful
    # inventory, active source, principal, leased job or permission fixture.
    p = SimpleNamespace(user_id=str(uuid4()), workspace_id=str(uuid4()))
    job = SimpleNamespace(data={'consumer_id': str(uuid4())})
    captured = []
    class HintOnlyDb:
        no_autoflush = nullcontext()
        def scalar(self, statement):
            captured.append(statement)
            return row
    assert consumer._allocation_fresh_hint(HintOnlyDb(), p, job) is expected
    parameters = set(captured[0].compile().params.values())
    assert {p.user_id, p.workspace_id, job.data['consumer_id']} <= parameters


def test_invalid_semantics_preserve_exact_original_raw_without_a_projection():
    row = answer_row(); original = '原回答不是合法归类JSON😀'.encode('utf-8')
    sha = hashlib.sha256(original).hexdigest(); row.data['projection'] = None
    row.data['allocation_raw_arguments_b64'] = base64.b64encode(original).decode('ascii')
    row.data['response']['arguments_sha256'] = sha
    row.data['response']['receipt']['arguments_sha256'] = sha
    consumer._check_allocation_answer(row)
    assert row.data['projection'] is None and row.data['manual_value'] is None


@pytest.mark.parametrize('change', [
    'missing_raw', 'missing_marker', 'bad_base64', 'noncanonical_base64',
    'raw_hash', 'receipt_hash', 'receipt_call', 'manual', 'projection',
])
def test_original_raw_or_receipt_projection_corruption_never_becomes_history(change):
    row = answer_row()
    if change == 'missing_raw': del row.data['allocation_raw_arguments_b64']
    elif change == 'missing_marker': del row.data['allocation_apply']
    elif change == 'bad_base64': row.data['allocation_raw_arguments_b64'] = '!'
    elif change == 'noncanonical_base64': row.data['allocation_raw_arguments_b64'] += '\n'
    elif change == 'raw_hash': row.data['response']['arguments_sha256'] = '0'*64
    elif change == 'receipt_hash': row.data['response']['receipt']['arguments_sha256'] = '0'*64
    elif change == 'receipt_call': row.data['response']['receipt']['call_id'] = str(uuid4())
    elif change == 'manual': row.data['manual_value'] = {}
    elif change == 'projection': row.data['projection']['projection_hash'] = '0'*64
    with pytest.raises((consumer.InventoryError, ValueError, HTTPException)):
        consumer._check_allocation_answer(row)
