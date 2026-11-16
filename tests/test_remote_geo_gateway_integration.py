"""Actual local FTP / signed SMB2 + isolated PostgreSQL + real GeoScene worker."""
import os
from uuid import uuid4
from pathlib import Path
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.ftp_geo_gateway_api import router as ftp_geo_router
from generic_agents.smb_geo_gateway_api import router as smb_geo_router
from generic_agents.models import Base,GeoScene,SetupMaterial,Job
from generic_agents.db import engine
from generic_agents.worker import process_once
from test_ftp_gateway_integration import ftp_gateway
from test_smb_gateway_integration import smb_gateway
app.include_router(ftp_geo_router);app.include_router(smb_geo_router)
Base.metadata.create_all(engine())

def check(resp):
    assert resp.status_code==200,resp.text
    value=resp.json();assert value['state']=='succeeded',value
    return value

@pytest.mark.parametrize('protocol,fixture',[('ftp','ftp_gateway'),('smb','smb_gateway')])
def test_raster_streams_to_geo_and_survives_recovery(protocol,fixture,request,client,tmp_path,monkeypatch):
    request.getfixturevalue(fixture)
    # A valid TIFF larger than the document-only 20 MB limit. Padding is legal;
    # the actual raster worker still parses the TIFF and confirms its metadata.
    image=tmp_path/'remote.tif';Image.new('RGB',(16,16),(120,180,80)).save(image)
    with image.open('ab') as f:f.truncate((512 if os.getenv('GA_TEST_LARGE_RASTER')=='1' else 21)*1024*1024)
    prefix='/api/'+protocol
    grant=client.get(prefix+'/grants').json()['grants'][0]
    con=check(client.post(prefix+'/connections',json={'request_id':str(uuid4()),'name':'imagery','grant_id':grant['id']}))['result']
    path=prefix+'/geo/connections/'+con['id'];request_id=str(uuid4())
    listing=check(client.post(path+'/listings',json={'request_id':request_id,'version':con['version']}))
    item=next(x for x in listing['result']['files'] if x['name']=='remote.tif');assert item['size']>20*1024*1024
    body={'request_id':str(uuid4()),'version':con['version'],'listing_id':listing['result']['listing_id'],'file_id':item['id']}
    imported=check(client.post(path+'/imports',json=body));repeat=check(client.post(path+'/imports',json=body));assert repeat==imported
    scene_id=imported['result']['scene_id'];scene=client.get('/api/geo/scenes/'+scene_id);assert scene.status_code==200
    assert scene.json()['source']==imported['result']['source'] and scene.json()['input_sha256']==imported['result']['source']['sha256']
    with Session(engine()) as db:
        row=db.get(GeoScene,scene_id);job_id=row.data['job_id'];assert db.get(Job,job_id).kind=='parse_geo_scene'
        assert not db.scalar(select(SetupMaterial).where(SetupMaterial.sha256==row.data['input_sha256']))
    process_once(job_id=job_id,deliver=False)
    scene=client.get('/api/geo/scenes/'+scene_id).json();assert scene['status']=='parsed',scene.get('error')
    assert scene['metadata']['width']==16 and scene['metadata']['height']==16
    monkeypatch.setenv('GA_'+protocol.upper()+'_ACCESS_GRANTS','{}')
    assert client.get(prefix+'/requests/'+body['request_id']).json()==imported
    hist=client.get(path+'/imports');assert hist.status_code==200 and hist.json()['source_available'] is False
    assert client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}).status_code==409

@pytest.mark.parametrize('protocol,fixture',[('ftp','ftp_gateway'),('smb','smb_gateway')])
def test_cancel_before_delivery_prevents_late_import(protocol,fixture,request,client,tmp_path):
    request.getfixturevalue(fixture);Image.new('RGB',(2,2)).save(tmp_path/'scene.tif')
    prefix='/api/'+protocol;grant=client.get(prefix+'/grants').json()['grants'][0]
    con=check(client.post(prefix+'/connections',json={'request_id':str(uuid4()),'name':'geo','grant_id':grant['id']}))['result']
    path=prefix+'/geo/connections/'+con['id'];ls=check(client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))
    item=next(x for x in ls['result']['files'] if x['name']=='scene.tif')
    body={'request_id':str(uuid4()),'version':con['version'],'listing_id':ls['result']['listing_id'],'file_id':item['id']};submission={**body,'connection_id':con['id']}
    cancel=client.post(prefix+'/geo/requests/'+body['request_id']+'/cancel',json={'kind':'geo_import','submission':submission})
    assert cancel.status_code==200 and cancel.json()['state']=='cancelled'
    late=client.post(path+'/imports',json=body);assert late.status_code==200 and late.json()['state']=='cancelled'
    with Session(engine()) as db:assert db.scalar(select(GeoScene)) is None

@pytest.mark.parametrize('protocol,fixture',[('ftp','ftp_gateway'),('smb','smb_gateway')])
@pytest.mark.parametrize('failure',['changed','revoked','diskfull','timeout','bad_tiff'])
def test_failure_never_creates_partial_scene(protocol,fixture,failure,request,client,tmp_path,monkeypatch,account):
    import errno
    from generic_agents import ftp_transport,smb_transport
    from generic_agents.config import settings
    request.getfixturevalue(fixture);Image.new('RGB',(8,8)).save(tmp_path/'fault.tif')
    prefix='/api/'+protocol;grant=client.get(prefix+'/grants').json()['grants'][0]
    con=check(client.post(prefix+'/connections',json={'request_id':str(uuid4()),'name':'geo','grant_id':grant['id']}))['result']
    path=prefix+'/geo/connections/'+con['id'];ls=check(client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))
    item=next(x for x in ls['result']['files'] if x['name']=='fault.tif')
    body={'request_id':str(uuid4()),'version':con['version'],'listing_id':ls['result']['listing_id'],'file_id':item['id']}
    module=ftp_transport if protocol=='ftp' else smb_transport;original=module.stream_file
    if failure=='changed':
        with (tmp_path/'fault.tif').open('ab') as f:f.write(b'changed')
    elif failure=='bad_tiff':
        # Take a fresh snapshot of a file with a TIFF extension but invalid header.
        (tmp_path/'fault.tif').write_bytes(b'not a tiff')
        ls=check(client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}));item=next(x for x in ls['result']['files'] if x['name']=='fault.tif');body.update(listing_id=ls['result']['listing_id'],file_id=item['id'])
    elif failure=='revoked':
        def revoke(*args,**kwargs):
            result=original(*args,**kwargs);monkeypatch.setenv('GA_'+protocol.upper()+'_ACCESS_GRANTS','{}');return result
        monkeypatch.setattr(module,'stream_file',revoke)
    elif failure=='diskfull':
        class BadDisk:
            def write(self,chunk):raise OSError(errno.ENOSPC,'fixture disk full')
        def no_space(grant,facts,dest,**kwargs):return original(grant,facts,BadDisk(),**kwargs)
        monkeypatch.setattr(module,'stream_file',no_space)
    else:
        def deadline(grant,facts,dest,**kwargs):return original(grant,facts,dest,max_file_bytes=kwargs['max_file_bytes'],deadline_seconds=-1)
        monkeypatch.setattr(module,'stream_file',deadline)
    res=client.post(path+'/imports',json=body);assert res.status_code==200 and res.json()['state']=='failed',res.text
    with Session(engine()) as db:
        assert db.scalar(select(GeoScene)) is None
        assert db.scalar(select(SetupMaterial)) is None
    directory=settings().storage_dir/account[2]
    assert not directory.exists() or list(directory.iterdir())==[]
    recovered=client.get(prefix+'/requests/'+body['request_id']);assert recovered.status_code==200 and recovered.json()['state']=='failed'

@pytest.mark.parametrize('protocol,fixture',[('ftp','ftp_gateway'),('smb','smb_gateway')])
def test_downgraded_owner_can_verify_or_cancel_without_admin_access(protocol,fixture,request,client,tmp_path,account):
    from generic_agents.models import Membership,User
    request.getfixturevalue(fixture);Image.new('RGB',(3,3)).save(tmp_path/'scene.tif')
    prefix='/api/'+protocol;grant=client.get(prefix+'/grants').json()['grants'][0]
    con=check(client.post(prefix+'/connections',json={'request_id':str(uuid4()),'name':'geo','grant_id':grant['id']}))['result']
    path=prefix+'/geo/connections/'+con['id'];ls=check(client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))
    item=next(x for x in ls['result']['files'] if x['name']=='scene.tif');body={'request_id':str(uuid4()),'version':con['version'],'listing_id':ls['result']['listing_id'],'file_id':item['id']}
    saved=check(client.post(path+'/imports',json=body))
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.user_id==user.id));membership.role='viewer';db.commit()
    assert client.get(prefix+'/connections').status_code==403
    assert client.get(prefix+'/geo/requests/'+body['request_id']).json()==saved
    verified=client.get(prefix+'/geo/requests/'+body['request_id']+'/result');assert verified.status_code==200 and verified.json()['scene_id']==saved['result']['scene_id']
    new={'request_id':str(uuid4()),'version':con['version'],'connection_id':con['id']}
    cancel=client.post(prefix+'/geo/requests/'+new['request_id']+'/cancel',json={'kind':'geo_listing','submission':new})
    assert cancel.status_code==200 and cancel.json()['state']=='cancelled'
    assert client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}).status_code==403

@pytest.mark.parametrize('protocol,fixture',[('ftp','ftp_gateway'),('smb','smb_gateway')])
def test_admin_downgraded_mid_stream_can_cancel_original(protocol,fixture,request,client,tmp_path,account,monkeypatch):
    from generic_agents.models import Membership,User
    from generic_agents import ftp_transport,smb_transport
    request.getfixturevalue(fixture);Image.new('RGB',(4,4)).save(tmp_path/'scene.tif')
    prefix='/api/'+protocol;grant=client.get(prefix+'/grants').json()['grants'][0]
    con=check(client.post(prefix+'/connections',json={'request_id':str(uuid4()),'name':'geo','grant_id':grant['id']}))['result']
    path=prefix+'/geo/connections/'+con['id'];ls=check(client.post(path+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))
    item=next(x for x in ls['result']['files'] if x['name']=='scene.tif');body={'request_id':str(uuid4()),'version':con['version'],'listing_id':ls['result']['listing_id'],'file_id':item['id']}
    module=ftp_transport if protocol=='ftp' else smb_transport;original=module.stream_file
    def downgrade(*args,**kwargs):
        result=original(*args,**kwargs)
        with Session(engine()) as db:
            user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.user_id==user.id));member.role='viewer';db.commit()
        return result
    monkeypatch.setattr(module,'stream_file',downgrade)
    failed=client.post(path+'/imports',json=body);assert failed.status_code==403,failed.text
    status=client.get(prefix+'/geo/requests/'+body['request_id']);assert status.status_code==200 and status.json()['state']=='pending'
    submission={**body,'connection_id':con['id']};cancel=client.post(prefix+'/geo/requests/'+body['request_id']+'/cancel',json={'kind':'geo_import','submission':submission})
    assert cancel.status_code==200 and cancel.json()['state']=='cancelled'
    with Session(engine()) as db:assert db.scalar(select(GeoScene)) is None
    from generic_agents.config import settings
    directory=settings().storage_dir/account[2]
    assert not directory.exists() or list(directory.iterdir())==[]
