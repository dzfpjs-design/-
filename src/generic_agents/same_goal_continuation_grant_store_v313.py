"""Task-scoped additive read-tool registration, not execution approval.

Only a new typed Plan and protected issue/revoke Audits are written. Original
Task, Goal, Q0, graph, continuation, paid answer and all jobs/calls stay intact.
Historical custody survives expiry; it never renews or reauthorizes a grant.
"""
import copy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import same_goal_continuation_grant_contracts_v313 as c
from . import same_goal_continuation_grant_sources_v313 as sources
from . import same_goal_continuation_store_v312 as drafts
from . import workspace_command_nonce_lock_v312 as command_nonce
from .contracts import digest
from .models import Audit, Plan, uid
from .relations import lock_current_actor
from .security import require
from .services import validate
from .temporal import instant

MAX_ROWS = 1000
MAX_GRANTS = 100
need, checked = drafts.need, drafts.checked
_ACTION = {'issue': c.ACTION_ISSUE, 'revoke': c.ACTION_REVOKE}
_MODEL = {'issue': c.IssueRequest, 'revoke': c.RevokeRequest}
_PLAN_FIELDS = {'schema', 'grant', 'operations'}
_GRANT_FIELDS = {'schema', 'grant_id', 'workspace_id', 'task_id', 'advice_plan_id', 'continuation_id',
    'issuer_id', 'issuer_role', 'request', 'request_hash', 'source', 'source_hash', 'basis',
    'issued_at', 'expires_at', 'record_hash', *c.FLAGS}
_COMMAND_FIELDS = {'schema', 'workspace_id', 'task_id', 'advice_plan_id', 'continuation_id', 'grant_id',
    'operation', 'actor_id', 'actor_role', 'request_id', 'request', 'body_hash', 'grant', 'grant_hash',
    'source_hash', 'issue_ref', 'version_before', 'version_after', 'phase', 'record_hash'}
_BASIS_FIELDS = {'schema', 'workspace_id', 'task_id', 'advice_plan_id', 'continuation_ref', 'source_hash',
    'goal', 'release', 'capability_pin', 'agent_role', 'input_schema_hash', 'output_schema_hash',
    'records_binding', 'scope', 'field', 'operation', 'evaluation', 'records', 'provenance',
    'inputs', 'input_hash', 'policy', 'actor_id', 'issued_at', 'expires_at', 'max_calls',
    'side_effect', 'external_allowed', 'cloud_allowed', 'simulation_only', 'test_only', 'record_hash'}


def _rows(db, p, model, *where, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, *where).limit(MAX_ROWS + 1)
    if lock: query = query.with_for_update()
    rows = list(db.scalars(query.execution_options(populate_existing=True)))
    need(len(rows) <= MAX_ROWS, '工具授权原件超过完整核对上限，历史保留，未新增登记')
    return rows


def _clock(db):
    at = db.scalar(select(func.clock_timestamp()))
    need(type(at) is datetime and at.tzinfo is not None)
    return at


def _time(value): return instant(value.isoformat() if isinstance(value, datetime) else value)
def _ref(event): return {'id': event.id, 'record_hash': digest(event.data)}


def _basis(raw, source, body, *, actor_id, task_id, advice_id, continuation_id, workspace_id):
    need(type(raw) is dict and set(raw) == _BASIS_FIELDS and raw['schema'] == c.BASIS
        and raw['record_hash'] == c.record_hash(raw) and raw['workspace_id'] == workspace_id
        and raw['task_id'] == task_id and raw['advice_plan_id'] == advice_id
        and raw['actor_id'] == actor_id and raw['source_hash'] == body['source_hash'] == digest(source))
    need(raw['continuation_ref'] == {'id': continuation_id, 'version': body['expected_continuation_version'],
        'revision_hash': body['revision_hash'], 'source_hash': body['source_hash']}
        and raw['capability_pin'] == body['capability_pin'] and raw['operation'] == body['operation']
        and raw['field']['key'] == body['field'] and raw['scope']['fields'] == [body['field']])
    need(raw['goal']['hash'] == source['goal_hash'] and raw['goal']['version'] == source['goal_version']
        and raw['goal']['snapshot_hash'] == source['goal_snapshot_hash']
        and raw['release']['id'] == source['release_id']
        and raw['release']['original_graph_hash'] == source['original_graph_hash']
        and raw['policy']['ref'] == body['policy_ref']
        and raw['policy']['subject_kind'] == c.SUBJECT_KIND
        and raw['policy']['application'] == 'explicit_additive_continuation_grant')
    need(type(raw['max_calls']) is int and raw['max_calls'] == 1
        and all(raw[key] is False for key in ('side_effect', 'external_allowed', 'cloud_allowed'))
        and type(raw['simulation_only']) is bool and type(raw['test_only']) is bool
        and raw['agent_role'] == 'data' and raw['records_binding'] in c.BINDINGS and raw['input_schema_hash'] == digest(c.input_schema())
        and raw['output_schema_hash'] == digest(c.output_schema())
        and type(raw['records']) is list and 1 <= len(raw['records']) <= c.MAX_RECORDS
        and type(raw['provenance']) is list and len(raw['provenance']) == len(raw['records'])
        and type(raw['inputs']) is dict and raw['input_hash'] == digest(raw['inputs'])
        and digest(raw['inputs'].get('records')) == digest(raw['records'])
        and raw['inputs'].get('field') == body['field'] and raw['inputs'].get('operation') == body['operation'])
    validate(c.input_schema(), raw['inputs'])
    need(all(row['entity_id'] in raw['scope']['entity_ids'] and set(row['attributes']) <= {body['field']}
        for row in raw['records']))
    need(_time(raw['issued_at']) < _time(raw['expires_at'])
        and (_time(raw['expires_at']) - _time(raw['issued_at'])).total_seconds() <= body['validity_seconds'])
    return copy.deepcopy(raw)


def _grant(value, p):
    need(type(value) is dict and set(value) == _GRANT_FIELDS and value['schema'] == c.GRANT
        and value['workspace_id'] == p.workspace_id and value['issuer_role'] == 'admin'
        and value['record_hash'] == c.record_hash(value) and all(value[key] is False for key in c.FLAGS))
    for key in ('grant_id', 'task_id', 'advice_plan_id', 'continuation_id', 'issuer_id'):
        drafts.existing._id(value[key])
    body = c.dump(c.IssueRequest, value['request'])
    need(digest(body) == digest(value['request']) and value['request_hash'] == digest(body))
    source = value['source']
    need(type(source) is dict and source.get('schema') == drafts.c.SOURCE
        and source.get('workspace_id') == p.workspace_id and source.get('task_id') == value['task_id']
        and source.get('advice_plan_id') == value['advice_plan_id']
        and source.get('origin_kind') == 'independent_advice' and source.get('bridge_state') == 'not_bound'
        and source.get('review_decision') in {'accepted', 'needs_revision'}
        and value['source_hash'] == digest(source) == body['source_hash'])
    basis = _basis(value['basis'], source, body, actor_id=value['issuer_id'], task_id=value['task_id'],
        advice_id=value['advice_plan_id'], continuation_id=value['continuation_id'], workspace_id=p.workspace_id)
    need(value['issued_at'] == basis['issued_at'] and value['expires_at'] == basis['expires_at'])
    return copy.deepcopy(value)


def _command(event, p):
    value = event.data
    need(type(value) is dict and set(value) == _COMMAND_FIELDS and value['schema'] == c.COMMAND
        and value['operation'] in _ACTION and event.action == _ACTION[value['operation']]
        and value['workspace_id'] == event.workspace_id == p.workspace_id
        and event.object_id == value['grant_id'] and event.actor_id == value['actor_id']
        and value['actor_role'] == 'admin' and value['record_hash'] == c.record_hash(value))
    for key in ('task_id', 'advice_plan_id', 'continuation_id', 'grant_id', 'actor_id', 'request_id'):
        drafts.existing._id(value[key])
    body = c.dump(_MODEL[value['operation']], value['request'])
    need(digest(body) == digest(value['request']) and body['request_id'] == value['request_id']
        and value['body_hash'] == digest(body) and type(value['version_before']) is int
        and type(value['version_after']) is int and value['version_after'] == value['version_before'] + 1)
    if value['operation'] == 'issue':
        grant = _grant(value['grant'], p)
        need(value['version_before'] == 0 and value['phase'] == 'active' and value['issue_ref'] is None
            and grant['grant_id'] == value['grant_id'] and grant['issuer_id'] == value['actor_id']
            and grant['task_id'] == value['task_id'] and grant['advice_plan_id'] == value['advice_plan_id']
            and grant['continuation_id'] == value['continuation_id']
            and grant['source_hash'] == value['source_hash'] and grant['record_hash'] == value['grant_hash']
            and digest(grant['request']) == digest(body) and _time(event.created_at) == _time(grant['issued_at']))
    else:
        need(value['grant'] is None and value['version_before'] == body['expected_grant_version'] == 1
            and value['phase'] == 'revoked' and type(value['issue_ref']) is dict
            and set(value['issue_ref']) == {'id', 'record_hash'})
        drafts.existing._id(value['issue_ref']['id'])
    _time(event.created_at)
    return copy.deepcopy(value)


def _nonce(db, p, request_id):
    request_id = drafts.existing._id(request_id)
    events = _rows(db, p, Audit, Audit.action.in_(c.ACTIONS), or_(
        Audit.data['request_id'].as_string() == request_id, Audit.data['request']['request_id'].as_string() == request_id))
    pointers = _rows(db, p, Plan, or_(Plan.data['operations'].contains([{'request_id': request_id}]),
        Plan.data['grant']['request']['request_id'].as_string() == request_id))
    need(len(events) <= 1, '原授权请求编号有重复收件，未创建替代登记')
    if not events:
        need(not pointers, '原授权正文仍在但保护回执缺失，未作为未保存重建')
        return None
    event = events[0];value = _command(event, p)
    need(len(pointers) <= 1 and all(row.id == value['grant_id'] and row.data.get('schema') == c.GRANT for row in pointers))
    return event, value


def _saved(plan, events, p, task):
    data = plan.data
    need(plan.workspace_id == p.workspace_id and plan.task_id == task.id and type(data) is dict
        and set(data) == _PLAN_FIELDS and data['schema'] == c.GRANT
        and plan.approved_by is None and plan.approved_hash is None
        and plan.status in {'active', 'revoked'} and type(plan.version) is int
        and type(data['operations']) is list and 1 <= len(data['operations']) <= 2)
    grant = _grant(data['grant'], p)
    need(grant['grant_id'] == plan.id and grant['task_id'] == task.id
        and _time(plan.created_at) == _time(grant['issued_at']))
    indexed = {event.id: event for event in events}
    need(len(indexed) == len(events) == len(data['operations']))
    first = None;revocation = None;version = 0;phase = None;nonces = set()
    for pointer in data['operations']:
        need(type(pointer) is dict and set(pointer) == {'id', 'record_hash', 'request_id'} and pointer['id'] in indexed)
        event = indexed[pointer['id']];value = _command(event, p)
        need(pointer == {'id': event.id, 'record_hash': digest(value), 'request_id': value['request_id']}
            and value['task_id'] == task.id and value['advice_plan_id'] == grant['advice_plan_id']
            and value['continuation_id'] == grant['continuation_id'] and value['grant_id'] == plan.id
            and value['grant_hash'] == grant['record_hash'] and value['source_hash'] == grant['source_hash']
            and value['version_before'] == version and value['request_id'] not in nonces)
        nonces.add(value['request_id'])
        if version == 0:
            need(value['operation'] == 'issue' and digest(value['grant']) == digest(grant));first = event
        else:
            need(value['operation'] == 'revoke' and value['issue_ref'] == _ref(first)
                and _time(event.created_at) >= _time(first.created_at));revocation = event
        version = value['version_after'];phase = value['phase']
    need(plan.version == version and plan.status == phase)
    return {'plan': plan, 'grant': grant, 'issue': first, 'revocation': revocation}


@checked
def _inventory(db, p, task, advice_id, continuation_id):
    scope = or_((Plan.task_id == task.id) & (Plan.data['schema'].as_string() == c.GRANT),
        Plan.data['grant']['task_id'].as_string() == task.id,
        Plan.data['grant']['continuation_id'].as_string() == continuation_id)
    plans = _rows(db, p, Plan, scope)
    events = _rows(db, p, Audit, Audit.action.in_(c.ACTIONS), or_(
        Audit.data['task_id'].as_string() == task.id,
        Audit.data['continuation_id'].as_string() == continuation_id, Audit.object_id.in_([row.id for row in plans])))
    values = [_command(event, p) for event in events]
    need(all(value['task_id'] == task.id for value in values))
    plans = _rows(db, p, Plan, or_(scope, Plan.id.in_([value['grant_id'] for value in values])))
    need(len(plans) <= MAX_GRANTS and len({row.id for row in plans}) == len(plans)
        and {value['grant_id'] for value in values} == {row.id for row in plans},
        '授权登记或独立回执存在孤儿、跨任务或缺失原件，未另建登记')
    need(len({value['request_id'] for value in values}) == len(values))
    return [_saved(plan, [event for event in events if event.object_id == plan.id], p, task) for plan in plans]


def _find(inventory, grant_id, advice_id, continuation_id):
    identity = drafts.existing._id(grant_id)
    rows = [saved for saved in inventory if saved['plan'].id == identity
        and saved['grant']['advice_plan_id'] == advice_id and saved['grant']['continuation_id'] == continuation_id]
    if not rows: raise HTTPException(404, '此工具授权不存在或不属于当前任务、建议和草案')
    need(len(rows) == 1);return rows[0]


def _view(db, p, saved):
    plan, grant = saved['plan'], saved['grant'];basis = grant['basis'];expired = _clock(db) >= _time(grant['expires_at'])
    revocation = saved['revocation']
    revoked = None if revocation is None else {'request_id': revocation.data['request_id'], 'actor_id': revocation.actor_id,
        'reason': revocation.data['request']['reason'], 'revoked_at': revocation.created_at.isoformat(), 'receipt_ref': _ref(revocation)}
    return {'schema': c.VIEW, 'task_id': plan.task_id, 'advice_plan_id': grant['advice_plan_id'],
        'continuation_id': grant['continuation_id'], 'grant_id': plan.id, 'version': plan.version, 'phase': plan.status,
        'grant_ref': {'id': plan.id, 'record_hash': grant['record_hash']}, 'source_hash': grant['source_hash'],
        'continuation_ref': copy.deepcopy(basis['continuation_ref']), 'capability_pin': copy.deepcopy(basis['capability_pin']),
        'scope': copy.deepcopy(basis['scope']), 'field': copy.deepcopy(basis['field']), 'operation': basis['operation'],
        'policy_ref': copy.deepcopy(basis['policy']['ref']), 'issued_at': grant['issued_at'], 'expires_at': grant['expires_at'],
        'expired': expired, 'max_calls': 1, 'simulation_only': basis['simulation_only'], 'test_only': basis['test_only'],
        'revocation': revoked, 'permissions': {'can_revoke': p.role == 'admin' and plan.status == 'active'},
        **c.FLAGS}


def _same_scope(value, task, advice_id, continuation_id):
    need(value['task_id'] == task.id and value['advice_plan_id'] == advice_id
        and value['continuation_id'] == continuation_id, '原授权请求不属于当前任务、建议或草案')


def _write(db, p, task_id, advice_id, continuation_id, body, operation, grant_id=None):
    require(p, 'admin');body = c.dump(_MODEL[operation], body)
    advice_id = drafts.existing._id(advice_id);continuation_id = drafts.existing._id(continuation_id)
    with db.no_autoflush:
        drafts._reader(db, p, write=True)
        command_nonce.acquire(db,p,action=_ACTION[operation],task_id=task_id,body=body,
            subject_id=grant_id,advice_id=advice_id)
        task = drafts.existing._task(db, p, task_id, lock=True);lock_current_actor(db, p)
        original = _nonce(db, p, body['request_id'])
        if original:
            event, value = original;_same_scope(value, task, advice_id, continuation_id)
            need(value['operation'] == operation and value['actor_id'] == p.user_id and value['body_hash'] == digest(body)
                and (grant_id is None or value['grant_id'] == grant_id), '原编号不能更换授权、操作、提交者或完整正文')
            return _view(db,p,_find(_inventory(db,p,task,advice_id,continuation_id),value['grant_id'],advice_id,continuation_id))
        inventory = _inventory(db, p, task, advice_id, continuation_id)
        saved = None if operation == 'issue' else _find(inventory, grant_id, advice_id, continuation_id)
        if operation == 'issue':
            need(len(inventory) < MAX_GRANTS)
            loaded = sources.issue_basis(db,p,task.id,advice_id,continuation_id,body,lock=True)
            need(loaded['task'].id == task.id and loaded['task'].workspace_id == p.workspace_id
                and loaded['task'].version == body['expected_task_version']
                and loaded['continuation'].id == continuation_id and loaded['continuation'].task_id == task.id
                and loaded['continuation'].status == 'draft'
                and loaded['continuation'].version == body['expected_continuation_version']
                and loaded['continuation'].data['source_hash'] == body['source_hash']
                and loaded['continuation'].data['revisions'][-1]['record_hash'] == body['revision_hash']
                and loaded['source_hash'] == body['source_hash'] == digest(loaded['source']))
            live = loaded['basis']
            need(live['goal']['native_context_hash'] == task.data['native_goal_context_hash']
                and live['goal']['hash'] == task.data['goal_hash'] and live['goal']['version'] == task.data['goal_version']
                and live['release']['id'] == task.data['instance_release_id']
                and live['release']['package_id'] == task.package_id
                and live['release']['original_graph_hash'] == task.data['graph_runtime']['graph_hash'])
            plan = Plan(id=uid(),workspace_id=p.workspace_id,task_id=task.id,status='active',version=1,
                approved_by=None,approved_hash=None,created_at=_time(loaded['basis']['issued_at']))
            raw = {'schema': c.GRANT, 'grant_id': plan.id, 'workspace_id': p.workspace_id, 'task_id': task.id,
                'advice_plan_id': advice_id, 'continuation_id': continuation_id, 'issuer_id': p.user_id, 'issuer_role': p.role,
                'request': body, 'request_hash': digest(body), 'source': copy.deepcopy(loaded['source']),
                'source_hash': loaded['source_hash'], 'basis': copy.deepcopy(loaded['basis']),
                'issued_at': loaded['basis']['issued_at'], 'expires_at': loaded['basis']['expires_at'], **c.FLAGS}
            raw['record_hash'] = c.record_hash(raw);grant = _grant(raw,p)
            now = _clock(db);need(_time(grant['issued_at']) <= now < _time(grant['expires_at']), '授权准备窗口已过期，未续期或新增登记')
            if task.data['goal_spec'].get('deadline') is not None:
                need(_time(grant['expires_at']) <= _time(task.data['goal_spec']['deadline']))
            before, after, phase, issue_ref, clock = 0, 1, 'active', None, _time(grant['issued_at'])
        else:
            plan = saved['plan'];locked = _rows(db,p,Plan,Plan.id==plan.id,lock=True);need(len(locked)==1)
            need(plan.status=='active' and plan.version==body['expected_grant_version']==1,
                '授权已撤销或版本改变，原登记和撤销记录均保留')
            grant = saved['grant'];before, after, phase, issue_ref, clock = 1, 2, 'revoked', _ref(saved['issue']), _clock(db)
        value = {'schema': c.COMMAND, 'workspace_id': p.workspace_id, 'task_id': task.id,
            'advice_plan_id': advice_id, 'continuation_id': continuation_id, 'grant_id': plan.id,
            'operation': operation, 'actor_id': p.user_id, 'actor_role': p.role, 'request_id': body['request_id'],
            'request': body, 'body_hash': digest(body), 'grant': copy.deepcopy(grant) if operation=='issue' else None,
            'grant_hash': grant['record_hash'], 'source_hash': grant['source_hash'], 'issue_ref': issue_ref,
            'version_before': before, 'version_after': after, 'phase': phase}
        value['record_hash']=c.record_hash(value)
        event=Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,action=_ACTION[operation],
            object_id=plan.id,created_at=clock,data=value)
        pointer={'id':event.id,'record_hash':digest(value),'request_id':body['request_id']}
        with db.begin_nested():
            plan.data={'schema':c.GRANT,'grant':copy.deepcopy(grant),
                'operations':[*copy.deepcopy(plan.data['operations']),pointer] if saved else [pointer]}
            plan.status=phase;plan.version=after
            if not saved: db.add(plan)
            db.add(event);db.flush()
            return _view(db,p,_find(_inventory(db,p,task,advice_id,continuation_id),plan.id,advice_id,continuation_id))


@checked
def issue(db,p,task_id,advice_id,continuation_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'issue')


@checked
def revoke(db,p,task_id,advice_id,continuation_id,grant_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'revoke',drafts.existing._id(grant_id))


@checked
def get(db,p,task_id,advice_id,continuation_id,grant_id):
    with db.no_autoflush:
        drafts._reader(db,p);task=drafts.existing._task(db,p,task_id)
        advice_id=drafts.existing._id(advice_id);continuation_id=drafts.existing._id(continuation_id)
        return _view(db,p,_find(_inventory(db,p,task,advice_id,continuation_id),grant_id,advice_id,continuation_id))


@checked
def listing(db,p,task_id,advice_id,continuation_id):
    with db.no_autoflush:
        drafts._reader(db,p);task=drafts.existing._task(db,p,task_id)
        advice_id=drafts.existing._id(advice_id);continuation_id=drafts.existing._id(continuation_id)
        return {'schema':c.VIEW+'/list','task_id':task.id,'advice_plan_id':advice_id,'continuation_id':continuation_id,
            'records':[_view(db,p,saved) for saved in _inventory(db,p,task,advice_id,continuation_id)
                if saved['grant']['advice_plan_id']==advice_id and saved['grant']['continuation_id']==continuation_id]}


@checked
def get_operation(db,p,task_id,advice_id,continuation_id,request_id):
    with db.no_autoflush:
        drafts._reader(db,p);task=drafts.existing._task(db,p,task_id)
        advice_id=drafts.existing._id(advice_id);continuation_id=drafts.existing._id(continuation_id)
        original=_nonce(db,p,request_id)
        if original is None: raise HTTPException(404,'尚未查到此授权原请求，请保留原编号核对')
        event,value=original;_same_scope(value,task,advice_id,continuation_id);status='available'
        try: result=_view(db,p,_find(_inventory(db,p,task,advice_id,continuation_id),value['grant_id'],advice_id,continuation_id))
        except HTTPException as error:
            if error.status_code not in {404,409,410}: raise
            result=None;status='needs_check'
        return {'schema':c.RECEIPT,'found':True,'operation':value['operation'],'task_id':task.id,
            'advice_plan_id':advice_id,'continuation_id':continuation_id,'grant_id':value['grant_id'],
            'request_id':value['request_id'],'actor_id':value['actor_id'],'body_hash':value['body_hash'],
            'result':result,'result_status':status}
