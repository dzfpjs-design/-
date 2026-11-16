"""Fast isolated contract checks; integration coverage must still use PostgreSQL."""
import unittest
import struct
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.responses import Response
from generic_agents import instance_observation_catalog as catalog
from generic_agents.contracts import digest


class CatalogBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.resource = {'id': 'wms:one', 'category': 'maps', 'protocol': 'WMS', 'name': '地图',
                         'backend_source_proof': {'manifest_hash': 'frozen'}}
        self.release = SimpleNamespace(data={'snapshot': {'resources': [self.resource]}})
        self.active = patch.object(catalog, 'active', return_value=(SimpleNamespace(data={'enabled_business_ids': []}), self.release))
        self.active.start()
        self.addCleanup(self.active.stop)
        access=patch('generic_agents.map_access.can_read',return_value=True)
        access.start()
        self.addCleanup(access.stop)

    def test_valid_saved_map_and_unrelated_map_denied(self):
        with patch.object(catalog, 'verified', return_value=[{'scene_id': 'scene-1', 'name': '地图'}]), \
             patch('generic_agents.geo_api.preview_internal', return_value=Response(b'png', media_type='image/png')):
            result = catalog.catalog('instance', None, None)['sources'][0]
            self.assertEqual(result['status'], 'available')
            self.assertEqual(result['observations'][0]['captured_at'], None)
            self.assertIn('/sources/wms%3Aone/observations/scene-1', result['observations'][0]['url'])
            self.assertEqual(catalog.observation('instance', 'wms:one', 'scene-1', None, None).body, b'png')
            with self.assertRaises(HTTPException) as error:
                catalog.observation('instance', 'wms:one', 'other', None, None)
            self.assertEqual(error.exception.status_code, 404)

    def test_missing_proof_and_disabled_are_unavailable(self):
        for change in ({'backend_source_proof': None}, {'status': 'disabled'}):
            with self.subTest(change=change):
                original = dict(self.resource)
                self.resource.update(change)
                result = catalog.catalog('instance', None, None)['sources'][0]
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['observations'], [])
                self.resource.clear(); self.resource.update(original)

    def test_changed_proof_is_denied(self):
        with patch('generic_agents.wms_source.resource_proof', return_value={'manifest_hash': 'new'}):
            with self.assertRaises(HTTPException) as error:
                catalog.verified(None, None, self.resource)
            self.assertEqual(error.exception.status_code, 409)

    def test_duplicate_resource_id_and_missing_resource_denied(self):
        self.release.data['snapshot']['resources'].append(dict(self.resource))
        self.assertTrue(all(x['status'] == 'unavailable' for x in catalog.catalog('instance', None, None)['sources']))
        with self.assertRaises(HTTPException) as error:
            catalog.one_resource(None, None, 'instance', 'wms:one')
        self.assertEqual(error.exception.status_code, 404)

    def test_active_authorization_failure_propagates(self):
        with patch.object(catalog, 'active', side_effect=HTTPException(403, 'denied')):
            with self.assertRaises(HTTPException) as error:
                catalog.catalog('foreign', None, None)
            self.assertEqual(error.exception.status_code, 403)

    def test_video_proof_requires_registered_channel_and_frozen_manifest(self):
        value = {'execution': {'adapter': 'media_source', 'channel_id': 'channel'}}
        manifest = SimpleNamespace(model_dump=lambda **_: value)
        capability = SimpleNamespace(id='cap', data=value, content_hash=digest(value))
        channel = SimpleNamespace(id='channel', data={'capability_id': 'cap'})
        resource = {'details': {'capability_id': 'cap', 'channel_id': 'channel'}}
        with patch.object(catalog, 'published', return_value=capability), \
             patch('generic_agents.media.validated_channel', return_value=(channel, object())), \
             patch('generic_agents.media.channel_manifest', return_value=manifest):
            proof = catalog.media_resource_proof(None, None, resource)
            self.assertEqual(proof['manifest_hash'], capability.content_hash)
            self.assertFalse(proof['streaming_ready'])
            channel.data['capability_id'] = 'other'
            with self.assertRaises(HTTPException):
                catalog.media_resource_proof(None, None, resource)

    def test_video_observation_requires_the_same_saved_evidence(self):
        self.resource.update(category='video', protocol='RTSP / H.264')
        rows = [{'session_id': 's', 'video_run_id': 'r', 'original_evidence_id': 'e', 'sha256': 'hash'}]
        run = SimpleNamespace(evidence_id='other', data={'input_sha256': 'hash'})
        with patch.object(catalog, 'verified', return_value=rows), patch.object(catalog, 'scoped', return_value=run):
            with self.assertRaises(HTTPException) as error:
                catalog.observation('instance', 'wms:one', 's', None, None)
            self.assertEqual(error.exception.status_code, 409)

    def test_live_map_uses_published_scope_and_no_user_supplied_endpoint(self):
        self.resource['backend_source_proof']['capability_id'] = 'cap'
        scope = {'layer': 'satellite', 'style': '', 'crs': 'CRS:84', 'bbox': [117, 24, 118, 25]}
        connection = SimpleNamespace(id='conn', status='active', version=1,
                                     data={'configuration': {'version': '1.3.0', 'endpoint':'https://example.test/wms'}})
        cap = SimpleNamespace(id='cap', version=1, data={'test':'cap'})
        cap.content_hash = digest(cap.data)
        png = b'\x89PNG\r\n\x1a\n' + b'\0\0\0\rIHDR' + struct.pack('>II', 1024, 768)
        with patch.object(catalog, 'verified', return_value=[]) as full_check, \
             patch.object(catalog, 'ensure_current_principal') as principal_check, \
             patch('generic_agents.wms_source.current_source', return_value=(cap, connection, scope)), \
             patch.object(catalog, 'scoped', return_value=connection), \
             patch.object(catalog, 'published', return_value=cap), \
             patch('generic_agents.wms.authorize'), \
             patch('generic_agents.config.settings', return_value=SimpleNamespace(wms_allowed_endpoints='https://example.test/wms')), \
             patch('generic_agents.wms.fetch', return_value=(png, 'image/png')) as fetch:
            result = catalog.catalog('instance', None, None)['sources'][0]
            self.assertTrue(result['live_map_ready'])
            full_check.reset_mock()
            def stream(_config, _params, _limit, guard):
                guard(); guard(); guard()
                return png, 'image/png'
            fetch.side_effect = stream
            response = catalog.live_wms('instance', 'wms:one', SimpleNamespace(workspace_id='w'), SimpleNamespace(expire_all=lambda: None))
            self.assertEqual(response.body, png)
            self.assertEqual(full_check.call_count, 2)
            self.assertEqual(principal_check.call_count, 6)
            params = fetch.call_args.args[1]
            self.assertEqual(params['BBOX'], '117.0,24.0,118.0,25.0')
            self.assertEqual(params['LAYERS'], 'satellite')
            self.assertEqual(params['WIDTH'], '1024')
            self.assertEqual(response.headers['x-image-capture-time'], 'unknown')

    def test_live_map_denies_wrong_source_and_invalid_remote_image(self):
        self.resource.update(category='video', protocol='RTSP / H.264')
        with self.assertRaises(HTTPException) as error:
            catalog.live_wms('instance', 'wms:one', None, None)
        self.assertEqual(error.exception.status_code, 404)
        self.resource.update(category='maps', protocol='WMS')
        self.resource['backend_source_proof']['capability_id'] = 'cap'
        connection = SimpleNamespace(id='conn', status='active', version=1,
                                     data={'configuration': {'version': '1.3.0', 'endpoint':'https://example.test/wms'}})
        cap = SimpleNamespace(id='cap', version=1, data={'test':'cap'})
        cap.content_hash = digest(cap.data)
        scope = {'layer': 'satellite', 'style': '', 'crs': 'CRS:84', 'bbox': [117, 24, 118, 25]}
        with patch.object(catalog, 'verified', return_value=[]), \
             patch.object(catalog, 'ensure_current_principal'), \
             patch('generic_agents.wms_source.current_source', return_value=(cap, connection, scope)), \
             patch.object(catalog, 'scoped', return_value=connection), \
             patch.object(catalog, 'published', return_value=cap), \
             patch('generic_agents.wms.authorize'), \
             patch('generic_agents.config.settings', return_value=SimpleNamespace(wms_allowed_endpoints='https://example.test/wms')), \
             patch('generic_agents.wms.fetch', return_value=(b'bad image', 'image/png')):
            with self.assertRaises(HTTPException) as error:
                catalog.live_wms('instance', 'wms:one', SimpleNamespace(workspace_id='w'), SimpleNamespace(expire_all=lambda: None))
            self.assertEqual(error.exception.status_code, 502)

    def test_live_map_stops_when_source_paused_during_response(self):
        self.resource['backend_source_proof']['capability_id'] = 'cap'
        scope = {'layer': 'satellite', 'style': '', 'crs': 'CRS:84', 'bbox': [117, 24, 118, 25]}
        connection = SimpleNamespace(id='conn', status='active', version=1,
                                     data={'configuration': {'version':'1.3.0', 'endpoint':'https://example.test/wms'}})
        cap = SimpleNamespace(id='cap', version=1, data={'test':'cap'})
        cap.content_hash = digest(cap.data)
        def stream(_config, _params, _limit, guard):
            guard()
            connection.status = 'paused'
            guard()
            raise AssertionError('must stop before returning bytes')
        with patch.object(catalog, 'verified', return_value=[]), \
             patch.object(catalog, 'ensure_current_principal'), \
             patch('generic_agents.wms_source.current_source', return_value=(cap, connection, scope)), \
             patch.object(catalog, 'scoped', return_value=connection), \
             patch.object(catalog, 'published', return_value=cap), \
             patch('generic_agents.wms.authorize'), \
             patch('generic_agents.config.settings', return_value=SimpleNamespace(wms_allowed_endpoints='https://example.test/wms')), \
             patch('generic_agents.wms.fetch', side_effect=stream):
            with self.assertRaises(HTTPException) as error:
                catalog.live_wms('instance', 'wms:one', SimpleNamespace(workspace_id='w'), SimpleNamespace(expire_all=lambda: None))
            self.assertEqual(error.exception.status_code, 409)

    def test_live_map_stops_when_workspace_authorization_revoked(self):
        self.resource['backend_source_proof']['capability_id'] = 'cap'
        scope = {'layer': 'satellite', 'style': '', 'crs': 'CRS:84', 'bbox': [117, 24, 118, 25]}
        connection = SimpleNamespace(id='conn', status='active', version=1,
                                     data={'configuration': {'version':'1.3.0', 'endpoint':'https://example.test/wms'}})
        cap = SimpleNamespace(id='cap', version=1, data={'test':'cap'})
        cap.content_hash = digest(cap.data)
        def stream(_config, _params, _limit, guard):
            guard(); guard()
            raise AssertionError('revoked authorization must stop response')
        with patch.object(catalog, 'verified', return_value=[]), \
             patch.object(catalog, 'ensure_current_principal'), \
             patch('generic_agents.wms_source.current_source', return_value=(cap, connection, scope)), \
             patch.object(catalog, 'scoped', return_value=connection), \
             patch.object(catalog, 'published', return_value=cap), \
             patch('generic_agents.wms.authorize', side_effect=[None, None, HTTPException(403, 'revoked')]), \
             patch('generic_agents.config.settings', return_value=SimpleNamespace(wms_allowed_endpoints='https://example.test/wms')), \
             patch('generic_agents.wms.fetch', side_effect=stream):
            with self.assertRaises(HTTPException) as error:
                catalog.live_wms('instance', 'wms:one', SimpleNamespace(workspace_id='w'), SimpleNamespace(expire_all=lambda: None))
            self.assertEqual(error.exception.status_code, 403)

    def test_current_principal_rejects_revocation_expiration_and_membership_changes(self):
        principal = SimpleNamespace(user_id='u', workspace_id='w', session_id='s', role='viewer')
        def run_case(change, expected):
            auth = SimpleNamespace(user_id='u', revoked=False, expires_at=catalog.now()+timedelta(minutes=5))
            user = SimpleNamespace(active=True)
            member = SimpleNamespace(role='viewer')
            auth, user, member = change(auth, user, member)
            class FakeDB:
                def get(self, model, _id):
                    return auth if model is catalog.AuthSession else user
                def scalar(self, _query):
                    return member
            if expected:
                catalog.ensure_current_principal(FakeDB(), principal)
            else:
                with self.assertRaises(HTTPException) as error:
                    catalog.ensure_current_principal(FakeDB(), principal)
                self.assertEqual(error.exception.status_code, 403)
        run_case(lambda a,u,m:(a,u,m), True)
        for change in (
            lambda a,u,m:(None,u,m),
            lambda a,u,m:(SimpleNamespace(user_id='u', revoked=True, expires_at=a.expires_at),u,m),
            lambda a,u,m:(SimpleNamespace(user_id='u', revoked=False, expires_at=catalog.now()-timedelta(seconds=1)),u,m),
            lambda a,u,m:(SimpleNamespace(user_id='other', revoked=False, expires_at=a.expires_at),u,m),
            lambda a,u,m:(a,None,m),
            lambda a,u,m:(a,SimpleNamespace(active=False),m),
            lambda a,u,m:(a,u,None),
            lambda a,u,m:(a,u,SimpleNamespace(role='operator')),
        ):
            run_case(change, False)

    def test_live_map_checks_revocation_before_midstream_and_after_final_proof(self):
        self.resource['backend_source_proof']['capability_id'] = 'cap'
        scope = {'layer': 'satellite', 'style': '', 'crs': 'CRS:84', 'bbox': [117, 24, 118, 25]}
        connection = SimpleNamespace(id='conn', status='active', version=1,
                                     data={'configuration': {'version':'1.3.0', 'endpoint':'https://example.test/wms'}})
        cap = SimpleNamespace(id='cap', version=1, data={'test':'cap'})
        cap.content_hash = digest(cap.data)
        principal = SimpleNamespace(workspace_id='w')
        db = SimpleNamespace(expire_all=lambda: None)
        png = b'\x89PNG\r\n\x1a\n' + b'\0\0\0\rIHDR' + struct.pack('>II', 1024, 768)
        for failure_stage in ('before', 'middle', 'final'):
            with self.subTest(stage=failure_stage):
                count = {'checks': 0, 'full': 0}
                def check(_db, _principal):
                    count['checks'] += 1
                    if (failure_stage == 'before' and count['checks'] == 1
                            or failure_stage == 'middle' and count['checks'] == 3
                            or failure_stage == 'final' and count['full'] == 2):
                        raise HTTPException(403, 'session revoked')
                def full(*_args):
                    count['full'] += 1
                    return []
                def stream(_config, _params, _limit, guard):
                    guard(); guard()
                    return png, 'image/png'
                with patch.object(catalog, 'verified', side_effect=full), \
                     patch.object(catalog, 'ensure_current_principal', side_effect=check), \
                     patch('generic_agents.wms_source.current_source', return_value=(cap, connection, scope)), \
                     patch.object(catalog, 'scoped', return_value=connection), \
                     patch.object(catalog, 'published', return_value=cap), \
                     patch('generic_agents.wms.authorize'), \
                     patch('generic_agents.config.settings', return_value=SimpleNamespace(wms_allowed_endpoints='https://example.test/wms')), \
                     patch('generic_agents.wms.fetch', side_effect=stream):
                    with self.assertRaises(HTTPException) as error:
                        catalog.live_wms('instance', 'wms:one', principal, db)
                    self.assertEqual(error.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
