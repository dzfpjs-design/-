"""Finite graph starts from actual anomaly receipts, never new execution grants.

The receiver preserves an independent SQL source-family reservation. Creation
uses the existing Task/Goal/graph transaction. Historical checks do not renew
authority; every new dispatch/result separately checks the original grant.
"""
import copy
from datetime import timedelta
from functools import wraps
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from .contracts import GoalSpec, digest
from .models import Audit, Capability, DomainInstance, Entity, Event, InstanceRelease, Invocation, Job, Membership, Observation, PerceptionSignal, SetupMaterial, Task, User, now
from .security import Principal
from .services import audit, check_version, published, require, scoped, validate
from .signal_graph_routing_models_v134 import SignalGraphDelivery as Delivery
from . import signal_routing as single
from . import signal_graph_http_source_v177 as http_source
from .report_text_parse_scope import report_text_parse_scope

KEY = 'signal_graph_routes_v134'
ORIGIN = 'signal_graph_origin_v134'
STATE = 'signal-graph-routing-state-v134'
PREVIEW = 'signal-graph-routing-preview-v134'
DELIVERY = 'signal-graph-delivery-v134'
TTL = 86400
_BUSINESS_FLOW_BINDING = 'business-flow-execution-binding-v1'
_LEGACY_GRAPH_BINDINGS = frozenset({
    'report-full-semantic-graph-binding-v3',
    'report-full-semantic-graph-binding-v4',
    'report-object-graph-binding-v171',
})


def fail(message='完整流程路由的原授权或来源无法核对，原输入和历史保留'):
    raise HTTPException(409, message)


def need(value, message=None):
    if not value: fail(message or '完整流程路由的原授权或来源无法核对，原输入和历史保留')


def guarded(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            with report_text_parse_scope():
                return function(*args, **kwargs)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration): fail()
    return call


def sealed(value, key):
    return {**copy.deepcopy(value), key: digest(value)}


def clock(db): return db.scalar(select(func.clock_timestamp()))


def same(left, right):
    # Python's == identifies 1 with 1.0 and both zero signs. Grants and
    # original commands retain the canonical typed payload instead.
    return digest(left) == digest(right)


def fresh(db, model, identity, p, *, lock=False):
    query = select(model).where(model.id == identity, model.workspace_id == p.workspace_id)
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None)
    return row


def _actor(db, workspace_id, identity, *, lock=True):
    query = select(User.id, User.username, Membership.role).join(Membership, Membership.user_id == User.id).where(
        User.id == identity, User.active.is_(True), Membership.workspace_id == workspace_id)
    if lock: query = query.with_for_update(read=True, of=(User, Membership))
    row = db.execute(query).first()
    need(row is not None and row.role in {'admin', 'operator'}, '原负责人已停用或失去业务操作权限，原输入与回执保留')
    return Principal(identity, workspace_id, row.role, 'explicit-graph-routing'), row.username


def preview_body(value):
    from .signal_graph_routing_api_v134 import Preview as Command
    body = {key: copy.deepcopy(val) for key, val in value.items()
        if key not in {'request_id', 'preview_hash', 'confirm_graph_execution'}}
    body['schema'] = 'signal-graph-routing-preview-command-v134'
    return Command.model_validate(body).model_dump(mode='json')


@guarded
def state(instance):
    value = copy.deepcopy(instance.data.get(KEY, {'schema': STATE, 'routes': [], 'requests': []}))
    need(type(value) is dict and set(value) == {'schema', 'routes', 'requests'} and value['schema'] == STATE
        and type(value['routes']) is list and len(value['routes']) <= 100
        and type(value['requests']) is list and len(value['requests']) <= 200)
    ids = set(); nonces = set()
    for entry in value['routes']:
        need(set(entry) == {'grant', 'paused_at', 'paused_by', 'pause_reason', 'pause_request_id', 'delivery_ids'})
        grant = entry['grant']
        need(grant['schema'] == 'signal-graph-routing-grant-v134'
            and grant['id'] not in ids and grant['instance_id'] == instance.id and grant['workspace_id'] == instance.workspace_id
            and grant['grant_hash'] == digest({key: val for key, val in grant.items() if key != 'grant_hash'})
            and single.stamp(grant['expires_at']) - single.stamp(grant['enabled_at']) == timedelta(seconds=TTL)
            and grant['preview']['preview_hash'] == digest({key: val for key, val in grant['preview'].items() if key != 'preview_hash'})
            and same(grant['preview']['command'], preview_body(grant['command']))
            and grant['command']['preview_hash'] == grant['preview']['preview_hash']
            and same(grant['binding'], grant['preview']['binding'])
            and type(entry['delivery_ids']) is list and len(entry['delivery_ids']) == len(set(entry['delivery_ids']))
            and len(entry['delivery_ids']) <= grant['command']['max_tasks'])
        ids.add(grant['id'])
    for item in value['requests']:
        receipt = item['receipt']
        need(set(item) == {'body', 'receipt'} and receipt['route_id'] in ids and receipt['request_id'] not in nonces
            and receipt['schema'] == 'signal-graph-routing-receipt-v134'
            and receipt['receipt_hash'] == digest({key: val for key, val in receipt.items() if key != 'receipt_hash'})
            and receipt['workspace_id'] == instance.workspace_id and receipt['instance_id'] == instance.id
            and receipt['accepted_version'] <= instance.version)
        nonces.add(receipt['request_id'])
    return value


def route_status(entry, at=None, *, quota=True):
    if entry['paused_at'] is not None: return 'paused'
    if (at or now()) >= single.stamp(entry['grant']['expires_at']): return 'expired'
    if quota and len(entry['delivery_ids']) >= entry['grant']['command']['max_tasks']: return 'exhausted'
    return 'active'


def active_routes(instance):
    return [entry for entry in state(instance)['routes'] if route_status(entry) == 'active']


def assert_no_active(instance):
    need(not active_routes(instance), '请先明确暂停完整流程路由，再启用其它自动交接')


def has_source_family(db, workspace_id, instance_id, source_identity_hash):
    from .signal_approval_models_v247 import SignalApprovalOccurrenceV247
    if db.scalar(select(SignalApprovalOccurrenceV247.id).where(SignalApprovalOccurrenceV247.workspace_id==workspace_id,
        SignalApprovalOccurrenceV247.instance_id==instance_id,SignalApprovalOccurrenceV247.source_identity_hash==source_identity_hash).limit(1)):
        return True
    return db.scalar(select(Delivery.id).where(Delivery.workspace_id == workspace_id,
        Delivery.instance_id == instance_id, Delivery.source_identity_hash == source_identity_hash).limit(1)) is not None


def _save(instance, value):
    from .typed_payload_codec_v131 import canonical_json
    need(len(canonical_json(value).encode('utf-8')) <= 8 * 1024 * 1024, '授权与恢复记录已达容量上限，原记录保留')
    instance.data = {**instance.data, KEY: value}; instance.version += 1


def _hash(p, instance_id, action, route_id, body):
    return digest({'schema': 'signal-graph-routing-request-v134', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'instance_id': instance_id, 'action': action, 'route_id': route_id, 'body': body})


@guarded
def _state_authority(db, p, instance):
    value = state(instance)
    records = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == instance.id, Audit.action.in_(['signal_graph_routing.enable', 'signal_graph_routing.pause'])).limit(201)))
    need(len(records) == len(value['requests']))
    by_nonce = {item['receipt']['request_id']: item for item in value['requests']}
    seen = set()
    for record in records:
        data = record.data; nonce = data['request_id']; need(nonce in by_nonce and nonce not in seen)
        item = by_nonce[nonce]; receipt = item['receipt']; seen.add(nonce)
        need(record.action == 'signal_graph_routing.' + receipt['action'] and record.actor_id == receipt['actor_id']
            and same(data, {key: receipt[key] for key in ('route_id', 'request_id', 'command_hash', 'grant_hash')}))
        author = Principal(receipt['actor_id'], p.workspace_id, 'viewer', '')
        need(receipt['command_hash'] == _hash(author, instance.id, receipt['action'],
            None if receipt['action'] == 'enable' else receipt['route_id'], item['body']))
    return value


@guarded
def _grant(db, p, instance, route_id):
    value = _state_authority(db, p, instance)
    entry = next(entry for entry in value['routes'] if entry['grant']['id'] == route_id)
    grant = entry['grant']; signature = _hash(Principal(grant['actor_id'], p.workspace_id, 'viewer', ''),
        instance.id, 'enable', None, grant['command'])
    entries = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == instance.id, Audit.action == 'signal_graph_routing.enable',
        Audit.data['route_id'].as_string() == route_id).limit(2)))
    need(len(entries) == 1 and entries[0].actor_id == grant['actor_id'] and same(entries[0].data, {
        'route_id': route_id, 'request_id': grant['command']['request_id'], 'command_hash': signature, 'grant_hash': grant['grant_hash']}))
    enables = [item for item in value['requests'] if item['receipt']['route_id'] == route_id and item['receipt']['action'] == 'enable']
    pauses = [item for item in value['requests'] if item['receipt']['route_id'] == route_id and item['receipt']['action'] == 'pause']
    need(len(enables) == 1 and same(enables[0]['body'], grant['command'])
        and enables[0]['receipt']['grant_hash'] == grant['grant_hash']
        and enables[0]['receipt']['accepted_at'] == grant['enabled_at'] and len(pauses) <= 1)
    if pauses:
        pause = pauses[0]; receipt = pause['receipt']
        need(entry['paused_at'] == receipt['accepted_at'] and entry['paused_by'] == receipt['actor_id']
            and entry['pause_reason'] == pause['body']['reason'] and entry['pause_request_id'] == receipt['request_id']
            and receipt['grant_hash'] == grant['grant_hash'])
    else:
        need(all(entry[key] is None for key in ('paused_at', 'paused_by', 'pause_reason', 'pause_request_id')))
    identifiers = list(db.scalars(select(Delivery.id).where(Delivery.workspace_id == p.workspace_id,
        Delivery.instance_id == instance.id, Delivery.route_id == route_id)))
    reservations = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'signal_graph_routing.reserved', Audit.data['route_id'].as_string() == route_id)
        .limit(grant['command']['max_tasks'] + 1)))
    need(set(identifiers) == set(entry['delivery_ids']) and len(identifiers) == len(entry['delivery_ids'])
        and len(identifiers) <= grant['command']['max_tasks']
        and len(reservations) == len(identifiers) and {record.object_id for record in reservations} == set(identifiers)
        and all(record.actor_id == grant['actor_id'] for record in reservations))
    return entry


def _entities(db, p, instance, release, ids, *, lock=True):
    from .entity_release_compat import require_entity
    rows = []
    for identity in sorted(ids):
        obj = fresh(db, Entity, identity, p, lock=lock)
        definition = require_entity(db, p, instance, release, obj, lock_sources=lock)
        kind = next(item for item in release.data['snapshot']['object_types'] if item['key'] == obj.entity_type)
        rows.append({'id': obj.id, 'name': next((val for val in obj.data.values() if type(val) is str and val.strip()), obj.external_id),
            'version': obj.version, 'type': obj.entity_type, 'definition_hash': digest(definition),
            'attributes_hash': digest(obj.data), 'fields': copy.deepcopy(kind['fields'])})
    return rows


def _binding_family(binding):
    need(type(binding) is dict and type(binding.get('schema')) is str
        and binding['schema'] in _LEGACY_GRAPH_BINDINGS | {_BUSINESS_FLOW_BINDING},
        '业务流程的来源绑定版本未知，原配置保留，未建立自动交接授权')
    return binding['schema']


def _graph_summary(graph, binding, business, abilities):
    if _binding_family(binding) == _BUSINESS_FLOW_BINDING:
        # The graph uses generated runtime IDs, but its source_step_id remains
        # the exact selected flow's original node ID. Display the saved source
        # names instead of exposing a runtime hash or guessing an old manifest.
        source_nodes = binding['flow']['source_flow']['nodes']
        need(type(source_nodes) is list
            and all(type(row) is dict and type(row.get('node_id')) is str
                and type(row.get('name')) is str and bool(row['name']) for row in source_nodes)
            and len({row['node_id'] for row in source_nodes}) == len(source_nodes),
            '当前流程的原步骤名称或对应无法核对')
        steps = {row['node_id']: row for row in source_nodes}
        need(all(node['kind'] not in {'human', 'ability'} or node.get('source_step_id') in steps
            for node in graph['nodes']), '执行步骤没有对应的原流程名称，未以内部编号替代')
    else:
        steps = {row['id']: row for row in business['flow']['steps']}
    names = {row['node_id']: row['name'] for row in abilities}
    kinds = {'human': '人工待办', 'ability': '已登记本机能力', 'decision': '条件判断',
        'fork': '并行启动', 'join': '等待汇合', 'wait': '等待处理结果', 'end': '本次流程结束'}
    return {'graph_hash': digest(graph), 'binding_hash': binding['binding_hash'],
        'nodes': [{'id': node['id'], 'kind': node['kind'], 'actor_id': node.get('actor_id'),
            'name': steps.get(node.get('source_step_id'), {}).get('name') or names.get(node['id']) or kinds[node['kind']]}
            for node in graph['nodes']],
        'abilities': [{key: copy.deepcopy(row[key]) for key in
            ('node_id', 'capability_id', 'release_version', 'adapter', 'actor_id', 'name')} for row in abilities]}


def _prepare(db, p, instance, body, *, lock_selection=True):
    from .instance_api import active
    from .workflow_graph import business_binding
    from .workflow_abilities import ADAPTERS, eligible
    from .perception_signals import rule_contract
    from .instance_compiler import material_ids_for_flow, verify_material_snapshots
    _, release = active(db, p, instance.id)
    need(release.id == body['release_id'])
    business = next(item for item in release.data['snapshot']['businesses'] if item['id'] == body['business_id'])
    need(business['available'] and business['id'] in instance.data.get('enabled_business_ids', [])
        and business['execution_mode'] == 'confirmed_graph_workflow', '请选择当前已启用的完整业务流程')
    binding = business_binding(release, business); family = _binding_family(binding); graph = binding['graph']
    policy = release.data['snapshot']['automation']['policies'][business['name']]
    need('异常事件' in policy.get('triggers', []), '请先在第4步明确确认此业务由异常事件启动，再生成并启用对应版本')
    actors = []
    for identity in sorted({node['actor_id'] for node in graph['nodes'] if node['kind'] in {'human', 'ability'}}):
        live, username = _actor(db, p.workspace_id, identity, lock=lock_selection)
        actors.append({'id': identity, 'role': live.role, 'username': username})
    abilities = []
    for node in graph['nodes']:
        if node['kind'] != 'ability': continue
        spec = node['execution']; fresh(db, Capability, spec['capability_id'], p, lock=lock_selection)
        cap = published(db, p, spec['capability_id']); eligible(cap, spec['agent_role'])
        need(spec['adapter'] in ADAPTERS - {'http', 'mcp', 'cloud_extractor', 'business_reasoning'}
            and cap.data['execution']['adapter'] == spec['adapter']
            and cap.data['data_policy'].get('cloud_allowed') is False
            and not cap.data['execution'].get('side_effect') and cap.content_hash == spec['capability_hash']
            and cap.release_version == spec['release_version'], '此授权仅允许完整图中已登记的本机只读能力；外发和设备动作需独立授权')
        abilities.append({'node_id': node['id'], 'capability_id': cap.id, 'name': cap.data['description'],
            'content_hash': cap.content_hash, 'release_version': cap.release_version,
            'adapter': spec['adapter'], 'actor_id': node['actor_id'], 'agent_role': spec['agent_role'],
            'input_schema': copy.deepcopy(cap.data['input_schema']), 'output_schema': copy.deepcopy(cap.data['output_schema'])})
    for identity in sorted({body['source_id'], body['extractor_id']}): fresh(db, Capability, identity, p, lock=lock_selection)
    source = published(db, p, body['source_id'], 'source'); extractor = published(db, p, body['extractor_id'])
    need('perception' in extractor.data['allowed_agent_roles'])
    source_contract = None
    if http_source.configured(source):
        entities = _entities(db, p, instance, release, body['entity_ids'], lock=lock_selection)
        source_contract = http_source.prepare(db, p, instance, release, source, extractor, body['entity_ids'])
    else:
        need(source.data['execution']['adapter'] in {'upload', 'builtin'}
            and (source.data['execution']['adapter'] != 'builtin' or source.data['execution'].get('handler') == 'numeric_rules')
            and extractor.data['execution']['adapter'] == 'builtin' and extractor.data['execution'].get('handler') == 'numeric_rules'
            and all(not cap.data['execution'].get('side_effect') and cap.data['data_policy'].get('cloud_allowed') is False
                for cap in (source, extractor)), '请选择已登记的本机只读来源和明确数值规则')
        entities = _entities(db, p, instance, release, body['entity_ids'], lock=lock_selection)
        for obj in entities:
            if lock_selection: rule_contract(db, p, instance, release, scoped(db, Entity, obj['id'], p), source, extractor)
    snapshot = release.data['snapshot']
    material_ids = material_ids_for_flow(business['flow']) | set(snapshot.get('relation_material_ids', [])) | set(snapshot.get('object_material_ids', []))
    if family == _BUSINESS_FLOW_BINDING:
        from .business_flow_execution_source import current_materials
        # Read the actual frozen-source contract without taking source locks
        # out of order; the existing sorted material locks and snapshot check
        # below remain the final gate for both source families.
        original_materials = current_materials(db, p, binding['source'], lock=False)
        material_ids.update(row['id'] for row in original_materials)
    else:
        selection = binding['source_proof']['selection']; anchor = selection['anchor']
        material_ids.update((anchor['source_ref']['id'], anchor['copy_material_id']))
    materials = copy.deepcopy([row for row in snapshot['materials'] if row['id'] in material_ids])
    need({row['id'] for row in materials} == material_ids, '本业务的原资料和完整图来源材料无法核对')
    lock_ids = set(material_ids)
    for material in materials:
        for pin in material.get('report_sources', []):
            from .report_configuration_handoff import owned_record
            proof = owned_record(db, p, pin['source_review_id'], kind='source_review').data['record']
            lock_ids.update(row['id'] for row in proof['materials'])
    for identity in sorted(lock_ids): fresh(db, SetupMaterial, identity, p, lock=lock_selection)
    verify_material_snapshots(db, p, materials)
    from .graph_task_inputs_v120 import describe
    metadata = describe(db, p, instance.id, release.id, business['id'])
    result = {'schema': 'signal-graph-routing-binding-v134', 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'business': {'id': business['id'], 'name': business['name']},
        'graph_hash': digest(graph), 'binding_hash': binding['binding_hash'], 'automation_hash': digest(policy),
        'graph': copy.deepcopy(graph), 'graph_summary': _graph_summary(graph, binding, business, abilities),
        'source': single.cap_ref(source), 'extractor': single.extractor_ref(extractor),
        'entities': entities, 'actors': actors, 'abilities': abilities, 'materials': materials,
        'materials_hash': digest(materials), 'input_metadata': metadata}
    if source_contract is not None:
        result[http_source.CONTRACT_KEY] = source_contract
    return result


def _expand(body, binding, deadline=None):
    from .workflow_graph import SYSTEM_INPUTS
    metadata = binding['input_metadata']; user = metadata['user_input_schema']
    props = user.get('properties', {}); selected = body['entity_input_fields']
    need(not set(body['graph_inputs']) & SYSTEM_INPUTS and set(selected) <= set(props)
        and all(props[key].get('type') == 'string' or type(props[key].get('type')) is list
            and set(props[key]['type']) == {'string', 'null'} for key in selected),
        '请明确选择字符串类型的对象关联字段；服务器字段不能被替换')
    for field in metadata['entity_fields']:
        need(field['field'] in selected, '所有等待消息的输入关联对象都须明确选择为当前触发对象')
    caps = {row['capability_id']: row for row in binding['abilities']}
    expanded = []
    for entity in binding['entities']:
        values = {**copy.deepcopy(body['graph_inputs']), **{field: entity['id'] for field in selected}}
        validate(user, values)
        goal = copy.deepcopy(body['goal_spec']); goal['entity_ids'] = [entity['id']]; goal['deadline'] = deadline
        goal = GoalSpec.model_validate(goal).model_dump()
        allowed = {field['key'] for field in entity['fields']}
        need(set(goal['required_fields']) <= allowed and goal['stop_on_conflict'])
        for item in goal['criteria']:
            need(item['kind'] in {'evidence_sufficient', 'field_value', 'human_confirmation', 'capability_success', 'tool_value'})
            if item['kind'] == 'field_value': need(item['field'] in goal['required_fields'])
            if item['kind'] in {'capability_success', 'tool_value'}:
                need(item['capability_id'] in caps, '完成标准只能引用本图实际登记的能力')
                if item['kind'] == 'tool_value': need(item['field'] in caps[item['capability_id']]['output_schema'].get('properties', {}))
        expanded.append({'entity_id': entity['id'], 'graph_inputs': values, 'goal_spec': goal})
    return expanded


@guarded
def preview(db, p, instance, body):
    require(p, 'admin', 'operator'); single.actor(db, p.workspace_id, p.user_id)
    check_version(instance, body['expected_version']); body = preview_body(body)
    binding = _prepare(db, p, instance, body)
    return sealed({'schema': PREVIEW, 'instance_id': instance.id, 'instance_version': instance.version,
        'command': body, 'binding': binding, 'expanded': _expand(body, binding),
        'deadline_policy': 'original_grant_expiry'}, 'preview_hash')


def _route_view(entry):
    grant = entry['grant']
    return {'id': grant['id'], 'actor_id': grant['actor_id'], 'release_id': grant['release_id'],
        'enabled_at': grant['enabled_at'], 'expires_at': grant['expires_at'], 'status': route_status(entry),
        'paused_at': entry['paused_at'], 'max_tasks': grant['command']['max_tasks'], 'used_tasks': len(entry['delivery_ids']),
        'business': copy.deepcopy(grant['binding']['business']), 'source': copy.deepcopy(grant['binding']['source']),
        'extractor': copy.deepcopy(grant['binding']['extractor']), 'entities': copy.deepcopy(grant['binding']['entities'])}


def reply(db, p, instance, receipt=None):
    value = _state_authority(db, p, instance)
    for entry in value['routes']: _grant(db, p, instance, entry['grant']['id'])
    return {'schema': 'signal-graph-routing-response-v134', 'instance_id': instance.id, 'instance_version': instance.version,
        'routes': [_route_view(entry) for entry in value['routes']], 'receipt': copy.deepcopy(receipt),
        'current_matches': bool(receipt and receipt['accepted_version'] == instance.version) if receipt else None,
        'entity_state_updated': False, 'source_time_verified': False, 'device_actions': False}


@guarded
def saved_request(db, p, instance, request_id):
    item = next((item for item in _state_authority(db, p, instance)['requests'] if item['receipt']['request_id'] == request_id), None)
    if item is None:
        return {'schema': 'signal-graph-routing-recovery-v134', 'found': False, 'result': None}
    need(item['receipt']['actor_id'] == p.user_id)
    receipt = item['receipt']; signature = _hash(p, instance.id, receipt['action'],
        None if receipt['action'] == 'enable' else receipt['route_id'], item['body'])
    need(signature == receipt['command_hash'])
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == instance.id, Audit.action == 'signal_graph_routing.' + receipt['action'],
        Audit.data['request_id'].as_string() == request_id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == p.user_id and rows[0].data['command_hash'] == signature)
    return {'schema': 'signal-graph-routing-recovery-v134', 'found': True, 'result': reply(db, p, instance, receipt)}


@guarded
def command(db, p, identity, action, body, route_id=None):
    instance = scoped(db, DomainInstance, identity, p, True)
    signature = _hash(p, identity, action, route_id, body); value = _state_authority(db, p, instance)
    prior = next((item for item in value['requests'] if item['receipt']['request_id'] == body['request_id']), None)
    if prior is not None:
        need(prior['receipt']['command_hash'] == signature)
        return saved_request(db, p, instance, body['request_id'])['result']
    require(p, 'admin', 'operator'); live = single.actor(db, p.workspace_id, p.user_id)
    check_version(instance, body['expected_version']); need(len(value['requests']) < 200)
    if action == 'enable':
        from .signal_approval_routing_v247 import assert_no_active as approval_no_active
        approval_no_active(db,p,instance,body['source_id'],body['extractor_id'],body['entity_ids'])
        from .signal_dual_handoffs import active_routes as dual_active
        need(not active_routes(instance) and not dual_active(instance)
            and not any(single.route_status(entry) == 'active' for entry in single.state(instance)['routes']),
            '请先暂停当前自动交接，再明确启用完整流程路由')
        need(len(value['routes']) < 100)
        proposed = preview(db, p, instance, body)
        need(body['confirm_graph_execution'] is True and proposed['preview_hash'] == body['preview_hash'], '原预览已变化；请保留原请求并重新核对')
        at = clock(db); route_id = str(uuid4())
        grant = sealed({'schema': 'signal-graph-routing-grant-v134', 'id': route_id,
            'workspace_id': p.workspace_id, 'instance_id': identity, 'actor_id': p.user_id, 'actor_role': live.role,
            'enabled_at': at.isoformat(), 'expires_at': (at + timedelta(seconds=TTL)).isoformat(),
            'command': copy.deepcopy(body), 'preview': proposed, 'binding': proposed['binding'],
            **{key: body[key] for key in ('release_id', 'source_id', 'extractor_id', 'entity_ids', 'business_id')}}, 'grant_hash')
        value['routes'].append({'grant': grant, 'paused_at': None, 'paused_by': None,
            'pause_reason': None, 'pause_request_id': None, 'delivery_ids': []})
    elif action == 'pause':
        entry = _grant(db, p, instance, route_id); need(entry['paused_at'] is None, '此授权已暂停，原请求可继续查询')
        at = clock(db)
        # Use the corresponding value copy, not the detached cold projection.
        entry = next(item for item in value['routes'] if item['grant']['id'] == route_id)
        entry.update(paused_at=at.isoformat(), paused_by=p.user_id, pause_reason=body['reason'], pause_request_id=body['request_id'])
        grant = entry['grant']
    else: fail()
    receipt = sealed({'schema': 'signal-graph-routing-receipt-v134', 'request_id': body['request_id'],
        'action': action, 'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'instance_id': identity,
        'route_id': route_id, 'command_hash': signature, 'grant_hash': grant['grant_hash'],
        'accepted_version': instance.version + 1, 'accepted_at': at.isoformat()}, 'receipt_hash')
    value['requests'].append({'body': copy.deepcopy(body), 'receipt': receipt}); _save(instance, value)
    audit(db, p, 'signal_graph_routing.' + action, instance.id, {'route_id': route_id,
        'request_id': body['request_id'], 'command_hash': signature, 'grant_hash': grant['grant_hash']})
    db.flush()
    return reply(db, p, instance, receipt)


@guarded
@report_text_parse_scope()
def options(db, p, instance):
    from .instance_api import active
    from .graph_task_inputs_v120 import describe
    from .workflow_graph import business_binding
    _, release = active(db, p, instance.id)
    # Reuse the actual registered local source/rule and compatible-object list;
    # its old human-only business filter is deliberately not reused.
    original = single.options(db, p, instance)
    sources = copy.deepcopy(original['sources']); extractors = copy.deepcopy(original['extractors'])
    http_options = http_source.options(db, p, instance, release)
    for option in http_options:
        if not any(row['id'] == option['source']['id'] for row in sources):
            sources.append(option['source'])
        if not any(row['id'] == option['extractor']['id'] for row in extractors):
            extractors.append(option['extractor'])
    sources.sort(key=lambda row: row['id']); extractors.sort(key=lambda row: row['id'])
    _state_authority(db, p, instance)
    entities = _entities(db, p, instance, release, [row['id'] for row in original['entities']], lock=False)
    fields = {}
    for entity in entities:
        for field in entity['fields']:
            key = field['key']
            if key not in fields:
                fields[key] = {name: copy.deepcopy(field.get(name)) for name in ('key', 'label', 'kind', 'unit')}
                fields[key]['entity_ids'] = []
            fields[key]['entity_ids'].append(entity['id'])
    businesses = []
    for business in release.data['snapshot']['businesses']:
        if business.get('execution_mode') != 'confirmed_graph_workflow': continue
        metadata = None; reason = None; summary = None
        try:
            need(business['available'] and business['id'] in instance.data.get('enabled_business_ids', []))
            binding = business_binding(release, business); graph = binding['graph']
            policy = release.data['snapshot']['automation']['policies'][business['name']]
            need('异常事件' in policy.get('triggers', []), '请先确认异常事件启动范围并生成启用对应版本')
            for node in graph['nodes']:
                if node['kind'] in {'human', 'ability'}: _actor(db, p.workspace_id, node['actor_id'], lock=False)
                if node['kind'] == 'ability':
                    from .workflow_abilities import ADAPTERS, eligible
                    fresh(db, Capability, node['execution']['capability_id'], p)
                    cap = published(db, p, node['execution']['capability_id'])
                    eligible(cap, node['execution']['agent_role'])
                    need(cap.data['execution']['adapter'] in ADAPTERS - {'http', 'mcp', 'cloud_extractor', 'business_reasoning'}
                        and cap.data['execution']['adapter'] == node['execution']['adapter']
                        and cap.data['data_policy'].get('cloud_allowed') is False
                        and not cap.data['execution'].get('side_effect')
                        and cap.content_hash == node['execution']['capability_hash']
                        and cap.release_version == node['execution']['release_version'], '此业务含外发、设备动作或能力版本变化，不在本次自动启动范围')
            metadata = describe(db, p, instance.id, release.id, business['id'])
            abilities = [{'node_id': node['id'], 'capability_id': node['execution']['capability_id'],
                'release_version': node['execution']['release_version'], 'adapter': node['execution']['adapter'],
                'actor_id': node['actor_id'], 'name': published(db, p, node['execution']['capability_id']).data['description']}
                for node in graph['nodes'] if node['kind'] == 'ability']
            summary = _graph_summary(graph, binding, business, abilities)
        except HTTPException as error:
            reason = error.detail if type(error.detail) is str else '当前业务或授权范围需要重新核对'
        props = (metadata or {}).get('user_input_schema', {}).get('properties', {})
        businesses.append({'id': business['id'], 'name': business['name'], 'available': reason is None,
            'reason': reason, 'input_metadata': metadata, 'graph_summary': summary,
            'goal_fields': list(fields.values()), 'entity_input_fields': [key for key, prop in props.items()
                if prop.get('type') == 'string' or type(prop.get('type')) is list and set(prop['type']) == {'string', 'null'}]})
    from .signal_dual_handoffs import active_routes as dual_active
    allowed = bool(p.role in {'admin', 'operator'} and sources and extractors and entities
        and any(row['available'] for row in businesses) and not active_routes(instance) and not dual_active(instance)
        and not any(single.route_status(entry) == 'active' for entry in single.state(instance)['routes']))
    result = {'schema': 'signal-graph-routing-options-v134', 'instance_id': instance.id, 'instance_version': instance.version,
        'release_id': release.id, 'ttl_seconds': TTL, 'max_tasks_limit': 100, 'max_entities': 30,
        'sources': sources, 'extractors': extractors, 'entities': entities,
        'businesses': businesses, 'can_enable': allowed,
        'blocking_reason': None if allowed else '请核对当前权限、来源对象、完整流程范围，并先暂停其它自动交接'}
    if http_options:
        result['http_mapped_source_ids_v177'] = sorted({option['source']['id'] for option in http_options})
    return sealed(result, 'options_hash')


def _processing(db, p, observation):
    call = scoped(db, Invocation, observation.data['invocation_id'], p)
    need(call.status == 'succeeded' and call.capability_id == observation.data['extractor_id']
        and same(call.data.get('output'), observation.data['extracted']))
    job = scoped(db, Job, call.data['job_id'], p)
    need(job.data.get('observation_id') == observation.id and job.data.get('extractor_id') == call.capability_id)
    return {'invocation_id': call.id, 'capability_id': call.capability_id, 'key': call.idempotency_key,
        'input_hash': call.data['input_hash'], 'output_hash': digest(call.data['output']),
        'capability_hash': call.data['capability_hash'], 'role': call.data['role'],
        'job_id': job.id, 'processing_actor_id': job.data['actor_id'], 'lease_token': call.data['lease_token']}


def _prior_family(db, p, signal, observation):
    from .signal_dual_models import SignalDualHandoff
    from .signal_dual_handoffs import checked_parent
    identity = single.existing_task(db, p, signal, observation)
    if identity is not None: return {'kind': 'task', 'id': identity}
    parent = db.scalar(select(SignalDualHandoff).where(SignalDualHandoff.workspace_id == p.workspace_id,
        SignalDualHandoff.instance_id == signal.instance_id,
        SignalDualHandoff.source_identity_hash == single.source_identity(observation))
        .order_by(SignalDualHandoff.created_at, SignalDualHandoff.id).limit(1))
    if parent is not None:
        checked_parent(parent)
        return {'kind': 'dual', 'id': parent.id}
    # A saved original single route intent consumes this source family even
    # when it has not created a Task yet. Mutual exclusion alone cannot turn
    # that old reserved/blocked notification into new graph work.
    family = select(Observation.id).where(Observation.workspace_id == p.workspace_id,
        Observation.source_id == observation.source_id, Observation.source_record_id == observation.source_record_id,
        Observation.entity_id == observation.entity_id)
    signals = list(db.scalars(select(PerceptionSignal).where(PerceptionSignal.workspace_id == p.workspace_id,
        PerceptionSignal.instance_id == signal.instance_id,
        PerceptionSignal.data['basis']['observation_id'].as_string().in_(family),
        PerceptionSignal.data['route_intent'].is_not(None)).order_by(PerceptionSignal.created_at, PerceptionSignal.id)))
    for previous in signals:
        if 'route_intent' in previous.data:
            single.checked_intent(previous.data['route_intent'])
            return {'kind': 'single', 'id': previous.id}
    return None


def _check_prior(db, p, signal, observation, value):
    if value is None: return
    need(type(value) is dict and set(value) == {'kind', 'id'})
    if value['kind'] == 'task':
        task = scoped(db, Task, value['id'], p)
        need(task.data['instance_id'] == signal.instance_id)
        source = scoped(db, Observation, task.data['perception_signal']['basis']['observation_id'], p)
        need(source.source_id == observation.source_id and source.source_record_id == observation.source_record_id)
    elif value['kind'] == 'dual':
        from .signal_dual_models import SignalDualHandoff
        from .signal_dual_handoffs import checked_parent
        parent = scoped(db, SignalDualHandoff, value['id'], p); checked_parent(parent)
        need(parent.instance_id == signal.instance_id and parent.source_identity_hash == single.source_identity(observation))
    elif value['kind'] == 'single':
        previous = scoped(db, PerceptionSignal, value['id'], p)
        intent = single.checked_intent(previous.data['route_intent'])
        need(previous.instance_id == signal.instance_id and intent['source_identity_hash'] == single.source_identity(observation))
    else: fail()


def _delivery_audit(db, p, row):
    audit(db, p, 'signal_graph_routing.delivery', row.id, {'status': row.status,
        'receipt_hash': row.data['receipt']['receipt_hash']})


@guarded
def _checked(db, p, row):
    need(row.workspace_id == p.workspace_id and type(row.data) is dict
        and set(row.data) == {'schema', 'immutable', 'immutable_hash', 'receipt'} and row.data['schema'] == DELIVERY)
    imm = row.data['immutable']
    need(type(imm) is dict and set(imm) == {'schema', 'delivery_id', 'workspace_id', 'instance_id', 'release_id',
        'route_id', 'grant_hash', 'signal_id', 'event_id', 'observation_id', 'actor_id', 'source_identity_hash',
        'basis_hash', 'event_hash', 'input_hash', 'observation_hash', 'processing', 'body', 'creation_hash', 'source_snapshot', 'prior_handoff'}
        and row.data['immutable_hash'] == digest(imm)
        and imm['schema'] == 'signal-graph-routing-intent-v134'
        and imm['delivery_id'] == row.id and imm['workspace_id'] == row.workspace_id
        and all(imm[key] == getattr(row, key) for key in ('instance_id', 'release_id', 'signal_id', 'event_id',
            'observation_id', 'route_id', 'actor_id', 'source_identity_hash')))
    instance = scoped(db, DomainInstance, row.instance_id, p)
    entry = _grant(db, p, instance, row.route_id); grant = entry['grant']
    need(row.id in entry['delivery_ids'] and imm['grant_hash'] == grant['grant_hash'])
    signal = scoped(db, PerceptionSignal, row.signal_id, p)
    event = scoped(db, Event, row.event_id, p); observation = scoped(db, Observation, row.observation_id, p)
    from .perception_signals import machine_hash
    need(signal.instance_id == row.instance_id and signal.event_id == row.event_id
        and signal.data['basis_hash'] == imm['basis_hash'] == digest(signal.data['basis'])
        and same(signal.data['basis'], imm['source_snapshot']['basis'])
        and event.kind == 'perception.anomaly' and digest(event.data) == imm['event_hash']
        and event.data['observation_id'] == observation.id and signal.entity_id == observation.entity_id
        and single.source_identity(observation) == row.source_identity_hash
        and observation.data['input_hash'] == imm['input_hash']
        and machine_hash(db, p, observation) == imm['observation_hash']
        and same(_processing(db, p, observation), imm['processing']))
    if http_source.CONTRACT_KEY in grant['binding']:
        need(http_source.registration_matches(db, p, grant, event, observation,
            imm['source_snapshot']['basis'], row.created_at))
    _check_prior(db, p, signal, observation, imm['prior_handoff'])
    entries = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'signal_graph_routing.reserved', Audit.object_id == row.id).limit(2)))
    need(len(entries) == 1 and entries[0].actor_id == row.actor_id and entries[0].data == {
        'intent_hash': row.data['immutable_hash'], 'route_id': row.route_id, 'signal_id': row.signal_id})
    from .workspace_api import NewTask, creation_body_hash
    body = NewTask.model_validate(imm['body'])
    expanded = next(value for value in _expand(preview_body(grant['command']), grant['binding'], grant['expires_at'])
        if value['entity_id'] == observation.entity_id)
    need(same(body.model_dump(mode='json'), imm['body']) and body.entity_ids == [observation.entity_id]
        and same(body.goal_spec.model_dump(), expanded['goal_spec'])
        and digest(body.graph_inputs) == digest(expanded['graph_inputs'])
        and body.signal_id == signal.id and body.signal_basis_hash == imm['basis_hash']
        and body.request_id == 'graph_route_' + digest(row.id)
        and creation_body_hash(body) == imm['creation_hash'])
    receipt = row.data['receipt']
    deliveries = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'signal_graph_routing.delivery', Audit.object_id == row.id).limit(2)))
    if row.status == 'reserved':
        need(row.task_id is None and receipt is None and not deliveries and imm['prior_handoff'] is None)
    else:
        need(row.status in {'created', 'blocked', 'skipped_existing_task'} and type(receipt) is dict
            and set(receipt) == {'schema', 'delivery_id', 'intent_hash', 'status', 'task_id', 'creation_hash',
                'message', 'source_time_verified', 'entity_state_updated', 'device_actions', 'receipt_hash'}
            and receipt['schema'] == 'signal-graph-routing-delivery-receipt-v134'
            and receipt['receipt_hash'] == digest({key: value for key, value in receipt.items() if key != 'receipt_hash'})
            and receipt['delivery_id'] == row.id and receipt['intent_hash'] == row.data['immutable_hash']
            and receipt['status'] == row.status and receipt['creation_hash'] == imm['creation_hash']
            and (row.status != 'created' or receipt['task_id'] == row.task_id and row.task_id is not None)
            and (row.status == 'created' or row.task_id is None)
            and receipt['source_time_verified'] is False and receipt['entity_state_updated'] is False and receipt['device_actions'] is False
            and len(deliveries) == 1 and deliveries[0].actor_id == row.actor_id and same(deliveries[0].data,
                {'status': row.status, 'receipt_hash': receipt['receipt_hash']}))
        if imm['prior_handoff'] is not None: need(row.status == 'skipped_existing_task')
    return imm, grant, body


def _summary(row):
    receipt = row.data.get('receipt')
    return {'schema': 'signal-graph-routing-public-v134', 'delivery_id': row.id, 'status': row.status,
        'task_id': row.task_id if row.status == 'created' else (receipt or {}).get('task_id'),
        'message': (receipt or {}).get('message') or '原异常已保留，等待按已确认范围创建业务流程',
        'terminal': row.status != 'reserved', 'automatic_retry': False}


def _publish(signal, row):
    value = _summary(row)
    signal.data = {**signal.data, 'signal_graph_route_v134': sealed(value, 'view_hash')}; signal.version += 1


@guarded
def freeze(db, instance, event, observation, signal, basis):
    entries = [entry for entry in _state_authority(db, Principal('', instance.workspace_id, 'viewer', ''), instance)['routes']
        if route_status(entry, clock(db)) == 'active']
    if not entries: return None
    need(len(entries) == 1)
    grant = entries[0]['grant']; p = Principal(grant['actor_id'], instance.workspace_id, 'viewer', 'graph-routing-source')
    _grant(db, p, instance, grant['id'])
    need(signal.workspace_id == instance.workspace_id and signal.instance_id == instance.id
        and signal.event_id == event.id and signal.entity_id == observation.entity_id)
    if basis['schema'] != 'reported-anomaly-v1':
        return None
    # The source family is part of the explicitly previewed, audited binding.
    # Unrelated HTTP/SIM observations must not damage a manual-only route.
    if http_source.CONTRACT_KEY in grant['binding']:
        if not http_source.registration_matches(db, p, grant, event, observation, basis, clock(db)):
            return None
    else:
        if (observation.source_id != grant['source_id']
                or observation.data.get('extractor_id') != grant['extractor_id']
                or observation.entity_id not in grant['entity_ids']):
            return None
        if not single.registration_matches(db, grant, event, observation, clock(db)):
            return None
    identity = single.source_identity(observation)
    previous = db.scalar(select(Delivery).where(Delivery.workspace_id == p.workspace_id,
        Delivery.instance_id == instance.id, Delivery.source_identity_hash == identity))
    if previous is not None:
        _checked(db, p, previous); _publish(signal, previous)
        return None
    prior = _prior_family(db, p, signal, observation)
    from .workspace_api import NewTask, creation_body_hash
    from .perception_signals import machine_hash
    identifier = str(uuid4())
    expanded = next(item for item in _expand(preview_body(grant['command']), grant['binding'], grant['expires_at'])
        if item['entity_id'] == signal.entity_id)
    body = NewTask(release_id=grant['release_id'], business_id=grant['business_id'], entity_ids=[signal.entity_id],
        title='核查登记异常并执行已确认流程：' + basis['object_name'][:200], request_id='graph_route_' + digest(identifier),
        signal_id=signal.id, signal_version=signal.version + 1, signal_basis_hash=signal.data['basis_hash'],
        graph_inputs=expanded['graph_inputs'], goal_spec=expanded['goal_spec'])
    imm = {'schema': 'signal-graph-routing-intent-v134', 'delivery_id': identifier, 'workspace_id': p.workspace_id,
        'instance_id': instance.id, 'release_id': grant['release_id'], 'route_id': grant['id'], 'grant_hash': grant['grant_hash'],
        'signal_id': signal.id, 'event_id': event.id, 'observation_id': observation.id, 'actor_id': grant['actor_id'],
        'source_identity_hash': identity, 'basis_hash': signal.data['basis_hash'], 'event_hash': digest(event.data),
        'input_hash': observation.data['input_hash'], 'observation_hash': machine_hash(db, p, observation),
        'processing': _processing(db, p, observation), 'body': body.model_dump(mode='json'), 'creation_hash': creation_body_hash(body),
        'prior_handoff': prior,
        'source_snapshot': {'signal_id': signal.id, 'basis_hash': signal.data['basis_hash'],
            'basis': copy.deepcopy(basis), 'note': signal.data.get('note', '')}}
    row = Delivery(id=identifier, workspace_id=p.workspace_id, instance_id=instance.id, release_id=grant['release_id'],
        signal_id=signal.id, event_id=event.id, observation_id=observation.id, actor_id=grant['actor_id'],
        route_id=grant['id'], source_identity_hash=identity, status='reserved',
        data={'schema': DELIVERY, 'immutable': imm, 'immutable_hash': digest(imm), 'receipt': None})
    db.add(row); db.flush()
    value = state(instance); entry = next(entry for entry in value['routes'] if entry['grant']['id'] == grant['id'])
    entry['delivery_ids'].append(row.id); _save(instance, value)
    audit(db, p, 'signal_graph_routing.reserved', row.id, {'intent_hash': row.data['immutable_hash'],
        'route_id': row.route_id, 'signal_id': row.signal_id})
    if prior is not None:
        row.status = 'skipped_existing_task'
        row.data = {**row.data, 'receipt': _receipt(row, row.status, prior['id'] if prior['kind'] == 'task' else None)}
        _delivery_audit(db, p, row)
    _publish(signal, row)
    return row


def _receipt(row, status, task_id=None):
    message = {'created': '已按原授权创建完整业务流程；结果仍须人工核查，未自动更新对象状态',
        'skipped_existing_task': '此原登记已有业务交接；包括已取消历史均不重复派单',
        'blocked': '授权或原依据已变化，本次交接终止；请保留异常和人工说明，不会因换路由自动重试'}[status]
    return sealed({'schema': 'signal-graph-routing-delivery-receipt-v134', 'delivery_id': row.id,
        'intent_hash': row.data['immutable_hash'], 'status': status, 'task_id': task_id,
        'creation_hash': row.data['immutable']['creation_hash'], 'message': message,
        'source_time_verified': False, 'entity_state_updated': False, 'device_actions': False}, 'receipt_hash')


@guarded
def prepare_handoff(db, p, delivery, body, instance, release):
    row = scoped(db, Delivery, delivery.id if isinstance(delivery, Delivery) else delivery, p, True)
    imm, grant, original = _checked(db, p, row)
    need(row.status == 'reserved' and p.user_id == row.actor_id and instance.id == row.instance_id and release.id == row.release_id
        and same(body.model_dump(mode='json'), original.model_dump(mode='json')))
    entry = _grant(db, p, instance, row.route_id)
    need(route_status(entry, clock(db), quota=False) == 'active')
    current = single.actor(db, p.workspace_id, row.actor_id); need(current.role == grant['actor_role'])
    binding = _prepare(db, p, instance, preview_body(grant['command']))
    need(digest(binding) == digest(grant['binding']))
    signal = scoped(db, PerceptionSignal, row.signal_id, p, True)
    need(signal.version == body.signal_version)
    from .perception_signals import checked, guard_rule
    observation = scoped(db, Observation, row.observation_id, p, True)
    basis = checked(db, p, signal, allow_rejected=False)
    need(digest(basis) == imm['basis_hash'])
    if http_source.CONTRACT_KEY in grant['binding']:
        http_source.guard(db, p, grant['binding'][http_source.CONTRACT_KEY], observation,
            result=basis['extracted'], expected_origin=basis['simulation_origin'])
    else:
        guard_rule(db, p, observation, result=basis['extracted'])
    return copy.deepcopy(imm['source_snapshot'])


@guarded
def attach_origin(db, p, task, delivery, body, snapshot):
    row = scoped(db, Delivery, delivery.id if isinstance(delivery, Delivery) else delivery, p, True)
    imm, grant, original = _checked(db, p, row)
    need(row.status == 'reserved' and task.id is not None and task.owner_id == row.actor_id == p.user_id
        and task.workspace_id == p.workspace_id and same(task.data['perception_signal'], snapshot) and same(snapshot, imm['source_snapshot'])
        and task.data['creation_hash'] == imm['creation_hash'] and same(body.model_dump(mode='json'), imm['body']))
    signal = scoped(db, PerceptionSignal, row.signal_id, p)
    need(task.id in signal.data['task_ids'])
    row.task_id = task.id; row.status = 'created'; row.data = {**row.data, 'receipt': _receipt(row, 'created', task.id)}
    origin = sealed({'schema': 'signal-graph-task-origin-v134', 'delivery_id': row.id,
        'intent_hash': row.data['immutable_hash'], 'grant_hash': grant['grant_hash'],
        'creation_hash': imm['creation_hash'], 'goal_hash': digest(original.goal_spec.model_dump()),
        'input_hash': digest(original.graph_inputs)}, 'snapshot_hash')
    task.data = {**task.data, ORIGIN: origin}; _publish(signal, row)
    audit(db, p, 'signal_graph_routing.created', row.id, {'task_id': task.id,
        'creation_hash': imm['creation_hash'], 'receipt_hash': row.data['receipt']['receipt_hash']})
    _delivery_audit(db, p, row)
    db.flush()


@guarded
def verify_task(db, p, task):
    rows = list(db.scalars(select(Delivery).where(Delivery.workspace_id == p.workspace_id, Delivery.task_id == task.id).limit(2)))
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'signal_graph_routing.created', Audit.data['task_id'].as_string() == task.id).limit(2)))
    marker = task.data.get(ORIGIN)
    if not rows:
        # Deleting both mutable links cannot erase the independently written
        # original creation. Only a Task with no issued-route trace is legacy.
        need(marker is None and not anchors)
        return None
    need(len(rows) == 1 and type(marker) is dict and len(anchors) == 1
        and anchors[0].object_id == rows[0].id)
    row = rows[0]; imm, grant, body = _checked(db, p, row)
    expected = sealed({'schema': 'signal-graph-task-origin-v134', 'delivery_id': row.id,
        'intent_hash': row.data['immutable_hash'], 'grant_hash': grant['grant_hash'], 'creation_hash': imm['creation_hash'],
        'goal_hash': digest(body.goal_spec.model_dump()), 'input_hash': digest(body.graph_inputs)}, 'snapshot_hash')
    need(marker == expected and row.status == 'created' and task.owner_id == row.actor_id
        and task.workspace_id == row.workspace_id and task.data['instance_id'] == row.instance_id
        and task.data['instance_release_id'] == row.release_id and task.data['business_id'] == body.business_id
        and task.data['entity_ids'] == body.entity_ids and task.data['creation_hash'] == imm['creation_hash']
        and task.data['creation_request_id'] == body.request_id and same(task.data['perception_signal'], imm['source_snapshot']))
    entries = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == 'signal_graph_routing.created').limit(2)))
    need(len(entries) == 1 and entries[0].id == anchors[0].id
        and entries[0].actor_id == row.actor_id and entries[0].data == {
        'task_id': task.id, 'creation_hash': imm['creation_hash'], 'receipt_hash': row.data['receipt']['receipt_hash']})
    # The creation transaction attaches this proof before defining the Goal.
    # Absence is permitted by history verification only, never by live guards.
    if task.data.get('goal_spec') is not None:
        need(digest(task.data['goal_spec']) == marker['goal_hash'] == task.data.get('goal_hash'))
    return row


@guarded
def guard_current(db, p, task, *, lock=True):
    row = verify_task(db, p, task)
    if row is None: return
    query = select(DomainInstance).where(DomainInstance.id == row.instance_id, DomainInstance.workspace_id == p.workspace_id)
    # Actual dispatch keeps this shared Instance grant lock until its commit;
    # pause takes the exclusive lock. GET never acquires this action lock.
    if lock: query = query.with_for_update(read=True)
    instance = db.scalar(query.execution_options(populate_existing=True)); need(instance is not None)
    entry = _grant(db, p, instance, row.route_id); grant = entry['grant']
    need(route_status(entry, clock(db), quota=False) == 'active', '原异常流程授权已暂停或到期，原输入、草稿和回执仍保留')
    owner, _ = _actor(db, p.workspace_id, row.actor_id, lock=lock); need(owner.role == grant['actor_role'])
    # The node actor may differ from the grant owner. Verify the original
    # owner's current grant without impersonating it for the node's execution.
    binding = _prepare(db, owner, instance, preview_body(grant['command']), lock_selection=False)
    need(digest(binding) == digest(grant['binding']))
    imm = row.data['immutable']; expected = imm['body']['goal_spec']
    need(task.data.get('goal_spec') is not None and digest(task.data['goal_spec']) == digest(expected)
        and task.data.get('native_goal_context') is not None
        and task.data['native_goal_context_hash'] == digest(task.data['native_goal_context']))


def task_state(db, p, task):
    found = db.scalar(select(Delivery).where(Delivery.workspace_id == p.workspace_id, Delivery.task_id == task.id))
    if found is None and ORIGIN not in task.data:
        anchor = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'signal_graph_routing.created', Audit.data['task_id'].as_string() == task.id).limit(1))
        if anchor is None: return None
    status = 'unavailable'; message = '原自动交接依据需要核对；可保留草稿、读取原请求或停止任务'
    try:
        row = verify_task(db, p, task)
        instance = scoped(db, DomainInstance, row.instance_id, p)
        entry = _grant(db, p, instance, row.route_id)
        current = route_status(entry, clock(db), quota=False)
        if current in {'paused', 'expired'}:
            status = current; message = '原自动流程授权已暂停' if current == 'paused' else '原自动流程授权已到期'
        else:
            guard_current(db, p, task, lock=False); status = 'active'; message = '原授权当前有效；每次新执行和提交仍由服务器重新核对'
    except HTTPException:
        pass
    return {'schema': 'signal-graph-task-state-v134', 'status': status, 'message': message,
        'can_submit_new_result': status == 'active', 'can_retry_ability': status == 'active',
        'delivery_id': found.id if found else None}


def public_state(signal):
    value = signal.data.get('signal_graph_route_v134')
    if value is None: return {'graph_route': None}
    if type(value) is not dict or value.get('view_hash') != digest({key: val for key, val in value.items() if key != 'view_hash'}):
        return {'graph_route': {'schema': 'signal-graph-routing-public-v134', 'delivery_id': None, 'status': 'blocked',
            'task_id': None, 'message': '自动交接显示记录需要核对；原异常保留，不自动重试', 'terminal': True, 'automatic_retry': False}}
    return {'graph_route': {key: copy.deepcopy(val) for key, val in value.items() if key != 'view_hash'}}


@guarded
def dispatch(db, workspace_id, delivery_id):
    initial = db.scalar(select(Delivery).where(Delivery.id == delivery_id, Delivery.workspace_id == workspace_id))
    if initial is None: return
    p = Principal(initial.actor_id, workspace_id, 'viewer', 'graph-routing-delivery')
    instance = scoped(db, DomainInstance, initial.instance_id, p, True)
    signal = scoped(db, PerceptionSignal, initial.signal_id, p, True)
    row = scoped(db, Delivery, initial.id, p, True)
    if row.status != 'reserved':
        _checked(db, p, row)
        return
    # A malformed immutable ledger is an integrity failure, not a new signed
    # blocked outcome. Only a verified original intent may reach creation.
    _checked(db, p, row)
    try:
        with db.begin_nested():
            imm, grant, body = _checked(db, p, row)
            actor = single.actor(db, workspace_id, row.actor_id); need(actor.role == grant['actor_role'])
            observation = scoped(db, Observation, row.observation_id, actor, True)
            old = single.existing_task(db, actor, signal, observation)
            if old:
                row.status = 'skipped_existing_task'; row.data = {**row.data, 'receipt': _receipt(row, row.status, old)}
                _delivery_audit(db, p, row)
            else:
                from .workspace_api import create_task
                create_task(instance.id, body, actor, db, graph_handoff=row)
                need(row.status == 'created' and row.task_id is not None)
    except HTTPException:
        db.refresh(row); row.status = 'blocked'; row.task_id = None
        row.data = {**row.data, 'receipt': _receipt(row, 'blocked')}
        _delivery_audit(db, p, row)
    _publish(signal, row)


def deliver_pending(workspace_id=None):
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        query = select(Delivery.workspace_id, Delivery.id).join(Event, Event.id == Delivery.event_id).where(
            Delivery.status == 'reserved', Event.status == 'received', Event.workspace_id == Delivery.workspace_id)
        if workspace_id is not None: query = query.where(Delivery.workspace_id == workspace_id)
        values = list(db.execute(query.order_by(Delivery.created_at, Delivery.id).limit(100)))
    for workspace, identity in values:
        with Session(engine()) as db:
            dispatch(db, workspace, identity); db.commit()
