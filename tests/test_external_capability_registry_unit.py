import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlalchemy.exc import IntegrityError

from generic_agents.contracts import Manifest, digest
from generic_agents import external_capability_registry as api


def manifest(**changes):
    value = {'kind': 'tool', 'namespace': 'examples', 'name': 'weather', 'version': '1.0.0',
             'owner': '测试', 'description': '只读天气查询',
             'execution': {'adapter': 'http', 'endpoint': 'https://example.org/api', 'side_effect': False},
             'allowed_agent_roles': ['business'], 'data_policy': {'external_allowed': True}}
    value.update(changes)
    return Manifest.model_validate(value)


class DB:
    def __init__(self): self.commits = 0
    def begin_nested(self): return nullcontext()
    def commit(self): self.commits += 1


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.p = SimpleNamespace(role='admin', workspace_id='test-workspace', user_id='user')
        self.body = manifest()
        self.row = SimpleNamespace(kind='tool', content_hash=digest(self.body.model_dump(mode='json')),
                                   data=self.body.model_dump(mode='json'))

    def test_retry_same_content_reuses_original_without_second_write(self):
        db, response = DB(), Response()
        with patch.object(api, '_row', return_value=self.row), patch.object(api, 'as_dict', return_value={'id':'original'}), patch.object(api, 'register') as create:
            self.assertEqual(api.create(self.body, response, self.p, db), {'id':'original'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.commits, 0)
        create.assert_not_called()

    def test_identity_conflict_does_not_overwrite_original(self):
        db = DB()
        with patch.object(api, '_row', return_value=self.row):
            with self.assertRaises(HTTPException) as error:
                api.create(manifest(description='不同内容'), Response(), self.p, db)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(db.commits, 0)

    def test_unique_race_reloads_same_identity(self):
        db, response = DB(), Response()
        with patch.object(api, '_row', side_effect=[None, self.row]), patch.object(api, 'register', side_effect=IntegrityError('insert', {}, Exception())), patch.object(api, 'as_dict', return_value={'id':'winner'}):
            self.assertEqual(api.create(self.body, response, self.p, db), {'id':'winner'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.commits, 0)

    def test_lookup_scope_uses_principal_workspace(self):
        class Capture:
            def scalar(self, query):
                compiled = query.compile()
                self.params = compiled.params
                return None
        db = Capture()
        self.assertIsNone(api._row(db, self.p, 'examples', 'weather', '1.0.0'))
        self.assertIn('test-workspace', db.params.values())

    def test_viewer_cannot_retry_create(self):
        with patch.object(api, '_row', return_value=self.row):
            with self.assertRaises(HTTPException) as error:
                api.create(self.body, Response(), SimpleNamespace(role='viewer', workspace_id='test-workspace'), DB())
        self.assertEqual(error.exception.status_code, 403)

    def test_only_external_adapters_are_accepted(self):
        with self.assertRaises(HTTPException) as error:
            api.create(manifest(execution={'adapter':'builtin'}), Response(), self.p, DB())
        self.assertEqual(error.exception.status_code, 422)

    def test_cancel_tombstone_blocks_delayed_create(self):
        class ReceiptDB(DB):
            def __init__(self): super().__init__(); self.rows = []
            def add(self, row): self.rows.append(row)
        db = ReceiptDB()
        request_id = 'test-request-12345678'
        body = api.RegistrationAttempt(request_id=request_id, manifest=self.body)
        with patch.object(api, '_attempt_lock') as lock, patch.object(api, '_attempt', side_effect=lambda *args: db.rows[0] if db.rows else None), patch.object(api, 'register') as create:
            cancelled = api.cancel_attempt(request_id, self.p, db)
            self.assertEqual(cancelled['state'], 'cancelled')
            delayed = api.submit_attempt(body, self.p, db)
            self.assertEqual(delayed['state'], 'cancelled')
            create.assert_not_called()
            self.assertEqual(lock.call_count, 2)
            self.assertEqual(db.commits, 1)

    def test_cancellation_cannot_adopt_another_actors_receipt(self):
        receipt = SimpleNamespace(kind='external_registration_attempt', status='internal',
                                  data={'request_id':'test-request-12345678', 'actor_id':'someone-else', 'state':'cancelled'})
        with patch.object(api, '_attempt_lock'), patch.object(api, '_attempt', return_value=receipt):
            with self.assertRaises(HTTPException) as error:
                api.cancel_attempt('test-request-12345678', self.p, DB())
        self.assertEqual(error.exception.status_code, 409)

    def test_cancelled_receipt_is_scoped_and_does_not_allow_bad_id(self):
        with self.assertRaises(HTTPException) as error:
            api.cancel_attempt('../bad', self.p, DB())
        self.assertEqual(error.exception.status_code, 422)


if __name__ == '__main__': unittest.main()
