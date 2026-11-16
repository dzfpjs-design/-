"""Actual normal API -> installed fixed CPU model -> durable artifacts, no inference seam.

Synthetic PNGs establish engineering behavior only, never domain accuracy.
Root runs this in its configured isolated disposable PostgreSQL/native setup.
"""
import hashlib
import json
import subprocess
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import PixelChangeRun, Evidence, CloudCall, Entity, GeoPair, SpecialistInstallation
from generic_agents.worker import process_once
from generic_agents import local_changer_install as install
from tests.test_instance_workspace import prepared, enable
from tests.test_pixel_change_pg_v249 import image, create, pause

pytestmark = pytest.mark.skipif(not settings().specialist_python.is_file() or not settings().geo_python.is_file()
    or not (install.ROOT / '.runtime/changer-candidate/changer-r18.safetensors').is_file()
    or not (install.ROOT / '.runtime/changer-candidate/conversion.json').is_file(),
    reason='requires real fixed Changer prepared weights/conversion and native environment; no substitute')


@pytest.fixture
def native_scene(client):
    release, _, _ = prepared(client); instance = enable(client, release)
    a, raw_a = image(client, instance['id'], color=80); b, raw_b = image(client, instance['id'], color=130)
    response = client.post('/api/specialists/installations', json={
        'pack_id': install.PACK, 'source': 'prepared_local', 'backend': 'cpu'})
    assert response.status_code == 201, response.text
    model = response.json(); assert process_once(job_id=model['job_id'], deliver=False)
    base = '/api/workspace/instances/' + instance['id'] + '/pixel-change'
    options = client.get(base + '/options'); assert options.status_code == 200, options.text
    options = options.json(); actual = next(m for m in options['installations'] if m['id'] == model['id'])
    assert actual['ready'] and actual['trace']['backend'] == 'cpu'
    body = {'schema': 'pixel-change-create-v249', 'request_id': 'native-pixel-' + uuid4().hex,
        'instance_version': instance['version'], 'release_id': release['id'],
        'before_image_id': a['id'], 'before_sha256': a['sha256'], 'after_image_id': b['id'], 'after_sha256': b['sha256'],
        'model_installation_id': actual['id'], 'model_generation': actual['generation'], 'model_recipe_hash': options['model_recipe_hash'],
        'pixel_correspondence_confirmed': True, 'full_image_present_confirmed': True, 'input_order': 'before_after', 'threshold_text': '0.5'}
    return SimpleNamespace(client=client, base=base, body=body, instance=instance, release=release,
        before=a, after=b, raw_before=raw_a, raw_after=raw_b, model=actual)


def test_actual_normal_pair_model_artifacts_recovery_and_pause_are_durable(native_scene, tmp_path):
    s = native_scene; row = create(s)
    assert row['status'] == 'queued' and row['result'] is None
    assert process_once(job_id=row['job_id'], deliver=False)
    response = s.client.get(s.base + '/runs/' + row['id']); assert response.status_code == 200, response.text
    saved = response.json(); assert saved['status'] == 'succeeded', saved['reason']
    result = saved['result']
    assert result['coordinate_space'] == 'pixel' and result['georeference_available'] is False
    assert result['common_valid_pixels'] == result['total_pixels'] == 5120 and result['unknown_pixels'] == 0
    assert result['cloud_calls'] == 0 and result['object_state_updated'] is False
    assert result['candidate_only'] and result['cloud_shadow_screening'] == 'not_applied'
    assert {a['kind'] for a in result['artifacts']} == {
        'valid_mask', 'probability_data', 'probability_preview', 'candidate_overlay', 'binary_mask', 'manifest'}
    downloaded = {}
    for ref in result['artifacts']:
        response = s.client.get(s.base + '/runs/' + row['id'] + '/artifacts/' + ref['kind'])
        assert response.status_code == 200, response.text
        assert response.headers['x-content-sha256'] == ref['sha256'] == hashlib.sha256(response.content).hexdigest()
        assert len(response.content) == ref['size_bytes']
        assert response.headers['content-type'].split(';')[0] == ref['media_type']
        downloaded[ref['kind']] = response.content
        (tmp_path / ref['kind']).write_bytes(response.content)
    script = '''import sys,json,numpy as np
from pathlib import Path
from PIL import Image
p=Path(sys.argv[1]);a=np.load(p/'probability_data',allow_pickle=False)
assert a.dtype==np.float32 and a.shape==(64,80) and np.isfinite(a).all()
mask=np.asarray(Image.open(p/'binary_mask'));valid=np.asarray(Image.open(p/'valid_mask'))
assert (valid==255).all() and np.array_equal(mask,(a>=.5).astype('uint8')*255)
preview=np.asarray(Image.open(p/'probability_preview'))
assert np.array_equal(preview,np.rint(a*65534+1).astype('uint16'))
print(json.dumps({'candidate_pixels':int((mask==255).sum())}))'''
    checked = subprocess.run([str(settings().specialist_python.absolute()), '-c', script, str(tmp_path)],
        capture_output=True, text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr[-400:]
    assert json.loads(checked.stdout)['candidate_pixels'] == result['candidate_pixels']
    manifest = json.loads(downloaded['manifest'])
    assert manifest['native_result']['learned_model'] is True and manifest['native_result']['cloud_calls'] == 0
    assert manifest['sources']['before']['sha256'] == s.body['before_sha256']
    assert manifest['native_result']['aligned_input_sha256']['before'] == s.body['before_sha256']
    assert manifest['native_result']['aligned_input_sha256']['after'] == s.body['after_sha256']
    assert 'crs' not in manifest and 'geometry' not in manifest and 'area' not in manifest
    pause(s)
    recovered = s.client.get(s.base + '/requests', params={'request_id': s.body['request_id'], 'expected_body_hash': digest(s.body)})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['run'] == saved
    assert s.client.get(s.base + '/runs/' + row['id'] + '/originals/before').content == s.raw_before
    assert s.client.get(s.base + '/runs/' + row['id'] + '/originals/after').content == s.raw_after
    with Session(engine()) as db:
        run = db.get(PixelChangeRun, row['id'])
        assert run.status == 'succeeded' and len(run.data['attempts']) == 1
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Entity)))
        assert not list(db.scalars(select(GeoPair)))


def test_actual_output_damage_is_not_displayed_or_repaired(native_scene):
    s = native_scene; row = create(s); assert process_once(job_id=row['job_id'], deliver=False)
    saved = s.client.get(s.base + '/runs/' + row['id']).json()
    assert saved['status'] == 'succeeded', saved['reason']
    ref = next(a for a in saved['result']['artifacts'] if a['kind'] == 'binary_mask')
    with Session(engine()) as db:
        ev = db.get(Evidence, ref['evidence_id']); key = ev.storage_key
        # Explicit post-success storage fault; never a substitute successful result.
        (settings().storage_dir / key).write_bytes(b'corrupted-output')
    assert s.client.get(s.base + '/runs/' + row['id']).status_code == 409
    assert s.client.get(s.base + '/runs/' + row['id'] + '/artifacts/binary_mask').status_code == 409
    with Session(engine()) as db:
        assert db.get(PixelChangeRun, row['id']).status == 'succeeded'
        assert len(db.get(PixelChangeRun, row['id']).data['attempts']) == 1
