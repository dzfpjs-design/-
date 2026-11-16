"""Synthetic WPML archive tests; never sends or simulates aircraft commands."""
from io import BytesIO
from uuid import uuid4
import zipfile
import pytest
from fastapi import HTTPException
from generic_agents.dji_wayline_archive import inspect_kmz
from generic_agents.dji_wayline_api import router
from generic_agents.api import app
from generic_agents.models import Job
from generic_agents.db import engine
from sqlalchemy import select
from sqlalchemy.orm import Session

if not any(getattr(r,'path',None)=='/api/dji/waylines' for r in app.routes):app.include_router(router)
K='http://www.opengis.net/kml/2.2';W='http://www.dji.com/wpmz/1.0.2'

def archive(*,coord='117.1,24.1',index='0',extra=None,template=None,wayline=None):
    template=template or f'<kml xmlns="{K}" xmlns:wpml="{W}"><Document><wpml:missionConfig/></Document></kml>'
    wayline=wayline or f'<kml xmlns="{K}" xmlns:wpml="{W}"><Document><Folder><wpml:waylineId>0</wpml:waylineId><wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode><Placemark><Point><coordinates>{coord}</coordinates></Point><wpml:index>{index}</wpml:index><wpml:executeHeight>60</wpml:executeHeight></Placemark></Folder></Document></kml>'
    out=BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('wpmz/template.kml',template);z.writestr('wpmz/waylines.wpml',wayline)
        if extra:z.writestr(*extra)
    return out.getvalue()

def test_summary_preserves_lon_lat_and_no_safety_claim():
    out=inspect_kmz(archive());assert out['point_count']==1 and out['flight_approved'] is False
    point=out['routes'][0]['points'][0];assert point['longitude']==117.1 and point['latitude']==24.1 and point['execute_height']==60
    assert out['routes'][0]['height_mode']=='relativeToStartPoint' and len(out['md5'])==32

@pytest.mark.parametrize('payload',[b'not a zip',archive(coord='181,0'),archive(coord='NaN,0'),archive(coord='1,0,NaN'),archive(index='-1'),archive(extra=('../secrets','bad')),archive(wayline=f'<!DOCTYPE foo [<!ENTITY a "bad">]><kml xmlns="{K}" xmlns:wpml="{W}"><Document>&a;</Document></kml>')])
def test_unsafe_files_fail_closed(payload):
    with pytest.raises(HTTPException):inspect_kmz(payload)

def test_import_resume_download_stop_and_isolation(client,account):
    rid=str(uuid4());payload=archive();path='/api/dji/waylines'
    def post(body=payload,request=rid,name='synthetic.kmz'):
        return client.post(path,data={'request_id':request},files={'file':(name,body,'application/vnd.google-earth.kmz')})
    res=post();assert res.status_code==200,res.text
    saved=res.json();wayline=saved['wayline'];assert saved['state']=='saved' and not wayline['can_dispatch']
    assert post().json()==saved and client.get(path+'/operations/'+rid).json()==saved
    assert post(archive(coord='117.2,24.2')).status_code==409
    assert client.get(path+'/'+wayline['id']).json()==wayline
    assert client.get(path+'/'+wayline['id']+'/download').content==payload
    assert client.get(path).json()['waylines'][0]['id']==wayline['id']
    cancel_id=str(uuid4());assert client.post(path+'/operations/'+cancel_id+'/cancel').json()['state']=='cancelled'
    assert post(request=cancel_id).json()['state']=='cancelled'
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.id==wayline['id']));row.data={**row.data,'sha256':'0'*64};db.commit()
    assert client.get(path+'/'+wayline['id']).status_code==409
    assert client.get(path+'/'+wayline['id']+'/download').status_code==409
    listing=client.get(path).json();assert listing['unavailable_count']==1 and listing['waylines']==[]

def test_viewer_can_recover_own_but_cannot_upload_and_cross_workspace_isolated(client,account):
    from generic_agents.models import Membership,User,Workspace
    from fastapi.testclient import TestClient
    rid=str(uuid4());payload=archive();base='/api/dji/waylines'
    saved=client.post(base,data={'request_id':rid},files={'file':('synthetic.kmz',payload,'application/zip')}).json()
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]));member.role='viewer'
        other=Workspace(name='another workspace');db.add(other);db.commit();other_id=other.id
    assert client.get(base+'/operations/'+rid).json()==saved
    assert client.post(base,data={'request_id':str(uuid4())},files={'file':('other.kmz',payload,'application/zip')}).status_code==403
    assert client.get(base+'/'+saved['wayline']['id'],headers={'X-Workspace-ID':other_id}).status_code==403
    with TestClient(app) as fresh:
        token=fresh.post('/api/login',json={'username':account[0],'password':account[1]}).json()['csrf_token'];fresh.headers.update({'X-CSRF-Token':token,'X-Workspace-ID':account[2]})
        assert fresh.get(base+'/operations/'+rid).json()==saved
        assert fresh.get(base+'/'+saved['wayline']['id']+'/download').content==payload

def test_duplicate_names_and_symlink_in_zip_rejected():
    data=BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('wpmz/template.kml','a');z.writestr('WPMZ/template.kml','b');z.writestr('wpmz/waylines.wpml','c')
    with pytest.raises(HTTPException):inspect_kmz(data.getvalue())
    data=BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        link=zipfile.ZipInfo('wpmz/link');link.create_system=3;link.external_attr=(0o120777<<16)
        z.writestr('wpmz/template.kml','a');z.writestr('wpmz/waylines.wpml','b');z.writestr(link,'target')
    with pytest.raises(HTTPException):inspect_kmz(data.getvalue())
