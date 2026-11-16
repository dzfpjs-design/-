"""M2 explicit negative fence over real PG/API, NOT a no-send proof.

Reuses the frozen M2 fixture's explicitly SYNTHETIC immutable-source admission
and HTTP transport boundaries. Real Auth/Membership, Catalogue/Binding, Start,
Job, nonce locks, CloudCall/capture and independent Audit histories are not
mocked or seeded as accepted. A closure blocks future Start; it never means a
previous request could not have left the browser, nor refunds a paid call.
Root alone executes these tests on its disposable PostgreSQL database.
"""
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from generic_agents import models as m
from generic_agents import catalogue_current_allocation_v260 as allocation
from generic_agents import catalogue_current_allocation_closure_v260 as closure
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents.contracts import digest
from tests.test_catalogue_current_allocation_v260 import (
    allocation_scene, reuse_scene, quote, start_body, start, finish, owned_inventory,
    rows, BASE)
from tests.test_catalogue_current_context_v260 import (
    catalogue, save_document, other_client, ok, raw, h, ids)


@pytest.fixture
def attempted_scene(allocation_scene):
    """A REAL HTTP Start attempt rejected by normal saved-document CAS.

    The browser still retains its exact original request. A subsequent lookup
    returning found=false is intentionally NOT treated as a no-send proof.
    No successful Command, Consumer or closure receipt is manufactured.
    """
    s = allocation_scene
    body, preview = quote(s)
    request = start_body(body, preview); original = raw(request)
    working = catalogue(s, s.working_id); document = copy.deepcopy(working['document'])
    document['businesses'][0]['purpose'] += '；真实保存推进CAS后保留旧申请'
    save_document(s, working, document)
    before = owned_inventory(s)
    response = s.client.post(BASE + '/start-allocation', content=original,
        headers={'Content-Type': 'application/json'})
    assert response.status_code == 409, response.text
    assert owned_inventory(s) == before and not s.posts
    lookup = ok(s.client.get(BASE + '/requests/' + request['request_id'],
        params={'expected_sha256': h(original)}))
    assert lookup['found'] is False and lookup['result'] is None
    s.attempted = {'body': body, 'preview': preview, 'request': request, 'raw': original}
    return s


def post_bytes(s, path, original):
    return s.client.post(path, content=original, headers={'Content-Type': 'application/json'})


def race_at_actual_nonce(s, request_id, first_request, later_request, monkeypatch):
    """Observe the REAL PG winner lock; never synthesize a committed result.

    Catalogue locking may serialize the second writer even before its nonce.
    pg_blocking_pids must prove that the second DB transaction is waiting on
    this winner, rather than treating two merely scheduled Futures as a race.
    """
    real_nonce = consumer._nonce
    assert s.bound.dialect.name == 'postgresql' and s.bound.url.database.startswith('ga_test_')
    # Two real HTTP writers may occupy the entire application pool. Observe
    # their real locks through a separate bounded READ ONLY connection, never
    # a third checkout that makes the winner time out before observation.
    observer = create_engine(s.bound.url, poolclass=NullPool, connect_args={
        'connect_timeout': 2,
        'options': '-c default_transaction_read_only=on -c statement_timeout=2000 -c lock_timeout=2000'})
    acquired, release = Event(), Event()
    mutex = Lock(); owner_pid = [None]
    def observed(db, p, nonce, *, shared=False):
        real_nonce(db, p, nonce, shared=shared)
        with mutex:
            hold = nonce == request_id and not shared and owner_pid[0] is None
            if hold:
                owner_pid[0] = db.scalar(text('SELECT pg_backend_pid()'))
        if hold:
            acquired.set()
            assert release.wait(15), 'bounded original nonce observation timed out'
    def bounded(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout='20s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='30s'")
    monkeypatch.setattr(consumer, '_nonce', observed)
    event.listen(s.bound, 'begin', bounded)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_request)
            try:
                assert acquired.wait(8), 'first real request never acquired its nonce'
                later = pool.submit(later_request)
                blocked = False; deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not later.done():
                    with Session(observer) as db:
                        blocked = db.scalar(text('SELECT EXISTS(SELECT 1 FROM pg_stat_activity a '
                            "WHERE a.wait_event_type='Lock' AND :pid = ANY(pg_blocking_pids(a.pid)))"),
                            {'pid': owner_pid[0]})
                    if blocked:
                        break
                    time.sleep(.02)
                assert blocked and not later.done(), 'second request did not wait on the real winner transaction'
            finally:
                release.set()
            return first.result(timeout=40), later.result(timeout=40)
    finally:
        event.remove(s.bound, 'begin', bounded)
        observer.dispose()


def close_request(s, attempted=None, *, reason='保留本次原申请，明确禁止今后按此原号启动；不声称过去未发送。😀'):
    attempted = s.attempted if attempted is None else attempted
    return {'schema': 'catalogue-current-allocation-close-v260',
        'original_raw': attempted['raw'].decode('utf-8'),
        'original_sha256': h(attempted['raw']), 'binding_id': s.binding['id'],
        'binding_record_hash': s.binding['record_hash'], 'confirm_close': True, 'reason': reason}


def close_original(s, attempted=None, *, client=None, body=None, original=None):
    attempted = s.attempted if attempted is None else attempted
    body = close_request(s, attempted) if body is None else body
    original = raw(body) if original is None else original
    response = (client or s.client).post(closure.close_path(attempted['request']['request_id']),
        content=original, headers={'Content-Type': 'application/json'})
    return body, original, response


def original_lookup(s, attempted=None, *, client=None):
    attempted = s.attempted if attempted is None else attempted
    return (client or s.client).get(BASE + '/requests/' + attempted['request']['request_id'],
        params={'expected_sha256': h(attempted['raw'])})


def checked_receipt(s, attempted, close_body, close_raw, receipt):
    """Assert the actual handler-created immutable witnesses, not fake rows."""
    fields = {'schema', 'id', 'version', 'workspace_id', 'actor_id', 'request_id',
        'binding_id', 'binding_record_hash', 'original_sha256', 'original_body_hash',
        'close_sha256', 'close_body_hash', 'created_at', 'reason',
        'prevents_future_start', 'past_dispatch', 'refunds_authorized', 'record_hash'}
    assert set(receipt) == fields and receipt['schema'] == 'catalogue-current-allocation-closed-v260'
    nonce = attempted['request']['request_id']
    assert receipt['id'] == closure.fence_identity(s.p.workspace_id, nonce)
    assert receipt['request_id'] == nonce and receipt['actor_id'] == s.p.user_id
    assert receipt['prevents_future_start'] is True and receipt['past_dispatch'] == 'not_asserted'
    assert receipt['refunds_authorized'] is False and receipt['version'] == 1
    assert receipt['original_sha256'] == h(attempted['raw'])
    assert receipt['original_body_hash'] == digest(allocation.StartBody.model_validate(attempted['request']).model_dump())
    assert receipt['close_sha256'] == h(close_raw)
    assert receipt['close_body_hash'] == digest(closure.CloseBody.model_validate(close_body).model_dump())
    with Session(s.bound) as db:
        fence = db.get(m.Job, receipt['id'])
        assert fence is not None and fence.kind == closure.JOB_KIND and fence.status == 'internal'
        assert fence.idempotency_key == 'saved-map-design:' + nonce
        assert fence.attempts == 0 and fence.lease_token is None and fence.lease_until is None
        record = fence.data
        assert record['original_raw'].encode() == attempted['raw'] and record['close_raw'].encode() == close_raw
        assert record['record_hash'] == receipt['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'})
        anchors = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == closure.AUDIT_ACTION, m.Audit.object_id == fence.id)))
        assert len(anchors) == 1 and anchors[0].actor_id == s.p.user_id
        assert anchors[0].data['record_hash'] == record['record_hash']
        assert anchors[0].data['original_sha256'] == h(attempted['raw'])
    return receipt


def test_real_cas_rejection_can_be_closed_recovered_and_only_explicit_new_activity_can_start(attempted_scene):
    s = attempted_scene; old = s.attempted; calls = rows(s, m.CloudCall)
    parent = catalogue(s, s.parent_id); working = catalogue(s, s.working_id)
    body, original, response = close_original(s)
    receipt = checked_receipt(s, old, body, original, ok(response))
    snapshot = owned_inventory(s)
    lookup = ok(original_lookup(s))
    assert set(lookup) == {'schema', 'workspace_id', 'actor_id', 'request_id',
        'original_sha256', 'closed', 'receipt', 'original_raw', 'close_raw'}
    assert lookup['schema'] == 'catalogue-current-allocation-closed-request-v260' and lookup['closed'] is True
    assert lookup['receipt'] == receipt and lookup['original_raw'].encode() == old['raw']
    assert lookup['close_raw'].encode() == original
    assert ok(close_original(s, body=body, original=original)[2]) == receipt
    late = post_bytes(s, BASE + '/start-allocation', old['raw'])
    assert late.status_code == 409 and late.json()['detail'] == lookup
    assert owned_inventory(s) == snapshot and rows(s, m.CloudCall) == calls and not s.posts
    assert catalogue(s, s.parent_id)['document'] == parent['document']
    assert catalogue(s, s.working_id)['document'] == working['document']
    # Closing did not authorize or generate this separate human activity.
    new_body, preview = quote(s)
    assert new_body['activity_id'] != old['request']['activity_id']
    _, _, created = start(s, new_body, preview)
    assert created['status'] == 'queued' and not s.posts and rows(s, m.CloudCall) == calls
    # Its normal fresh gate still applies after real configuration advance.
    from tests.test_catalogue_current_context_v260 import advance
    advance(s, '关闭后配置前进仍不得借旧目标分派')
    before = owned_inventory(s)
    request = {**new_body, 'activity_id': str(uuid4())}
    assert post_bytes(s, BASE + '/preview-allocation', raw(request)).status_code == 409
    assert owned_inventory(s) == before and ok(original_lookup(s))['receipt'] == receipt


def test_same_original_nonce_cannot_change_reason_outer_bytes_or_original_start_bytes(attempted_scene):
    s = attempted_scene; body, original, response = close_original(s); receipt = ok(response)
    before = owned_inventory(s)
    candidates = [(dict(body, reason='另一份明确人稿不能替换已保存的结束原因'), None),
        (body, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()),
        ({**body, 'original_raw': body['original_raw'] + '\n',
            'original_sha256': h((body['original_raw'] + '\n').encode())}, None)]
    for changed, changed_raw in candidates:
        result = close_original(s, body=changed, original=changed_raw)[2]
        assert result.status_code == 409
        assert owned_inventory(s) == before
    assert ok(original_lookup(s))['receipt'] == receipt and not s.posts


def test_viewer_owner_can_first_close_and_read_history_but_foreign_actor_cannot_claim_it(attempted_scene):
    s = attempted_scene
    # A real current Membership downgrade, not a forged Principal or bypassed
    # IAM guard. Normal login below observes the persisted viewer role.
    with Session(s.bound) as db:
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.p.workspace_id,
            m.Membership.user_id == s.p.user_id))
        assert member.role == 'admin'; member.role = 'viewer'; db.commit()
    with other_client(s, same_actor=True) as viewer:
        assert ok(viewer.get('/api/me'))['role'] == 'viewer'
        body, original, result = close_original(s, client=viewer)
        receipt = checked_receipt(s, s.attempted, body, original, ok(result))
        before = owned_inventory(s)
        assert ok(original_lookup(s, client=viewer))['receipt'] == receipt
        assert viewer.post(BASE + '/preview-allocation', json=s.attempted['body']).status_code == 403
        assert owned_inventory(s) == before
    with other_client(s) as foreign:
        before = owned_inventory(s)  # Includes that legitimate login's Job.
        for result in (original_lookup(s, client=foreign), close_original(s, client=foreign)[2]):
            assert result.status_code in {403, 409}
            assert body['reason'] not in result.text and body['original_raw'] not in result.text
        assert owned_inventory(s) == before and not s.posts


@pytest.mark.parametrize('damage', ['job-removed', 'audit-removed', 'job-key-stripped'])
def test_fence_and_independent_audit_never_downgrade_to_lookup_false_or_late_start(attempted_scene, damage):
    s = attempted_scene; _, _, response = close_original(s); receipt = ok(response)
    with Session(s.bound) as db:
        fence = db.get(m.Job, receipt['id'])
        audit = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == closure.AUDIT_ACTION, m.Audit.object_id == fence.id))
        assert audit is not None
        if damage == 'job-removed': db.delete(fence)
        elif damage == 'audit-removed': db.delete(audit)
        else: fence.idempotency_key = 'stripped:' + s.attempted['request']['request_id']
        db.commit()
    before = owned_inventory(s)
    assert original_lookup(s).status_code == 409
    assert post_bytes(s, BASE + '/start-allocation', s.attempted['raw']).status_code == 409
    assert close_original(s)[2].status_code == 409
    assert owned_inventory(s) == before and not s.posts


def remove_real_start_anchors(s, created, *, retain_state=False):
    """Explicit integrity-corruption fixture, never a production repair."""
    with Session(s.bound) as db:
        command_ids = list(db.scalars(select(consumer.Command.id).where(
            consumer.Command.workspace_id == s.p.workspace_id, consumer.Command.consumer_id == created['id'])))
        assert len(command_ids) == 1
        states = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == consumer.STATE_AUDIT, m.Audit.object_id == created['id'])))
        assert states
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            ((m.Audit.action == consumer.COMMAND_AUDIT) & m.Audit.object_id.in_(command_ids))
            | ((m.Audit.action == consumer.STATE_AUDIT) & (m.Audit.object_id == created['id'])))))
        for anchor in audits:
            if not retain_state or anchor.action != consumer.STATE_AUDIT:
                db.delete(anchor)
        db.execute(delete(consumer.Command).where(consumer.Command.id.in_(command_ids)))
        row = db.get(consumer.Consumer, created['id']); assert row is not None; db.delete(row); db.flush()
        job = db.get(m.Job, created['job_id']); assert job is not None; db.delete(job); db.commit()
        return [anchor.id for anchor in states] if retain_state else []


def test_only_original_state_audit_remaining_blocks_close_without_reconstructing_a_consumer(allocation_scene):
    s = allocation_scene; body, preview = quote(s); request, original, created = start(s, body, preview)
    attempted = {'body': body, 'preview': preview, 'request': request, 'raw': original}
    witness_ids = remove_real_start_anchors(s, created, retain_state=True)
    before = owned_inventory(s)
    assert original_lookup(s, attempted).status_code == 409
    assert close_original(s, attempted)[2].status_code == 409
    assert owned_inventory(s) == before and not s.posts
    with Session(s.bound) as db:
        assert all(db.get(m.Audit, identity) is not None for identity in witness_ids)


def test_only_real_current_cloud_call_remaining_blocks_false_none_and_preserves_charges(allocation_scene):
    s = allocation_scene; body, preview = quote(s); request, original, created = start(s, body, preview)
    view = finish(s, created, preview)
    assert view['reserved_call_id'] is not None and len(s.posts) == 1
    attempted = {'body': body, 'preview': preview, 'request': request, 'raw': original}
    calls_before = rows(s, m.CloudCall)
    remove_real_start_anchors(s, created)
    before = owned_inventory(s)
    assert original_lookup(s, attempted).status_code == 409
    assert close_original(s, attempted)[2].status_code == 409
    assert post_bytes(s, BASE + '/start-allocation', original).status_code == 409
    assert rows(s, m.CloudCall) == calls_before and owned_inventory(s) == before and len(s.posts) == 1


@pytest.mark.parametrize('winner', ['start', 'close'])
def test_actual_start_and_close_serialize_at_the_original_nonce_and_only_one_can_win(allocation_scene, monkeypatch, winner):
    s = allocation_scene; body, preview = quote(s); request = start_body(body, preview); original = raw(request)
    attempted = {'body': body, 'preview': preview, 'request': request, 'raw': original}
    end_body = close_request(s, attempted); end_raw = raw(end_body)
    start_call = lambda: post_bytes(s, BASE + '/start-allocation', original)
    close_call = lambda: post_bytes(s, closure.close_path(request['request_id']), end_raw)
    first, second = race_at_actual_nonce(s, request['request_id'],
        start_call if winner == 'start' else close_call,
        close_call if winner == 'start' else start_call, monkeypatch)
    with Session(s.bound) as db:
        consumers = list(db.scalars(select(consumer.Consumer).where(
            consumer.Consumer.workspace_id == s.p.workspace_id,
            consumer.Consumer.actor_id == s.p.user_id, consumer.Consumer.request_id == request['request_id'])))
        fences = list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.p.workspace_id,
            m.Job.kind == closure.JOB_KIND)))
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == closure.AUDIT_ACTION)))
    if winner == 'start':
        accepted = ok(first, 202)
        assert ok(second) == accepted and len(consumers) == 1 and fences == audits == []
        found = ok(original_lookup(s, attempted))
        assert found['found'] is True and found['result'] == accepted
        assert found['schema'] == 'report-saved-map-design-request-v176'
    else:
        receipt = checked_receipt(s, attempted, end_body, end_raw, ok(first))
        assert second.status_code == 409 and second.json()['detail']['receipt'] == receipt
        assert consumers == [] and len(fences) == len(audits) == 1
        assert ok(original_lookup(s, attempted))['receipt'] == receipt
    assert not s.posts and ids(s, m.CloudCall) == s.calls


def string_values(value):
    if type(value) is str:
        yield value
    elif type(value) is dict:
        for child in value.values():
            yield from string_values(child)
    elif type(value) is list:
        for child in value:
            yield from string_values(child)


def dictionaries(value):
    if type(value) is dict:
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif type(value) is list:
        for child in value:
            yield from dictionaries(child)


@pytest.mark.parametrize('damage', ['none', 'kind', 'schema', 'kind-and-schema'])
def test_real_overview_never_publishes_private_fence_originals_even_when_markers_are_stripped(attempted_scene, damage):
    """Root verified the real overview route; this test does not read api.py.

    Public Job serialization is separate from the owner-only cold Closure API.
    A broken marker must remain a private record, not a fake closed receipt.
    """
    s = attempted_scene
    body = close_request(s, reason='只有原作者可读的关闭说明-' + uuid4().hex)
    _, original, result = close_original(s, body=body)
    receipt = ok(result)
    private = ok(original_lookup(s))
    assert private['original_raw'].encode() == s.attempted['raw'] and private['close_raw'].encode() == original
    if damage != 'none':
        with Session(s.bound) as db:
            fence = db.get(m.Job, receipt['id'])
            if damage in {'kind', 'kind-and-schema'}:
                fence.kind = 'fixture_stripped_fence_v260'
            if damage in {'schema', 'kind-and-schema'}:
                fence.data = {**fence.data, 'schema': 'fixture-stripped-private-schema-v260'}
            db.commit()
    secrets = (s.attempted['raw'].decode(), original.decode(), body['reason'], s.attempted['request']['note'])
    def check_public(client):
        value = ok(client.get('/api/overview'))
        rendered = list(string_values(value))
        assert all(not any(secret in text for text in rendered) for secret in secrets)
        owned = [row for row in dictionaries(value) if row.get('id') == receipt['id']]
        assert owned, 'overview must show the safe Job record, not hide the inventory test'
        for row in owned:
            assert row['data'] == {'schema': 'catalogue-current-allocation-private-job-v260', 'private_record': True}
            assert all('original_raw' not in item and 'close_raw' not in item for item in dictionaries(row))
            assert all(item.get('closed') is not True for item in dictionaries(row))
    check_public(s.client)
    with other_client(s) as foreign:
        baseline = owned_inventory(s)
        check_public(foreign)
        denied = original_lookup(s, client=foreign)
        assert denied.status_code in {403, 409}
        assert not any(secret in text for text in string_values(denied.json()) for secret in secrets)
        assert owned_inventory(s) == baseline
