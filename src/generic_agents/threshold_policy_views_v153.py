"""Read-only finite-policy input display, never a new execution authority.

Reception means the actual Event/Memory/ack chain was read successfully. A
completed numeric invocation alone says neither data reception nor a declared
business output. This module never re-enters the recursive Grant view path.
"""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from .models import Event
from .services import scoped
from .threshold_policy_models_v152 import ThresholdPolicyGrant as Grant


_REVOKED_STOP_REASON = '原操作者执行授权已撤销；已停止此输入，原记录保留，不重新计算'
_UNREADABLE_SELECTION_REASON = '当前固定审核、规则或来源不可用于这些机器数值；原输入及核查历史保留'


def selection_unreadable_issue(status,error):
    """Retain only a fixed safe stop explanation; never echo machine errors."""
    if type(status) is str and status=='stopped' and type(error) is str and error==_REVOKED_STOP_REASON:
        return _REVOKED_STOP_REASON+'；'+_UNREADABLE_SELECTION_REASON
    return _UNREADABLE_SELECTION_REASON


def _reception(db,p,observation):
    """Return real reception state; absent or damaged evidence is not success."""
    from .instance_data_receipts import observation_receipt
    events=list(db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,
        Event.kind=='observation.data',Event.idempotency_key=='data:'+observation.id)
        .execution_options(populate_existing=True)))
    if not events:
        if 'data_reception' in observation.data:
            return 'unavailable',None,'原接收标记缺少实际数据事件；记录保留，不能声称已接收'
        return None,None,None
    if len(events)!=1:
        return 'unavailable',None,'原数据事件无法唯一核对；记录保留，不能声称已接收'
    event=events[0]
    if type(event.data) is not dict:
        return 'unavailable',None,'原数据事件的接收结构无法核对；记录保留'
    if event.status!='received':
        if 'data_reception' in observation.data or 'data_reception' in event.data:
            return 'unavailable',None,'原数据接收和确认状态不一致；请核对原接收记录'
        return event.status,None,('原数据接收受阻；原输入和实际调用记录保留'
            if event.status=='blocked_delivery' else None)
    try:
        checked=observation_receipt(db,p,observation)
        receipt=checked.get('data_reception') if type(checked) is dict else None
        if type(receipt) is not dict or receipt.get('declaration_status') not in {'declared','not_declared'}:
            return 'unavailable',None,'尚无可核对的现代接收回执，不能声称已接收或已声明业务输出'
        return 'received',receipt['declaration_status'],None
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        return 'unavailable',None,'原数据事件、接收回执或保存资料无法完整核对；记录保留'


def selection_view(db,p,row,checked=False,*,_readiness=None):
    from . import threshold_policy_store_v153 as store
    from . import threshold_policy_execution_v153 as execution
    store.need(type(checked) is bool and row.workspace_id==p.workspace_id)
    if not checked:
        store.inventory(db,p)
        rows,_=execution._selection_records(db,p)
        row=next((actual for actual in rows if actual.id==row.id),None)
        store.need(row is not None)
    # Even a checked internal caller cannot project a modified in-memory row.
    execution._shape(execution.snapshot(row))
    store.need(row.record_hash==store.hash_value(execution.snapshot(row)))
    grant=scoped(db,Grant,row.policy_id,p) if _readiness is None else _readiness.grant
    if _readiness is not None and getattr(_readiness,'inventory',None) is not None:
        store.need(checked)
        observation,job,call=_readiness.binding(db,p,grant,row)
    else:
        if _readiness is not None:_readiness.check(db,p,grant,row)
        observation,job,call=execution._bindings(db,p,row,grant)
    scope=grant.data['scope'];original=row.data['original_request']
    baseline,candidate=store._rules(grant)
    extractor=deepcopy(candidate if row.choice=='candidate' else baseline)
    readable=True;issue=row.data['error']
    try:
        if _readiness is None:
            authority=store.fresh_candidate if row.choice=='candidate' else store.fresh_baseline
            authority(db,p,grant,lock=False)
        else:
            store.need(checked)
            if not _readiness.readable(db,p,grant,row.choice,row):
                raise HTTPException(409,'当前显示资格不可用')
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        readable=False;issue=selection_unreadable_issue(row.status,row.data['error'])
    value=deepcopy(original['attributes'][scope['field']]) if readable else None
    matched=projection=None
    if readable and row.status=='completed':
        result=row.data['result']
        store.need(type(result) is dict and type(result['actual_matched']) is bool
            and type(result['baseline_projection_matched']) is bool)
        matched=result['actual_matched'];projection=result['baseline_projection_matched']
    if not readable:extractor['threshold']=None;baseline['threshold']=None
    receipt_status,declaration,reception_issue=_reception(db,p,observation)
    if _readiness is not None:_readiness.check(db,p,grant,row)
    if reception_issue:
        issue=reception_issue if issue is None else issue+'；'+reception_issue
    if issue is not None:issue=issue[:2000]
    # This is a link to the real chosen rule's original observation. Its own
    # feedback API independently checks current basis and saving permission;
    # displaying the entry neither signs a label nor re-approves a policy.
    actual_extractor=original['extractor_id']
    feedback=({'observation_id':observation.id,'baseline_capability_id':actual_extractor,
        'instance_id':scope['instance_id'],'rule_id':scope['rule_id']}
        if observation.status=='processed' and observation.data.get('extractor_id')==actual_extractor else None)
    return {'schema':'threshold-policy-selection-view-v153','workspace_id':p.workspace_id,
        'id':row.id,'version':row.version,'policy_id':row.policy_id,'choice':row.choice,'status':row.status,
        'source_record_id':row.source_record_id,'processing_version':original['processing_version'],
        'observed_at':original['observed_at'],'business_day':original['business_day'],
        'entity_name':scope['entity_name'],'source_name':scope['source_name'],'field':scope['field'],
        'field_label':scope['field_label'],'unit':scope['unit'],'value':value,'extractor':extractor,
        'baseline':baseline,'invocation_status':call.status if call is not None else None,
        'actual_matched':matched,'baseline_projection_matched':projection,'late':row.data['late'],'issue':issue,
        'observation_status':observation.status,'job_status':job.status,'receipt_status':receipt_status,
        'business_declaration':declaration,'feedback':feedback,
        'can_feedback':feedback is not None and p.role in {'admin','operator'}}
