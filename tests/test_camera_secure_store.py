import asyncio, json, os, tempfile, unittest, uuid, subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from generic_agents import camera_secure_store as store
from generic_agents import camera_secure_config_api as api
from starlette.requests import Request

class SecureStoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.settings=patch.object(store,'settings',return_value=SimpleNamespace(storage_dir=self.root/'evidence'));self.settings.start();self.addCleanup(self.settings.stop)
  key=self.root/'cert.key';cert=self.root/'cert.pem'
  subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=fixture'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  self.w=str(uuid.uuid4());self.ref=store.reference(self.w,str(uuid.uuid4()));self.value={'workspace_id':self.w,'actor_id':'actor','candidate_id':'a'*32,'source_ip':'192.168.1.2','service_address':'https://192.168.1.2/onvif','gateway_id':'gateway','gateway_origin':'http://127.0.0.1:19997','username':'fixture-user','password':'fixture-secret','certificate':cert.read_text()}
 def test_encrypted_persistence_scope_and_revoke(self):
  self.assertEqual(store.save(self.ref,self.value),'saved');path,key=store._paths(self.ref)
  self.assertNotIn(b'fixture-secret',path.read_bytes());self.assertEqual(path.stat().st_mode&0o777,0o600);self.assertEqual(key.stat().st_mode&0o777,0o600)
  self.assertEqual(store.read(self.ref)['password'],'fixture-secret');self.assertEqual(store.save(self.ref,self.value),'saved')
  device={'source_ip':self.value['source_ip'],'service_address':self.value['service_address']};gateway=SimpleNamespace(id='gateway',data={'api_origin':self.value['gateway_origin']})
  self.assertEqual(store.matching(self.w,device,gateway),[])
  self.assertEqual(store.matching(self.w,device,gateway,include_unaudited=True),[self.ref])
  store.mark_audited(self.ref);self.assertEqual(store.matching(self.w,device,gateway),[self.ref]);self.assertEqual(store.matching(str(uuid.uuid4()),device,gateway),[])
  store.revoke(self.ref,self.w,'actor');self.assertTrue(store.revoked(self.ref));self.assertEqual(store.revocation_actor(self.ref),'actor');self.assertEqual(store.matching(self.w,device,gateway),[])
 def test_cancel_before_submit_blocks_late_write(self):
  self.assertEqual(store.cancel(self.ref,self.w,'actor','gateway','a'*32),'cancelled');self.assertEqual(store.save(self.ref,self.value),'cancelled');self.assertNotIn('password',store.read(self.ref))
 def test_concurrent_submit_and_cancel_have_single_outcome(self):
  with ThreadPoolExecutor(max_workers=2) as pool:
   a=pool.submit(store.save,self.ref,self.value);b=pool.submit(store.cancel,self.ref,self.w,'actor','gateway','a'*32);one,two=a.result(),b.result()
  self.assertEqual(one,two)
 def test_invalid_certificate_and_lost_key_fail_closed(self):
  with self.assertRaises(store.StoreError):store.save(self.ref,{**self.value,'certificate':'not PEM'})
  store.save(self.ref,self.value);_,key=store._paths(self.ref);key.unlink()
  with self.assertRaises(store.StoreError):store.save(store.reference(self.w,str(uuid.uuid4())),self.value)
  self.assertFalse(key.exists())
 def test_graphical_api_never_returns_secrets_and_grants_exact_target(self):
  scan=SimpleNamespace(kind='onvif_discovery_scan',status='internal',data={'confirmed_ids':['a'*32],'devices':[{'candidate_id':'a'*32,'source_ip':'192.168.1.2','service_address':'https://192.168.1.2/onvif'}]})
  gateway=SimpleNamespace(id='gateway',status='draft',data={'api_origin':'http://127.0.0.1:19997'})
  p=SimpleNamespace(workspace_id=self.w,user_id='actor',role='admin');db=SimpleNamespace(commit=lambda:None)
  rid=str(uuid.uuid4());body={'request_id':rid,'scan_id':'scan','candidate_id':'a'*32,'gateway_id':'gateway','username':'fixture-user','password':'fixture-secret','certificate':self.value['certificate'],'confirm_identity':True,'confirm_trust':True}
  async def receive():return {'type':'http.request','body':json.dumps(body).encode(),'more_body':False}
  request=Request({'type':'http','method':'POST','path':'/','headers':[],'scheme':'http','server':('127.0.0.1',8766),'client':('127.0.0.1',1234),'query_string':b''},receive)
  with patch.object(api,'scoped',side_effect=[scan,gateway]),patch.object(api,'require'),patch.object(api,'origin',return_value='http://127.0.0.1:19997'),patch.object(api,'ensure_created_audit',side_effect=lambda db,p,ref,item:store.mark_audited(ref)):
   result=asyncio.run(api.create(request,p,db))
  self.assertEqual(result['state'],'saved');self.assertNotIn('fixture-secret',str(result));self.assertEqual(store.read(result['reference'])['source_ip'],'192.168.1.2')
 def test_audit_can_be_recovered_after_file_was_saved(self):
  store.save(self.ref,self.value);db=unittest.mock.MagicMock();db.scalar.return_value=None;p=SimpleNamespace(workspace_id=self.w,user_id='actor')
  api.ensure_created_audit(db,p,self.ref,self.value)
  self.assertTrue(store.audited(self.ref));self.assertEqual(db.add.call_count,1);self.assertEqual(db.add.call_args.args[0].actor_id,'actor')
  db.scalar.return_value=SimpleNamespace(actor_id='actor');api.ensure_created_audit(db,p,self.ref,self.value);self.assertEqual(db.add.call_count,1)
 def test_other_admin_recovers_original_actors_pending_audit_without_secret(self):
  store.save(self.ref,self.value);device={'candidate_id':'a'*32,'source_ip':self.value['source_ip'],'service_address':self.value['service_address']};scan=SimpleNamespace(kind='onvif_discovery_scan',status='internal',data={'confirmed_ids':['a'*32],'devices':[device]});gateway=SimpleNamespace(id='gateway',data={'api_origin':self.value['gateway_origin']});p=SimpleNamespace(workspace_id=self.w,user_id='other-admin',role='admin');db=unittest.mock.MagicMock();db.scalar.return_value=None
  with patch.object(api,'require'),patch.object(api,'scoped',side_effect=[scan,gateway]):rows=api.pending_audits('scan','a'*32,'gateway',p,db)
  self.assertEqual(rows['records'][0]['reference'],self.ref);self.assertNotIn('fixture-secret',str(rows));self.assertNotIn('fixture-user',str(rows))
  with patch.object(api,'require'):out=api.recover_audit(self.ref,p,db)
  self.assertTrue(out['audit_recovered']);self.assertEqual(db.add.call_args.args[0].actor_id,'actor');self.assertTrue(store.audited(self.ref))
 def test_inconsistent_backup_cannot_reactivate_revoked_credentials(self):
  from fastapi import HTTPException
  store.save(self.ref,self.value);store.mark_audited(self.ref);db=unittest.mock.MagicMock();p=SimpleNamespace(workspace_id=self.w,user_id='other-admin',role='admin')
  db.scalar.side_effect=[SimpleNamespace(actor_id='actor'),SimpleNamespace(actor_id='actor')]
  self.assertFalse(api.audit_is_present(db,p,self.ref))
  db.scalar.side_effect=None;db.scalar.return_value=SimpleNamespace(actor_id='actor')
  with patch.object(api,'require'),self.assertRaises(HTTPException) as caught:api.recover_audit(self.ref,p,db)
  self.assertEqual(caught.exception.status_code,409)

if __name__=='__main__':unittest.main()
