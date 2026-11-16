import os
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from generic_agents import ftps_transport as transport
from generic_agents import ftps_gateway_api as api

EP={'host':'192.168.0.100','port':18897,'tls_name':'192.168.0.100','data_port_min':18898,'data_port_max':18902}
GRANT={'name':'test','endpoint':EP,'root':'/','username_ref':'GA_FTPS_USERNAME_TEST','password_ref':'GA_FTPS_PASSWORD_TEST','ca_ref':'','server_chroot_confirmed':True}

class TransportTests(unittest.TestCase):
 def configuration(self,workspaces=None):
  import json
  return SimpleNamespace(ftps_allowed_endpoints=json.dumps([EP]),ftps_access_grants=json.dumps(workspaces or {'work':[GRANT]}))
 def test_grants_are_scoped_and_default_closed(self):
  with patch.object(transport,'settings',return_value=self.configuration()):
   one=transport.grants('work');self.assertEqual(len(one),1);self.assertEqual(transport.grants('other'),[])
   self.assertNotIn('password_ref',transport.public_grant(one[0]))
   with self.assertRaises(transport.FTPSError):transport.find_grant('other',one[0]['id'])
 def test_server_chroot_and_exact_global_endpoint_required(self):
  bad={**GRANT,'server_chroot_confirmed':False}
  with patch.object(transport,'settings',return_value=self.configuration({'work':[bad]})):
   with self.assertRaises(transport.FTPSError):transport.grants('work')
  bad={**GRANT,'endpoint':{**EP,'port':1234}}
  with patch.object(transport,'settings',return_value=self.configuration({'work':[bad]})):
   with self.assertRaises(transport.FTPSError):transport.grants('work')
 def test_unsafe_names_root_and_control_chars_are_rejected(self):
  for value in ('/a/../b','/a/./b','/a//b','/a\nRETR x'):
   with self.assertRaises(transport.FTPSError):transport._path(value)
  for name in ('../a','a/b','a\\b','a\r\nDELE x'):
   self.assertFalse(transport._entry_name(name))
 def test_pasv_pin_and_allowed_port(self):
  ftp=transport._PinnedFTP(GRANT,ssl.create_default_context())
  with patch('ftplib.FTP_TLS.makepasv',return_value=('192.168.0.101',18898)):
   with self.assertRaises(transport.FTPSError):ftp.makepasv()
  with patch('ftplib.FTP_TLS.makepasv',return_value=('192.168.0.100',21)):
   with self.assertRaises(transport.FTPSError):ftp.makepasv()
  with patch('ftplib.FTP_TLS.makepasv',return_value=('192.168.0.100',18898)):
   self.assertEqual(ftp.makepasv(),('192.168.0.100',18898))
 def test_mlsd_only_offers_normal_files_with_verifiable_facts(self):
  class Fake:
   def retrlines(self,cmd,callback):
    for line in ['type=file;size=5;modify=20261115010101;unique=abc; a.txt',
                 'type=OS.unix=slink;size=5;modify=20261115010101;unique=bad; link.txt',
                 'type=file;size=5;modify=20261115010101;unique=evil\x01; bad.txt',
                 'type=file;size=5;modify=20261115010101; a2.txt',
                 'type=file;size=5;modify=20261115010101;unique=bad; ../escape.txt']:
     callback(line)
  import time
  result=transport._facts(Fake(),time.monotonic()+2);self.assertEqual([x['name'] for x in result],['a.txt'])
 def test_ftp_close_on_auth_error(self):
  class Fake:
   last=None
   def __init__(self,*a):Fake.last=self;self.closed=False
   def connect(self,*a,**kw):pass
   def auth(self):raise OSError('must not leak credentials')
   def close(self):self.closed=True
  with patch.dict(os.environ,{'GA_FTPS_USERNAME_TEST':'user','GA_FTPS_PASSWORD_TEST':'pass'}),patch.object(transport,'_PinnedFTP',Fake):
   with self.assertRaises(transport.FTPSError) as err:transport._open(GRANT)
   self.assertTrue(Fake.last.closed);self.assertNotIn('credentials',str(err.exception))
 def test_ambiguous_duplicate_name_rejects_whole_listing(self):
  class Fake:
   def retrlines(self,cmd,callback):
    callback('type=file;size=5;modify=20261115010101;unique=a; same.txt')
    callback('type=file;size=6;modify=20261115010101;unique=b; same.txt')
  import time
  with self.assertRaises(transport.FTPSError):transport._facts(Fake(),time.monotonic()+2)
 def test_changed_remote_metadata_prevents_retrieval(self):
  class Fake:
   def __init__(self):self.retrieved=False;self.closed=False
   def retrbinary(self,*a,**kw):self.retrieved=True
   def close(self):self.closed=True
  fake=Fake();expected={'name':'a.txt','size':5,'modified':'20261115010101','unique':'a'}
  with patch.object(transport,'_open',return_value=fake),patch.object(transport,'_facts',return_value=[{**expected,'size':6}]):
   with self.assertRaises(transport.FTPSError):transport.read_file(GRANT,expected)
  self.assertFalse(fake.retrieved);self.assertTrue(fake.closed)
 def test_cancel_submission_must_not_store_extra_secret_fields(self):
  from uuid import uuid4
  with self.assertRaises(HTTPException):api._canonical_submission('connect',{'request_id':str(uuid4()),'grant_id':'a'*64,'name':'x','password':'must-not-store'})
 def test_history_remains_recoverable_after_grant_revocation(self):
  p=SimpleNamespace(role='admin',workspace_id='workspace',user_id='admin')
  connection=SimpleNamespace(data={'grant_id':'g'})
  receipt=SimpleNamespace(data={'request_id':'r','state':'succeeded','kind':'listing',
       'submission':{'connection_id':'c'},'result':{'listing_id':'l','files':[]}})
  db=SimpleNamespace(scalars=lambda query:[receipt])
  with patch.object(api,'_get',return_value=connection),patch.object(api,'find_grant',side_effect=transport.FTPSError('revoked')):
   result=api._history('c',p,db,'listing','listings')
  self.assertFalse(result['source_available']);self.assertEqual(result['listings'][0]['request_id'],'r')
 def test_exact_listing_recovers_after_revoke_but_rejects_other_connection(self):
  from uuid import uuid4
  identity=str(uuid4());listing_id=uuid4();p=SimpleNamespace(role='admin',workspace_id='workspace',user_id='admin')
  connection=SimpleNamespace(data={'grant_id':'g'})
  receipt=SimpleNamespace(id=str(listing_id),data={'request_id':'r','state':'succeeded','kind':'listing',
       'submission':{'connection_id':identity},'result':{'listing_id':str(listing_id),'files':[]}})
  with patch.object(api,'_get',side_effect=[connection,receipt]),patch.object(api,'find_grant',side_effect=transport.FTPSError('revoked')):
   result=api.exact_listing(identity,listing_id,p,object())
  self.assertFalse(result['source_available']);self.assertEqual(result['listing']['request_id'],'r')
  with patch.object(api,'_get',side_effect=[connection,receipt]):
   with self.assertRaises(HTTPException) as failure:api.exact_listing(str(uuid4()),listing_id,p,object())
  self.assertEqual(failure.exception.status_code,404)
 def test_exact_connection_recovers_after_grant_revoke_and_is_workspace_scoped(self):
  from uuid import uuid4
  identity=uuid4();p=SimpleNamespace(role='admin',workspace_id='workspace',user_id='admin')
  row=SimpleNamespace(id=str(identity),version=1,data={'name':'saved','grant_id':'g','status':'active'})
  with patch.object(api,'_get',return_value=row),patch.object(api,'find_grant',side_effect=transport.FTPSError('revoked')):
   result=api.exact_connection(identity,p,object())
  self.assertFalse(result['source_available']);self.assertEqual(result['connection']['id'],str(identity))
  db=SimpleNamespace(scalar=lambda query:None)
  with self.assertRaises(HTTPException) as failure:api.exact_connection(identity,p,db)
  self.assertEqual(failure.exception.status_code,404)
 def test_import_uses_identical_lock_to_normal_material_upload(self):
  import hashlib
  raw=b'test file';sha=hashlib.sha256(raw).hexdigest();p=SimpleNamespace(workspace_id='workspace')
  existing=SimpleNamespace(evidence_id='evidence');seen=[]
  class FakeDB:
   def get_bind(self):return SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
   def execute(self,query,params):seen.append(params['key'])
   def scalar(self,query):return existing
  with patch.object(api,'evidence_bytes',return_value=(object(),raw)):
   result,reused=api._material(FakeDB(),p,'a.txt',raw,sha)
  expected=int.from_bytes(hashlib.sha256(('workspace'+sha).encode()).digest()[:8],'big',signed=True)
  self.assertEqual(seen,[expected]);self.assertIs(result,existing);self.assertTrue(reused)
 def test_revoked_identity_blocks_commit(self):
  p=SimpleNamespace(workspace_id='w',user_id='u',session_id='s')
  db=SimpleNamespace(execute=lambda query:SimpleNamespace(first=lambda:None))
  with self.assertRaises(HTTPException) as failure:api._current_admin(db,p)
  self.assertEqual(failure.exception.status_code,403)

if __name__=='__main__':unittest.main()
