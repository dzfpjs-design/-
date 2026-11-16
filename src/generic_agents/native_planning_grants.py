"""Explicit, release-pinned read-only planner grants. Empty means no tools."""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Annotated
from sqlalchemy import select, or_
from pydantic import Field, StrictBool, ValidationError
from .contracts import Contract, digest
from .models import Capability
from .services import published
from .security import authenticated, Principal, require
from .db import session


class Grant(Contract):
    capability_id: str = Field(min_length=1, max_length=100)
    allow_external: StrictBool = False


def eligible(cap):
    execution = cap.data['execution']
    if (cap.kind not in {'tool', 'specialist_model'}
        or execution.get('adapter') not in {'builtin', 'http', 'mcp'}
        or execution.get('side_effect') or execution.get('query_tool')
        or not cap.data['allowed_agent_roles']):
        raise HTTPException(422, '智能计划目前只支持已登记的只读工具；设备、写入、专用查询链和原件模型需使用各自入口')
    from .business_tools import validate_bindings
    validate_bindings(cap.data)
    if cap.data['input_schema'].get('type') != 'object':
        raise HTTPException(422, '规划工具必须使用具有命名字段的输入契约')
    external = execution['adapter'] in {'http', 'mcp'}
    if external and not cap.data['data_policy'].get('external_allowed'):
        raise HTTPException(422, '此工具尚未批准向登记服务发送数据')
    return external


def pin(db, p, raw):
    if not isinstance(raw, list) or len(raw) > 12:
        raise HTTPException(422, '每个人工步骤可指定最多12个规划工具')
    result = []
    seen = set()
    for item in raw:
        # Compiled fields are generated only by the server, never accepted as authority.
        if not isinstance(item, dict):
            raise HTTPException(422, '规划工具授权格式无效')
        try:
            value = Grant.model_validate(item)
        except ValidationError:
            raise HTTPException(422, '规划工具仅需明确能力及外发许可；版本证明由服务器固定')
        if value.capability_id in seen:
            raise HTTPException(422, '同一步骤不能重复指定规划工具')
        cap = published(db, p, value.capability_id)
        external = eligible(cap)
        if external and not value.allow_external:
            raise HTTPException(422, '请明确允许该规划工具向登记服务发送绑定输入')
        seen.add(cap.id)
        result.append({'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'release_version': cap.release_version, 'name': cap.data['description'],
            'allow_external': value.allow_external, 'external': external})
    return result


def current(db, p, task):
    """A grant is usable only at the original task's current human step."""
    from .native_goals import guard
    from .workspace_api import execution, current_step
    require(p, 'admin', 'operator')
    guard(db, p, task)
    _, release, business = execution(db, p, task.id)
    step = current_step(task, business)
    if step['agent'] != '人工确认':
        raise HTTPException(409, '请等待当前自动步骤结束后再生成智能计划')
    if p.role != 'admin' and release.data['snapshot']['roles'][step['role']]['user_id'] != p.user_id:
        raise HTTPException(403, '请由当前步骤负责人或说明代办原因的管理员生成计划')
    package = published(db, p, task.package_id, 'domain_package')
    spec = package.data['specification']
    expected = step.get('planning_tools', [])
    fixed = spec.get('planning_grants', {}).get(business['id'], {}).get(step['id'], [])
    if expected != fixed:
        raise HTTPException(409, '原步骤规划工具与生成版本授权不一致')
    request = [{'capability_id': x['capability_id'], 'allow_external': x['allow_external']} for x in expected]
    actual = pin(db, p, request)
    if actual != expected:
        raise HTTPException(409, '原步骤规划工具版本或数据外发策略已变化')
    allowed = set(spec.get('business_tool_ids', [])) & set(package.data['dependencies'])
    if {x['capability_id'] for x in actual} - allowed:
        raise HTTPException(409, '规划工具不在原业务包固定依赖和授权内')
    return release, business, step, [published(db, p, x['capability_id']) for x in actual]


def options(db, p, selected=(), search=''):
    query = select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.status == 'published', Capability.kind.in_(['tool', 'specialist_model']),
        Capability.data['execution']['adapter'].as_string().in_(['builtin', 'http', 'mcp']),
        or_(Capability.data['execution']['side_effect'].as_string().is_(None), Capability.data['execution']['side_effect'].as_string() == 'false'),
        or_(Capability.data['execution']['query_tool'].as_string().is_(None), Capability.data['execution']['query_tool'].as_string() == 'false'))
    catalog = query.where(or_(Capability.name.ilike('%'+search+'%'),
        Capability.data['description'].as_string().ilike('%'+search+'%'))) if search else query
    rows = list(db.scalars(catalog.order_by(Capability.created_at.desc(), Capability.id).limit(201)))
    has_more = len(rows) > 200
    rows = rows[:200]
    # An old selected capability is not declared unavailable merely because a catalog is long.
    selected_rows = list(db.scalars(query.where(Capability.id.in_(selected)))) if selected else []
    rows += [row for row in selected_rows if row.id not in {item.id for item in rows}]
    result = []
    for row in rows:
        try:
            cap = published(db, p, row.id)
            external = eligible(cap)
        except HTTPException:
            continue
        result.append({'id': cap.id, 'name': cap.data['description'], 'version': cap.release_version,
            'external': external, 'destination': cap.data['execution'].get('endpoint', '') if external else '',
            'agents': list(cap.data['allowed_agent_roles'])})
    return {'items': result, 'has_more': has_more}


router = APIRouter(prefix='/api/workspace')


@router.get('/planning-tool-options')
def available(p: Annotated[Principal, Depends(authenticated)], db: Annotated[object, Depends(session)],
        selected: Annotated[list[str] | None, Query(max_length=12)] = None,
        q: Annotated[str, Query(max_length=100)] = ''):
    return options(db, p, selected or [], q.strip())
