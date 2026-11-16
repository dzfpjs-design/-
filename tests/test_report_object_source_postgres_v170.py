"""Root-run authenticated PG source journeys, with public synthetic IO only.

Normal V166/V169 commands create the human source; source-review, OCR page and
whole-copy confirmation, position mapping and proof stores remain real. Only
the imported public document/OCR/native HTTP fixture supplies synthetic input.
No original report quality, source meaning, schema or execution is approved.
Root alone executes; no successful row, proof or validator result is invented.
"""
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from generic_agents.contracts import digest
from tests import test_material_ocr as ocr_test
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v7_postgres import v7_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_region_source_postgres import full_source_scene, confirm_page
from tests.test_report_full_semantic_source_review_postgres import command as review_command
from tests.test_report_object_adoption_postgres_v169 import (
    confirmed_scene, confirm_revision, commit, assert_paid_unchanged, PRIVATE_NOTE,
)


SOURCE_ROOT = '/api/setup/full-semantic-source-reviews/'
REGION_ROOT = '/api/setup/full-semantic-region-mappings/'
REVIEW_NOTE = 'PRIVATE_V170_SOURCE_REVIEW_NOTE_NOT_IN_PUBLIC_PROOF_末字🙂'


@pytest.fixture
def object_source_scene(confirmed_scene, monkeypatch):
    """A real normal adoption of explicitly human-authored identity proposals."""
    fence(); scene = {**confirmed_scene}; client, report_id = scene['client'], scene['report_id']
    value = copy.deepcopy(scene['value'])
    original = value['result']['objects'][-1]
    second = copy.deepcopy(original); second['name'] = '人工待核补充记录'
    second['fields'] = [copy.deepcopy(second['fields'][1])]
    second['business_identity'] = {'status': 'not_established', 'field_labels': [], 'citations': [],
        'explanation': '人工结构稿尚未明确业务标识，不能认定原文没有标识。'}
    value['result']['objects'].append(second)
    original['business_identity'] = {'status': 'referenced',
        'field_labels': [original['fields'][0]['label']], 'citations': [{'span_ref': 'w:p1/s1'}],
        'explanation': '人工关联现有字段及原片段，含义与业务唯一性仍待核对，不代表原文明示。'}
    value['object_identity_confirmations'] = [{'object_name': obj['name'], 'acknowledge_platform_identity': True}
        for obj in reversed(value['result']['objects'])]
    value['note'] += 'V170私人结构补充'
    confirmed = confirm_revision(client, report_id, value)
    scene.update(value=value, confirmed=confirmed)
    _, _, body, raw, accepted = commit(client, report_id)
    return {'client': client, 'scene': scene, 'record': accepted['record'], 'adopted': accepted,
        'value': value, 'report_id': report_id, 'path': SOURCE_ROOT + accepted['record']['id'],
        'adoption_command': body, 'adoption_wire': raw}


def source_state(seed):
    return reply(seed['client'].get(seed['path']))


def owner_of(state, kind, *, status=None):
    return next(item for item in state['manifest']['owners'] if item['kind'] == kind
        and (status is None or item['evidence_status'] == status))


def assert_no_public_private_values(*values):
    for value in values:
        text = json.dumps(value, ensure_ascii=False)
        assert PRIVATE_NOTE not in text and REVIEW_NOTE not in text


def start_mapping(seed):
    client = seed['client']; path = REGION_ROOT + seed['record']['id']
    state = reply(client.get(path)); options = reply(client.get(path + '/source-options'))
    option = next(item for item in options['copies'] if item['selection'])
    command = {'schema': 'report-full-semantic-region-session-command-v1', 'request_id': str(uuid4()),
        'manifest_hash': state['manifest']['manifest_hash'], 'selection': option['selection']}
    saved = reply(client.post(path + '/sessions', json=command))
    assert reply(client.post(path + '/sessions', json=command)) == saved
    return path, state, option, saved['session_id']


def published_proof(seed):
    """Create and cold-read an actual normally published new-family position proof.

    The supplied object_source_scene has an unconfirmed two-page public copy.
    No schema/compiler is called here. Returned ctx information is actual HTTP
    output, suitable for a later independent normal schema-adoption journey.
    """
    client = seed['client']
    for page in range(1, seed['scene']['page_count'] + 1):
        confirm_page(seed, page)
    ocr_test.confirm_document(client, seed['scene']['copy'])
    path, state, option, session_id = start_mapping(seed)
    assert option['whole_document_confirmed'] is True
    assert state['manifest']['schema'] == 'report-object-source-manifest-v170'
    pre = {'schema': 'report-full-semantic-region-proof-preview-command-v1', 'session_id': session_id,
        'manifest_hash': state['manifest']['manifest_hash'],
        'configuration_revision': state['configuration']['revision'], 'configuration_hash': state['configuration']['hash']}
    refs = state['manifest']['refs']; assert refs
    for index, ref in enumerate(refs):
        if index == len(refs) - 1:
            incomplete = client.post(path + '/proof-preview', json=pre)
            assert incomplete.status_code == 409 and incomplete.json()['detail']['code'] == 'full_region_incomplete'
        found = reply(client.get(path + '/sessions/' + session_id + '/candidates',
            params={'ref_hash': ref['ref_hash']}))
        assert found['candidates'], ref['path']
        mapping = found['candidates'][0]
        for page in found['pages']:
            original = client.get(page['image_url'])
            assert original.status_code == 200 and hashlib.sha256(original.content).hexdigest() == page['image_sha256']
        body = {'schema': 'report-full-semantic-region-entry-command-v1', 'request_id': str(uuid4()),
            'ref_hash': ref['ref_hash'], 'expected_version': 0, 'mapping': mapping,
            'image_acknowledgements': [segment['unit_id'] for segment in mapping['segments']],
            'privacy_acknowledged': True}
        saved = reply(client.post(path + '/sessions/' + session_id + '/entries', json=body))
        assert reply(client.get(path + '/requests/' + body['request_id']))['receipt'] == saved['receipt']
    preview = reply(client.post(path + '/proof-preview', json=pre))
    proof = preview['proof']
    assert preview['schema'] == 'report-object-region-proof-preview-v170'
    assert proof['schema'] == 'report-object-region-proof-v170'
    assert preview['preview_hash'] == digest({'schema': 'report-object-region-proof-preview-hash-v170', 'proof': proof})
    assert [item['ref_hash'] for item in proof['mappings']] == [ref['ref_hash'] for ref in refs]
    assert proof['pending_semantic_owner_paths'] == [owner['path'] for owner in state['manifest']['owners']]
    assert proof['can_confirm'] is proof['can_publish'] is False
    command = {**pre, 'schema': 'report-full-semantic-region-proof-command-v1', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True}
    old_hash = {**command, 'request_id': str(uuid4()),
        'preview_hash': digest({'schema': 'report-full-semantic-region-proof-preview-hash-v1', 'proof': proof})}
    assert client.post(path + '/proofs', json=old_hash).status_code == 409
    accepted = reply(client.post(path + '/proofs', json=command))
    assert reply(client.post(path + '/proofs', json=command)) == accepted
    proof_id = accepted['receipt']['resource_id']
    public = reply(client.get(path + '/proofs/' + proof_id))
    assert public['current_source_verified'] is True and public['proof'] == proof
    assert reply(client.get(path + '/requests/' + command['request_id']))['receipt'] == accepted['receipt']
    assert_no_public_private_values(preview, proof, accepted, public)
    return {'client': client, 'path': path, 'state': state, 'manifest': state['manifest'],
        'session_id': session_id, 'preview': preview, 'command': command, 'accepted': accepted,
        'public': public, 'proof_id': proof_id, 'proof': proof,
        'configuration': reply(client.get('/api/setup/draft'))}


def test_complete_identity_inventory_and_ack_evidence_uses_top_level_confirmations(object_source_scene):
    seed = object_source_scene; state = source_state(seed); manifest = state['manifest']
    assert state['active'] is True and manifest['schema'] == 'report-object-source-manifest-v170'
    assert manifest['design_hash'] == seed['record']['design']['design_hash']
    assert state['can_confirm'] is state['can_publish'] is False
    owners = {owner['path']: owner for owner in manifest['owners']}
    for index, obj in enumerate(seed['value']['result']['objects']):
        path = '/objects/' + str(index) + '/business_identity'
        assert owners[path]['kind'] == 'business_identity'
        evidence = reply(seed['client'].get(seed['path'] + '/evidence', params={'owner_path': path}))
        assert evidence['owner']['value'] == obj['business_identity']
        assert digest(evidence['owner']['value']) == owners[path]['value_hash']
        assert all(field['required'] is False for field in obj['fields'])
    for index, confirmation in enumerate(seed['value']['object_identity_confirmations']):
        path = '/object_identity_confirmations/' + str(index)
        evidence = reply(seed['client'].get(seed['path'] + '/evidence', params={'owner_path': path}))
        assert evidence['owner']['value'] == confirmation
        assert evidence['owner']['evidence_status'] == 'context' and evidence['references'] == []
        assert evidence['can_confirm'] is evidence['can_publish'] is False
    assert_no_public_private_values(manifest)
    assert_paid_unchanged(seed['scene'])


@pytest.mark.parametrize('kind,status', [('business_identity', 'unknown'), ('platform_identity_confirmation', 'context')])
def test_unknown_or_human_context_cannot_be_source_checked_but_private_progress_is_durable(object_source_scene, kind, status):
    seed = object_source_scene; client, path = seed['client'], seed['path']
    state = source_state(seed); owner = owner_of(state, kind, status=status)
    checked = review_command(state, owner, decision='checked', note=REVIEW_NOTE)
    assert client.post(path + '/entries', json=checked).status_code == 422
    assert reply(client.get(path + '/requests/' + checked['request_id']))['found'] is False
    pending = review_command(state, owner, decision='pending', note=REVIEW_NOTE)
    saved = reply(client.post(path + '/entries', json=pending))
    assert reply(client.post(path + '/entries', json=pending))['receipt'] == saved['receipt']
    original = reply(client.get(path + '/requests/' + pending['request_id']))
    assert original['found'] and original['entry']['value'] == pending['value']
    revised = review_command(state, owner, version=1, decision='needs_change', note=REVIEW_NOTE + '修订说明')
    reply(client.post(path + '/entries', json=revised))
    stale = review_command(state, owner, decision='pending', note='保留本机草稿')
    denied = client.post(path + '/entries', json=stale)
    assert denied.status_code == 409 and denied.json()['detail']['code'] == 'source_review_conflict'
    history = reply(client.get(path + '/history', params={'owner_path': owner['path']}))['items']
    assert [item['version'] for item in history] == [1, 2]
    assert history[0]['value'] == pending['value'] and history[1]['value'] == revised['value']
    original = reply(client.get(path + '/requests/' + pending['request_id']))
    assert original['receipt'] == saved['receipt'] and original['current_matches'] is False
    reopened = source_state(seed)
    latest = next(item for item in reopened['reviews'] if item['owner_path'] == owner['path'])
    assert latest['value'] == revised['value'] and latest['version'] == 2
    assert_no_public_private_values(reopened['manifest'])
    assert_paid_unchanged(seed['scene'])


def test_referenced_business_identity_keeps_real_citations_and_private_check_grants_no_source_approval(object_source_scene):
    seed = object_source_scene; state = source_state(seed)
    owner = owner_of(state, 'business_identity', status='not_declared')
    assert owner['ref_hashes'] and 'identity_claim_requires_review' in owner['pending_reasons']
    evidence = reply(seed['client'].get(seed['path'] + '/evidence', params={'owner_path': owner['path']}))
    assert [item['ref_hash'] for item in evidence['references']] == owner['ref_hashes']
    assert evidence['owner']['value']['status'] == 'referenced'
    assert all(item['quote'] and item['owner_path'] == owner['path'] for item in evidence['references'])
    body = review_command(state, owner, decision='checked', note=REVIEW_NOTE)
    reply(seed['client'].post(seed['path'] + '/entries', json=body))
    reopened = source_state(seed)
    assert reopened['can_confirm'] is reopened['can_publish'] is False
    assert next(item for item in reopened['manifest']['owners'] if item['path'] == owner['path']) == owner
    assert_no_public_private_values(reopened['manifest'])
    assert_paid_unchanged(seed['scene'])


def test_all_actual_identity_references_publish_new_position_proof_with_every_owner_still_pending(object_source_scene):
    seed = object_source_scene; state = source_state(seed)
    owner = owner_of(state, 'platform_identity_confirmation')
    note = review_command(state, owner, decision='pending', note=REVIEW_NOTE)
    reply(seed['client'].post(seed['path'] + '/entries', json=note))
    result = published_proof(seed)
    identity_refs = [ref for ref in result['manifest']['refs'] if '/business_identity/' in ref['path']]
    assert identity_refs and set(ref['ref_hash'] for ref in identity_refs) <= {
        item['ref_hash'] for item in result['proof']['mappings']}
    pending = result['proof']['pending_semantic_owner_paths']
    assert all(item['path'] in pending for item in result['manifest']['owners']
        if item['kind'] in {'business_identity', 'platform_identity_confirmation'})
    assert result['configuration'] == seed['adopted']['configuration']
    assert REVIEW_NOTE in json.dumps(source_state(seed)['reviews'], ensure_ascii=False)
    assert_no_public_private_values(result['public'])
    assert_paid_unchanged(seed['scene'])


def test_replacement_keeps_old_source_history_but_old_manifest_cannot_authorize_new_saves(object_source_scene):
    seed = object_source_scene; client = seed['client']; state = source_state(seed)
    owner = owner_of(state, 'platform_identity_confirmation')
    body = review_command(state, owner, decision='pending', note=REVIEW_NOTE)
    saved = reply(client.post(seed['path'] + '/entries', json=body))
    target = {'handoff_id': seed['record']['id'], 'record_hash': seed['record']['record_hash'],
        'design_hash': seed['record']['design']['design_hash']}
    _, _, _, _, next_adoption = commit(client, seed['report_id'], target=target)
    old = source_state(seed)
    assert old['active'] is False and old['manifest'] == state['manifest']
    assert reply(client.get(seed['path'] + '/requests/' + body['request_id']))['receipt'] == saved['receipt']
    stale = review_command(old, owner, version=1, decision='needs_change', note='旧版本本机进度应保留')
    assert client.post(seed['path'] + '/entries', json=stale).status_code == 409
    new_path = SOURCE_ROOT + next_adoption['record']['id']
    current = reply(client.get(new_path))
    assert current['active'] is True and current['manifest']['manifest_hash'] != state['manifest']['manifest_hash']
    assert client.post(new_path + '/entries', json=stale).status_code == 409
    assert reply(client.get(seed['path'] + '/history', params={'owner_path': owner['path']}))['items'][0]['value'] == body['value']
    assert_no_public_private_values(next_adoption, current['manifest'])
    assert_paid_unchanged(seed['scene'])
