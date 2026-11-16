"""Business-scoped goal tools are metadata, never new execution permission."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from .contracts import digest
from .services import published
from .security import Principal, authenticated
from .db import session


def catalog(db, p, release, business):
    package = published(db, p, release.package_id, 'domain_package')
    if (digest(release.data['snapshot']) != release.configuration_hash
        or package.data['specification'].get('configuration_hash') != release.configuration_hash):
        raise HTTPException(409, '原业务版本内容摘要无法核对')
    if business.get('execution_mode') == 'confirmed_graph_workflow' or 'full_semantic_graph_origin' in business:
        from .workflow_graph_goal_tools_v126 import catalog as graph_catalog
        return graph_catalog(db, p, release, business)
    entries = {}
    dependencies = set(package.data['dependencies'])
    specification = package.data['specification']
    for step in business['flow']['steps']:
        grants = []
        if step['agent'] == '人工确认':
            expected = step.get('planning_tools', [])
            fixed = specification.get('planning_grants', {}).get(business['id'], {}).get(step['id'], [])
            if expected != fixed:
                raise HTTPException(409, '原业务步骤工具授权与业务包不一致')
            if expected:
                from .native_planning_grants import pin
                fresh = pin(db, p, [{'capability_id': x['capability_id'], 'allow_external': x['allow_external']} for x in expected])
                if fresh != expected:
                    raise HTTPException(409, '原业务规划工具版本或外发策略已变化')
                grants = [(g['capability_id'], g['capability_hash'], 'planning_tool') for g in expected]
        else:
            execution = step.get('execution') or {}
            if execution.get('capability_id'):
                grants = [(execution['capability_id'], execution.get('capability_hash'), 'workflow_step')]
        for identity, signature, source in grants:
            if identity not in dependencies or source == 'planning_tool' and identity not in specification.get('business_tool_ids', []):
                raise HTTPException(409, '业务目标工具不在生成版本依赖和明确授权内')
            cap = published(db, p, identity)
            if cap.kind not in {'tool', 'specialist_model'} or cap.content_hash != signature:
                raise HTTPException(409, '业务目标工具类型或固定版本已变化')
            entry = entries.setdefault(identity, {'capability': cap, 'step_names': [], 'sources': []})
            if step['name'] not in entry['step_names']: entry['step_names'].append(step['name'])
            if source not in entry['sources']: entry['sources'].append(source)
    return entries


def metadata(db, p, release, business, *, task=None):
    tools = []
    for identity, item in catalog(db, p, release, business).items():
        cap = item['capability']
        fields = []
        schema = cap.data.get('output_schema', {})
        for key, prop in schema.get('properties', {}).items():
            kind = prop.get('type')
            if isinstance(kind, list):
                nonnull = [value for value in kind if value != 'null']
                kind = nonnull[0] if len(nonnull) == 1 else None
            if kind not in {'number', 'integer', 'string', 'boolean'}: continue
            fields.append({'key': key, 'label': prop.get('title') or key, 'kind': kind,
                'unit': prop.get('unit') if isinstance(prop.get('unit'), str) else None})
        tools.append({'id': identity, 'name': cap.data['description'], 'version': cap.release_version,
            'content_hash': cap.content_hash, 'step_names': item['step_names'], 'sources': item['sources'],
            'output_fields': fields})
    value = {'schema': 'native-goal-tools-v1', 'instance_id': release.instance_id,
        'release_id': release.id, 'business_id': business['id'],
        'configuration_hash': release.configuration_hash, 'tools': tools}
    from sqlalchemy import select
    from .models import Entity,DomainInstance
    from .services import scoped
    from .instance_relation_types import relation_metadata
    package = published(db,p,release.package_id,'domain_package')
    has_proof=task is not None and any(key in task.data for key in ('object_definition_bindings','object_definition_bindings_hash'))
    if has_proof:
        from .entity_release_compat import verify_task_objects
        verify_task_objects(db,p,task,release)
        entities=[scoped(db,Entity,identity,p) for identity in task.data['entity_ids']]
        truncated=False
    elif task is not None:
        entities = db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id,
            Entity.schema_capability_id==package.id).order_by(Entity.id).limit(201)).all()
        truncated=len(entities)>200
    else:
        from .entity_release_compat import EntityReleaseContext
        instance=scoped(db,DomainInstance,release.instance_id,p)
        compatibility=EntityReleaseContext(db,p,instance,release)
        rows=db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id,
            Entity.namespace==instance.namespace).order_by(Entity.id).limit(1001)).all()
        entities=[]
        for entity in rows[:1000]:
            try:compatibility.require(entity)
            except HTTPException:continue
            entities.append(entity)
            if len(entities)>200:break
        truncated=len(entities)>200 or len(rows)>1000
    value.update(relation_metadata(package,entities[:200]))
    value['entities_truncated'] = truncated
    value['relation_types_error'] = None
    from .native_query_catalog import metadata as query_metadata
    try:value.update(query_metadata(db,p,published(db,p,release.package_id,'domain_package')))
    except HTTPException as error:value.update(query_tools=[],query_tools_error=str(error.detail))
    return {**value, 'hash': digest(value)}


def for_task(db, p, task):
    from .workspace_api import saved_execution
    _, release, business = saved_execution(db, p, task.id)
    return metadata(db, p, release, business,task=task)


def validate_criteria(db, p, task, specification):
    if not any(item['kind'] in {'capability_success', 'tool_value'} for item in specification['criteria']):
        return {}
    available = {item['id']: item for item in for_task(db, p, task)['tools']}
    for criterion in specification['criteria']:
        if criterion['kind'] not in {'capability_success', 'tool_value'}: continue
        tool = available.get(criterion.get('capability_id'))
        if not tool:
            raise HTTPException(422, '完成标准只能使用当前业务已固定的工具或模型，不能引用其他业务的能力')
        if criterion['kind'] == 'tool_value':
            field = next((x for x in tool['output_fields'] if x['key'] == criterion.get('field')), None)
            if not field:
                raise HTTPException(422, '工具数值或内容标准需使用登记的单值输出字段')
            expected = criterion.get('expected')
            kind = field['kind']
            if (kind == 'boolean' and not isinstance(expected, bool)
                or kind == 'string' and not isinstance(expected, str)
                or kind in {'number', 'integer'} and (isinstance(expected, bool) or not isinstance(expected, (int, float)))
                or kind == 'integer' and isinstance(expected, (int, float)) and not float(expected).is_integer()
                or kind in {'string', 'boolean'} and criterion.get('operator') != 'eq'):
                raise HTTPException(422, '完成标准的比较方式或期望值类型不符合工具输出契约')
    return available


def workflow_goal_receipts(db, p, task, *, lock_sources=True):
    """Only real registered workflow calls can satisfy an explicit tool criterion."""
    if 'graph_runtime' in task.data:
        from .workflow_graph_goal_tools_v126 import receipts as graph_receipts
        return graph_receipts(db, p, task, lock_sources=lock_sources)
    from types import SimpleNamespace
    import copy
    from .models import Invocation, Job, Entity
    from .services import scoped, validate
    from .workspace_api import saved_execution
    required = {item['capability_id'] for item in task.data['goal_spec']['criteria']
        if item['kind'] in {'capability_success', 'tool_value'}}
    if not required: return []
    candidates = task.data['workflow_runtime']['receipts']
    if not any(item.get('execution_mode') == 'registered_ability' and item.get('capability_id') in required for item in candidates):
        return []
    _, release, business = saved_execution(db, p, task.id)
    allowed = catalog(db, p, release, business)
    from .native_goals import verify_workflow_inputs
    verify_workflow_inputs(db, p, task, lock_sources=lock_sources)
    for original in task.data.get('object_snapshots', []):
        obj = scoped(db, Entity, original['id'], p)
        if obj.version != original['version'] or obj.data != original['attributes']:
            raise HTTPException(409, '原流程工具所用对象版本已变化，旧结果不能满足当前工具目标')
    result = []
    for index, receipt in enumerate(candidates):
        if receipt.get('execution_mode') != 'registered_ability' or receipt.get('capability_id') not in required: continue
        entry = allowed.get(receipt['capability_id'])
        step = next((s for s in business['flow']['steps'] if s['id'] == receipt['step_id']), None)
        if not entry or not step or step['agent'] == '人工确认' or step['execution']['capability_id'] != receipt['capability_id']:
            raise HTTPException(409, '原流程工具回执不属于当前业务固定节点')
        cap = entry['capability']
        call = scoped(db, Invocation, receipt['invocation_id'], p)
        job = scoped(db, Job, receipt['request_id'], p)
        scope = job.data.get('scope', {})
        if (job.kind != 'execute_workflow_step' or job.data.get('task_id') != task.id
            or scope.get('step_id') != step['id'] or scope.get('visit') != index
            or scope.get('input_hash') != receipt['input_hash'] or scope.get('inputs') != receipt['input_snapshot']
            or call.idempotency_key != job.data.get('invocation_key')
            or call.data.get('job_id', job.id) != job.id
            or call.status != 'succeeded' or call.capability_id != cap.id
            or call.data.get('role') != step['execution']['agent_role']
            or call.data.get('capability_hash') != cap.content_hash
            or receipt['capability_hash'] != cap.content_hash
            or call.data.get('input_hash') != digest(receipt['input_snapshot'])
            or call.data.get('output') != receipt['output']):
            raise HTTPException(409, '原流程真实调用、输入或输出无法核对，不能作为工具目标结果')
        # Rebind only the prefix available when the call actually ran, including loops.
        prior = SimpleNamespace(id=task.id, data={**task.data, 'workflow_runtime': {
            **task.data['workflow_runtime'], 'receipts': copy.deepcopy(candidates[:index])}})
        from .workflow_abilities import bind
        if bind(prior, step, db, p, call.idempotency_key) != receipt['input_snapshot']:
            raise HTTPException(409, '原流程工具输入来源已变化，不能复用旧结果')
        validate(cap.data['output_schema'], receipt['output'])
        result.append({'status': 'succeeded', 'method': 'registered_capability',
            'capability_id': cap.id, 'capability_name': cap.data['description'],
            'capability_hash': cap.content_hash, 'invocation_id': call.id,
            'input_hash': receipt['input_hash'], 'output': copy.deepcopy(receipt['output']),
            'workflow_step_id': step['id'], 'workflow_receipt_hash': digest(receipt),
            'candidate_only': receipt.get('candidate_only', False), 'physical_state_verified': False})
    return result


router = APIRouter(prefix='/api/workspace/instances')


@router.get('/{instance_id}/businesses/{business_id}/goal-tool-options')
def options(instance_id: str, business_id: str,
        p: Annotated[Principal, Depends(authenticated)], db: Annotated[object, Depends(session)],
        release_id: Annotated[str, Query(min_length=1, max_length=100)]):
    from .instance_api import active
    instance, release = active(db, p, instance_id)
    if release.id != release_id:
        raise HTTPException(409, '实例版本已变化，请核对新版本后再确定工具完成标准')
    business = next((x for x in release.data['snapshot']['businesses'] if x['id'] == business_id), None)
    if not business or not business['available'] or business_id not in instance.data.get('enabled_business_ids', []):
        raise HTTPException(409, '当前业务尚未启用，不能选择工具完成标准')
    return metadata(db, p, release, business)
