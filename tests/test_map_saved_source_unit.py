import base64
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from generic_agents import map_saved_source as source

class SavedMapSourceTest(unittest.TestCase):
    def setUp(self):
        raw=b'saved image bytes for identity test'
        self.p=SimpleNamespace(workspace_id='workspace-a',user_id='admin-a',role='admin')
        self.c=SimpleNamespace(id='feed0000-0000-4000-8000-000000000000',kind='map_service_connection',status='internal',
           data={'protocol':'wmts','name':'测试来源','endpoint':'http://127.0.0.1:18891/wmts','secret_ref':''})
        self.v=SimpleNamespace(id='beef0000-0000-4000-8000-000000000000',kind='map_service_preview',status='internal',
           data={'connection_id':self.c.id,'png':base64.b64encode(raw).decode(), 'sha256':hashlib.sha256(raw).hexdigest(),
                 'catalog_sha256':'a'*64,'selection':{'layer':'test','row':0,'col':0}})
        self.scope=patch.object(source,'scoped',side_effect=lambda db,model,identity,p: {self.v.id:self.v,self.c.id:self.c}[identity]);self.scope.start()
        self.endpoint=patch('generic_agents.map_services_api._url',return_value=self.c.data['endpoint']);self.endpoint.start()
        self.auth=patch('generic_agents.wms.authorize');self.auth.start()
    def tearDown(self):
        self.scope.stop();self.endpoint.stop();self.auth.stop()
    def test_manifest_freezes_map_and_never_performs_network_read(self):
        preview,connection=source._load(None,self.p,self.v.id)
        manifest=source._manifest(preview,connection)
        self.assertEqual(manifest.execution['adapter'],'saved_gateway_map')
        self.assertEqual(manifest.execution['proof']['sha256'],self.v.data['sha256'])
        self.assertFalse(manifest.specification['raw_satellite_bands'])
        self.assertFalse(manifest.execution['side_effect'])
    def test_source_proof_rejects_mismatched_resource_or_image(self):
        manifest=source._manifest(self.v,self.c)
        cap=SimpleNamespace(id='cap1',data=manifest.model_dump(mode='json'),content_hash=source.digest(manifest.model_dump(mode='json')))
        receipt=SimpleNamespace(data={'manifest_hash':cap.content_hash,'capability_id':cap.id})
        with patch.object(source,'published',return_value=cap),patch.object(source,'_publication',return_value=receipt):
            resource={'protocol':'WMTS','details':{'capability_id':cap.id,'map_connection_id':self.c.id,'map_preview_id':self.v.id,'manifest_hash':cap.content_hash}}
            self.assertEqual(source.resource_proof(None,self.p,resource)['sha256'],self.v.data['sha256'])
            resource['details']['map_preview_id']='other'
            with self.assertRaises(HTTPException):source.resource_proof(None,self.p,resource)
            self.v.data['png']=base64.b64encode(b'changed').decode()
            with self.assertRaises(HTTPException):source.current_source(None,self.p,cap.id)
    def test_cannot_treat_arcgis_map_as_wmts(self):
        manifest=source._manifest(self.v,self.c)
        cap=SimpleNamespace(id='cap1',data=manifest.model_dump(mode='json'),content_hash=source.digest(manifest.model_dump(mode='json')))
        receipt=SimpleNamespace(data={'manifest_hash':cap.content_hash,'capability_id':cap.id})
        with patch.object(source,'published',return_value=cap),patch.object(source,'_publication',return_value=receipt):
            resource={'protocol':'ArcGIS MapServer','details':{'capability_id':cap.id,'map_connection_id':self.c.id,'map_preview_id':self.v.id,'manifest_hash':cap.content_hash}}
            with self.assertRaises(HTTPException):source.resource_proof(None,self.p,resource)
    def test_only_enabled_business_exposes_new_fixed_map_source(self):
        from generic_agents import instance_observation_catalog as catalog
        mapped={'id':'map1','category':'maps','protocol':'WMTS','businesses':['林地调查']}
        legacy={'id':'wms1','category':'maps','protocol':'WMS','businesses':[]}
        instance=SimpleNamespace(data={'enabled_business_ids':['other-id']})
        release=SimpleNamespace(data={'snapshot':{'resources':[mapped,legacy], 'businesses':[
           {'id':'forest-id','name':'林地调查','available':True}, {'id':'other-id','name':'其他业务','available':True}]}})
        with patch.object(catalog,'active',return_value=(instance,release)):
            self.assertEqual([r['id'] for r in catalog.resources(None,self.p,'instance')],['wms1'])
            instance.data['enabled_business_ids']=['forest-id']
            self.assertEqual([r['id'] for r in catalog.resources(None,self.p,'instance')],['map1','wms1'])
    def test_completed_receipt_survives_revoked_access_without_granting_use(self):
        manifest=source._manifest(self.v,self.c)
        cap=SimpleNamespace(id='cap1',data=manifest.model_dump(mode='json'),content_hash=source.digest(manifest.model_dump(mode='json')))
        publication=SimpleNamespace(data={'capability_id':cap.id,'manifest_hash':cap.content_hash})
        record={'capability_id':cap.id,'manifest_hash':cap.content_hash,'preview_id':self.v.id,
          'connection_id':self.c.id,'protocol':'wmts','name':self.c.data['name'],'selection':self.v.data['selection']}
        attempt=SimpleNamespace(kind='map_source_attempt',status='internal',data={'actor_id':self.p.user_id,'preview_id':self.v.id,
          'state':'created','capability_id':cap.id,'record':record,'record_hash':source.digest(record)})
        body=source.Publication(request_id='request-12345678',preview_id=self.v.id)
        with patch.object(source,'scoped',return_value=cap),patch.object(source,'_publication',return_value=publication), \
             patch.object(source,'current_source',side_effect=HTTPException(409,'授权已撤销')):
            result=source._attempt_result(None,self.p,body,attempt)
            self.assertEqual(result['state'],'created')
            self.assertIs(result['source_valid'],False)
            self.assertEqual(result['preview_id'],self.v.id)
            self.assertIn('授权',result['source_message'])
            record['name']='篡改的名称'
            with self.assertRaises(HTTPException):source._attempt_result(None,self.p,body,attempt)
    def test_legacy_receipt_recovers_from_manifest_after_authorization_revoked(self):
        manifest=source._manifest(self.v,self.c)
        cap=SimpleNamespace(id='cap1',data=manifest.model_dump(mode='json'),content_hash=source.digest(manifest.model_dump(mode='json')))
        publication=SimpleNamespace(data={'capability_id':cap.id,'manifest_hash':cap.content_hash})
        attempt=SimpleNamespace(kind='map_source_attempt',status='internal',data={'actor_id':self.p.user_id,'preview_id':self.v.id,
          'state':'created','capability_id':cap.id})
        body=source.Publication(request_id='request-legacy-123',preview_id=self.v.id)
        def lookup(db,model,identity,p):return cap if model is source.Capability else self.c
        with patch.object(source,'scoped',side_effect=lookup),patch.object(source,'_publication',return_value=publication), \
             patch.object(source,'current_source',side_effect=HTTPException(409,'授权已撤销')):
            result=source._attempt_result(None,self.p,body,attempt)
            self.assertEqual(result['state'],'created')
            self.assertIs(result['source_valid'],False)
            self.assertEqual(result['name'],self.c.data['name'])
            self.c.data['name']='名称被篡改'
            with self.assertRaises(HTTPException):source._attempt_result(None,self.p,body,attempt)

if __name__=='__main__':unittest.main()
