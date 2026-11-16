"""Owned PostgreSQL two-connection ACK interleavings, authored but not run here.

Only the root's external --noconftest fixture plugin on a fresh owned port may
execute these cases (first57528). All data/accounts are synthetic dual_scene.
No model, service, browser, normal DB, old paid row or runtime is used.
"""
import copy
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import signal_dual_api as api, signal_dual_handoffs as dual
from generic_agents import signal_dual_read_session as reads, workspace_api as workspace
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine, session as legacy_session
from generic_agents.models import AuthSession, Base, CloudCall, Membership, Task, User, DomainInstance, InstanceRelease
from generic_agents.security import Principal, authenticated
from generic_agents.signal_dual_models import SignalDualHandoff
from tests.test_signal_dual_handoffs import (
    dual_scene, receive_parent, dispatch_slot, task_detail, ack_body,
    routing_path, handoff_path, require_reply,
)


@pytest.fixture(autouse=True)
def owned_database_only():
    url = make_url(settings().database_url)
    marker = os.environ.get('GA_F11_DUAL_TESTS_OWNED','')
    assert marker.isascii() and marker.isdecimal()
    port = int(marker)
    assert 56828 <= port <= 60028 and port % 100 == 28
    assert url.host in {'127.0.0.1','localhost'} and url.port == port
    assert url.database == 'ga_f11_dual_tests'


def persisted_state():
    """All 127 tables' opaque rows and all synthetic files; never decode paid content."""
    # Additive migrations expanded the schema; equality still covers every table.
    expected = set(Base.metadata.tables)
    assert len(expected) == 127
    with Session(engine()) as db:
        # Refuse any nonempty paid ledger before the opaque snapshot. This test
        # operates only a fresh synthetic schema, never a historical clone.
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    with engine().connect() as connection:
        actual = set(connection.scalars(text(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")))
        assert actual == expected
        rows = {name:tuple(connection.scalars(text('SELECT row_to_json(t)::text FROM "'+name+'" t ORDER BY t.id')))
                for name in sorted(expected)}
    directory = settings().storage_dir
    files = {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in directory.rglob('*') if p.is_file()}
    return rows,files


@pytest.fixture
def response_scene(dual_scene):
    _,_,parent = receive_parent(dual_scene)
    parent = dispatch_slot(dual_scene,parent,'response')
    task = task_detail(dual_scene,parent,'response')
    assert parent['slots']['response']['ack'] is None
    assert parent['slots']['verification']['state'] == 'pending'
    return dual_scene,parent,task


class Interleaving:
    def __init__(self):
        self.barrier = threading.Barrier(2)
        self.committed = threading.Event()
        self.triggered = False
        self.reader_pid = None
        self.writer_pid = None
        self.isolation = None
        self.read_only = None

    def pause(self,db):
        if db.info.get('dual_read_race') is not self or self.triggered: return
        self.triggered = True
        self.reader_pid = db.scalar(text('SELECT pg_backend_pid()'))
        self.isolation = db.scalar(text('SHOW transaction_isolation'))
        self.read_only = db.scalar(text('SHOW transaction_read_only'))
        self.barrier.wait(timeout=15)
        assert self.committed.wait(timeout=30), 'actual ACK writer did not complete'


@contextmanager
def read_mode(mode,race=None,observations=None):
    """RC is the former behavior as an explicit failing negative control."""
    def dependency(p:Principal=Depends(authenticated)):
        generator = reads.read_session(p) if mode == 'repeatable_read' else legacy_session()
        try:
            db = next(generator)
            if race: db.info['dual_read_race'] = race
            if observations is not None:
                observations.append((db.scalar(text('SHOW transaction_isolation')),
                                     db.scalar(text('SHOW transaction_read_only'))))
            yield db
        finally:
            generator.close()
    missing = object(); prior = app.dependency_overrides.get(reads.read_session,missing)
    app.dependency_overrides[reads.read_session] = dependency
    try:
        yield
    finally:
        if prior is missing: app.dependency_overrides.pop(reads.read_session,None)
        else: app.dependency_overrides[reads.read_session] = prior


@pytest.mark.parametrize('mode',['read_committed','repeatable_read'])
@pytest.mark.parametrize('surface',['handoff','inbox','task_detail'])
def test_ack_between_real_parent_and_task_reads_is_one_snapshot(response_scene,monkeypatch,mode,surface):
    scene,parent,task = response_scene; race = Interleaving()
    original_view,original_verify,original_ack = dual.parent_view,dual.verify_task,dual.acknowledge

    def view(db,p,row):
        # Parent/inbox has loaded the old parent, but not its Task. Committing
        # ACK now used to combine old parent.ack with new Task.dual_response_ack.
        if surface != 'task_detail' and row.id == parent['id']: race.pause(db)
        return original_view(db,p,row)

    def verify(db,p,row):
        # Task detail first loads its old Task. Commit before its first parent
        # lookup to reproduce the reverse direction of that same torn read.
        if surface == 'task_detail' and row.id == task['id']: race.pause(db)
        return original_verify(db,p,row)

    def acknowledge(db,p,pid,body):
        if pid == parent['id'] and p.user_id == scene['members']['response']['id']:
            race.writer_pid = db.scalar(text('SELECT pg_backend_pid()'))
        return original_ack(db,p,pid,body)

    monkeypatch.setattr(dual,'parent_view',view)
    monkeypatch.setattr(dual,'verify_task',verify)
    monkeypatch.setattr(dual,'acknowledge',acknowledge)
    paths = {'handoff':handoff_path(parent['id']),
             'inbox':'/api/workspace/instances/'+scene['release']['instance_id']+'/signal-handoffs?mine_only=false',
             'task_detail':'/api/workspace/tasks/'+task['id']}
    command = ack_body(parent,task)
    with read_mode(mode,race),ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(scene['admin'].get,paths[surface])
        try:
            race.barrier.wait(timeout=15)
            saved = require_reply(scene['actors']['response'].post(handoff_path(parent['id'])+'/response/ack',json=command))
            assert saved['receipt']['accepted_task_version'] == task['version']+1
            after_ack = persisted_state()
        finally:
            race.committed.set()
        reply = future.result(timeout=30)
    assert race.triggered and race.reader_pid != race.writer_pid and race.writer_pid is not None
    assert race.isolation == ('repeatable read' if mode == 'repeatable_read' else 'read committed')
    assert race.read_only == ('on' if mode == 'repeatable_read' else 'off')
    assert persisted_state() == after_ack, 'the GET must not repair or mutate any data/file'
    if mode == 'read_committed' and surface == 'task_detail':
        assert reply.status_code == 409
    else:
        value = require_reply(reply)
        if surface == 'inbox':
            assert value['integrity_failures'] == []
            value = next(item for item in value['items'] if item['id'] == parent['id'])
        elif surface == 'task_detail':
            assert value['version'] == task['version']
            value = value['dual_handoff_view']
        if mode == 'read_committed':
            assert value['slots']['response']['integrity_issue']
            assert value['slots']['response']['can_open_task'] is False
        else:
            assert value['version'] == parent['version']
            assert value['slots']['response']['task_version'] == task['version']
            assert value['slots']['response']['ack'] is None
            assert value['slots']['response']['integrity_issue'] is None
            assert value['slots']['response']['can_open_task'] is True
    # A later genuine GET observes the completely saved new ACK, rather than
    # silently concealing it or depending on a lucky ORM identity-map cache.
    latest = require_reply(scene['admin'].get(handoff_path(parent['id'])))
    assert latest['slots']['response']['ack']['receipt_hash'] == saved['receipt']['receipt_hash']
    assert latest['slots']['response']['task_version'] == task['version']+1
    assert latest['slots']['response']['integrity_issue'] is None
    assert persisted_state() == after_ack


def test_every_dual_get_including_options_is_readonly_and_changes_no_rows_or_files(response_scene):
    scene,parent,task = response_scene
    body = ack_body(parent,task)
    saved = require_reply(scene['actors']['response'].post(handoff_path(parent['id'])+'/response/ack',json=body))
    assert saved['receipt']['accepted_task_version'] == task['version']+1
    before = persisted_state(); observations = []
    path = routing_path(scene['release'])
    requests = [(scene['admin'],path+'/dual-options'),(scene['admin'],path+'/dual'),
        (scene['admin'],path+'/dual/requests/'+scene['command']['request_id']),
        (scene['admin'],'/api/workspace/instances/'+scene['release']['instance_id']+'/signal-handoffs?mine_only=false'),
        (scene['admin'],handoff_path(parent['id'])),
        (scene['actors']['response'],handoff_path(parent['id'])+'/response/ack-requests/'+body['request_id']),
        (scene['actors']['response'],'/api/workspace/tasks/'+task['id'])]
    statements = []
    def record(connection,cursor,statement,parameters,context,executemany): statements.append(statement.upper())
    event.listen(engine(),'before_cursor_execute',record)
    try:
        with read_mode('repeatable_read',observations=observations):
            for client,path in requests: require_reply(client.get(path))
    finally:
        event.remove(engine(),'before_cursor_execute',record)
    assert observations == [('repeatable read','on')]*len(requests)
    assert not any('FOR UPDATE' in value or 'FOR SHARE' in value for value in statements)
    assert persisted_state() == before


@pytest.mark.parametrize('lock_sources',[True,False])
def test_entity_context_keeps_explicit_source_lock_mode_on_followup_release_lookup(response_scene,lock_sources):
    from generic_agents.entity_release_compat import EntityReleaseContext
    scene,parent,task = response_scene; before = persisted_state(); statements = []
    principal = Principal(scene['members']['response']['id'],scene['workspace_id'],'operator','owned-test')
    def record(connection,cursor,statement,parameters,context,executemany): statements.append(statement.upper())
    generator = legacy_session() if lock_sources else reads.read_session(principal)
    event.listen(engine(),'before_cursor_execute',record)
    try:
        db = next(generator)
        instance = db.get(DomainInstance,scene['release']['instance_id'])
        release = db.get(InstanceRelease,scene['release']['id'])
        context = (EntityReleaseContext(db,principal,instance,release) if lock_sources else
                   EntityReleaseContext(db,principal,instance,release,lock_sources=False))
        # Exercise the same cached-context traversal used for ancestor releases:
        # a later _get_release must retain the mode, not default back to a lock.
        context.releases.clear()
        assert context._get_release(release.id).id == release.id
    finally:
        generator.close(); event.remove(engine(),'before_cursor_execute',record)
    row_locks = [value for value in statements if 'FOR UPDATE' in value or 'FOR SHARE' in value]
    assert len(row_locks) >= 2 if lock_sources else not row_locks
    assert persisted_state() == before


def test_existing_native_goal_task_detail_is_readonly_but_definition_post_retains_source_locks(client):
    from tests.test_native_goals import setup, define
    _,_,task,base,body = setup(client)
    statements = []
    def record(connection,cursor,statement,parameters,context,executemany): statements.append(statement.upper())
    event.listen(engine(),'before_cursor_execute',record)
    try:
        declared = define(client,base,body)
        assert any('FOR UPDATE' in value and 'CAPABILITIES' in value for value in statements)
        with Session(engine()) as db:
            assert db.get(Task,task['id']).data.get('native_goal_context')
        before = persisted_state(); statements.clear()
        detail = require_reply(client.get('/api/workspace/tasks/'+task['id']))
        assert detail['version'] == declared['version']
        assert not any('FOR UPDATE' in value or 'FOR SHARE' in value for value in statements)
        assert persisted_state() == before
    finally:
        event.remove(engine(),'before_cursor_execute',record)


@pytest.mark.parametrize('damage',['task_marker','parent_hash','ack_receipt'])
def test_real_damage_is_still_rejected_or_explicitly_isolated_without_get_repair(response_scene,damage):
    scene,parent,task = response_scene
    if damage == 'ack_receipt':
        require_reply(scene['actors']['response'].post(handoff_path(parent['id'])+'/response/ack',json=ack_body(parent,task)))
    with Session(engine()) as db:
        row = db.get(SignalDualHandoff,parent['id'])
        if damage == 'task_marker':
            row = db.get(Task,task['id']); value = copy.deepcopy(row.data)
            value['dual_handoff']['snapshot_hash'] = '0'*64; row.data = value
        else:
            value = copy.deepcopy(row.data)
            if damage == 'parent_hash': value['immutable_hash'] = '0'*64
            else: value['ack_requests'][0]['receipt']['receipt_hash'] = '0'*64
            row.data = value
        db.commit()
    before = persisted_state()
    assert scene['actors']['response'].get('/api/workspace/tasks/'+task['id']).status_code == 409
    reply = scene['admin'].get(handoff_path(parent['id']))
    if damage == 'task_marker':
        value = require_reply(reply)
        assert value['slots']['response']['integrity_issue'] and not value['slots']['response']['can_open_task']
    else:
        assert reply.status_code == 409
    assert persisted_state() == before


@pytest.mark.parametrize('revocation',['inactive_user','revoked_session','removed_membership','viewer_role','wrong_workspace'])
def test_separate_snapshot_never_bypasses_authentication_or_fresh_ack_authority(response_scene,revocation):
    scene,parent,task = response_scene; client = scene['actors']['response']
    user_id = scene['members']['response']['id']
    with Session(engine()) as db:
        if revocation == 'inactive_user': db.get(User,user_id).active = False
        elif revocation == 'revoked_session':
            for row in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id)): row.revoked = True
        elif revocation in {'removed_membership','viewer_role'}:
            row = db.scalar(select(Membership).where(Membership.workspace_id == scene['workspace_id'],Membership.user_id == user_id))
            if revocation == 'removed_membership': db.delete(row)
            else: row.role = 'viewer'
        db.commit()
    before = persisted_state(); path = handoff_path(parent['id'])
    if revocation == 'wrong_workspace':
        assert client.get(path,headers={'X-Workspace-ID':str(uuid4())}).status_code == 403
    elif revocation == 'viewer_role':
        value = require_reply(client.get(path)); assert value['can_ack_response'] is False
        assert client.post(path+'/response/ack',json=ack_body(parent,task)).status_code == 403
    else:
        expected = 401 if revocation in {'inactive_user','revoked_session'} else 403
        assert client.get(path).status_code == expected
        assert client.post(path+'/response/ack',json=ack_body(parent,task)).status_code == expected
    assert persisted_state() == before


def test_options_read_actor_excludes_revoked_recipient_without_locking_or_writing(dual_scene):
    scene = dual_scene
    with Session(engine()) as db:
        row = db.scalar(select(Membership).where(Membership.workspace_id == scene['workspace_id'],
            Membership.user_id == scene['members']['verification']['id']))
        row.role = 'viewer'; db.commit()
    before = persisted_state()
    value = require_reply(scene['admin'].get(routing_path(scene['release'])+'/dual-options'))
    assert not value['can_enable']
    assert all(b['recipient']['user_id'] != scene['members']['verification']['id'] for b in value['businesses'])
    assert persisted_state() == before


def test_route_dependencies_keep_posts_writable_and_only_task_detail_changes():
    found = set()
    for route in api.router.routes:
        if not isinstance(route,APIRoute): continue
        calls = {d.call for d in route.dependant.dependencies}
        if 'GET' in route.methods:
            found.add(route.endpoint.__name__)
            assert reads.read_session in calls and legacy_session not in calls
        else:
            assert legacy_session in calls and reads.read_session not in calls
    assert found == {'options','request','routes','inbox','inbox_v2','handoff','acknowledgement'}
    detail_routes = []
    # The exported router wraps this original registry using include_router.
    # Inspect the actual workspace definitions, including the unique detail.
    for route in workspace.workspace_routes.routes:
        if not isinstance(route,APIRoute): continue
        if route.endpoint.__module__ != workspace.__name__: continue
        calls = {d.call for d in route.dependant.dependencies}
        if route.endpoint is workspace.detail:
            detail_routes.append(route)
            assert reads.read_session in calls and legacy_session not in calls
        else:
            assert reads.read_session not in calls
    assert len(detail_routes) == 1


def test_first_sql_readonly_database_rejects_accidental_write_and_pool_default_is_not_changed(response_scene):
    scene,parent,task = response_scene; statements = []
    before = persisted_state()
    def record(connection,cursor,statement,parameters,context,executemany): statements.append(statement)
    event.listen(engine(),'before_cursor_execute',record)
    generator = reads.read_session(Principal(scene['members']['response']['id'],scene['workspace_id'],'operator','owned-test'))
    try:
        db = next(generator)
        assert statements[0].strip().upper() == 'SET TRANSACTION READ ONLY'
        assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        with pytest.raises(DBAPIError) as failure:
            db.execute(text('UPDATE tasks SET version=version WHERE false'))
        assert (getattr(failure.value.orig,'sqlstate',None) or getattr(failure.value.orig,'pgcode',None)) == '25006'
    finally:
        generator.close(); event.remove(engine(),'before_cursor_execute',record)
    with Session(engine()) as db:
        assert db.scalar(text('SHOW transaction_isolation')) == 'read committed'
        assert db.scalar(text('SHOW transaction_read_only')) == 'off'
    assert persisted_state() == before
