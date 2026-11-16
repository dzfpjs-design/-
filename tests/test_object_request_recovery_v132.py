"""Original object request API: real issued release, actor, schema and PostgreSQL.

The manual business and records are explicitly synthetic. Root alone executes
the optional frontend bridge; no original document or model quality is claimed.
"""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Entity, Membership
from tests import test_instance_workspace as workspace
from tests.test_typed_payload_journey_v131 import _canonical, _typed_number
from tests.test_report_evidence_pipeline import reply


def issued(client, monkeypatch):
    objects = copy.deepcopy(workspace.OBJECTS)
    objects[0]['fields'].append({'label': '原登记数值', 'kind': '数值', 'required': False})
    monkeypatch.setattr(workspace, 'OBJECTS', objects)
    release, _, _ = workspace.prepared(client)
    workspace.enable(client, release)
    return release, release['snapshot']['object_types'][0]


def post(client, path, body, status=200):
    return reply(client.post(path, content=_canonical(body), headers={'Content-Type': 'application/json'}), status=status)


def inventory():
    with Session(engine()) as db:
        return {model.__name__: sorted(db.scalars(select(model.id))) for model in (Entity, Audit)}


@pytest.mark.parametrize('number', [-0.0, 1.0, 1e21], ids=['negative-zero', 'explicit-float', 'large-float'])
def test_original_object_lookup_read_only_replay_and_paused_history(client, monkeypatch, number):
    release, kind = issued(client, monkeypatch)
    field = next(item['key'] for item in kind['fields'] if item['kind'] == '数值')
    body = {'release_id': release['id'], 'object_type': kind['key'], 'reference': '',
        'request_id': str(uuid4()), 'attributes': {kind['fields'][0]['key']: '公开合成对象', field: number}}
    path = '/api/instances/' + release['instance_id'] + '/objects'
    before = inventory()
    missing = post(client, path + '/requests/lookup-v132', body)
    assert not missing['found'] and missing['retirement'] is None
    assert missing['request_hash'] == digest(body) and inventory() == before
    saved = post(client, path, body, 201)
    assert post(client, path, body, 201)['id'] == saved['id']
    found = post(client, path + '/requests/lookup-v132', body)
    assert found['found'] and found['object']['id'] == saved['id']
    _typed_number(found['object']['attributes'][field], number)
    with Session(engine()) as db:
        _typed_number(db.get(Entity, saved['id']).data[field], number)
        audit = db.scalar(select(Audit).where(Audit.action == 'instance.object.created', Audit.object_id == saved['id']))
        assert audit.data['request_hash'] == digest(body)
    changed = copy.deepcopy(body); changed['attributes'][field] = int(number)
    assert client.post(path + '/requests/lookup-v132', content=_canonical(changed), headers={'Content-Type': 'application/json'}).status_code == 409
    current = next(item for item in client.get('/api/instances').json() if item['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + current['id'] + '/pause', json={'version': current['version']}))
    before = inventory()
    assert post(client, path + '/requests/lookup-v132', body)['object']['id'] == saved['id']
    assert post(client, path + '/requests/retire-v132', {'original': body, 'reason': '核对既有结果'})['found']
    assert inventory() == before
    with Session(engine()) as db:
        db.scalar(select(Membership)).role = 'viewer'; db.commit()
    assert client.post(path + '/requests/lookup-v132', content=_canonical(body), headers={'Content-Type': 'application/json'}).status_code == 403


def test_retirement_is_durable_idempotent_and_fences_late_original_create(client, monkeypatch):
    release, kind = issued(client, monkeypatch)
    body = {'release_id': release['id'], 'object_type': kind['key'], 'attributes': {kind['fields'][0]['key']: '原输入保留'}, 'reference': '', 'request_id': str(uuid4())}
    path = '/api/instances/' + release['instance_id'] + '/objects'
    first = post(client, path + '/requests/retire-v132', {'original': body, 'reason': '保留输入，停止原登记'})
    assert first['retirement']['request_hash'] == digest(body) and not first['found']
    before = inventory()
    assert post(client, path + '/requests/retire-v132', {'original': body, 'reason': '再次核对'}) == first
    assert post(client, path + '/requests/lookup-v132', body) == first
    assert client.post(path, content=_canonical(body), headers={'Content-Type': 'application/json'}).status_code == 409
    changed = copy.deepcopy(body); changed['reference'] = 'changed'
    assert client.post(path + '/requests/retire-v132', content=_canonical({'original': changed, 'reason': '不允许更改原请求'}), headers={'Content-Type': 'application/json'}).status_code == 409
    assert inventory() == before
