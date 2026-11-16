import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from generic_agents import map_access as a

class MapAccessTest(unittest.TestCase):
 def setUp(self):
  self.db=object();self.admin=SimpleNamespace(role='admin',user_id='a');self.viewer=SimpleNamespace(role='viewer',user_id='v');self.operator=SimpleNamespace(role='operator',user_id='o')
 def test_legacy_workspace_retains_access(self):
  with patch.object(a,'_row',return_value=None):
   self.assertTrue(a.can_read(self.db,self.viewer,['巡护']));a.require_config(self.db,self.viewer)
 def test_explicit_policy_checks_business_roles_and_people(self):
  row=SimpleNamespace(data={'businesses':{'巡护':{'roles':['operator'],'users':['v']},'保护':{'roles':[],'users':[]}}})
  with patch.object(a,'_row',return_value=row):
   self.assertTrue(a.can_read(self.db,self.admin,[]));self.assertTrue(a.can_read(self.db,self.viewer,['巡护']));self.assertFalse(a.can_read(self.db,self.viewer,['保护']));self.assertTrue(a.can_read(self.db,self.operator,['巡护']));self.assertFalse(a.can_read(self.db,self.operator,[]))
   with self.assertRaises(HTTPException):a.require_config(self.db,self.viewer)
 def test_revocation_effect_is_checked_each_time(self):
  row=SimpleNamespace(data={'businesses':{'巡护':{'roles':['viewer'],'users':[]}}})
  with patch.object(a,'_row',return_value=row):
   self.assertTrue(a.can_read(self.db,self.viewer,['巡护']));row.data={'businesses':{'巡护':{'roles':[],'users':[]}}};self.assertFalse(a.can_read(self.db,self.viewer,['巡护']))
 def test_direct_geo_evidence_and_pixel_export_are_guarded(self):
  row=SimpleNamespace(data={'businesses':{}})
  with patch.object(a,'_row',return_value=row),patch('generic_agents.security.authenticated',return_value=self.viewer):
   for path in ('/api/geo/scenes/x/original','/api/wms/reads/x/response','/api/map-services/previews/x/image','/api/evidence/unknown/content','/api/workspace/instances/i/pixel-change/runs/r/image','/api/workspace/instances/i/documents/r/original','/api/setup/materials/x/content','/api/workspace/tasks/x/frame-supplements/y/content','/api/workspace/tasks/x/comparisons/y/artifacts/z','/api/runtime-documents/versions/x/original','/api/runtime-document-ocr/results/x/image','/api/business-data/reads/x/original'):
    with self.subTest(path=path),self.assertRaises(HTTPException):a.route_guard(SimpleNamespace(method='GET',url=SimpleNamespace(path=path)),self.db)
   a.route_guard(SimpleNamespace(method='GET',url=SimpleNamespace(path='/api/instance-observations/i')),self.db)
   a.route_guard(SimpleNamespace(method='POST',url=SimpleNamespace(path='/api/evidence')),self.db)
 def test_legacy_direct_paths_remain_accessible(self):
  with patch.object(a,'_row',return_value=None),patch('generic_agents.security.authenticated',return_value=self.viewer):
   a.route_guard(SimpleNamespace(method='GET',url=SimpleNamespace(path='/api/setup/materials/x/content')),self.db)
 def test_video_resource_requires_associated_enabled_business(self):
  from generic_agents import instance_observation_catalog as catalog
  instance=SimpleNamespace(data={'enabled_business_ids':['active']})
  release=SimpleNamespace(data={'snapshot':{'businesses':[{'id':'active','name':'巡护','available':True},{'id':'off','name':'防火','available':True}],
    'resources':[{'id':'allowed','category':'video','protocol':'RTSP / H.264','businesses':['巡护']},{'id':'disabled-business','category':'video','protocol':'RTSP / H.264','businesses':['防火']},{'id':'no-business','category':'video','protocol':'RTSP / H.264','businesses':[]}]}})
  row=SimpleNamespace(data={'businesses':{'巡护':{'roles':['viewer'],'users':[]},'防火':{'roles':['viewer'],'users':[]}}})
  with patch.object(catalog,'active',return_value=(instance,release)),patch.object(a,'_row',return_value=row):
   self.assertEqual([x['id'] for x in catalog.resources(self.db,self.viewer,'i')],['allowed'])
   self.assertEqual(catalog.one_resource(self.db,self.viewer,'i','allowed')['id'],'allowed')
   with self.assertRaises(HTTPException):catalog.one_resource(self.db,self.viewer,'i','disabled-business')
   row.data={'businesses':{'巡护':{'roles':[],'users':[]}}}
   with self.assertRaises(HTTPException):catalog.one_resource(self.db,self.viewer,'i','allowed')
   self.assertEqual(len(catalog.resources(self.db,self.admin,'i')),3)
 def test_direct_video_and_stream_bypass_are_guarded(self):
  with patch.object(a,'_row',return_value=SimpleNamespace(data={'businesses':{}})),patch('generic_agents.security.authenticated',return_value=self.viewer):
   for path in ('/api/videos/run/original','/api/workspace/tasks/t/video/v/original','/api/media/channels/c/stream','/api/media/channels/c/capture'):
    with self.subTest(path=path),self.assertRaises(HTTPException):a.route_guard(SimpleNamespace(method='GET',url=SimpleNamespace(path=path)),self.db)
   with self.assertRaises(HTTPException):a.route_guard(SimpleNamespace(method='POST',url=SimpleNamespace(path='/api/media/channels/c/capture')),self.db)
 def test_public_source_requires_enabled_associated_and_granted_business(self):
  from generic_agents import public_observations as public
  cap=SimpleNamespace(id='s',content_hash='h');instance=SimpleNamespace(data={'enabled_business_ids':['b1']})
  resource={'protocol':'PUBLIC_OBSERVATION','details':{'public_source_id':'s','manifest_hash':'h'},'businesses':['巡护']}
  release=SimpleNamespace(data={'snapshot':{'resources':[resource],'businesses':[{'id':'b1','name':'巡护','available':True}]}})
  policy=SimpleNamespace(data={'businesses':{'巡护':{'roles':['viewer'],'users':[]}}})
  with patch.object(public,'active',return_value=(instance,release)),patch.object(public,'verified_cap',return_value=(cap,'nps_redwood')),patch.object(a,'_row',return_value=policy):
   self.assertEqual(public.configured_cap(self.db,self.viewer,'i','s')[0],cap)
   policy.data={'businesses':{'巡护':{'roles':[],'users':[]}}}
   with self.assertRaises(HTTPException):public.configured_cap(self.db,self.viewer,'i','s')
   policy.data={'businesses':{'巡护':{'roles':['viewer'],'users':[]}}};instance.data={'enabled_business_ids':[]}
   with self.assertRaises(HTTPException):public.configured_cap(self.db,self.viewer,'i','s')
 def test_source_registry_filtered_and_public_instance_image_reaches_business_guard(self):
  policy=SimpleNamespace(data={'businesses':{}});rows=[SimpleNamespace(kind='source'),SimpleNamespace(kind='tool')]
  with patch.object(a,'_row',return_value=policy),patch('generic_agents.security.authenticated',return_value=self.viewer):
   self.assertEqual(a.filter_capabilities(self.db,self.viewer,rows),[rows[1]])
   self.assertEqual(a.filter_capabilities(self.db,self.operator,rows),[rows[1]])
   self.assertEqual(a.filter_capabilities(self.db,self.admin,rows),rows)
   for tail in ('snapshot','tiles/2026-11-14/1/0/0'):
    a.route_guard(SimpleNamespace(method='GET',url=SimpleNamespace(path='/api/public-observations/12345678-1234-1234-1234-123456789012/'+tail)),self.db)
   with self.assertRaises(HTTPException):a.route_guard(SimpleNamespace(method='GET',url=SimpleNamespace(path='/api/public-observations/untrusted/snapshot')),self.db)
 def test_releases_cannot_disclose_configuration_to_viewer(self):
  from generic_agents import instance_api
  with patch.object(a,'_row',return_value=SimpleNamespace(data={'businesses':{}})):
   with self.assertRaises(HTTPException):instance_api.releases('i',self.viewer,self.db)
   with self.assertRaises(HTTPException):instance_api.releases('i',self.operator,self.db)

if __name__=='__main__':unittest.main()
