"""READ COMMITTED safety experiment for possible ACK Capability batching.

The batch below is deliberately a test-only unsafe comparator. It is not an
implementation change and must never be interpreted as an authorization.
"""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from generic_agents import goal_evaluation_query_v288 as query
from generic_agents.db import engine
from generic_agents.models import Capability, Membership, User


@pytest.fixture
def actors_and_caps(account):
    username, _, workspace = account
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == username))
        p = SimpleNamespace(user_id=user.id, workspace_id=workspace, role='admin')
        source = Capability(workspace_id=workspace, namespace='synthetic', name=uuid4().hex,
            release_version='1.0.0', kind='synthetic', status='published', content_hash='a'*64, data={})
        extractor = Capability(workspace_id=workspace, namespace='synthetic', name=uuid4().hex,
            release_version='1.0.0', kind='synthetic', status='published', content_hash='b'*64, data={})
        db.add_all([source, extractor]); db.commit()
        return p, source.id, extractor.id


def _batch_unsafe(db,p,source_id,extractor_id):
    rows=list(db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,
        Capability.id.in_({source_id,extractor_id})).execution_options(populate_existing=True)))
    mapping={row.id:row for row in rows}
    query.need(set(mapping)=={source_id,extractor_id})
    return mapping[source_id], mapping[extractor_id]


def _update(identity, content):
    with Session(engine()) as db:
        row=db.get(Capability,identity);row.content_hash=content;db.commit()


def test_batch_matches_fresh_without_concurrency_and_missing_fails(actors_and_caps):
    p,source,extractor=actors_and_caps
    with Session(engine()) as db:
        old=(query._fresh(db,p,Capability,source).content_hash,query._fresh(db,p,Capability,extractor).content_hash)
        result=_batch_unsafe(db,p,source,extractor)
        assert tuple(row.content_hash for row in result)==old
        same=_batch_unsafe(db,p,source,source)
        assert same[0] is same[1]
        with pytest.raises(HTTPException):_batch_unsafe(db,p,source,str(uuid4()))


@pytest.mark.parametrize('same_identity',[False,True])
def test_concurrent_change_makes_naive_batch_accept_stale_pin(actors_and_caps,same_identity):
    p,source,extractor=actors_and_caps
    if same_identity:extractor=source
    expected='a'*64 if same_identity else 'b'*64
    with Session(engine()) as db:
        assert db.scalar(text("show transaction_isolation"))=='read committed'
        first=query._fresh(db,p,Capability,source)
        _update(extractor,'c'*64)
        second=query._fresh(db,p,Capability,extractor)
        # Same-ID ORM references are refreshed together on the second query.
        assert second.content_hash=='c'*64 and second.content_hash!=expected
        if same_identity:assert first is second and first.content_hash=='c'*64
    _update(extractor,expected)
    with Session(engine()) as db:
        first,second=_batch_unsafe(db,p,source,extractor)
        _update(extractor,'c'*64)
        # Counterexample: a one-statement prefetch would accept the stale pin
        # at precisely the point the actual two-read path rejects it.
        assert second.content_hash==expected
        assert first is second if same_identity else first is not second


def test_role_revocation_is_rechecked_in_same_read_committed_transaction(actors_and_caps):
    p,source,extractor=actors_and_caps
    with Session(engine()) as db:
        query._reader(db,p);_batch_unsafe(db,p,source,extractor)
        with Session(engine()) as other:
            member=other.scalar(select(Membership).where(Membership.user_id==p.user_id,
                Membership.workspace_id==p.workspace_id))
            member.role='viewer';other.commit()
        with pytest.raises(HTTPException):query._reader(db,p)
