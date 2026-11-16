"""Bounded, proven inbox discovery; never an authority to mutate a workflow.

All scope records are proved before actor/mode filtering. Task notes, media and
historical evidence are excluded from the batch projection. Only the requested
page renders the unchanged parent-v1 view in the caller's read-only snapshot.
"""
import base64
import binascii
import hashlib
import hmac
import json
from collections import Counter
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from .contracts import digest
from .models import Audit, AuthSession, DomainInstance, InstanceRelease, Task, User, now
from .services import scoped
from .signal_dual_models import SignalDualHandoff as Handoff
from . import signal_dual_handoffs as dual

SCHEMA = 'signal-dual-handoff-inbox-v2'
CURSOR = 'signal-dual-inbox-cursor-v2'
MAX_PARENTS = 5000
HISTORY = ('assigned_before','acknowledged','step_completed','note_saved','cancelled')
AUDIT_ACTIONS = ('workflow.note_saved','workflow.step_completed','workflow.cancelled')


def fail(code='dual_inbox_integrity', message='待办来源或步骤完整性无法核对，原记录保留'):
    raise HTTPException(409,{'code':code,'message':message})


def failure(row):
    return dict(id=row.id,instance_id=row.instance_id,code='dual_inbox_integrity',
        message='此交接或任务的来源、步骤完整性无法核对；原记录保留，请核对详情')


def cursor_key(db,p):
    value = db.scalar(select(AuthSession.token_hash).where(AuthSession.id == p.session_id,
        AuthSession.user_id == p.user_id,AuthSession.revoked.is_(False),AuthSession.expires_at > now()))
    if type(value) is not str or len(value) != 64:
        raise HTTPException(401,'会话已失效，请重新登录后读取待办')
    try:
        key = bytes.fromhex(value)
        if len(key) != 32 or key.hex() != value: raise ValueError('invalid session digest')
        return key
    except ValueError: raise HTTPException(401,'会话已失效，请重新登录后读取待办') from None


def cursor_mac(value,key):
    body = json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return hmac.new(key,body,hashlib.sha256).hexdigest()


def unique_object(pairs):
    value = {}
    for key,item in pairs:
        if key in value: raise ValueError('duplicate cursor key')
        value[key] = item
    return value


def cursor(scope,after,key):
    frozen = {**scope,'upper':now().isoformat(),'last_time':None,'last_id':None}
    if after is None: return frozen
    try:
        if type(after) is not str or not after or len(after) > 4096: fail('dual_cursor_invalid')
        value = json.loads(base64.b64decode(after+'='*(-len(after)%4),altchars=b'-_',validate=True),
            object_pairs_hook=unique_object,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid cursor number')))
        if type(value) is not dict: fail('dual_cursor_invalid')
        plain = {k:v for k,v in value.items() if k != 'hash'}
        if (set(value) != set(frozen) | {'hash'} or type(value['hash']) is not str
                or not hmac.compare_digest(value['hash'],cursor_mac(plain,key))
                or any(type(plain[k]) is not type(v) or plain[k] != v for k,v in scope.items())
                or type(plain['upper']) is not str or type(plain['last_time']) is not str
                or type(plain['last_id']) is not str or str(UUID(plain['last_id'])) != plain['last_id']
                or dual.single.stamp(plain['last_time']) > dual.single.stamp(plain['upper'])
                or dual.single.stamp(plain['upper']) > now()): fail('dual_cursor_invalid')
        return plain
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError,binascii.Error,UnicodeError,RecursionError):
        fail('dual_cursor_invalid','分页范围已改变，请从待办首页重新读取')


def encode_cursor(frozen,row,key):
    value = {**frozen,'last_time':row.created_at.isoformat(),'last_id':row.id}
    return base64.urlsafe_b64encode(json.dumps({**value,'hash':cursor_mac(value,key)},sort_keys=True).encode()).decode().rstrip('=')


def _json(value):
    return cast(literal(value),JSONB)


def task_projection(db,p,identities):
    """One SQL projection: proof inputs and actors, without notes/media blobs."""
    if not identities: return {}
    runtime = Task.data['workflow_runtime']; receipts = runtime['receipts']; drafts = runtime['drafts']
    safe_receipts = case((func.jsonb_typeof(receipts) == 'array',receipts),else_=_json('[]'))
    safe_receipts = case((func.jsonb_array_length(safe_receipts) <= 100,safe_receipts),else_=_json('[]'))
    elements = func.jsonb_array_elements(safe_receipts).table_valued('value',with_ordinality='ordinality').alias('inbox_receipt')
    item = cast(elements.c.value,JSONB)
    receipt_values = select(func.coalesce(func.jsonb_agg(aggregate_order_by(func.jsonb_build_object(
        'step_id',item['step_id'],'role',item['role'],'actor',item['actor'],'branch',item['branch']),elements.c.ordinality)),_json('[]'))
        ).select_from(elements).correlate(Task).scalar_subquery()
    safe_drafts = case((func.jsonb_typeof(drafts) == 'object',drafts),else_=_json('{}'))
    entries = func.jsonb_each(safe_drafts).table_valued('key','value').alias('inbox_draft')
    draft = cast(entries.c.value,JSONB)
    draft_values = select(func.coalesce(func.jsonb_object_agg(entries.c.key,
        func.jsonb_build_object('actor',draft['actor'])),_json('{}'))).select_from(entries).correlate(Task).scalar_subquery()
    fields = ('dual_handoff','creation_hash','creation_request_id','instance_id',
              'instance_release_id','business_id','dual_response_ack')
    pairs = [part for key in fields for part in (key,Task.data[key])]
    data = func.jsonb_build_object(*pairs,'perception_signal',func.jsonb_build_object(
        'signal_id',Task.data['perception_signal']['signal_id'],'basis_hash',Task.data['perception_signal']['basis_hash']),
        'workflow_runtime',func.jsonb_build_object('current_step_id',runtime['current_step_id'],
            'receipts',receipt_values,'drafts',draft_values)).label('data')
    shape = ((func.jsonb_typeof(runtime) == 'object') & (func.jsonb_typeof(receipts) == 'array')
        & (func.jsonb_array_length(safe_receipts) == func.jsonb_array_length(case(
            (func.jsonb_typeof(receipts) == 'array',receipts),else_=_json('[]'))))
        & (func.jsonb_typeof(drafts) == 'object')).label('runtime_shape')
    rows = db.execute(select(Task.id,Task.workspace_id,Task.owner_id,Task.package_id,Task.status,Task.version,data,shape)
        .where(Task.workspace_id == p.workspace_id,Task.id.in_(identities)).limit(MAX_PARENTS*2+1))
    return {r.id:SimpleNamespace(**dict(r._mapping)) for r in rows}


def batches(db,p,parents):
    ids = {getattr(row,slot+'_task_id') for row in parents for slot in dual.SLOTS} - {None}
    tasks = task_projection(db,p,ids)
    release_ids = {row.release_id for row in parents}
    releases = {row.id:row for row in db.scalars(select(InstanceRelease).where(
        InstanceRelease.workspace_id == p.workspace_id,InstanceRelease.id.in_(release_ids)))} if release_ids else {}
    user_ids = set()
    for release in releases.values():
        snapshot = release.data.get('snapshot') if isinstance(release.data,dict) else None
        roles = snapshot.get('roles') if isinstance(snapshot,dict) else None
        if isinstance(roles,dict):
            user_ids.update(value.get('user_id') for value in roles.values() if isinstance(value,dict)
                            and isinstance(value.get('user_id'),str))
    users = {row.id:row for row in db.execute(select(User.id,User.username).where(User.id.in_(user_ids)))} if user_ids else {}
    activity = {}
    if ids:
        # Group repeated note saves; fetch only actor/action/step, never notes.
        step = Audit.data['step'].as_string(); target = Audit.data['next'].as_string()
        fields = (Audit.object_id,Audit.action,Audit.actor_id,step,target)
        rows = db.execute(select(*fields,func.count(Audit.id).label('count')).where(
            Audit.workspace_id == p.workspace_id,Audit.object_id.in_(ids),Audit.action.in_(AUDIT_ACTIONS)).group_by(*fields))
        for row in rows:
            record = activity.setdefault(row.object_id,dict(events=set(),steps=Counter(),notes=set(),cancelled=set()))
            record['events'].add((row.action,row.actor_id == p.user_id))
            if row.action == 'workflow.step_completed': record['steps'][(row.actor_id,row[3],row[4])] += row._mapping['count']
            elif row.action == 'workflow.note_saved': record['notes'].add(row.actor_id)
            else: record['cancelled'].add(row.actor_id)
    return tasks,releases,users,activity


def account(role,roles,users):
    binding = roles[role]
    if not isinstance(binding,dict) or set(binding) != {'user_id','username'}: fail()
    user = users.get(binding['user_id'])
    if not user or type(binding['username']) is not str or not binding['username']: fail()
    return dict(role=role,user_id=user.id,username=user.username)


def next_step(steps,index,branch):
    step = steps[index]
    if branch is not None and type(branch) is not bool: fail()
    if bool(step.get('condition','').strip()) != (branch is not None): fail()
    value = step.get('elseId','end') if branch is False else step.get('nextId','auto')
    return (steps[index+1]['id'] if index+1 < len(steps) else 'end') if value == 'auto' else value


@dual.controlled
def assignment(p,parent,slot,task,release,users,activity):
    immutable = dual.checked_parent(parent); entry = parent.data['slots'][slot]
    if (not release or release.instance_id != parent.instance_id or release.package_id != (task.package_id if task else release.package_id)
            or release.workspace_id != p.workspace_id or release.status != 'prepared'): fail()
    snapshot = release.data['snapshot']
    if digest(snapshot) != release.configuration_hash: fail()
    business = next(b for b in snapshot['businesses'] if b['id'] == immutable['businesses'][slot]['id'])
    steps = business['flow']['steps']; roles = snapshot['roles']
    if not steps or any(step['agent'] != '人工确认' for step in steps): fail()
    if len({step['id'] for step in steps}) != len(steps): fail()
    selected_roles = {s['role']:roles[s['role']] for s in steps}
    if digest({'business':business,'roles':selected_roles}) != immutable['businesses'][slot]['binding_hash']: fail()
    first = immutable['businesses'][slot]['recipient']
    if first != {'role':steps[0]['role'],**roles[steps[0]['role']]}: fail()
    recipient = account(first['role'],roles,users)
    actions, reasons = [], set(); current = assignee = None; historical = False
    if task is None:
        if entry['task_id'] is not None: fail()
        kind = 'dispatch_pending' if entry['state'] == 'pending' else 'blocked'
        if entry['state'] not in {'pending','blocked','failed'}: fail()
        actions.append(dict(kind=kind,assignee=recipient))
    else:
        dual.verify_task_binding(parent,task)
        if task.runtime_shape is not True or type(task.version) is not int or task.version < 1: fail()
        expected_status = {'created':'workflow_waiting','completed':'workflow_completed','cancelled':'workflow_cancelled'}
        if task.status != expected_status.get(entry['state']): fail()
        runtime = task.data['workflow_runtime']; step_map = {step['id']:(i,step) for i,step in enumerate(steps)}
        expected = steps[0]['id']; completed = Counter()
        for receipt in runtime['receipts']:
            if receipt['step_id'] != expected: fail()
            index,step = step_map[expected]
            if receipt['role'] != step['role'] or type(receipt['actor']) is not str: fail()
            previous_assignee = account(step['role'],roles,users)
            if previous_assignee['user_id'] == p.user_id: reasons.add('assigned_before')
            if receipt['actor'] == p.user_id: reasons.add('step_completed')
            expected = next_step(steps,index,receipt['branch']); historical = True
            completed[(receipt['actor'],step['id'],expected)] += 1
        saved_activity = activity.get(task.id,dict(events=set(),steps=Counter(),notes=set(),cancelled=set()))
        if completed != saved_activity['steps']: fail()
        if bool(saved_activity['cancelled']) != (task.status == 'workflow_cancelled'): fail()
        expected_id = None if expected == 'end' else expected
        if runtime['current_step_id'] != expected_id: fail()
        if task.status == 'workflow_completed' and expected_id is not None: fail()
        if task.status == 'workflow_waiting' and expected_id is None: fail()
        if expected_id is not None:
            step = step_map[expected_id][1]; current = {k:step[k] for k in ('id','name')}
            assignee = account(step['role'],roles,users)
            if task.status == 'workflow_waiting': actions.append(dict(kind='step',assignee=assignee))
            elif assignee['user_id'] == p.user_id: reasons.add('assigned_before')
        for step_id,draft in runtime['drafts'].items():
            if (step_id not in step_map or type(draft['actor']) is not str
                    or draft['actor'] not in saved_activity['notes']): fail()
            historical = True
            if draft['actor'] == p.user_id: reasons.add('note_saved')
        if task.status in {'workflow_completed','workflow_cancelled'}: historical = True
        if slot == 'response' and entry['ack'] is not None:
            historical = True
            if entry['ack']['actor_id'] == p.user_id: reasons.add('acknowledged')
        if slot == 'response' and entry['ack'] is None and task.status == 'workflow_waiting':
            actions.insert(0,dict(kind='ack',assignee=recipient))
        for action,mine in saved_activity['events']:
            historical = True
            if mine: reasons.add({'workflow.note_saved':'note_saved','workflow.step_completed':'step_completed',
                                  'workflow.cancelled':'cancelled'}[action])
    if reasons: historical = True
    view = dict(task_id=task.id if task else None,task_version=task.version if task else None,
        current_step=current,current_assignee=assignee,actions=actions,
        history_reasons=[reason for reason in HISTORY if reason in reasons])
    pending_mine = any(action['assignee']['user_id'] == p.user_id for action in actions)
    return view,dict(pending=bool(actions),pending_mine=pending_mine,history=historical,history_mine=bool(reasons))


@dual.controlled
def prove(p,parent,tasks,releases,users,activity):
    dual.checked_parent(parent); dual.check_ack_requests(parent)
    assignments, matches = {}, {}
    for slot in dual.SLOTS:
        identity = getattr(parent,slot+'_task_id'); task = tasks.get(identity) if identity else None
        if identity and task is None: fail()
        assignments[slot],matches[slot] = assignment(p,parent,slot,task,releases.get(parent.release_id),users,activity)
    return assignments,matches


def listing(db,p,instance_id,limit=20,after=None,mine_only=True,mode='pending'):
    scoped(db,DomainInstance,instance_id,p)
    if type(limit) is not int or not 1 <= limit <= 100 or type(mine_only) is not bool or mode not in {'pending','history'}:
        fail('dual_inbox_query_invalid')
    scope = dict(schema=CURSOR,workspace_id=p.workspace_id,instance_id=instance_id,actor_id=p.user_id,
        mode=mode,mine_only=mine_only,sort='created_desc')
    signing_key = cursor_key(db,p)
    frozen = cursor(scope,after,signing_key)
    parents = list(db.scalars(select(Handoff).where(Handoff.workspace_id == p.workspace_id,
        Handoff.instance_id == instance_id,Handoff.created_at <= dual.single.stamp(frozen['upper']))
        .order_by(Handoff.created_at.desc(),Handoff.id.desc()).limit(MAX_PARENTS+1)))
    if len(parents) > MAX_PARENTS:
        fail('dual_inbox_capacity','交接记录超过本次可完整核对范围，请联系管理员；不能据此判断没有待办')
    tasks,releases,users,activity = batches(db,p,parents)
    selected = []
    boundary = (dual.single.stamp(frozen['last_time']),frozen['last_id']) if frozen['last_id'] else None
    for row in parents:
        issue = None; assignments = matches = None
        try: assignments,matches = prove(p,row,tasks,releases,users,activity)
        except HTTPException as error:
            if error.status_code != 409: raise
            issue = failure(row)
        except (RecursionError,UnicodeError):
            issue = failure(row)
        match_key = mode+('_mine' if mine_only else '')
        slots = [] if issue else [slot for slot in dual.SLOTS if matches[slot][match_key]]
        # A damaged record is never hidden by its untrusted actor/status fields.
        if (issue or slots) and (boundary is None or (row.created_at,row.id) < boundary):
            selected.append((row,issue,assignments,slots))
    page = selected[:limit]; items,failures = [],[]
    for row,issue,assignments,slots in page:
        if issue: failures.append(issue); continue
        try: handoff = dual.parent_view(db,p,row)
        except HTTPException as error:
            if error.status_code != 409: raise
            failures.append(failure(row)); continue
        items.append(dict(handoff=handoff,matched_slots=slots,current_assignments=assignments))
    after_value = encode_cursor(frozen,page[-1][0],signing_key) if len(selected) > limit else None
    return dict(schema=SCHEMA,instance_id=instance_id,mode=mode,mine_only=mine_only,
        snapshot_upper=frozen['upper'],sort='created_desc',items=items,next_cursor=after_value,integrity_failures=failures)
