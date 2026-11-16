"""Actual fixed local model and normal API/PG; no inference or storage seam.

Small synthetic images prove engineering durability, never remote-sensing quality.
All databases and evidence files belong to the disposable conftest environment.
"""
import copy
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import Audit, Entity, Job, Task, CloudCall, Membership, Evidence
from generic_agents.pixel_annotation_models_v256 import PixelAnnotationV256 as Annotation, PixelAnnotationCommandV256 as Command
from generic_agents import pixel_annotations_v256 as store
from generic_agents.worker import process_once
from tests.test_pixel_change_pg_v249 import create
from tests.test_pixel_change_native_pg_v249 import native_scene, pytestmark


def raw(body):
    return json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()


def body_for(run, **changes):
    return {'schema': 'pixel-annotation-save-v256', 'request_id': 'annotation/' + uuid4().hex,
        'expected_run_version': run['version'], 'result_hash': run['result']['result_hash'],
        'supersedes_annotation_id': None, 'note': '人工参考标注；未标区域保持未知，不作为独立留出',
        'regions': [{'label': 'change', 'rectangle': {'x0': 1, 'y0': 2, 'x1': 7, 'y1': 6}}], **changes}


@pytest.fixture
def actual(native_scene):
    s = native_scene
    queued = create(s)
    assert process_once(job_id=queued['job_id'], deliver=False)
    result = s.client.get(s.base + '/runs/' + queued['id'])
    assert result.status_code == 200, result.text
    run = result.json(); assert run['status'] == 'succeeded', run.get('reason')
    s.run = run; s.annotation_base = s.base + '/runs/' + run['id']
    return s


def counts():
    with Session(engine()) as db:
        return {name: db.scalar(select(func.count()).select_from(cls)) for name, cls in
            [('Annotation', Annotation), ('Command', Command), ('Audit', Audit), ('Evidence', Evidence),
             ('Entity', Entity), ('Job', Job), ('Task', Task), ('CloudCall', CloudCall)]}


def save(s, body, status=201):
    response = s.client.post(s.annotation_base + '/annotations', content=raw(body), headers={'Content-Type': 'application/json'})
    assert response.status_code == status, response.text
    return response.json()


def recover(s, body):
    response = s.client.get(s.annotation_base + '/annotation-requests', params={
        'request_id': body['request_id'], 'raw_sha256': hashlib.sha256(raw(body)).hexdigest()})
    assert response.status_code == 200, response.text
    return response.json()


def test_actual_annotations_unknown_mask_history_exact_raw_replay_and_no_model_side_effect(actual):
    s = actual; before = counts(); body = body_for(s.run)
    first = save(s, body)
    assert first['counts'] == {'changed': 24, 'unchanged': 0, 'unknown': 5096, 'known_valid': 24, 'total': 5120}
    assert first['supersedes_annotation_id'] is None and first['regions'] == body['regions']
    downloaded = {}
    for kind, ref in first['artifacts'].items():
        response = s.client.get(s.annotation_base + '/annotations/' + first['id'] + '/artifacts/' + kind)
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == ref['sha256'] == response.headers['x-content-sha256']
        assert len(response.content) == ref['size_bytes']
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (80, 64) and image.mode == 'L'
            downloaded[kind] = image.tobytes()
    assert set(downloaded['labels']) == {1, 255} and downloaded['labels'].count(1) == 24
    assert set(downloaded['known_mask']) == {0, 255} and downloaded['known_mask'].count(255) == 24
    second_body = body_for(s.run, supersedes_annotation_id=first['id'], regions=[
        *body['regions'], {'label': 'unknown', 'rectangle': {'x0': 1, 'y0': 2, 'x1': 4, 'y1': 6}},
        {'label': 'unchanged', 'rectangle': {'x0': 20, 'y0': 20, 'x1': 24, 'y1': 25}}])
    second = save(s, second_body)
    assert second['counts'] == {'changed': 12, 'unchanged': 20, 'unknown': 5088, 'known_valid': 32, 'total': 5120}
    assert recover(s, body)['original_raw'] == raw(body).decode() and recover(s, body)['annotation'] == first
    state = counts(); assert save(s, body) == first and counts() == state
    listing = s.client.get(s.annotation_base + '/annotations').json()
    assert {x['id'] for x in listing['items']} == {first['id'], second['id']}
    assert s.client.get(s.annotation_base + '/annotations/options').json()['latest'] == second
    after = counts()
    for key in ['Entity', 'Job', 'Task', 'CloudCall']:
        assert after[key] == before[key]
    assert after['Annotation'] - before['Annotation'] == 2 and after['Command'] - before['Command'] == 2
    # Each PNG has its normal evidence.created audit, in addition to the
    # annotation and command audit. These four file audits must not disappear.
    assert after['Evidence'] - before['Evidence'] == 4 and after['Audit'] - before['Audit'] == 8
    altered = s.client.post(s.annotation_base + '/annotations', content=json.dumps(body, ensure_ascii=False, indent=1), headers={'Content-Type': 'application/json'})
    assert altered.status_code == 409 and counts() == after


def test_concurrent_first_annotations_serialize_and_rejection_recovers_after_response_loss(actual):
    s = actual; bodies = [body_for(s.run), body_for(s.run, note='第二个标签页的独立原稿')]
    barrier = Barrier(2)
    def post(body):
        barrier.wait(timeout=10)
        return s.client.post(s.annotation_base + '/annotations', content=raw(body), headers={'Content-Type': 'application/json'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(post, bodies))
    assert sorted(r.status_code for r in responses) == [201, 409]
    winner = responses[next(i for i, r in enumerate(responses) if r.status_code == 201)].json()
    loser_index = next(i for i, r in enumerate(responses) if r.status_code == 409)
    recovered = recover(s, bodies[loser_index])
    assert recovered['found'] and recovered['outcome'] == 'rejected' and recovered['annotation'] is None
    assert recovered['rejection']['code'] == 'annotation_changed' and recovered['original_raw'] == raw(bodies[loser_index]).decode()
    before = counts(); assert save(s, bodies[loser_index], 409)['detail'] == recovered and counts() == before
    revised = {**bodies[loser_index], 'request_id': 'revised-' + uuid4().hex, 'supersedes_annotation_id': winner['id']}
    assert save(s, revised)['supersedes_annotation_id'] == winner['id']


@pytest.mark.parametrize('damage', ['self_cycle', 'actor_drift', 'audit_missing', 'command_missing', 'artifact_metadata'])
def test_cold_inventory_integrity_blocks_save_and_never_downgrades_latest(actual, damage):
    s = actual; first_body = body_for(s.run); first = save(s, first_body)
    second = save(s, body_for(s.run, supersedes_annotation_id=first['id']))
    with Session(engine()) as db:
        row = db.get(Annotation, second['id'])
        if damage == 'self_cycle':
            row.supersedes_id = row.id
        elif damage == 'actor_drift':
            # An unrelated existing principal cannot hide this row from its author's chain.
            from generic_agents.models import User
            from generic_agents.security import password_hash
            user = User(username='other-' + uuid4().hex, password_hash=password_hash(uuid4().hex)); db.add(user); db.flush()
            row.actor_id = user.id
        elif damage == 'audit_missing':
            db.execute(delete(Audit).where(Audit.id == row.data['audit_id']))
        elif damage == 'command_missing':
            db.execute(delete(Command).where(Command.request_id == row.request_id))
        else:
            data = copy.deepcopy(row.data); data['artifacts']['labels']['size_bytes'] += 1; row.data = data
        db.commit()
    before = counts()
    assert s.client.get(s.annotation_base + '/annotations/options').status_code == 409
    save(s, body_for(s.run, supersedes_annotation_id=second['id']), 409)
    assert counts() == before


def test_deleted_rejected_request_cannot_be_reused_under_a_different_run(actual):
    s = actual; bad = body_for(s.run, expected_run_version=s.run['version'] + 10)
    save(s, bad, 409); assert recover(s, bad)['rejection']['code'] == 'analysis_changed'
    second = create(s, request_id='another-run-' + uuid4().hex)
    assert process_once(job_id=second['job_id'], deliver=False)
    second = s.client.get(s.base + '/runs/' + second['id']).json(); assert second['status'] == 'succeeded'
    with Session(engine()) as db:
        db.execute(delete(Command).where(Command.request_id == bad['request_id'])); db.commit()
    before = counts()
    target = s.base + '/runs/' + second['id']
    response = s.client.post(target + '/annotations', content=raw(body_for(second, request_id=bad['request_id'])), headers={'Content-Type': 'application/json'})
    assert response.status_code == 409 and counts() == before


def test_viewer_and_raw_parser_failures_do_not_save_annotations(actual):
    s = actual; before = counts(); good = raw(body_for(s.run))
    for value in [b'{"schema":1,"schema":2}', good[:-1] + b',"unexpected":true}', b'{"note":NaN}', b'\xff',
                  b'[' * 1200 + b'0' + b']' * 1200, good + b' ' * 131072]:
        response = s.client.post(s.annotation_base + '/annotations', content=value, headers={'Content-Type': 'application/json'})
        assert response.status_code in {413, 422}, response.text
    assert counts() == before
    assert s.client.get(s.annotation_base + '/annotation-requests', params={
        'request_id': 'nul-query-id-' + '\x00', 'raw_sha256': 'a' * 64}).status_code == 422
    with Session(engine()) as db:
        member = db.scalar(select(Membership)); member.role = 'viewer'; db.commit()
    options = s.client.get(s.annotation_base + '/annotations/options')
    assert options.status_code == 200 and options.json()['can_save'] is False
    response = s.client.post(s.annotation_base + '/annotations', content=good, headers={'Content-Type': 'application/json'})
    assert response.status_code == 403 and counts() == before


def test_missing_original_valid_or_annotation_blob_blocks_without_reinference_and_recovers(actual):
    s = actual; body = body_for(s.run); annotation = save(s, body)
    original_recovery = recover(s, body)
    valid = next(x for x in s.run['result']['artifacts'] if x['kind'] == 'valid_mask')
    ids = [s.run['sources']['before']['id'], valid['evidence_id'], annotation['artifacts']['labels']['evidence_id']]
    for identity in ids:
        with Session(engine()) as db:
            evidence = db.get(Evidence, identity); path = settings().storage_dir / evidence.storage_key
        hidden = path.with_suffix('.temporarily-missing-v256')
        assert path.is_file() and not hidden.exists()
        before = counts(); original = path.read_bytes(); path.rename(hidden)
        try:
            for url in ['/annotations/options', '/annotations/' + annotation['id']]:
                assert s.client.get(s.annotation_base + url).status_code == 409
            response = s.client.get(s.annotation_base + '/annotation-requests', params={
                'request_id': body['request_id'], 'raw_sha256': hashlib.sha256(raw(body)).hexdigest()})
            assert response.status_code == 409 and counts() == before
        finally:
            hidden.rename(path)
        assert path.read_bytes() == original and recover(s, body) == original_recovery and counts() == before
