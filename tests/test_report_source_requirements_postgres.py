"""Root-owned PostgreSQL/API checks; all source and provider responses synthetic.

Runs only with the isolated fixture driver, never the repository .env fixture.
No real model quality, physical restart or browser claim comes from this file.
"""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupReportDraft
from generic_agents.report_source_requirement_models import SetupRequirementCheck
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v5 import v5_scene, SyntheticHTTP, mapped, reduce, start, reply, BASE


def body(view, *, disposition='needs_evidence', targets=None, check_id=None, version=0):
    binding = view['binding']
    return {'schema': 'report-source-requirement-save-v1', 'request_id': str(uuid4()),
        'check_id': check_id or str(uuid4()), 'expected_version': version,
        'draft_revision': binding['draft_revision'], 'value': {
            'schema': 'report-source-requirement-check-v1', 'requirement': '合成核对：应保留这处原文的业务要求。',
            'source_unit_ids': [view['selected_unit']['source_unit_id']],
            'disposition': disposition, 'reason': '合成测试的人工判断，不能证明模型质量。',
            'targets': targets or [], 'candidate_hash': binding['candidate_hash'],
            'source_hash': binding['source_hash'], 'catalog_hash': binding['catalog_hash']}}


def save(client, identity, command):
    return client.put(BASE + '/' + identity + '/requirement-checks/' + command['check_id'], json=command)


def test_source_requirement_pending_save_before_candidate_and_honest_state(v5_scene, monkeypatch):
    fence(); scene = v5_scene; client = scene['client']; http = SyntheticHTTP(monkeypatch)
    _, _, accepted = start(scene, http); identity = accepted['draft']['id']
    view = reply(client.get(BASE + '/' + identity + '/requirement-review'))
    assert view['candidate_available'] is False and view['binding']['candidate_hash'] is None
    assert view['target_options'] == [] and view['semantic_completeness_verified'] is False
    assert view['selected_unit']['text'] and view['checks'] == []
    command = body(view)
    saved = reply(save(client, identity, command))
    assert saved['replayed'] is False and saved['saved']['review_status']['status'] == 'pending_candidate'
    assert reply(save(client, identity, command))['saved']['receipt'] == saved['saved']['receipt']
    fetched = reply(client.get(BASE + '/' + identity + '/requirement-review'))
    assert fetched['checks'][0]['checked']['record'] == command['value']
    assert fetched['checks'][0]['review_status']['status'] == 'pending_candidate'
    wrong = copy.deepcopy(command); wrong['request_id'] = str(uuid4()); wrong['check_id'] = str(uuid4())
    wrong['value']['disposition'] = 'represented'
    assert save(client, identity, wrong).status_code == 422
    assert len(http.requests) == 0
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(SetupRequirementCheck)) == 1
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.get(SetupReportDraft, identity).version == view['binding']['draft_revision']


def test_source_requirement_append_cas_nonce_stale_human_priority_and_receipt(v5_scene, monkeypatch, account):
    fence(); scene = v5_scene; client = scene['client']; http = SyntheticHTTP(monkeypatch)
    _, _, _, middle = mapped(scene, http); _, _, _, final = reduce(scene, http, middle)
    identity = final['id']; url = BASE + '/' + identity
    view = reply(client.get(url + '/requirement-review', params={'limit': 1, 'target_limit': 1}))
    assert view['candidate_available'] and view['binding']['candidate_kind'] == 'model'
    assert len(view['source_units']) == len(view['target_options']) == 1
    assert view['target_total'] > 1 and view['source_total'] > 1
    command = body(view, disposition='represented', targets=[view['target_options'][0]['pointer']])
    count = len(http.requests)
    saved = reply(save(client, identity, command)); receipt = saved['saved']['receipt']
    assert saved['saved']['review_status']['status'] == 'current'
    assert saved['saved']['checked']['record']['disposition'] == 'represented'
    assert saved['saved']['semantic_completeness_verified'] is False
    assert reply(save(client, identity, command))['saved']['receipt'] == receipt
    changed = copy.deepcopy(command); changed['value']['reason'] += ' altered'
    assert save(client, identity, changed).status_code == 409
    next_command = copy.deepcopy(command); next_command.update(request_id=str(uuid4()), expected_version=1)
    next_command['value']['reason'] += ' second version'
    second = reply(save(client, identity, next_command))
    assert second['saved']['version'] == 2
    stale_write = copy.deepcopy(command); stale_write['request_id'] = str(uuid4())
    assert save(client, identity, stale_write).status_code == 409
    # Only a real API human save changes the effective candidate; no DB rewrite.
    human = {'schema': 'report-evidence-manual-review-v5',
        'result': wire6.decode_reduction_wire_v6(http.values[-1])['result'], 'note': '合成资料的人工作业版本'}
    human['result']['global_claims'][0]['statement'] += ' 人工补充仍待核。'
    human_save = reply(client.put(url + '/review', json={
        'revision': final['revision'], 'request_id': str(uuid4()), 'value': human}))
    updated = reply(client.get(url + '/requirement-review'))
    assert updated['binding']['candidate_kind'] == 'manual'
    assert updated['checks'][0]['review_status']['status'] == 'stale'
    assert updated['checks'][0]['receipt'] == second['saved']['receipt']
    # Restore historical nonce BEFORE current source/value/revision validation.
    replay = reply(save(client, identity, command))
    assert replay['saved']['receipt'] == receipt and replay['saved']['review_status']['status'] == 'history'
    assert reply(client.get(url + '/requirement-receipts/' + command['request_id'])) == replay
    history = reply(client.get(url + '/requirement-checks/' + command['check_id'] + '/history'))
    assert [entry['version'] for entry in history['entries']] == [2, 1]
    assert history['entries'][1]['receipt'] == receipt
    assert reply(client.get(url))['revision'] == human_save['draft']['revision']
    assert len(http.requests) == count
    with Session(engine()) as db:
        records = db.scalars(select(SetupRequirementCheck).order_by(SetupRequirementCheck.item_version)).all()
        assert len(records) == 2 and records[0].data['receipt'] == receipt
    # Optional private Root locator supports later real login/browser/restarts.
    output = os.environ.get('GA_SOURCE_REVIEW_SEED_OUT')
    if output:
        target = Path(output); assert target.is_absolute() and not target.exists()
        raw = json.dumps({'draft_id': identity, 'material_id': scene['original']['id'],
            'workspace_id': scene['p'].workspace_id, 'username': account[0], 'password': account[1],
            'check_id': command['check_id'], 'commands': [command, next_command],
            'receipts': [receipt, second['saved']['receipt']], 'binding': updated['binding'],
            'synthetic_only': True, 'real_model_quality_verified': False}, ensure_ascii=False).encode()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
