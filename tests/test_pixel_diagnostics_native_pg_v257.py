"""Normal saved V249 source + V256 annotation + actual isolated native math.

Synthetic images/labels prove engineering persistence, never model quality or
independent ground truth. No inference/output/storage substitute is installed.
Root executes in disposable PG/native environments; this file is not executed
by its author. Native calls below invoke the actual bridge after observation.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import pixel_diagnostics_v257 as store
from generic_agents import pixel_diagnostic_bridge_v257 as bridge
from generic_agents.pixel_diagnostic_models_v257 import PixelDiagnosticStudyV257 as Study, PixelDiagnosticEvaluationV257 as Evaluation, PixelDiagnosticCommandV257 as Command
from tests.test_pixel_annotations_native_pg_v256 import actual, native_scene, body_for, raw, save
from tests.test_pixel_change_native_pg_v249 import pytestmark


@pytest.fixture
def diagnostic_scene(actual):
    s = actual
    s.annotation = save(s, body_for(s.run))
    s.diagnostic_base = s.annotation_base + '/annotations/' + s.annotation['id'] + '/diagnostics'
    return s


def freeze_body(s, **changes):
    return {'schema': 'pixel-diagnostic-freeze-v257', 'request_id': '冻结/原文?' + uuid4().hex,
        'annotation_record_hash': s.annotation['record_hash'], 'candidate_threshold_text': '0.25', **changes}


def evaluate_body(study, **changes):
    return {'schema': 'pixel-diagnostic-evaluate-v257', 'request_id': '计算/原文?' + uuid4().hex,
        'study_record_hash': study['record_hash'], **changes}


def post(s, body, *, study=None, expected=201):
    path = s.diagnostic_base + ('/' + study['id'] + '/evaluate' if study is not None else '')
    response = s.client.post(path, content=raw(body), headers={'Content-Type': 'application/json'})
    assert response.status_code == expected, response.text
    return response.json()


def lookup(s, body):
    response = s.client.get(s.diagnostic_base + '/requests', params={
        'request_id': body['request_id'], 'raw_sha256': hashlib.sha256(raw(body)).hexdigest()})
    assert response.status_code == 200, response.text
    return response.json()


def inventory(s):
    with Session(engine()) as db:
        run = db.get(m.PixelChangeRun, s.run['id'])
        classes = [Study, Evaluation, Command, m.Audit, m.Evidence, m.Job, m.Invocation,
            m.Observation, m.Event, m.Memory, m.Task, m.CloudCall, m.Entity, m.PixelChangeRun]
        result = {cls.__name__: db.scalar(select(func.count()).select_from(cls).where(cls.workspace_id == run.workspace_id)) for cls in classes}
        result['source_hash'] = digest({'id': run.id, 'status': run.status, 'version': run.version, 'data': run.data})
        result['entity_hash'] = digest([{'id': row.id, 'version': row.version, 'data': row.data}
            for row in db.scalars(select(m.Entity).where(m.Entity.workspace_id == run.workspace_id).order_by(m.Entity.id))])
        return result


def no_model_side_effect(before, after):
    for name in ['Job', 'Invocation', 'Observation', 'Event', 'Memory', 'Task', 'CloudCall', 'Entity', 'PixelChangeRun', 'source_hash', 'entity_hash']:
        assert after[name] == before[name], name


def real_evaluator_counter(monkeypatch):
    original = bridge.evaluate; calls = []; mutex = Lock()
    def observed(manifest, probability, labels, valid):
        # A separate DB transaction must already observe a committed Study.
        with Session(engine()) as db:
            rows = list(db.scalars(select(Study).where(Study.data['manifest']['manifest_hash'].as_string() == manifest['manifest_hash'])))
            assert len(rows) == 1
        with mutex: calls.append(manifest['manifest_hash'])
        return original(manifest, probability, labels, valid)
    monkeypatch.setattr(bridge, 'evaluate', observed)
    return calls


def test_freeze_commits_before_actual_native_evaluation_and_raw_recovery_is_readonly(diagnostic_scene, monkeypatch):
    s = diagnostic_scene; before = inventory(s); calls = real_evaluator_counter(monkeypatch)
    options = s.client.get(s.diagnostic_base + '/options')
    assert options.status_code == 200, options.text
    assert options.json()['can_freeze'] and options.json()['runtime_ready']
    fb = freeze_body(s); study = post(s, fb)
    assert calls == [] and study['version'] == 1 and study['request_id'] == fb['request_id']
    assert study['manifest']['split'] == 'exploratory' and study['manifest']['annotation_exposure'] == 'model_result_available'
    assert study['manifest']['release_authorized'] is False and study['manifest']['field_truth_verified'] is False
    empty = s.client.get(s.diagnostic_base + '/' + study['id'] + '/evaluation')
    assert empty.status_code == 200 and empty.json()['found'] is False and empty.json()['evaluation'] is None
    stable = inventory(s); assert post(s, fb) == study and inventory(s) == stable and calls == []
    assert lookup(s, fb)['receipt'] == study and lookup(s, fb)['original_raw'] == raw(fb).decode()
    eb = evaluate_body(study); evaluation = post(s, eb, study=study)
    assert calls == [study['manifest']['manifest_hash']]
    result = evaluation['result']; assert result['release_authorized'] is False and result['field_truth_verified'] is False
    assert result['annotation_exposure'] == 'model_result_available'
    assert result['manifest_hash'] == study['manifest']['manifest_hash']
    assert result['baseline_score']['coverage_hash'] == result['candidate_score']['coverage_hash']
    path = s.diagnostic_base + '/' + study['id']
    response = s.client.get(path + '/evaluation'); assert response.status_code == 200, response.text
    assert response.json()['evaluation'] == evaluation
    artifact = s.client.get(path + '/evaluation/artifact'); assert artifact.status_code == 200, artifact.text
    assert artifact.headers['cache-control'] == 'private, no-store' and artifact.headers['content-type'].split(';')[0] == 'application/json'
    assert hashlib.sha256(artifact.content).hexdigest() == evaluation['result_artifact']['sha256'] == artifact.headers['x-content-sha256']
    assert len(artifact.content) == evaluation['result_artifact']['size_bytes'] and json.loads(artifact.content) == result
    stable = inventory(s)
    assert post(s, eb, study=study) == evaluation and lookup(s, eb)['receipt'] == evaluation
    assert inventory(s) == stable and len(calls) == 1
    fresh = post(s, evaluate_body(study), study=study, expected=409)['detail']
    assert fresh['outcome'] == 'rejected' and fresh['rejection']['code'] == 'already_evaluated' and len(calls) == 1
    no_model_side_effect(before, inventory(s))


def test_new_annotation_version_and_unavailable_current_runtime_do_not_destroy_frozen_history(diagnostic_scene, monkeypatch):
    s = diagnostic_scene; fb = freeze_body(s); study = post(s, fb); eb = evaluate_body(study)
    evaluation = post(s, eb, study=study)
    newer = save(s, body_for(s.run, supersedes_annotation_id=s.annotation['id'], note='后来独立保存的修正版，不重写旧标注'))
    assert newer['id'] != s.annotation['id']
    def unavailable():
        raise bridge.DiagnosticRuntimeError('runtime_unavailable', '本地环境暂不可用')
    monkeypatch.setattr(bridge, 'probe', unavailable)
    before = inventory(s); path = s.diagnostic_base + '/' + study['id']
    assert s.client.get(path).json() == study
    assert s.client.get(path + '/evaluation').json()['evaluation'] == evaluation
    assert lookup(s, fb)['receipt'] == study and lookup(s, eb)['receipt'] == evaluation
    assert post(s, eb, study=study) == evaluation and inventory(s) == before
    options = s.client.get(s.diagnostic_base + '/options')
    assert options.status_code == 200 and options.json()['runtime_ready'] is False and not options.json()['can_freeze']


def test_missing_source_is_durable_rejection_then_explicit_new_nonce_after_restore(diagnostic_scene):
    s = diagnostic_scene; study = post(s, freeze_body(s)); body = evaluate_body(study)
    ref = next(x for x in s.run['result']['artifacts'] if x['kind'] == 'probability_data')
    with Session(engine()) as db:
        path = settings().storage_dir / db.get(m.Evidence, ref['evidence_id']).storage_key
    hidden = path.with_name(path.name + '.diagnostic-missing-v257'); assert path.is_file() and not hidden.exists()
    original = path.read_bytes(); path.rename(hidden)
    try:
        before = inventory(s); rejection = post(s, body, study=study, expected=409)['detail']
        assert rejection['outcome'] == 'rejected' and rejection['rejection']['code'] == 'source_unavailable'
        assert rejection['receipt'] is None and rejection['original_raw'] == raw(body).decode()
        assert s.client.get(s.diagnostic_base + '/' + study['id']).status_code == 200
        assert lookup(s, body) == rejection
        assert inventory(s)['PixelDiagnosticEvaluationV257'] == before['PixelDiagnosticEvaluationV257']
    finally:
        hidden.rename(path)
    assert path.read_bytes() == original
    before = inventory(s); assert post(s, body, study=study, expected=409)['detail'] == rejection and inventory(s) == before
    evaluation = post(s, evaluate_body(study), study=study)
    assert evaluation['study_id'] == study['id']


def test_runtime_fingerprint_gate_saved_rejection_cannot_turn_into_later_success(diagnostic_scene, monkeypatch):
    s = diagnostic_scene; study = post(s, freeze_body(s)); body = evaluate_body(study)
    original = bridge.adapter_hash
    monkeypatch.setattr(bridge, 'adapter_hash', lambda: 'e' * 64 if original() != 'e' * 64 else 'f' * 64)
    rejected = post(s, body, study=study, expected=409)['detail']
    assert rejected['rejection']['code'] == 'evaluator_changed'
    monkeypatch.setattr(bridge, 'adapter_hash', original)
    before = inventory(s); assert post(s, body, study=study, expected=409)['detail'] == rejected and inventory(s) == before
    assert post(s, evaluate_body(study), study=study)['study_id'] == study['id']


def test_two_evaluate_requests_commit_one_actual_result_without_replacing_loser(diagnostic_scene, monkeypatch):
    s = diagnostic_scene; study = post(s, freeze_body(s)); bodies = [evaluate_body(study), evaluate_body(study)]
    calls = real_evaluator_counter(monkeypatch); barrier = Barrier(2)
    def send(body):
        barrier.wait(timeout=10)
        return s.client.post(s.diagnostic_base + '/' + study['id'] + '/evaluate', content=raw(body), headers={'Content-Type': 'application/json'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, bodies))
    assert sorted(x.status_code for x in responses) == [201, 409], [(x.status_code, x.text[:250]) for x in responses]
    assert len(calls) == 1
    winner = next(x.json() for x in responses if x.status_code == 201)
    loser_index = next(i for i, x in enumerate(responses) if x.status_code == 409)
    saved = lookup(s, bodies[loser_index]); assert saved['rejection']['code'] == 'already_evaluated'
    before = inventory(s)
    assert post(s, bodies[loser_index], study=study, expected=409)['detail'] == saved
    assert s.client.get(s.diagnostic_base + '/' + study['id'] + '/evaluation').json()['evaluation'] == winner
    assert inventory(s) == before and len(calls) == 1


def test_cross_operation_study_raw_and_annotation_scope_never_reuse_nonce(diagnostic_scene):
    s = diagnostic_scene; fb = freeze_body(s); study = post(s, fb); before = inventory(s)
    changed = s.client.post(s.diagnostic_base, content=json.dumps(fb, ensure_ascii=False, indent=2), headers={'Content-Type': 'application/json'})
    assert changed.status_code == 409 and inventory(s) == before
    post(s, evaluate_body(study, request_id=fb['request_id']), study=study, expected=409)
    assert inventory(s) == before
    other = post(s, freeze_body(s)); eb = evaluate_body(study); evaluation = post(s, eb, study=study)
    before = inventory(s)
    post(s, {**eb, 'study_record_hash': other['record_hash']}, study=other, expected=409)
    assert inventory(s) == before
    wrong_hash = s.client.get(s.diagnostic_base + '/requests', params={'request_id': eb['request_id'], 'raw_sha256': 'f' * 64})
    assert wrong_hash.status_code == 409
    newer = save(s, body_for(s.run, supersedes_annotation_id=s.annotation['id']))
    foreign = s.annotation_base + '/annotations/' + newer['id'] + '/diagnostics'
    before = inventory(s)
    assert s.client.get(foreign + '/' + study['id']).status_code == 404
    assert s.client.post(foreign, content=raw(fb), headers={'Content-Type': 'application/json'}).status_code == 409
    assert inventory(s) == before
    assert lookup(s, eb)['receipt'] == evaluation


@pytest.mark.parametrize('damage', ['study_audit', 'evaluation_audit', 'command_leaf', 'result_file'])
def test_independent_inventory_and_result_bytes_damage_never_repairs_or_hides_saved_result(diagnostic_scene, damage):
    s = diagnostic_scene; fb = freeze_body(s); study = post(s, fb); eb = evaluate_body(study); result = post(s, eb, study=study)
    path = None; original = None
    with Session(engine()) as db:
        if damage == 'study_audit':
            row = db.get(Study, study['id']); db.execute(delete(m.Audit).where(m.Audit.id == row.data['audit_id']))
        elif damage == 'evaluation_audit':
            row = db.get(Evaluation, result['id']); db.execute(delete(m.Audit).where(m.Audit.id == row.data['audit_id']))
        elif damage == 'command_leaf':
            db.execute(delete(Command).where(Command.request_id == eb['request_id']))
        else:
            path = settings().storage_dir / db.get(m.Evidence, result['result_artifact']['evidence_id']).storage_key
            original = path.read_bytes(); path.write_bytes(original + b' ')
        db.commit()
    before = inventory(s)
    try:
        response = s.client.get(s.diagnostic_base + '/' + study['id'] + '/evaluation')
        assert response.status_code == 409, response.text
        assert s.client.get(s.diagnostic_base + '/' + study['id'] + '/evaluation/artifact').status_code == 409
        assert s.client.get(s.diagnostic_base + '/requests', params={
            'request_id': eb['request_id'], 'raw_sha256': hashlib.sha256(raw(eb)).hexdigest()}).status_code == 409
        assert inventory(s) == before
    finally:
        if path is not None: path.write_bytes(original)


def test_other_operator_may_read_but_cannot_compute_another_creators_study(diagnostic_scene):
    s = diagnostic_scene; study = post(s, freeze_body(s)); password = uuid4().hex; username = 'diagnostic_' + uuid4().hex[:12]
    actor = s.client.post('/api/users', json={'username': username, 'password': password, 'role': 'operator'})
    assert actor.status_code == 201, actor.text
    with TestClient(s.client.app) as other:
        login = other.post('/api/login', json={'username': username, 'password': password})
        assert login.status_code == 200, login.text
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': study['workspace_id']})
        before = inventory(s); path = s.diagnostic_base + '/' + study['id']
        assert other.get(path).json() == study
        response = other.post(path + '/evaluate', content=raw(evaluate_body(study)), headers={'Content-Type': 'application/json'})
        assert response.status_code == 403, response.text
        assert inventory(s) == before
        own = other.post(s.diagnostic_base, content=raw(freeze_body(s)), headers={'Content-Type': 'application/json'})
        assert own.status_code == 201, own.text
        assert own.json()['actor_id'] == actor.json()['id'] and own.json()['actor_id'] != study['actor_id']
        response = other.post(path + '/evaluate', content=raw(evaluate_body(study)), headers={'Content-Type': 'application/json'})
        assert response.status_code == 403


def test_malformed_raw_cannot_create_commands_and_changed_pins_are_durable_rejections(diagnostic_scene):
    s = diagnostic_scene; body = freeze_body(s); good = raw(body); before = inventory(s)
    for malformed in [b'{"schema":1,"schema":2}', good[:-1] + b',"extra":true}', b'{"x":NaN}', b'\xff',
            b'[' * 1200 + b'0' + b']' * 1200, good + b' ' * 131072,
            raw({**body, 'request_id': 'nul-request-' + '\x00'}), raw({**body, 'candidate_threshold_text': True})]:
        response = s.client.post(s.diagnostic_base, content=malformed, headers={'Content-Type': 'application/json'})
        assert response.status_code in {413, 422}, response.text
    assert inventory(s) == before
    wrong = freeze_body(s, annotation_record_hash='0' * 64)
    rejected = post(s, wrong, expected=409)['detail']
    assert rejected['rejection']['code'] == 'annotation_changed' and lookup(s, wrong) == rejected
    study = post(s, body); changed = evaluate_body(study, study_record_hash='0' * 64)
    assert post(s, changed, study=study, expected=409)['detail']['rejection']['code'] == 'study_changed'


def test_all_unknown_annotation_returns_no_evaluable_pixels_not_perfect_accuracy(actual):
    s = actual; s.annotation = save(s, body_for(s.run, regions=[]))
    s.diagnostic_base = s.annotation_base + '/annotations/' + s.annotation['id'] + '/diagnostics'
    study = post(s, freeze_body(s)); result = post(s, evaluate_body(study), study=study)['result']
    assert result['baseline_score']['status'] == result['candidate_score']['status'] == 'no_evaluable_pixels'
    assert result['comparison'] is None
    assert all(value is None for value in result['baseline_score']['metrics'].values())
    assert result['release_authorized'] is False and result['field_truth_verified'] is False


def test_studies_use_chronological_keyset_and_reject_foreign_scope_cursor(diagnostic_scene):
    s = diagnostic_scene; saved = [post(s, freeze_body(s)) for _ in range(12)]
    expected = sorted(saved, key=lambda row: (row['created_at'], row['id']), reverse=True)
    before = inventory(s)
    response = s.client.get(s.diagnostic_base, params={'limit': 10})
    assert response.status_code == 200, response.text
    first = response.json(); assert first['items'] == expected[:10] and first['has_more']
    second = s.client.get(s.diagnostic_base, params={'limit': 10, 'before_id': first['next_cursor']})
    assert second.status_code == 200, second.text
    assert second.json()['items'] == expected[10:] and not second.json()['has_more'] and second.json()['next_cursor'] is None
    assert inventory(s) == before
    newer = save(s, body_for(s.run, supersedes_annotation_id=s.annotation['id']))
    foreign = s.annotation_base + '/annotations/' + newer['id'] + '/diagnostics'
    response = s.client.get(foreign, params={'before_id': saved[0]['id']})
    assert response.status_code == 404, response.text
    for params in [{'limit': 9}, {'limit': 21}, {'before_id': 'bad-cursor'}, {'before_id': str(uuid4())}]:
        response = s.client.get(s.diagnostic_base, params=params)
        assert response.status_code in {404, 422}, response.text
