"""One eligibility contract for HTTP anomaly menus and approval draft creation.

This is not graph admission or execution authority. Only the route owns the
occurrence, and V175 owns submission, approval, explicit start and acceptance.
"""
import copy
from functools import wraps

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select

from .contracts import digest
from .models import Capability, Entity, SetupMaterial
from .services import published, scoped, validate

SCHEMA = 'signal-approval-target-v247'
MODES = {'confirmed_human_workflow', 'registered_ability_workflow'}
# These registered builtin handlers perform bounded local calculations. Media,
# task artifact builders and data-query tools have different physical inputs
# or authorization contracts and are not admitted by a false side_effect flag.
LOCAL_HANDLERS = {'aggregate_values', 'numeric_rules'}
SOURCES = {'object_records', 'object_attributes', 'object_ids', 'task_identifier',
    'task_context', 'request_key', 'observation_records', 'previous_output'}
_KEYS = {'schema', 'business', 'source_contract_hash', 'entities', 'roles',
    'capabilities', 'materials_hash', 'approval_policy', 'input_provider',
    'required_inputs', 'target_hash'}


def need(value, reason='业务输入、当前人员、能力或正式审批策略不满足仅建草稿条件'):
    if not value:
        raise HTTPException(409, reason)


def closed(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError,
                OverflowError, RecursionError):
            need(False)
    return call


def manual(business):
    """A graph display facade must never masquerade as a manual workflow."""
    steps = business.get('flow', {}).get('steps')
    return (business.get('execution_mode') == 'confirmed_human_workflow'
        and type(steps) is list and bool(steps)
        and all(step.get('agent') == '人工确认' for step in steps))


def covers(producer, consumer):
    """Conservative structural implication, not sample-value validation.

    Only a proved subset is admitted. Complex or unresolved consumer schemas
    need a separately registered input contract; a guessed example is no proof.
    Runtime still validates the full schema against actual received inputs.
    """
    if consumer is True or consumer == {} or producer == consumer:
        return True
    if consumer is False or type(producer) is not dict or type(consumer) is not dict:
        return False
    if 'const' in producer:
        return not list(Draft202012Validator(consumer).iter_errors(producer['const']))
    if 'enum' in producer:
        return all(not list(Draft202012Validator(consumer).iter_errors(value))
                   for value in producer['enum'])
    if 'anyOf' in producer:
        return all(covers(branch, consumer) for branch in producer['anyOf'])
    if 'anyOf' in consumer:
        return any(covers(producer, branch) for branch in consumer['anyOf'])
    annotations = {'title', 'description', '$comment', 'default', 'examples', 'unit', 'x-unit'}
    supported = {'type', 'properties', 'required', 'additionalProperties', 'items',
        'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum',
        'exclusiveMinimum', 'exclusiveMaximum'} | annotations
    if set(consumer) - supported:
        return False
    def kinds(schema):
        value = schema.get('type')
        return {value} if type(value) is str else set(value or [])
    offered, accepted = kinds(producer), kinds(consumer)
    if accepted and (not offered or any(kind not in accepted
            and not (kind == 'integer' and 'number' in accepted) for kind in offered)):
        return False
    for key in ('minimum', 'exclusiveMinimum', 'minLength', 'minItems'):
        if key in consumer and (key not in producer or producer[key] < consumer[key]):
            return False
    for key in ('maximum', 'exclusiveMaximum', 'maxLength', 'maxItems'):
        if key in consumer and (key not in producer or producer[key] > consumer[key]):
            return False
    if 'array' in offered or 'items' in consumer:
        if not covers(producer.get('items', {}), consumer.get('items', {})):
            return False
    if 'object' in offered or {'properties', 'required', 'additionalProperties'} & set(consumer):
        pp, cp = producer.get('properties', {}), consumer.get('properties', {})
        if not set(consumer.get('required', [])) <= set(producer.get('required', [])):
            return False
        pa, ca = producer.get('additionalProperties', True), consumer.get('additionalProperties', True)
        if ca is False and (pa is not False or not set(pp) <= set(cp)):
            return False
        for key, schema in cp.items():
            if key in pp:
                if not covers(pp[key], schema): return False
            elif pa is not False and not covers({} if pa is True else pa, schema):
                return False
        if type(ca) is dict:
            if pa is not False and not covers({} if pa is True else pa, ca): return False
            if any(not covers(schema, ca) for key, schema in pp.items() if key not in cp): return False
    return True


def checked_pin(value):
    need(type(value) is dict and set(value) == _KEYS and value['schema'] == SCHEMA
        and value['target_hash'] == digest({k: v for k, v in value.items() if k != 'target_hash'})
        and type(value['business']) is dict
        and set(value['business']) == {'id', 'name', 'execution_mode'}
        and value['business']['execution_mode'] in MODES
        and value['input_provider'] in {'none', 'registered_archives', 'received_observation_records'}
        and type(value['approval_policy']) is dict
        and set(value['approval_policy']) == {'id', 'version_id', 'hash'}
        and type(value['required_inputs']) is list)
    return copy.deepcopy(value)


def _source_schema(source, contract, previous):
    if source == 'observation_records':
        from .signal_observation_provider_v247 import record_schema
        return record_schema(contract)
    if source == 'object_ids':
        return {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'string'}}
    if source in {'task_identifier', 'request_key'}:
        return {'type': 'string', 'minLength': 1}
    if source == 'object_records':
        return {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'object'}}
    if source in {'object_attributes', 'task_context'}:
        return {'type': 'object'}
    if source == 'previous_output':
        return previous
    need(False, '此数值来源没有节点所需的原图、音视频、运行期资料或其它输入提供器')


def _step_inputs(step, cap, contract, preceding):
    ex = step['execution']; properties = cap.data['input_schema']['properties']
    for field, binding in ex.get('bindings', {}).items():
        source = binding['source']
        need(source in SOURCES, '数值异常不能替代节点所需的图片、音视频或资料原件')
        previous = None
        if source == 'previous_output':
            prior = preceding.get(binding.get('step_id'))
            need(prior is not None and binding['field'] in prior.get('required', []),
                 '前序输入必须来自每次均会先完成的已登记能力必需输出')
            previous = prior.get('properties', {}).get(binding['field'])
            need(type(previous) is dict, '前序输出字段未声明完整Schema')
        need(covers(_source_schema(source, contract, previous), properties[field]),
             '节点输入Schema无法由此来源完整保证；请核对实际输入提供器和字段约束')
    # Cross-field conditionals cannot be proved by one valid example.
    top = set(cap.data['input_schema']) - {'type', 'properties', 'required',
        'additionalProperties', 'title', 'description', '$schema', '$defs'}
    need(not top, '此节点包含尚未为自动草稿输入证明的组合Schema约束')


def calculation_fields(step, cap, contract):
    """The implemented aggregation handler reads one explicit domain key."""
    ex = step['execution']
    if (cap.data['execution'].get('handler') == 'aggregate_values'
        and ex.get('bindings', {}).get('records', {}).get('source') == 'observation_records'):
        field = ex.get('arguments', {}).get('field')
        need(type(field) is str and field in contract['field_units']
             and 'field' not in ex.get('bindings', {}),
             '聚合字段须明确固定为所选来源的原领域字段；不采用同名档案字段或猜测前序结果')


def dominators(steps):
    """Prove mandatory producers using real next/else edges, including loops."""
    ids = [step['id'] for step in steps]
    need(bool(ids) and len(ids) == len(set(ids)))
    predecessors = {identity: set() for identity in ids}
    edges = {}
    for index, step in enumerate(steps):
        targets = [step.get('nextId', 'auto')]
        if step.get('condition'): targets.append(step.get('elseId', 'end'))
        edges[step['id']] = set()
        for target in targets:
            if target == 'auto': target = ids[index + 1] if index + 1 < len(ids) else 'end'
            if target == 'end': continue
            need(target in predecessors)
            predecessors[target].add(step['id']); edges[step['id']].add(target)
    reachable, pending = set(), [ids[0]]
    while pending:
        identity = pending.pop()
        if identity in reachable: continue
        reachable.add(identity); pending.extend(edges[identity])
    result = {identity: ({identity} if identity == ids[0] else set(reachable)) for identity in reachable}
    changed = True
    while changed:
        changed = False
        for identity in ids[1:]:
            if identity not in reachable: continue
            parents = predecessors[identity] & reachable
            value = {identity} | set.intersection(*(result[parent] for parent in parents))
            if value != result[identity]: result[identity] = value; changed = True
    return result


@closed
def prepare(db, p, instance, release, business_id, *, source_contract,
            entity_ids, lock_sources=True):
    from .signal_graph_http_source_v177 import checked_contract
    from .entity_release_compat import require_entity
    from .instance_compiler import material_ids_for_flow, verify_material_snapshots
    from .layered_approval_store_v175 import (real_member, policy_for, current_policy,
                                            policy_value, ancestors)
    from .workflow_abilities import pin, eligible, ROLES
    contract = checked_contract(source_contract)
    scope = contract['scope']
    need(instance.workspace_id == p.workspace_id == scope['workspace_id']
        and instance.status == 'partially_active' and instance.active_release_id == release.id
        and release.instance_id == instance.id == scope['instance_id']
        and release.id == scope['release_id'] and release.package_id == scope['package_id'])
    package = published(db, p, release.package_id, 'domain_package')
    need(release.workspace_id == p.workspace_id and release.status == 'prepared'
         and digest(release.data['snapshot']) == release.configuration_hash
         and package.namespace == instance.namespace
         and package.data['specification']['configuration_hash'] == release.configuration_hash
         and package.content_hash == scope['package_hash']
         and contract['configuration_hash'] == release.configuration_hash,
         '原发布快照或业务包摘要无法核对，不能从被改写的流程外观取得资格')
    business = next((row for row in release.data['snapshot']['businesses'] if row['id'] == business_id), None)
    need(business and business.get('available') and business_id in instance.data.get('enabled_business_ids', [])
        and business.get('execution_mode') in MODES and business['flow'].get('steps'),
        '请选择真正已启用的人工流程或输入适配的本地注册能力流程；完整图不能用人工外观代替资格')
    steps = business['flow']['steps']
    need((manual(business) if business['execution_mode'] == 'confirmed_human_workflow'
          else any(step['agent'] != '人工确认' for step in steps)))
    need(type(entity_ids) is list and 1 <= len(entity_ids) <= 20
         and len(set(entity_ids)) == len(entity_ids), '请明确选择1至20个当前实例中的监测对象')
    entities = []
    for identity in sorted(entity_ids):
        entity = scoped(db, Entity, identity, p)
        need(entity.entity_type == scope['entity_type'])
        definition = require_entity(db, p, instance, release, entity, lock_sources=lock_sources)
        entities.append({'id': entity.id, 'definition_hash': digest(definition)})
    roles = {}
    for role in sorted({step['role'] for step in steps if step['agent'] == '人工确认'}):
        binding = release.data['snapshot']['roles'][role]
        real_member(db, p, binding['user_id'], lock_sources=lock_sources)
        roles[role] = copy.deepcopy(binding)
    policy = policy_for(db, p, instance.id, business_id)
    need(policy is not None, '请先为此业务设置有效的正式层级审批策略；自动路由只保存审批草稿')
    policy = current_policy(db, p, policy.id, lock_sources=lock_sources)
    version, spec = policy_value(db, p, policy)
    ancestors(db, p, spec['scope_id'], lock_sources=lock_sources)
    caps, sources = [], set()
    dominated = dominators(steps)
    schemas = {step['id']: step['execution']['output_schema'] for step in steps if step['agent'] != '人工确认'}
    if lock_sources:
        for identity in sorted({step['execution']['capability_id'] for step in steps if step['agent'] != '人工确认'}):
            # Source/extractor pins already use SHARE. Never upgrade to EX
            # here: two routes can use each other's extractor as a local tool.
            need(db.scalar(select(Capability).where(Capability.id == identity,
                Capability.workspace_id == p.workspace_id).with_for_update(read=True)
                .execution_options(populate_existing=True)) is not None)
    automation = release.data['snapshot']['automation']['policies'][business['name']]
    for step in steps:
        if step['agent'] == '人工确认':
            need(not step.get('planning_tools'), '此自动草稿入口暂不授权人工节点的规划工具')
            continue
        ex = step['execution']
        cap = published(db, p, ex['capability_id'])
        eligible(cap, ROLES[step['agent']])
        need(cap.data['execution'].get('adapter') == 'builtin'
            and cap.data['execution'].get('handler') in LOCAL_HANDLERS
            and cap.data['execution'].get('side_effect', False) is False
            and cap.data['data_policy'].get('cloud_allowed') is False
            and not cap.data['execution'].get('cloud')
            and pin(db, p, step, automation) == ex,
            '此入口仅接入已登记的本地只读数值计算；媒体处理、资料包、外部调用或设备动作需独立输入与授权')
        calculation_fields(step, cap, contract)
        prior = {identity: schema for identity, schema in schemas.items()
                 if identity != step['id'] and identity in dominated.get(step['id'], set())}
        _step_inputs(step, cap, contract, prior)
        sources.update(binding['source'] for binding in ex.get('bindings', {}).values())
        caps.append({'step_id': step['id'], 'capability_id': cap.id,
            'capability_hash': cap.content_hash, 'release_version': cap.release_version,
            'input_schema_hash': digest(cap.data['input_schema']),
            'output_schema_hash': digest(cap.data['output_schema']), 'execution_hash': digest(ex)})
    mids = (material_ids_for_flow(business['flow'])
        | set(release.data['snapshot'].get('relation_material_ids', []))
        | set(release.data['snapshot'].get('object_material_ids', [])))
    materials = [row for row in release.data['snapshot']['materials'] if row['id'] in mids]
    need({row['id'] for row in materials} == mids)
    if lock_sources:
        for identity in sorted(mids): scoped(db, SetupMaterial, identity, p, True)
    verify_material_snapshots(db, p, materials)
    value = {'schema': SCHEMA,
        'business': {key: business[key] for key in ('id', 'name', 'execution_mode')},
        'source_contract_hash': digest(contract), 'entities': entities, 'roles': roles,
        'capabilities': caps, 'materials_hash': digest(materials),
        'approval_policy': {'id': policy.id, 'version_id': version.id, 'hash': digest(version.data)},
        'input_provider': ('received_observation_records' if 'observation_records' in sources else
            'registered_archives' if sources & {'object_records', 'object_attributes'} else 'none'),
        'required_inputs': sorted(sources)}
    return checked_pin({**value, 'target_hash': digest(value)})


@closed
def preflight(db, p, task, release, business):
    """Validate actual received inputs without invoking a tool or queuing work."""
    from .signal_observation_provider_v247 import verify_task
    from .workflow_abilities import bind
    need(verify_task(db, p, task) is not None)
    for step in business['flow']['steps']:
        if step['agent'] == '人工确认': continue
        cap = published(db, p, step['execution']['capability_id'])
        ex = step['execution']
        deferred = {field for field, binding in ex.get('bindings', {}).items() if binding['source'] == 'previous_output'}
        partial = copy.deepcopy(step)
        partial['execution']['bindings'] = {key: value for key, value in ex.get('bindings', {}).items() if key not in deferred}
        inputs = bind(task, partial, db, p, 'signal-approval-preflight:' + task.id)
        schema = copy.deepcopy(cap.data['input_schema'])
        schema['required'] = [key for key in schema.get('required', []) if key not in deferred]
        validate(schema, inputs)
        if (cap.data['execution'].get('handler') == 'aggregate_values'
            and ex.get('bindings', {}).get('records', {}).get('source') == 'observation_records'):
            need('field' in inputs, '计算字段须由本次固定参数明确，不从未执行的前序结果猜测')
            need(all(inputs['field'] in row['attributes'] for row in inputs['records']),
                 '本次已收件观测未包含计算所需字段；不能采用对象档案或缺值计算替代')
    return True
