"""User confirmation of draft business content, independent of publication.

This is not evidence verification, a formal flow signature, or permission to
run equipment.  Each stored review is tied to the saved flow and is removed
when its contents or basis change.
"""
import copy
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from .contracts import Contract
from .db import session
from .models import AuthSession, Membership, SetupMaterial, User, now
from .security import Principal, authenticated, require
from .services import check_version, scoped

FIELD = 'candidateContentReview'
router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


class ContentReview(Contract):
    revision: int = Field(strict=True, ge=1)
    business: str = Field(min_length=1, max_length=100)
    action: Literal['confirm', 'clear']


def _lock_actor(db, p):
    """Keep the HTTP session and write role current until the review commits."""
    query = (select(User.id).join(Membership, Membership.user_id == User.id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(User.id == p.user_id, User.active.is_(True),
               Membership.workspace_id == p.workspace_id, Membership.role.in_(['admin', 'operator']),
               AuthSession.id == p.session_id, AuthSession.revoked.is_(False),
               AuthSession.expires_at > func.clock_timestamp())
        .with_for_update(read=True, of=[User, Membership, AuthSession]))
    if db.scalar(query.execution_options(populate_existing=True)) != p.user_id:
        raise HTTPException(403, '当前账号或权限已变化，请重新登录')


def _signature(configuration, flow):
    from .instance_compiler import signature_value
    return json.dumps(signature_value(configuration, flow), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def _review_source_ids(flow):
    from .flow_sources import flow_source_ids
    result = set(flow_source_ids(flow))
    for item in [flow, *flow.get('steps', [])]:
        pending = item.get('candidateUnresolvedSources', [])
        if not isinstance(pending, list):
            raise HTTPException(409, '待核依据无法核对，请重新读取草案')
        for source in pending:
            if not isinstance(source, dict) or not isinstance(source.get('material_id'), str):
                raise HTTPException(409, '待核依据无法核对，请重新读取草案')
            result.add(source['material_id'])
    return result


def _existing(previous, name, flow):
    flows = previous.get('flows', {})
    if name in flows:
        return flows[name]
    key = flow.get('candidateKey')
    matches = [value for value in flows.values()
               if isinstance(value, dict) and key and value.get('candidateKey') == key]
    return matches[0] if len(matches) == 1 else None


def reject_client_review_changes(previous, submitted):
    """Only this module's endpoint may add or alter an approval marker."""
    for name, flow in submitted.get('flows', {}).items():
        if not isinstance(flow, dict):
            continue
        before = _existing(previous, name, flow)
        old = before.get(FIELD) if isinstance(before, dict) else None
        if flow.get(FIELD) != old:
            raise HTTPException(409, '草案确认状态已变化，请刷新后重试；原草稿保留')


def invalidate_content_reviews(previous, proposed, *, changed_sources=()):
    """Invalidate on any saved semantic/basis change, never create a review."""
    value = copy.deepcopy(proposed)
    old_materials = {item['id']: item for item in previous.get('materials', [])
                     if isinstance(item, dict) and isinstance(item.get('id'), str)}
    new_materials = {item['id']: item for item in value.get('materials', [])
                     if isinstance(item, dict) and isinstance(item.get('id'), str)}
    changed = set(changed_sources)
    for identity in old_materials.keys() | new_materials.keys():
        before, after = old_materials.get(identity), new_materials.get(identity)
        if before is None or after is None or any(before.get(key) != after.get(key)
                for key in ('version', 'hash', 'archived')):
            changed.add(identity)
    for name, flow in value.get('flows', {}).items():
        if not isinstance(flow, dict) or FIELD not in flow:
            continue
        review = flow[FIELD]
        try:
            valid = (not changed.intersection(_review_source_ids(flow))
                     and type(review) is dict and review.get('business') == name
                     and isinstance(review.get('signature'), str)
                     and json.loads(review['signature']) == json.loads(_signature(value, flow)))
        except (ValueError, TypeError, KeyError, RecursionError, HTTPException):
            valid = False
        if not valid:
            flow.pop(FIELD, None)
    return value


def _reviewable(flow):
    if (not isinstance(flow, dict) or not flow.get('candidateKey')
            or not isinstance(flow.get('trigger'), str) or not flow['trigger'].strip()
            or not isinstance(flow.get('output'), str) or not flow['output'].strip()
            or not isinstance(flow.get('steps'), list) or not 1 <= len(flow['steps']) <= 16):
        return False
    for step in flow['steps']:
        if (not isinstance(step, dict) or not isinstance(step.get('name'), str)
                or not step['name'].strip() or not isinstance(step.get('output'), str)
                or not step['output'].strip() or step.get('agent') not in
                {'业务智能体', '多模感知智能体', '数据管理智能体', '人工确认'}
                or (step.get('agent') == '人工确认'
                    and (not isinstance(step.get('role'), str) or not step['role'].strip()))):
            return False
    return True


@router.post('/candidates/content-review')
def content_review(body: ContentReview, p: P, db: DB):
    require(p, 'admin', 'operator')
    _lock_actor(db, p)
    from .setup_api import draft, draft_view
    from .setup_business_candidates import _save_candidates
    row = draft(db, p, True)
    check_version(row, body.revision)
    value = copy.deepcopy(row.data)
    flow = value.get('flows', {}).get(body.business)
    if not _reviewable(flow):
        raise HTTPException(409, '请先补全业务启动条件、产出和步骤内容')
    if body.action == 'confirm':
        materials = {item['id']: item for item in value.get('materials', [])
                     if isinstance(item, dict) and isinstance(item.get('id'), str)}
        for identity in _review_source_ids(flow):
            pinned = materials.get(identity)
            if not pinned:
                raise HTTPException(409, '关联资料已变化，请重新核对草案')
            current = scoped(db, SetupMaterial, identity, p, True)
            if (current.archived or pinned.get('archived')
                    or pinned.get('version') != current.version or pinned.get('hash') != current.sha256):
                raise HTTPException(409, '关联资料已更新，请重新核对草案')
        try:
            signature = _signature(value, flow)
        except (ValueError, TypeError, KeyError, RecursionError, HTTPException):
            raise HTTPException(409, '草案内容无法核对，请刷新后重试') from None
        flow[FIELD] = {'business': body.business, 'signature': signature,
                       'confirmedAt': now().isoformat(), 'actor_id': p.user_id}
    else:
        flow.pop(FIELD, None)
    if value == row.data:
        db.commit()
        return {'draft': draft_view(row)}
    return {'draft': _save_candidates(db, p, row, value, 'setup.candidate.content_review')}
