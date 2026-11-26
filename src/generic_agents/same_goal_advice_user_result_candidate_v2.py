"""Authenticated, read-only user view of a verified test-only first result.

This router is deliberately not installed by the application. It never sends,
recovers, settles, authorizes, or retries a request. A result is returned only
when the original response, the complete custody chain and atomic usage ledger
all verify in the same dedicated disposable PostgreSQL transaction.
"""
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, text

from .config import settings
from .contracts import digest
from .gateway import ModelBlocked
from .models import Audit, Capability, Plan
from .setup_api import DB, P
from . import same_goal_advice_common_inventory_v2 as inventory
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_first_response_candidate_v2 as response
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_human_preview_v2 as human_preview
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_terminal_candidate_v2 as terminal
from . import same_goal_advice_unlimited_store_v2 as custody
from . import same_goal_advice_unlimited_contracts_v2 as v2_contracts
from . import same_goal_advice_unlimited_preview_candidate_v2 as preview

router = APIRouter()
BASE = '/tasks/{task_id}/business-advice-v2/preparations/{plan_id}/result'
_INTERNAL = re.compile(r'(?i)\b(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|(?:fact|source|candidate|requirement|capability|conflict)_[a-z0-9_]+)\b')


def _auth(db, p, task_id, plan_id):
    guard.require_disposable_database(db)
    if p.role not in {'admin', 'operator'}:
        raise HTTPException(403, '当前账号无法查看建议')
    try:
        UUID(p.session_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(403, '请重新登录') from None
    old._reader(db, p)
    old.need(custody._lock_current_reader(db, p), '请重新登录')
    task = old.existing._task(db, p, task_id)
    plan = db.scalar(select(Plan).where(Plan.workspace_id == p.workspace_id, Plan.task_id == task.id,
        Plan.id == old.existing._id(plan_id)).execution_options(populate_existing=True))
    if plan is None:
        raise HTTPException(404, '未找到建议')
    # An unchecked actor may deny access, but never grants it. Full custody is
    # verified below before any content is returned.
    raw = plan.data.get('preparation_snapshot') if type(plan.data) is dict else None
    if p.role != 'admin' and (type(raw) is not dict or raw.get('business_actor_id') != p.user_id):
        raise HTTPException(403, '当前账号无法查看建议')
    return task, plan


def _pending_preparation(db, p, task, plan):
    """Read the original prepared actor, without inferring a sent outcome."""
    if plan.status == 'queued':
        prep, _ = custody.read_queued(db, p, task, plan)
    elif plan.status == 'awaiting_consent':
        prep, _ = custody.read_prepared(db, p, task, plan)
    elif plan.status == 'awaiting_dispatch':
        # Any failed custody state stays pending review rather than trying a
        # weaker historical interpretation of a malformed proof.
        attempts = (
            lambda: custody.read_started(db, p, task, plan),
            lambda: custody.read_reserved_test_only(db, p, task, plan),
            lambda: custody.read_intent_test_only(db, p, task, plan),
        )
        prep = None
        for check in attempts:
            try:
                prep = check()[0]; break
            except HTTPException:
                continue
        old.need(prep is not None)
    else:
        old.need(False)
    old.need(p.role == 'admin' or prep['business_actor_id'] == p.user_id)
    return prep


def _safe_text(value):
    old.need(isinstance(value, str) and not _INTERNAL.search(value), '建议内容需要核对')
    return value


def _public(cloud, opinion, frame, aliases, snapshots):
    """Preserve advice and its reference relationships without internal keys."""
    value = contracts.check_model_value(cloud, opinion)
    proposals = {row['candidate_ref']: _safe_text(row['proposal']) for row in cloud['candidates']}
    fact_numbers = {row['fact_ref']: index for index, row in enumerate(cloud['facts'], 1)}
    source_numbers = {row['source_ref']: index for index, row in enumerate(cloud['sources'], 1)}
    source_kinds = {row['source_ref']:row['kind'] for row in cloud['sources']}
    source_types = {'evidence':'文档','packet':'业务资料','observation':'观测资料',
        'archive_record':'档案资料','capability':'能力资料'}
    status = {'suitable':'适合', 'unsuitable':'不适合', 'uncertain':'待核对'}
    source_status = {'reviewed':'已分析所提供内容', 'unreadable':'无法读取', 'incomparable':'无法比较'}
    fact_types = {'observation':'现场观测', 'document_statement':'资料',
        'capability_declaration':'已登记能力', 'inference':'推断'}
    pins=frame['capability_pins']
    old.need(digest(snapshots)==pins['object_snapshots_hash'] and len(snapshots)==len(pins['entities']))
    labels={}
    for index,(snapshot,pin) in enumerate(zip(snapshots,pins['entities']),1):
        old.need(snapshot['id']==pin['id'])
        identity=aliases.get(pin['id'].lower(),pin['id'])
        name='对象 '+str(index)+' · '+_safe_text(snapshot['type_name'])+'（本地）'
        for field in snapshot['fields']:
            old.need((identity,field['key']) not in labels)
            labels[(identity,field['key'])]=(name,_safe_text(field['label']))
    used=set(value['recommendation']['supporting_fact_refs'])
    for item in value['candidate_reviews']: used.update(item['supporting_fact_refs'])
    for item in value['conflicts']: used.update(item['fact_refs'])
    rendered_facts=[]
    for row in cloud['facts']:
        if row['fact_ref'] not in used: continue
        if row['field']=='capability_declaration':
            obj=next((name for (identity,_),(name,_) in labels.items() if identity==row['entity_id']),None)
            field='已登记能力'
        else:
            obj,field=labels.get((row['entity_id'],row['field']),(None,None))
        old.need(obj is not None and field is not None)
        datum=_safe_text(row['value']) if isinstance(row['value'],str) else row['value']
        rendered_facts.append({'number':fact_numbers[row['fact_ref']],'type':fact_types[row['kind']],
            'object':obj,'field':field+'（本地）',
            'value':datum,'unit':_safe_text(row['unit']) if row['unit'] is not None else None, 'start':row['time_scope']['start'], 'end':row['time_scope']['end'],
            'sources':[source_numbers[ref['source_ref']] for ref in row['source_refs']]})
    recommendation = value['recommendation']
    selected = recommendation['candidate_ref']
    return {
        'recommendation': {'proposal': proposals[selected] if selected is not None else None,
            'reason': _safe_text(recommendation['reason']),
            'supporting_facts': [fact_numbers[ref] for ref in recommendation['supporting_fact_refs']]},
        'candidates': [{'proposal': proposals[row['candidate_ref']], 'assessment': status[row['suitability']],
            'reason': _safe_text(row['reasoning']), 'blocking_conditions': [_safe_text(item) for item in row['blocking_conditions']],
            'supporting_facts':[fact_numbers[ref] for ref in row['supporting_fact_refs']]} for row in value['candidate_reviews']],
        'source_assessments': [{'source': source_numbers[row['source_ref']],
            'source_type':source_types[source_kinds[row['source_ref']]], 'assessment':source_status[row['assessment']],
            'reason':_safe_text(row['reason'])} for row in value['source_reviews']],
        'conflicts': [{'reason':_safe_text(row['reason']), 'facts':[fact_numbers[ref] for ref in row['fact_refs']]}
            for row in value['conflicts']],
        'facts': rendered_facts,
    }


@router.get(BASE)
def read(task_id: str, plan_id: str, p: P, db: DB):
    if not settings().v2_advice_setup_test_enabled:
        raise HTTPException(404, '建议暂不可用')
    task, plan = _auth(db, p, task_id, plan_id)
    try:
        present = db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_terminals')"))
        if present is None or not settings().v2_terminal_history_test_enabled:
            _pending_preparation(db, p, task, plan)
            old.need(custody._lock_current_reader(db, p))
            return {'state':'pending'}
        terminal._schema(db)
        entry = db.execute(text('SELECT call_id,intent_id FROM public.ga_test_advice_v2_terminals '
            'WHERE workspace_id=:w AND plan_id=:p'), {'w':p.workspace_id, 'p':plan.id}).one_or_none()
        if entry is None:
            _pending_preparation(db, p, task, plan)
            old.need(custody._lock_current_reader(db, p))
            return {'state':'pending'}
        # The whole inventory is checked, including its existing 1000-row
        # limits; no partial or cursor-truncated result is treated as complete.
        inventory.read(db, p, task)
        prep, _, reservation, intent = custody._read_queued(db, p, task.id, plan.id, prepared=True,
            started=True, reserved_test_only=True, intent_test_only=True, settled_test_only=True)
        old.need(p.role == 'admin' or prep['business_actor_id'] == p.user_id)
        old.need(reservation['cloud_call_id'] == entry.call_id and intent['intent_audit_id'] == entry.intent_id)
        proof = terminal.verify(db, p.workspace_id, entry.call_id, entry.intent_id)
        old.need(proof['plan_id'] == plan.id and proof['status'] == 'succeeded')
        captured = storage.read(db, p.workspace_id, entry.call_id, entry.intent_id)
        old.need(type(captured) is tuple and len(captured) == 3)
        _, _, sealed = captured
        terminal_audit = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.id == proof['terminal_audit_id'], Audit.action == terminal.ACTION))
        old.need(terminal_audit is not None and terminal_audit.data['response_hash'] == digest(sealed))
        opinion, _ = response.parse_saved(sealed, cloud_call_id=entry.call_id,
            intent_audit_id=entry.intent_id, schema=contracts.model_value_schema())
        shown_id = plan.data['advice_start_v2']['preview_id']
        shown = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.id == shown_id, Audit.object_id == plan.id, Audit.action == preview.ACTION))
        old.need(shown is not None)
        cloud = shown.data['preview_record']['cloud_request']['decision_input']
        frame=shown.data['frame_snapshot'];aliases=shown.data['projection']['aliases'];snapshots=task.data['object_snapshots']
        # This also refuses unknown outbound fields, truncated/unresolved links
        # and unverified local field labels; it does not issue a network call.
        human_preview.display(shown.data['prepared']['messages'],frame,aliases,snapshots,shown.data['evidence_parts'])
        result = _public(cloud, opinion, frame, aliases, snapshots)
        # A later change to original bytes, session, or the shared daily ledger
        # invalidates this read; checking twice cannot create authority to send.
        old.need(terminal.verify(db, p.workspace_id, entry.call_id, entry.intent_id) == proof)
        old.need(custody._lock_current_reader(db, p))
        return {'state':'ready', **result, 'usage_tokens':proof['charged_tokens']}
    except (HTTPException, ModelBlocked, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return {'state':'needs_attention'}
