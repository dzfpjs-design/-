import copy

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.instance_compiler import flow_issues
from generic_agents.models import SetupMaterial
from tests.test_setup_business_candidates import add_flow, bundle, material


def stage(client, *, missing=False):
    source = material(client)
    # A partially processed material may still support a content-only review.
    with Session(engine()) as db:
        row = db.get(SetupMaterial, source['id'])
        row.status = 'needs_input'
        row.version += 1
        db.commit()
    source = client.get('/api/setup/materials/' + source['id']).json()
    proposal = bundle(source, source_text='资料中尚未识别的原文。', quote='尚未识别') if missing else bundle(source)
    initial = client.get('/api/setup/draft').json()
    appended = client.post('/api/setup/candidates', json={'revision': initial['revision'], 'bundle': proposal})
    assert appended.status_code == 200, appended.text
    preview = client.post('/api/setup/candidates/preview', json=proposal)
    assert preview.status_code == 200, preview.text
    draft = add_flow(appended.json()['configuration'], preview.json())
    draft['materials'] = [source]
    saved = client.put('/api/setup/draft', json={'revision': appended.json()['revision'], 'configuration': draft})
    assert saved.status_code == 200, saved.text
    return saved.json()


def confirm(client, revision, action='confirm'):
    return client.post('/api/setup/candidates/content-review',
                       json={'revision': revision, 'business': '日常检查', 'action': action})


def test_content_review_survives_reopen_but_does_not_authorize_publication(client, account):
    draft = stage(client)
    response = confirm(client, draft['revision'])
    assert response.status_code == 200, response.text
    saved = response.json()['draft']
    flow = saved['configuration']['flows']['日常检查']
    assert flow['candidateContentReview']['business'] == '日常检查'
    assert not flow['confirmedSignature'] and not flow['confirmedAt']
    with Session(engine()) as db:
        from generic_agents.security import Principal
        from generic_agents.models import Membership, User
        from sqlalchemy import select
        user = db.scalar(select(User).where(User.username == account[0]))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        # The formal gate remains blocked by the partially processed material.
        principal = Principal(user_id=user.id, workspace_id=account[2], role=membership.role, session_id='test-session')
        assert any('引用已失效' in issue for issue in flow_issues(db, principal, saved['configuration'], flow,
                                                                 name='日常检查', require_signature=False))
    token = client.post('/api/tokens').json()['token']
    engine().dispose()
    with TestClient(app) as reopened:
        reopened.headers['Authorization'] = 'Bearer ' + token
        assert reopened.get('/api/setup/draft').json()['configuration']['flows']['日常检查']['candidateContentReview'] == flow['candidateContentReview']


def test_direct_forgery_stale_review_and_content_edits(client):
    draft = stage(client)
    forged = copy.deepcopy(draft['configuration'])
    forged['flows']['日常检查']['candidateContentReview'] = {'business': '日常检查', 'signature': '{}'}
    assert client.put('/api/setup/draft', json={'revision': draft['revision'], 'configuration': forged}).status_code == 409
    assert confirm(client, draft['revision'] + 1).status_code == 409
    approved = confirm(client, draft['revision']).json()['draft']
    removed = copy.deepcopy(approved['configuration'])
    removed['flows']['日常检查'].pop('candidateContentReview')
    assert client.put('/api/setup/draft', json={'revision': approved['revision'], 'configuration': removed}).status_code == 409
    altered = copy.deepcopy(approved['configuration'])
    altered['flows']['日常检查']['candidateContentReview']['actor_id'] = 'forged'
    assert client.put('/api/setup/draft', json={'revision': approved['revision'], 'configuration': altered}).status_code == 409
    changed = copy.deepcopy(approved['configuration'])
    changed['flows']['日常检查']['steps'][0]['purpose'] = '用户补充的目的'
    result = client.put('/api/setup/draft', json={'revision': approved['revision'], 'configuration': changed})
    assert result.status_code == 200, result.text
    assert 'candidateContentReview' not in result.json()['configuration']['flows']['日常检查']
    assert confirm(client, approved['revision']).status_code == 409


def test_revoke_and_clear_are_explicit(client, account):
    draft = stage(client)
    approved = confirm(client, draft['revision']).json()['draft']
    cleared = confirm(client, approved['revision'], action='clear')
    assert cleared.status_code == 200
    assert 'candidateContentReview' not in cleared.json()['draft']['configuration']['flows']['日常检查']
    # No workspace selected means no authorization for this operation.
    with TestClient(app) as anonymous:
        assert confirm(anonymous, cleared.json()['draft']['revision']).status_code in (401, 403)


def test_membership_revocation_and_workspace_scope_are_checked_each_time(client, account):
    from sqlalchemy import select
    from generic_agents.models import Membership, User, Workspace
    draft = stage(client)
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                         Membership.workspace_id == account[2]))
        outsider = Workspace(name='另一合成工作区'); db.add(outsider); db.flush()
        other_workspace = outsider.id
        membership.role = 'viewer'; db.commit()
    assert confirm(client, draft['revision']).status_code == 403
    client.headers['X-Workspace-ID'] = other_workspace
    assert confirm(client, draft['revision']).status_code == 403
    client.headers['X-Workspace-ID'] = account[2]
    assert 'candidateContentReview' not in client.get('/api/setup/draft').json()['configuration']['flows']['日常检查']


def test_revocation_between_authentication_and_commit_is_rechecked(client, account, monkeypatch):
    from sqlalchemy import select
    from generic_agents.models import Membership, User
    from generic_agents import setup_content_review as review
    draft = stage(client)
    original = review.require
    def revoke_after_auth(principal, *roles):
        original(principal, *roles)
        with Session(engine()) as db:
            user = db.scalar(select(User).where(User.username == account[0]))
            membership = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                             Membership.workspace_id == account[2]))
            membership.role = 'viewer'; db.commit()
    monkeypatch.setattr(review, 'require', revoke_after_auth)
    assert confirm(client, draft['revision']).status_code == 403
    assert 'candidateContentReview' not in client.get('/api/setup/draft').json()['configuration']['flows']['日常检查']


def test_unmatched_quote_can_be_reviewed_as_content_but_remains_unresolved(client):
    draft = stage(client, missing=True)
    assert draft['configuration']['flows']['日常检查']['candidateUnresolvedSources']
    response = confirm(client, draft['revision'])
    assert response.status_code == 200, response.text
    flow = response.json()['draft']['configuration']['flows']['日常检查']
    assert flow['candidateContentReview'] and flow['candidateUnresolvedSources']
    assert not flow['confirmedAt'] and not flow['confirmedSignature']


def test_material_reparse_invalidates_even_unresolved_source_review(client):
    draft = stage(client, missing=True)
    approved = confirm(client, draft['revision']).json()['draft']
    material_id = approved['configuration']['materials'][0]['id']
    with Session(engine()) as db:
        current = db.get(SetupMaterial, material_id)
        current.version += 1
        db.commit()
    assert confirm(client, approved['revision']).status_code == 409
    result = client.put('/api/setup/draft', json={'revision': approved['revision'],
                                                  'configuration': approved['configuration']})
    assert result.status_code == 200, result.text
    assert 'candidateContentReview' not in result.json()['configuration']['flows']['日常检查']
