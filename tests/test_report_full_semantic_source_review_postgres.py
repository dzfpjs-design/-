"""Root-run owned synthetic PG: complete private source checks, no approval.

Real upload/conversion/OCR/reading/manual/handoff APIs; only IO engines and
provider transport are synthetic. This does not prove real model quality.
"""
import copy
import json
import io
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Membership, User, SetupDraft, SetupReportDraft, Task, CloudCall
from generic_agents.report_full_semantic_source_review_models import FullSemanticSourceReview as Review
from generic_agents.security import password_hash
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene as base_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v7_postgres import v7_scene, NativeHTTP, mapped, reduced, BASE
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_handoff_postgres import human_value, PRIVATE_NOTE

ROOT = '/api/setup/full-semantic-source-reviews/'


@pytest.fixture
def full_source_scene(client, monkeypatch, local_runtime):
    from tests import test_report_full_reading_source as source
    original = source.word_bytes
    def synthetic_word():
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original())) as before, zipfile.ZipFile(buffer, 'w') as after:
            for name in before.namelist():
                data = before.read(name)
                if name == 'word/document.xml':
                    data = data.replace(b'</w:body>', ('<w:p><w:r><w:t>联络 synthetic@example.invalid；核对资料关联核对任务。</w:t></w:r></w:p></w:body>').encode())
                after.writestr(name, data)
        return buffer.getvalue()
    monkeypatch.setattr(source, 'word_bytes', synthetic_word)
    return base_source_scene.__wrapped__(client, monkeypatch, local_runtime)


def complete_source_review_seed(scene, monkeypatch):
    fence(); http = NativeHTTP(monkeypatch)
    _, _, _, middle = mapped(scene, http)
    _, _, _, final = reduced(scene, http, middle)
    value = human_value(final['model_result'])
    citation = {'span_ref': 'w:p3/s1'}
    explicit = {'status': 'explicit', 'citations': [copy.deepcopy(citation)], 'explanation': ''}
    business = value['result']['businesses'][1]
    business.update(citations=[copy.deepcopy(citation)], inference_reason='')
    for step in business['steps']:
        step.update(citations=[copy.deepcopy(citation)], inference_reason='')
    for key in ('participants', 'interactions', 'conditions', 'coordination'):
        for item in business[key]:
            item['evidence'] = copy.deepcopy(explicit)
    obj = value['result']['objects'][-1]
    obj['name'] = '核对资料'
    obj.update(citations=[copy.deepcopy(citation)], inference_reason='')
    obj['fields'][0].update(citations=[copy.deepcopy(citation)], inference_reason='')
    other_obj = copy.deepcopy(obj); other_obj['name'] = '核对任务'
    value['result']['objects'].append(other_obj)
    value['result']['relationships'].append({'name': '资料关联任务', 'description': '人工填写，仅核对协议',
        'source_type': obj['name'], 'target_type': other_obj['name'], 'cardinality': 'unspecified',
        'citations': [copy.deepcopy(citation)], 'inference_reason': ''})
    from generic_agents.report_evidence_wire_v3 import CandidateWireV5
    CandidateWireV5.model_validate(value['result'])
    client = scene['client']
    from generic_agents import report_evidence_pipeline as pipeline
    from generic_agents.security import Principal
    me = reply(client.get('/api/me'))
    with Session(engine()) as db:
        stored = db.get(SetupReportDraft, final['id'])
        check = pipeline.ReviewCommand(revision=final['revision'], request_id=str(uuid4()), value=value)
        pipeline.v3.manual_projection(pipeline, stored,
            Principal(me['user_id'], me['workspace_id'], me['role'], ''), check, value,
            pipeline._catalog(stored.data['preview']))
    saved = reply(client.put(BASE + '/' + final['id'] + '/review', json={
        'revision': final['revision'], 'request_id': str(uuid4()), 'value': value}))
    config = reply(client.get('/api/setup/draft')); row = saved['draft']
    body = {'schema': 'report-full-semantic-handoff-command-v1', 'request_id': str(uuid4()),
        'report_revision': row['revision'], 'review_receipt_hash': saved['receipt']['receipt_hash'],
        'manual_value_hash': digest(row['review_value']), 'model_result_hash': row['model_result_hash'],
        'source_hash': row['preview']['source_hash'], 'catalog_hash': value['result']['catalog_hash'],
        'context_hash': digest(row['preview']['map_preparation']['context']),
        'configuration_revision': config['revision'], 'configuration_hash': digest(config['configuration']),
        'share_scope': 'whole_result_excluding_private_note_v1'}
    preview = reply(client.post(BASE + '/' + row['id'] + '/full-configuration-preview', json=body))
    shared = reply(client.post(BASE + '/' + row['id'] + '/full-configuration-handoff', json={
        **body, 'preview_hash': preview['preview_hash'], 'consent': True}))
    return {'client': client, 'report': row, 'record': shared['record'], 'value': value,
        'path': ROOT + shared['record']['id'], 'http': http, 'scene': scene}


@pytest.fixture
def source_scene(v7_scene, monkeypatch):
    return complete_source_review_seed(v7_scene, monkeypatch)


def command(state, owner, *, version=0, decision='checked', note='人工核对说明：需要保留🙂'):
    return {'schema': 'report-full-semantic-source-review-save-v1', 'request_id': str(uuid4()),
        'manifest_hash': state['manifest']['manifest_hash'], 'owner_path': owner['path'],
        'owner_value_hash': owner['value_hash'], 'expected_version': version,
        'value': {'decision': decision, 'note': note,
            'checked_ref_hashes': owner['ref_hashes'] if decision == 'checked' else []}}


def test_all_paths_source_quotes_private_append_only_nonce_cas_and_cold_read(source_scene, account):
    seed = source_scene; client, path = seed['client'], seed['path']
    with Session(engine()) as db:
        before = db.get(SetupReportDraft, seed['report']['id']).data
        config = copy.deepcopy(db.scalar(select(SetupDraft)).data)
        calls = db.query(CloudCall).count()
    state = reply(client.get(path)); manifest = state['manifest']
    assert state['active'] is True and state['reviews'] == []
    assert state['can_confirm'] is state['can_publish'] is False
    kinds = {owner['kind'] for owner in manifest['owners']}
    assert {'domain', 'global_claim', 'business', 'step', 'participant', 'interaction',
        'condition', 'coordination', 'object', 'field', 'relationship', 'question'} <= kinds
    assert PRIVATE_NOTE not in json.dumps(state, ensure_ascii=False)
    chosen = []
    for kind in ('global_claim', 'business', 'step', 'participant', 'interaction', 'condition',
                 'coordination', 'object', 'field', 'relationship'):
        owner = next(o for o in manifest['owners'] if o['kind'] == kind and o['ref_hashes'])
        evidence = reply(client.get(path + '/evidence', params={'owner_path': owner['path']}))
        value = seed['value']['result']
        for key in owner['path'].strip('/').split('/'):
            value = value[int(key)] if isinstance(value, list) else value[key]
        assert evidence['owner']['value'] == value
        assert [r['ref_hash'] for r in evidence['references']] == owner['ref_hashes']
        assert evidence['formal_source_status'] == 'pending_final_view_projection'
        for ref in evidence['references']:
            a = ref['visible_start'] - ref['context_start']; b = ref['visible_end'] - ref['context_start']
            assert ref['context'][a:b] == ref['quote']
            assert ref['location'] == ref['source_anchor']['raw_location']
            assert 'text' not in ref['source_anchor'] and ref['quote']
            assert 'synthetic@example.invalid' not in ref['context']
            if ref['image_url']:
                import hashlib
                image = client.get(ref['image_url'])
                assert image.status_code == 200 and hashlib.sha256(image.content).hexdigest() == ref['image_sha256']
            if ref['privacy_relation'] == 'privacy_projected':
                assert '[邮箱已移除]' in ref['quote'] and ref['source_anchor']['contains_redaction']
        body = command(state, owner)
        saved = reply(client.post(path + '/entries', json=body)); chosen.append((owner, body, saved['receipt']))
        assert reply(client.post(path + '/entries', json=body))['receipt'] == saved['receipt']
        recovered = reply(client.get(path + '/requests/' + body['request_id']))
        assert recovered['found'] and recovered['current_matches'] and recovered['receipt'] == saved['receipt']
        assert recovered['entry']['value'] == body['value']
        changed = copy.deepcopy(body); changed['value']['note'] = '同编号不同内容'
        assert client.post(path + '/entries', json=changed).status_code == 409
        stale = command(state, owner)
        assert client.post(path + '/entries', json=stale).json()['detail']['code'] == 'source_review_conflict'
    owner, first, _ = chosen[0]
    second = command(state, owner, version=1, decision='needs_change', note='发现问题，保存新判断')
    reply(client.post(path + '/entries', json=second))
    history = reply(client.get(path + '/history', params={'owner_path': owner['path']}))['items']
    assert [v['version'] for v in history] == [1, 2] and history[0]['value'] == first['value']
    assert not reply(client.get(path + '/requests/' + first['request_id']))['current_matches']
    # Explicitly restoring the earlier value creates a third version, never overwrites.
    restore = command(state, owner, version=2); restore['value'] = first['value']
    reply(client.post(path + '/entries', json=restore))
    unknown = next(o for o in manifest['owners'] if o['evidence_status'] == 'unknown')
    assert client.post(path + '/entries', json=command(state, unknown)).status_code == 422
    pending = command(state, unknown, decision='pending', note='原文不足；先保存核对中进度')
    reply(client.post(path + '/entries', json=pending))
    for bad in [dict(command(state, owner, version=3), owner_value_hash='a'*64),
                dict(command(state, owner, version=3), manifest_hash='a'*64)]:
        assert client.post(path + '/entries', json=bad).status_code in (409, 422)
    current = reply(client.get(path))
    with TestClient(app) as reopened:
        auth = reply(reopened.post('/api/login', json={'username': account[0], 'password': account[1]}))
        reopened.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(reopened.get(path)) == current
    with Session(engine()) as db:
        assert db.get(SetupReportDraft, seed['report']['id']).data == before
        assert db.scalar(select(SetupDraft)).data == config
        assert db.query(CloudCall).count() == calls and db.query(Task).count() == 0
        assert db.query(Review).count() == len(chosen) + 3
    assert PRIVATE_NOTE not in json.dumps(current, ensure_ascii=False)


def test_independent_actor_private_notes_transaction_rollback_and_source_corruption(source_scene, account, monkeypatch):
    seed = source_scene; client, path = seed['client'], seed['path']
    state = reply(client.get(path)); owner = next(o for o in state['manifest']['owners'] if o['ref_hashes'])
    body = command(state, owner, note='本人的私人判断不能泄露给其他成员')
    saved = reply(client.post(path + '/entries', json=body))
    name, password = 'source_other_' + uuid4().hex[:12], 'Synthetic-' + uuid4().hex
    with Session(engine()) as db:
        user = User(username=name, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=account[2], role='operator')); db.commit()
    with TestClient(app) as other:
        auth = reply(other.post('/api/login', json={'username': name, 'password': password}))
        other.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        other_state = reply(other.get(path))
        assert other_state['reviews'] == [] and other_state['manifest'] == state['manifest']
        assert not reply(other.get(path + '/requests/' + body['request_id']))['found']
        assert body['value']['note'] not in json.dumps(other_state, ensure_ascii=False)
        assert reply(other.post(path + '/entries', json=body))['receipt']['actor_id'] != saved['receipt']['actor_id']
    from generic_agents import report_full_semantic_source_review as api
    def broken_audit(*args, **kwargs):
        raise RuntimeError('synthetic fault immediately before commit')
    bad = command(state, owner, version=1, decision='needs_change')
    with monkeypatch.context() as patch:
        patch.setattr(api, 'audit', broken_audit)
        with pytest.raises(RuntimeError, match='before commit'):
            client.post(path + '/entries', json=bad)
    assert not reply(client.get(path + '/requests/' + bad['request_id']))['found']
    assert reply(client.get(path))['reviews'][0]['version'] == 1
    # Re-signed rows do not let a caller substitute a current design value.
    with Session(engine()) as db:
        row = db.scalar(select(Review).where(Review.actor_id == saved['receipt']['actor_id']))
        value = copy.deepcopy(row.data); value['command']['owner_value_hash'] = 'b'*64
        value['entry_hash'] = digest({k: v for k,v in value.items() if k != 'entry_hash'})
        row.data = value; db.commit()
    assert client.get(path).json()['detail']['code'] == 'source_review_integrity'
