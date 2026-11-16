import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from generic_agents import map_workspace_grants as grants, wms


class FakeDB:
    def __init__(self, returned=()):
        self.returned=list(returned);self.added=[];self.committed=False
    def get_bind(self):return SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
    def scalar(self, query):return self.returned.pop(0) if self.returned else None
    def add(self, row):
        self.added.append(row)
        if not getattr(row,'id',None):row.id='new-record'
        if not getattr(row,'version',None):row.version=1
    def flush(self):pass
    def commit(self):self.committed=True


class MapWorkspaceGrantsTests(unittest.TestCase):
    def setUp(self):self.p=SimpleNamespace(workspace_id='workspace-a',user_id='administrator')
    def body(self, **overrides):
        data={'request_id':'operation-123456789','endpoint':'https://8.8.8.8/maps','secret_ref':'','allowed':True,'version':0}
        data.update(overrides);return grants.Change(**data)
    def test_environment_ceiling_and_literal_ip_are_mandatory(self):
        conf=SimpleNamespace(wms_allowed_endpoints='https://8.8.8.8/maps,http://192.168.1.5/wms,http://127.0.0.1/internal,https://example.com/maps', wms_self_service_access='{"workspace-a":["https://8.8.8.8/maps","http://192.168.1.5/wms","http://127.0.0.1/internal","https://example.com/maps"]}')
        with patch.object(grants,'settings',return_value=conf):
            self.assertEqual(grants.eligible('https://8.8.8.8/maps','workspace-a'),'https://8.8.8.8/maps')
            self.assertEqual(grants.eligible('http://192.168.1.5/wms','workspace-a'),'http://192.168.1.5/wms')
            for url in ['https://8.8.4.4/maps','http://127.0.0.1/internal','https://example.com/maps','http://169.254.169.254/x']:
                with self.subTest(url=url),self.assertRaises(HTTPException):grants.eligible(url,'workspace-a')
            with self.assertRaises(HTTPException):grants.eligible('https://8.8.8.8/maps','workspace-b')
    def test_legacy_grant_can_be_revoked_without_erasing_original(self):
        db=FakeDB([None,None]);body=self.body(allowed=False);legacy=[{'endpoint':body.endpoint,'secret_ref':''}]
        with patch.object(grants,'require'),patch.object(grants,'_legacy',return_value=legacy),patch.object(grants,'audit'):
            result=grants.change(body,self.p,db)
        self.assertFalse(result['allowed']);self.assertTrue(result['legacy_grant']);self.assertTrue(db.committed)
        self.assertEqual(db.added[0].data['actor_id'],self.p.user_id)
        self.assertEqual(db.added[1].kind,grants.RECEIPT)
    def test_nonlegacy_secret_cannot_be_adopted(self):
        body=self.body(secret_ref='GA_CONNECTOR_SECRET_NOT_MINE');db=FakeDB([None,None])
        with patch.object(grants,'require'),patch.object(grants,'_legacy',return_value=[]),patch.object(grants,'eligible',return_value=body.endpoint),self.assertRaises(HTTPException) as e:
            grants.change(body,self.p,db)
        self.assertEqual(e.exception.status_code,403);self.assertFalse(db.committed)
    def test_nonlegacy_grant_is_usable_only_under_same_workspace_ceiling(self):
        target={'workspace_id':'workspace-a','endpoint':'https://8.8.8.8/maps','secret_ref':''}
        conf=SimpleNamespace(wms_allowed_endpoints=target['endpoint'],wms_self_service_access='{"workspace-a":["https://8.8.8.8/maps"]}')
        with patch.object(grants,'decision',return_value=True),patch.object(grants,'settings',return_value=conf),patch.object(wms,'grants',return_value=[]):
            self.assertIsNone(wms.authorize(target,'workspace-a',object()))
            target['workspace_id']='workspace-b'
            with self.assertRaises(HTTPException):wms.authorize(target,'workspace-b',object())
    def test_receipt_replay_and_conflicting_operation_id(self):
        body=self.body();original={'endpoint':body.endpoint,'allowed':True,'version':1}
        receipt=SimpleNamespace(kind=grants.RECEIPT,status='internal',data={'actor_id':self.p.user_id,'submission_hash':grants.digest(body.model_dump()),'submission':body.model_dump(),'result':original})
        with patch.object(grants,'require'):
            self.assertEqual(grants.change(body,self.p,FakeDB([receipt])),original)
            with self.assertRaises(HTTPException) as e:grants.change(self.body(allowed=False),self.p,FakeDB([receipt]))
            self.assertEqual(e.exception.status_code,409)
    def test_cancel_blocks_late_request_and_completed_request_returns_its_submission(self):
        body=self.body();db=FakeDB([None])
        with patch.object(grants,'require'),patch.object(grants,'audit'):
            stopped=grants.cancel_request(body.request_id,self.p,db)
            self.assertEqual(stopped['state'],'cancelled');receipt=db.added[0]
            with self.assertRaises(HTTPException):grants.change(body,self.p,FakeDB([receipt]))
            again=grants.cancel_request(body.request_id,self.p,FakeDB([receipt]));self.assertEqual(again['state'],'cancelled')
            other=SimpleNamespace(workspace_id=self.p.workspace_id,user_id='another-admin')
            with self.assertRaises(HTTPException):grants.cancel_request(body.request_id,other,FakeDB([receipt]))
            submission=body.model_dump();out={'request_id':body.request_id,'endpoint':body.endpoint,'secret_ref':'','allowed':True,'version':1}
            applied=SimpleNamespace(kind=grants.RECEIPT,status='internal',data={'actor_id':self.p.user_id,'submission':submission,'submission_hash':grants.digest(submission),'result':out})
            recovered=grants.cancel_request(body.request_id,self.p,FakeDB([applied]))
            self.assertEqual(recovered['state'],'applied');self.assertEqual(recovered['submission'],submission);self.assertEqual(recovered['result'],out)
    def test_revocation_overrides_legacy_and_env_secret_removal_wins(self):
        target={'workspace_id':self.p.workspace_id,'endpoint':'https://8.8.8.8/maps','secret_ref':''}
        with patch.object(grants,'decision',return_value=False),patch.object(wms,'grants',return_value=[{'endpoint':target['endpoint'],'secret_ref':''}]):
            with self.assertRaises(HTTPException):wms.authorize(target,self.p.workspace_id,object())
        target['secret_ref']='GA_CONNECTOR_SECRET_REMOVED'
        with patch.object(grants,'decision',return_value=True),patch.object(wms,'grants',return_value=[]):
            with self.assertRaises(HTTPException):wms.authorize(target,self.p.workspace_id,object())
        target['secret_ref']=''
        with patch.object(grants,'decision',return_value=True),patch.object(wms,'grants',return_value=[]),patch.object(grants,'eligible',side_effect=HTTPException(422,'environment removed')):
            with self.assertRaises(HTTPException):wms.authorize(target,self.p.workspace_id,object())
    def test_cross_workspace_and_corrupt_record_fail_closed(self):
        with self.assertRaises(HTTPException):wms.authorize({'workspace_id':'workspace-b'},self.p.workspace_id)
        bad=SimpleNamespace(kind='unexpected',status='internal',data={'endpoint':'x','secret_ref':'','allowed':True})
        with self.assertRaises(HTTPException):grants.decision(FakeDB([bad]),self.p.workspace_id,'x','')


if __name__ == '__main__':unittest.main()
