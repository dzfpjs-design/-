"""Real producer stopping and original receipts, with explicit corruption seams.

The PG cases register actual published synthetic sources. No producer, source
authority or receipt is fabricated. Direct mutations below are named storage or
live-health fault controls, not claims that such faults occurred in production.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import graph_event_revocation_v122 as revocations
from generic_agents.db import engine
from generic_agents.graph_event_api import Revoke
from generic_agents.graph_event_models import (
    GraphEventProducer, GraphEventIntent, GraphEventAdmission, GraphEventStream,
)
from generic_agents.models import (
    Audit, Capability, CloudCall, Event, Invocation, Job, Membership, Task, User,
)
from generic_agents.security import password_hash
from tests.test_graph_event_pipeline import BASE, ok, scene


POST = BASE + '/revocations'
RECORD = revocations.GraphEventProducerRevocation


def _command(producer):
    return {'schema': 'graph-event-producer-revocation-v2', 'request_id': str(uuid4()),
        'producer_id': producer['id'], 'producer_version': producer['version'],
        'contract_hash': producer['contract_hash'], 'reason': '核对后停止此登记来源的新消息接纳🙂'}


def _path(command):
    return POST + '/requests/' + command['request_id']


def _counts():
    with Session(engine()) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Task, Job, Invocation, CloudCall, Event, GraphEventIntent,
                GraphEventAdmission, GraphEventStream, RECORD, Audit)}


def _cold():
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()))
    command = _command({'id': str(uuid4()), 'version': 3, 'contract_hash': 'a' * 64})
    row = NS(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id,
        producer_id=command['producer_id'], request_id=command['request_id'], version=1,
        command_hash=revocations.command_hash(p, command), data={'command': copy.deepcopy(command),
            'result': {'status': 'revoked', 'version': 4, 'contract_hash': command['contract_hash']}})
    return p, row


def test_legacy_revoke_dto_is_unchanged_and_v2_has_only_the_explicit_target_pins():
    assert set(Revoke.model_fields) == {'version', 'reason'}
    p, row = _cold()
    body = revocations.Revocation.model_validate(row.data['command'])
    assert body.model_dump(mode='json') == row.data['command']
    assert set(revocations.Revocation.model_fields) == {'schema', 'request_id', 'producer_id',
        'producer_version', 'contract_hash', 'reason'}
    assert revocations.command_hash(p, body.model_dump(mode='json')) == row.command_hash


@pytest.mark.parametrize(('field', 'value'), [
    ('producer_version', True), ('producer_version', 0), ('producer_version', '1'),
    ('producer_version', 1.0), ('reason', ''), ('reason', ' '), ('reason', ' 前后空白 '),
    ('reason', 'x' * 1001), ('reason', True), ('reason', '\x00'), ('reason', '\ud800'),
    ('contract_hash', 'A' * 64), ('contract_hash', 'a' * 64 + '\n'),
    ('producer_id', 'not-a-producer'), ('unexpected', '不能增加命令字段'),
])
def test_v2_command_rejects_untyped_untrimmed_or_open_fields(field, value):
    _, row = _cold(); body = {**row.data['command'], field: value}
    with pytest.raises(ValidationError): revocations.Revocation.model_validate(body)


def test_cold_receipt_is_detached_and_does_not_need_current_producer_or_source():
    p, row = _cold(); before = copy.deepcopy(row.__dict__)
    receipt = revocations.checked_receipt(row, p, request_id=row.request_id)
    assert set(receipt) == {'id', 'request_id', 'actor_id', 'producer_id',
        'command_hash', 'command', 'result'}
    assert receipt['result'] == {'status': 'revoked', 'version': 4, 'contract_hash': 'a' * 64}
    receipt['command']['reason'] = '不能修改原命令'
    receipt['result']['version'] = 99
    assert row.__dict__ == before


@pytest.mark.parametrize('change', ['record_extra', 'record_missing', 'command_extra', 'reason',
    'command_nonce', 'command_producer', 'row_nonce', 'row_producer', 'workspace', 'command_hash',
    'result_extra', 'result_missing', 'result_status', 'result_version', 'boolean_result_version',
    'result_contract', 'record_version', 'boolean_record_version', 'coerced_command', 'uuid_normalization'])
def test_cold_corruption_is_409_and_never_repaired_even_with_resealed_malformed_command(change):
    p, row = _cold()
    if change == 'record_extra': row.data['extra'] = '不可增补'
    elif change == 'record_missing': row.data.pop('result')
    elif change == 'command_extra': row.data['command']['extra'] = '不可增补'
    elif change == 'reason': row.data['command']['reason'] = '另一份未签入的停止理由'
    elif change == 'command_nonce': row.data['command']['request_id'] = str(uuid4())
    elif change == 'command_producer': row.data['command']['producer_id'] = str(uuid4())
    elif change == 'row_nonce': row.request_id = str(uuid4())
    elif change == 'row_producer': row.producer_id = str(uuid4())
    elif change == 'workspace': row.workspace_id = str(uuid4())
    elif change == 'command_hash': row.command_hash = 'f' * 64
    elif change == 'result_extra': row.data['result']['extra'] = '不可增补'
    elif change == 'result_missing': row.data['result'].pop('version')
    elif change == 'result_status': row.data['result']['status'] = 'active'
    elif change == 'result_version': row.data['result']['version'] += 1
    elif change == 'boolean_result_version': row.data['result']['version'] = True
    elif change == 'result_contract': row.data['result']['contract_hash'] = 'b' * 64
    elif change == 'record_version': row.version = 2
    elif change == 'boolean_record_version': row.version = True
    else:
        if change == 'coerced_command': row.data['command']['producer_version'] = '3'
        else: row.data['command']['producer_id'] = '{' + row.producer_id + '}'
        row.command_hash = revocations.command_hash(p, row.data['command'])
    before = copy.deepcopy(row.__dict__)
    with pytest.raises(HTTPException) as caught: revocations.checked_receipt(row, p)
    assert caught.value.status_code == 409 and row.__dict__ == before


def test_cold_foreign_actor_precedes_corrupt_private_body():
    p, row = _cold(); row.actor_id = str(uuid4()); row.data = None
    with pytest.raises(HTTPException) as caught: revocations.checked_receipt(row, p)
    assert caught.value.status_code == 403


def test_actual_stop_exact_replay_and_original_nonce_survive_source_health_and_role_changes(client):
    value = scene(client); producer = value['producers'][0]; body = _command(producer)
    before = _counts()
    accepted = ok(client.post(POST, json=body))
    receipt = accepted['receipt']
    assert accepted['replayed'] is False and receipt['command'] == body
    assert receipt['actor_id'] == value['principal'].user_id and receipt['producer_id'] == producer['id']
    assert receipt['command_hash'] == revocations.command_hash(value['principal'], body)
    assert receipt['result'] == {'status': 'revoked', 'version': producer['version'] + 1,
        'contract_hash': producer['contract_hash']}
    assert _counts() == {**before, RECORD.__tablename__: before[RECORD.__tablename__] + 1,
        Audit.__tablename__: before[Audit.__tablename__] + 1}
    after = _counts()
    with Session(engine()) as db:
        row = db.get(GraphEventProducer, producer['id'])
        assert row.status == 'revoked' and row.version == producer['version'] + 1
        assert row.data['registration']['command'] == value['commands'][0]
        # Deliberate current-health/state fault controls: the old immutable
        # receipt must neither consult these rows nor report a newer version.
        db.get(Capability, value['source']['id']).status = 'retired'
        db.get(Capability, value['extractor']['id']).status = 'retired'
        row.version += 1
        db.commit()
    assert ok(client.get(_path(body))) == {'found': True, 'receipt': receipt}
    assert ok(client.post(POST, json=body)) == {'receipt': receipt, 'replayed': True}
    assert client.post(POST, json={**body, 'reason': '同编号不能替换停止理由'}).status_code == 409
    assert _counts() == after
    missing = {**body, 'request_id': str(uuid4())}
    assert ok(client.get(_path(missing))) == {'found': False, 'receipt': None}
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id))
        member.role = 'viewer'; db.commit()
    assert ok(client.get(_path(body))) == {'found': True, 'receipt': receipt}
    assert client.post(POST, json=body).status_code == 403
    assert _counts() == after


def test_actual_cas_and_already_revoked_producer_never_create_a_receipt_for_a_new_nonce(client):
    value = scene(client); producer = value['producers'][0]; body = _command(producer)
    before = _counts()
    assert client.post(POST, json={**body, 'producer_version': producer['version'] + 1}).status_code == 409
    assert client.post(POST, json={**body, 'contract_hash': 'f' * 64}).status_code == 409
    assert _counts() == before
    assert ok(client.get(_path(body))) == {'found': False, 'receipt': None}
    with Session(engine()) as db:
        row = db.get(GraphEventProducer, producer['id'])
        assert row.status == 'active' and row.version == producer['version']
    # The unchanged legacy endpoint really stops it, but supplies no receipt
    # for this new protocol's nonce. It cannot be attributed to this command.
    old = ok(client.post(BASE + '/' + producer['id'] + '/revoke',
        json={'version': producer['version'], 'reason': '旧协议实际停止来源'}))
    after_old = _counts()
    assert after_old == {**before, Audit.__tablename__: before[Audit.__tablename__] + 1}
    current_pin = {**body, 'producer_version': old['version']}
    assert client.post(POST, json=current_pin).status_code == 409
    assert client.post(POST, json=body).status_code == 409
    assert _counts() == after_old
    assert ok(client.get(_path(body))) == {'found': False, 'receipt': None}


def test_actual_other_admin_cannot_read_or_rebind_the_original_actors_revocation_nonce(client):
    value = scene(client); body = _command(value['producers'][0])
    receipt = ok(client.post(POST, json=body))['receipt']
    username, password = 'graph_revoke_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password))
        db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=value['principal'].workspace_id, role='admin'))
        db.commit()
        job_ids = set(db.scalars(select(Job.id)))
    before = _counts()
    with TestClient(app) as other:
        login = ok(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'],
            'X-Workspace-ID': value['principal'].workspace_id})
        after_login = _counts()
        assert after_login == {**before, Job.__tablename__: before[Job.__tablename__] + 1}
        with Session(engine()) as db:
            added = db.execute(select(Job.kind, Job.status).where(Job.id.not_in(job_ids))).all()
            assert [tuple(row) for row in added] == [('login_limit', 'internal')]
        assert other.get(_path(body)).status_code == 403
        assert other.post(POST, json=body).status_code == 403
        assert _counts() == after_login
    assert ok(client.get(_path(body))) == {'found': True, 'receipt': receipt}


def test_actual_corrupt_receipt_blocks_lookup_and_replay_without_repair_or_new_work(client):
    value = scene(client); body = _command(value['producers'][0])
    accepted = ok(client.post(POST, json=body)); identity = accepted['receipt']['id']
    with Session(engine()) as db:
        row = db.get(RECORD, identity)
        original = {'data': copy.deepcopy(row.data), 'command_hash': row.command_hash, 'version': row.version}
    before = _counts()
    for change in ('reason', 'original_target', 'nonce', 'hash', 'closed_record', 'closed_result', 'result', 'version'):
        with Session(engine()) as db:
            row = db.get(RECORD, identity)
            for key, content in original.items(): setattr(row, key, copy.deepcopy(content))
            data = copy.deepcopy(row.data)
            if change == 'reason': data['command']['reason'] = '另一份未签入理由'
            elif change == 'original_target': data['command']['producer_id'] = str(uuid4())
            elif change == 'nonce': data['command']['request_id'] = str(uuid4())
            elif change == 'hash': row.command_hash = 'f' * 64
            elif change == 'closed_record': data['extra'] = '未知字段'
            elif change == 'closed_result': data['result']['extra'] = '未知字段'
            elif change == 'result': data['result']['version'] += 1
            else: row.version = 2
            row.data = data; db.commit()
            corrupted = {'data': copy.deepcopy(row.data), 'command_hash': row.command_hash, 'version': row.version}
        assert client.get(_path(body)).status_code == 409, change
        assert client.post(POST, json=body).status_code == 409, change
        assert _counts() == before, change
        with Session(engine()) as db:
            row = db.get(RECORD, identity)
            assert {key: copy.deepcopy(getattr(row, key)) for key in corrupted} == corrupted
            producer = db.get(GraphEventProducer, body['producer_id'])
            assert producer.status == 'revoked' and producer.version == body['producer_version'] + 1
    # Restore only this test's explicit corruption, never in the public API.
    with Session(engine()) as db:
        row = db.get(RECORD, identity)
        for key, content in original.items(): setattr(row, key, copy.deepcopy(content))
        db.commit()
    assert ok(client.get(_path(body))) == {'found': True, 'receipt': accepted['receipt']}
    assert ok(client.post(POST, json=body)) == {'receipt': accepted['receipt'], 'replayed': True}
    assert _counts() == before
