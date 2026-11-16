"""Fixed response/verification handoffs with independent durable transactions.

This module never claims Jobs, calls a model, contacts a device or sends an
external notification. Legacy single routing and workflow contracts stay typed.
"""
import base64
import copy
import json
import signal as os_signal
from datetime import timedelta
from functools import wraps
from threading import Event as StopEvent
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from .contracts import digest
from .models import DomainInstance, PerceptionSignal, Observation, Event, Task, InstanceRelease, Membership, User, Workspace, now
from .security import Principal
from .services import scoped, require, check_version, audit
from .signal_dual_models import SignalDualHandoff as Handoff
from . import signal_routing as single

STATE = 'signal-dual-routing-state-v1'
REQUEST = 'signal-dual-routing-request-v1'
PARENT = 'signal-dual-immutable-v1'
MARKER = 'signal-dual-task-v1'
SLOTS = ('response', 'verification')
TERMINAL = {'created', 'blocked', 'failed', 'cancelled', 'completed'}
MAX_BYTES = 2 * 1024 * 1024


def fail(code, message='双路交接依据无法核对，原异常与人工记录保留'):
    raise HTTPException(409, {'code': code, 'message': message})


def controlled(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError, StopIteration):
            fail('dual_integrity')
    return guarded


def sealed(value, key):
    return single.sealed(value, key)


def bounded(value):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        fail('dual_capacity', '双路交接记录已达容量上限；旧授权、原回执和人工说明保留')
    return value


@controlled
def state(instance):
    value = copy.deepcopy(instance.data.get('signal_dual_routes', {'schema': STATE, 'routes': [], 'requests': []}))
    if set(value) != {'schema', 'routes', 'requests'} or value['schema'] != STATE:
        fail('dual_state_invalid')
    if type(value['routes']) is not list or type(value['requests']) is not list or len(value['routes']) > 100 or len(value['requests']) > 200:
        fail('dual_state_invalid')
    ids = []
    for entry in value['routes']:
        grant = entry['grant']; ids.append(grant['id'])
        if (set(entry) != {'grant', 'paused_at', 'paused_by', 'pause_reason', 'pause_request_id', 'parent_ids'}
                or grant['grant_hash'] != digest({k:v for k,v in grant.items() if k != 'grant_hash'})
                or grant['workspace_id'] != instance.workspace_id or grant['instance_id'] != instance.id
                or single.stamp(grant['expires_at']) - single.stamp(grant['enabled_at']) != timedelta(hours=24)
                or type(grant['max_tasks']) is not int or not 2 <= grant['max_tasks'] <= 100
                or grant['response_business_id'] == grant['verification_business_id']
                or type(entry['parent_ids']) is not list or len(set(entry['parent_ids'])) != len(entry['parent_ids'])
                or len(entry['parent_ids']) * 2 > grant['max_tasks']): fail('dual_state_invalid')
    if len(set(ids)) != len(ids): fail('dual_state_invalid')
    nonces = []
    for item in value['requests']:
        receipt = item['receipt']; nonces.append(receipt['request_id'])
        p = Principal(receipt['actor_id'], instance.workspace_id, 'viewer', 'dual-receipt')
        if (receipt['schema'] != 'signal-dual-routing-receipt-v1'
                or receipt['receipt_hash'] != digest({k:v for k,v in receipt.items() if k != 'receipt_hash'})
                or receipt['workspace_id'] != instance.workspace_id or receipt['instance_id'] != instance.id
                or receipt['route_id'] not in ids or receipt['accepted_version'] > instance.version
                or receipt['action'] not in {'enable_dual', 'pause_dual'}
                or receipt['request_hash'] != request_hash(p, instance.id, receipt['action'],
                    None if receipt['action'] == 'enable_dual' else receipt['route_id'], item['body'])):
            fail('dual_state_invalid')
    if len(set(nonces)) != len(nonces): fail('dual_state_invalid')
    return value


def route_status(entry, clock=None):
    if entry['paused_at']: return 'paused'
    if (clock or now()) >= single.stamp(entry['grant']['expires_at']): return 'expired'
    if 2 * len(entry['parent_ids']) + 2 > entry['grant']['max_tasks']: return 'exhausted'
    return 'active'


def active_routes(instance):
    return [r for r in state(instance)['routes'] if route_status(r) == 'active']


def request_hash(p, instance_id, action, route_id, body):
    return digest(dict(schema=REQUEST, workspace_id=p.workspace_id, actor_id=p.user_id,
        instance_id=instance_id, action=action, route_id=route_id, body=body))


def save_state(instance, value):
    instance.data = bounded({**instance.data, 'signal_dual_routes': value}); instance.version += 1


def recipient(release, business_id):
    business = next(b for b in release.data['snapshot']['businesses'] if b['id'] == business_id)
    step = business['flow']['steps'][0]
    return {'role': step['role'], **copy.deepcopy(release.data['snapshot']['roles'][step['role']])}


def business_choice(release, business, binding):
    step = business['flow']['steps'][0]
    roles = {s['role']:release.data['snapshot']['roles'][s['role']] for s in business['flow']['steps']}
    return {'id': business['id'], 'name': business['name'], 'first_step_id': step['id'],
        'first_step_name': step['name'], 'recipient': recipient(release, business['id']), 'binding_hash': digest({'business':business,'roles':roles})}


@controlled
def options(db, p, instance):
    from .instance_api import active
    from .signal_dual_read_session import read_actor
    _, release = active(db, p, instance.id)
    legacy = single.options(db, p, instance, actor_check=read_actor, lock_sources=False)
    businesses = []
    # The complete hash is sealed on enable; these human summaries explain who
    # will initially receive each selected workflow without leaking raw config.
    for choice in legacy['businesses']:
        business = next(b for b in release.data['snapshot']['businesses'] if b['id'] == choice['id'])
        roles = {s['role']:release.data['snapshot']['roles'][s['role']] for s in business['flow']['steps']}
        binding = {'business':business, 'roles':roles}
        businesses.append(business_choice(release, business, binding))
    from .signal_graph_routing_v134 import active_routes as active_graph_routes
    blocked = (p.role not in {'admin','operator'} or active_routes(instance) or active_graph_routes(instance)
        or any(single.route_status(r) == 'active' for r in single.state(instance)['routes'])
        or len(businesses) < 2 or not all(legacy[k] for k in ('sources','extractors','entities')))
    return sealed(dict(schema='signal-dual-options-v1', instance_id=instance.id, instance_version=instance.version,
        release_id=release.id, ttl_seconds=86400, max_tasks_default=10, max_tasks_limit=100, max_tasks_min=2,
        sources=legacy['sources'], extractors=legacy['extractors'], entities=legacy['entities'], businesses=businesses,
        can_enable=not bool(blocked), blocking_reason='当前只读、已有有效路由，或缺少两个合格人工业务及来源对象' if blocked else None), 'options_hash')


def route_view(db, entry):
    grant = entry['grant']
    task_count = 0
    if entry['parent_ids']:
        # Counting issued SQL pointers is not an authorization or an immutable
        # parent check. A quarantined parent must not block explicit pause.
        for parent in db.scalars(select(Handoff).where(Handoff.workspace_id == grant['workspace_id'],
            Handoff.instance_id == grant['instance_id'], Handoff.route_id == grant['id'], Handoff.id.in_(entry['parent_ids']))):
            task_count += int(parent.response_task_id is not None) + int(parent.verification_task_id is not None)
    return {**{k:grant[k] for k in ('id','instance_id','release_id','actor_id','enabled_at','expires_at','max_tasks','reason','grant_hash')},
        **{k:copy.deepcopy(grant['bindings']['response'][k]) for k in ('source','extractor','entities')},
        'response_business':copy.deepcopy(grant['businesses']['response']),
        'verification_business':copy.deepcopy(grant['businesses']['verification']),
        'status':route_status(entry), 'reserved_tasks':len(entry['parent_ids']) * 2,
        'created_tasks':task_count, 'paused_at':entry['paused_at']}


def reply(db, instance, receipt=None):
    routes = [route_view(db, r) for r in state(instance)['routes']]
    selected = next((r for r in routes if receipt and r['id'] == receipt['route_id']), None)
    return dict(schema='signal-dual-routing-response-v1', instance_id=instance.id, instance_version=instance.version,
        routes=routes, receipt=copy.deepcopy(receipt), current_matches=(bool(selected and
            instance.version == receipt['accepted_version'] and selected['status'] == receipt['accepted_route_status']) if receipt else None),
        human_review_only=True, device_actions=False, cloud_calls=0)


def saved_request(db, instance, p, request_id):
    item = next((r for r in state(instance)['requests'] if r['receipt']['request_id'] == request_id), None)
    if not item or item['receipt']['actor_id'] != p.user_id: raise HTTPException(404, '没有本人已保存的双路请求')
    return reply(db, instance, item['receipt'])


@controlled
def command(db, p, instance_id, action, body, route_id=None):
    instance = scoped(db, DomainInstance, instance_id, p, True); value = state(instance)
    signature = request_hash(p, instance_id, action, route_id, body)
    prior = next((r for r in value['requests'] if r['receipt']['request_id'] == body['request_id']), None)
    if prior:
        if prior['receipt']['request_hash'] != signature: fail('dual_nonce_conflict')
        return reply(db, instance, prior['receipt'])
    require(p, 'admin', 'operator')
    live = single.actor(db, p.workspace_id, p.user_id); check_version(instance, body['expected_version'])
    if len(value['requests']) >= 200: fail('dual_capacity')
    clock = now()
    if action == 'enable_dual':
        if len(value['routes']) >= 100: fail('dual_capacity')
        from .signal_graph_routing_v134 import assert_no_active
        assert_no_active(instance)
        from .signal_approval_routing_v247 import assert_no_active as approval_no_active
        approval_no_active(db,p,instance,body['source_id'],body['extractor_id'],body['entity_ids'])
        if active_routes(instance) or any(single.route_status(r) == 'active' for r in single.state(instance)['routes']):
            fail('dual_already_active', '请先暂停已有路由，再明确批准双路授权')
        single.lock_selection(db, p, body['source_id'], body['extractor_id'], body['entity_ids'])
        if body['options_hash'] != options(db,p,instance)['options_hash']: fail('dual_options_changed')
        if body['response_business_id'] == body['verification_business_id']: fail('dual_businesses_must_differ')
        bindings = {}; choices = {}; release = scoped(db, InstanceRelease, body['release_id'], p)
        for slot in SLOTS:
            bid = body[slot+'_business_id']
            bindings[slot] = single.prepare(db,live,instance,**{k:body[k] for k in ('release_id','source_id','extractor_id','entity_ids')},business_id=bid)
            b = next(b for b in release.data['snapshot']['businesses'] if b['id'] == bid)
            choices[slot] = business_choice(release,b,bindings[slot])
        grant = sealed(dict(id=str(uuid4()), actor_id=p.user_id, actor_role=live.role,
            workspace_id=p.workspace_id, instance_id=instance.id, enabled_at=clock.isoformat(), expires_at=(clock+timedelta(hours=24)).isoformat(),
            bindings=bindings, businesses=choices, **{k:copy.deepcopy(body[k]) for k in
                ('release_id','source_id','extractor_id','entity_ids','response_business_id','verification_business_id','max_tasks','reason')}), 'grant_hash')
        route_id = grant['id']; value['routes'].append(dict(grant=grant, paused_at=None, paused_by=None,
            pause_reason=None, pause_request_id=None, parent_ids=[])); accepted = 'active'
    elif action == 'pause_dual':
        entry = next((r for r in value['routes'] if r['grant']['id'] == route_id), None)
        if not entry: raise HTTPException(404, '没有此双路授权')
        if entry['paused_at']: fail('dual_already_paused')
        entry.update(paused_at=clock.isoformat(), paused_by=p.user_id, pause_reason=body['reason'], pause_request_id=body['request_id'])
        accepted = 'paused'
    else: fail('dual_action_invalid')
    receipt = sealed(dict(schema='signal-dual-routing-receipt-v1', request_id=body['request_id'], action=action,
        actor_id=p.user_id, workspace_id=p.workspace_id, instance_id=instance.id, route_id=route_id,
        request_hash=signature, accepted_version=instance.version+1, accepted_at=clock.isoformat(), accepted_route_status=accepted), 'receipt_hash')
    value['requests'].append({'body':copy.deepcopy(body), 'receipt':receipt}); save_state(instance,value)
    audit(db,p,'signal_dual.'+action,instance.id,{'route_id':route_id,'request_id':body['request_id'],'request_hash':signature})
    return reply(db,instance,receipt)


@controlled
def checked_parent(parent):
    immutable = parent.data['immutable']
    if (immutable['schema'] != PARENT or parent.data['immutable_hash'] != digest(immutable)
            or any(immutable[k] != getattr(parent,k) for k in ('workspace_id','instance_id','release_id','signal_id','event_id','observation_id','route_id','source_identity_hash'))
            or immutable['id'] != parent.id or set(parent.data['slots']) != set(SLOTS)
            or type(parent.data['ack_requests']) is not list or len(parent.data['ack_requests']) > 100): fail('dual_parent_invalid')
    for slot in SLOTS:
        value = parent.data['slots'][slot]; tid = getattr(parent,slot+'_task_id')
        if (value['state'] not in {'pending'} | TERMINAL or value['task_id'] != tid
                or (value['state'] in {'created','completed','cancelled'}) != bool(tid)): fail('dual_slot_invalid')
        if value['receipt'] is not None:
            receipt = value['receipt']
            if (receipt['receipt_hash'] != digest({k:v for k,v in receipt.items() if k != 'receipt_hash'})
                    or receipt['handoff_id'] != parent.id or receipt['slot'] != slot
                    or receipt['immutable_hash'] != parent.data['immutable_hash'] or receipt['task_id'] != tid
                    or receipt['request_id'] != creation_nonce(parent,slot)): fail('dual_slot_invalid')
    if parent.response_task_id and parent.response_task_id == parent.verification_task_id: fail('dual_slot_invalid')
    return immutable


def creation_nonce(parent, slot):
    return 'dual_'+digest(dict(parent_id=parent.id,slot=slot,immutable_hash=parent.data['immutable_hash']))


@controlled
def freeze_parent(db, instance, event, observation, basis, signal):
    if observation:
        # An exhausted/paused route can still recover an already accepted
        # source identity. It cannot turn a repeated notification into new work.
        existing_parent = db.scalar(select(Handoff).where(Handoff.workspace_id == instance.workspace_id,
            Handoff.instance_id == instance.id,Handoff.source_identity_hash == single.source_identity(observation))
            .order_by(Handoff.created_at,Handoff.id).limit(1))
        if existing_parent: checked_parent(existing_parent); return existing_parent
        from .signal_graph_routing_v134 import has_source_family
        if has_source_family(db,instance.workspace_id,instance.id,single.source_identity(observation)):
            return None
    entries = active_routes(instance)
    if not entries or not observation or not basis or basis['schema'] != 'reported-anomaly-v1': return None
    if len(entries) != 1: fail('dual_state_invalid')
    value = state(instance); entry = next(r for r in value['routes'] if r['grant']['id'] == entries[0]['grant']['id']); grant = entry['grant']
    if not single.registration_matches(db,grant,event,observation,now()): return None
    identity = single.source_identity(observation)
    prior = db.scalar(select(Handoff).where(Handoff.workspace_id == instance.workspace_id, Handoff.instance_id == instance.id,
        Handoff.route_id == grant['id'], Handoff.source_identity_hash == identity))
    if prior:
        checked_parent(prior); return prior
    # Manual or legacy handoffs, including cancelled/completed Tasks, are not
    # silently recreated as a fresh pair.
    family = select(Observation.id).where(Observation.workspace_id == instance.workspace_id,
        Observation.source_id == observation.source_id,Observation.source_record_id == observation.source_record_id)
    existing = db.scalar(select(Task.id).where(Task.workspace_id == instance.workspace_id,
        Task.data['instance_id'].as_string() == instance.id,
        Task.data['perception_signal']['basis']['observation_id'].as_string().in_(family)).limit(1))
    if existing: fail('dual_already_handed_off', '此原登记已有人工任务，未重新安排双路')
    pid = str(uuid4()); clock = now()
    immutable = dict(schema=PARENT,id=pid,workspace_id=instance.workspace_id,instance_id=instance.id,release_id=grant['release_id'],
        signal_id=signal.id,event_id=event.id,observation_id=observation.id,route_id=grant['id'],source_identity_hash=identity,
        grant_hash=grant['grant_hash'],grant_actor_id=grant['actor_id'],basis_hash=digest(basis),input_hash=observation.data['input_hash'],
        registered_at=observation.created_at.isoformat(),created_at=clock.isoformat(),businesses=copy.deepcopy(grant['businesses']),
        bindings=copy.deepcopy(grant['bindings']),source=dict(source_id=observation.source_id,source_record_id=observation.source_record_id,
            entity_id=observation.entity_id,object_name=basis['object_name'],observation_id=observation.id,
            registered_at=observation.created_at.isoformat(),observed_at=observation.observed_at.isoformat() if observation.observed_at else None,
            source_time_verified=False))
    slots = {slot:dict(state='pending',task_id=None,receipt=None,ack=None,result=None) for slot in SLOTS}
    parent = Handoff(id=pid,workspace_id=instance.workspace_id,instance_id=instance.id,release_id=grant['release_id'],
        signal_id=signal.id,event_id=event.id,observation_id=observation.id,route_id=grant['id'],source_identity_hash=identity,
        data=bounded(dict(immutable=immutable,immutable_hash=digest(immutable),slots=slots,ack_requests=[])))
    db.add(parent); db.flush()
    entry['parent_ids'].append(pid); save_state(instance,value)
    signal.data = {**signal.data,'dual_handoff_id':pid}
    return parent


def lock_parent(db, p, parent_id):
    initial = scoped(db,Handoff,parent_id,p)
    instance = scoped(db,DomainInstance,initial.instance_id,p,True)
    signal = scoped(db,PerceptionSignal,initial.signal_id,p,True)
    parent = scoped(db,Handoff,parent_id,p,True); checked_parent(parent)
    return instance, signal, parent


def parent_for_task(db, p, task_id):
    return db.scalar(select(Handoff).where(Handoff.workspace_id == p.workspace_id,
        or_(Handoff.response_task_id == task_id,Handoff.verification_task_id == task_id)))


def prelock_task(db, p, task_id):
    parent = parent_for_task(db,p,task_id)
    if parent: lock_parent(db,p,parent.id)
    else:
        task = scoped(db,Task,task_id,p)
        if 'dual_handoff' in task.data: fail('dual_task_parent_missing')
    return parent


def authorize_mutation(db,p,task):
    parent = verify_task(db,p,task)
    if parent: single.actor(db,p.workspace_id,p.user_id)
    return parent


@controlled
def verify_task(db, p, task):
    parent = parent_for_task(db,p,task.id); marker = task.data.get('dual_handoff')
    if not parent:
        if 'dual_handoff' in task.data: fail('dual_task_parent_missing')
        return None
    return verify_task_binding(parent,task)


@controlled
def verify_task_binding(parent,task):
    """The original binding proof, shared by SQL writers and batch read views."""
    marker = task.data.get('dual_handoff')
    immutable = checked_parent(parent)
    if (not isinstance(marker,dict) or marker['schema'] != MARKER
            or set(marker) != {'schema','handoff_id','slot','immutable_hash','creation_hash','recipient','snapshot_hash'}
            or marker['snapshot_hash'] != digest({k:v for k,v in marker.items() if k != 'snapshot_hash'})
            or marker['handoff_id'] != parent.id or marker['slot'] not in SLOTS
            or getattr(parent,marker['slot']+'_task_id') != task.id
            or marker['immutable_hash'] != parent.data['immutable_hash'] or marker['creation_hash'] != task.data['creation_hash']
            or task.data['creation_request_id'] != creation_nonce(parent,marker['slot'])
            or marker['recipient'] != immutable['businesses'][marker['slot']]['recipient']
            or task.owner_id != immutable['grant_actor_id'] or task.data['instance_id'] != parent.instance_id
            or task.data['instance_release_id'] != parent.release_id
            or task.data['business_id'] != immutable['businesses'][marker['slot']]['id']
            or task.data['perception_signal']['signal_id'] != parent.signal_id
            or task.data['perception_signal']['basis_hash'] != immutable['basis_hash']): fail('dual_task_binding_invalid')
    if marker['slot'] == 'response':
        check_ack_requests(parent)
        if task.data.get('dual_response_ack') != parent.data['slots']['response']['ack']: fail('dual_ack_invalid')
    return parent


def task_snapshot(db, p, parent, slot, body, instance, release):
    from .perception_signals import checked
    immutable = checked_parent(parent)
    if (slot not in SLOTS or parent.workspace_id != p.workspace_id or parent.instance_id != instance.id
            or parent.release_id != release.id or body.request_id != creation_nonce(parent,slot)
            or parent.data['slots'][slot]['state'] != 'pending' or getattr(parent,slot+'_task_id')
            or body.business_id != immutable['businesses'][slot]['id'] or body.entity_ids != [immutable['source']['entity_id']]):
        fail('dual_creation_binding_invalid')
    signal = scoped(db,PerceptionSignal,parent.signal_id,p)
    check_version(signal,body.signal_version)
    basis = checked(db,p,signal,allow_rejected=False)
    if digest(basis) != immutable['basis_hash'] or body.signal_id != signal.id or body.signal_basis_hash != immutable['basis_hash']: fail('dual_creation_binding_invalid')
    return {'signal_id':signal.id,'basis_hash':immutable['basis_hash'],'basis':copy.deepcopy(basis),'note':signal.data.get('note','')}


def attach_task(db, p, task, parent, slot):
    immutable = checked_parent(parent)
    marker = sealed(dict(schema=MARKER,handoff_id=parent.id,slot=slot,immutable_hash=parent.data['immutable_hash'],
        creation_hash=task.data['creation_hash'],recipient=copy.deepcopy(immutable['businesses'][slot]['recipient'])), 'snapshot_hash')
    task.data = {**task.data,'dual_handoff':marker}
    # The SQL pointer is assigned in the same transaction, never inferred from
    # a client-supplied marker. Receipt is recorded by dispatch_slot.
    setattr(parent,slot+'_task_id',task.id)
    data = copy.deepcopy(parent.data); data['slots'][slot].update(state='created',task_id=task.id)
    parent.data = data
    signal = scoped(db,PerceptionSignal,parent.signal_id,p)
    signal.data = {**signal.data,'task_ids':[*signal.data.get('task_ids',[]),task.id]}; signal.version += 1; signal.status = 'handed_off'


@controlled
def dispatch_slot(db, workspace_id, parent_id, slot):
    if slot not in SLOTS: fail('dual_slot_invalid')
    system = Principal('dual-dispatch',workspace_id,'viewer','dual-dispatch')
    instance, signal, parent = lock_parent(db,system,parent_id)
    if parent.data['slots'][slot]['state'] != 'pending': return parent
    status,code,message,tid = 'failed','dual_integrity','本路交接依据无法核对，原异常保留，不自动重试',None
    try:
        with db.begin_nested():
            immutable = checked_parent(parent); value = state(instance)
            entry = next(r for r in value['routes'] if r['grant']['id'] == parent.route_id); grant = entry['grant']
            if grant['grant_hash'] != immutable['grant_hash'] or grant['release_id'] != parent.release_id: fail('dual_grant_changed')
            # A reservation may exhaust the route; it remains valid for its
            # own two pending slots, but pause/expiry never get ignored.
            if entry['paused_at'] or now() >= single.stamp(grant['expires_at']): fail('dual_not_active')
            if parent.id not in entry['parent_ids']: fail('dual_reservation_missing')
            p = single.actor(db,workspace_id,grant['actor_id'])
            if p.role != grant['actor_role']: fail('dual_actor_changed')
            observation = scoped(db,Observation,parent.observation_id,p,True)
            from .perception_signals import checked, guard_rule
            basis = checked(db,p,signal,allow_rejected=False); event = scoped(db,Event,parent.event_id,p)
            if (digest(basis) != immutable['basis_hash'] or observation.data['input_hash'] != immutable['input_hash']
                    or single.source_identity(observation) != parent.source_identity_hash
                    or not single.registration_matches(db,grant,event,observation,single.stamp(immutable['created_at']))):
                fail('dual_original_changed')
            single.lock_selection(db,p,grant['source_id'],grant['extractor_id'],grant['entity_ids'])
            binding = single.prepare(db,p,instance,**{k:grant[k] for k in ('release_id','source_id','extractor_id','entity_ids')},
                business_id=grant[slot+'_business_id'])
            if binding != immutable['bindings'][slot]: fail('dual_binding_changed')
            guard_rule(db,p,observation,result=basis['extracted'])
            from .workspace_api import NewTask, create_task
            body = NewTask(release_id=parent.release_id,business_id=grant[slot+'_business_id'],entity_ids=[signal.entity_id],
                title=('人工响应：' if slot == 'response' else '异常核验：')+basis['object_name'][:220],
                request_id=creation_nonce(parent,slot),signal_id=signal.id,signal_version=signal.version,signal_basis_hash=immutable['basis_hash'])
            task = create_task(instance.id,body,p,db,dual_handoff=(parent,slot))
            if getattr(parent,slot+'_task_id') != task.id: fail('dual_creation_binding_invalid')
            tid = task.id; status,code,message = 'created','human_task_created','人工待办已保存到责任人工作区，尚未确认收到'
    except HTTPException as error:
        tid = None; status = 'blocked'
        code = error.detail.get('code','dual_authority_or_source_changed') if isinstance(error.detail,dict) else 'dual_authority_or_source_changed'
        message = '本路的授权、负责人、业务或原依据已变化；原响应与另一任务保留，本路不自动重试'
    data = copy.deepcopy(parent.data)
    receipt = sealed(dict(schema='signal-dual-slot-receipt-v1',handoff_id=parent.id,slot=slot,
        immutable_hash=data['immutable_hash'],status=status,code=code,message=message,task_id=tid,
        request_id=creation_nonce(parent,slot),created_at=now().isoformat()), 'receipt_hash')
    data['slots'][slot].update(state=status,task_id=tid,receipt=receipt)
    parent.data = bounded(data); parent.version += 1
    # Nested rollback expires both SQL pointer and signal attachment; never
    # retain a Python ghost Task after any failed create/receipt transaction.
    setattr(parent,slot+'_task_id',tid)
    audit(db,Principal(parent.data['immutable']['grant_actor_id'],workspace_id,'viewer','dual-dispatch'),
        'signal_dual.delivery',parent.id,{'slot':slot,'status':status,'task_id':tid,'receipt_hash':receipt['receipt_hash']})
    return parent


def deliver_pending(workspace_id):
    from .db import engine
    # These are independent transactions by design. Do not wrap this loop in a
    # shared Session: a verification failure must never roll back response.
    with Session(engine()) as db:
        ids = list(db.scalars(select(Handoff.id).where(Handoff.workspace_id == workspace_id,
            Handoff.data['dispatch_integrity_failure'].as_string().is_(None),
            or_(Handoff.data['slots']['response']['state'].as_string() == 'pending',
                Handoff.data['slots']['verification']['state'].as_string() == 'pending'))
            .order_by(Handoff.created_at,Handoff.id).limit(100)))
    for pid in ids:
        for slot in SLOTS:
            with Session(engine()) as db:
                try:
                    dispatch_slot(db,workspace_id,pid,slot); db.commit()
                except HTTPException:
                    db.rollback()
                    # Do not repair/reseal a damaged immutable record. Keep a
                    # separate visible quarantine and continue other parents.
                    p = Principal('dual-quarantine',workspace_id,'viewer','dual-quarantine')
                    _,signal,parent = lock_parent_unchecked(db,p,pid)
                    parent.data = {**parent.data,'dispatch_integrity_failure':{'code':'dual_parent_integrity','message':'此交接完整性无法核对，已隔离；未重新派送'}}
                    parent.version += 1
                    author = single.receipt_actor(db,signal)
                    if author:
                        audit(db,Principal(author.id,workspace_id,'viewer','dual-quarantine'),'signal_dual.quarantined',parent.id,
                            {'code':'dual_parent_integrity','slot':slot})
                    db.commit()
                    break


def lock_parent_unchecked(db,p,parent_id):
    initial = scoped(db,Handoff,parent_id,p)
    instance = scoped(db,DomainInstance,initial.instance_id,p,True)
    signal = scoped(db,PerceptionSignal,initial.signal_id,p,True)
    return instance,signal,scoped(db,Handoff,parent_id,p,True)


@controlled
def check_ack_requests(parent):
    checked_parent(parent); seen = []
    for entry in parent.data['ack_requests']:
        body, receipt = entry['body'], entry['receipt']; seen.append(body['request_id'])
        if (set(body) != {'request_id','expected_task_version','parent_hash','note','override_reason'}
                or type(body['expected_task_version']) is not int or body['expected_task_version'] < 1
                or type(body['note']) is not str or not body['note'].strip() or len(body['note']) > 5000
                or type(body['override_reason']) is not str or len(body['override_reason']) > 500
                or '\x00' in body['note'] or '\x00' in body['override_reason']): fail('dual_ack_invalid')
        body['note'].encode('utf-8'); body['override_reason'].encode('utf-8')
        signature = digest(dict(schema='signal-dual-ack-request-v1',workspace_id=parent.workspace_id,actor_id=receipt['actor_id'],
            handoff_id=parent.id,task_id=parent.response_task_id,body=body))
        if (receipt['receipt_hash'] != digest({k:v for k,v in receipt.items() if k != 'receipt_hash'})
                or receipt['request_hash'] != signature or receipt['request_id'] != body['request_id']
                or receipt['handoff_id'] != parent.id or receipt['task_id'] != parent.response_task_id
                or receipt['parent_hash'] != parent.data['immutable_hash'] or receipt['workspace_id'] != parent.workspace_id
                or receipt['schema'] != 'signal-dual-ack-receipt-v1' or receipt['ack_actor_id'] != receipt['actor_id']
                or receipt['override_reason'] != body['override_reason'] or body['parent_hash'] != receipt['parent_hash']
                or receipt['accepted_task_version'] != body['expected_task_version']+1): fail('dual_ack_invalid')
    if len(seen) != len(set(seen)): fail('dual_ack_invalid')
    ack = parent.data['slots']['response']['ack']
    if ack is None:
        if parent.data['ack_requests']: fail('dual_ack_invalid')
    else:
        if len(parent.data['ack_requests']) != 1: fail('dual_ack_invalid')
        entry = parent.data['ack_requests'][0]; body,receipt = entry['body'],entry['receipt']
        expected = dict(request_id=body['request_id'],actor_id=receipt['actor_id'],at=receipt['ack_at'],note=body['note'],
            override_reason=body['override_reason'],receipt_hash=receipt['receipt_hash'],
            by_recipient=receipt['actor_id'] == parent.data['immutable']['businesses']['response']['recipient']['user_id'])
        if ack != expected: fail('dual_ack_invalid')


def ack_permissions(db,p,parent):
    immutable = checked_parent(parent); rec = immutable['businesses']['response']['recipient']
    task = None
    if parent.response_task_id:
        try:
            candidate = scoped(db,Task,parent.response_task_id,p); verify_task(db,p,candidate); task = candidate
        except HTTPException as error:
            if error.status_code not in {404,409}: raise
    live_recipient = db.scalar(select(User.id).join(Membership,Membership.user_id == User.id).where(
        User.id == rec['user_id'],User.active.is_(True),Membership.workspace_id == p.workspace_id,Membership.role.in_(['admin','operator'])))
    allowed = bool(live_recipient and task and task.status == 'workflow_waiting' and not parent.data['slots']['response']['ack']
        and p.role in {'admin','operator'} and (p.user_id == rec['user_id'] or p.role == 'admin'))
    return dict(can_ack_response=allowed,ack_requires_override=bool(allowed and p.user_id != rec['user_id']),
        ack_blocking_reason=None if allowed else '尚未创建可处理响应任务、已接收，或当前账号不是责任人/管理员')


@controlled
def parent_view(db,p,parent):
    immutable = checked_parent(parent); slots = {}
    check_ack_requests(parent)
    for slot in SLOTS:
        entry = parent.data['slots'][slot]; task = None
        issue = None
        if entry['task_id']:
            try:
                task = scoped(db,Task,entry['task_id'],p); verify_task(db,p,task)
            except HTTPException as error:
                if error.status_code not in {404,409}: raise
                issue = '该路任务完整性无法核对，禁止继续；另一任务与已存说明保留'
        slots[slot] = {**copy.deepcopy(entry),'task_version':task.version if task else None,
            'task_status':task.status if task else None,'recipient':copy.deepcopy(immutable['businesses'][slot]['recipient']),
            'business':{k:immutable['businesses'][slot][k] for k in ('id','name')},
            'integrity_issue':issue,'can_open_task':bool(task and not issue)}
    event = scoped(db,Event,parent.event_id,p); receipt = slots['response']['receipt']
    latency = max(0,int((single.stamp(receipt['created_at'])-event.received_at).total_seconds()*1000)) if receipt and receipt['status'] == 'created' and event.received_at else None
    return sealed(dict(schema='signal-dual-handoff-v1',id=parent.id,version=parent.version,workspace_id=parent.workspace_id,
        instance_id=parent.instance_id,release_id=parent.release_id,signal_id=parent.signal_id,immutable_hash=parent.data['immutable_hash'],
        grant_actor_id=immutable['grant_actor_id'],source=copy.deepcopy(immutable['source']),slots=slots,
        response_latency_ms=latency,response_delayed=bool(latency is not None and latency > 10000),**ack_permissions(db,p,parent),
        device_actions=False,cloud_calls=0,entity_state_updated=False,external_notification_sent=False), 'view_hash')


def ack_reply(db,p,parent,receipt):
    task = scoped(db,Task,parent.response_task_id,p)
    return dict(schema='signal-dual-ack-response-v1',handoff=parent_view(db,p,parent),receipt=copy.deepcopy(receipt),
        current_matches=task.version == receipt['accepted_task_version'])


def ack_request(db,p,parent,request_id):
    check_ack_requests(parent)
    entry = next((x for x in parent.data['ack_requests'] if x['receipt']['request_id'] == request_id),None)
    if not entry or entry['receipt']['actor_id'] != p.user_id: raise HTTPException(404,'没有本人已保存的响应接收请求')
    return ack_reply(db,p,parent,entry['receipt'])


@controlled
def acknowledge(db,p,parent_id,body):
    _,_,parent = lock_parent(db,p,parent_id)
    check_ack_requests(parent)
    signature = digest(dict(schema='signal-dual-ack-request-v1',workspace_id=p.workspace_id,actor_id=p.user_id,
        handoff_id=parent.id,task_id=parent.response_task_id,body=body))
    prior = next((x for x in parent.data['ack_requests'] if x['receipt']['request_id'] == body['request_id']),None)
    if prior:
        if prior['receipt']['request_hash'] != signature: fail('dual_nonce_conflict')
        return ack_reply(db,p,parent,prior['receipt'])
    require(p,'admin','operator')
    single.actor(db,p.workspace_id,p.user_id)
    if len(parent.data['ack_requests']) >= 100: fail('dual_capacity')
    if not parent.response_task_id: fail('dual_response_not_created')
    task = scoped(db,Task,parent.response_task_id,p,True); verify_task(db,p,task)
    if not ack_permissions(db,p,parent)['can_ack_response']: raise HTTPException(403,'请由已指定响应责任人或管理员明确接收')
    recipient_id = parent.data['immutable']['businesses']['response']['recipient']['user_id']
    single.actor(db,p.workspace_id,recipient_id)
    if p.user_id != recipient_id and not body['override_reason'].strip(): raise HTTPException(403,'管理员代为接收须填写原因')
    if body['parent_hash'] != parent.data['immutable_hash']: fail('dual_parent_changed')
    check_version(task,body['expected_task_version'])
    task.version += 1; clock = now().isoformat()
    receipt = sealed(dict(schema='signal-dual-ack-receipt-v1',request_id=body['request_id'],request_hash=signature,
        workspace_id=p.workspace_id,actor_id=p.user_id,handoff_id=parent.id,task_id=task.id,parent_hash=parent.data['immutable_hash'],
        accepted_task_version=task.version,ack_actor_id=p.user_id,ack_at=clock,override_reason=body['override_reason']), 'receipt_hash')
    ack = dict(request_id=body['request_id'],actor_id=p.user_id,at=clock,note=body['note'],override_reason=body['override_reason'],
        receipt_hash=receipt['receipt_hash'],by_recipient=p.user_id == recipient_id)
    data = copy.deepcopy(parent.data); data['slots']['response']['ack'] = ack
    data['ack_requests'].append({'body':copy.deepcopy(body),'receipt':receipt}); parent.data = bounded(data); parent.version += 1
    task.data = {**task.data,'dual_response_ack':copy.deepcopy(ack)}
    audit(db,p,'signal_dual.response_ack',parent.id,{'task_id':task.id,'request_id':body['request_id'],'receipt_hash':receipt['receipt_hash']})
    return ack_reply(db,p,parent,receipt)


def reject_response_verdict(parent, task, decision):
    if parent and task.data['dual_handoff']['slot'] == 'response' and decision is not None:
        raise HTTPException(422,'响应说明不能填写或代替异常核验判断')


def guard_completion(db,p,task,next_id,decision):
    parent = verify_task(db,p,task)
    if not parent:return
    reject_response_verdict(parent,task,decision)
    if next_id == 'end' and task.data['dual_handoff']['slot'] == 'response':
        check_ack_requests(parent)
        ack = parent.data['slots']['response']['ack']
        if not ack or task.data.get('dual_response_ack') != ack: fail('dual_response_ack_required','请先明确接收响应待办，再完成响应流程')


def finish_task(db,p,task,decision,note):
    parent = verify_task(db,p,task)
    if not parent: return False
    slot = task.data['dual_handoff']['slot']; data = copy.deepcopy(parent.data)
    reject_response_verdict(parent,task,decision)
    if slot == 'response':
        check_ack_requests(parent)
        ack = data['slots']['response']['ack']
        if not ack or task.data.get('dual_response_ack') != ack: fail('dual_response_ack_required','请先明确接收响应待办，再完成响应流程')
    else:
        if decision not in {'abnormal','not_abnormal','uncertain'}: raise HTTPException(422,'请明确选择核验结果，证据不足时选择尚不能判断')
        data['slots']['verification']['result'] = dict(decision=decision,note=note,actor_id=p.user_id,at=now().isoformat(),
            source_time_verified=False,entity_state_updated=False,device_action_authorized=False)
    data['slots'][slot]['state'] = 'completed'; parent.data = bounded(data); parent.version += 1
    return True


def task_cancelled(db,p,task):
    parent = verify_task(db,p,task)
    if not parent: return
    data = copy.deepcopy(parent.data); data['slots'][task.data['dual_handoff']['slot']]['state'] = 'cancelled'
    parent.data = bounded(data); parent.version += 1


@controlled
def listing(db,p,instance_id,limit=20,after=None,mine_only=True):
    scoped(db,DomainInstance,instance_id,p)
    scope = dict(workspace_id=p.workspace_id,instance_id=instance_id,actor_id=p.user_id,mine_only=mine_only)
    frozen = {**scope,'upper':now().isoformat(),'last_time':None,'last_id':None}
    if after:
        value = json.loads(base64.urlsafe_b64decode(after+'='*(-len(after)%4)))
        plain = {k:v for k,v in value.items() if k != 'hash'}
        if value['hash'] != digest(plain) or any(plain[k] != v for k,v in scope.items()): fail('dual_cursor_invalid')
        frozen = plain
    query = select(Handoff).where(Handoff.workspace_id == p.workspace_id,Handoff.instance_id == instance_id,
        Handoff.created_at <= single.stamp(frozen['upper']))
    if mine_only:
        query = query.where(or_(Handoff.data['immutable']['businesses']['response']['recipient']['user_id'].as_string() == p.user_id,
            Handoff.data['immutable']['businesses']['verification']['recipient']['user_id'].as_string() == p.user_id))
    if frozen['last_id']:
        previous_time = single.stamp(frozen['last_time'])
        query = query.where(or_(Handoff.created_at > previous_time,(Handoff.created_at == previous_time) & (Handoff.id > frozen['last_id'])))
    rows = list(db.scalars(query.order_by(Handoff.created_at,Handoff.id).limit(limit+1)))
    cursor = None
    if len(rows) > limit:
        last = rows[limit-1]; cursor_value = {**frozen,'last_time':last.created_at.isoformat(),'last_id':last.id}
        cursor = base64.urlsafe_b64encode(json.dumps(sealed(cursor_value,'hash'),sort_keys=True).encode()).decode().rstrip('=')
    items, failures = [], []
    for row in rows[:limit]:
        try:
            items.append(parent_view(db,p,row))
        except HTTPException as error:
            if error.status_code != 409: raise
            # This is a separate SQL-scoped failure summary, not a replacement
            # or newly sealed immutable parent. The raw row/cursor is retained.
            failures.append({'id':row.id,'instance_id':row.instance_id,'code':'dual_parent_integrity',
                'message':'此双路交接完整性无法核对，已隔离；原记录保留'})
    return dict(schema='signal-dual-handoff-list-v1',instance_id=instance_id,items=items,next_cursor=cursor,integrity_failures=failures)


def run_worker(workspace_id):
    """A scoped lightweight loop. No call to claim/process_once/Job runners."""
    from .worker import _workspace_uuid, deliver_events
    from .db import engine
    workspace_id = _workspace_uuid(workspace_id); stop = StopEvent(); previous = {}
    with Session(engine()) as db:
        if db.get(Workspace,workspace_id) is None:
            print('工作区不存在；未启动双路交接或领取任何任务',flush=True); return 1
    print('双路人工交接处理器已启动；仅工作区 '+workspace_id,flush=True)
    for signum in (os_signal.SIGINT,os_signal.SIGTERM):
        previous[signum] = os_signal.getsignal(signum); os_signal.signal(signum,lambda *_:stop.set())
    try:
        while not stop.is_set():
            # Existing registered-rule reception only, with the same event
            # family as perception_signals.deliver_pending; no arbitrary queue.
            with Session(engine()) as db:
                rows = list(db.execute(select(Observation.id).join(Event,Event.data['observation_id'].as_string() == Observation.id)
                    .join(DomainInstance,DomainInstance.id == Observation.data['reported_rule_contract']['instance_id'].as_string())
                    .where(Observation.workspace_id == workspace_id,Event.workspace_id == workspace_id,
                        DomainInstance.workspace_id == workspace_id,DomainInstance.data['signal_dual_routes'].is_not(None),
                        Event.status == 'pending_delivery',Event.kind.in_(['observation.data','observation.failed','perception.anomaly']),
                        Observation.data['reported_rule_contract']['schema'].as_string() == 'local-reported-rule-v1')
                    .order_by(Observation.created_at).limit(100)))
            for (identity,) in rows:
                if stop.is_set(): break
                deliver_events(workspace_id=workspace_id,observation_id=identity)
            if not stop.is_set(): deliver_pending(workspace_id)
            stop.wait(0.5)
        return 0
    except Exception as error:
        print('双路人工交接处理器已停止；类别='+type(error).__name__+'；原记录保留，未确认继续派送',flush=True)
        return 1
    finally:
        for signum,handler in previous.items(): os_signal.signal(signum,handler)
        print('双路人工交接处理器已退出；持久任务和已存人工记录保留',flush=True)
