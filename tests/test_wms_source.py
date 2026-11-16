"""Real HTTP WMS responses, real local raster conversion, disposable database."""
import hashlib,io,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.models import GeoScene,Evidence,CloudCall,Membership
from generic_agents.db import engine
from generic_agents.worker import process_once
from generic_agents.wms_protocol import catalog,normalize_endpoint


def xml(version='1.3.0'):
    coord='CRS' if version=='1.3.0' else 'SRS';root='WMS_Capabilities' if version=='1.3.0' else 'WMT_MS_Capabilities'
    bounds='minx="20" miny="100" maxx="30" maxy="120"' if version=='1.3.0' else 'minx="100" miny="20" maxx="120" maxy="30"'
    return f'''<?xml version="1.0"?><{root} version="{version}"><Service><Title>公开参考地图服务</Title></Service>
     <Capability><Request><GetMap><Format>image/png</Format><DCPType><HTTP><Get><OnlineResource href="http://169.254.169.254/forbidden"/></Get></HTTP></DCPType></GetMap></Request>
     <Layer><Title>父图层</Title><{coord}>EPSG:4326</{coord}><BoundingBox {coord}="EPSG:4326" {bounds}/>
     <Style><Name>baseline</Name></Style><Layer><Name>public:render</Name><Title>公开渲染图（非实测）</Title><Dimension name="time">2020-01-01</Dimension></Layer></Layer>
     </Capability></{root}>'''.encode()


@pytest.fixture
def service(account):
    state={'version':'1.3.0','maps':0,'requests':[],'mode':'normal'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            parsed=urlsplit(self.path);query=parse_qs(parsed.query,keep_blank_values=True)
            state['requests'].append({'path':parsed.path,'query':query})
            if query.get('REQUEST')==['GetCapabilities']:content=xml(state['version']);mime='application/xml'
            else:
                state['maps']+=1
                if state['mode']=='xml_error':content=b'<ServiceException>not a map</ServiceException>';mime='text/xml'
                elif state['mode']=='large':content=b'x'*(16*1024*1024+1);mime='image/png'
                elif state['mode']=='redirect':
                    self.send_response(302);self.send_header('Location','http://169.254.169.254/private');self.end_headers();return
                else:
                    width=int(query['WIDTH'][0]);height=int(query['HEIGHT'][0]);image=Image.new('RGBA',(width,height),(50,120,80,255))
                    for x in range(width):
                        for y in range(2):image.putpixel((x,y),(0,0,0,0))
                    stream=io.BytesIO();image.save(stream,format='PNG');content=stream.getvalue();mime='image/png'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)));self.end_headers()
            try:self.wfile.write(content)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    old=settings().wms_allowed_endpoints;old_grants=settings().wms_access_grants
    state['endpoint']=f'http://127.0.0.1:{server.server_port}/wms';settings().wms_allowed_endpoints=state['endpoint']
    settings().wms_access_grants=json.dumps({account[2]:[{'endpoint':state['endpoint'],'secret_ref':''}]})
    yield state
    settings().wms_allowed_endpoints=old;settings().wms_access_grants=old_grants;server.shutdown();server.server_close();thread.join(timeout=2)


def connect(client,service,version='1.3.0'):
    service['version']=version;body={'request_id':str(uuid4()),'name':'公开参考WMS（不是现场验收）','endpoint':service['endpoint'],'version':version}
    response=client.post('/api/wms/connections',json=body);assert response.status_code==201,response.text
    created=response.json();assert client.post('/api/wms/connections',json=body).json()['id']==created['id']
    assert process_once(created['job_id'],deliver=False)
    saved=next(c for c in client.get('/api/wms/connections').json() if c['id']==created['id']);assert saved['status']=='catalog_ready',saved.get('error')
    assert saved['catalog']['layers'][0]['bounds']['EPSG:4326']==[100,20,120,30]
    response=client.post('/api/wms/connections/'+saved['id']+'/approve',json={'version':saved['version'],'layer':'public:render','crs':'EPSG:4326',
        'bbox':[102,22,118,28],'style':'baseline','confirm_read_only':True});assert response.status_code==200,response.text
    return response.json()


def request_read(client,connection,body=None):
    body=body or {'request_id':str(uuid4()),'scope_id':next(iter(connection['scopes'])),'bbox':[104,23,116,27],'width':64,'height':48}
    response=client.post('/api/wms/connections/'+connection['id']+'/reads',json=body);assert response.status_code==201,response.text
    return response.json(),body


def finish(client,row):
    assert process_once(row['job_id'],deliver=False)
    return client.get('/api/wms/reads/'+row['id']).json()


@pytest.mark.parametrize('version',['1.1.1','1.3.0'])
def test_real_wms_axis_alpha_source_chain_and_daily(client,service,version):
    connection=connect(client,service,version);row,body=request_read(client,connection);row=finish(client,row)
    assert row['status']=='succeeded',row.get('error')
    query=service['requests'][-1]['query'];expected='23.0,104.0,27.0,116.0' if version=='1.3.0' else '104.0,23.0,116.0,27.0'
    assert query['BBOX']==[expected] and query['LAYERS']==['public:render'] and all(r['path']=='/wms' for r in service['requests'])
    assert row['result']['transparent_pixels']==128 and row['result']['valid_pixels']==2944 and not row['result']['raw_satellite_bands']
    scene=client.get('/api/geo/scenes/'+row['scene_id']).json();assert process_once(scene['job_id'],deliver=False)
    scene=client.get('/api/geo/scenes/'+scene['id']).json();assert scene['status']=='parsed'
    assert scene['metadata']['source_kind']=='wms_rendered_map' and scene['metadata']['observed_at'] is None
    assert scene['metadata']['transform']==pytest.approx([12/64,0,104,0,-4/48,27])
    configured=client.put('/api/geo/scenes/'+scene['id']+'/configuration',json={'version':scene['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True})
    assert configured.status_code==200,configured.text;assert process_once(configured.json()['job_id'],deliver=False)
    scene=client.get('/api/geo/scenes/'+scene['id']).json();assert scene['status']=='ready' and scene['preview']['valid_pixels']==2944
    response=client.get('/api/wms/reads/'+row['id']+'/response');assert response.status_code==200
    assert hashlib.sha256(response.content).hexdigest()==row['response']['sha256']
    assert client.get('/api/wms/reads/'+row['id']+'/export').json()['provenance']['bbox']==[104,23,116,27]
    repeated,_=request_read(client,connection,body);assert repeated['id']==row['id'] and service['maps']==1
    daily=client.post('/api/imagery-daily/reconcile').json()
    item=next(r for d in daily['rows'] for r in d['records'] if r['id']==scene['id'])
    assert item['integrity_verified'] and item['sources'][0]['wms_origin']['png_sha256']==row['response']['sha256']
    assert 'rendered_map_not_raw_bands' in item['quality_gaps'] and 'capture_time_unknown' in item['quality_gaps']
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))


def test_local_conversion_retry_reuses_saved_response(client,service,monkeypatch):
    import generic_agents.wms as wms
    connection=connect(client,service);row,body=request_read(client,connection);native=wms.convert
    def fail(*args,**kwargs):raise ValueError('本地转换暂不可用，原响应保留')
    monkeypatch.setattr(wms,'convert',fail);row=finish(client,row)
    assert row['status']=='failed' and row['response'] and not row['scene_id'] and service['maps']==1
    original_sha=row['response']['sha256'];monkeypatch.setattr(wms,'convert',native)
    response=client.post('/api/wms/reads/'+row['id']+'/retry',json={'version':row['version']});assert response.status_code==200,response.text
    row=finish(client,response.json());assert row['status']=='succeeded',row.get('error')
    assert service['maps']==1 and row['response']['sha256']==original_sha and len(row['history'])==1


def test_pause_after_conversion_fences_late_save_and_preserves_response(client,service,monkeypatch):
    import generic_agents.wms as wms
    connection=connect(client,service);row,_=request_read(client,connection);original=wms.convert
    def pause_after_native(*args,**kwargs):
        result=original(*args,**kwargs)
        assert client.post('/api/wms/connections/'+connection['id']+'/pause',json={'version':connection['version']}).status_code==200
        return result
    monkeypatch.setattr(wms,'convert',pause_after_native);row=finish(client,row)
    assert row['status']=='failed' and row['response'] and not row['scene_id']
    with Session(engine()) as db:assert not list(db.scalars(select(GeoScene)))


def test_scope_nonce_cancel_and_network_permissions(client,service):
    assert client.post('/api/wms/connections',json={'request_id':str(uuid4()),'name':'bad','endpoint':'http://169.254.169.254/private'}).status_code==422
    connection=connect(client,service);row,body=request_read(client,connection)
    url='/api/wms/connections/'+connection['id']+'/reads'
    assert client.post(url,json={**body,'width':32}).status_code==409
    assert client.post(url,json={**body,'request_id':str(uuid4()),'bbox':[100,20,120,30]}).status_code==422
    assert client.post(url,json={**body,'request_id':str(uuid4()),'width':True}).status_code==422
    response=client.post('/api/wms/reads/'+row['id']+'/cancel',json={'version':row['version']});assert response.status_code==200
    assert process_once(row['job_id'],deliver=False) and service['maps']==0


@pytest.mark.parametrize('mode',['redirect','xml_error','large'])
def test_invalid_service_responses_do_not_create_raster(client,service,mode):
    connection=connect(client,service);service['mode']=mode;row,_=request_read(client,connection);row=finish(client,row)
    assert row['status']=='failed' and not row['scene_id']
    assert all(r['path']=='/wms' for r in service['requests'])
    with Session(engine()) as db:assert not list(db.scalars(select(GeoScene)))


def test_corrupt_original_png_blocks_history_and_daily_integrity(client,service):
    connection=connect(client,service);row,_=request_read(client,connection);row=finish(client,row);assert row['status']=='succeeded'
    with Session(engine()) as db:
        ev=db.get(Evidence,row['response']['evidence_id']);path=settings().storage_dir/ev.storage_key;path.write_bytes(b'corrupt disposable evidence')
    assert client.get('/api/wms/reads/'+row['id']+'/export').status_code==409
    daily=client.post('/api/imagery-daily/reconcile').json();item=next(r for d in daily['rows'] for r in d['records'] if r['id']==row['scene_id'])
    assert not item['integrity_verified']


def test_workspace_isolation_and_operator_cannot_approve_catalog(client,service,account):
    connection=connect(client,service)
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='operator';db.commit()
    assert client.post('/api/wms/connections/'+connection['id']+'/approve',json={'version':connection['version'],'layer':'public:render',
        'crs':'EPSG:4326','bbox':[102,22,118,28],'confirm_read_only':True}).status_code==403
    from generic_agents.models import Workspace
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));other=Workspace(name='隔离资料');db.add(other);db.flush()
        db.add(Membership(user_id=member.user_id,workspace_id=other.id,role='operator'));db.commit();other_id=other.id
    client.headers['X-Workspace-ID']=other_id
    assert client.get('/api/wms/connections').json()==[]
    assert client.post('/api/wms/connections/'+connection['id']+'/reads',json={'request_id':str(uuid4()),'scope_id':next(iter(connection['scopes'])),
        'bbox':[104,23,116,27],'width':64,'height':48}).status_code==404


def test_workspace_grant_and_credential_pair_required_before_network_read(client,service,account):
    response=client.post('/api/wms/connections',json={'request_id':str(uuid4()),'name':'wrong credential','endpoint':service['endpoint'],'secret_ref':'GA_CONNECTOR_SECRET_OTHER_WORKSPACE'})
    assert response.status_code==403 and not service['requests']
    connection=connect(client,service);row,_=request_read(client,connection);settings().wms_access_grants='{}'
    row=finish(client,row);assert row['status']=='failed' and service['maps']==0
    assert client.get('/api/wms/catalog').json()['secret_references']==[]


def test_malformed_grants_and_non_boolean_authorization_fail_closed(client,service,account):
    original=settings().wms_access_grants
    for bad in [[],{account[2]:[{'endpoint':service['endpoint'],'secret_ref':['bad']}]},
                {account[2]:[{'endpoint':service['endpoint'],'secret_ref':'secret value'}]}]:
        settings().wms_access_grants=json.dumps(bad)
        assert client.get('/api/wms/catalog').status_code==409
    settings().wms_access_grants=original
    connection=connect(client,service)
    response=client.post('/api/wms/connections/'+connection['id']+'/approve',json={'version':connection['version'],
        'layer':'public:render','crs':'EPSG:4326','bbox':[102,22,118,28],'style':'baseline','confirm_read_only':'true'})
    assert response.status_code==422 and service['maps']==0


def test_wms_business_source_freezes_provider_receipt(client,service):
    from tests.test_instance_workspace import configuration,prepared
    from tests.test_workflow_abilities import enable
    from tests.test_workflow_scenes import attach
    from generic_agents.models import WorkflowScene,WMSRead
    connection=connect(client,service);row,_=request_read(client,connection);row=finish(client,row)
    scene=client.get('/api/geo/scenes/'+row['scene_id']).json();assert process_once(scene['job_id'],deliver=False)
    scene=client.get('/api/geo/scenes/'+scene['id']).json()
    saved=client.put('/api/geo/scenes/'+scene['id']+'/configuration',json={'version':scene['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});assert saved.status_code==200
    assert process_once(saved.json()['job_id'],deliver=False);scene=client.get('/api/geo/scenes/'+scene['id']).json()
    release,_,_=prepared(client,configuration());enable(client,release);source=attach(client,release,scene,roi=[0,0,64,48]);assert source.status_code==201,source.text
    assert source.json()['snapshot']['wms_origin']['read_id']==row['id']
    with Session(engine()) as db:
        read=db.get(WMSRead,row['id']);data=dict(read.data);data['response']={**data['response'],'request_params':{**data['response']['request_params'],'BBOX':'changed'}};read.data=data;db.commit()
    assert client.get('/api/workspace/instances/'+release['instance_id']+'/input-scenes/'+source.json()['id']+'/preview').status_code==409


def test_malformed_saved_source_is_recoverable_conflict_not_server_error(client,service):
    from generic_agents.models import WMSRead
    connection=connect(client,service);row,_=request_read(client,connection);row=finish(client,row)
    with Session(engine()) as db:
        saved=db.get(WMSRead,row['id']);saved.data={**saved.data,'result':None};db.commit()
    response=client.get('/api/wms/reads/'+row['id']+'/export')
    assert response.status_code==409 and '证据结构不完整' in response.json()['detail']


def test_delayed_wms_derivation_keeps_map_request_day(client,service):
    from datetime import timedelta
    from generic_agents.models import WMSRead
    from generic_agents.geo_daily import day
    connection=connect(client,service);row,_=request_read(client,connection)
    with Session(engine()) as db:
        saved=db.get(WMSRead,row['id']);saved.created_at-=timedelta(days=2);requested_day=day(saved.created_at);db.commit()
    row=finish(client,row)
    response=client.post('/api/imagery-daily/reconcile');assert response.status_code==200
    collection=next(r for r in client.get('/api/imagery-daily').json()['rows'] if r['received_day']==requested_day)
    source=next(r for r in collection['records'] if r['id']==row['scene_id'])
    assert source['date_basis']=='WMS_map_read_request' and source['sources'][0]['upload_day']==requested_day
    assert source['wms_source_schema_version']==1 and source['integrity_verified']


def test_catalog_safe_legacy_doctype_inheritance_axis_and_entities():
    legacy=xml('1.1.1').replace(b'<?xml version="1.0"?>',b'<!DOCTYPE WMT_MS_Capabilities SYSTEM "http://127.0.0.1:1/not-fetched">')
    assert catalog(legacy,'1.1.1')['layers'][0]['bounds']['EPSG:4326']==[100,20,120,30]
    malicious=b'<!DOCTYPE WMS_Capabilities [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+xml().replace(b'<?xml version="1.0"?>',b'').replace('公开参考地图服务'.encode(),b'&x;')
    with pytest.raises(ValueError):catalog(malicious,'1.3.0')
    with pytest.raises(ValueError):catalog(xml(),'1.1.1')
    with pytest.raises(ValueError):normalize_endpoint('https://example.com/wms?token=secret')
    assert normalize_endpoint('https://example.com/wms?service=WMS&request=GetCapabilities')=='https://example.com/wms'
