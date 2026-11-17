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
    sandbox.verified_guard_id = lambda: 'f'*64
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

class RecoveryDocker:
    def __init__(self, sandbox, active=True):
        suffix = sandbox.guard_name.rsplit('-', 1)[1]
        def container(identity, name, image, component):
            return {'Id': identity, 'Name': '/'+name, 'Image': image,
                    'Config': {'Labels': {'org.generic-agents.component': component,
                                         'org.generic-agents.lease': suffix}}}
        self.objects = {}
        self.network = None
        self.removed = []
        if active:
            self.objects[sandbox.sdk_name] = container('e'*64, sandbox.sdk_name,
                sandbox.spec.sdk_image, 'dji-cloud2-sdk')
            self.objects[sandbox.guard_name] = container('f'*64, sandbox.guard_name,
                sandbox.spec.guard_image, 'dji-cloud2-egress')
            self.network = {'Id': 'd'*64, 'Name': sandbox.network_name, 'Driver': 'bridge',
                'Scope': 'local', 'Internal': True, 'Containers': {'f'*64: {}},
                'Labels': {'org.generic-agents.component': 'dji-cloud2-test-network',
                           'org.generic-agents.lease': suffix}}
    def verify_daemon(self): pass
    def inspect_container(self, name, identity=None):
        if identity:
            for obj in self.objects.values():
                if obj['Id'] == identity: return obj
        return self.objects.get(name)
    def inspect_network(self, identity): return self.network if self.network and self.network['Id'] == identity else None
    def remove_container(self, identity):
        self.removed.append(identity)
        for name, obj in list(self.objects.items()):
            if obj['Id'] == identity: del self.objects[name]
        if self.network: self.network['Containers'].pop(identity, None)
    def remove_network(self, identity): self.removed.append(identity); self.network = None
    def containers(self): return set(self.objects)
    def networks(self): return {self.network['Id']} if self.network else set()


def _recovery_setup(client, account, active=True):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    with Session(engine()) as db:
        jid = journal.begin(db, sandbox); db.commit()
    if active:
        sandbox._owned_network_id = sandbox.network_id = 'd'*64
        with Session(engine()) as db:
            journal.record_network(db, account[2], jid, sandbox); db.commit()
        sandbox._started = True; sandbox.guard = Running(); sandbox.sdk = Running()
        sandbox.verify_sdk_ready = lambda: 'e'*64
        sandbox.verified_guard_id = lambda: 'f'*64
        with Session(engine()) as db:
            journal.mark_active(db, account[2], jid, sandbox); db.commit()
    return con, sandbox, jid


def test_recovery_owns_ids_and_never_fabricates_fence(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account)
    docker = RecoveryDocker(sandbox)
    result = journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)
    assert result == {'state':'cleanup_unverified', 'outcome':'resources_absent_unverified'}
    assert docker.removed == ['e'*64, 'f'*64, 'd'*64]
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker) == result
    with Session(engine()) as db:
        assert journal.pending(db, account[2])[0]['recovery_outcome'] == 'resources_absent_unverified'
        with pytest.raises(HTTPException): journal.begin(db, _sandbox(account[2], {'id':sandbox.spec.connection_id, 'version':sandbox.spec.generation}))


def test_recovery_refuses_changed_identity_or_foreign_network_endpoint(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account)
    docker = RecoveryDocker(sandbox)
    docker.objects[sandbox.guard_name]['Id'] = '9'*64
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)['outcome'] == 'identity_unverified'
    assert docker.removed == []
    docker = RecoveryDocker(sandbox)
    docker.network['Containers']['9'*64] = {}
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)['outcome'] == 'network_in_use'
    assert docker.removed == []


def test_recovery_unknown_create_and_daemon_failure_are_unresolved(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account, active=False)
    docker = RecoveryDocker(sandbox, active=False)
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)['outcome'] == 'creation_unresolved'
    docker = RecoveryDocker(sandbox)
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)['outcome'] == 'identity_unverified'
    assert not docker.removed
    docker.verify_daemon = lambda: (_ for _ in ()).throw(journal._RecoveryFailure('daemon_unavailable'))
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)['outcome'] == 'daemon_unavailable'


def test_recovery_workspace_and_lease_isolation(client, account, allowed):
    from generic_agents.mqtt_subscriber import subscription_lease
    _, sandbox, jid = _recovery_setup(client, account)
    docker = RecoveryDocker(sandbox)
    with pytest.raises(HTTPException) as error:
        journal.recover_owned(str(uuid4()), jid, _docker_factory=lambda: docker)
    assert error.value.status_code == 404 and not docker.removed
    with subscription_lease('dji-cloud2:'+account[2]+':'+sandbox.spec.connection_id):
        with pytest.raises(HTTPException) as error:
            journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)
    assert error.value.status_code == 409 and not docker.removed


def test_network_mode_is_bound_to_original_intent(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account, active=False)
    sandbox._owned_network_id = 'd'*64
    sandbox.spec = replace(sandbox.spec, network_mode='lan_test')
    with Session(engine()) as db:
        with pytest.raises(HTTPException): journal.record_network(db, account[2], jid, sandbox)


def test_recovery_cas_detects_concurrent_state_change(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account)
    docker = RecoveryDocker(sandbox)
    def alter_during_daemon_call():
        # This commit also proves recovery has not held a workspace transaction
        # lock across its daemon operations.
        with Session(engine()) as db:
            journal.mark_unverified(db, account[2], jid); db.commit()
    docker.verify_daemon = alter_during_daemon_call
    with pytest.raises(HTTPException) as error:
        journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)
    assert error.value.status_code == 409
    with Session(engine()) as db:
        row = journal.pending(db, account[2])[0]
        assert row['state'] == 'cleanup_unverified'
        assert row['recovery_outcome'] == 'checking'


def test_recovery_rejects_renamed_recorded_guard(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account)
    docker = RecoveryDocker(sandbox)
    moved = docker.objects.pop(sandbox.guard_name)
    moved['Name'] = '/renamed-guard'
    docker.objects['renamed-guard'] = moved
    assert journal.recover_owned(account[2], jid, _docker_factory=lambda: docker)['outcome'] == 'identity_unverified'
    assert not docker.removed


def test_timeout_stop_is_durable_and_never_overwrites_confirmed_cleanup(client, account, allowed):
    _, sandbox, jid = _recovery_setup(client, account)
    with Session(engine()) as db:
        with pytest.raises(ValueError):
            journal.mark_stop(db, account[2], jid, sandbox, sandbox._timeout_stop_result)
    entered, release = Event(), Event()
    def hold_start_lock():
        with sandbox._lifecycle_lock:
            entered.set()
            assert release.wait(8)
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(hold_start_lock)
        try:
            assert entered.wait(2)
            result = sandbox.stop()
            assert result == StopResult(False, False, False)
            with Session(engine()) as db:
                with pytest.raises(ValueError):
                    journal.mark_stop(db, account[2], jid, sandbox, StopResult(False, False, False))
                db.rollback()
                assert journal.mark_stop(db, account[2], jid, sandbox, result) == 'cleanup_unverified'
                db.commit()
            with Session(engine()) as db:
                assert journal.pending(db, account[2])[0]['state'] == 'cleanup_unverified'
        finally:
            release.set()
            blocked.result(timeout=5)
    # Simulate a later separately verified stop receipt, as in the other unit
    # journal tests; the negative timeout receipt cannot undo its durable close.
    verified = StopResult(True, True, True)
    sandbox._last_stop_result = verified
    with Session(engine()) as db:
        assert journal.mark_stop(db, account[2], jid, sandbox, verified) == 'closed'
        db.commit()
    with Session(engine()) as db:
        assert journal.mark_stop(db, account[2], jid, sandbox, result) == 'closed'
        db.commit()
    with Session(engine()) as db:
        assert journal.pending(db, account[2]) == []


def test_timeout_during_network_creation_remains_unverified_after_id_arrives(client, account, allowed):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    sandbox = _sandbox(account[2], con)
    with Session(engine()) as db:
        jid = journal.begin(db, sandbox); db.commit()
    entered, release = Event(), Event()
    def hold_start_lock():
        with sandbox._lifecycle_lock:
            entered.set()
            assert release.wait(8)
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(hold_start_lock)
        try:
            assert entered.wait(2)
            result = sandbox.stop()
            # Network create returned between timeout and durable reporting.
            sandbox._owned_network_id = 'd'*64
            with Session(engine()) as db:
                assert journal.mark_stop(db, account[2], jid, sandbox, result) == 'cleanup_unverified'
                db.commit()
        finally:
            release.set()
            blocked.result(timeout=5)
    with Session(engine()) as db:
        assert journal.pending(db, account[2])[0]['state'] == 'cleanup_unverified'
        with pytest.raises(HTTPException):
            journal.begin(db, _sandbox(account[2], con))
