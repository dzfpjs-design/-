from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from generic_agents.config import settings
from tests.test_mqtt_api import configuration


def test_lost_response_retry_is_one_saved_gateway_even_if_allowlist_removed(client,monkeypatch):
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    body={'name':'sensor one','configuration':configuration(),'request_id':str(uuid4())}
    first=client.post('/api/mqtt/gateways',json=body)
    assert first.status_code==201
    row=first.json();assert row['request_id']==body['request_id']
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','')
    retry=client.post('/api/mqtt/gateways',json=body)
    assert retry.status_code==201 and retry.json()==row
    assert client.get('/api/mqtt/gateways').json()==[row]
    changed=client.post('/api/mqtt/gateways',json={**body,'name':'different'})
    assert changed.status_code==409
    assert client.get('/api/mqtt/gateways').json()==[row]


def test_concurrent_retries_same_request_are_serial_and_do_not_duplicate(client,monkeypatch):
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    body={'name':'sensor one','configuration':configuration(),'request_id':str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post('/api/mqtt/gateways',json=body),range(2)))
    assert [r.status_code for r in responses]==[201,201]
    assert responses[0].json()['id']==responses[1].json()['id']
    assert len(client.get('/api/mqtt/gateways').json())==1


def test_recovery_request_shape_rejects_invalid_ids_without_saving(client,monkeypatch):
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    response=client.post('/api/mqtt/gateways',json={'name':'sensor one','configuration':configuration(),'request_id':'wrong'})
    assert response.status_code==422
    assert client.get('/api/mqtt/gateways').json()==[]
