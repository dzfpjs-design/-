"""Authenticated v2 preparation and preview in the disposable test DB.

This does not install a sender or authorize a cloud call.  All writes use the
ordinary workspace session and CSRF dependencies and the original services.
"""
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, or_, select

from . import evidence
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_common_inventory_v2 as inventory
from . import same_goal_advice_context_v310 as context
from . import same_goal_advice_file_intent_candidate_v2 as file_intent
from . import same_goal_advice_human_preview_v2 as human_preview
from . import same_goal_advice_prepare_document_candidate_v2 as document
from . import same_goal_advice_prepare_writer_candidate_v2 as writer
from . import same_goal_advice_preview_v307 as legacy_preview
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_analysis_candidate_v2 as frame
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_unlimited_preview_candidate_v2 as preview
from . import same_goal_advice_unlimited_store_v2 as custody
from . import workflow_evidence_consent_wait_v199 as consent
from .config import settings
from .contracts import digest
from .goals import snapshot
from .models import Audit, Plan, Task
from .setup_api import DB, P
from .temporal import instant

router = APIRouter()
BASE = '/tasks/{task_id}/business-advice-v2'


class Preparation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request: contracts.Prepare
    policy_ref: contracts.Ref


class Consent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: contracts.ID
    expected_version: contracts.Positive
    reason: contracts.Text
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError('请先确认授权')
        return value


def _enabled(db):
    if not settings().v2_advice_setup_test_enabled:
        raise HTTPException(404, '当前服务尚未启用')
    writer.require_disposable_database(db)


def _prepared(db, p, task_id, plan_id):
    old._reader(db, p)
    task = old.existing._task(db, p, task_id)
    plan = db.scalar(select(Plan).where(Plan.workspace_id == p.workspace_id,
        Plan.task_id == task.id, Plan.id == old.existing._id(plan_id))
        .execution_options(populate_existing=True))
    if plan is None:
        raise HTTPException(404, '建议申请不存在')
    return task, plan


@old.checked
def _disabled_has_unfinished(db, p, task):
    """Conservatively match authenticated policies to verified terminal custody.

    The caller holds the Task read lock for this entire routine. All policy,
    authorization and v2 plan writers take the same Task lock before commit.
    """
    now = db.scalar(select(func.clock_timestamp()))
    original = inventory._read(db, p, task.id, include_closed_policy_ids=True)
    policies = old._rows(db, p, Audit, Audit.action == policy.POLICY, Audit.object_id == task.id)
    authorizations = old._rows(db, p, Audit, Audit.action == policy.AUTHORIZATION, Audit.object_id == task.id)
    by_id = {row.id: row for row in policies}
    old.need(len(by_id) == len(policies))
    authorized = set()
    for event in authorizations:
        data = event.data
        old.need(type(data) is dict and type(data.get('policy_ref')) is dict)
        ref = data['policy_ref']; row = by_id.get(ref.get('id'))
        old.need(row is not None and row.id not in authorized and row.actor_id == event.actor_id
            and row.created_at == event.created_at and ref == {'id':row.id, 'record_hash':digest(row.data)})
        value, _ = policy.policy_shape(row, now)
        body = policy.Authorize.model_validate(data.get('body')).model_dump(mode='json')
        expected = {'schema':policy.AUTHORIZATION_SCHEMA, 'workspace_id':p.workspace_id,
            'task_id':task.id, 'actor_id':event.actor_id, 'request_id':body['request_id'],
            'body':body, 'body_hash':digest(body), 'policy_ref':ref,
            'issued_at':value['issued_at'], 'cloud_send_authorized':False}
        old.need(data == expected and event.workspace_id == p.workspace_id
            and event.object_id == task.id and value['scope']['workspace_id'] == p.workspace_id
            and value['scope']['task_id'] == task.id
            and value['scope']['goal_hash'] == body['expected_goal_hash']
            and value['mature_head_hash'] == body['expected_mature_head_hash']
            and value['expires_at'] == instant(body['expires_at']).isoformat())
        authorized.add(row.id)
    # Orphaned or ambiguous audit records never establish an empty state.
    old.need(len(authorized) == len(by_id), '原建议授权记录尚无法核对')
    closed = set(original['closed_policy_ids'])
    old.need(closed <= set(by_id), '原建议终态关联尚无法核对')
    return bool(original['queued_v2'] or any(
        row.id not in closed or instant(row.data['expires_at']) > now for row in policies))


@router.get(BASE + '/availability')
def availability(task_id: str, p: P, db: DB):
    if settings().v2_advice_setup_test_enabled:
        return {'enabled': True}
    old._reader(db, p)
    task = db.scalar(select(Task).where(Task.workspace_id == p.workspace_id,
        Task.id == old.existing._id(task_id)).with_for_update(read=True)
        .execution_options(populate_existing=True))
    if task is None:
        raise HTTPException(404, '任务不存在')
    return {'enabled':False, 'has_unfinished':_disabled_has_unfinished(db, p, task)}


@router.get(BASE + '/context')
def read_context(task_id: str, p: P, db: DB):
    if not settings().v2_advice_setup_test_enabled:
        return {'enabled': False}
    _enabled(db)
    old._reader(db, p)
    task = old.existing._task(db, p, task_id)
    selected = old.provider.binding(db, p, task)
    old.need(selected is not None, '业务配置尚未就绪')
    released = context._released(db, p, selected)
    old.need(released['model'].data['execution']['provider'] == 'deepseek', '当前模型不可用')
    mature = old.existing._mature_head(db, p, task, selected)
    choices = context._approved_sources(db, p, task, selected)
    goal = snapshot(task)
    now = db.scalar(select(func.clock_timestamp()))
    end = now + timedelta(hours=2)
    if goal['spec'].get('deadline'):
        end = min(end, instant(goal['spec']['deadline']))
    old.need(end > now, '该任务已到期')
    maximum = released['model'].data['execution']['max_tokens']
    old.need(type(maximum) is int and maximum >= 128)
    return {'enabled': True, 'title': task.title, 'goal_hash': goal['hash'],
        'head_hash': mature['head']['head_hash'], 'sources': [{'title': item['title'],
        'selection': item['selection']} for item in choices],
        'model': released['model'].name, 'max_tokens': min(8192, maximum),
        'expires_at': end.isoformat(), 'can_prepare': p.role == 'admin', 'can_start': False}


@router.get(BASE + '/preparations')
def list_preparations(task_id: str, p: P, db: DB):
    _enabled(db)
    task = old.existing._task(db, p, task_id)
    inventory.read(db, p, task)  # authenticate the full, original inventory
    plans = old._rows(db, p, Plan, Plan.task_id == task.id, Plan.data['schema'].as_string() == custody.PLAN)
    result = []
    for plan in plans:
        if plan.status == 'queued':
            prep, _ = custody.read_queued(db, p, task, plan)
        elif plan.status == 'awaiting_consent':
            prep, _ = custody.read_prepared(db, p, task, plan)
        else:
            raise HTTPException(409, '历史建议需要核对')
        if p.role == 'admin' or p.user_id in {prep['business_actor_id'], prep['data_actor_id']}:
            result.append({'plan_id': plan.id, 'request_id': prep['request']['request_id'],
                'phase': 'waiting' if plan.status == 'queued' else 'review'})
    return {'items': result}


@router.post(BASE + '/policy', status_code=201)
def authorize_policy(task_id: str, body: policy.Authorize, p: P, db: DB):
    _enabled(db)
    result = policy.authorize(db, p, task_id, body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE + '/preparations', status_code=201)
def prepare(task_id: str, body: Preparation, p: P, db: DB):
    _enabled(db)
    result = writer.prepare(db, p, task_id, body.request.model_dump(mode='json'),
        body.policy_ref.model_dump(mode='json'))
    db.commit()
    return result


@router.get(BASE + '/preparations/{plan_id}')
def status(task_id: str, plan_id: str, p: P, db: DB):
    _enabled(db)
    task, plan = _prepared(db, p, task_id, plan_id)
    if plan.status == 'queued':
        prep, job = custody.read_queued(db, p, task, plan)
        phase = 'waiting'
    elif plan.status == 'awaiting_consent':
        prep, job = custody.read_prepared(db, p, task, plan)
        phase = 'review'
    else:
        # In particular, don't infer outcome from a parked or unknown sender.
        raise HTTPException(409, '该申请需要核对')
    old.need(p.role == 'admin' or p.user_id in {prep['business_actor_id'], prep['data_actor_id']})
    return {'plan_id': plan.id, 'job_id': job.id, 'phase': phase, 'version': plan.version,
        'goal_hash': prep['scope']['goal_hash'], 'head_hash': prep['mature_head']['head_hash'],
        'can_process': job.status == 'queued' and p.user_id == prep['data_actor_id'],
        'can_preview': phase == 'review' and (p.role == 'admin' or p.user_id == prep['business_actor_id']),
        'can_start': False}


@router.post(BASE + '/preparations/{plan_id}/documents')
def prepare_documents(task_id: str, plan_id: str, p: P, db: DB):
    _enabled(db)
    task, plan = _prepared(db, p, task_id, plan_id)
    if plan.status == 'awaiting_consent':
        prep, job = custody.read_prepared(db, p, task, plan)
        old.need(p.user_id == prep['data_actor_id'])
        return {'plan_id': plan.id, 'phase': 'review', 'replayed': True}
    prep, job = custody.read_queued(db, p, task, plan)
    old.need(p.user_id == prep['data_actor_id'])
    if prep['request']['document_source']['kind'] == 'approved_source_set':
        file_intent.register(db, p, task.id, job.id)
        db.commit()  # Original file intent must be durable before file I/O.
    result = document.process(db, p, task.id, job.id)
    db.commit()
    return {'plan_id': plan_id, 'phase': 'review', 'replayed': result['replayed']}


@router.get(BASE + '/preparations/{plan_id}/documents')
def read_documents(task_id: str, plan_id: str, p: P, db: DB):
    _enabled(db)
    task, plan = _prepared(db, p, task_id, plan_id)
    prep, _ = custody.read_prepared(db, p, task, plan)
    current = frame.build_current(db, p, task, plan)
    model = legacy_preview._model(db, p, prep, live=True)
    documents = []
    for identity in current['evidence_ids']:
        item, raw = evidence.evidence_bytes(db, p, identity)
        old.need(len(raw) <= 50000)
        text = raw.decode('utf-8')
        documents.append({'id': item.id, 'version': item.version, 'text': text,
            'authorized': bool(consent._grants(db, item, model))})
    return {'plan_id': plan.id, 'model': model.data['execution']['model'], 'documents': documents}


@router.post(BASE + '/preparations/{plan_id}/consent')
def authorize_document(task_id: str, plan_id: str, body: Consent, p: P, db: DB):
    _enabled(db)
    task, plan = _prepared(db, p, task_id, plan_id)
    prep, _ = custody.read_prepared(db, p, task, plan)
    current = frame.build_current(db, p, task, plan)
    old.need(body.evidence_id in current['evidence_ids'])
    result = evidence.authorize_cloud(db, p, body.evidence_id, prep['model_pin']['id'],
        body.expected_version, body.reason)
    db.commit()
    return {'authorized': True, 'version': result.version}


@router.post(BASE + '/preparations/{plan_id}/preview', status_code=201)
def prepare_preview(task_id: str, plan_id: str, body: legacy_preview.PreviewRequest, p: P, db: DB):
    _enabled(db)
    result = preview.prepare(db, p, task_id, plan_id, body.model_dump(mode='json'))
    task, plan = _prepared(db, p, task_id, plan_id)
    prep, _ = custody.read_prepared(db, p, task, plan)
    events = old._rows(db, p, Audit, Audit.action == preview.ACTION,
        or_(Audit.object_id == plan.id, Audit.data['plan_id'].as_string() == plan.id))
    receipts = old._rows(db, p, Audit, Audit.action == custody.DOCUMENT_PREPARED, Audit.object_id == plan.id)
    old.need(len(receipts) == 1)
    chain = preview.validate_chain(db, p, task, plan, prep, receipts[0].data, events)
    matches = [value for _, value in chain if value['request']['request_id'] == body.request_id]
    old.need(len(matches) == 1 and matches[0]['preview_record']['preview_hash'] == result['preview_hash'])
    value = matches[0]
    try:
        display = human_preview.display(value['prepared']['messages'], value['frame_snapshot'],
            value['projection']['aliases'], task.data['object_snapshots'], value['evidence_parts'])
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise HTTPException(409, '分析内容暂无法完整呈现，请联系管理员核对') from None
    result.pop('messages', None)
    result['display'] = display
    db.commit()
    return result
