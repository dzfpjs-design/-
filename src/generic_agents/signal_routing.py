"""Explicit 24-hour routing of new reported anomalies to human review Tasks."""
import copy
import json
import math
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, func

from .contracts import digest
from .models import DomainInstance, PerceptionSignal, Observation, Event, Task, User, Membership, Capability, Entity, SetupMaterial, now
from .security import Principal
from .services import scoped, published, require, check_version, audit

STATE = 'explicit-signal-routing-v1'
REQUEST = 'signal-routing-request-v1'
INTENT = 'signal-routing-intent-v1'
DELIVERY = 'signal-routing-delivery-v1'
MAX_BYTES = 2 * 1024 * 1024


def fail(code, message='路由依据或授权已变化；原通知与人工任务保留'):
    raise HTTPException(409, {'code': code, 'message': message})


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None: fail('routing_time_invalid')
    return result


def sealed(value, key):
    return {**copy.deepcopy(value), key: digest(value)}


def request_hash(p, instance_id, action, route_id, body):
    return digest(dict(schema=REQUEST, workspace_id=p.workspace_id, actor_id=p.user_id,
        instance_id=instance_id, action=action, route_id=route_id, body=body))


def actor(db, workspace_id, user_id):
    row = db.execute(select(User.id, Membership.role).join(Membership, Membership.user_id == User.id)
        .where(User.id == user_id, User.active.is_(True), Membership.workspace_id == workspace_id)
        .with_for_update(read=True, of=(User, Membership))).first()
    if not row or row.role not in {'admin', 'operator'}:
        raise HTTPException(403, '路由授权人已停用或失去资料/业务操作权限')
    return Principal(user_id, workspace_id, row.role, 'explicit-signal-routing')


def state(instance):
    value = copy.deepcopy(instance.data.get('signal_routes', {'schema': STATE, 'routes': [], 'requests': []}))
    try:
        if (set(value) != {'schema', 'routes', 'requests'} or value['schema'] != STATE
                or type(value['routes']) is not list or type(value['requests']) is not list
                or len(value['routes']) > 100 or len(value['requests']) > 200): fail('routing_state_invalid')
        ids = []
        for entry in value['routes']:
            g = entry['grant']; ids.append(g['id'])
            if (set(entry) != {'grant', 'paused_at', 'paused_by', 'pause_reason', 'pause_request_id', 'task_ids'}
                    or g['grant_hash'] != digest({k:v for k,v in g.items() if k != 'grant_hash'})
                    or g['instance_id'] != instance.id or g['workspace_id'] != instance.workspace_id
                    or stamp(g['expires_at']) - stamp(g['enabled_at']) != timedelta(hours=24)
                    or type(g['max_tasks']) is not int or not 1 <= g['max_tasks'] <= 100
                    or type(entry['task_ids']) is not list or len(entry['task_ids']) > g['max_tasks']
                    or len(entry['task_ids']) != len(set(entry['task_ids']))): fail('routing_state_invalid')
        if len(ids) != len(set(ids)): fail('routing_state_invalid')
        requests = []
        for old in value['requests']:
            r = old['receipt']; requests.append(r['request_id'])
            principal = Principal(r['actor_id'], r['workspace_id'], 'viewer', 'saved-routing-request')
            if (r['receipt_hash'] != digest({k:v for k,v in r.items() if k != 'receipt_hash'})
                    or r['instance_id'] != instance.id or r['workspace_id'] != instance.workspace_id
                    or r['route_id'] not in ids or r['accepted_version'] > instance.version
                    or r['request_hash'] != request_hash(principal, instance.id, r['action'],
                        None if r['action'] == 'enable' else r['route_id'], old['body'])): fail('routing_state_invalid')
        if len(requests) != len(set(requests)): fail('routing_state_invalid')
        return value
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        fail('routing_state_invalid')


def route_status(entry, clock=None):
    clock = clock or now()
    if entry['paused_at']: return 'paused'
    if clock >= stamp(entry['grant']['expires_at']): return 'expired'
    if len(entry['task_ids']) >= entry['grant']['max_tasks']: return 'exhausted'
    return 'active'


def cap_ref(cap):
    return {'id': cap.id, 'name': cap.data.get('description') or cap.name,
            'version': cap.version, 'content_hash': cap.content_hash}


def extractor_ref(cap):
    rules=cap.data['specification'].get('rules',[])
    if (not isinstance(rules,list) or not rules or len(rules)>100
            or any(not isinstance(r,dict) or any(type(r.get(k)) is not str or not r[k].strip() for k in ('id','field','operator'))
                or r['operator'] not in {'gt','lt'} or type(r.get('threshold')) not in {int,float}
                or type(r['threshold']) is float and not math.isfinite(r['threshold']) for r in rules)):
        fail('routing_local_rules_required','请先登记有明确字段、比较方式与有限数值阈值的本地规则')
    return {**cap_ref(cap),'rules':[{**{k:r[k] for k in ('id','field','operator')},'threshold':str(r['threshold'])} for r in rules]}


def prepare(db, p, instance, release_id, source_id, extractor_id, entity_ids, business_id):
    from .instance_api import active
    from .instance_compiler import material_ids_for_flow, verify_material_snapshots
    from .entity_release_compat import require_entity
    from .perception_signals import rule_contract
    _, release = active(db, p, instance.id)
    if release.id != release_id: fail('routing_release_changed')
    business = next((b for b in release.data['snapshot']['businesses'] if b['id'] == business_id), None)
    if (not business or not business.get('available') or business_id not in instance.data.get('enabled_business_ids', [])
            or not business['flow'].get('steps') or any(s['agent'] != '人工确认' for s in business['flow']['steps'])
            or business['execution_mode'] != 'confirmed_human_workflow'): fail('routing_human_business_required')
    roles = {}
    for step in business['flow']['steps']:
        binding = release.data['snapshot']['roles'][step['role']]
        actor(db, p.workspace_id, binding['user_id']); roles[step['role']] = copy.deepcopy(binding)
    source, extractor = published(db, p, source_id, 'source'), published(db, p, extractor_id)
    if 'perception' not in extractor.data.get('allowed_agent_roles', []): fail('routing_local_rules_required')
    entities, definitions = [], {}
    for entity_id in entity_ids:
        obj = scoped(db, Entity, entity_id, p)
        definition = require_entity(db, p, instance, release, obj)
        rule_contract(db, p, instance, release, obj, source, extractor)
        entities.append({'id': obj.id, 'name': next((v for v in obj.data.values() if type(v) is str and v.strip()), obj.external_id),
                         'version':obj.version,'definition_hash':digest(definition)})
        definitions[obj.id] = digest(definition)
    material_ids = material_ids_for_flow(business['flow']) | set(release.data['snapshot'].get('relation_material_ids', [])) | set(release.data['snapshot'].get('object_material_ids', []))
    materials = [s for s in release.data['snapshot']['materials'] if s['id'] in material_ids]
    if {s['id'] for s in materials} != material_ids: fail('routing_material_scope_invalid')
    # Archive/version writers lock these same rows. Keep this fresh check
    # serialized until the Task and its routing receipt commit together.
    lock_ids=set(material_ids)
    for material in materials:
        for pin in material.get('report_sources',[]):
            from .report_configuration_handoff import owned_record
            proof=owned_record(db,p,pin['source_review_id'],kind='source_review').data['record']
            lock_ids.update(m['id'] for m in proof['materials'])
    for identity in sorted(lock_ids): scoped(db,SetupMaterial,identity,p,True)
    verify_material_snapshots(db, p, materials)
    return dict(source=cap_ref(source), extractor=extractor_ref(extractor), entities=entities,
        business={'id': business['id'], 'name': business['name']}, definitions=definitions,
        business_hash=digest({'business': business, 'roles': roles}), materials_hash=digest(materials))


def route_view(entry):
    g = entry['grant']; binding = g['binding']
    return {**{k:g[k] for k in ('id','instance_id','release_id','actor_id','enabled_at','expires_at','max_tasks','reason','grant_hash')},
        **{k:copy.deepcopy(binding[k]) for k in ('source','extractor','entities','business')},
        'status':route_status(entry), 'used_tasks':len(entry['task_ids']), 'paused_at':entry['paused_at']}


def reply(instance, receipt=None):
    routes = [route_view(r) for r in state(instance)['routes']]
    current = next((r for r in routes if receipt and r['id'] == receipt['route_id']), None)
    return dict(schema='signal-routing-response-v1', instance_id=instance.id, instance_version=instance.version,
        routes=routes, receipt=copy.deepcopy(receipt), current_matches=(bool(current and
            receipt['accepted_version'] == instance.version and current['status'] == receipt['accepted_route_status']) if receipt else None),
        human_review_only=True, device_actions=False, cloud_calls=0)


def saved_request(instance, p, request_id):
    item = next((x for x in state(instance)['requests'] if x['receipt']['request_id'] == request_id), None)
    if not item or item['receipt']['actor_id'] != p.user_id:
        raise HTTPException(404, '没有本人已保存的路由请求')
    return reply(instance, item['receipt'])


def save_state(instance, value):
    proposed = {**instance.data, 'signal_routes': value}
    if len(json.dumps(proposed, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        fail('routing_capacity', '路由历史已达容量上限；原授权与回执保留，未创建新任务')
    instance.data = proposed; instance.version += 1


def command(db, p, instance_id, action, body, route_id=None):
    require(p, 'admin', 'operator')
    instance = scoped(db, DomainInstance, instance_id, p, True)
    value = state(instance); signature = request_hash(p, instance_id, action, route_id, body)
    prior = next((x for x in value['requests'] if x['receipt']['request_id'] == body['request_id']), None)
    if prior:
        if prior['receipt']['request_hash'] != signature: fail('routing_nonce_conflict', '此原请求编号已用于不同内容，回执保留')
        return reply(instance, prior['receipt'])
    live = actor(db, p.workspace_id, p.user_id); check_version(instance, body['expected_version'])
    if len(value['requests']) >= 200: fail('routing_capacity')
    clock = now()
    if action == 'enable':
        if len(value['routes']) >= 100: fail('routing_capacity')
        from .signal_graph_routing_v134 import assert_no_active
        assert_no_active(instance)
        from .signal_approval_routing_v247 import assert_no_active as approval_no_active
        approval_no_active(db,p,instance,body['source_id'],body['extractor_id'],body['entity_ids'])
        from .signal_dual_handoffs import active_routes
        if active_routes(instance): fail('routing_already_active', '请先明确暂停双路授权，再启用单路授权')
        if any(route_status(r, clock) == 'active' for r in value['routes']): fail('routing_already_active', '请先明确暂停现有路由，再启用新授权')
        lock_selection(db,p,body['source_id'],body['extractor_id'],body['entity_ids'])
        if body['options_hash'] != options(db,p,instance)['options_hash']:
            fail('routing_options_changed','已展示的来源、规则、对象或业务发生变化；请重新核对，不改签原请求')
        binding = prepare(db, live, instance, **{k:body[k] for k in ('release_id','source_id','extractor_id','entity_ids','business_id')})
        grant = sealed(dict(id=str(uuid4()), actor_id=p.user_id, actor_role=live.role,
            workspace_id=p.workspace_id, instance_id=instance_id, enabled_at=clock.isoformat(),
            expires_at=(clock+timedelta(hours=24)).isoformat(), binding=binding,
            **{k:copy.deepcopy(body[k]) for k in ('release_id','source_id','extractor_id','entity_ids','business_id','max_tasks','reason')}), 'grant_hash')
        route_id = grant['id']; value['routes'].append(dict(grant=grant, paused_at=None, paused_by=None,
            pause_reason=None, pause_request_id=None, task_ids=[])); accepted = 'active'
    elif action == 'pause':
        entry = next((x for x in value['routes'] if x['grant']['id'] == route_id), None)
        if not entry: raise HTTPException(404, '没有此路由授权')
        if entry['paused_at']: fail('routing_already_paused', '此路由已暂停；旧通知不会重放')
        entry.update(paused_at=clock.isoformat(), paused_by=p.user_id, pause_reason=body['reason'], pause_request_id=body['request_id'])
        accepted = 'paused'
    else: fail('routing_action_invalid')
    receipt = sealed(dict(request_id=body['request_id'], action=action, actor_id=p.user_id,
        workspace_id=p.workspace_id, instance_id=instance_id, route_id=route_id, request_hash=signature,
        accepted_version=instance.version+1, accepted_at=clock.isoformat(), accepted_route_status=accepted), 'receipt_hash')
    value['requests'].append({'body':copy.deepcopy(body), 'receipt':receipt})
    save_state(instance, value)
    audit(db, p, 'signal_routing.'+action, instance.id, {'route_id':route_id,'request_id':body['request_id'],'request_hash':signature})
    return reply(instance, receipt)


def options(db, p, instance, *, actor_check=None, lock_sources=True):
    if actor_check is None: actor_check = actor
    from .instance_api import active
    _, release = active(db, p, instance.id)
    sources, extractors = [], []
    for cap in db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.status == 'published')):
        try: published(db, p, cap.id)
        except HTTPException: continue
        execution = cap.data['execution']
        if execution.get('side_effect') or cap.data['data_policy'].get('cloud_allowed') is not False: continue
        if cap.kind == 'source' and (execution.get('adapter') == 'upload' or execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules'):
            sources.append(cap_ref(cap))
        if execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules' and 'perception' in cap.data.get('allowed_agent_roles', []):
            try:extractors.append(extractor_ref(cap))
            except HTTPException:continue
    from .entity_release_compat import require_entity
    entities = []
    for entity in db.scalars(select(Entity).where(Entity.workspace_id == p.workspace_id, Entity.namespace == instance.namespace)):
        try:
            definition = (require_entity(db, p, instance, release, entity) if lock_sources else
                          require_entity(db, p, instance, release, entity, lock_sources=False))
        except HTTPException: continue
        entities.append({'id':entity.id,'name':next((v for v in entity.data.values() if type(v) is str and v.strip()),entity.external_id),
                         'version':entity.version,'definition_hash':digest(definition)})
    businesses = []
    for b in release.data['snapshot']['businesses']:
        from .signal_approval_targets_v247 import manual as actual_manual
        if (b['id'] not in instance.data.get('enabled_business_ids', []) or not b.get('available')
                or not actual_manual(b)): continue
        try:
            for s in b['flow']['steps']: actor_check(db, p.workspace_id, release.data['snapshot']['roles'][s['role']]['user_id'])
        except HTTPException: continue
        businesses.append({'id':b['id'],'name':b['name']})
    from .signal_graph_routing_v134 import active_routes as active_graph_routes
    blocked = (p.role not in {'admin','operator'} or bool(active_graph_routes(instance)) or any(route_status(x) == 'active' for x in state(instance)['routes'])
               or not all([sources,extractors,entities,businesses]))
    return sealed(dict(schema='signal-routing-options-v1',instance_id=instance.id,instance_version=instance.version,release_id=release.id,
        ttl_seconds=86400,max_tasks_default=10,max_tasks_limit=100,sources=sorted(sources,key=lambda x:x['id']),
        extractors=sorted(extractors,key=lambda x:x['id']),entities=sorted(entities,key=lambda x:x['id']),
        businesses=sorted(businesses,key=lambda x:x['id']),can_enable=not blocked,
        blocking_reason='当前账号只读、已有有效路由，或缺少合格来源/对象/人工业务' if blocked else None), 'options_hash')


def lock_selection(db,p,source_id,extractor_id,entity_ids):
    for identity in sorted({source_id,extractor_id}): scoped(db,Capability,identity,p,True)
    for identity in sorted(entity_ids): scoped(db,Entity,identity,p,True)


def source_identity(observation):
    return digest({'source_id':observation.source_id,'source_record_id':observation.source_record_id,'entity_id':observation.entity_id})


def first_registration(db, observation):
    return db.scalar(select(func.min(Observation.created_at)).where(Observation.workspace_id == observation.workspace_id,
        Observation.source_id == observation.source_id, Observation.source_record_id == observation.source_record_id))


def registration_matches(db, grant, event, observation, clock):
    original = observation.data.get('original_request'); contract = observation.data.get('reported_rule_contract')
    if not isinstance(original, dict) or digest(original) != observation.data.get('input_hash') or not isinstance(contract, dict):
        fail('routing_registration_invalid')
    if any(original.get(k) != getattr(observation,k) for k in ('source_id','source_record_id','processing_version','entity_id')):
        fail('routing_registration_invalid')
    if original.get('extractor_id') != observation.data.get('extractor_id'): fail('routing_registration_invalid')
    if (event.kind != 'perception.anomaly' or event.workspace_id != observation.workspace_id
            or event.data.get('observation_id') != observation.id or event.data.get('entity_id') != observation.entity_id
            or observation.workspace_id != grant['workspace_id'] or contract.get('instance_id') != grant['instance_id']
            or contract.get('release_id') != grant['release_id'] or observation.source_id != grant['source_id']
            or observation.data['extractor_id'] != grant['extractor_id'] or observation.entity_id not in grant['entity_ids']): return False
    first = first_registration(db, observation)
    return bool(first and stamp(grant['enabled_at']) <= first <= observation.created_at <= clock
                and event.created_at >= observation.created_at)


def freeze_intent(db, instance, event, observation, basis):
    clock = now(); entries = [r for r in state(instance)['routes'] if route_status(r,clock) == 'active']
    if not entries: return None
    if len(entries) != 1: fail('routing_state_invalid')
    grant = entries[0]['grant']
    if not registration_matches(db,grant,event,observation,clock): return None
    if basis['schema'] != 'reported-anomaly-v1' or basis['release_id'] != grant['release_id']: return None
    identity = source_identity(observation)
    from .signal_graph_routing_v134 import has_source_family
    if has_source_family(db,instance.workspace_id,instance.id,identity):return None
    return sealed(dict(schema=INTENT,route_id=grant['id'],grant_hash=grant['grant_hash'],actor_id=grant['actor_id'],
        workspace_id=instance.workspace_id,instance_id=instance.id,release_id=grant['release_id'],
        event_id=event.id,observation_id=observation.id,observation_created_at=observation.created_at.isoformat(),
        input_hash=observation.data['input_hash'],basis_hash=digest(basis),source_identity_hash=identity,
        dedup_key=digest({'route_id':grant['id'],'source_identity_hash':identity}),frozen_at=clock.isoformat()), 'intent_hash')


def checked_intent(value):
    keys={'schema','route_id','grant_hash','actor_id','workspace_id','instance_id','release_id','event_id',
          'observation_id','observation_created_at','input_hash','basis_hash','source_identity_hash','dedup_key','frozen_at','intent_hash'}
    try:
        if (not isinstance(value,dict) or set(value)!=keys or value['schema']!=INTENT
                or value['intent_hash']!=digest({k:v for k,v in value.items() if k!='intent_hash'})): fail('routing_intent_invalid')
        for key in ('route_id','actor_id','workspace_id','instance_id','release_id','event_id','observation_id'):
            if str(UUID(value[key]))!=value[key]: fail('routing_intent_invalid')
        for key in ('grant_hash','input_hash','basis_hash','source_identity_hash','dedup_key','intent_hash'):
            if type(value[key]) is not str or len(value[key])!=64 or any(c not in '0123456789abcdef' for c in value[key]): fail('routing_intent_invalid')
        if stamp(value['frozen_at'])<stamp(value['observation_created_at']): fail('routing_intent_invalid')
        return value
    except (KeyError,TypeError,ValueError,AttributeError): fail('routing_intent_invalid')


def checked_receipt(value):
    keys={'schema','route_id','intent_hash','status','code','message','task_id','request_id','source_identity_hash',
          'dedup_key','completed_at','cloud_calls','device_actions','source_time_verified','receipt_hash'}
    if (not isinstance(value,dict) or set(value)!=keys or value.get('schema')!=DELIVERY
            or value.get('receipt_hash')!=digest({k:v for k,v in value.items() if k!='receipt_hash'})
            or value['status'] not in {'created','skipped_existing_task','duplicate','blocked','failed'}
            or value['cloud_calls']!=0 or value['device_actions'] is not False or value['source_time_verified'] is not False
            or value['status'] in {'created','skipped_existing_task'} and not value['task_id']
            or value['status'] in {'blocked','failed'} and value['task_id'] is not None): fail('routing_delivery_invalid')
    return value


def checked_delivery(value,intent,signal_id):
    receipt=checked_receipt(value)
    if (any(receipt[k]!=intent[k] for k in ('route_id','intent_hash','source_identity_hash','dedup_key'))
            or receipt['request_id']!='route_'+digest(intent['route_id']+signal_id)):
        fail('routing_delivery_invalid')
    return receipt


def failure_state(source):
    return sealed(dict(schema='signal-routing-failure-v1',code='routing_integrity',
        message='自动交接依据或回执损坏；原值保留，本次不会自动重试',failed_at=now().isoformat(),
        source_intent_hash=digest(source)),'failure_hash')


def receipt_actor(db,signal):
    # Attribution is derived from the original scoped DB rows, never a bad intent.
    event=db.get(Event,signal.event_id)
    observation=db.get(Observation,event.data.get('observation_id')) if event and event.workspace_id==signal.workspace_id else None
    return db.get(User,observation.data.get('submitted_by')) if observation and observation.workspace_id==signal.workspace_id else None


def public_state(signal):
    intent = signal.data.get('route_intent'); receipt = signal.data.get('route_receipt')
    failure=signal.data.get('route_failure')
    if failure is not None:
        valid=isinstance(failure,dict) and failure.get('failure_hash')==digest({k:v for k,v in failure.items() if k!='failure_hash'})
        return {'route_intent':None,'route_receipt':None,'route_failure':copy.deepcopy(failure) if valid else
            {'schema':'signal-routing-failure-v1','code':'routing_integrity','message':'路由隔离记录无法核实；原异常保留，不自动重试'}}
    try:
        if intent: checked_intent(intent)
        if receipt:
            if not intent: fail('routing_delivery_invalid')
            checked_delivery(receipt,intent,signal.id)
    except HTTPException:
        return {'route_intent':None,'route_receipt':None,'route_failure':{'schema':'signal-routing-failure-v1',
            'code':'routing_integrity','message':'自动交接依据或回执损坏，原记录保留；本次不会自动重试'}}
    return {'route_intent':{k:intent[k] for k in ('route_id','grant_hash','intent_hash','frozen_at','observation_created_at')} if intent else None,
            'route_receipt':copy.deepcopy(receipt),'route_failure':None}


def quarantine(db,signal):
    failure=failure_state(signal.data.get('route_intent'))
    signal.data={**signal.data,'route_failure':failure};signal.version+=1
    user=receipt_actor(db,signal)
    if user:
        audit(db,Principal(user.id,signal.workspace_id,'viewer','original-signal-receiver'),
            'signal_routing.quarantined',signal.id,{'failure_hash':failure['failure_hash'],'code':'routing_integrity'})


def existing_task(db, p, signal, observation):
    if signal.data.get('task_ids'):
        task=scoped(db,Task,signal.data['task_ids'][0],p)
        if task.data.get('instance_id')!=signal.instance_id or task.data.get('perception_signal',{}).get('signal_id')!=signal.id:
            fail('routing_task_binding_invalid')
        return task.id
    # Do not lock old Tasks: complete-step locks Task -> Signal. The instance
    # lock already serializes all manual and automatic task creation instead.
    family = select(Observation.id).where(Observation.workspace_id == p.workspace_id,
        Observation.source_id == observation.source_id, Observation.source_record_id == observation.source_record_id)
    return db.scalar(select(Task.id).where(Task.workspace_id == p.workspace_id,
        Task.data['instance_id'].as_string() == signal.instance_id,
        Task.data['perception_signal']['basis']['observation_id'].as_string().in_(family)).limit(1))


def dispatch(db, workspace_id, signal_id):
    initial = db.scalar(select(PerceptionSignal).where(PerceptionSignal.id == signal_id, PerceptionSignal.workspace_id == workspace_id))
    if not initial: return
    instance = db.scalar(select(DomainInstance).where(DomainInstance.id == initial.instance_id,
        DomainInstance.workspace_id == workspace_id).with_for_update().execution_options(populate_existing=True))
    signal = db.scalar(select(PerceptionSignal).where(PerceptionSignal.id == signal_id,
        PerceptionSignal.workspace_id == workspace_id).with_for_update().execution_options(populate_existing=True))
    if signal.data.get('route_failure') is not None:return
    try:
        intent = checked_intent(signal.data['route_intent'])
        if signal.data.get('route_receipt') is not None:
            checked_delivery(signal.data['route_receipt'],intent,signal.id); return
    except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
        quarantine(db,signal);return
    request_id = 'route_'+digest(intent['route_id']+signal.id)
    task_id = None; status,code,message = 'failed','routing_integrity','路由依据无法核对；本次不会自动重试，请查看原异常'
    try:
        with db.begin_nested():
            if (not instance or intent['workspace_id'] != workspace_id or intent['instance_id'] != instance.id
                    or intent['event_id'] != signal.event_id or intent['basis_hash'] != signal.data['basis_hash']): fail('routing_intent_invalid')
            value = state(instance); entry = next((x for x in value['routes'] if x['grant']['id'] == intent['route_id']), None)
            if not entry or entry['grant']['grant_hash'] != intent['grant_hash']: fail('routing_grant_changed')
            grant = entry['grant']
            if intent['actor_id'] != grant['actor_id'] or intent['release_id'] != grant['release_id']: fail('routing_intent_invalid')
            p = actor(db,workspace_id,grant['actor_id'])
            if p.role != grant['actor_role']: fail('routing_role_changed')
            from .perception_signals import checked, guard_rule
            # Human verification locks Observation; hold its current version
            # through commit so a rejected source cannot race a new Task.
            observation = scoped(db,Observation,intent['observation_id'],p,True)
            basis = checked(db,p,signal,allow_rejected=False)
            event = scoped(db,Event,intent['event_id'],p)
            if (not registration_matches(db,grant,event,observation,stamp(intent['frozen_at']))
                    or observation.data['input_hash'] != intent['input_hash']
                    or observation.created_at != stamp(intent['observation_created_at'])
                    or source_identity(observation) != intent['source_identity_hash']
                    or intent['dedup_key'] != digest({'route_id':grant['id'],'source_identity_hash':source_identity(observation)})):
                fail('routing_registration_invalid')
            from .signal_graph_routing_v134 import has_source_family
            if has_source_family(db,workspace_id,instance.id,source_identity(observation)):
                fail('routing_source_has_graph_handoff','此原登记已有完整流程交接记录，未重复创建人工任务')
            prior_task = existing_task(db,p,signal,observation)
            prior = db.scalar(select(PerceptionSignal).where(PerceptionSignal.workspace_id == workspace_id,
                PerceptionSignal.id != signal.id, PerceptionSignal.data['route_receipt']['dedup_key'].as_string() == intent['dedup_key']).limit(1))
            if prior_task:
                task_id,status,code,message = prior_task,'skipped_existing_task','already_handed_off','此原登记已有业务任务，包括已结束或已取消任务；没有重新创建'
            elif prior:
                prior_receipt=checked_delivery(prior.data['route_receipt'],checked_intent(prior.data['route_intent']),prior.id)
                if prior_receipt['task_id'] is not None: fail('routing_task_binding_invalid')
                task_id,status,code,message = None,'duplicate','source_already_attempted','同一原登记已有路由回执；没有重新派送'
            else:
                if route_status(entry) != 'active': fail('routing_not_active','路由已暂停、过期或达到任务上限；旧通知不会重放')
                lock_selection(db,p,grant['source_id'],grant['extractor_id'],grant['entity_ids'])
                binding = prepare(db,p,instance,**{k:grant[k] for k in ('release_id','source_id','extractor_id','entity_ids','business_id')})
                if binding != grant['binding']: fail('routing_binding_changed')
                guard_rule(db,p,observation,result=basis['extracted'])
                from .workspace_api import NewTask, create_task, creation_body_hash
                body = NewTask(release_id=grant['release_id'],business_id=grant['business_id'],entity_ids=[signal.entity_id],
                    title='人工核查登记异常：'+basis['object_name'][:220],request_id=request_id,
                    signal_id=signal.id,signal_version=signal.version,signal_basis_hash=signal.data['basis_hash'])
                task = create_task(instance.id,body,p,db)
                snapshot = sealed(dict(schema='signal-auto-routing-v1',route_id=grant['id'],grant_hash=grant['grant_hash'],
                    intent_hash=intent['intent_hash'],request_id=request_id,creation_hash=creation_body_hash(body),
                    actor_id=p.user_id,source_identity_hash=intent['source_identity_hash'],dedup_key=intent['dedup_key'],
                    source_time_verified=False,device_actions=False,cloud_calls=0), 'snapshot_hash')
                task.data = {**task.data,'auto_routing':snapshot}
                task_id = task.id; entry['task_ids'].append(task.id); save_state(instance,value)
                status,code,message = 'created','human_review_task_created','已交给明确授权的人工核查业务；采集时间与现场状态仍未核实'
    except HTTPException as error:
        task_id=None
        status = 'blocked'; detail=error.detail
        code = detail['code'] if isinstance(detail,dict) and type(detail.get('code')) is str else 'routing_authority_or_evidence_changed'
        message = '授权、当前业务或原登记依据已变化；原线索保留，本次不自动重试'
    except (KeyError,TypeError,ValueError,AttributeError,IndexError,StopIteration):
        task_id=None
        pass  # Explicit integrity failures only; database/programming errors propagate.
    # A nested rollback restores Task/signal/instance together before a terminal
    # failure receipt. No call, capability dispatch or device effect occurs here.
    receipt = sealed(dict(schema=DELIVERY,route_id=intent['route_id'],intent_hash=intent['intent_hash'],
        status=status,code=code,message=message,task_id=task_id,request_id=request_id,
        source_identity_hash=intent['source_identity_hash'],dedup_key=intent['dedup_key'],completed_at=now().isoformat(),
        cloud_calls=0,device_actions=False,source_time_verified=False), 'receipt_hash')
    signal.data = {**signal.data,'route_receipt':receipt}; signal.version += 1
    user=receipt_actor(db,signal)
    if user:
        audit(db,Principal(user.id,workspace_id,'viewer','routing-receipt'),'signal_routing.delivery',signal.id,
              {'route_id':intent['route_id'],'status':status,'receipt_hash':receipt['receipt_hash'],'task_id':task_id})


def verify_task(db, p, task, *, lock_sources=False):
    from .signal_approval_routing_v247 import checked_task_origin
    if checked_task_origin(db,p,task,lock_sources=lock_sources) is not None:return
    from .signal_graph_routing_v134 import verify_task as verify_graph_task
    if verify_graph_task(db,p,task) is not None:return
    from .signal_dual_handoffs import verify_task as verify_dual_task
    if verify_dual_task(db,p,task): return
    snapshot = task.data.get('auto_routing')
    if not snapshot:
        original=task.data.get('perception_signal')
        if original:
            signal=scoped(db,PerceptionSignal,original['signal_id'],p)
            receipt=signal.data.get('route_receipt') or {}
            if receipt.get('status')=='created' and receipt.get('task_id')==task.id:fail('routing_task_binding_invalid')
        return
    try:
        signal = scoped(db,PerceptionSignal,task.data['perception_signal']['signal_id'],p)
        intent=checked_intent(signal.data['route_intent'])
        receipt=checked_delivery(signal.data['route_receipt'],intent,signal.id)
        if (snapshot['snapshot_hash'] != digest({k:v for k,v in snapshot.items() if k != 'snapshot_hash'})
                or snapshot['schema'] != 'signal-auto-routing-v1' or snapshot['intent_hash'] != intent['intent_hash']
                or snapshot['grant_hash'] != intent['grant_hash'] or snapshot['route_id'] != intent['route_id']
                or snapshot['request_id'] != task.data['creation_request_id'] or snapshot['creation_hash'] != task.data['creation_hash']
                or snapshot['actor_id'] != intent['actor_id'] or task.owner_id != intent['actor_id']
                or snapshot['source_identity_hash'] != intent['source_identity_hash'] or snapshot['dedup_key'] != intent['dedup_key']
                or intent['instance_id'] != task.data['instance_id'] or intent['workspace_id'] != task.workspace_id
                or receipt['request_id'] != 'route_'+digest(intent['route_id']+signal.id)
                or snapshot['cloud_calls'] != 0 or snapshot['device_actions'] is not False or snapshot['source_time_verified'] is not False
                or receipt['task_id'] != task.id or receipt['status'] != 'created'
                or receipt['request_id'] != snapshot['request_id'] or receipt['intent_hash'] != snapshot['intent_hash']): fail('routing_task_binding_invalid')
    except (KeyError,TypeError,ValueError,AttributeError): fail('routing_task_binding_invalid')


def deliver_pending(workspace_id=None):
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        query = select(PerceptionSignal.workspace_id,PerceptionSignal.id).join(Event,Event.id == PerceptionSignal.event_id).where(
            Event.workspace_id == PerceptionSignal.workspace_id, Event.status == 'received',
            PerceptionSignal.data['route_intent']['schema'].as_string() == INTENT,
            PerceptionSignal.data['route_receipt'].as_string().is_(None),
            PerceptionSignal.data['route_failure'].as_string().is_(None))
        if workspace_id is not None: query=query.where(PerceptionSignal.workspace_id == workspace_id)
        pending = list(db.execute(query.order_by(PerceptionSignal.created_at,PerceptionSignal.id).limit(100)))
    for workspace,identity in pending:
        with Session(engine()) as db:
            dispatch(db,workspace,identity); db.commit()
