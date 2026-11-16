"""Root-run owned-PG sharing/security; synthetic identities, real auth and API.

No principal/dependency/gate is replaced. Only the reusable public provider
MockTransport is synthetic; this test is not paid-model or physical-restart proof.
"""
import copy
import json
from contextlib import ExitStack, contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Membership, User, Workspace
from generic_agents.security import password_hash
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, NativeHTTP, v7_scene, mapped, reduced, storage_files, call_job_rows,
)
from tests.test_report_full_semantic_handoff_postgres import human_value, PRIVATE_NOTE


PUBLIC_PASSWORD = 'FullSemantic_SYNTHETIC_only_20261018!'


@contextmanager
def authenticated(app, credentials, workspace_id, expected_role):
    with TestClient(app) as client:
        auth = reply(client.post('/api/login', json={
            'username': credentials[0], 'password': credentials[1]}))
        client.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': workspace_id})
        me = reply(client.get('/api/me'))
        assert me['username'] == credentials[0] and me['workspace_id'] == workspace_id
        assert me['role'] == expected_role and client.cookies.get('ga_session')
        yield client


def test_full_semantic_private_scope_explicit_share_strict_consent_and_bound_current_review(
        v7_scene, monkeypatch, account):
    fence()
    scene, http = v7_scene, NativeHTTP(monkeypatch)
    owner = scene['client']
    _, _, _, middle = mapped(scene, http)
    _, _, _, generated = reduced(scene, http, middle)
    identity = generated['id']
    value = human_value(generated['model_result'])
    manual = reply(owner.put(BASE + '/' + identity + '/review', json={
        'revision': generated['revision'], 'request_id': str(uuid4()), 'value': value}))
    private = manual['draft']
    config = reply(owner.get('/api/setup/draft'))
    body = {'schema': 'report-full-semantic-handoff-command-v1', 'request_id': str(uuid4()),
        'report_revision': private['revision'], 'review_receipt_hash': manual['receipt']['receipt_hash'],
        'manual_value_hash': digest(value), 'model_result_hash': private['model_result_hash'],
        'source_hash': private['preview']['source_hash'], 'catalog_hash': value['result']['catalog_hash'],
        'context_hash': digest(private['preview']['map_preparation']['context']),
        'configuration_revision': config['revision'], 'configuration_hash': digest(config['configuration']),
        'share_scope': 'whole_result_excluding_private_note_v1'}
    preview_url = BASE + '/' + identity + '/full-configuration-preview'
    submit_url = BASE + '/' + identity + '/full-configuration-handoff'
    receipts_url = BASE + '/' + identity + '/full-configuration-receipts/'
    paid = (call_job_rows()[CloudCall.__tablename__], fees(call_ids(private)),
        storage_files(), len(http.requests))

    def no_paid_activity():
        assert (call_job_rows()[CloudCall.__tablename__], fees(call_ids(private)),
            storage_files(), len(http.requests)) == paid

    snapshot = all_rows()
    unknown = owner.get(receipts_url + str(uuid4()))
    assert unknown.status_code == 404 and unknown.json()['detail']['code'] == 'receipt_not_found'
    assert all_rows() == snapshot
    for field in ('review_receipt_hash', 'manual_value_hash', 'model_result_hash',
            'source_hash', 'catalog_hash', 'context_hash', 'report_revision', 'schema'):
        forged = copy.deepcopy(body)
        forged['request_id'] = str(uuid4())
        forged[field] = (body[field] + 1 if field == 'report_revision' else
            'report-full-semantic-handoff-command-v0' if field == 'schema' else '0' * 64)
        response = owner.post(preview_url, json=forged)
        assert response.status_code == (422 if field == 'schema' else 409), (field, response.text)
        if field != 'schema':
            assert response.json()['detail']['code'] == 'full_semantic_review_changed'
        assert all_rows() == snapshot
        no_paid_activity()
    response = owner.post(BASE + '/' + str(uuid4()) + '/full-configuration-preview', json=body)
    assert response.status_code == 404 and all_rows() == snapshot

    preview = reply(owner.post(preview_url, json=body))
    before_share = owner.get('/api/setup/full-semantic-handoffs/' + preview['design']['handoff_id'])
    assert before_share.status_code == 404 and all_rows() == snapshot
    submit = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    for consent in (1, 'true', False, None):
        response = owner.post(submit_url, json={**submit, 'consent': consent})
        assert response.status_code == 422
        assert all_rows() == snapshot
        no_paid_activity()
    accepted = reply(owner.post(submit_url, json=submit))
    assert accepted['current_matches'] is True
    assert accepted['record']['design']['result'] == value['result']
    assert PRIVATE_NOTE not in json.dumps(accepted, ensure_ascii=False)
    shared_url = '/api/setup/full-semantic-handoffs/' + accepted['record']['id']
    shared = reply(owner.get(shared_url))
    assert shared['record'] == accepted['record'] and shared['execution_supported'] is False
    assert reply(owner.get(BASE + '/' + identity)) == private
    no_paid_activity()

    # Explicit synthetic fixture rows create independent identities only; they
    # do not replace authentication, elevate the existing owner or write a draft.
    other_name, viewer_name = 'full_share_other_' + uuid4().hex, 'full_share_viewer_' + uuid4().hex
    with Session(engine()) as db:
        other = User(username=other_name, password_hash=password_hash(PUBLIC_PASSWORD))
        viewer = User(username=viewer_name, password_hash=password_hash(PUBLIC_PASSWORD))
        workspace = Workspace(name='合成完整设计隔离工作区 ' + uuid4().hex)
        db.add_all([other, viewer, workspace]); db.flush()
        other_workspace = workspace.id
        db.add_all([
            Membership(user_id=other.id, workspace_id=scene['p'].workspace_id, role='operator'),
            Membership(user_id=viewer.id, workspace_id=scene['p'].workspace_id, role='viewer'),
            Membership(user_id=scene['p'].user_id, workspace_id=other_workspace, role='operator'),
        ])
        db.commit()

    with ExitStack() as clients:
        peer = clients.enter_context(authenticated(owner.app, (other_name, PUBLIC_PASSWORD),
            scene['p'].workspace_id, 'operator'))
        viewer = clients.enter_context(authenticated(owner.app, (viewer_name, PUBLIC_PASSWORD),
            scene['p'].workspace_id, 'viewer'))
        other_scope = clients.enter_context(authenticated(owner.app, account, other_workspace, 'operator'))
        same_owner = clients.enter_context(authenticated(owner.app, account, scene['p'].workspace_id, 'admin'))
        # Auth has its own real session/limiter writes. All attack/GET requests
        # below must preserve every table from the post-login checkpoint.
        snapshot = all_rows()
        for client in (peer, other_scope):
            response = client.post(preview_url, json=body)
            assert response.status_code == 404
            response = client.get(receipts_url + body['request_id'])
            assert response.status_code == 404 and response.json()['detail']['code'] == 'receipt_not_found'
            response = client.post(submit_url, json={**submit, 'request_id': str(uuid4())})
            assert response.status_code == 404
            assert all_rows() == snapshot
        response = peer.post(submit_url, json=submit)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'full_semantic_nonce_conflict'
        assert reply(peer.get(shared_url)) == shared  # Explicitly shared workspace result is readable.
        assert PRIVATE_NOTE not in json.dumps(reply(peer.get(shared_url)), ensure_ascii=False)
        assert reply(viewer.get(shared_url)) == shared
        assert other_scope.get(shared_url).status_code == 404
        for response in (viewer.post(preview_url, json=body), viewer.post(submit_url, json=submit)):
            assert response.status_code == 403
        assert all_rows() == snapshot

        workspace_header = same_owner.headers.pop('X-Workspace-ID')
        try:
            for response in (same_owner.get(shared_url), same_owner.post(preview_url, json=body)):
                assert response.status_code == 403
                assert response.json()['detail']['code'] == 'explicit_workspace_required'
        finally:
            same_owner.headers['X-Workspace-ID'] = workspace_header
        csrf = same_owner.headers.pop('X-CSRF-Token')
        try:
            response = same_owner.post(submit_url, json=submit)
            assert response.status_code == 403 and response.json()['detail'] == 'CSRF validation failed'
        finally:
            same_owner.headers['X-CSRF-Token'] = csrf
        assert all_rows() == snapshot
        assert reply(same_owner.get(receipts_url + body['request_id'])) == accepted
        assert reply(same_owner.get(BASE + '/' + identity)) == private
        no_paid_activity()
