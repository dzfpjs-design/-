"""Durability and fail-closed semantics using a disposable PostgreSQL DB."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import sleep

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from generic_agents.db import engine
from generic_agents.dji_cloud2_sandbox import DockerSandbox, SandboxSpec, StopResult
from generic_agents import dji_cloud2_sandbox_journal as journal
from generic_agents.dji_cloud2_connections import _lock
from tests.test_managed_mqtt_grants import vault
from tests.test_dji_cloud2_connections import allowed, payload


class Running:
    def poll(self): return None


def _sandbox(workspace, connection):
    spec = SandboxSpec(workspace_id=workspace, connection_id=connection['id'],
        generation=connection['version'], host='192.168.0.10', port=443,
        policy_digest='a'*64, guard_image='sha256:'+'b'*64, sdk_image='sha256:'+'c'*64,
        allowed_networks=('192.168.0.0/24',))
    return DockerSandbox(spec, lambda _: True)


def test_restart_reloads_intent_and_confirmed_cleanup_frees_connection(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    with Session(engine()) as db:
        jid = journal.begin(db, sandbox); db.commit()
    with Session(engine()) as db:
        saved = journal.pending(db, account[2])
        assert len(saved) == 1 and saved[0]['state'] == 'starting'
        assert saved[0]['network_name'] == sandbox.network_name
        with pytest.raises(HTTPException): journal.begin(db, _sandbox(account[2], con))
        db.rollback()
    sandbox._owned_network_id = sandbox.network_id = 'd'*64
    with Session(engine()) as db:
        journal.record_network(db, account[2], jid, sandbox); db.commit()
    sandbox._started = True; sandbox.guard = Running(); sandbox.sdk = Running()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): journal.mark_active(db, account[2], jid, sandbox)
        db.rollback()
    # Unit-level attestation stand-in. The Linux end-to-end test obtains READY
    # from the real SDK pipe and verifies Docker's container identity.
    sandbox.verify_sdk_ready = lambda: 'e'*64
    with Session(engine()) as db:
        journal.mark_active(db, account[2], jid, sandbox); db.commit()
    result = StopResult(False, False, False); sandbox._last_stop_result = result
    with Session(engine()) as db:
        assert journal.mark_stop(db, account[2], jid, sandbox, result) == 'cleanup_unverified'
        db.commit()
    with Session(engine()) as db:
        assert journal.pending(db, account[2])[0]['state'] == 'cleanup_unverified'
        db.rollback()
    result = StopResult(True, True, True); sandbox._last_stop_result = result
    with Session(engine()) as db:
        assert journal.mark_stop(db, account[2], jid, sandbox, result) == 'closed'
        db.commit()
    with Session(engine()) as db:
        assert journal.pending(db, account[2]) == []
        next_id = journal.begin(db, _sandbox(account[2], con)); db.commit()
        assert next_id != jid


def test_uncertain_create_or_unverified_stop_blocks_automatic_restart(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    with Session(engine()) as db:
        jid = journal.begin(db, sandbox); db.commit()
    # A timed-out create may appear after a negative list. Even a superficially
    # successful stop cannot close an intent that never reached known-active.
    result = StopResult(True, True, True); sandbox._last_stop_result = result
    with Session(engine()) as db:
        assert journal.mark_stop(db, account[2], jid, sandbox, result) == 'cleanup_unverified'
        db.commit()
    with Session(engine()) as db:
        assert journal.pending(db, account[2])[0]['state'] == 'cleanup_unverified'
        with pytest.raises(HTTPException): journal.begin(db, _sandbox(account[2], con))
        db.rollback()


def test_revocation_prevents_starting_new_journal(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    response = client.post('/api/dji/cloud2/connections/'+con['id']+'/revoke',
        json={'request_id':str(uuid4()), 'version':con['version'], 'confirm':True})
    assert response.status_code == 200
    with Session(engine()) as db:
        with pytest.raises(HTTPException): journal.begin(db, _sandbox(account[2], con))


def test_other_sandbox_or_fabricated_stop_result_cannot_close_intent(client, account, allowed):
    first = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    second = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    a, b = _sandbox(account[2], first), _sandbox(account[2], second)
    with Session(engine()) as db:
        jid = journal.begin(db, a); db.commit()
    fake = StopResult(True, True, True); b._last_stop_result = fake
    with Session(engine()) as db:
        with pytest.raises(HTTPException): journal.mark_stop(db, account[2], jid, b, fake)
        db.rollback()
        with pytest.raises(ValueError): journal.mark_stop(db, account[2], jid, a, fake)
        db.rollback()
        assert journal.pending(db, account[2])[0]['state'] == 'starting'


def test_sandbox_destination_must_match_persisted_connection(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    sandbox.spec = replace(sandbox.spec, host='192.168.0.11')
    with Session(engine()) as db:
        with pytest.raises(HTTPException): journal.begin(db, sandbox)


def test_init_write_is_serialized_with_revocation(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    with Session(engine()) as db:
        jid = journal.begin(db, sandbox); db.commit()
    sent = []; entered = Event(); release = Event()
    sandbox.verify_preflight = lambda: None
    def send(frame):
        entered.set()
        assert release.wait(3)
        sent.append(frame)
    sandbox.send_init = send
    revoke = {'request_id':str(uuid4()), 'version':con['version'], 'confirm':True}
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(journal.send_init, account[2], jid, sandbox)
        assert entered.wait(2)
        pending = pool.submit(client.post, '/api/dji/cloud2/connections/'+con['id']+'/revoke', json=revoke)
        sleep(.2)
        assert not pending.done()
        release.set(); writing.result(timeout=5)
        assert pending.result(timeout=5).status_code == 200
    with pytest.raises(HTTPException): journal.send_init(account[2], jid, sandbox)
    assert len(sent) == 1


def test_init_fails_with_bounded_wait_when_authorization_lock_is_held(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    with Session(engine()) as db:
        jid = journal.begin(db, sandbox); db.commit()
    sent = []
    sandbox.verify_preflight = lambda: None
    sandbox.send_init = lambda frame: sent.append(frame)
    with Session(engine()) as blocker:
        _lock(blocker, account[2])
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(journal.send_init, account[2], jid, sandbox)
            with pytest.raises(OperationalError): pending.result(timeout=4)
        blocker.rollback()
    assert sent == []
