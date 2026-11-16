import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from generic_agents import onvif_camera_enrolment_api as api

class DB:
    def __init__(self):self.row=None;self.commits=0
    def add(self,row):self.row=row
    def flush(self):
        if self.row is not None and not self.row.id:self.row.id='fixture-job'
        if self.row is not None and self.row.version is None:self.row.version=1
    def commit(self):self.commits+=1
    def rollback(self):pass

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.p=SimpleNamespace(workspace_id='workspace',user_id='admin',role='admin')
        self.body=api.Enrollment(request_id='fixture-request-1234',scan_id='scan',candidate_id='a'*32,
            gateway_id='gateway',gateway_version=1,credential_ref='GA_ONVIF_CAMERA_SECRET_FIXTURE_TEST',
            confirm_onvif_read=True,confirm_gateway_write_and_credential_storage=True)
        self.db=DB()

    def test_cancel_before_delayed_submit_is_durable_and_no_network(self):
        with patch.object(api,'_lock'),patch.object(api,'_receipt',side_effect=lambda *_:self.db.row),patch.object(api,'audit'),patch.object(api,'stream_uri') as network:
            self.assertEqual(api.cancel(self.body.request_id,self.body,self.p,self.db)['state'],'cancelled')
            self.assertEqual(api.enroll(self.body,self.p,self.db)['state'],'cancelled')
            self.assertEqual(self.db.commits,1);network.assert_not_called()

    def test_cancel_cannot_disguise_a_completed_gateway_write(self):
        data={**self.body.model_dump(),'submission_hash':api.digest(self.body.model_dump()),'gateway_path':'onvif_'+'a'*32,'state':'configured'}
        self.db.row=SimpleNamespace(id='fixture-job',kind=api.KIND,status='internal',data=data,version=1)
        with patch.object(api,'_lock'),patch.object(api,'_receipt',return_value=self.db.row):
            self.assertEqual(api.cancel(self.body.request_id,self.body,self.p,self.db)['state'],'configured')
            self.assertEqual(self.db.commits,0)

    def test_same_request_with_different_body_is_rejected(self):
        data={**self.body.model_dump(),'submission_hash':api.digest(self.body.model_dump()),'gateway_path':'onvif_'+'a'*32,'state':'pending'}
        self.db.row=SimpleNamespace(kind=api.KIND,status='internal',data=data,version=1)
        other=self.body.model_copy(update={'gateway_id':'other'})
        with patch.object(api,'_lock'),patch.object(api,'_receipt',return_value=self.db.row):
            with self.assertRaises(HTTPException) as error:api.cancel(other.request_id,other,self.p,self.db)
            self.assertEqual(error.exception.status_code,409)

    def test_view_reports_gateway_missing_without_sending_credentials(self):
        data={**self.body.model_dump(),'gateway_path':'onvif_'+'a'*32,'gateway_generation':1,'state':'configured'}
        self.db.row=SimpleNamespace(kind=api.KIND,status='internal',data=data,version=1)
        with patch.object(api,'_receipt',return_value=self.db.row),patch.object(api,'_inputs',return_value=({'source_ip':'192.168.45.32','service_address':'https://192.168.45.32/onvif'},None,'http://127.0.0.1:19997')),patch.object(api,'stream_uri',return_value='rtsp://192.168.45.32/private?token=sensitive'),patch.object(api,'gateway_state',return_value='missing'),patch.object(api,'_gateway_auth',return_value={}):
            value=api.lookup(self.body.request_id,self.p,self.db)
            self.assertEqual(value['gateway_state'],'missing')
            self.assertNotIn('sensitive',str(value));self.assertNotIn('credential_ref',value)

    def test_pending_is_committed_before_network_and_secrets_are_never_saved(self):
        gateway=SimpleNamespace(id='gateway',data={'generation':1})
        device={'service_address':'https://192.168.45.32/onvif','source_ip':'192.168.45.32'}
        def get_stream(*_):
            self.assertEqual(self.db.commits,1,'pending receipt must precede any device request')
            return 'rtsp://192.168.45.32/live?token=fixture-secret'
        with patch.object(api,'_lock'),patch.object(api,'_receipt',side_effect=lambda *_:self.db.row),patch.object(api,'audit'),patch.object(api,'_inputs',return_value=(device,gateway,'http://127.0.0.1:19997')),patch.object(api,'stream_uri',side_effect=get_stream),patch.object(api,'_gateway_auth',return_value={}),patch.object(api,'configure_gateway',return_value='created'):
            result=api.enroll(self.body,self.p,self.db)
            self.assertEqual(result['state'],'configured');self.assertEqual(self.db.commits,3)
            self.assertNotIn('fixture-secret',str(self.db.row.data));self.assertNotIn('rtsp://',str(self.db.row.data))

    def test_lost_gateway_write_cannot_be_cancelled_on_missing_path(self):
        data={**self.body.model_dump(),'submission_hash':api.digest(self.body.model_dump()),'gateway_path':'onvif_'+'a'*32,
              'gateway_generation':1,'gateway_write_started':True,'state':'pending'}
        self.db.row=SimpleNamespace(kind=api.KIND,status='internal',data=data,version=1)
        device={'service_address':'https://192.168.45.32/onvif','source_ip':'192.168.45.32'}
        with patch.object(api,'_lock'),patch.object(api,'_receipt',return_value=self.db.row),patch.object(api,'_inputs',return_value=(device,None,'http://127.0.0.1:19997')),patch.object(api,'stream_uri',return_value='rtsp://192.168.45.32/live'),patch.object(api,'gateway_state',return_value='missing'),patch.object(api,'_gateway_auth',return_value={}):
            with self.assertRaises(HTTPException) as error:api.cancel(self.body.request_id,self.body,self.p,self.db)
            self.assertEqual(error.exception.status_code,409);self.assertEqual(self.db.row.data['state'],'pending');self.assertEqual(self.db.commits,0)

    def test_lost_gateway_write_reconciles_matching_existing_path(self):
        data={**self.body.model_dump(),'submission_hash':api.digest(self.body.model_dump()),'gateway_path':'onvif_'+'a'*32,
              'gateway_generation':1,'gateway_write_started':True,'state':'pending'}
        self.db.row=SimpleNamespace(id='fixture-job',kind=api.KIND,status='internal',data=data,version=1)
        device={'service_address':'https://192.168.45.32/onvif','source_ip':'192.168.45.32'}
        with patch.object(api,'_lock'),patch.object(api,'_receipt',return_value=self.db.row),patch.object(api,'audit'),patch.object(api,'_inputs',return_value=(device,None,'http://127.0.0.1:19997')),patch.object(api,'stream_uri',return_value='rtsp://192.168.45.32/live'),patch.object(api,'gateway_state',return_value='matches'),patch.object(api,'_gateway_auth',return_value={}):
            result=api.cancel(self.body.request_id,self.body,self.p,self.db)
            self.assertEqual(result['state'],'configured');self.assertEqual(self.db.commits,1)

    def test_gateway_writes_then_response_drops_cancel_reconciles_side_effect(self):
        gateway=SimpleNamespace(id='gateway',data={'generation':1});side_effect={'written':False}
        device={'service_address':'https://192.168.45.32/onvif','source_ip':'192.168.45.32'}
        def lost_response(*_):
            self.assertTrue(self.db.row.data['gateway_write_started'])
            self.assertEqual(self.db.commits,2,'write fence must be durable before the external request')
            side_effect['written']=True
            raise api.EnrollmentError('模拟网关写入后响应丢失')
        def current(*_):return 'matches' if side_effect['written'] else 'missing'
        with patch.object(api,'_lock'),patch.object(api,'_receipt',side_effect=lambda *_:self.db.row),patch.object(api,'audit'),patch.object(api,'_inputs',return_value=(device,gateway,'http://127.0.0.1:19997')),patch.object(api,'stream_uri',return_value='rtsp://192.168.45.32/live'),patch.object(api,'_gateway_auth',return_value={}),patch.object(api,'configure_gateway',side_effect=lost_response),patch.object(api,'gateway_state',side_effect=current):
            with self.assertRaises(HTTPException):api.enroll(self.body,self.p,self.db)
            self.assertTrue(side_effect['written']);self.assertEqual(self.db.row.data['state'],'pending')
            result=api.cancel(self.body.request_id,self.body,self.p,self.db)
            self.assertEqual(result['state'],'configured');self.assertEqual(self.db.row.data['state'],'configured')

if __name__=='__main__':unittest.main()
