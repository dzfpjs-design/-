"""Root-run synthetic PG user path; real stores/authority, synthetic IO only."""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SetupDraft, Task, CloudCall
from generic_agents.report_full_semantic_region_models import FullSemanticRegionEntry as Entry, FullSemanticRegionProof as Proof
from generic_agents.contracts import digest
from tests.test_report_full_semantic_source_review_postgres import source_scene, v7_scene, reading_scene, full_source_scene, local_runtime
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_region_source_postgres import full_source_scene, source_scene2, confirm_page
from tests import test_material_ocr as ocr_test

ROOT = '/api/setup/full-semantic-region-mappings/'


def started(seed):
    c = seed['client']; path = ROOT + seed['record']['id']; state = reply(c.get(path))
    options = reply(c.get(path + '/source-options'))
    option = next(item for item in options['copies'] if item['selection'])
    command = {'schema': 'report-full-semantic-region-session-command-v1', 'request_id': str(uuid4()),
        'manifest_hash': state['manifest']['manifest_hash'], 'selection': option['selection']}
    accepted = reply(c.post(path + '/sessions', json=command)); sid = accepted['session_id']
    return c, path, state, option, command, accepted, sid


def choice(c, path, state, sid):
    ref = next(ref for ref in state['manifest']['refs'] if ref['source_ref'] == 'o:p1')
    found = reply(c.get(path + '/sessions/' + sid + '/candidates', params={'ref_hash': ref['ref_hash']}))
    assert found['candidates'] and found['pages'] and found['source_quote']
    mapping = found['candidates'][0]
    command = {'schema': 'report-full-semantic-region-entry-command-v1', 'request_id': str(uuid4()),
        'ref_hash': ref['ref_hash'], 'expected_version': 0, 'mapping': mapping,
        'image_acknowledgements': [seg['unit_id'] for seg in mapping['segments']], 'privacy_acknowledged': True}
    return found, command


def test_partial_positions_are_durable_nonce_cas_history_and_never_formal(source_scene):
    c, path, state, option, start, accepted, sid = started(source_scene)
    assert reply(c.post(path + '/sessions', json=start)) == accepted
    assert accepted['receipt']['session_id'] == sid
    with Session(engine()) as db:
        config = copy.deepcopy(db.scalar(select(SetupDraft)).data); tasks = db.query(Task).count(); calls = db.query(CloudCall).count()
    found, body = choice(c, path, state, sid)
    import hashlib
    for page in found['pages']:
        image = c.get(page['image_url']); assert image.status_code == 200
        assert hashlib.sha256(image.content).hexdigest() == page['image_sha256']
    target = path + '/sessions/' + sid
    bad = {**body, 'image_acknowledgements': [str(uuid4())]}
    assert c.post(target + '/entries', json=bad).status_code == 422
    saved = reply(c.post(target + '/entries', json=body))
    assert reply(c.post(target + '/entries', json=body)) == saved
    recovered = reply(c.get(path + '/requests/' + body['request_id']))
    assert recovered['found'] and recovered['receipt'] == saved['receipt']
    stale = {**body, 'request_id': str(uuid4())}
    assert c.post(target + '/entries', json=stale).json()['detail']['code'] == 'full_region_conflict'
    forged = copy.deepcopy(body); forged['privacy_acknowledged'] = False
    assert c.post(target + '/entries', json=forged).json()['detail']['code'] == 'full_region_nonce_conflict'
    second = {**body, 'request_id': str(uuid4()), 'expected_version': 1}
    reply(c.post(target + '/entries', json=second))
    history = reply(c.get(target + '/history', params={'ref_hash': body['ref_hash']}))
    assert [item['version'] for item in history['entries']] == [1, 2]
    reopened = reply(c.get(target))
    assert reopened['entries'][0]['version'] == 2 and reopened['entries'][0]['mapping'] == body['mapping']
    assert reopened['current'] and not reopened['formal_source_ready']
    # A different explicit session cannot consume the same entry nonce.
    another = reply(c.post(path + '/sessions', json={**start, 'request_id': str(uuid4())}))['session_id']
    assert c.post(path + '/sessions/' + another + '/entries', json=body).json()['detail']['code'] == 'full_region_nonce_conflict'
    preview = {'schema': 'report-full-semantic-region-proof-preview-command-v1', 'session_id': sid,
        'manifest_hash': state['manifest']['manifest_hash'], 'configuration_revision': state['configuration']['revision'],
        'configuration_hash': state['configuration']['hash']}
    assert c.post(path + '/proof-preview', json=preview).status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(SetupDraft)).data == config and db.query(Task).count() == tasks and db.query(CloudCall).count() == calls
        assert db.query(Entry).count() == 2 and db.query(Proof).count() == 0


def test_audit_failure_rolls_back_position_and_nonce(source_scene, monkeypatch):
    from generic_agents import report_full_semantic_region_mapping as module
    c, path, state, _, _, _, sid = started(source_scene)
    _, body = choice(c, path, state, sid)
    def broken(*args, **kwargs):
        raise RuntimeError('synthetic audit refusal')
    monkeypatch.setattr(module, 'audit', broken)
    with pytest.raises(RuntimeError, match='synthetic audit refusal'):
        c.post(path + '/sessions/' + sid + '/entries', json=body)
    assert not reply(c.get(path + '/requests/' + body['request_id']))['found']
    assert reply(c.get(path + '/sessions/' + sid))['entries'] == []


def test_all_occurrences_public_position_proof_needs_new_explicit_confirmed_copy(source_scene2):
    seed = source_scene2
    c, path, state, _, start, _, old_sid = started(seed)
    _, old_body = choice(c, path, state, old_sid)
    first = reply(c.post(path + '/sessions/' + old_sid + '/entries', json=old_body))
    with Session(engine()) as db:
        config = copy.deepcopy(db.scalar(select(SetupDraft)).data)
    for page in (1, 2):
        confirm_page(seed, page)
    ocr_test.confirm_document(c, seed['scene']['copy'])
    stale = reply(c.get(path + '/sessions/' + old_sid))
    assert not stale['current'] and not stale['formal_source_ready'] and stale['entries'][0]['mapping'] == old_body['mapping']
    assert reply(c.post(path + '/sessions/' + old_sid + '/entries', json=old_body)) == first
    newer = {**old_body, 'request_id': str(uuid4()), 'expected_version': 1}
    assert c.post(path + '/sessions/' + old_sid + '/entries', json=newer).json()['detail']['code'] == 'full_region_stale'
    assert not reply(c.get(path + '/requests/' + newer['request_id']))['found']
    assert reply(c.get(path + '/requests/' + old_body['request_id']))['receipt'] == first['receipt']
    c, path, state, option, _, _, sid = started(seed)
    assert option['whole_document_confirmed'] and sid != old_sid
    assert option['selection']['selection_hash'] != start['selection']['selection_hash']
    refs = state['manifest']['refs']; assert refs
    pre = {'schema': 'report-full-semantic-region-proof-preview-command-v1', 'session_id': sid,
        'manifest_hash': state['manifest']['manifest_hash'], 'configuration_revision': state['configuration']['revision'],
        'configuration_hash': state['configuration']['hash']}
    for index, ref in enumerate(refs):
        if index == len(refs) - 1:
            assert c.post(path + '/proof-preview', json=pre).json()['detail']['code'] == 'full_region_incomplete'
        found = reply(c.get(path + '/sessions/' + sid + '/candidates', params={'ref_hash': ref['ref_hash']}))
        assert found['candidates'], ref['path']
        mapping = found['candidates'][0]
        body = {'schema': 'report-full-semantic-region-entry-command-v1', 'request_id': str(uuid4()),
            'ref_hash': ref['ref_hash'], 'expected_version': 0, 'mapping': mapping,
            'image_acknowledgements': [seg['unit_id'] for seg in mapping['segments']], 'privacy_acknowledged': True}
        if index == 0:
            first_body = copy.deepcopy(body)
            wrong = copy.deepcopy(body); wrong['mapping']['segments'][0]['start'] += 1
            wrong['mapping']['mapping_hash'] = digest({k:v for k,v in wrong['mapping'].items() if k != 'mapping_hash'})
            assert c.post(path + '/sessions/' + sid + '/entries', json=wrong).status_code == 422
        if mapping['privacy_relation'] == 'privacy_projected':
            assert c.post(path + '/sessions/' + sid + '/entries', json={**body, 'privacy_acknowledged': False}).status_code == 422
        reply(c.post(path + '/sessions/' + sid + '/entries', json=body))
    preview = reply(c.post(path + '/proof-preview', json=pre))
    proof = preview['proof']
    assert [item['ref_hash'] for item in proof['mappings']] == [item['ref_hash'] for item in refs]
    assert proof['pending_semantic_owner_paths'] == [o['path'] for o in state['manifest']['owners']]
    assert proof['can_confirm'] is proof['can_publish'] is False
    command = {**pre, 'schema': 'report-full-semantic-region-proof-command-v1', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': False}
    assert c.post(path + '/proofs', json=command).status_code == 422
    command['consent'] = True
    accepted = reply(c.post(path + '/proofs', json=command))
    assert reply(c.post(path + '/proofs', json=command)) == accepted
    public = reply(c.get(path + '/proofs/' + accepted['receipt']['resource_id']))
    assert public['current_source_verified'] and public['proof'] == proof
    import json
    text = json.dumps(public, ensure_ascii=False)
    from tests.test_report_full_semantic_handoff_postgres import PRIVATE_NOTE
    assert PRIVATE_NOTE not in text and 'synthetic@example.invalid' not in text and 'note' not in proof
    assert reply(c.get(path + '/requests/' + command['request_id']))['receipt'] == accepted['receipt']
    # A different, valid private revision cannot be substituted under old consent.
    next_body = {**first_body, 'request_id': str(uuid4()), 'expected_version': 1}
    next_saved = reply(c.post(path + '/sessions/' + sid + '/entries', json=next_body))
    with Session(engine()) as db:
        stored = db.get(Proof, accepted['receipt']['resource_id']); original = copy.deepcopy(stored.data)
        forged = copy.deepcopy(original); item = forged['proof']['mappings'][0]
        item['entry_id'] = next_saved['receipt']['resource_id']; item['entry_version'] = 2
        forged['proof']['proof_hash'] = digest({k:v for k,v in forged['proof'].items() if k != 'proof_hash'})
        forged['ledger_hash'] = digest({k:v for k,v in forged.items() if k != 'ledger_hash'})
        stored.data = forged; db.commit()
    assert c.get(path + '/proofs/' + accepted['receipt']['resource_id']).json()['detail']['code'] == 'full_region_integrity'
    with Session(engine()) as db:
        db.get(Proof, accepted['receipt']['resource_id']).data = original; db.commit()
    assert reply(c.get(path + '/proofs/' + accepted['receipt']['resource_id']))['proof'] == proof
    with Session(engine()) as db:
        assert db.scalar(select(SetupDraft)).data == config and db.query(Proof).count() == 1


def test_final_permission_revocation_prevents_save_after_mapping_validation(source_scene2, monkeypatch):
    from generic_agents import report_full_semantic_region_mapping as module
    from generic_agents.models import User
    c, path, state, _, _, _, sid = started(source_scene2)
    _, body = choice(c, path, state, sid)
    original = module.check_mapping
    def revoke(*args, **kwargs):
        value = original(*args, **kwargs)
        with Session(engine()) as db:
            user = db.get(User, source_scene2['scene']['p'].user_id)
            user.active = False; db.commit()
        return value
    monkeypatch.setattr(module, 'check_mapping', revoke)
    response = c.post(path + '/sessions/' + sid + '/entries', json=body)
    assert response.status_code in (401, 403)
    with Session(engine()) as db:
        assert db.query(Entry).count() == 0 and db.query(Proof).count() == 0


def test_two_authenticated_sessions_serialize_cas_and_duplicate_nonce(source_scene2, account):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    c, path, state, _, _, _, sid = started(source_scene2)
    _, body = choice(c, path, state, sid)
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': account[0], 'password': account[1]}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': account[2]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.post, path + '/sessions/' + sid + '/entries',
                json={**body, 'request_id': str(uuid4())}) for client in (c, other)]
            results = [f.result(timeout=30) for f in futures]
        assert sorted(r.status_code for r in results) == [200, 409]
        exact = {**body, 'expected_version': 1, 'request_id': str(uuid4())}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.post, path + '/sessions/' + sid + '/entries', json=exact) for client in (c, other)]
            results = [f.result(timeout=30) for f in futures]
        assert all(r.status_code == 200 for r in results) and results[0].json() == results[1].json()
    assert [e['version'] for e in reply(c.get(path + '/sessions/' + sid + '/history',
        params={'ref_hash': body['ref_hash']}))['entries']] == [1, 2]


def test_new_run_with_only_first_page_never_returns_truncated_candidates(source_scene2):
    from generic_agents import report_full_semantic_region_source as authority
    from generic_agents.models import SetupMaterial, Job
    from generic_agents.worker import process_once
    seed = source_scene2; c, path, state, _, _, _, sid = started(seed)
    material = reply(c.get('/api/setup/materials/' + seed['scene']['copy']['id']))
    reparsed = reply(c.post('/api/setup/materials/' + material['id'] + '/retry', json={'version': material['version']}))
    assert process_once(reparsed['job_id'])
    material = reply(c.get('/api/setup/materials/' + material['id']))
    _, restarted = ocr_test.start(c, material)
    run_id = restarted['view']['run']['id']; assert process_once(restarted['view']['run']['job_id'])
    with Session(engine()) as db:
        jobs = list(db.scalars(select(Job).where(Job.kind == 'recognize_material_ocr_unit',
            Job.status == 'queued', Job.data['run_id'].as_string() == run_id)))
        assert len(jobs) == 1; job_id = jobs[0].id
    assert process_once(job_id)
    options = reply(c.get(path + '/source-options')); option = options['copies'][0]
    assert option['page_count'] == 2 and option['selection'] is None
    assert '全部页面' in option['reason']
    # A caller may propose real page-one metadata, but cannot claim full coverage.
    with Session(engine()) as db:
        p = seed['scene']['p']; origin = db.get(SetupMaterial, seed['scene']['original']['id'])
        copied = db.get(SetupMaterial, material['id']); run = authority.ocr.checked_run(db, p, copied, run_id)
        provenance = authority.projection.copy_provenance(db, p, origin, copied, copied.data['document_copy']['integrity_hash'])
        anchor = authority.region._copy_anchor(origin, copied, provenance, run)
        pages, refs = authority.selected_pages(db, p, copied, run, current=False)
        assert len(pages) == len(refs) == 1
        selection = {'schema': authority.SCHEMA, 'anchor': anchor, 'copy_ref': authority.region._ref(copied), 'pages': refs}
        selection['selection_hash'] = digest(selection)
    command = {'schema': 'report-full-semantic-region-session-command-v1', 'request_id': str(uuid4()),
        'manifest_hash': state['manifest']['manifest_hash'], 'selection': selection}
    rejected = c.post(path + '/sessions', json=command)
    assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'full_region_stale'
    assert not reply(c.get(path + '/requests/' + command['request_id']))['found']
    old = reply(c.get(path + '/sessions/' + sid)); assert not old['current']
