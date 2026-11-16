"""Normal authenticated flow source/preview/adopt/original-receipt endpoints."""
import copy
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select

from . import business_flow_execution_adoption as service
from . import business_flow_nested_adoption_v195 as nested
from . import business_flow_qualification_store as qualifications
from . import business_flow_execution_drafts as drafts
from .contracts import digest
from .db import session
from .security import Principal, authenticated, require

router = APIRouter(prefix=service.PREFIX)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def context(db, p, qualification_id):
    require(p, 'admin', 'operator')
    value = qualifications.get(db, p, qualification_id)
    service.need(value.get('schema') != nested.review.RESULT,
        '递归流程请进入专用执行设计入口；未解释为旧线性图', 422)
    row = service.current_configuration(db, p)
    inventory = service._inventory(db, p)
    current_origins = row.data.get(service.CONFIG_KEY, {})
    service.need(type(current_origins) is dict and all(type(v) is dict
        and type(v.get('adoption_id')) is str for v in current_origins.values()) and
        {v.get('adoption_id') for v in current_origins.values() if type(v) is dict} ==
        {r.id for r in inventory if r.configuration_id == row.id})
    adopted = []
    from .instance_compiler import names
    for name, origin in current_origins.items():
        binding = service.checked_origin(db, p, origin)
        service.need(name == binding['business_name'] and name in names(row.data)
            and service.reviews._same(row.data.get('flows', {}).get(name), binding['flow']))
        if binding['qualification_id'] != qualification_id: continue
        service.need(binding['qualification_hash'] == value['qualification_hash'])
        record = next(r for r in inventory if r.id == origin['adoption_id'])
        adopted.append({'flow_id': binding['flow_id'], 'business_name': name, 'adoption_id': record.id,
            'accepted_revision': record.accepted_revision, 'qualification_id': qualification_id,
            'qualification_hash': binding['qualification_hash'], 'binding_hash': binding['binding_hash']})
    return {'schema': 'business-flow-execution-context-v1', 'qualification_id': qualification_id,
        'qualification_hash': value['qualification_hash'], 'source_value': copy.deepcopy(value['value']),
        'configuration_revision': row.version, 'configuration_hash': digest(row.data),
        'adopted_flows': adopted,
        'current_source_checked': False, 'can_apply': False, 'can_execute': False}


def _nested_catalog(db, p):
    """Public current member/capability choices, never credentials or grants."""
    from .models import User, Membership, Capability
    from .workflow_abilities import eligible, eligible_service, external
    people = db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator']))).all()
    members = [{'user_id': user.id, 'username': user.username, 'membership_role': membership.role}
        for user, membership in people]
    capabilities = []
    extractor_options = []
    rows = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.status == 'published', Capability.kind.in_(['tool', 'specialist_model']))))
    service.need(len(members) <= 2000 and len(rows) <= 2000, '成员或能力清单超过当前界面容量；未截断', 422)
    for cap in sorted(rows, key=lambda row: row.id):
        data, execution = cap.data, cap.data['execution']
        roles = data['allowed_agent_roles']
        usable = []
        for role in roles:
            try:
                if execution['adapter'] == 'workflow_service':
                    eligible_service(cap, role, service_actor=execution.get('service_actor'),
                        operation=execution.get('operation'))
                elif execution['adapter'] == 'task_archive':
                    eligible(cap, role, allow_task_archive=True)
                elif execution['adapter'] == 'task_evidence_packet':
                    eligible(cap, role, allow_task_packet=True)
                elif execution['adapter'] == 'runtime_document_packet':
                    eligible(cap, role, allow_document_packet=True)
                else:
                    eligible(cap, role)
            except HTTPException as error:
                if error.status_code != 409:
                    raise
            else:
                usable.append(role)
        public = {'id': cap.id, 'name': cap.name, 'kind': cap.kind,
            'release_version': cap.release_version, 'hash': cap.content_hash,
            'allowed_agent_roles': copy.deepcopy(roles), 'eligible_agent_roles': usable,
            'adapter': execution['adapter'], 'input_schema': copy.deepcopy(data['input_schema']),
            'output_schema': copy.deepcopy(data['output_schema']),
            'registered_input_bindings': copy.deepcopy(execution.get('input_bindings', {})),
            'max_attempts': data['budget'].get('max_attempts', 2), 'external': external(cap),
            'requires_approved_plan': execution.get('side_effect') is True,
            'external_allowed': data['data_policy'].get('external_allowed') is True,
            'cloud_allowed': data['data_policy'].get('cloud_allowed') is True}
        if execution['adapter'] == 'workflow_service':
            public.update(service_actor=execution.get('service_actor'), operation=execution.get('operation'))
        capabilities.append(public)
        if 'perception' in usable and nested.is_local_extractor(cap):
            extractor_options.append({'capability_id': cap.id, 'capability_hash': cap.content_hash,
                'release_version': cap.release_version, 'name': cap.name})
    return sorted(members, key=lambda row: row['user_id']), capabilities, extractor_options


def nested_context(db, p, qualification_id, flow_index):
    require(p, 'admin', 'operator')
    package = service.sources.fresh_nested(db, p, qualification_id)
    plan, q, flow = nested.selected(package, flow_index)
    walked = nested.plans.walk_action_ports(plan, scope_binding=package['scope_binding'])
    action_ids = nested.compiler.graph_action_ids(plan, flow_index, scope_binding=package['scope_binding'])
    actions = [{**copy.deepcopy(row), 'graph_node_id': action_ids[row['path']]}
        for row in walked['actions'] if row['flow_index'] == flow_index]
    row = service.current_configuration(db, p)
    inventory = service._inventory(db, p)
    current_origins = row.data.get(service.CONFIG_KEY, {})
    service.need(type(current_origins) is dict and all(type(value) is dict
        and type(value.get('adoption_id')) is str for value in current_origins.values())
        and {value['adoption_id'] for value in current_origins.values()}
            == {record.id for record in inventory if record.configuration_id == row.id})
    adopted = []
    from .instance_compiler import names
    for name, issued in current_origins.items():
        binding = service.checked_origin(db, p, issued)
        service.need(name == binding['business_name'] and name in names(row.data)
            and service.reviews._same(row.data.get('flows', {}).get(name), binding['flow']))
        if binding['qualification_id'] != qualification_id:
            continue
        service.need(binding['schema'] == nested.BINDING and binding['qualification_hash'] == q['qualification_hash'])
        record = next(record for record in inventory if record.id == issued['adoption_id'])
        adopted.append({'flow_index': binding['flow_index'], 'business_name': name, 'adoption_id': record.id,
            'accepted_revision': record.accepted_revision, 'qualification_id': qualification_id,
            'qualification_hash': q['qualification_hash'], 'binding_hash': binding['binding_hash']})
    controls = []
    def collect(phases, prefix):
        for i, phase in enumerate(phases):
            path = prefix + '/' + str(i)
            if phase['kind'] != 'steps':
                controls.append({'path': path, 'kind': phase['kind'],
                    'branches': [{'branch_index': j, 'condition': branch.get('condition')}
                        for j, branch in enumerate(phase.get('branches', []))],
                    'wait_mode': phase.get('wait_mode'), 'repeat_if': phase.get('repeat_if'),
                    'finish_if': phase.get('finish_if')})
            for j, branch in enumerate(phase.get('branches', [])):
                collect(branch['phases'], path + '/branches/' + str(j) + '/phases')
    collect(flow['phases'], '/business/flows/' + str(flow_index) + '/phases')
    members, caps, extractor_options = _nested_catalog(db, p)
    modes = nested.allowed_entry_modes(package, flow_index)
    issues = []
    if modes == ['authorized_anomaly_handoff']:
        issues.append({'code': 'anomaly_route_requires_live_binding', 'path': '/entry_mode',
            'message': '原流程为事件触发；采用设计不会建立真实异常交接，路由核验完成前不能启动'})
    if not modes:
        issues.append({'code': 'trigger_not_connected', 'path': '/entry_mode',
            'message': '此触发类型尚未接入实际启动路径，可以核对资料但不能默认改成人工触发'})
    return {'schema': nested.CONTEXT, 'qualification_id': qualification_id,
        'qualification_hash': q['qualification_hash'], 'review_mode': q['review_mode'],
        'simulation_only': q['simulation_only'], 'value_schema': nested.plans.PLAN, 'flow_index': flow_index,
        'name': flow['name'], 'source_value': copy.deepcopy(plan), 'source_flow': copy.deepcopy(flow),
        'scope_binding': copy.deepcopy(package['scope_binding']), 'action_ports': actions, 'controls': controls,
        'all_action_ports': copy.deepcopy(walked['actions']), 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(package['scope_binding']),
        'roles': sorted({action['role'] for action in actions}), 'members': members, 'capabilities': caps,
        'observation_extractor_options': extractor_options if modes == ['engineering_manual'] else [],
        'allowed_entry_modes': modes, 'issues': issues, 'configuration_revision': row.version,
        'configuration_hash': digest(row.data), 'adopted_flows': adopted, 'current_source_checked': True,
        'semantic_quality_verified': False, 'can_apply': False, 'can_execute': False}


@router.get('/qualifications/{qualification_id}/context')
async def context_route(qualification_id: UUID, p: P, db: DB):
    return await run_in_threadpool(context, db, p, str(qualification_id))


@router.post('/preview')
async def preview_route(request: Request, p: P, db: DB):
    body = service.parse(await request.body(), service.Preview)
    return (await run_in_threadpool(service.prepare, db, p, body))[1]


@router.post('/adopt')
async def adopt_route(request: Request, p: P, db: DB):
    return await run_in_threadpool(service.adopt, db, p, await request.body())


@router.get('/requests/{request_id}')
async def recover_route(request_id: str, request: Request, p: P, db: DB):
    service.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1, code=422)
    return await run_in_threadpool(service.recover, db, p, request_id, request.query_params['expected_sha256'])


@router.get('/qualifications/{qualification_id}/flows/{flow_id}/draft')
async def draft_route(qualification_id: UUID, flow_id: str, p: P, db: DB):
    return await run_in_threadpool(drafts.get, db, p, str(qualification_id), flow_id)


@router.post('/drafts/save')
async def save_draft_route(request: Request, p: P, db: DB):
    raw = await request.body()
    try: drafts.parse(raw)
    except ValueError as error: raise HTTPException(422, str(error)) from None
    return await run_in_threadpool(drafts.save, db, p, raw)


@router.get('/draft-requests/{request_id}')
async def draft_request_route(request_id: str, request: Request, p: P, db: DB):
    service.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1, code=422)
    return await run_in_threadpool(drafts.recover, db, p, request_id, request.query_params['expected_sha256'])


@router.get('/nested-context')
async def nested_context_route(request: Request, p: P, db: DB):
    service.need(set(request.query_params) == {'qualification_id', 'flow_index'}
        and all(len(request.query_params.getlist(key)) == 1 for key in ('qualification_id', 'flow_index')), code=422)
    qualification_id = request.query_params['qualification_id']
    try:
        service.reviews.identity(qualification_id)
        position = request.query_params['flow_index']
        service.need(position in {str(i) for i in range(16)}, '请选择明确的原流程位置', 422)
        flow_index = int(position)
    except ValueError:
        raise HTTPException(422, '核对编号或原流程位置格式不符') from None
    return await run_in_threadpool(nested_context_read_scope, db, p, qualification_id, flow_index)


def nested_context_read_scope(db, p, qualification_id, flow_index):
    # Only pure, exact-content parser/catalog work is shared in this synchronous
    # operation. All IAM, inventories, current materials and audits still run.
    # The qualification UUID labels a lifetime; it is not an authority handle.
    from .evidence_request_scope_v191 import catalog_read_scope
    from .report_text_parse_scope import report_text_parse_scope
    with catalog_read_scope(p, qualification_id), report_text_parse_scope():
        return nested_context(db, p, qualification_id, flow_index)


@router.post('/nested-preview')
async def nested_preview_route(request: Request, p: P, db: DB):
    body = service.parse(await request.body(), nested.Preview)
    return (await run_in_threadpool(service.prepare, db, p, body))[1]


@router.post('/nested-adopt')
async def nested_adopt_route(request: Request, p: P, db: DB):
    return await run_in_threadpool(service.adopt_nested, db, p, await request.body())


@router.get('/nested-requests/{request_id}')
async def nested_recover_route(request_id: str, request: Request, p: P, db: DB):
    service.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1, code=422)
    return await run_in_threadpool(service.recover_nested, db, p, request_id, request.query_params['expected_sha256'])


from .business_flow_nested_configuration_api import router as nested_configuration_routes
router.include_router(nested_configuration_routes)
