"""V233 source attacks against normal generic capture, ACK and Task creation.

Reuses V226's normal handlers, Jobs, frames, independent Daily receipt, copied
Evidence and Task helper. Its legacy image-flow eligibility predicate is an
explicit seam; transport/model execution is mocked. No Frame, ACK, Task or
successful Job is fabricated here, and this is not true-Graph qualification,
physical-camera, model-quality or three-user acceptance evidence.
"""
import copy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, media_daily_delivery as delivery
from generic_agents import media_daily_task_adapter as daily
from generic_agents import daily_frame_assessment_source_v233 as reader
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.media_daily_task_models import DailyTaskIntent, DailyTaskSlot
from generic_agents.security import Principal, password_hash
from tests.test_media_daily_task_pg_v226 import (
    scene as daily_scene, authorize, run_slot, reconfirm_camera,
    daily_camera_source, media_runtime, mocked_runtime,
)
from tests.test_instance_workspace import object_row, complete
from tests.test_workflow_abilities import finish_job


def counts(db, case):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(
        model.workspace_id == case.p.workspace_id)) for model in
        (m.Task, m.Job, m.Invocation, m.Evidence, m.Event, m.Memory, m.Audit, m.CloudCall)}


@pytest.fixture
def daily_frame_source(daily_scene, monkeypatch):
    scene = daily_scene
    assert scene.bound.dialect.name == 'postgresql' and scene.bound.url.database.startswith('ga_test_')
    assert settings().storage_dir == settings().storage_dir.resolve(strict=True)
    admission = daily.target

    def qualified_entity_pin(db, p, identity, body, *, locked=False):
        # Preserve V226's explicitly mechanical graph-eligibility seam, but
        # freeze the REAL production entity pin before any Enable is saved.
        # No existing Intent, Task or independent Audit is repaired/re-signed.
        pin = admission(db, p, identity, body, locked=locked)
        from generic_agents.entity_release_compat import require_entity
        instance = daily.scoped(db, m.DomainInstance, identity, p)
        release = daily.scoped(db, m.InstanceRelease, body['release_id'], p)
        entity = daily.scoped(db, m.Entity, body['entity_id'], p)
        definition = require_entity(db, p, instance, release, entity, lock_sources=False)
        assert pin['entity_version'] == entity.version
        return {**pin, 'entity_hash': digest({'data': entity.data, 'definition': definition})}

    monkeypatch.setattr(daily, 'target', qualified_entity_pin)
    _, _, enabled = authorize(scene)
    slot_id = run_slot(scene, enabled)
    with Session(scene.bound) as db:
        slot = db.get(DailyTaskSlot, slot_id)
        task = db.get(m.Task, slot.task_id)
        before = counts(db, scene)
        # Shared baseline is required for EVERY fault. A failure of the common
        # reader must become setup failure, never false-green attacks.
        bundle = reader.read_source(db, scene.p, task.id)
        assert bundle['task'].id == task.id and bundle['slot'].id == slot_id
        assert bundle['image'].id == slot.data['image_id']
        assert hashlib.sha256(bundle['content']).hexdigest() == bundle['frame_sha256']
        assert bundle['view']['source_hash'] == bundle['source_hash'] == digest(bundle['pin'])
        assert bundle['view']['time']['scene_observed_at'] is None
        assert bundle['view']['time']['scene_time_trust'] == 'unknown'
        assert bundle['view']['physical_state_verified'] is False
        assert bundle['view']['scope'] == 'saved_single_frame_not_verified_coverage'
        dependency = reader.dependency(bundle)
        checked = reader.recheck_dependency(db, scene.p, copy.deepcopy(dependency))
        assert checked['source_hash'] == bundle['source_hash']
        assert counts(db, scene) == before
        return SimpleNamespace(scene=scene, bound=scene.bound, p=scene.p,
            task_id=task.id, slot_id=slot.id, intent_id=slot.intent_id,
            image_id=slot.data['image_id'], origin=copy.deepcopy(slot.data['source']),
            pin=copy.deepcopy(bundle['pin']), source_hash=bundle['source_hash'],
            frame_sha256=bundle['frame_sha256'], dependency=copy.deepcopy(dependency))


def reread(db, case):
    return reader.read_source(db, case.p, case.task_id, expected_pin=copy.deepcopy(case.pin))


def rejects_original(case, *, fresh=True):
    with Session(case.bound) as db:
        before = counts(db, case)
        functions = [
            lambda: reread(db, case),
            lambda: reader.recheck_dependency(db, case.p, copy.deepcopy(case.dependency)),
        ]
        if fresh:
            # Fixed-scope failures must also reject a NEW preview. Merely
            # changing its hash and rejecting the old expected_pin is unsafe.
            functions.insert(0, lambda: reader.read_source(db, case.p, case.task_id))
        for function in functions:
            with pytest.raises(HTTPException) as error: function()
            assert error.value.status_code in {404, 409}
        assert counts(db, case) == before


def test_cold_reader_binds_the_actual_copy_and_never_grants_scene_or_object_truth(daily_frame_source):
    case = daily_frame_source
    with Session(case.bound) as db:
        bundle = reread(db, case)
        assert bundle['task'].data['entity_ids'] == [case.scene.entity['id']]
        assert bundle['view']['entity']['id'] == case.scene.entity['id']
        assert bundle['view']['release_id'] == case.scene.release['id']
        assert bundle['image'].id != case.origin['frame']['evidence_id']
        assert bundle['image'].sha256 == case.origin['frame']['sha256'] == case.frame_sha256
        assert bundle['view']['time']['attempt_day'] == case.scene.source.day
        assert bundle['view']['cloud_calls'] == 0
        assert bundle['view']['image']['content_path'] == '/api/workspace/tasks/' + case.task_id + '/daily-frame/content'


def test_original_slot_object_cannot_be_retargeted_by_actual_task_entity_ids(daily_frame_source):
    case = daily_frame_source
    other = object_row(case.scene.client, case.scene.release)
    assert other['id'] != case.scene.entity['id']
    with Session(case.bound) as db:
        assert reread(db, case)['source_hash'] == case.source_hash
        task = db.get(m.Task, case.task_id)
        task.data = {**task.data, 'entity_ids': [other['id']]}
        db.commit()
    rejects_original(case)


@pytest.mark.parametrize('fault', ['snapshot_attributes', 'resigned_snapshot', 'resigned_definition', 'missing_definition'])
def test_original_object_snapshot_and_definition_proof_are_not_replaced_by_fresh_hashes(daily_frame_source, fault):
    case = daily_frame_source
    with Session(case.bound) as db:
        original = reread(db, case)
        assert original['source_hash'] == case.source_hash
        task = db.get(m.Task, case.task_id)
        data = copy.deepcopy(task.data)
        assert data['object_definition_bindings']['objects'][0]['entity_id'] == case.scene.entity['id']
        if fault in {'snapshot_attributes', 'resigned_snapshot'}:
            snapshot = data['object_snapshots'][0]
            key = next(field['key'] for field in snapshot['fields']
                if field['kind'] == '文本' and isinstance(snapshot['attributes'].get(field['key']), str))
            snapshot['attributes'][key] += '（未经原来源确认的名称）'
            if fault == 'resigned_snapshot':
                binding = data['object_definition_bindings']['objects'][0]
                binding['attributes_hash'] = digest(snapshot['attributes'])
                data['object_definition_bindings_hash'] = digest(data['object_definition_bindings'])
                # The local snapshot and its proof are self-consistent, but
                # cannot replace the independently saved original Intent pin.
        elif fault == 'resigned_definition':
            binding = data['object_definition_bindings']['objects'][0]
            assert binding['definition']['contract_hash'] != '0' * 64
            binding['definition']['contract_hash'] = '0' * 64
            # A locally recomputed container hash is not a valid original
            # entity definition; the actual frozen snapshot must close too.
            data['object_definition_bindings_hash'] = digest(data['object_definition_bindings'])
        else:
            data.pop('object_definition_bindings')
            data.pop('object_definition_bindings_hash')
        task.data = data
        db.commit()
    # Both a new preview and the saved expected pin/dependency must fail.
    rejects_original(case)


def test_normal_current_entity_edit_preserves_the_original_frozen_object_input(daily_frame_source):
    from generic_agents.services import update_entity
    case = daily_frame_source
    with Session(case.bound) as db:
        original = reread(db, case)
        frozen = copy.deepcopy(original['task'].data['object_snapshots'])
        original_label = original['view']['entity']['label']
        entity = db.get(m.Entity, case.scene.entity['id'])
        attributes = copy.deepcopy(entity.data)
        key = next(field['key'] for field in frozen[0]['fields']
            if field['kind'] == '文本' and isinstance(attributes.get(field['key']), str))
        attributes[key] += '（后续正常修订）'
        version = entity.version
        updated = update_entity(db, case.p, entity.id, attributes, version, '当前对象正常修订，保留原任务冻结输入')
        assert updated.version == version + 1
        db.commit()
    with Session(case.bound) as db:
        result = reread(db, case)
        assert result['task'].data['object_snapshots'] == frozen
        assert result['view']['entity']['label'] == original_label
        assert result['source_hash'] == case.source_hash
        assert reader.recheck_dependency(db, case.p, case.dependency)['source_hash'] == case.source_hash


@pytest.mark.parametrize('field', ['instance_release_id', 'package_id', 'owner_id'])
def test_actual_task_fixed_release_package_or_owner_cannot_be_borrowed(daily_frame_source, field):
    case = daily_frame_source
    other_id = None
    if field == 'owner_id':
        username = 'frame_owner_' + uuid4().hex[:12]
        result = case.scene.client.post('/api/users', json={'username': username,
            'password': uuid4().hex, 'role': 'operator'})
        assert result.status_code == 201, result.text[:200]
    with Session(case.bound) as db:
        if field == 'owner_id':
            other_id = db.scalar(select(m.User.id).where(m.User.username == username))
            assert other_id is not None
        assert reread(db, case)['source_hash'] == case.source_hash
        task = db.get(m.Task, case.task_id)
        if field == 'instance_release_id':
            task.data = {**task.data, field: str(uuid4())}
        elif field == 'package_id':
            cap = db.scalar(select(m.Capability).where(m.Capability.workspace_id == case.p.workspace_id,
                m.Capability.kind == 'tool', m.Capability.id != task.package_id))
            assert cap is not None
            task.package_id = cap.id
        else:
            assert other_id != task.owner_id
            task.owner_id = other_id
        db.commit()
    rejects_original(case)


@pytest.mark.parametrize('resign', [False, True])
def test_original_release_snapshot_cannot_be_changed_or_self_resigned_under_the_same_id(daily_frame_source, resign):
    case = daily_frame_source
    with Session(case.bound) as db:
        release = db.get(m.InstanceRelease, case.scene.release['id'])
        old_hash = release.configuration_hash
        data = copy.deepcopy(release.data)
        snapshot = data['snapshot']
        snapshot['profile'] = {**snapshot.get('profile', {}),
            'v233_untrusted_configuration_change': 'same release identity is not authority'}
        assert digest(snapshot) != old_hash
        release.data = data
        if resign: release.configuration_hash = digest(snapshot)
        db.commit()
    # Fresh reads must reject too, not just an old expected_pin. A new local
    # hash cannot replace the original Intent/published/Task-creation proof.
    rejects_original(case)


@pytest.mark.parametrize('field', ['purpose', 'width', 'size_bytes', 'sha256', 'storage_key', 'media_type', 'extra_metadata'])
def test_copy_metadata_and_physical_columns_are_part_of_the_fixed_source(daily_frame_source, field):
    case = daily_frame_source
    with Session(case.bound) as db:
        image = db.get(m.Evidence, case.image_id)
        if field == 'purpose': image.data = {**image.data, field: 'ordinary_unbound_upload'}
        elif field == 'width': image.data = {**image.data, field: image.data[field] + 1}
        elif field == 'extra_metadata': image.data = {**image.data, 'unreviewed_extra_metadata': 'changed'}
        elif field == 'size_bytes': image.size_bytes += 1
        elif field == 'sha256': image.sha256 = 'f' * 64
        elif field == 'storage_key': image.storage_key = case.p.workspace_id + '/' + '0' * 64 + '.bin'
        else: image.media_type = 'image/png' if image.media_type == 'image/jpeg' else 'image/jpeg'
        db.commit()
    # expected_pin/dependency must detect the complete original row, including
    # extra metadata which need not itself be an independently invalid upload.
    rejects_original(case, fresh=field != 'extra_metadata')


def test_actual_other_workspace_member_cannot_read_or_recheck_this_origin(daily_frame_source):
    case = daily_frame_source
    with Session(case.bound) as db:
        workspace = m.Workspace(name='V233 foreign source negative')
        user = m.User(username='foreign_frame_' + uuid4().hex[:12], password_hash=password_hash(uuid4().hex))
        db.add_all([workspace, user]); db.flush()
        db.add(m.Membership(workspace_id=workspace.id, user_id=user.id, role='admin')); db.commit()
        foreign = Principal(user.id, workspace.id, 'admin', 'v233-actual-foreign-member')
    with Session(case.bound) as db:
        for call in (
            lambda: reader.read_source(db, foreign, case.task_id),
            lambda: reader.recheck_dependency(db, foreign, copy.deepcopy(case.dependency)),
        ):
            with pytest.raises(HTTPException) as error: call()
            assert error.value.status_code in {403, 404, 409}
        assert reread(db, case)['source_hash'] == case.source_hash


@pytest.mark.parametrize('kind', ['daily_publication', 'daily_received', 'frame_memory', 'slot_created', 'enable_command'])
def test_independent_ack_and_origin_proofs_cannot_be_recreated_or_replaced(daily_frame_source, kind):
    case = daily_frame_source
    with Session(case.bound) as db:
        if kind == 'frame_memory':
            value = db.get(m.Memory, case.origin['frame']['memory_id'])
        else:
            action, identity = {
                'daily_publication': (delivery.PUBLICATION, case.origin['event_id']),
                'daily_received': (delivery.TRANSITION, case.origin['event_id']),
                'slot_created': (daily.STATE, case.slot_id),
                'enable_command': (daily.COMMAND, case.intent_id),
            }[kind]
            rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
                m.Audit.action == action, m.Audit.object_id == identity)))
            if kind == 'slot_created':
                rows = [row for row in rows if row.data['snapshot']['status'] == 'created']
            elif kind == 'daily_received':
                rows = [row for row in rows if row.data['operation'] == 'received']
            assert len(rows) == 1
            value = rows[0]
        assert value is not None
        db.delete(value); db.commit()
    rejects_original(case)


@pytest.mark.parametrize('kind', ['task_marker', 'copy_marker'])
def test_removing_an_origin_marker_never_downgrades_the_source_to_a_plain_upload(daily_frame_source, kind):
    case = daily_frame_source
    with Session(case.bound) as db:
        row = db.get(m.Task, case.task_id) if kind == 'task_marker' else db.get(m.Evidence, case.image_id)
        data = copy.deepcopy(row.data)
        assert daily.KEY in data
        data.pop(daily.KEY); row.data = data; db.commit()
    rejects_original(case)


def metadata_snapshot(db, case):
    task, image = db.get(m.Task, case.task_id), db.get(m.Evidence, case.image_id)
    return (task.version, copy.deepcopy(task.data), image.version, image.sha256,
        image.storage_key, image.size_bytes, image.media_type, copy.deepcopy(image.data))


@pytest.mark.parametrize('kind', ['selected_frame', 'unselected_frame', 'video_truncated', 'frame_symlink', 'video_symlink'])
def test_cold_dependency_detects_actual_files_even_when_all_metadata_signatures_stay_equal(daily_frame_source, tmp_path, kind):
    case = daily_frame_source
    with Session(case.bound) as db:
        if kind.startswith('video'):
            identity = case.origin['video_evidence_id']
        elif kind == 'unselected_frame':
            frame = db.scalar(select(m.VideoFrame).where(m.VideoFrame.run_id == case.origin['run_id'],
                m.VideoFrame.sample_index > 0).order_by(m.VideoFrame.sample_index))
            assert frame is not None; identity = frame.evidence_id
        else:
            identity = case.origin['frame']['evidence_id']
        evidence = db.get(m.Evidence, identity)
        path = settings().storage_dir / evidence.storage_key
        original = path.read_bytes()
        assert hashlib.sha256(original).hexdigest() == evidence.sha256
        if kind.startswith('selected') or kind == 'frame_symlink':
            # The copied Evidence has a separate row but intentionally shares
            # the exact immutable SHA blob with the selected original frame.
            assert db.get(m.Evidence, case.image_id).storage_key == evidence.storage_key
        before = metadata_snapshot(db, case)
    try:
        if kind.endswith('symlink'):
            target = tmp_path / ('same-bytes-' + kind)
            target.write_bytes(original)
            path.unlink(); path.symlink_to(target.resolve(strict=True))
        else:
            path.write_bytes(original[:1] if kind == 'video_truncated' else b'isolated damaged frame')
        with Session(case.bound) as db:
            assert metadata_snapshot(db, case) == before
        rejects_original(case)
        if kind.endswith('symlink'): assert path.is_symlink(), 'Reader must not repair the source'
    finally:
        if path.is_symlink(): path.unlink()
        path.write_bytes(original)


def test_normal_workflow_progress_is_not_part_of_the_frozen_input_hash(daily_frame_source):
    case = daily_frame_source
    initial = case.scene.client.get('/api/workspace/tasks/' + case.task_id)
    assert initial.status_code == 200
    task = finish_job(case.scene.client, initial.json())
    result = complete(case.scene.client, task, note='仅复核公开合成画面来源，不作现场状态判断')
    assert result.status_code == 200, result.text[:200]
    assert result.json()['version'] > initial.json()['version']
    with Session(case.bound) as db:
        assert reread(db, case)['source_hash'] == case.source_hash
        assert reader.recheck_dependency(db, case.p, case.dependency)['source_hash'] == case.source_hash


@pytest.mark.parametrize('kind', ['pause', 'camera_reconfiguration'])
def test_history_remains_readable_after_normal_pause_or_camera_reconfiguration(daily_frame_source, kind):
    case = daily_frame_source
    if kind == 'pause':
        with Session(case.bound) as db: version = db.get(DailyTaskIntent, case.intent_id).version
        response = case.scene.client.post(case.scene.path + '/' + case.intent_id + '/pause', json={
            'schema': 'media-daily-task-pause-v226', 'request_id': str(uuid4()),
            'expected_version': version, 'reason': '停止未来发起，原画面核查仍保留'})
        assert response.status_code == 200, response.text[:200]
    else:
        reconfirm_camera(case.scene)
    with Session(case.bound) as db:
        assert reread(db, case)['source_hash'] == case.source_hash
        assert reader.recheck_dependency(db, case.p, case.dependency)['source_hash'] == case.source_hash


def test_caller_supplied_pin_json_cannot_authorize_a_different_projection(daily_frame_source):
    case = daily_frame_source
    forged = copy.deepcopy(case.pin)
    forged['caller_authorized_source'] = {'entity_id': str(uuid4())}
    with Session(case.bound) as db:
        with pytest.raises(HTTPException) as error:
            reader.read_source(db, case.p, case.task_id, expected_pin=forged)
        assert error.value.status_code in {404, 409}
        assert reread(db, case)['source_hash'] == case.source_hash
