"""Isolated v2 first-answer accounting/recovery; never sends or retries.

Only the fixed historical origin validator and exact saved bytes can drive
settlement.  No user-facing route or normal worker is installed.
"""
import copy
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import cloud_budget
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_first_response_candidate_v2 as responses
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_response_intent_gate_candidate_v2 as gate
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_store_v2 as custody
from .db import engine
from .gateway import ModelBlocked
from .models import CloudCall
from .security import Principal


def reconcile(workspace_id,call_id,intent_id):
    """Settle one captured answer in its original ledger, without user auth.

    This is an internal accounting operation. It returns no answer or secret and
    grants no dispatch, review, or acceptance. Failure before a saved answer
    never sends, retries, cancels or invents a zero usage value.
    """
    with Session(engine()) as db:
        guard.require_disposable_database(db)
        pin=gate.check_original(db,workspace_id,call_id,intent_id)
        captured=storage.read(db,workspace_id,call_id,intent_id)
        store.need(type(captured) is tuple and len(captured)==3,
            '原首答尚未保全；调用及费用状态待核，禁止重发')
        _raw,_receipt,sealed=captured
        proven=responses.proven_receipt(sealed,cloud_call_id=call_id,intent_audit_id=intent_id)
        try:
            _value,parsed=responses.parse_saved(sealed,cloud_call_id=call_id,
                intent_audit_id=intent_id,schema=contracts.model_value_schema())
            store.need(all(parsed.get(key)==proven.get(key) for key in
                ('call_id','provider','model','request_id','usage','usage_unavailable')))
            status='succeeded'
        except ModelBlocked:
            status='failed'
        call=db.scalar(select(CloudCall).where(CloudCall.workspace_id==workspace_id,CloudCall.id==call_id)
            .with_for_update().execution_options(populate_existing=True))
        store.need(call is not None and type(call.data) is dict
            and type(call.data.get('same_goal_advice_unlimited_v2')) is dict)
        original=call.data['same_goal_advice_unlimited_v2']
        expected={'workspace_id':workspace_id,'capability_id':call.capability_id,'actor_id':pin['actor_id'],
            'request_hash':original['request_hash'],'dispatch_options_hash':original['dispatch_options_hash'],
            'provider':pin['provider'],'model':pin['model']}
        store.need(call.status not in {'succeeded','failed'} or call.status==status,
            '已结算状态与原首答不同；保留账本等待核对')
        cloud_budget.settle_paid_response(db,call_id,copy.deepcopy(proven),expected,status)
        # Revalidate the original immutable chain after all locks/updates; no
        # current role or lease is needed to preserve a paid historical answer.
        after=gate.check_original(db,workspace_id,call_id,intent_id)
        store.need(after==pin)
        db.commit()
        return {'state':'settled','answer_state':status,'usage_unavailable':bool(proven.get('usage_unavailable')),
            'retry_authorized':False,'business_result_verified':False}


def read_result(db,p,call_id,intent_id):
    """Current authenticated workspace reader; a schema result is not review."""
    guard.require_disposable_database(db)
    store.need(type(p) is Principal)
    try: UUID(p.session_id)
    except (ValueError,TypeError,AttributeError):store.need(False,'需要当前有效登录会话')
    store._reader(db,p)
    custody._lock_current_reader(db,p)
    captured=storage.read(db,p.workspace_id,call_id,intent_id)
    store.need(type(captured) is tuple and len(captured)==3,
        '原首答尚未保全；调用及费用状态待核，禁止重发')
    _raw,_receipt,sealed=captured
    value,_=responses.parse_saved(sealed,cloud_call_id=call_id,intent_audit_id=intent_id,
        schema=contracts.model_value_schema())
    # Parsing JSON Schema alone does not authenticate business claims or create
    # the product's reviewable business result.
    return {'model_value':value,'business_result_verified':False,'retry_authorized':False}
