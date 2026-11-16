"""Pure tests for registration recovery; PostgreSQL concurrency needs integration."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response

from generic_agents import media_gateway_registration as reg
from generic_agents.contracts import digest
from generic_agents.models import Job, MediaGateway


class FakeDB:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.locks = 0

    def execute(self, _statement, _params):
        self.locks += 1

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            if isinstance(row, MediaGateway) and not row.id:
                row.id = 'test-gateway'

    def commit(self):
        self.commits += 1


class GatewayRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.principal = SimpleNamespace(workspace_id='test-workspace', user_id='test-actor', role='admin')
        self.body = reg.GatewayRegistration(request_id='request_123456789', name='  测试网关  ',
            api_origin='http://127.0.0.1:19997/', rtsp_origin='rtsp://127.0.0.1:18554/')
        self.canonical = {'name':'测试网关','api_origin':'http://127.0.0.1:19997',
                          'rtsp_origin':'rtsp://127.0.0.1:18554'}
        self.record = SimpleNamespace(kind=reg.KIND, status='internal', data={
            'request_id':self.body.request_id,'gateway_id':'gateway-1',
            'submission_hash':digest(self.canonical)})
        self.gateway = SimpleNamespace(id='gateway-1', data=dict(self.canonical))

    def mocks(self, receipt=None, gateway=None):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(reg, 'require'))
        stack.enter_context(patch.object(reg, '_receipt', return_value=receipt))
        stack.enter_context(patch.object(reg, 'origin', side_effect=lambda value, _kind:value))
        stack.enter_context(patch.object(reg, 'audit'))
        stack.enter_context(patch.object(reg, 'scoped', return_value=gateway or self.gateway))
        stack.enter_context(patch.object(reg, 'view', side_effect=lambda row:{'id':row.id, **row.data}))
        return stack

    def test_new_registration_commits_receipt_and_gateway_together(self):
        db=FakeDB();response=Response()
        with self.mocks():
            result=reg.create(self.body,response,self.principal,db)
        self.assertEqual(response.status_code,201)
        self.assertEqual(db.commits,1)
        self.assertEqual(db.locks,1)
        self.assertEqual(len([x for x in db.added if isinstance(x,MediaGateway)]),1)
        marker=next(x for x in db.added if isinstance(x,Job))
        self.assertEqual(marker.status,'internal')
        self.assertEqual(marker.data['gateway_id'],result['id'])
        self.assertTrue(result['registration_matches_current'])

    def test_retry_returns_existing_without_creating_another(self):
        db=FakeDB();response=Response()
        with self.mocks(self.record):
            result=reg.create(self.body,response,self.principal,db)
        self.assertEqual(response.status_code,200)
        self.assertEqual(result['id'],'gateway-1')
        self.assertEqual(db.added,[])
        self.assertEqual(db.commits,0)

    def test_changed_submission_conflicts_without_mutation(self):
        db=FakeDB();response=Response()
        changed=self.body.model_copy(update={'name':'不同网关'})
        with self.mocks(self.record),self.assertRaises(HTTPException) as error:
            reg.create(changed,response,self.principal,db)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(db.added,[])
        self.assertEqual(db.commits,0)

    def test_lookup_reports_gateway_modified_after_registration(self):
        changed=SimpleNamespace(id='gateway-1',data={**self.canonical,'name':'后来修改'})
        with self.mocks(self.record,changed):
            result=reg.lookup(self.body.request_id,self.principal,FakeDB())
        self.assertFalse(result['registration_matches_current'])

    def test_missing_or_corrupt_receipt_does_not_recreate(self):
        with self.mocks(),self.assertRaises(HTTPException) as missing:
            reg.lookup(self.body.request_id,self.principal,FakeDB())
        self.assertEqual(missing.exception.status_code,404)
        corrupt=SimpleNamespace(kind=reg.KIND,status='internal',data={'request_id':self.body.request_id})
        with self.mocks(corrupt),self.assertRaises(HTTPException) as invalid:
            reg.lookup(self.body.request_id,self.principal,FakeDB())
        self.assertEqual(invalid.exception.status_code,409)

    def test_admin_required_for_both_actions(self):
        for action in ('create','lookup'):
            with patch.object(reg,'require',side_effect=HTTPException(403,'denied')):
                with self.assertRaises(HTTPException) as error:
                    if action=='create':reg.create(self.body,Response(),self.principal,FakeDB())
                    else:reg.lookup(self.body.request_id,self.principal,FakeDB())
            self.assertEqual(error.exception.status_code,403)


if __name__=='__main__':
    unittest.main()
