"""Synthetic two-position HTTP workflow on a disposable PostgreSQL cluster only.

The release binds a separately logged-in operator to the document position
before publication. No production report, device, or provider is touched.
"""
import copy
import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import authored_goal_data_workflow_contracts_v288 as commands
from generic_agents import models as m
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents.goal_evaluation_provider_v288 import binding
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_manual_append_pg import (
    matured_manual_case, managed, scene, reject_non_loopback_network,
)
import tests.test_same_goal_advice_manual_append_pg as manual
import tests.test_auto_evaluation_real_journey_fixture_v356 as journey


@pytest.fixture
def deepseek_3600_publication(managed, monkeypatch):
    """Publish actual graph with a distinct data-position account."""
    credentials = []
    for _ in range(2):
        username, password = uuid4().hex, uuid4().hex
        response = managed.client.post('/api/users', json={'username': username,
            'password': password, 'role': 'operator'})
        assert response.status_code == 201, response.text
        credentials.append((username, password, response.json()['id']))
    data_actor, unrelated_actor = credentials
    original_post = managed.client.post
    def post(url, *args, **kwargs):
        if url in (commands.PREFIX + '/preview', commands.PATH):
            body = copy.deepcopy(kwargs['json'])
            body['tool_bindings']['packet']['actor_id'] = data_actor[2]
            kwargs['json'] = body
        return original_post(url, *args, **kwargs)
    monkeypatch.setattr(managed.client, 'post', post)
    # Reuse all existing source-map compatibility and real publication checks.
    from tests.test_same_goal_advice_offline_fresh_checkpoint_pg import deepseek_3600_publication as base
    publication = base.__wrapped__(managed, monkeypatch)
    monkeypatch.setattr(managed.client, 'post', original_post)
    publication.synthetic_data_actor = data_actor
    publication.synthetic_unrelated_actor = unrelated_actor
    # The actual document-packet executor also needs the configured approval
    # executor role in its organizational scope. Issue that grant via the
    # normal admin API before task start, without weakening any runtime guard.
    import tests.test_authored_workflow_document_packet_dispatch_pg_v208 as dispatch
    original_configure = dispatch.configure_two_stage_policy
    def configure_with_data_role(scope, release, first, second, acceptor):
        units = []
        post = scope.client.post
        def capture_unit(url, *args, **kwargs):
            result = post(url, *args, **kwargs)
            if url == dispatch.SETTINGS + '/units' and result.status_code < 300:
                units.append(kwargs['json']['unit_id'])
            return result
        monkeypatch.setattr(scope.client, 'post', capture_unit)
        try:
            original_configure(scope, release, first, second, acceptor)
        finally:
            monkeypatch.setattr(scope.client, 'post', post)
        assert len(units) == 1
        granted = scope.client.post(dispatch.SETTINGS + '/roles', json={
            'request_id':str(uuid4()),'grant_id':str(uuid4()),'expected_version':0,
            'unit_id':units[0],'user_id':data_actor[2],'role_key':'executor','enabled':True})
        assert granted.status_code < 300, granted.text[:300]
    monkeypatch.setattr(dispatch, 'configure_two_stage_policy', configure_with_data_role)
    return publication


def login(credentials, workspace):
    client = TestClient(app)
    response = client.post('/api/login', json={'username': credentials[0], 'password': credentials[1]})
    assert response.status_code == 200
    client.headers.update({'X-CSRF-Token': response.json()['csrf_token'], 'X-Workspace-ID': workspace})
    return client


def test_two_positions_handoff_and_denied_original_access(matured_manual_case, monkeypatch):
    case = matured_manual_case
    monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    business = case.client
    data = login(case.publication.synthetic_data_actor, case.workspace_id)
    unrelated = login(case.publication.synthetic_unrelated_actor, case.workspace_id)
    try:
        graph = case.publication.preview['binding']['graph']
        nodes = {node['id']: node for node in graph['nodes']}
        assert nodes['packet']['actor_id'] == case.publication.synthetic_data_actor[2]
        assert nodes['reasoning']['actor_id'] != nodes['packet']['actor_id']
        # Publish the ordinary capability manifest required by the advice service.
        with Session(engine()) as db:
            task = db.get(m.Task, case.task_id)
            selected = binding(db, case.principal, task)
            node = next(item for item in selected['binding']['graph']['nodes'] if item['id'] == 'reasoning')
            capability = db.get(m.Capability, node['execution']['capability_id'])
            manifest = contracts.build_manifest(capability.data, store._pin(capability))
        assert publish(business, manifest)['status'] == 'published'
        base = '/api/workspace/tasks/' + case.task_id + '/business-advice-v2'
        context = business.get(base + '/context')
        assert context.status_code == 200, context.text[:300]
        ctx = context.json()
        with Session(engine()) as db:
            clock = db.scalar(select(func.clock_timestamp()))
        issued = business.post(base + '/policy', json={'request_id':str(uuid4()),
            'expected_goal_hash':ctx['goal_hash'],'expected_mature_head_hash':ctx['head_hash'],
            'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
            'expires_at':(clock+timedelta(hours=2)).isoformat(),'reason':'合成岗位交接'})
        assert issued.status_code == 201, issued.text[:300]
        request = {'request_id':str(uuid4()), 'expected_goal_hash':ctx['goal_hash'],
            'expected_mature_head_hash':ctx['head_hash'],
            'document_source':ctx['sources'][0]['selection'],'reason':'合成资料整理'}
        prepared = business.post(base + '/preparations', json={'request':request,
            'policy_ref':issued.json()['policy_ref']})
        assert prepared.status_code == 201, prepared.text[:300]
        url = base + '/preparations/' + prepared.json()['plan_id']
        assert data.get(url).json()['can_process'] is True
        denied = unrelated.post(url + '/documents')
        assert denied.status_code in (403,404,409), denied.status_code
        processed = data.post(url + '/documents')
        assert processed.status_code == 200, processed.text[:300]
        assert data.get(url).json()['phase'] == 'review'
        assert data.get(url + '/documents').status_code in (403,404,409)
        assert unrelated.get(url + '/documents').status_code in (403,404,409)
        docs = business.get(url + '/documents')
        assert docs.status_code == 200, docs.text[:300]
        assert docs.json()['documents']
        doc = docs.json()['documents'][0]
        body = {'evidence_id':doc['id'],'expected_version':doc['version'],
            'reason':'业务岗位已确认','confirmed':True}
        assert data.post(url + '/consent', json=body).status_code in (403,404,409)
        assert unrelated.post(url + '/consent', json=body).status_code in (403,404,409)
        assert business.post(url + '/consent', json=body).status_code == 200
        for item in docs.json()['documents'][1:]:
            consent = business.post(url + '/consent', json={
                'evidence_id':item['id'],'expected_version':item['version'],
                'reason':'业务岗位已确认','confirmed':True})
            assert consent.status_code == 200
        state = business.get(url).json()
        preview = business.post(url + '/preview', json={'request_id':str(uuid4()),
            'expected_plan_version':state['version'],'expected_goal_hash':ctx['goal_hash'],
            'expected_mature_head_hash':ctx['head_hash'],'reason':'核对合成分析内容'})
        assert preview.status_code == 201, preview.text[:300]
        assert preview.json()['can_start'] is False
        destination = os.environ.get('GA_TEST_SYNTHETIC_PREVIEW_OUTPUT')
        if destination:
            assert destination == '/tmp/advice-v2-synthetic-preview.json'
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(preview.json(), output, ensure_ascii=False)
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(m.CloudCall)) == 0
    finally:
        data.close()
        unrelated.close()


def test_isolated_approval_scope_allows_only_granted_executor(client, account):
    """Exercise the production role guard against actual API-created grants."""
    from generic_agents.layered_approval_store_v175 import role_allowed
    from generic_agents.security import Principal
    from fastapi import HTTPException

    workspace = account[2]
    usernames = []
    for _ in range(2):
        username, password = uuid4().hex, uuid4().hex
        result = client.post('/api/users', json={'username':username,'password':password,'role':'operator'})
        assert result.status_code == 201
        usernames.append((username, password, result.json()['id']))
    unit, elsewhere = str(uuid4()), str(uuid4())
    base = '/api/workspace/approval-settings-v175'
    for identity in (unit, elsewhere):
        response = client.post(base + '/units', json={'request_id':str(uuid4()),
            'unit_id':identity,'expected_version':0,'name':'合成组织范围','parent_id':None,'enabled':True})
        assert response.status_code == 200
    def grant(who, scope):
        response = client.post(base + '/roles', json={'request_id':str(uuid4()),
            'grant_id':str(uuid4()),'expected_version':0,'unit_id':scope,
            'user_id':who,'role_key':'executor','enabled':True})
        assert response.status_code == 200
    grant(usernames[0][2], unit)
    grant(usernames[1][2], elsewhere)
    data, other = (login(user, workspace) for user in usernames)
    try:
        for browser, expected in ((data,usernames[0][2]),(other,usernames[1][2])):
            me = browser.get('/api/me')
            assert me.status_code == 200 and me.json()['user_id'] == expected
        with Session(engine()) as db:
            principals = []
            for username, _, identity in usernames:
                session = db.scalar(select(m.AuthSession).where(m.AuthSession.user_id == identity,
                    m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > func.clock_timestamp())
                    .order_by(m.AuthSession.created_at.desc()))
                assert session is not None
                principals.append(Principal(identity, workspace, 'operator', session.id))
            stage = {'scope_id':unit,'role_key':'executor'}
            assert role_allowed(db, principals[0], stage).user_id == usernames[0][2]
            with pytest.raises(HTTPException) as denied:
                role_allowed(db, principals[1], stage)
            assert denied.value.status_code == 403
    finally:
        data.close()
        other.close()
