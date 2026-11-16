"""Genuine media/Daily ACK -> copied image -> ordinary Task transaction.

The existing image-workflow fixture is a legacy, normally generated instance.
Only V226.target (qualified plain-flow/trial eligibility) is an explicit seam;
that gate has separate predicate tests and requires Root's real plain-flow
runtime acceptance. No source check, ACK, file copy, Task helper, Audit or
creation transaction is replaced. External media/model IO is mocked by the
public media fixture; these tests do not establish real inference quality.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import media_daily_task_adapter as a, models as m
from generic_agents.contracts import digest
from generic_agents.media_daily_task_models import DailyTaskIntent as Intent, DailyTaskSlot as Slot
from tests.test_media_daily_task_source_v226 import daily_camera_source, media_runtime, mocked_runtime
from tests import test_workflow_images as image_fixture
from tests.test_media import capture, finish
from tests.test_instance_workspace import object_row
from tests.test_workflow_abilities import enable


def raw(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def counts(db, p):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(model.workspace_id == p.workspace_id))
        for model in (m.Task, m.Job, m.Invocation, m.Evidence, m.Event, m.Memory, m.CloudCall)}


@pytest.fixture
def scene(client, daily_camera_source, monkeypatch):
    source = daily_camera_source
    def existing_installation(client):
        with Session(source.bound) as db:
            installation_id = db.get(m.StreamSession, source.session_id).data['installation_id']
        response = client.get('/api/specialists/installations')
        assert response.status_code == 200
        rows = [row for row in response.json() if row['id'] == installation_id]
        assert len(rows) == 1 and rows[0]['status'] == 'ready'
        return rows[0]
    # Reuse the real normal installation already needed by the capture. Do
    # not reinstall, clear records or alter the existing shared test helper.
    monkeypatch.setattr(image_fixture, 'ready', existing_installation)
    release, _, _ = image_fixture.setup(client); enable(client, release); entity = object_row(client, release)
    identity = release['instance_id']; path = '/api/workspace/instances/'+identity+'/daily-task-intents'
    from generic_agents import instance_api
    def admission(db, p, identity, body, *, locked=False):
        # Explicit only-eligibility seam. The actual legacy source and normal
        # Task helper still perform their real release/entity/image gates.
        instance, released = instance_api.active(db, p, identity, locked)
        camera = a.scoped(db, m.MediaChannel, body['camera_id'], p)
        gateway = a.scoped(db, m.MediaGateway, camera.gateway_id, p)
        obj = a.scoped(db, m.Entity, body['entity_id'], p)
        a.need(body['instance_version'] == instance.version and body['release_id'] == released.id
            and body['configuration_hash'] == released.configuration_hash
            and body['business_id'] in instance.data['enabled_business_ids']
            and body['camera_version'] == camera.version and body['camera_hash'] == a.camera_hash(camera))
        return {'instance_id': identity, 'instance_version': instance.version, 'release_id': released.id,
            'configuration_hash': released.configuration_hash, 'business_id': body['business_id'],
            'business_name': '正常图片流程机械夹具', 'entity_id': obj.id, 'entity_version': obj.version,
            'entity_hash': digest(obj.data), 'camera_id': camera.id, 'camera_version': camera.version,
            'camera_hash': a.camera_hash(camera), 'gateway_id': gateway.id, 'gateway_version': gateway.version,
            'gateway_hash': digest(gateway.data), 'binding_hash': digest({'fixture_eligibility': 'explicit'}),
            'graph_hash': digest({'fixture_graph': 'legacy-real-Task-helper'}),
            'capabilities_hash': digest({'fixture': 'no-authority-claim'}), 'roles_hash': digest({'actor': p.user_id})}
    monkeypatch.setattr(a, 'target', admission)
    with Session(source.bound) as db:
        instance = db.get(m.DomainInstance, identity); released = db.get(m.InstanceRelease, release['id'])
        camera = db.get(m.MediaChannel, source.camera_id)
        body = {'schema': 'media-daily-task-preview-v226', 'instance_version': instance.version,
            'release_id': released.id, 'configuration_hash': released.configuration_hash,
            'business_id': released.data['snapshot']['businesses'][0]['id'], 'entity_id': entity['id'],
            'camera_id': camera.id, 'camera_version': camera.version, 'camera_hash': a.camera_hash(camera),
            'start_day': source.day, 'end_day': source.day, 'expires_at': (a.clock(db)+timedelta(days=1)).isoformat(),
            'max_tasks': 1, 'frame_rule': 'earliest_verified_frame', 'allow_incomplete': True}
    return NS(source=source, bound=source.bound, p=source.p, identity=identity, release=release,
        entity=entity, path=path, body=body, client=client, admission=admission)


def authorize(scene):
    preview = scene.client.post(scene.path+'/preview', content=raw(scene.body), headers={'content-type': 'application/json'})
    assert preview.status_code == 200, preview.text[:300]
    assert preview.json()['availability']['status'] == 'ready'
    body = {**scene.body, 'schema': 'media-daily-task-enable-v226', 'request_id': str(uuid4()),
        'preview_hash': preview.json()['preview_hash'], 'confirm_scope': True}
    exact = raw(body)
    saved = scene.client.post(scene.path+'/enable', content=exact, headers={'content-type': 'application/json'})
    assert saved.status_code == 201, saved.text[:300]
    return body, exact, saved.json()


def run_slot(scene, result):
    slot_id = result['intent']['slots'][0]['id']
    with Session(scene.bound) as db: assert a.process_slot(db, scene.p.workspace_id, slot_id)
    return slot_id


def test_real_ack_copy_and_normal_task_are_atomic_unique_and_frozen_recovery_survives_new_head(scene):
    with Session(scene.bound) as db:
        before = counts(db, scene.p)
    body, exact, result = authorize(scene); slot_id = run_slot(scene, result)
    with Session(scene.bound) as db:
        slot = db.get(Slot, slot_id); task = db.get(m.Task, slot.task_id); image = db.get(m.Evidence, slot.data['image_id'])
        old = db.get(m.Evidence, slot.data['source']['frame']['evidence_id'])
        assert old.id != image.id and old.data.get('purpose') != 'workflow_input_image'
        assert a.originals.read_original(a.originals.settings().storage_dir, scene.p.workspace_id, old) == a.originals.read_original(
            a.originals.settings().storage_dir, scene.p.workspace_id, image)
        assert old.sha256 == image.sha256 == slot.data['source']['frame']['sha256']
        assert image.data['purpose'] == 'workflow_input_image' and '资料未齐' in image.data['name']
        assert task.data['image_snapshots'][0]['id'] == image.id
        assert task.data['creation_request_id'] == slot.data['body']['request_id']
        assert task.data['entity_ids'] == [scene.entity['id']]
        assert task.data['not_current_state_update'] is True
        assert counts(db, scene.p)['tasks'] == before['tasks']+1
        assert counts(db, scene.p)['cloud_model_calls'] == before['cloud_model_calls']
        assert not a.process_slot(db, scene.p.workspace_id, slot_id)
        inventory = a.inventory(db, scene.p, scene.identity)
        assert len(inventory[0]) == len(inventory[1]) == 1
        task_id, request_body = task.id, copy.deepcopy(slot.data['body'])
    sha = hashlib.sha256(exact).hexdigest()
    recovered = scene.client.get(scene.path+'/requests/'+body['request_id'], params={'expected_sha256': sha})
    assert recovered.status_code == 200 and recovered.json()['result'] == result
    replay = scene.client.post(scene.path+'/enable', content=exact, headers={'content-type': 'application/json'})
    assert replay.status_code == 201 and replay.json() == result
    lookup = scene.client.post('/api/workspace/instances/'+scene.identity+'/task-requests/lookup', json=request_body)
    assert lookup.status_code == 200 and lookup.json()['found'] is True and lookup.json()['request']['task_id'] == task_id
    current = scene.client.get(scene.path)
    assert current.status_code == 200 and current.json()['intents'][0]['slots'][0]['task']['id'] == task_id
    with Session(scene.bound) as db: assert db.scalar(select(func.count()).select_from(Slot).where(Slot.task_id == task_id)) == 1


def test_default_incomplete_waits_and_explicit_new_same_scope_cannot_double_authorize(scene):
    body = {**scene.body, 'allow_incomplete': False}
    response = scene.client.post(scene.path+'/preview', json=body)
    assert response.status_code == 200 and response.json()['availability']['status'] == 'waiting_data'
    _, _, original = authorize(scene)
    response = scene.client.post(scene.path+'/preview', json=scene.body)
    next_body = {**scene.body, 'schema': 'media-daily-task-enable-v226', 'request_id': str(uuid4()),
        'preview_hash': response.json()['preview_hash'], 'confirm_scope': True}
    rejected = scene.client.post(scene.path+'/enable', json=next_body)
    assert rejected.status_code == 409
    with Session(scene.bound) as db: assert db.scalar(select(func.count()).select_from(Intent).where(Intent.instance_id == scene.identity)) == 1


def test_pause_and_withdrawn_role_stop_the_existing_slot_without_replacement(scene):
    _, _, result = authorize(scene)
    body = {'schema': 'media-daily-task-pause-v226', 'request_id': str(uuid4()),
        'expected_version': result['saved_version'], 'reason': '暂停有限试运行'}
    paused = scene.client.post(scene.path+'/'+result['intent']['id']+'/pause', json=body)
    assert paused.status_code == 200 and paused.json()['intent']['state'] == 'paused'
    with Session(scene.bound) as db:
        before = counts(db, scene.p)
        assert not a.process_slot(db, scene.p.workspace_id, result['intent']['slots'][0]['id'])
        assert counts(db, scene.p) == before


def test_pause_original_raw_cannot_be_replayed_against_another_intent_path(scene):
    _, _, first = authorize(scene)
    next_day = (date.fromisoformat(scene.body['start_day'])+timedelta(days=1)).isoformat()
    second_body = {**scene.body, 'start_day': next_day, 'end_day': next_day}
    preview = scene.client.post(scene.path+'/preview', json=second_body)
    assert preview.status_code == 200, preview.text[:300]
    enable_body = {**second_body, 'schema': 'media-daily-task-enable-v226', 'request_id': str(uuid4()),
        'preview_hash': preview.json()['preview_hash'], 'confirm_scope': True}
    saved = scene.client.post(scene.path+'/enable', json=enable_body)
    assert saved.status_code == 201, saved.text[:300]
    second = saved.json()
    pause = raw({'schema': 'media-daily-task-pause-v226', 'request_id': str(uuid4()),
        'expected_version': 1, 'reason': '仅暂停第一份授权'})
    first_path = scene.path+'/'+first['intent']['id']+'/pause'
    second_path = scene.path+'/'+second['intent']['id']+'/pause'
    response = scene.client.post(first_path, content=pause, headers={'content-type': 'application/json'})
    assert response.status_code == 200
    assert scene.client.post(first_path, content=pause, headers={'content-type': 'application/json'}).json() == response.json()
    assert scene.client.post(second_path, content=pause, headers={'content-type': 'application/json'}).status_code == 409
    with Session(scene.bound) as db:
        assert db.get(Intent, first['intent']['id']).status == 'paused'
        assert db.get(Intent, second['intent']['id']).status == 'enabled'


def test_two_real_consumers_create_one_normal_task_for_the_same_scope_day(scene):
    _, _, result = authorize(scene); slot_id = result['intent']['slots'][0]['id']
    barrier = Barrier(2)
    def consume():
        with Session(scene.bound) as db:
            barrier.wait(timeout=5)
            return a.process_slot(db, scene.p.workspace_id, slot_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume), pool.submit(consume)]
        values = [future.result(timeout=20) for future in futures]
    assert sorted(values) == [False, True]
    with Session(scene.bound) as db:
        slot = db.get(Slot, slot_id)
        assert slot.status == 'created' and slot.task_id is not None
        tasks = list(db.scalars(select(m.Task).where(m.Task.workspace_id == scene.p.workspace_id,
            m.Task.data[a.KEY]['slot_id'].as_string() == slot_id)))
        assert [task.id for task in tasks] == [slot.task_id]
        assert len(a.inventory(db, scene.p, scene.identity)[1]) == 1


@pytest.mark.parametrize('fault', ['revoked', 'create_after_partial_rows', 'expired_after_create'])
def test_live_failures_do_not_leave_a_successful_task_or_copied_evidence(scene, monkeypatch, fault):
    _, _, result = authorize(scene)
    from generic_agents import workspace_api
    original = workspace_api.create_task; reached = []
    if fault == 'revoked':
        with Session(scene.bound) as db:
            membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == scene.p.workspace_id,
                m.Membership.user_id == scene.p.user_id)); membership.role = 'viewer'; db.commit()
    else:
        def create(*args, **kwargs):
            task = original(*args, **kwargs); reached.append(task.id)
            if fault == 'create_after_partial_rows': raise HTTPException(409, 'injected after genuine Task construction')
            return task
        monkeypatch.setattr(workspace_api, 'create_task', create)
        if fault == 'expired_after_create':
            clock = a.clock
            monkeypatch.setattr(a, 'clock', lambda db: clock(db)+timedelta(days=2) if reached else clock(db))
    with Session(scene.bound) as db:
        before = counts(db, scene.p)
        assert not a.process_slot(db, scene.p.workspace_id, result['intent']['slots'][0]['id'])
        slot = db.get(Slot, result['intent']['slots'][0]['id'])
        assert slot.status == 'blocked' and slot.task_id is None and not slot.data.get('image_id')
        assert counts(db, scene.p) == before
    assert bool(reached) == (fault != 'revoked')


@pytest.mark.parametrize('corruption', ['command', 'state', 'image_saved', 'task_created', 'copy_bytes'])
def test_missing_independent_evidence_never_recovers_as_absent_or_recreates(scene, corruption):
    body, exact, result = authorize(scene); slot_id = run_slot(scene, result)
    with Session(scene.bound) as db:
        slot = db.get(Slot, slot_id)
        if corruption == 'copy_bytes':
            image = db.get(m.Evidence, slot.data['image_id'])
            path = a.originals.settings().storage_dir/image.storage_key
            assert path.is_file() and not path.is_symlink()
            path.write_bytes(b'damaged copied original')
        else:
            action, identity = {'command': (a.COMMAND, result['intent']['id']), 'state': (a.STATE, slot.id),
                'image_saved': ('workflow.image_saved', slot.data['image_id']), 'task_created': ('workflow.created', slot.task_id)}[corruption]
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == scene.p.workspace_id,
                m.Audit.action == action, m.Audit.object_id == identity).order_by(m.Audit.created_at.desc()))
            assert event is not None; db.delete(event); db.commit()
    response = scene.client.get(scene.path+'/requests/'+body['request_id'],
        params={'expected_sha256': hashlib.sha256(exact).hexdigest()})
    assert response.status_code == 409
    assert scene.client.get(scene.path).status_code == 409
    with Session(scene.bound) as db:
        before = counts(db, scene.p)
        with pytest.raises(HTTPException): a.process_slot(db, scene.p.workspace_id, slot_id)
        assert counts(db, scene.p) == before


def test_blocked_graph_transport_is_visible_even_while_task_itself_is_waiting(scene):
    _, _, result = authorize(scene); slot_id = run_slot(scene, result)
    with Session(scene.bound) as db:
        slot = db.get(Slot, slot_id); task = db.get(m.Task, slot.task_id)
        job = db.scalar(select(m.Job).where(m.Job.workspace_id == scene.p.workspace_id,
            m.Job.data['task_id'].as_string() == task.id))
        assert job is not None
        # Deliberate failure fixture, never a synthetic successful execution.
        job.status = 'blocked'; task.status = 'workflow_waiting'
        task.data = {**task.data, 'graph_jobs': {'failed-activation': {'job_id': job.id, 'phase': 'blocked'}}}
        db.commit(); task_id = task.id
    response = scene.client.get(scene.path)
    assert response.status_code == 200, response.text[:300]
    view = response.json()['intents'][0]
    assert view['state'] == 'blocked' and view['slots'][0]['label'] == '任务受阻，请查看原任务'
    assert view['slots'][0]['task']['id'] == task_id
    with Session(scene.bound) as db: assert not a.process_slot(db, scene.p.workspace_id, slot_id)


def current_camera_body(scene):
    with Session(scene.bound) as db:
        camera = db.get(m.MediaChannel, scene.source.camera_id)
        return {**scene.body, 'camera_version': camera.version, 'camera_hash': a.camera_hash(camera)}


def reconfirm_camera(scene):
    with Session(scene.bound) as db:
        camera = db.get(m.MediaChannel, scene.source.camera_id)
        version, generation = camera.version, camera.data['generation']
    response = scene.client.post('/api/media/channels/'+scene.source.camera_id+'/reconfirm', json={'version': version})
    assert response.status_code == 200 and response.json()['status'] == 'confirmed', response.text[:300]
    with Session(scene.bound) as db:
        camera = db.get(m.MediaChannel, scene.source.camera_id)
        assert camera.data['generation'] == generation+1
    return response.json()


def advance_camera_with_real_new_capture(scene):
    confirmed = reconfirm_camera(scene)
    with Session(scene.bound) as db:
        installation_id = db.get(m.StreamSession, scene.source.session_id).data['installation_id']
    installations = scene.client.get('/api/specialists/installations')
    assert installations.status_code == 200
    installation = next(row for row in installations.json() if row['id'] == installation_id)
    queued = capture(scene.client, confirmed, installation)
    assert queued.status_code == 201, queued.text[:300]
    captured, processed = finish(scene.client, queued.json())
    assert captured['status'] == 'captured' and processed['status'] == 'succeeded'
    assert processed['delivery']['data_received'] == len(processed['frames']) > 0
    with Session(scene.bound) as db:
        camera = db.get(m.MediaChannel, scene.source.camera_id)
        assert camera.status == 'validated'
        run = db.get(m.VideoRun, processed['id'])
        assert run.data['source_context']['channel_generation'] == camera.data['generation']
        assert db.get(m.Job, run.data['job_id']).status == 'succeeded'
    return processed['id']


def test_old_daily_generation_cannot_be_selected_under_a_new_current_configuration(scene):
    new_run_id = advance_camera_with_real_new_capture(scene)
    assert new_run_id != scene.source.run_id
    # No Daily reconcile yet: the original independently received publication
    # still contains only the old configuration's successfully processed run.
    preview = scene.client.post(scene.path+'/preview', json=current_camera_body(scene))
    assert preview.status_code == 200, preview.text[:300]
    assert preview.json()['availability']['status'] == 'waiting_data'
    assert preview.json()['availability']['source'] is None
    assert '旧配置代次' in preview.json()['availability']['reason']
    with Session(scene.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Task).where(
            m.Task.workspace_id == scene.p.workspace_id)) == 0


def test_selection_skips_an_older_generation_and_uses_the_next_real_received_record(scene):
    from generic_agents import video_daily, media_daily_delivery
    new_run_id = advance_camera_with_real_new_capture(scene)
    with Session(scene.bound) as db:
        video_daily.reconcile(db, scene.p.workspace_id); db.commit()
    received = media_daily_delivery.consume_once(scene.p.workspace_id)
    assert received['received'] == 1 and received['blocked'] == 0
    preview = scene.client.post(scene.path+'/preview', json=current_camera_body(scene))
    assert preview.status_code == 200, preview.text[:300]
    availability = preview.json()['availability']
    assert availability['status'] == 'ready' and availability['source']['run_id'] == new_run_id
    assert availability['source']['run_id'] != scene.source.run_id
    with Session(scene.bound) as db:
        frame = db.get(m.VideoFrame, availability['source']['frame_id'])
        assert frame.run_id == new_run_id
        assert frame.data['source_context']['channel_generation'] == db.get(m.MediaChannel, scene.source.camera_id).data['generation']


def test_current_failed_gateway_with_the_same_generation_can_select_genuine_received_data(scene, monkeypatch):
    from generic_agents import media_api
    with Session(scene.bound) as db:
        camera = db.get(m.MediaChannel, scene.source.camera_id); gateway = db.get(m.MediaGateway, camera.gateway_id)
        identity, version, generation = gateway.id, gateway.version, gateway.data['generation']
    def offline(_): raise HTTPException(502, '公开测试网关暂时离线')
    monkeypatch.setattr(media_api, 'inventory', offline)
    failed = scene.client.post('/api/media/gateways/'+identity+'/discover', json={'version': version})
    assert failed.status_code == 200 and failed.json()['status'] == 'failed'
    preview = scene.client.post(scene.path+'/preview', json=scene.body)
    assert preview.status_code == 200, preview.text[:300]
    assert preview.json()['availability']['status'] == 'ready'
    assert preview.json()['availability']['source']['run_id'] == scene.source.run_id
    with Session(scene.bound) as db: assert db.get(m.MediaGateway, identity).data['generation'] == generation


def test_original_task_and_nonce_history_survive_later_camera_reconfiguration(scene):
    body, exact, saved = authorize(scene); slot_id = run_slot(scene, saved)
    with Session(scene.bound) as db:
        slot = db.get(Slot, slot_id); original_task_id = slot.task_id; original_source = copy.deepcopy(slot.data['source'])
    reconfirm_camera(scene)
    current = scene.client.get(scene.path)
    assert current.status_code == 200, current.text[:300]
    view = current.json()['intents'][0]['slots'][0]
    assert view['task']['id'] == original_task_id and view['source']['run_id'] == scene.source.run_id
    recovered = scene.client.get(scene.path+'/requests/'+body['request_id'],
        params={'expected_sha256': hashlib.sha256(exact).hexdigest()})
    assert recovered.status_code == 200 and recovered.json()['result'] == saved
    with Session(scene.bound) as db:
        slot = db.get(Slot, slot_id)
        assert slot.task_id == original_task_id and slot.data['source'] == original_source
        assert len(a.inventory(db, scene.p, scene.identity)[1]) == 1


def queued_mechanical_scopes(scene, ready_id, count, *, kind='waiting'):
    """Separate-instance scheduler records, explicitly not approved intents.

    Only their scheduling callback is replaced. The real authorized target's
    Source/ACK/Task helper stays genuine; malformed synthetic records cannot
    contaminate its instance-scoped historical inventory or grant execution.
    """
    ids = []
    with Session(scene.bound) as db:
        at = a.clock(db)
        other = m.DomainInstance(workspace_id=scene.p.workspace_id, namespace='queue-fixture-'+str(uuid4()),
            status='draft', data={})
        db.add(other); db.flush()
        # IDs order before the real ready slot, so the first full batch cannot
        # accidentally prove fairness merely because it happened to be first.
        ready_number = int(ready_id.replace('-', ''), 16); assert ready_number > count+1
        for index in range(count):
            intent = Intent(workspace_id=scene.p.workspace_id, instance_id=other.id,
                actor_id=scene.p.user_id, status='paused' if kind == 'paused' else 'enabled',
                data={'configuration': {'expires_at': (at+timedelta(days=-1 if kind == 'expired' else 1)).isoformat(),
                    'max_tasks': 1}})
            db.add(intent); db.flush()
            day = (at.astimezone(a.CHINA).date()+timedelta(days=1)).isoformat() if kind == 'future' else scene.body['start_day']
            slot = Slot(id=str(UUID(int=ready_number-index-1)),
                workspace_id=scene.p.workspace_id, instance_id=other.id, intent_id=intent.id,
                business_id='mechanical-'+str(index), entity_id=scene.entity['id'], camera_id=scene.source.camera_id,
                day=day, status='waiting_data', data={'not_an_approved_scope': True})
            db.add(slot); db.flush(); ids.append(slot.id)
            if kind == 'limit':
                package = db.get(m.InstanceRelease, scene.release['id']).package_id
                failed = m.Task(workspace_id=scene.p.workspace_id, package_id=package, owner_id=scene.p.user_id,
                    title='仅额度否控的未成功记录', status='workflow_failed', data={'not_an_execution_proof': True})
                db.add(failed); db.flush()
                db.add(Slot(workspace_id=scene.p.workspace_id, instance_id=other.id, intent_id=intent.id,
                    business_id=slot.business_id+'-previous', entity_id=scene.entity['id'], camera_id=scene.source.camera_id,
                    day=day, status='created', task_id=failed.id, data={'not_an_execution_proof': True}))
        db.commit(); other_id = other.id
    return set(ids), other_id


def test_fifty_missing_scopes_rotate_then_real_received_frame_creates_its_normal_task(scene, monkeypatch):
    _, _, result = authorize(scene); ready_id = result['intent']['slots'][0]['id']
    waiting, _ = queued_mechanical_scopes(scene, ready_id, 50)
    actual = a.process_slot; reached = []
    def process(db, workspace_id, identity):
        reached.append(identity)
        return False if identity in waiting else actual(db, workspace_id, identity)
    monkeypatch.setattr(a, 'process_slot', process)
    first = a.consume_once(scene.p.workspace_id)
    assert first['created'] == 0 and set(reached) == waiting and ready_id not in reached
    reached.clear()
    second = a.consume_once(scene.p.workspace_id)
    assert second['created'] == 1 and ready_id in reached
    with Session(scene.bound) as db:
        ready = db.get(Slot, ready_id)
        assert ready.task_id and db.get(m.Task, ready.task_id).data['image_snapshots']
        assert all(row.last_polled_at is not None and row.status == 'waiting_data'
            for row in db.scalars(select(Slot).where(Slot.id.in_(waiting))))
        assert len(a.inventory(db, scene.p, scene.identity)[1]) == 1
    assert a.consume_once(scene.p.workspace_id)['created'] == 0


@pytest.mark.parametrize('kind', ['paused', 'expired', 'future', 'limit'])
def test_inactive_history_does_not_occupy_the_live_batch_or_reach_execution(scene, monkeypatch, kind):
    _, _, result = authorize(scene); ready_id = result['intent']['slots'][0]['id']
    excluded, other_id = queued_mechanical_scopes(scene, ready_id, 51, kind=kind)
    actual = a.process_slot; reached = []
    def process(db, workspace_id, identity):
        reached.append(identity)
        assert identity not in excluded, 'Retained inactive history must not enter the active execution queue'
        return actual(db, workspace_id, identity)
    monkeypatch.setattr(a, 'process_slot', process)
    assert a.consume_once(scene.p.workspace_id)['created'] == 1
    assert reached == [ready_id]
    with Session(scene.bound) as db:
        rows = list(db.scalars(select(Slot).where(Slot.id.in_(excluded))))
        assert len(rows) == 51 and all(row.status == 'waiting_data' and row.last_polled_at is None for row in rows)
        assert db.get(m.DomainInstance, other_id) is not None
