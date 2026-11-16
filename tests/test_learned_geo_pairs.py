"""Real local model/GeoTIFF processing with disposable PostgreSQL records.

The fixture installs through the public API using verified prepared files and
the real CPU probe. Engineering execution does not prove field quality or
production licensing.
"""
import hashlib
import copy
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import SpecialistInstallation, Membership, CloudCall, GeoPair, Capability, Entity
from generic_agents import local_changer_install as install
from generic_agents.worker import process_once
from generic_agents.contracts import digest
from tests.test_geo_pairs import ready

pytestmark = pytest.mark.skipif(not settings().geo_python.is_file()
    or not (install.ROOT / '.runtime/changer-candidate/changer-r18.safetensors').is_file()
    or not (install.ROOT / '.runtime/changer-candidate/conversion.json').is_file(),
    reason='requires prepared local Changer checkpoint, conversion receipt and geospatial runtime')


@pytest.fixture
def rasters(tmp_path):
    script = '''
import sys,numpy as np,rasterio
from pathlib import Path
from rasterio.transform import from_origin
root=Path(sys.argv[1]);a=np.full((3,64,80),100,dtype='uint8');b=a.copy();b[:,20:40,20:45]=180;b[:,40:44,50:55]=0
for name,value in [('a',a),('b',b)]:
 with rasterio.open(root/(name+'.tif'),'w',driver='GTiff',height=64,width=80,count=3,dtype='uint8',nodata=0,crs='EPSG:32650',transform=from_origin(500000,2680000,.5,.5)) as out:out.write(value)
'''
    result = subprocess.run([str(settings().geo_python.absolute()), '-c', script, str(tmp_path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr[-300:]
    return tmp_path


@pytest.fixture
def configured(client, rasters, account):
    a = ready(client, rasters / 'a.tif', '工程A.tif'); b = ready(client, rasters / 'b.tif', '工程B.tif')
    # Exercise the real public installation, local copy and CPU probe. A ready
    # row without the independent ready Audit cannot prove a new native run.
    response = client.post('/api/specialists/installations', json={
        'pack_id': install.PACK, 'source': 'prepared_local', 'backend': 'cpu'})
    assert response.status_code == 201, response.text
    requested = response.json()
    assert process_once(requested['job_id'], deliver=False)
    with Session(engine()) as db:
        row = db.get(SpecialistInstallation, requested['id'])
        assert row.status == 'ready', row.data.get('error')
        assert row.data['smoke']['inference_performed'] is True
        assert row.data['smoke']['backend'] == 'cpu' and row.data['smoke']['cloud_calls'] == 0
        identity = row.id
    value = {'request_id': str(uuid4()), 'model_installation_id': identity, 'model_generation': 1,
             'model_recipe_hash': install.RECIPE_HASH, 'before_scene_id': a['id'], 'after_scene_id': b['id'],
             'before_version': a['version'], 'after_version': b['version'], 'roi': [0, 0, 80, 64],
             'alignment': 'same_grid', 'probability_threshold': .5, 'rgb_comparability_confirmed': True}
    return value


def create(client, value):
    response = client.post('/api/geo/learned-pairs', json=value)
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_defaults_local_and_filters_installations_by_workspace(client, account):
    from generic_agents.models import Workspace
    with Session(engine()) as db:
        foreign=Workspace(name='Separate model workspace');db.add(foreign);db.flush()
        owned=SpecialistInstallation(workspace_id=account[2],pack_id=install.PACK,status='failed',data={'generation':1})
        other=SpecialistInstallation(workspace_id=foreign.id,pack_id=install.PACK,status='ready',data={'generation':1})
        db.add_all([owned,other]);db.commit();owned_id=owned.id;other_id=other.id
    response=client.get('/api/geo/learned-pairs/catalog')
    assert response.status_code==200
    catalog=response.json()
    assert catalog['default_deployment']=='local' and catalog['backend']=='cpu' and not catalog['cloud_fallback']
    assert catalog['model_recipe_hash']==install.RECIPE_HASH and catalog['pipeline_hash']==install.PIPELINE_HASH
    assert not catalog['domain_quality_verified'] and catalog['business_registration_enabled']
    assert [row['id'] for row in catalog['installations']]==[owned_id]
    assert other_id not in response.text


def test_real_learned_result_review_export_and_daily_receipt_are_saved(client, configured, account):
    row = create(client, configured); path = '/api/geo/pairs/' + row['id']
    assert create(client, configured)['id'] == row['id']
    assert client.post('/api/geo/learned-pairs', json={**configured, 'probability_threshold': .4}).status_code == 409
    assert client.get('/api/geo/pairs/requests/' + configured['request_id']).json()['id'] == row['id']
    assert process_once(row['job_id'], deliver=False)
    row = client.get(path).json(); assert row['status'] == 'succeeded', row.get('error')
    assert row['result']['learned_model'] is True and row['result']['unknown_pixels'] == 20
    assert row['result']['cloud_calls'] == 0 and len(row['result']['artifacts']) == 8
    assert client.get('/api/geo/pairs').json() == []
    assert client.get('/api/geo/learned-pairs').json()[0]['id'] == row['id']
    for kind, saved in row['result']['artifacts'].items():
        result = client.get(path + '/artifacts/' + kind)
        assert result.status_code == 200, result.text
        assert hashlib.sha256(result.content).hexdigest() == saved['sha256']
        assert result.headers['content-type'].split(';')[0] == saved['media_type']
    assert client.get(path + '/artifacts/difference').status_code == 404
    response = client.put(path + '/review', json={'version': row['version'], 'decision': 'uncertain', 'note': '工程合成输入；保留候选，不能判断真实变化原因'})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert client.get(path + '/export').json()['review'] == saved['review']
    registered=client.post(path + '/register-business', json={'version': saved['version']})
    assert registered.status_code==200 and registered.json()['learned_model']
    with Session(engine()) as db:
        from generic_agents.geo_daily import record
        from generic_agents.security import Principal
        membership = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        item = record(db, Principal(membership.user_id, account[2], 'admin', ''), 'comparison', db.get(GeoPair, row['id']), force=True)
        assert item['integrity_verified'] and item['learned_change_model'] and not item['entity_state_updated']
        assert item['review_state'] == 'confirmed' and item['not_authoritative']
        assert not list(db.scalars(select(CloudCall))) and len(list(db.scalars(select(Capability))))==1 and not list(db.scalars(select(Entity)))
        # A later installation generation never relabels completed evidence.
        model=db.get(SpecialistInstallation,configured['model_installation_id'])
        model.data={**model.data,'generation':2};db.commit()
    assert client.get(path+'/export').status_code==200
    # Corrupt saved shape with a matching outer digest: readable refusal,
    # then exact restoration in this disposable database only.
    with Session(engine()) as db:
        current=db.get(GeoPair,row['id']);original=copy.deepcopy(current.data)
        bad={**current.data['result'],'artifacts':[]}
        current.data={**current.data,'result':bad,'result_hash':digest(bad)};db.commit()
    assert client.get(path+'/export').status_code==409
    with Session(engine()) as db:
        current=db.get(GeoPair,row['id']);current.data=original;db.commit()
    assert client.get(path+'/export').status_code==200


def test_cancel_then_retry_preserves_model_and_sources(client, configured):
    row = create(client, configured); path = '/api/geo/pairs/' + row['id']
    assert client.post(path + '/cancel', json={'version': row['version']}).status_code == 200
    assert process_once(row['job_id'], deliver=False)
    cancelled = client.get(path).json(); assert cancelled['status'] == 'cancelled'
    response = client.post(path + '/retry', json={'version': cancelled['version']}); assert response.status_code == 200
    retried = response.json(); assert retried['generation'] == 2 and retried['model_trace'] == row['model_trace']
    assert process_once(retried['job_id'], deliver=False)
    assert client.get(path).json()['status'] == 'succeeded'


def test_model_change_before_job_blocks_new_inference_and_recovery_retains_request(client, configured):
    row = create(client, configured)
    with Session(engine()) as db:
        model = db.get(SpecialistInstallation, configured['model_installation_id'])
        model.data = {**model.data, 'generation': 2}; db.commit()
    process_once(row['job_id'], deliver=False)
    saved = client.get('/api/geo/pairs/' + row['id']).json()
    assert saved['status'] == 'failed' and saved['result'] is None
    assert create(client, configured)['id'] == row['id']
    assert client.post('/api/geo/pairs/' + row['id'] + '/retry', json={'version': saved['version']}).status_code == 409


def test_invalid_scope_threshold_and_unconfirmed_rgb_are_rejected(client, configured):
    for changes in ({'roi': [0, 0, 31, 64]}, {'roi': [0, 0, 81, 64]}, {'probability_threshold': True},
                    {'probability_threshold': 1.0}, {'rgb_comparability_confirmed': False}, {'model_generation': 2}):
        result = client.post('/api/geo/learned-pairs', json={**configured, **changes})
        assert result.status_code in (409, 422), result.text
    assert client.get('/api/geo/learned-pairs').json() == []
