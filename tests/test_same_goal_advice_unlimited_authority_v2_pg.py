"""Real-PG negative and price-reader tests; not a mature Goal or preview E2E."""
import copy
import socket
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_unlimited_authority_v2 as authority
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Membership, User
from generic_agents.security import Principal
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_business_reasoning_v197 import manifest
from tests.test_same_goal_advice_price_v307 import html


@pytest.fixture(autouse=True)
def only_loopback(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1'}:
                raise PermissionError('synthetic authority tests permit loopback only')
        return original(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)


def principal(db, account):
    user = db.scalar(select(User).where(User.username == account[0]))
    session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id,
        AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
        .order_by(AuthSession.created_at.desc()))
    assert session is not None
    return Principal(user.id, account[2], 'admin', session.id)


def test_missing_task_and_revoked_membership_never_authorize_policy_or_unknown_price(client, account):
    with Session(engine()) as db:
        p = principal(db, account)
        with pytest.raises(HTTPException) as error:
            authority.current_policy(db, p, str(uuid4()), {'id': str(uuid4()), 'record_hash': 'a' * 64})
        assert error.value.status_code == 404
        assert authority.current_price(db, p, {}, None) is None
        assert not list(db.scalars(select(CloudCall)))
        member = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id))
        member.role = 'viewer'; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            authority.current_price(db, p, {}, None)
        assert not list(db.scalars(select(CloudCall)))


def test_optional_price_uses_real_published_model_and_actual_price_audit(client, account, monkeypatch):
    model = copy.deepcopy(manifest())
    model['name'] = 'synthetic_deepseek_authority_price'
    model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    published = publish(client, model)
    pin = {'id': published['id'], 'content_hash': published['content_hash'],
        'release_version': published['release_version']}
    # Only the external official HTTP fetch is substituted, with synthetic
    # bytes; registry, actor, Audit, Evidence and price reader use real PG.
    monkeypatch.setattr(prices, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    response = client.post('/api/workspace/business-advice/price-bases', json={
        'request_id': str(uuid4()), 'model_pin': pin, 'validity_seconds': 3600,
        'reason': '隔离合成价目来源核对'})
    assert response.status_code == 201, response.text
    basis = response.json()['basis_ref']
    with Session(engine()) as db:
        p = principal(db, account)
        verified = authority.current_price(db, p, pin, basis)
        assert verified['basis_ref'] == basis and verified['price']['provider'] == 'deepseek'
        assert verified['price']['database_price_authorization_verified'] is False
        assert authority.current_price(db, p, pin, None) is None
        with pytest.raises(HTTPException):
            authority.current_price(db, p, pin, {**basis, 'record_hash': 'e' * 64})
        assert not list(db.scalars(select(CloudCall)))
