"""Private test-only paid-response recovery; this module never dispatches.

The authenticated spool remains on disk after recovery. Removal is deliberately
not automatic: an operator must retain it until durable database capture and
settlement have been independently verified. No partial spool is removed here.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_response_intent_gate_candidate_v2 as gate
from . import same_goal_advice_dispatch_permit_candidate_v2 as permits
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_response_reconcile_candidate_v2 as reconcile
from . import same_goal_advice_response_spool_test_v2 as spool
from . import same_goal_advice_store_v302 as store
from .config import settings
from .db import engine
from .models import Audit


def _context(db, workspace_id, call_id, intent_id, permit_id):
    guard.require_disposable_database(db)
    pin=gate.check_original(db,workspace_id,call_id,intent_id)
    permit=db.scalar(select(Audit).where(Audit.workspace_id==workspace_id,Audit.id==permit_id,
        Audit.action==permits.ACTION).execution_options(populate_existing=True))
    store.need(permit is not None and type(permit.data) is dict
        and permit.data.get('cloud_call_id')==call_id
        and type(permit.data.get('intent_ref')) is dict
        and permit.data['intent_ref'].get('id')==intent_id)
    return spool._check_context({'schema':'advice-v2-private-spool-test-v1',
        'workspace_id':workspace_id,'call_id':call_id,'intent_id':intent_id,'permit_id':permit_id,
        'anchor_hash':pin['anchor_hash'],'provider':pin['provider'],'model':pin['model']})


def current_context(workspace_id,call_id,intent_id,permit_id):
    with Session(engine()) as db:
        return _context(db,workspace_id,call_id,intent_id,permit_id)


def recover(workspace_id,call_id,intent_id,permit_id,*,authorize_capture=None):
    """Authenticate, capture, and settle, each DB phase separate from file IO."""
    store.need(settings().v2_response_spool_enabled and settings().v2_response_spool_root is not None)
    context=current_context(workspace_id,call_id,intent_id,permit_id)
    # If a previous attempt durably captured the original bytes but crashed
    # before settlement, the saved original is sufficient. Do not require a
    # now-missing spool or attempt to reconstruct/replace those bytes.
    with Session(engine()) as db:
        store.need(_context(db,workspace_id,call_id,intent_id,permit_id)==context)
        existing=storage.read(db,workspace_id,call_id,intent_id)
        if type(existing) is tuple and len(existing)==3:
            if authorize_capture is not None:
                authorize_capture(db)
            db.commit()
            outcome=reconcile.reconcile(workspace_id,call_id,intent_id)
            return {**outcome,'spool_state':'not_checked'}
    raw,receipt=spool.load(settings().v2_response_spool_root,context)
    with Session(engine()) as db:
        # HTTP callers lock and recheck their live role/session immediately
        # before the write; do not hold those locks during file IO.
        if authorize_capture is not None:
            authorize_capture(db)
        store.need(_context(db,workspace_id,call_id,intent_id,permit_id)==context)
        storage.capture(db,workspace_id,call_id,intent_id,raw,receipt)
        db.commit()
    outcome=reconcile.reconcile(workspace_id,call_id,intent_id)
    return {**outcome,'spool_state':'retained'}
