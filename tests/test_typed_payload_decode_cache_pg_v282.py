"""Real disposable PG: caches never replace row reloads or durable state."""
import copy
import json
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from generic_agents.db import engine
from generic_agents.models import Memory
from generic_agents.typed_payload_orm_v131 import FORMAT, CANONICAL
from generic_agents import typed_payload_codec_v131 as codec


def test_cross_session_drafts_warm_reads_and_real_updates_are_independent(account):
    codec._verified_decode_json.cache_clear()
    with Session(engine()) as db:
        row=Memory(workspace_id=account[2],text='独立存储验证',data={'nested':[{'value':1.0}]})
        db.add(row);db.commit();identity=row.id
    with Session(engine(),expire_on_commit=False) as first:
        initial=first.get(Memory,identity);marker=getattr(initial,FORMAT)
        initial.data['nested'][0]['value']='unsaved local draft'
        with Session(engine()) as second:
            saved=second.get(Memory,identity);assert saved.data=={'nested':[{'value':1.0}]}
            saved.data={'nested':[{'value':2.0}]};second.commit()
        current=first.get(Memory,identity,populate_existing=True)
        assert current.data=={'nested':[{'value':2.0}]} and type(current.data['nested'][0]['value'])is float
        assert getattr(current,FORMAT)!=marker
        current.status='trusted';first.commit()
        with Session(engine()) as third:
            latest=third.get(Memory,identity)
            assert latest.status=='trusted' and latest.data=={'nested':[{'value':2.0}]}
    assert codec._verified_decode_json.cache_info().hits>0


def test_warm_entry_preserves_DB_pairing_constraints_and_rollback_recovers(account):
    codec._verified_decode_json.cache_clear()
    with Session(engine()) as db:
        row=Memory(workspace_id=account[2],text='真实JSONB破坏验证',data={'value':1.0});db.add(row);db.commit();identity=row.id
    with Session(engine()) as db:assert db.get(Memory,identity).data=={'value':1.0}
    with Session(engine()) as db:
        marker=codec.new_marker();owner={'table':Memory.__tablename__,'field':'data','owner_id':identity,'workspace_id':account[2]}
        canonical=codec.encode_payload({'value':1.0},owner,marker)
        # The real database rejects this malformed pair before any reader can
        # see it. Keep that stronger boundary; do not disable it for a cache test.
        with pytest.raises(IntegrityError):
            db.execute(text('UPDATE "'+Memory.__tablename__+'" SET data=CAST(:body AS jsonb), "'+FORMAT+'"=:marker, "'+CANONICAL+'"=:canonical WHERE id=:id'),{'body':json.dumps({'value':True}),'id':identity,'marker':marker,'canonical':canonical})
        db.rollback()
    with Session(engine()) as db:
        saved=db.get(Memory,identity);assert saved.data=={'value':1.0} and type(saved.data['value'])is float
