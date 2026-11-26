"""Authenticated v2 preparation/preview with real synthetic approvals.

No actual user data, devices or external provider are used. The sender remains
unavailable; this proves only the locally prepared and reviewable part.
"""
import json
import os
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_store_v302 as old
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY, binding
from generic_agents.models import AuthSession, Capability, CloudCall, Task, User
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet
from generic_agents.security import Principal
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db
from tests.test_authored_service_workflow_pg_v270 import publish
from generic_agents.api import app


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_CHECKPOINT'), reason='isolated checkpoint required')
def test_real_synthetic_approved_sources_through_authenticated_http_and_restart(monkeypatch):
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    from generic_agents.same_goal_advice_intent_index_migration_candidate_v2 import rehearse
    assert rehearse()['status'] in {'installed', 'already_installed'}
    login = json.loads((settings().storage_dir / '__synthetic_checkpoint_login__.json').read_text())
    with TestClient(app) as client:
        auth = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
        assert auth.status_code == 200
        client.headers.update({'X-CSRF-Token': auth.json()['csrf_token'], 'X-Workspace-ID': login['workspace_id']})
        with Session(engine()) as db:
            user = db.scalar(select(User).where(User.username == login['username']))
            session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id,
                AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
                .order_by(AuthSession.created_at.desc()))
            p = Principal(user.id, login['workspace_id'], 'admin', session.id)
            task = db.get(Task, task_id); head = task.data[KEY]
            caps = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
                Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
            origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
            assert len(origins) == 1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', o) for o in origins)
            monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', next(iter(origins)))
            source = db.scalar(select(RuntimeTaskDocumentSourceSet).where(
                RuntimeTaskDocumentSourceSet.task_id == task_id, RuntimeTaskDocumentSourceSet.workspace_id == p.workspace_id))
            assert source is not None
            selected = binding(db, p, task)
            node = next(n for n in selected['binding']['graph']['nodes'] if n['id'] == 'reasoning')
            capability = db.get(Capability, node['execution']['capability_id'])
            manifest = contracts.build_manifest(capability.data, old._pin(capability))
            goal_hash, head_hash = task.data['goal_hash'], head['head_hash']
            source_ref = {'kind': 'approved_source_set', 'source_set_ref': {
                'source_set_id': source.id, 'source_set_hash': source.source_set_hash}}
            clock = db.scalar(select(func.clock_timestamp()))
        assert publish(client, manifest)['status'] == 'published'
        base = f'/api/workspace/tasks/{task_id}/business-advice-v2'
        context_response = client.get(base + '/context')
        assert context_response.status_code == 200, context_response.status_code
        assert context_response.json()['sources'][0]['selection'] == source_ref
        assert context_response.json()['can_start'] is False
        policy_body = {'request_id': str(uuid4()), 'expected_goal_hash': goal_hash,
            'expected_mature_head_hash': head_hash, 'limits': {'max_tokens': 512,
                'max_request_bytes': 50000, 'max_evidence_items': 2},
            'expires_at': (clock + timedelta(hours=2)).isoformat(), 'reason': '合成资料授权测试'}
        csrf = client.headers.pop('X-CSRF-Token')
        assert client.post(base + '/policy', json=policy_body).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        issued = client.post(base + '/policy', json=policy_body)
        assert issued.status_code == 201, issued.status_code
        request = {'request_id': str(uuid4()), 'expected_goal_hash': goal_hash,
            'expected_mature_head_hash': head_hash, 'document_source': source_ref, 'reason': '合成准备'}
        prepared = client.post(base + '/preparations', json={'request': request, 'policy_ref': issued.json()['policy_ref']})
        assert prepared.status_code == 201, prepared.status_code
        plan = prepared.json()['plan_id']; url = base + '/preparations/' + plan
        assert client.get(url).json()['phase'] == 'waiting'
        processed = client.post(url + '/documents')
        assert processed.status_code == 200, processed.status_code
        assert client.post(url + '/documents').json()['replayed'] is True
        state = client.get(url).json()
        assert state['phase'] == 'review' and state['can_start'] is False
        docs = client.get(url + '/documents')
        assert docs.status_code == 200 and len(docs.json()['documents']) == 2
        for item in docs.json()['documents']:
            assert item['text']
            unauthorized = client.post(url + '/consent', json={'evidence_id': item['id'],
                'expected_version': item['version'], 'reason': '合成核对', 'confirmed': False})
            assert unauthorized.status_code == 422
            authorized = client.post(url + '/consent', json={'evidence_id': item['id'],
                'expected_version': item['version'], 'reason': '合成核对', 'confirmed': True})
            assert authorized.status_code == 200, authorized.status_code
        request_preview = {'request_id': str(uuid4()), 'expected_plan_version': state['version'],
            'expected_goal_hash': goal_hash, 'expected_mature_head_hash': head_hash, 'reason': '合成预览'}
        first = client.post(url + '/preview', json=request_preview)
        assert first.status_code == 201, first.text[:300]
        assert first.json()['can_start'] is False and first.json()['cloud_send_authorized'] is False
        engine().dispose(); client.cookies.clear()
        login_again = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
        assert login_again.status_code == 200
        client.headers['X-CSRF-Token'] = login_again.json()['csrf_token']
        assert client.get(url).json()['phase'] == 'review'
        assert client.get(base + '/preparations').json()['items'] == [{'plan_id': plan,
            'request_id': request['request_id'], 'phase': 'review'}]
        assert all(item['authorized'] for item in client.get(url + '/documents').json()['documents'])
        assert client.post(url + '/preview', json=request_preview).json() == first.json()
        assert client.post(url + '/start', json={}).status_code == 404
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
