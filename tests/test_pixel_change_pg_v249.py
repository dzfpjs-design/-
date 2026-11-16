"""Normal API/PG boundary tests; only installation probe is a declared fixture seam.

No successful inference substitute is used. Actual CPU inference lives in the
separate native test file and Root's product run.
"""
import copy
import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import PixelChangeRun, Job, Audit, Evidence, CloudCall, Entity, SpecialistInstallation, Membership
from generic_agents import pixel_change_runs_v249 as runs
from generic_agents import pixel_change_codec_v249 as codec
from generic_agents.worker import process_once, claim, SETUP_KINDS
from generic_agents.security import Principal
from tests.test_instance_workspace import prepared as prepare_instance, enable
from tests.test_changer_install import prepared, install, saved


def image(client, identity, *, color=80, size=(80, 64), mode='RGB', format='PNG'):
    stream = io.BytesIO(); Image.new(mode, size, color).save(stream, format=format)
    raw = stream.getvalue(); media = 'image/png' if format == 'PNG' else 'image/jpeg'
    response = client.post('/api/workspace/instances/' + identity + '/input-images',
        files={'file': ('public-engineering-image.' + format.lower(), raw, media)})
    assert response.status_code == 201, response.text
    assert response.json()['sha256'] == hashlib.sha256(raw).hexdigest()
    return response.json(), raw


@pytest.fixture
def scene(client, account, prepared):
    release, _, _ = prepare_instance(client); instance = enable(client, release)
    before, a = image(client, instance['id']); after, b = image(client, instance['id'], color=130)
    model = install(client); assert process_once(job_id=model['job_id'], deliver=False)
    model = saved(client, model['id']); assert model['status'] == 'ready'
    base = '/api/workspace/instances/' + instance['id'] + '/pixel-change'
    options = client.get(base + '/options'); assert options.status_code == 200, options.text
    options = options.json(); assert options['can_create']
    body = {'schema': 'pixel-change-create-v249', 'request_id': 'pixel/原号?保留-' + uuid4().hex,
        'instance_version': options['instance']['version'], 'release_id': release['id'],
        'before_image_id': before['id'], 'before_sha256': before['sha256'],
        'after_image_id': after['id'], 'after_sha256': after['sha256'],
        'model_installation_id': model['id'], 'model_generation': model['generation'],
        'model_recipe_hash': options['model_recipe_hash'], 'pixel_correspondence_confirmed': True,
        'full_image_present_confirmed': True, 'input_order': 'before_after', 'threshold_text': '0.5'}
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        p = Principal(member.user_id, member.workspace_id, member.role, 'pixel-test')
    return SimpleNamespace(client=client, base=base, instance=instance, release=release, body=body,
        before=before, after=after, raw_before=a, raw_after=b, model=model, p=p)


def create(s, **changes):
    response = s.client.post(s.base + '/runs', json={**s.body, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def inventories(p):
    with Session(engine()) as db:
        return [db.scalar(select(func.count()).select_from(cls).where(cls.workspace_id == p.workspace_id))
            for cls in (PixelChangeRun, Job, Audit, Evidence, CloudCall, Entity)]


def pause(s):
    instance = next(v for v in s.client.get('/api/instances').json() if v['id'] == s.instance['id'])
    response = s.client.post('/api/instances/' + instance['id'] + '/pause', json={'version': instance['version']})
    assert response.status_code == 200, response.text


def test_real_app_routes_have_single_workspace_prefix(client):
    from generic_agents.api import app
    paths = app.openapi()['paths']
    assert '/api/workspace/instances/{instance_id}/pixel-change/options' in paths
    assert '/api/workspace/instances/{instance_id}/pixel-change/runs/{run_id}/artifacts/{kind}' in paths
    assert not any('/api/workspace/api/workspace/' in p for p in paths)


def test_original_request_survives_pause_and_query_nonce_characters_readonly(scene):
    s = scene; run = create(s); count = inventories(s.p); pause(s)
    response = s.client.get(s.base + '/requests', params={'request_id': s.body['request_id'],
        'expected_body_hash': digest(s.body)})
    assert response.status_code == 200, response.text
    assert response.json()['run']['id'] == run['id'] and response.json()['found']
    assert s.client.post(s.base + '/runs', json=s.body).json()['id'] == run['id']
    assert s.client.get(s.base + '/runs/' + run['id']).json()['status'] == 'queued'
    assert s.client.get(s.base + '/options').json()['can_create'] is False
    assert s.client.post(s.base + '/runs', json={**s.body, 'request_id': str(uuid4())}).status_code == 409
    assert s.client.get(s.base + '/requests', params={'request_id': 'unknown-request-123',
        'expected_body_hash': 'a' * 64}).json()['state'] == 'unknown'
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert runs.recover(db, s.p, s.instance['id'], s.body['request_id'], digest(s.body))['run']['id'] == run['id']
    # Only the deliberate normal pause adds its audit; read/recover creates nothing.
    assert inventories(s.p) == [count[0], count[1], count[2] + 1, *count[3:]]


@pytest.mark.parametrize('change', ['body', 'source_hash', 'shape', 'mode', 'jpeg', 'duplicate'])
def test_wrong_input_or_reused_body_never_queues_inference(scene, change):
    s = scene; run = create(s); before = inventories(s.p); body = copy.deepcopy(s.body)
    expected = 422
    if change == 'body':
        body['threshold_text'] = '0.4'; expected = 409
    else:
        body['request_id'] = str(uuid4())
        if change == 'source_hash':
            body['before_sha256'] = 'f' * 64; expected = 409
        elif change == 'duplicate':
            body['after_image_id'] = s.before['id']; body['after_sha256'] = s.before['sha256']
        else:
            item, _ = image(s.client, s.instance['id'], color=140,
                size=(81, 64) if change == 'shape' else (80, 64),
                mode='L' if change == 'mode' else 'RGB', format='JPEG' if change == 'jpeg' else 'PNG')
            body['after_image_id'], body['after_sha256'] = item['id'], item['sha256']
    response = s.client.post(s.base + '/runs', json=body)
    assert response.status_code == expected, response.text
    after = inventories(s.p)
    assert after[:2] == before[:2] and after[4:] == before[4:]
    assert s.client.get(s.base + '/runs/' + run['id']).json()['status'] == 'queued'


def test_exact_scope_and_cancelled_generation_are_never_globally_claimed(scene):
    s = scene; run = create(s)
    assert runs.KIND in SETUP_KINDS
    assert claim(kinds=(runs.KIND,)) is None
    assert claim(kinds=(runs.KIND,), workspace_id=str(uuid4())) is None
    body = {'schema': 'pixel-change-command-v249', 'request_id': 'cancel-' + uuid4().hex, 'version': run['version']}
    response = s.client.post(s.base + '/runs/' + run['id'] + '/cancel', json=body)
    assert response.status_code == 200, response.text
    assert s.client.post(s.base + '/runs/' + run['id'] + '/cancel', json=body).json() == response.json()
    assert process_once(job_id=run['job_id'], deliver=False)
    assert s.client.get(s.base + '/runs/' + run['id']).json()['status'] == 'cancelled'
    with Session(engine()) as db:
        assert db.get(Job, run['job_id']).status == 'cancelled'


def test_real_failure_preserved_and_explicit_retry_uses_new_attempt(scene, monkeypatch):
    s = scene; run = create(s)
    def unavailable(*args, **kwargs):
        raise ValueError('测试故障：节点暂不可用；未产生模型成功结果')
    monkeypatch.setattr(codec, 'call', unavailable)
    assert process_once(job_id=run['job_id'], deliver=False)
    failed = s.client.get(s.base + '/runs/' + run['id']).json()
    assert failed['status'] == 'failed' and failed['result'] is None
    retry = {'schema': 'pixel-change-command-v249', 'request_id': 'retry-' + uuid4().hex, 'version': failed['version']}
    response = s.client.post(s.base + '/runs/' + run['id'] + '/retry', json=retry)
    assert response.status_code == 200, response.text
    queued = response.json()['run']
    assert queued['generation'] == 2 and queued['job_id'] != run['job_id']
    assert queued['attempts'][0] == failed['attempts'][0] and queued['attempts'][1]['status'] == 'queued'
    assert s.client.post(s.base + '/runs/' + run['id'] + '/retry', json=retry).json() == response.json()
    assert s.client.post(s.base + '/runs/' + run['id'] + '/retry', json={**retry, 'version': queued['version']}).status_code == 409


def test_historical_request_does_not_depend_on_current_model_ready(scene):
    s = scene; run = create(s)
    with Session(engine()) as db:
        model = db.get(SpecialistInstallation, s.model['id'])
        # Later installation-unavailable fault: original request remains readable.
        model.status = 'failed'; db.commit()
    assert s.client.get(s.base + '/runs/' + run['id']).status_code == 200
    response = s.client.get(s.base + '/requests', params={'request_id': s.body['request_id'], 'expected_body_hash': digest(s.body)})
    assert response.status_code == 200 and response.json()['run']['id'] == run['id']
    assert s.client.get(s.base + '/options').json()['can_create'] is False


def test_interrupted_original_job_is_not_automatic_permission_for_second_inference(scene, monkeypatch):
    s = scene; run = create(s)
    with Session(engine()) as db:
        job = db.get(Job, run['job_id'])
        # An expired/aborted first attempt left no successful output. Claiming
        # again is recovery only, not permission to repeat CPU work silently.
        job.attempts = 1; db.commit()
    monkeypatch.setattr(codec, 'call', lambda *a, **k: pytest.fail('interrupted attempt must not infer again'))
    assert process_once(job_id=run['job_id'], deliver=False)
    view = s.client.get(s.base + '/runs/' + run['id']).json()
    assert view['status'] == 'failed' and view['result'] is None and len(view['attempts']) == 1


def test_current_actor_revocation_fails_original_job_without_inference_or_erasing_request(scene, monkeypatch):
    s = scene; run = create(s)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == s.p.workspace_id,
            Membership.user_id == s.p.user_id))
        member.role = 'viewer'; db.commit()
    monkeypatch.setattr(codec, 'call', lambda *a, **k: pytest.fail('revoked actor must not infer'))
    assert process_once(job_id=run['job_id'], deliver=False)
    response = s.client.get(s.base + '/runs/' + run['id'])
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'failed' and response.json()['result'] is None
    assert s.client.post(s.base + '/runs', json={**s.body, 'request_id': str(uuid4())}).status_code == 403


def test_original_request_is_not_disclosed_through_a_different_instance(scene):
    from tests.test_instance_workspace import configuration
    s = scene; run = create(s)
    other, _, _ = prepare_instance(s.client, configuration(extra=True)); assert other['instance_id'] != s.instance['id']
    base = '/api/workspace/instances/' + other['instance_id'] + '/pixel-change'
    assert s.client.get(base + '/runs/' + run['id']).status_code == 404
    assert s.client.get(base + '/requests', params={'request_id': s.body['request_id'],
        'expected_body_hash': digest(s.body)}).status_code == 404


@pytest.mark.parametrize('damage', ['creation_audit', 'job_actor', 'source_sha'])
def test_internal_damage_is_integrity409_without_repair(scene, damage):
    s = scene; run = create(s)
    with Session(engine()) as db:
        row = db.get(PixelChangeRun, run['id'])
        if damage == 'creation_audit':
            db.delete(db.get(Audit, row.data['creation_audit_id']))
        elif damage == 'job_actor':
            job = db.get(Job, row.data['job_id']); job.data = {**job.data, 'actor_id': str(uuid4())}
        else:
            value = copy.deepcopy(row.data); value['sources']['before']['sha256'] = 'e' * 64
            value['sources_hash'] = digest(value['sources']); row.data = value
        db.commit()
    before = inventories(s.p)
    response = s.client.get(s.base + '/runs/' + run['id'])
    assert response.status_code == 409, response.text
    assert inventories(s.p) == before


def test_new_job_cannot_save_late_output_after_real_instance_pause(scene, monkeypatch):
    s = scene; run = create(s)
    def paused(*args, **kwargs):
        pause(s)
        kwargs['guard']()
        raise AssertionError('paused guard must stop before any output')
    monkeypatch.setattr(codec, 'call', paused)
    assert process_once(job_id=run['job_id'], deliver=False)
    result = s.client.get(s.base + '/runs/' + run['id'])
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'failed' and result.json()['result'] is None
    with Session(engine()) as db:
        assert not list(db.scalars(select(Evidence).where(Evidence.workspace_id == s.p.workspace_id,
            Evidence.data['purpose'].as_string() == 'pixel_change_output_v249')))


def test_command_nonce_cannot_be_reused_to_cancel_a_different_original_run(scene):
    s = scene; first = create(s); second = create(s, request_id=str(uuid4()))
    body = {'schema': 'pixel-change-command-v249', 'request_id': 'cancel-once-' + uuid4().hex, 'version': first['version']}
    response = s.client.post(s.base + '/runs/' + first['id'] + '/cancel', json=body)
    assert response.status_code == 200, response.text
    assert s.client.post(s.base + '/runs/' + second['id'] + '/cancel', json=body).status_code == 409
    assert s.client.get(s.base + '/runs/' + second['id']).json()['status'] == 'queued'


def test_damaged_job_target_cannot_mark_an_unrelated_queued_run_failed(scene, monkeypatch):
    s = scene; first = create(s); second = create(s, request_id=str(uuid4()))
    with Session(engine()) as db:
        target = db.get(PixelChangeRun, second['id']); prior = (target.status, target.version, digest(target.data))
        job = db.get(Job, first['job_id']); job.data = {**job.data, 'run_id': second['id']}; db.commit()
    monkeypatch.setattr(codec, 'call', lambda *a, **k: pytest.fail('damaged job must not infer'))
    assert process_once(job_id=first['job_id'], deliver=False)
    with Session(engine()) as db:
        target = db.get(PixelChangeRun, second['id'])
        assert (target.status, target.version, digest(target.data)) == prior
        assert db.get(Job, first['job_id']).status == 'failed'
        assert not list(db.scalars(select(Audit).where(Audit.workspace_id == s.p.workspace_id,
            Audit.object_id == second['id'], Audit.action == runs.FINISHED)))


def test_removing_terminal_marker_cannot_hide_the_independent_finished_history(scene):
    s = scene; original = create(s)
    body = {'schema': 'pixel-change-command-v249', 'request_id': 'cancel-history-' + uuid4().hex, 'version': original['version']}
    response = s.client.post(s.base + '/runs/' + original['id'] + '/cancel', json=body)
    assert response.status_code == 200, response.text
    with Session(engine()) as db:
        row = db.get(PixelChangeRun, original['id']); data = copy.deepcopy(row.data)
        data['attempts'][-1].update(status='queued', audit_id=None)
        row.data = data; row.status = 'queued'; db.commit()
    assert s.client.get(s.base + '/runs/' + original['id']).status_code == 409
