"""Normal capture/processing/independent ACK -> immutable automation input.

Public media fixture replaces external RTSP/model IO only. All saved Jobs,
Daily revisions, receiver memories and evidence come from normal handlers.
These tests establish source/provenance, not field model accuracy or a valid
automation subscription. Root owns real service and normal Task acceptance.
"""
import copy
import hashlib
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m, video_daily
from generic_agents import business_automation_sources as source_api
from generic_agents import media_daily_delivery as delivery
from generic_agents import media_api
from generic_agents.config import settings
from tests.test_media_daily_task_source_v226 import daily_camera_source, media_runtime, mocked_runtime, inventory
from tests.test_instance_workspace import prepared, enable, object_row
from tests.test_media import capture, finish


@pytest.fixture
def source_scene(client, daily_camera_source):
    source = daily_camera_source
    with Session(source.bound) as db:
        camera = db.get(m.MediaChannel, source.camera_id)
        version = camera.version
    registered = client.post('/api/media/channels/'+source.camera_id+'/register', json={'version':version})
    assert registered.status_code == 200, registered.text[:300]
    release, _, _ = prepared(client)
    enable(client, release)
    entity = object_row(client, release)
    selection = {'release_id':release['id'], 'business_id':release['available_business_ids'][0],
        'entity_id':entity['id'], 'camera_id':source.camera_id, 'provider_id':source_api.PROVIDER,
        'trigger_id':'new_data', 'limits':{'max_task_starts_per_day':None,'min_interval_seconds':0}}
    with Session(source.bound) as db:
        instance = db.get(m.DomainInstance, release['instance_id'])
        released = db.get(m.InstanceRelease, release['id'])
        business = next(row for row in released.data['snapshot']['businesses'] if row['id'] == selection['business_id'])
        target = source_api.validate_selection(db, source.p, instance, released, business, selection)
        received = db.get(m.Event, source.event_id).received_at
        units = source_api.new_units(db, source.p, selection, target,
            authorized_at=received-timedelta(seconds=1), at=received+timedelta(seconds=1))
        assert len(units) == 1 and units[0]['run_id'] == source.run_id
    return NS(source=source, client=client, selection=selection, target=target, unit=units[0],
        received=received, release=release, instance_id=release['instance_id'])


def test_normal_registered_source_and_copy_preserve_original_bytes_and_independent_receivers(source_scene):
    s = source_scene
    with Session(s.source.bound) as db:
        before = inventory(db, s.source)
        directory = source_api.catalog(db, s.source.p, s.instance_id, s.release['id'], s.selection['business_id'])
        assert any(row['id'] == s.selection['entity_id'] for row in directory['entities'])
        assert any(row['id'] == s.source.camera_id and row['available'] for row in directory['cameras'])
        pin, original, raw = source_api.checked_unit(db, s.source.p, s.unit, s.target)
        assert pin['scene_time_trust'] == 'unknown' and pin['run']['id'] == s.source.run_id
        assert hashlib.sha256(raw).hexdigest() == original.sha256 == pin['image']['sha256']
        assert inventory(db, s.source) == before
        original_data = copy.deepcopy(original.data)
        image = source_api.materialize_input(db, s.source.p, db.get(m.DomainInstance, s.instance_id), s.unit)
        assert image.id != original.id and image.sha256 == original.sha256
        assert image.data['purpose'] == 'workflow_input_image'
        assert image.data[source_api.KEY] == {'unit':s.unit} and original.data == original_data
        assert (settings().storage_dir/image.storage_key).read_bytes() == raw
        assert inventory(db, s.source)['tasks'] == before['tasks']
        assert inventory(db, s.source)['jobs'] == before['jobs']
        db.commit()
        identity = image.id
    with Session(s.source.bound) as db:
        image = db.get(m.Evidence, identity)
        assert image.data[source_api.KEY] == {'unit':s.unit}
        assert hashlib.sha256((settings().storage_dir/image.storage_key).read_bytes()).hexdigest() == image.sha256


def test_second_actual_capture_changes_progress_not_authorized_source_and_republication_cannot_rearm_old_run(source_scene):
    s = source_scene
    with Session(s.source.bound) as db:
        camera = db.get(m.MediaChannel, s.source.camera_id)
        old_version = camera.version
        installation = {'id':db.get(m.StreamSession, s.source.session_id).data['installation_id']}
        authorized_at = db.scalar(select(func.clock_timestamp()))
    queued = capture(s.client, {'id':s.source.camera_id, 'version':old_version}, installation)
    assert queued.status_code == 201, queued.text[:300]
    captured, processed = finish(s.client, queued.json())
    assert captured['status'] == 'captured' and processed['status'] == 'succeeded'
    with Session(s.source.bound) as db:
        camera, current = source_api._camera(db, s.source.p, s.source.camera_id)
        assert camera.version > old_version and camera.data['validation']['video_run_id'] == processed['id']
        assert all(s.target[key] == value for key,value in current.items())
        # Video completion and frame delivery are not the separate Daily ACK.
        assert source_api.new_units(db, s.source.p, s.selection, s.target,
            authorized_at=authorized_at, at=db.scalar(select(func.clock_timestamp()))) == []
        video_daily.reconcile(db, s.source.p.workspace_id)
        db.commit()
    assert delivery.consume_once(s.source.p.workspace_id)['received'] >= 1
    with Session(s.source.bound) as db:
        at = db.scalar(select(func.clock_timestamp()))
        units = source_api.new_units(db, s.source.p, s.selection, s.target, authorized_at=authorized_at, at=at)
        assert [unit['run_id'] for unit in units] == [processed['id']]
        source_api.checked_unit(db, s.source.p, units[0], s.target)
        # Discovery still follows original exact revision, despite a new head.
        assert source_api.checked_unit(db, s.source.p, s.unit, s.target)[0] == s.unit['source_pin']


def test_occurrence_antijoin_is_authorization_scoped_and_does_not_require_timestamp_cursor(source_scene):
    s = source_scene
    subscription_id, authorization = str(uuid4()), 'a'*64
    with Session(s.source.bound) as db:
        # This deliberately unproved row tests only the SQL discovery filter.
        # The consumer must independently authenticate its occurrence ledger.
        job = m.Job(workspace_id=s.source.p.workspace_id, kind='business_automation_start',
            idempotency_key='synthetic-filter-'+str(uuid4()), data={
                'subscription_id':subscription_id, 'authorization_hash':authorization,
                'origin':{'trigger_id':'new_data','unit_id':s.source.run_id}})
        db.add(job); db.flush()
        kwargs = {'authorized_at':s.received-timedelta(seconds=1), 'at':s.received+timedelta(seconds=1),
            'limit':1, 'exclude_occurrence_for':{'subscription_id':subscription_id,'authorization_hash':authorization}}
        assert source_api.new_units(db, s.source.p, s.selection, s.target, **kwargs) == []
        kwargs['exclude_occurrence_for']['authorization_hash'] = 'b'*64
        assert [unit['run_id'] for unit in source_api.new_units(db, s.source.p, s.selection, s.target, **kwargs)] == [s.source.run_id]
        kwargs['exclude_occurrence_for']['authorization_hash'] = authorization
        kwargs['exclude_occurrence_for']['subscription_id'] = str(uuid4())
        assert len(source_api.new_units(db, s.source.p, s.selection, s.target, **kwargs)) == 1


@pytest.mark.parametrize('fault', ['generation','path','read_authorized','gateway_generation'])
def test_real_source_configuration_changes_are_rejected_but_original_saved_unit_is_preserved(source_scene, fault):
    s = source_scene
    with Session(s.source.bound) as db:
        source_api.checked_unit(db, s.source.p, s.unit, s.target)
        camera = db.get(m.MediaChannel, s.source.camera_id)
        if fault == 'generation': camera.data = {**camera.data,'generation':camera.data['generation']+1}
        elif fault == 'path': camera.path = 'changed/source'
        elif fault == 'read_authorized': camera.data = {**camera.data,'read_authorized':False}
        else:
            gateway = db.get(m.MediaGateway, camera.gateway_id)
            gateway.data = {**gateway.data,'generation':gateway.data['generation']+1}
        db.flush()
        if fault == 'path':
            _, changed = source_api._camera(db, s.source.p, s.source.camera_id)
            assert changed['camera_hash'] != s.target['camera_hash']
        else:
            with pytest.raises(HTTPException) as error: source_api._camera(db, s.source.p, s.source.camera_id)
            assert error.value.status_code == 409
        # No caller config is used by the original-byte checker; it follows
        # the sealed historical receiving revision rather than current health.
        assert source_api.checked_unit(db, s.source.p, s.unit, s.target)[0] == s.unit['source_pin']


@pytest.mark.parametrize('kind', ['image','video'])
def test_corrupt_original_bytes_never_become_materialized_task_input(source_scene, kind):
    s = source_scene
    with Session(s.source.bound) as db:
        source_api.checked_unit(db, s.source.p, s.unit, s.target)
        identity = s.unit['source_pin']['image']['id'] if kind == 'image' else db.get(m.VideoRun, s.source.run_id).evidence_id
        path = settings().storage_dir/db.get(m.Evidence, identity).storage_key
        original = path.read_bytes()
    try:
        path.write_bytes(b'explicit-test-corruption-not-observation')
        with Session(s.source.bound) as db:
            before = inventory(db, s.source)
            with pytest.raises(HTTPException) as error:
                source_api.materialize_input(db, s.source.p, db.get(m.DomainInstance, s.instance_id), s.unit)
            assert error.value.status_code == 409 and inventory(db, s.source) == before
            assert path.read_bytes() == b'explicit-test-corruption-not-observation'
    finally: path.write_bytes(original)


def test_missing_independent_daily_ack_memory_cannot_be_recreated_by_discovery(source_scene):
    s = source_scene
    with Session(s.source.bound) as db:
        source_api.checked_unit(db, s.source.p, s.unit, s.target)
        proof = delivery.checked(db, s.source.p, db.get(m.Event, s.source.event_id))
        db.delete(db.get(m.Memory, proof['state']['memory_id'])); db.flush()
        before = inventory(db, s.source)
        with pytest.raises(HTTPException) as error: source_api.checked_unit(db, s.source.p, s.unit, s.target)
        assert error.value.status_code == 409 and inventory(db, s.source) == before


def test_foreign_workspace_unit_is_a_generic_integrity_conflict_without_reference_disclosure(source_scene):
    from generic_agents.security import Principal
    s = source_scene
    wrong_scope = Principal(s.source.p.user_id, str(uuid4()), 'admin', 'explicit-wrong-scope-test')
    with Session(s.source.bound) as db:
        before = inventory(db, s.source)
        source_api.checked_unit(db, s.source.p, s.unit, s.target)
        with pytest.raises(HTTPException) as error: source_api.checked_unit(db, wrong_scope, s.unit, s.target)
        assert error.value.status_code == 409
        assert s.source.event_id not in str(error.value.detail) and s.source.p.workspace_id not in str(error.value.detail)
        assert str(error.value.detail) == '原接收资料或保存原件无法核对；未补造输入或继续派发'
        assert inventory(db, s.source) == before


@pytest.mark.parametrize('status', [403, 409, 422])
def test_saved_unit_conflict_wrapper_preserves_explicit_permission_and_validation_failures(monkeypatch, status):
    original = HTTPException(status, '明确权限或校验失败')
    def fail(*args, **kwargs): raise original
    monkeypatch.setattr(source_api, '_checked_unit', fail)
    with pytest.raises(HTTPException) as error: source_api.checked_unit(None, None, {})
    assert error.value is original


@pytest.mark.parametrize('unit', [None, {}, {'unit_id':'missing'}, {'unit_id':'x','run_id':'y'}])
def test_malformed_stored_unit_returns_recoverable_conflict_without_db_access(unit):
    with pytest.raises(HTTPException) as error: source_api.checked_unit(None, None, unit)
    assert error.value.status_code == 409


def source_mutation(s, db, kind):
    """The original real media endpoint handlers, with their actual lock order.

    Authentication is supplied by the existing fixture Principal; HTTP auth is
    outside this narrow PostgreSQL row-lock acceptance test.
    """
    camera = db.get(m.MediaChannel, s.source.camera_id)
    if kind == 'disable':
        return media_api.disable(camera.id, media_api.Version(version=camera.version), s.source.p, db)
    gateway = db.get(m.MediaGateway, camera.gateway_id)
    body = media_api.GatewayEdit(version=gateway.version, name='明确变更的测试媒体网关',
        api_origin=gateway.data['api_origin'], rtsp_origin=gateway.data['rtsp_origin'])
    return media_api.edit(gateway.id, body, s.source.p, db)


@pytest.mark.parametrize('kind', ['disable', 'gateway_edit'])
def test_write_source_fence_linearizes_real_media_mutation_after_dispatch_commit_without_deadlock(source_scene, kind):
    s = source_scene
    identities = queue.Queue()
    def change_source():
        with Session(s.source.bound) as other:
            other.execute(text("SET LOCAL lock_timeout = '2500ms'"))
            other.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            identities.put(other.scalar(select(func.pg_backend_pid())))
            return source_mutation(s, other, kind)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(s.source.bound) as db:
            db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            # Real caller order: Actor -> Instance -> existing definition
            # sources -> Gateway SHARE -> Channel SHARE. No reverse outer lock.
            source_api.current_actor(db, s.source.p, write=True)
            instance = source_api.scoped(db, m.DomainInstance, s.instance_id, s.source.p, True)
            released = db.get(m.InstanceRelease, s.release['id'])
            business = next(row for row in released.data['snapshot']['businesses']
                if row['id'] == s.selection['business_id'])
            target = source_api.validate_selection(db, s.source.p, instance, released, business, s.selection, write=True)
            assert target == s.target
            future = pool.submit(change_source)
            pid = identities.get(timeout=3)
            try:
                deadline = time.monotonic()+2
                waiting = False
                while time.monotonic() < deadline:
                    db.execute(text('SELECT pg_stat_clear_snapshot()'))
                    waiting = db.scalar(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'),
                        {'pid':pid}) == 'Lock'
                    if waiting: break
                    time.sleep(.01)
                assert waiting and not future.done(), 'Media mutation must wait for the actual dispatch source fence'
                # The dispatch can still verify its exact saved input before
                # committing; revocation cannot sneak into this final window.
                source_api.checked_unit(db, s.source.p, s.unit, target)
                db.commit()
            finally:
                # Release our own locks even on an assertion failure; never
                # leave a blocked endpoint or unbounded executor join behind.
                db.rollback()
            changed = future.result(timeout=6)
            assert changed['status'] == ('disabled' if kind == 'disable' else 'draft')
    with Session(s.source.bound) as db:
        with pytest.raises(HTTPException) as error: source_api._camera(db, s.source.p, s.source.camera_id, write=True)
        assert error.value.status_code == 409


@pytest.mark.parametrize('kind', ['disable', 'gateway_edit'])
def test_write_fence_refreshes_cached_source_when_real_change_commits_first(source_scene, kind):
    s = source_scene
    with Session(s.source.bound) as db:
        # Deliberately populate this Session's identity map before the other
        # transaction changes the source; a SELECT without populate_existing
        # would otherwise return this stale validated ORM object.
        camera = db.get(m.MediaChannel, s.source.camera_id)
        gateway = db.get(m.MediaGateway, camera.gateway_id)
        assert camera.status == 'validated'
        with Session(s.source.bound) as other: source_mutation(s, other, kind)
        with pytest.raises(HTTPException) as error: source_api._camera(db, s.source.p, camera.id, write=True)
        assert error.value.status_code == 409 and camera.status == 'disabled'
        if kind == 'gateway_edit': assert gateway.status == 'draft'
