"""Real ORM release/Task/image inventory and original PNG bytes.

The inherited wait fixture has an explicitly synthetic graph/source authority
seam. Entity definition compatibility is the only additional authority seam:
real instance/release/entity rows and their namespace/package links are checked
below, without claiming a genuine V184/175 source approval. Inventory, image
snapshots, scoped SQL reads, Evidence metadata, filesystem SHA and image parser
are real. No Gateway execution, successful Invocation or paid result is made.
"""
import copy
import hashlib
import io
from pathlib import Path
import stat

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import entity_release_compat, models as m
from generic_agents import workflow_data_quality_v219 as quality
from generic_agents import workflow_images
from generic_agents.config import settings
from generic_agents.evidence import save_evidence
from tests.test_workflow_provenance_wait_pg_v197 import scene as wait_scene


@pytest.fixture
def image_inventory(wait_scene, monkeypatch):
    s = wait_scene
    root = Path(settings().storage_dir)
    assert s.bound.url.database.startswith('ga_test_')
    assert root.name.startswith('ga-test-evidence-')
    assert stat.S_ISDIR(root.lstat().st_mode) and not root.is_symlink()
    # Canonicalize only conftest's disposable storage. Production NOFOLLOW
    # remains unchanged, including for a symlink in any supplied root ancestor.
    monkeypatch.setattr(settings(), 'storage_dir', root.resolve(strict=True))
    encoded = io.BytesIO()
    Image.new('RGB', (12, 9), 'green').save(encoded, format='PNG')
    s.image_bytes = encoded.getvalue()
    s.image_sha256 = hashlib.sha256(s.image_bytes).hexdigest()
    s.compatibility_reads = []
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task_id)
        release = db.get(m.InstanceRelease, s.release_id)
        assert isinstance(release, m.InstanceRelease) and not hasattr(release, 'snapshot')
        image = save_evidence(db, s.p, s.image_bytes, 'image/png')
        image.data = {**image.data, 'name': 'public-original.png',
            'purpose': 'workflow_input_image', 'instance_id': task.data['instance_id']}
        db.flush()
        s.image_id = image.id
        s.image_snapshot = workflow_images.view(image)
        entity = db.get(m.Entity, task.data['entity_ids'][0])
        # The normal Task creation shape also always includes object context.
        # This is real ORM entity data, not another declared original file.
        s.object_snapshot = {'id': entity.id, 'version': entity.version,
            'type_name': '公开合成资料对象', 'attributes': copy.deepcopy(entity.data),
            'fields': [{'key': 'test_only', 'label': '合成对象标志', 'kind': '是否', 'required': True}]}
        task.data = {**task.data, 'image_snapshots': [copy.deepcopy(s.image_snapshot)],
            'object_snapshots': [copy.deepcopy(s.object_snapshot)]}
        db.commit()

    def source_definition_seam(db, p, instance, release, entity, *, lock_sources=True):
        assert lock_sources is False
        assert (p.user_id, p.workspace_id) == (s.p.user_id, s.p.workspace_id)
        assert isinstance(instance, m.DomainInstance) and isinstance(release, m.InstanceRelease)
        assert isinstance(entity, m.Entity)
        assert db.get(m.InstanceRelease, release.id) is release
        task = db.get(m.Task, s.task_id)
        assert task.data['object_snapshots'] == [s.object_snapshot]
        assert instance.workspace_id == release.workspace_id == entity.workspace_id == p.workspace_id
        assert instance.id == release.instance_id == task.data['instance_id']
        assert release.id == task.data['instance_release_id']
        assert entity.namespace == instance.namespace and entity.schema_capability_id == release.package_id
        assert entity.id in task.data['entity_ids']
        s.compatibility_reads.append(entity.id)
        return {'synthetic_source_authority_only': True, 'entity_id': entity.id, 'release_id': release.id}

    monkeypatch.setattr(entity_release_compat, 'require_entity', source_definition_seam)
    return s


def counts(db, s):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model)
        .where(model.workspace_id == s.p.workspace_id))
        for model in (m.Task, m.Job, m.Invocation, m.Evidence, m.Audit, m.Memory, m.CloudCall)}


def test_real_inventory_reads_orm_data_snapshot_and_frozen_png_then_verifies_actual_bytes(image_inventory):
    s = image_inventory
    with Session(s.bound) as db:
        before = counts(db, s)
        task = db.get(m.Task, s.task_id)
        rows, issues, covered, source_hash, entity_hash = quality.inventory(db, s.p, task)
        assert issues == [] and covered == ['image']
        assert len(rows) == 1 and isinstance(rows[0]['row'], m.Evidence)
        assert rows[0]['row'].id == s.image_id and rows[0]['row'].sha256 == s.image_sha256
        assert rows[0]['row'].media_type == 'image/png' and rows[0]['parse_proof'] is False
        assert rows[0]['refs'] == [{'kind': 'image', 'id': s.image_id,
            'selection_hash': quality.digest(s.image_snapshot)}]
        files, actual_issues = quality.inspect_files(rows, root=settings().storage_dir,
            workspace_id=s.p.workspace_id, source_issues=issues)
        assert actual_issues == [], actual_issues
        assert len(files) == 1
        assert files[0]['expected_sha256'] == files[0]['actual_sha256'] == s.image_sha256
        assert files[0]['hash_verified'] is True and files[0]['format_verified'] is True
        assert files[0]['format_method'] == 'bounded_image_parser'
        assert files[0]['size_bytes'] == len(s.image_bytes)
        assert quality.SHA.fullmatch(source_hash) and quality.SHA.fullmatch(entity_hash)
        assert s.compatibility_reads == task.data['entity_ids']
        assert counts(db, s) == before and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('snapshot', [None, [], 'not-a-snapshot', {},
    {'businesses': None}, {'businesses': {}}, {'businesses': []},
    {'businesses': ['not-a-business']}, {'businesses': [{}]},
    {'businesses': [{'id': True}]}, {'businesses': [{'id': '   '}]},
    {'businesses': [{'id': 'wait_business'}, {'id': 'wait_business'}]}])
def test_missing_or_malformed_saved_snapshot_returns_clear_409_without_false_inventory(image_inventory, snapshot):
    s = image_inventory
    with Session(s.bound) as db:
        release = db.get(m.InstanceRelease, s.release_id)
        release.data = {'snapshot': copy.deepcopy(snapshot)}
        db.commit()
    with Session(s.bound) as db:
        before = counts(db, s)
        with pytest.raises(HTTPException) as error:
            quality.inventory(db, s.p, db.get(m.Task, s.task_id))
        assert error.value.status_code == 409
        assert '业务快照' in str(error.value.detail)
        assert s.compatibility_reads == []
        assert counts(db, s) == before


def test_absent_snapshot_key_and_unknown_task_business_fail_closed(image_inventory):
    s = image_inventory
    for value, message in (({}, '业务快照'), ({'snapshot': {'businesses': [{'id': 'another_business'}]}}, '原任务业务')):
        with Session(s.bound) as db:
            db.get(m.InstanceRelease, s.release_id).data = copy.deepcopy(value)
            db.commit()
        with Session(s.bound) as db:
            with pytest.raises(HTTPException) as error:
                quality.inventory(db, s.p, db.get(m.Task, s.task_id))
            assert error.value.status_code == 409 and message in str(error.value.detail)
    assert s.compatibility_reads == []


def test_real_inventory_does_not_accept_a_changed_image_snapshot(image_inventory):
    s = image_inventory
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task_id)
        snapshot = copy.deepcopy(task.data['image_snapshots'])
        snapshot[0]['sha256'] = '0' * 64
        task.data = {**task.data, 'image_snapshots': snapshot}
        db.commit()
    with Session(s.bound) as db:
        rows, issues, covered, _, _ = quality.inventory(db, s.p, db.get(m.Task, s.task_id))
        assert rows == [] and covered == ['image']
        assert any(value['code'] == 'source_changed' and value['severity'] == 'blocked' for value in issues)
        assert s.compatibility_reads


def test_unknown_original_modality_stays_a_review_gap_despite_valid_png_and_object_context(image_inventory):
    s = image_inventory
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task_id)
        _, original_issues, _, original_hash, _ = quality.inventory(db, s.p, task)
        assert original_issues == []
        task.data = {**task.data, 'unknown_sensor_snapshots': [{'opaque_ref': 'explicitly-unhandled'}]}
        db.commit()
    with Session(s.bound) as db:
        task = db.get(m.Task, s.task_id)
        rows, issues, _, changed_hash, _ = quality.inventory(db, s.p, task)
        assert changed_hash != original_hash
        assert any(value['code'] == 'unsupported_source' and value['field'] == 'unknown_sensor_snapshots'
            and value['severity'] == 'review' for value in issues)
        assert not any(value['field'] == 'object_snapshots' for value in issues)
        files, actual_issues = quality.inspect_files(rows, root=settings().storage_dir,
            workspace_id=s.p.workspace_id, source_issues=issues)
        assert len(files) == 1 and files[0]['actual_sha256'] == s.image_sha256
        assert files[0]['hash_verified'] is True and files[0]['format_verified'] is True
        metrics = quality.attributes(files, actual_issues, [])
        assert metrics['technical_review_required'] == 1 and metrics['integrity_blocked'] == 0
