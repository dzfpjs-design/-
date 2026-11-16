"""Real M1 residual-STATE witness over the explicitly synthetic source fixture.

The imported fixture does not prove paid/provider/original-report authority.
The accepted Fork, Catalogue commands and independent Audits are produced by
normal M1/Catalogue APIs on disposable PG. This negative then removes records;
it never inserts a successful Binding or reconstructs any acceptance evidence.
"""
import copy

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import catalogue_current_context_store_v260 as current
from generic_agents import report_business_catalogue_store_v211 as cat
from generic_agents.catalogue_current_context_models_v260 import (
    CatalogueCurrentContextBindingV260 as Binding,
    CatalogueCurrentContextCommandV260 as Command)
from tests.test_catalogue_current_context_v260 import (
    BASE, fork, h, ids, other_client, recover, reuse_scene)


def test_only_real_catalogue_state_audit_prevents_unknown_nonce_and_foreign_reuse(reuse_scene):
    s = reuse_scene
    body, original, receipt = fork(s)
    working_id = receipt['working_draft_ref']['id']
    with Session(s.bound) as db:
        witnesses = list(db.scalars(select(m.Audit).where(
            m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == cat.STATE_AUDIT,
            m.Audit.object_id == working_id,
            m.Audit.data['version'].as_integer() == 2)))
        assert len(witnesses) == 1
        witness = witnesses[0]
        witness_id = witness.id
        witness_data = copy.deepcopy(witness.data)
        assert witness_data['draft_id'] == working_id
        assert witness_data['record_hash'] == receipt['working_draft_ref']['record_hash']
        # Only the genuine original STATE_AUDIT remains for this new copy.
        db.delete(db.get(Command, receipt['id'])); db.flush()
        db.delete(db.get(Binding, receipt['binding_id'])); db.flush()
        db.execute(delete(cat.Command).where(cat.Command.workspace_id == s.p.workspace_id,
            cat.Command.draft_id == working_id))
        db.execute(delete(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action.in_([current.COMMAND_ACTION, current.BOUND_ACTION,
                current.LINK_ACTION, current.CONFIRM_ACTION])))
        db.execute(delete(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.data['draft_id'].as_string() == working_id,
            m.Audit.id != witness_id))
        db.delete(db.get(cat.Draft, working_id)); db.commit()
    before = (ids(s, Command), ids(s, Binding), ids(s, cat.Draft),
        ids(s, cat.Command), ids(s, m.Audit), ids(s, m.Job), ids(s, m.CloudCall))
    response = recover(s, body, original)
    assert response.status_code == 409, response.text
    assert response.json() != {'found': False}
    assert (ids(s, Command), ids(s, Binding), ids(s, cat.Draft),
        ids(s, cat.Command), ids(s, m.Audit), ids(s, m.Job), ids(s, m.CloudCall)) == before
    with other_client(s) as foreign:
        # Real login has its own normal login_limit Job. Fence the GET after
        # that authentication side effect, rather than call it a recovery write.
        foreign_before = (ids(s, Command), ids(s, Binding), ids(s, cat.Draft),
            ids(s, cat.Command), ids(s, m.Audit), ids(s, m.Job), ids(s, m.CloudCall))
        hidden = foreign.get(BASE + '/requests/' + body['request_id'],
            params={'raw_sha256': h(original)})
        assert hidden.status_code in {403, 409}, hidden.text
        assert body['reason'] not in hidden.text and original.decode() not in hidden.text
        assert (ids(s, Command), ids(s, Binding), ids(s, cat.Draft),
            ids(s, cat.Command), ids(s, m.Audit), ids(s, m.Job), ids(s, m.CloudCall)) == foreign_before
    with Session(s.bound) as db:
        saved = db.get(m.Audit, witness_id)
        assert saved is not None and saved.data == witness_data
        assert db.get(cat.Draft, working_id) is None
        assert db.get(Command, receipt['id']) is None
        assert db.get(Binding, receipt['binding_id']) is None
        assert not db.scalar(select(cat.Command.id).where(
            cat.Command.workspace_id == s.p.workspace_id,
            cat.Command.draft_id == working_id))
    assert ids(s, m.Job) == foreign_before[-2] and ids(s, m.CloudCall) == foreign_before[-1]
