import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException,Response
from generic_agents import feature_gateway_api as api

class DB:
 def __init__(self):self.rows={}
 def add(self,x):self.rows[x.idempotency_key]=x
 def flush(self):
  for x in self.rows.values():
   if not x.id:x.id='saved-id'
 def commit(self):pass

class FeatureServiceTests(unittest.TestCase):
 def setUp(self):
  self.p=SimpleNamespace(role='admin',workspace_id='w',user_id='u');self.db=DB()
  self.row=SimpleNamespace(id='conn',version=1,data={'name':'test','endpoint':'http://127.0.0.1:88/FeatureServer','secret_ref':'','request_id':'connection-id-123','catalog':None})
  self.ps=[patch.object(api,'_lock',lambda *x:None),patch.object(api,'audit',lambda *x:None),patch.object(api,'job',lambda db,p,k:db.rows.get(k)),patch.object(api,'connection',lambda *x:self.row)]
  for p in self.ps:p.start()
 def tearDown(self):
  for p in self.ps:p.stop()
 def test_catalog_shares_one_total_deadline_across_layer_requests(self):
  with patch.object(api,'_fetch',side_effect=self.fake) as fetch:
   api.catalog('conn',api.Version(request_id='catalog-deadline-123',version=1),self.p,self.db)
  self.assertEqual(len(fetch.call_args_list),2)
  self.assertEqual(fetch.call_args_list[0].kwargs['deadline'],fetch.call_args_list[1].kwargs['deadline'])
 def fake(self,conf,params,limit,suffix='',db=None,deadline=None):
  if suffix=='':v={'layers':[{'id':0,'name':'trees'}]}
  elif suffix=='/0':v={'name':'trees','extent':{'xmin':116,'ymin':23,'xmax':119,'ymax':26,'spatialReference':{'wkid':4326}},'fields':[{'name':'name','alias':'名称','type':'esriFieldTypeString'}]}
  else:v={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':'a'},'geometry':{'type':'Point','coordinates':[117,24]}}]}
  return json.dumps(v).encode(),'application/json'
 def test_catalog_and_read_then_repeat_are_bounded(self):
  with patch.object(api,'_fetch',side_effect=self.fake):
   c=api.catalog('conn',api.Version(request_id='catalog-0000001',version=1),self.p,self.db);self.assertEqual(c['catalog']['layers'][0]['fields'][0]['name'],'name')
   b=api.Read(request_id='reading-0000001',version=2,layer=0,fields=['name'],bounds=(116.5,23.5,118.5,25.5),confirm_read_only=True)
   a=api.read('conn',b,Response(),self.p,self.db);again=api.read('conn',b,Response(),self.p,self.db);self.assertEqual(a['id'],again['id']);self.assertEqual(len(self.db.rows[api.key(api.READ,b.request_id)].data['features']['features']),1);self.assertIsInstance(a['submission']['bounds'],list);self.assertEqual(json.loads(json.dumps(a['submission'])),again['submission'])
 def test_rejects_unlisted_fields_and_outside_bounds_before_network(self):
  with patch.object(api,'_fetch',side_effect=self.fake):api.catalog('conn',api.Version(request_id='catalog-0000002',version=1),self.p,self.db)
  for fields,bounds in [(['password'],(117,24,118,25)),(['name'],(0,0,1,1))]:
   with patch.object(api,'_fetch',side_effect=AssertionError('network should not run')):
    with self.assertRaises(HTTPException):api.read('conn',api.Read(request_id='reading-0000002',version=2,layer=0,fields=fields,bounds=bounds,confirm_read_only=True),Response(),self.p,self.db)
 def test_cancelled_read_cannot_execute_late(self):
  body=api.Resolve(kind='read',request_id='reading-0000003',connection_id='conn');self.assertEqual(api.resolve(body,self.p,self.db)['state'],'cancelled')
  with patch.object(api,'_fetch',side_effect=AssertionError('cancelled operation must not run')):
   with self.assertRaises(HTTPException):api.read('conn',api.Read(request_id=body.request_id,version=1,layer=0,fields=['name'],bounds=(117,24,118,25),confirm_read_only=True),Response(),self.p,self.db)
 def test_vector_instance_requires_enabled_business_and_actor_authorization(self):
  from generic_agents import instance_observation_catalog as catalog
  resource={'id':'vector-test','category':'maps','protocol':'ArcGIS FeatureServer','businesses':['巡护']}
  instance=SimpleNamespace(data={'enabled_business_ids':['b1']});release=SimpleNamespace(data={'snapshot':{'resources':[resource],'businesses':[{'id':'b1','name':'巡护','available':True}]}})
  allowed=SimpleNamespace(role='viewer',user_id='allowed');denied=SimpleNamespace(role='viewer',user_id='denied')
  with patch.object(catalog,'active',return_value=(instance,release)),patch('generic_agents.map_access.can_read',side_effect=lambda db,p,names:p.user_id=='allowed' and names==['巡护']):
   self.assertEqual(catalog.resources(None,allowed,'i'),[resource]);self.assertEqual(catalog.resources(None,denied,'i'),[])
   with self.assertRaises(HTTPException) as e:catalog.one_resource(None,denied,'i','vector-test')
   self.assertEqual(e.exception.status_code,404)
   instance.data['enabled_business_ids']=[];self.assertEqual(catalog.resources(None,allowed,'i'),[])
 def test_published_receipt_remains_recoverable_after_access_revoked(self):
  fixed={'capability_id':'cap','manifest_hash':'a'*64,'read_id':'read','connection_id':'connection','name':'test','selection':{'layer':0}}
  receipt=SimpleNamespace(kind=api.ATTEMPT,data={'actor_id':'u','read_id':'read','state':'created','snapshot':fixed,'snapshot_hash':api.digest(fixed)})
  body=api.Publication(request_id='publication-0001',read_id='read')
  with patch.object(api,'current_source',side_effect=HTTPException(403,'revoked')):
   result=api.pub_result(None,self.p,body,receipt);self.assertEqual(result['state'],'created');self.assertFalse(result['source_valid']);self.assertEqual(result['read_id'],'read')
 def test_instance_observation_returns_valid_geojson_and_enforces_membership(self):
  from generic_agents import instance_observation_catalog as catalog
  resource={'id':'source','category':'maps','protocol':'ArcGIS FeatureServer','businesses':['巡护']}
  payload={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':'林木'},'geometry':{'type':'Point','coordinates':[117,24]}}]}
  row=SimpleNamespace(data={'features':payload})
  with patch.object(catalog,'one_resource',return_value=resource),patch.object(catalog,'verified',return_value=[{'scene_id':'r1'}]),patch.object(api,'read_record',return_value=row):
   response=catalog.observation('i','source','r1',self.p,self.db)
   self.assertEqual(response.media_type,'application/geo+json');self.assertEqual(json.loads(response.body),payload);self.assertIn('no-store',response.headers['cache-control'])
   with self.assertRaises(HTTPException) as e:catalog.observation('i','source','not-published',self.p,self.db)
   self.assertEqual(e.exception.status_code,404)
 def test_geometry_rejects_bbox_overlap_without_actual_intersection_and_holes(self):
  box=[0,0,1,1]
  self.assertFalse(api.geometry_intersects({'type':'LineString','coordinates':[[-1,0.6],[0.4,2]]},box))
  self.assertTrue(api.geometry_intersects({'type':'LineString','coordinates':[[-1,-1],[2,2]]},box))
  self.assertFalse(api.geometry_intersects({'type':'Polygon','coordinates':[[[-5,-5],[5,-5],[5,5],[-5,5],[-5,-5]],[[-2,-2],[2,-2],[2,2],[-2,2],[-2,-2]]]},box))
  self.assertTrue(api.geometry_intersects({'type':'Polygon','coordinates':[[[-5,-5],[5,-5],[5,5],[-5,5],[-5,-5]]]},box))
 def test_geometry_rejects_malformed_and_non_wgs84(self):
  for g in [{'type':'Point','coordinates':[181,0]},{'type':'Point','coordinates':[1]},{'type':'LineString','coordinates':[[0,0]]},{'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,1]]]},{'type':'Polygon','coordinates':[[[0,0],[2,2],[0,2],[2,0],[0,0]]]},{'type':'Point','coordinates':[float('nan'),0]},{'type':'GeometryCollection','geometries':[]}]:
   with self.subTest(g=g),self.assertRaises(ValueError):api.geometry_intersects(g,[0,0,1,1])
 def test_service_outside_requested_area_is_not_saved(self):
  with patch.object(api,'_fetch',side_effect=self.fake):api.catalog('conn',api.Version(request_id='catalog-0000004',version=1),self.p,self.db)
  def outside(*args,**kwargs):return json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':'a'},'geometry':{'type':'Point','coordinates':[118,25]}}]}).encode(),'application/json'
  with patch.object(api,'_fetch',side_effect=outside):
   with self.assertRaises(HTTPException) as exc:api.read('conn',api.Read(request_id='reading-0000004',version=2,layer=0,fields=['name'],bounds=(116.5,23.5,117.5,24.5),confirm_read_only=True),Response(),self.p,self.db)
  self.assertEqual(exc.exception.status_code,502);self.assertNotIn(api.key(api.READ,'reading-0000004'),self.db.rows)

if __name__=='__main__':unittest.main()
