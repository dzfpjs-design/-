"""Real GeoTIFF persistence + worker decoding; no mocked GDAL or model result."""
import hashlib,json
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.models import Evidence,GeoScene,CloudCall,Membership
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.worker import process_once
from tests.test_geo_codec import scenes,pytestmark


def upload(client,path,request_id=None,name='公开工程旋转标签.tif'):
    return client.post('/api/geo/scenes',data={'request_id':request_id or str(uuid4())},files={'file':(name,path.read_bytes(),'image/tiff')})


def parsed(client,scenes):
    response=upload(client,scenes/'rgb.tif');assert response.status_code==201,response.text;r=response.json()
    assert process_once(r['job_id'],deliver=False)
    row=client.get('/api/geo/scenes/'+r['id']).json();assert row['status']=='parsed',row
    return row


def test_original_is_readback_checked_and_configuration_generates_saved_preview(client,scenes):
    row=parsed(client,scenes);path='/api/geo/scenes/'+row['id']
    response=client.get(path+'/original');assert response.content==(scenes/'rgb.tif').read_bytes()
    assert response.headers['x-content-sha256']==row['input_sha256']
    assert client.get(path+'/original?workspace_id=not-owned').status_code==403
    assert client.get(path+'/preview').status_code==409
    body={'version':row['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True}
    response=client.put(path+'/configuration',json=body);assert response.status_code==200,response.text
    assert process_once(response.json()['job_id'],deliver=False)
    current=client.get(path).json();assert current['status']=='ready',current
    preview=client.get(path+'/preview');assert preview.status_code==200
    assert hashlib.sha256(preview.content).hexdigest()==current['preview']['sha256']
    assert current['preview']['statistics_scope']=='preview_samples' and current['preview']['valid_pixels']==76
    assert current['configuration']['observed_at'] is None and current['metadata']['ground_accuracy']=='not_verified'
    assert client.put(path+'/configuration',json=body).status_code==409
    with Session(engine()) as db:assert not db.scalars(select(CloudCall)).all()


def test_upload_retry_dedupes_but_refuses_same_request_with_different_original(client,scenes):
    request_id=str(uuid4());r=upload(client,scenes/'rgb.tif',request_id).json()
    assert upload(client,scenes/'rgb.tif',request_id).json()['id']==r['id']
    assert upload(client,scenes/'high.tif',request_id).status_code==409
    assert len(client.get('/api/geo/scenes').json())==1
    with Session(engine()) as db:assert len(db.scalars(select(Evidence)).all())==1


def test_cancel_and_explicit_retry_preserve_original_and_history(client,scenes):
    row=upload(client,scenes/'rgb.tif').json();path='/api/geo/scenes/'+row['id']
    cancelled=client.post(path+'/cancel',json={'version':row['version']});assert cancelled.status_code==200
    assert process_once(row['job_id'],deliver=False)
    old=client.get(path).json();assert old['status']=='cancelled'
    response=client.post(path+'/retry',json={'version':old['version']});assert response.status_code==200,response.text
    assert process_once(response.json()['job_id'],deliver=False)
    current=client.get(path).json();assert current['status']=='parsed' and current['history'][0]['status']=='cancelled'
    assert current['input_sha256']==row['input_sha256']


def test_bad_tiff_decode_keeps_original_and_corrupt_original_blocks_preview(client,scenes,tmp_path):
    bad=tmp_path/'bad.tif';bad.write_bytes(b'II*\x00not-a-geotiff')
    row=upload(client,bad).json();assert process_once(row['job_id'],deliver=False)
    saved=client.get('/api/geo/scenes/'+row['id']).json();assert saved['status']=='failed' and '解码' in saved['error']
    assert client.get('/api/geo/scenes/'+row['id']+'/original').content==bad.read_bytes()
    row=parsed(client,scenes);path='/api/geo/scenes/'+row['id']
    with Session(engine()) as db:
        ev=db.get(Evidence,row['evidence_id']);(settings().storage_dir/ev.storage_key).write_bytes(b'corrupt')
    assert client.get(path+'/original').status_code==409
    response=client.put(path+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,3]});assert response.status_code==200
    assert process_once(response.json()['job_id'],deliver=False)
    assert client.get(path).json()['status']=='failed'
    assert client.get(path+'/preview').status_code==409


def test_spectral_and_capture_time_must_be_explicit_and_viewer_cannot_upload(client,scenes,account):
    row=upload(client,scenes/'high.tif').json();assert process_once(row['job_id'],deliver=False)
    row=client.get('/api/geo/scenes/'+row['id']).json();path='/api/geo/scenes/'+row['id']
    assert client.put(path+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,3]}).status_code==422
    assert client.put(path+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,4],'ranges':[[0,65535]]*3}).status_code==422
    assert client.put(path+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,3],'ranges':[[0,65535]]*3,'observed_at':'2026-10-08T12:00:00'}).status_code==422
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    assert upload(client,scenes/'rgb.tif').status_code==403
    assert client.get(path+'/original').status_code==200


def test_multipart_transport_limit_does_not_trust_content_length_or_store_partial_original(client,monkeypatch):
    import generic_agents.geo_upload_limit as transport
    monkeypatch.setitem(transport.RECIPE,'max_upload_bytes',128)
    oversized=b'II*\x00'+b'x'*70000
    for headers in [{},{'content-length':'0'}]:
        response=client.post('/api/geo/scenes',data={'request_id':str(uuid4())},files={'file':('too-large.tif',oversized,'image/tiff')},headers=headers)
        assert response.status_code==413,response.text
    assert client.get('/api/geo/scenes').json()==[]
    with Session(engine()) as db:assert not db.scalars(select(Evidence)).all()


def test_raster_cloud_boundary_rejects_before_loading_original(client,scenes,account,monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from generic_agents.security import Principal
    from generic_agents.models import User
    import generic_agents.evidence as evidence
    row=upload(client,scenes/'rgb.tif').json()
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','test-boundary')
        def must_not_read(*args,**kwargs):raise AssertionError('unsupported or unconsented raster was loaded into API memory')
        monkeypatch.setattr(evidence,'evidence_bytes',must_not_read)
        with pytest.raises(HTTPException) as error:evidence.cloud_parts(db,p,[row['evidence_id']],SimpleNamespace(id='unit-model'))
        assert error.value.status_code==403
        original=db.get(Evidence,row['evidence_id']);original.data={**original.data,'cloud_model_ids':['unit-model']};db.flush()
        with pytest.raises(HTTPException) as error:evidence.cloud_parts(db,p,[row['evidence_id']],SimpleNamespace(id='unit-model'))
        assert error.value.status_code==409


def test_upload_request_recovery_checks_original_and_workspace(client,scenes,account):
    from generic_agents.models import Workspace,User
    request_id=str(uuid4());url='/api/geo/uploads/'+request_id
    assert client.get(url).status_code==404
    row=upload(client,scenes/'rgb.tif',request_id).json()
    recovered=client.get(url);assert recovered.status_code==200 and recovered.json()['id']==row['id']
    with Session(engine()) as db:
        workspace=Workspace(name='隔离读取工作区');db.add(workspace);db.flush();other=workspace.id
        db.add(Membership(user_id=db.scalar(select(User.id).where(User.username==account[0])),workspace_id=other,role='viewer'));db.commit()
    assert client.get(url,headers={'X-Workspace-ID':other}).status_code==404
    with Session(engine()) as db:
        ev=db.get(Evidence,row['evidence_id']);(settings().storage_dir/ev.storage_key).write_bytes(b'corrupt')
    assert client.get(url).status_code==409
