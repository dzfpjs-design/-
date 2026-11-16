import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException, Response
from generic_agents import map_services_api as api

class DB:
    def __init__(self): self.rows={}; self.commits=0
    def add(self,row): self.rows[row.idempotency_key]=row
    def commit(self): self.commits+=1
    def flush(self): pass

class MapOperationRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.p=SimpleNamespace(workspace_id='workspace',role='admin',user_id='actor')
        self.connection=SimpleNamespace(id='connection',version=1,data={'protocol':'arcgis','endpoint':'http://127.0.0.1:8/MapServer','secret_ref':'','catalog':None})
        self.patches=[patch.object(api,'_lock',lambda *args:None),patch.object(api,'audit',lambda *args:None),
            patch.object(api,'_job',lambda db,p,key:db.rows.get(key)),patch.object(api,'_connection',lambda db,p,i:self.connection)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
    def test_total_deadline_prevents_starting_late_service_request(self):
        with patch.object(api,'authorize'),patch.object(api,'_url',return_value='https://8.8.8.8/wmts'),patch.object(api,'headers',return_value={}):
            with self.assertRaises(HTTPException) as e:
                api._fetch({'workspace_id':'workspace','endpoint':'https://8.8.8.8/wmts'}, {}, 1024,deadline=0)
            self.assertEqual(e.exception.status_code,504)
    def test_cancelled_create_blocks_late_request_and_is_idempotent(self):
        req='aaaaaaaaaaaa';stop=api.Resolve(kind='create',request_id=req)
        self.assertEqual(api.resolve(stop,self.p,self.db)['state'],'cancelled')
        self.assertEqual(api.resolve(stop,self.p,self.db)['state'],'cancelled')
        with self.assertRaises(HTTPException) as e:
            api.create(api.Connect(request_id=req,name='Test',protocol='wmts',endpoint='http://127.0.0.1:8/wmts'),Response(),self.p,self.db)
        self.assertEqual(e.exception.status_code,409)
    def test_cancelled_catalog_and_preview_block_late_request(self):
        for kind in ('catalog','preview'):
            req=kind+'-request-12345';api.resolve(api.Resolve(kind=kind,request_id=req,connection_id='connection'),self.p,self.db)
            body=api.Catalog(request_id=req,version=1) if kind=='catalog' else api.Capture(request_id=req,version=1,selection={},confirm_read_only=True)
            with patch.object(api,'_fetch',side_effect=AssertionError('network must not run')):
                with self.assertRaises(HTTPException) as e:
                    if kind=='catalog':api.catalog('connection',body,self.p,self.db)
                    else:api.capture('connection',body,Response(),self.p,self.db)
                self.assertEqual(e.exception.status_code,409)
    def test_completed_operation_is_not_misreported_cancelled(self):
        req='abcdefghijkl';body=api.Capture(request_id=req,version=1,selection={'layer':'0','bbox':[1,2,3,4]},confirm_read_only=True)
        self.db.rows[api._key(api.READ,req)]=SimpleNamespace(kind=api.READ,status='internal',data={'connection_id':'connection','submission':body.model_dump()})
        result=api.resolve(api.Resolve(kind='preview',request_id=req,connection_id='connection'),self.p,self.db)
        self.assertEqual(result['state'],'completed');self.assertEqual(result['submission'],body.model_dump())
    def test_dns_names_and_credentials_in_address_are_not_accepted(self):
        with self.assertRaises(HTTPException):api._url('https://example.org/wmts','wmts')
        with self.assertRaises(HTTPException):api._url('http://u:p@127.0.0.1:8/wmts','wmts')

    def test_wmts_stitches_four_tiles_and_rejects_unauthorised_range_before_fetch(self):
        import io, base64
        from PIL import Image
        matrix={'id':'z','width':2,'height':2,'tile_width':2,'tile_height':2,'limits':{'min_row':0,'max_row':1,'min_col':0,'max_col':1}}
        self.connection.data.update(protocol='wmts',catalog={'layers':[{'id':'forest','styles':['default'],'matrix_sets':[{'id':'grid','matrices':[matrix]}]}]},catalog_sha256='abc')
        selection={'layer':'forest','matrix_set':'grid','matrix':'z','style':'default','row':0,'col':0,'max_row':1,'max_col':1}
        def fetch(conf,params,limit,suffix='',db=None):
            out=io.BytesIO();Image.new('RGB',(2,2),(int(params['TILECOL'])*100,int(params['TILEROW'])*100,0)).save(out,'PNG');return out.getvalue(),'image/png'
        with patch.object(api,'_fetch',side_effect=fetch):
            api.capture('connection',api.Capture(request_id='four-tiles-0001',version=1,selection=selection,confirm_read_only=True),Response(),self.p,self.db)
        data=self.db.rows[api._key(api.READ,'four-tiles-0001')].data
        with Image.open(io.BytesIO(base64.b64decode(data['png']))) as image:
            self.assertEqual(image.size,(4,4));self.assertEqual(image.getpixel((3,3))[:3],(100,100,0))
        self.connection.version=1
        with patch.object(api,'_fetch',side_effect=AssertionError('unauthorised range must not fetch')):
            with self.assertRaises(HTTPException):api.capture('connection',api.Capture(request_id='invalid-range-01',version=1,selection={**selection,'max_col':2},confirm_read_only=True),Response(),self.p,self.db)

    def test_wmts_single_tile_size_must_match_catalog(self):
        import io
        from PIL import Image
        matrix={'id':'z','width':1,'height':1,'tile_width':2,'tile_height':2}
        self.connection.data.update(protocol='wmts',catalog={'layers':[{'id':'f','styles':['d'],'matrix_sets':[{'id':'g','matrices':[matrix]}]}]},catalog_sha256='abc')
        selection={'layer':'f','matrix_set':'g','matrix':'z','style':'d','row':0,'col':0}
        raw=io.BytesIO();Image.new('RGB',(1,1)).save(raw,'PNG')
        with patch.object(api,'_fetch',return_value=(raw.getvalue(),'image/png')):
            with self.assertRaises(HTTPException) as result:api.capture('connection',api.Capture(request_id='bad-tile-size-1',version=1,selection=selection,confirm_read_only=True),Response(),self.p,self.db)
        self.assertEqual(result.exception.status_code,502)

    def test_compressed_response_rejected_before_stream_decompression(self):
        class ResponseMock:
            status_code=200; headers={'content-encoding':'gzip'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def raise_for_status(self):pass
            def iter_raw(self,**kwargs):raise AssertionError('compressed body must never be read')
        class ClientMock:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def stream(self,*args,**kwargs):
                assert kwargs['headers']['Accept-Encoding']=='identity';return ResponseMock()
        with patch.object(api,'authorize'),patch.object(api,'_url',return_value='http://127.0.0.1:8'),patch.object(api,'headers',return_value={}),patch.object(api.httpx,'Client',ClientMock):
            with self.assertRaises(HTTPException) as result:api._fetch({'workspace_id':'workspace','endpoint':'http://127.0.0.1:8'}, {}, 1024)
        self.assertEqual(result.exception.status_code,502)
