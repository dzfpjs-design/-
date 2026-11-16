"""Durable Cloud API 2 permission boundaries using isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from generic_agents import dji_cloud2_auth_epoch as epochs
from generic_agents import dji_cloud2_connections as cloud2
from generic_agents.db import engine
from generic_agents.models import Job, Membership, User
from tests.test_dji_cloud2_connections import allowed, payload
from tests.test_managed_mqtt_grants import vault


def _create(client):
    body = payload()
    response = client.post('/api/dji/cloud2/connections', json=body)
    assert response.status_code == 200, response.text
    return body, response.json()['connection']


def _valid(workspace, item):
    with Session(engine()) as db:
        return cloud2.is_current(db, workspace, item['id'], item['version'])


def test_role_changes_between_polls_in_one_transaction_permanently_invalidate(client, account, allowed):
    body, item = _create(client)
    assert _valid(account[2], item)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        member.role = 'viewer'; db.flush()
        member.role = 'admin'; db.flush(); db.commit()
    assert not _valid(account[2], item)
    with Session(engine()) as db:
        assert not cloud2.publish_state(db, account[2], item['id'], item['version'], 'connected', 1)
        with pytest.raises(HTTPException): cloud2.acquire_snapshot(db, account[2], item['id'])
    listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert listing['status'] == 'authorization_lost' and listing['device_count'] is None
    assert '重新新增' in listing['notice']
    receipt = client.post('/api/dji/cloud2/connections', json=body).json()
    assert receipt['connection']['id'] == item['id'] and receipt['connection']['status'] == 'authorization_lost'
    assert client.get('/api/dji/cloud2/operations/'+body['request_id']).json() == receipt
    _, replacement = _create(client)
    assert replacement['id'] != item['id'] and _valid(account[2], replacement)


def test_user_disable_and_reenable_invalidates_original_authorization(client, account, allowed):
    _, item = _create(client)
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        user.active = False; db.flush()
        user.active = True; db.flush(); db.commit()
    assert not _valid(account[2], item)
    _, replacement = _create(client)
    assert _valid(account[2], replacement)


def test_deleted_and_recreated_membership_cannot_reuse_old_authorization(client, account, allowed):
    _, item = _create(client)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        identity, uid, wid = member.id, member.user_id, member.workspace_id
        db.delete(member); db.flush()
        # Even intentionally reusing the primary key cannot reuse the nonce.
        db.add(Membership(id=identity, user_id=uid, workspace_id=wid, role='admin')); db.commit()
    assert not _valid(account[2], item)


def test_uncommitted_permission_change_rollback_preserves_authorization(client, account, allowed):
    _, item = _create(client)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        member.role = 'viewer'; db.flush(); db.rollback()
    assert _valid(account[2], item)


def test_migration_is_idempotent_and_legacy_authorizations_remain_closed(client, account, allowed):
    _, item = _create(client)
    with Session(engine()) as db:
        before = epochs.actor_identity(db, account[2], db.get(Job, item['id']).data['actor_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        for task in [pool.submit(epochs.migrate, engine()) for _ in range(2)]: task.result(timeout=15)
    with Session(engine()) as db:
        row = db.get(Job, item['id'])
        assert epochs.actor_identity(db, account[2], row.data['actor_id']) == before
        row.data = {k:v for k,v in row.data.items() if k != 'authorization'}
        db.commit()
    assert not _valid(account[2], item)
    listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert listing['status'] == 'authorization_lost'
    # Existing disabled/legacy records remain removable without migrating secrets.
    result = client.post('/api/dji/cloud2/connections/'+item['id']+'/revoke', json={
        'request_id':str(uuid4()), 'version':item['version'], 'confirm':True})
    assert result.status_code == 200 and result.json()['connection']['enabled'] is False


def test_additive_migration_preserves_existing_identity_rows_and_rolls_back_safely():
    # conftest already verified this separate local PostgreSQL cluster's
    # data directory and system identifier; create only our own random DB.
    name = 'ga_epoch_migration_' + uuid4().hex[:12]
    admin = create_engine(engine().url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    isolated = create_engine(engine().url.set(database=name))
    with admin.connect() as conn:
        conn.exec_driver_sql('CREATE DATABASE "' + name + '"')
    try:
        with isolated.begin() as conn:
            conn.execute(text('CREATE TABLE public.users (id varchar(36) PRIMARY KEY, active boolean NOT NULL)'))
            conn.execute(text('CREATE TABLE public.memberships (id varchar(36) PRIMARY KEY, user_id varchar(36) NOT NULL, workspace_id varchar(36) NOT NULL, role varchar(30) NOT NULL)'))
            conn.execute(text("INSERT INTO public.users (id,active) VALUES ('legacy-user',true)"))
            conn.execute(text("INSERT INTO public.memberships (id,user_id,workspace_id,role) VALUES ('legacy-member','legacy-user','legacy-workspace','admin')"))
        epochs.migrate(isolated)
        with isolated.connect() as conn:
            original = conn.execute(text('SELECT active,_ga_cloud2_auth_nonce,_ga_cloud2_auth_generation FROM public.users')).one()
            assert original[0] is True and original[1] is not None and original[2] == 1
        epochs.migrate(isolated)
        with isolated.begin() as conn:
            assert conn.execute(text('SELECT active,_ga_cloud2_auth_nonce,_ga_cloud2_auth_generation FROM public.users')).one() == original
            conn.execute(text('ALTER TABLE public.users DISABLE TRIGGER tr_ga_cloud2_user_epoch_v1'))
        with pytest.raises(RuntimeError, match='guard is missing or changed'):
            epochs.migrate(isolated)
        with isolated.connect() as conn:
            assert conn.execute(text('SELECT active,_ga_cloud2_auth_nonce,_ga_cloud2_auth_generation FROM public.users')).one() == original
    finally:
        isolated.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql('DROP DATABASE "' + name + '" WITH (FORCE)')
        admin.dispose()


def test_guard_missing_fails_closed_and_migration_never_silently_repairs(client, account, allowed):
    _, item = _create(client)
    with engine().begin() as conn:
        conn.execute(text('ALTER TABLE public.memberships DISABLE TRIGGER tr_ga_cloud2_membership_epoch_v1'))
    try:
        assert not _valid(account[2], item)
        listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
        assert listing['status'] == 'authorization_unavailable'
        assert '保留记录' in listing['notice']
        with pytest.raises(RuntimeError, match='guard is missing or changed'):
            epochs.migrate(engine())
    finally:
        with engine().begin() as conn:
            conn.execute(text('ALTER TABLE public.memberships ENABLE TRIGGER tr_ga_cloud2_membership_epoch_v1'))
    assert _valid(account[2], item)
    listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert listing['id'] == item['id'] and listing['status'] == 'not_verified'


def test_temporarily_unavailable_key_preserves_original_record_and_recovers(client, account, allowed, vault, monkeypatch, tmp_path):
    body, item = _create(client)
    with Session(engine()) as db:
        original_cipher = db.get(Job, item['id']).data['cipher']
    wrong_key = tmp_path / 'wrong-key'
    wrong_key.write_bytes(Fernet.generate_key()); wrong_key.chmod(0o600)
    for unavailable in (tmp_path / 'missing-key', wrong_key):
        monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE', str(unavailable))
        listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
        assert listing['status'] == 'authorization_unavailable' and '保留记录' in listing['notice']
        assert 'synthetic-http-secret' not in str(listing)
        recovered = client.get('/api/dji/cloud2/operations/'+body['request_id']).json()['connection']
        assert recovered['id'] == item['id'] and recovered['status'] == 'authorization_unavailable'
        with Session(engine()) as db:
            assert db.get(Job, item['id']).data['cipher'] == original_cipher
            assert not cloud2.is_current(db, account[2], item['id'], item['version'])
    monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE', str(vault))
    assert _valid(account[2], item)
    listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert listing['id'] == item['id'] and listing['status'] == 'not_verified'


def test_temporarily_disallowed_endpoint_preserves_and_recovers_connection(client, account, allowed, monkeypatch):
    body, item = _create(client)
    monkeypatch.setenv('GA_DJI_CLOUD2_ENDPOINTS', '192.168.0.10:444')
    listing = client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert listing['status'] == 'authorization_unavailable' and '保留记录' in listing['notice']
    assert client.get('/api/dji/cloud2/operations/'+body['request_id']).json()['connection']['status'] == 'authorization_unavailable'
    assert not _valid(account[2], item)
    monkeypatch.setenv('GA_DJI_CLOUD2_ENDPOINTS', '192.168.0.10:443')
    assert _valid(account[2], item)
    assert client.get('/api/dji/cloud2/connections').json()['connections'][0]['status'] == 'not_verified'


def test_original_create_receipt_reflects_later_revoke(client, account, allowed):
    body, item = _create(client)
    result = client.post('/api/dji/cloud2/connections/'+item['id']+'/revoke', json={
        'request_id':str(uuid4()), 'version':item['version'], 'confirm':True})
    assert result.status_code == 200
    current = client.get('/api/dji/cloud2/operations/'+body['request_id']).json()
    assert current['state'] == 'succeeded' and current['connection']['id'] == item['id']
    assert current['connection']['enabled'] is False and current['connection']['status'] == 'revoked'
    assert current['connection']['version'] == item['version'] + 1
    assert client.post('/api/dji/cloud2/connections', json=body).json() == current
    assert client.post('/api/dji/cloud2/operations/'+body['request_id']+'/cancel').json() == current


def test_narrowed_update_trigger_is_not_accepted_as_full_guard(client, account, allowed):
    _, item = _create(client)
    with engine().begin() as conn:
        conn.execute(text('DROP TRIGGER tr_ga_cloud2_membership_epoch_v1 ON public.memberships'))
        conn.execute(text('CREATE TRIGGER tr_ga_cloud2_membership_epoch_v1 BEFORE INSERT OR UPDATE OF role ON public.memberships FOR EACH ROW EXECUTE FUNCTION public.ga_cloud2_membership_epoch_v1()'))
    try:
        assert not _valid(account[2], item)
        with pytest.raises(RuntimeError, match='guard is missing or changed'):
            epochs.migrate(engine())
    finally:
        with engine().begin() as conn:
            conn.execute(text('DROP TRIGGER tr_ga_cloud2_membership_epoch_v1 ON public.memberships'))
            conn.execute(text('CREATE TRIGGER tr_ga_cloud2_membership_epoch_v1 BEFORE INSERT OR UPDATE ON public.memberships FOR EACH ROW EXECUTE FUNCTION public.ga_cloud2_membership_epoch_v1()'))
    assert _valid(account[2], item)


def test_guard_with_changed_search_path_is_not_trusted(client, account, allowed):
    _, item = _create(client)
    with engine().begin() as conn:
        conn.execute(text('ALTER FUNCTION public.ga_cloud2_user_epoch_v1() SET search_path = public'))
    try:
        assert not _valid(account[2], item)
        with pytest.raises(RuntimeError, match='guard is missing or changed'):
            epochs.migrate(engine())
    finally:
        with engine().begin() as conn:
            conn.execute(text('ALTER FUNCTION public.ga_cloud2_user_epoch_v1() SET search_path = pg_catalog'))
    assert _valid(account[2], item)


def test_missing_migration_marker_cannot_silently_reinstall_guards(client, account, allowed):
    _, item = _create(client)
    with engine().begin() as conn:
        conn.execute(text('DROP TABLE public.ga_cloud2_auth_epoch_schema_v1'))
    try:
        assert not _valid(account[2], item)
        with pytest.raises(RuntimeError, match='migration state is ambiguous'):
            epochs.migrate(engine())
    finally:
        with engine().begin() as conn:
            conn.execute(text('CREATE TABLE public.ga_cloud2_auth_epoch_schema_v1 (version integer PRIMARY KEY CHECK (version=1))'))
    assert _valid(account[2], item)


def test_ordinary_writer_cannot_reset_epoch_or_nonce(client, account, allowed):
    _, item = _create(client)
    with Session(engine()) as db:
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE public.memberships SET _ga_cloud2_auth_generation=2 WHERE workspace_id=:w"), {'w':account[2]})
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE public.users SET _ga_cloud2_auth_nonce=pg_catalog.gen_random_uuid() WHERE username=:u"), {'u':account[0]})
            db.commit()
    assert _valid(account[2], item)


def test_permission_update_serializes_with_in_flight_result(client, account, allowed):
    _, item = _create(client)
    with Session(engine()) as db:
        actor = db.get(Job, item['id']).data['actor_id']
        epochs.actor_identity(db, account[2], actor)
        def change_role():
            with Session(engine()) as other:
                member = other.scalar(select(Membership).where(Membership.workspace_id == account[2]))
                member.role = 'viewer'; other.flush()
                member.role = 'admin'; other.flush(); other.commit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(change_role)
            sleep(.2); assert not future.done()
            assert cloud2.publish_state(db, account[2], item['id'], item['version'], 'connected', 1)
            db.commit(); future.result(timeout=5)
    with Session(engine()) as db:
        assert not cloud2.publish_state(db, account[2], item['id'], item['version'], 'connected', 2)
    assert not _valid(account[2], item)
