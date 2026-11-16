"""One explicit new reading of a current conflict; never a new device grant.

Old conflict queries and gaps remain immutable evidence. A normal local query
branch and ordinary sensing plan perform the work, with at most one Acquisition.
Only the final command Audit permits the new plan to enter normal selection.
"""
import copy
from datetime import UTC, timedelta
import hashlib
import json
import math
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from . import models as m
from .contracts import digest
from .services import scoped
from .security import require
from .relations import lock_current_actor
from .goal_conflict_observation_models_v259 import GoalConflictObservationCommandV259 as Command
from .goal_conflict_observation_contracts_v259 import ObserveOnce

KEY = 'goal_conflict_observation_v259'
REQUEST = 'goal.conflict_observation_requested_v259'
COMPLETED = 'goal.conflict_observation_command_v259'
ACTION = 'goal.conflict_observation_acquisition_v259'
ERROR = '原冲突复测申请、查询分支或独立依据无法核对；原记录保留'
MAX_RAW = 131072
LIMIT = 200
OPEN = {'authorized', 'dispatching', 'unknown', 'awaiting_observation'}
REASONS = {'context_changed':'原Goal或冲突快照已变化，请核对后明确另拟申请',
    'policy_changed':'原监测批准版本已变化，未扩大或替换设备授权',
    'already_requested':'同一原Goal及冲突已有一次受理，请打开原请求核对，不重复采集',
    'goal_not_allowed':'原Goal当前不允许本次新观测，原记录保留',
    'busy_action':'原Goal仍有待派发、在途或结果未知的动作，请先核对原回执',
    'source_unavailable':'原冲突及独立收件依据不能完整核对，未新增设备动作',
    'preparation_failed':'本次查询分支或观测计划未完整准备，原申请保留且未新增设备动作'}


def need(ok, message=ERROR, status=409):
    if not ok: raise HTTPException(status, message)


def sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate key')
            result[key] = value
        return result
    def invalid(_): raise ValueError('nonfinite')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    stack = [(value, 0)]; nodes = 0
    while stack:
        item, depth = stack.pop(); nodes += 1
        if nodes > 10000 or depth > 32: raise ValueError('structure budget')
        if type(item) is dict:
            for key, child in item.items():
                if '\0' in key: raise ValueError('NUL')
                key.encode('utf-8'); stack.append((child, depth + 1))
        elif type(item) is list: stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if '\0' in item: raise ValueError('NUL')
            item.encode('utf-8')
    return value


def parse(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, '申请正文为空或超过上限', 422)
    try:
        original = raw.decode('utf-8'); body = ObserveOnce.model_validate(_json(original))
        return body, original, hashlib.sha256(raw).hexdigest()
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise HTTPException(422, '单次冲突复测申请的字段或原文格式不完整') from None


def _row(db, p, cls, identity, *, lock=False, internal=False):
    query = select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None, ERROR if internal else '没有此工作区的原目标资料', 409 if internal else 404)
    return row


def _row_hash(row):
    names = ('owner_id', 'question', 'status', 'query_id', 'entity_id', 'policy_id')
    return digest({'id': row.id, 'workspace_id': row.workspace_id, 'version': row.version,
        'created_at': row.created_at.astimezone(UTC).isoformat(),
        **{key: getattr(row, key) for key in names if hasattr(row, key)}, 'data': row.data})


def _nonce(db, p, nonce):
    value = 'goal-conflict-once-v259\0' + p.workspace_id + '\0' + p.user_id + '\0' + nonce
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)})


def _audit(db, p, action, identity, data):
    proof = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=identity, data=copy.deepcopy(data))
    db.add(proof); db.flush(); return proof


def _proof(db, p, action, identity, proof_id, actor_id, expected):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).limit(2)))
    need(len(rows) == 1 and rows[0].id == proof_id and rows[0].actor_id == actor_id and digest(rows[0].data) == digest(expected))
    return rows[0]


def _numeric(value):
    if type(value) not in {int, float}: return False
    try: return math.isfinite(value)
    except (ValueError, OverflowError): return False


def _ack(db, p, observation, report):
    from .state_receipt_guard_v245 import check as check_receipt
    reception = check_receipt(db, p, observation)
    if reception is not None: need(reception.get('status') != 'waiting_data' and sha(reception.get('receipt_hash')))
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation.id).limit(2)))
    need(len(events) == 1 and events[0].status == 'received' and events[0].received_at is not None)
    event = events[0]
    need(event.data.get('observation_id') == observation.id and event.data.get('entity_id') == observation.entity_id)
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id,
        m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)))
    need(len(memories) == 1)
    memory = memories[0]
    need(digest(memory.data) == digest({**event.data, 'event_id': event.id, 'type': 'observation_evidence', 'not_authoritative': True}))
    # This is a digest of existing independent ACK evidence, not a new receipt.
    return digest({'schema':'goal-conflict-ack-evidence-v259', 'observation_id':observation.id,
        'event_id':event.id, 'received_at':event.received_at.astimezone(UTC).isoformat(),
        'event_data_hash':digest(event.data), 'memory_id':memory.id, 'memory_data_hash':digest(memory.data),
        'data_receipt_hash':reception['receipt_hash'] if reception is not None else None})


def _scope(db, p, task, gap_id, *, current=True):
    from .native_goals import saved_context
    c = saved_context(db, p, task)
    gap = _row(db, p, m.DataGap, gap_id)
    run = _row(db, p, m.DataRun, gap.query_id, internal=True)
    need(run.data.get('native_goal_task_id') == task.id and run.data.get('native_goal_context_hash') == digest(c)
        and digest(run.data['request']) == run.data.get('native_goal_request_hash')
        and digest(run.data.get('goal_delegation')) == digest({'task_id':task.id, 'goal_snapshot':c['goal_snapshot']})
        and gap.entity_id in c['entity_ids'] and gap.data.get('business_task_id') == task.id,
        '原冲突资料不属于此正式Goal', 404)
    if current: need(task.data.get('native_goal_query_id') == run.id, '请选择原Goal当前查询中的冲突')
    return c, run, gap


def _conflicts(db, p, task, gap, evidence):
    from .observation_integrity import checked_report
    fields=sorted(set(task.data['goal_spec']['required_fields'])&set(gap.data['request']['fields']))
    values=[]
    for field in fields:
        refs=[v for v in evidence.get('observation_evidence',[]) if v.get('entity_id')==gap.entity_id and field in v.get('attributes',{})]
        identities=sorted({v['observation_id'] for v in refs})
        need(len(identities)<=LIMIT)
        rows=[_row(db,p,m.Observation,identity,internal=True) for identity in identities]
        rows=[row for row in rows if row.entity_id==gap.entity_id and row.observed_at is not None]
        if len(rows)<2:continue
        newest=max(row.observed_at for row in rows);rows=[row for row in rows if row.observed_at==newest]
        latest={row.id for row in rows}
        declared=[v for v in evidence.get('conflicts',[]) if v.get('field')==field and v.get('entity_id',gap.entity_id)==gap.entity_id]
        if len(rows)<2 or not any(len(set(v.get('observation_ids',[]))&latest)>=2 for v in declared):continue
        candidates=[]
        for row in sorted(rows,key=lambda r:r.id):
            need(row.status=='processed' and row.data.get('candidate_only') is not True)
            report=checked_report(db,p,row);value=report['attributes'].get(field)
            need(report.get('review')!='rejected' and _numeric(value))
            source=_row(db,p,m.Capability,row.source_id,internal=True)
            need(source.content_hash==digest(source.data))
            name=report.get('source_name') or source.data.get('description') or source.name
            need(type(name) is str and bool(name.strip()))
            candidates.append({'observation_id':row.id,'source_id':row.source_id,'source_name':name,
                'source_hash':source.content_hash,'invocation_id':report['invocation_id'],
                'value':value,'receipt_hash':_ack(db,p,row,report)})
        if not any(v['value']!=candidates[0]['value'] for v in candidates):continue
        values.append({'field':field,'observed_at':newest.astimezone(UTC).isoformat(),'candidates':candidates})
    need(values,'当前原Goal没有可完整核对的最新同刻数值冲突')
    return values


def _policies(db, p, entity_id, fields):
    from .perception import authorized_monitor
    rows = list(db.scalars(select(m.MonitorPolicy).where(m.MonitorPolicy.workspace_id == p.workspace_id,
        m.MonitorPolicy.status == 'active').order_by(m.MonitorPolicy.created_at, m.MonitorPolicy.id).limit(LIMIT + 1)))
    need(len(rows) <= LIMIT)
    result = []
    for row in rows:
        data = row.data; requirement = data.get('selection_requirement')
        if not requirement or entity_id not in data.get('entity_ids', []) or not set(fields).issubset(data.get('fields', [])): continue
        try: authorized_monitor(db,p,row)
        except HTTPException: continue
        source_names=[]
        for source_id in data['source_ids']:
            source=_row(db,p,m.Capability,source_id,internal=True)
            need(source.content_hash==digest(source.data))
            source_names.append(str(source.data.get('description') or source.name))
        title=data.get('name') or data.get('title') or ' / '.join(source_names)+'监测'
        result.append({'id':row.id, 'version':row.version, 'name':str(title),
            'approved_hash':data['approved_hash'], 'fields':copy.deepcopy(data['fields']),
            'source_ids':copy.deepcopy(data['source_ids']), 'valid_until':data['valid_until'],
            'limits':{'max_acquisitions_per_day':data['max_acquisitions_per_day'], 'cooldown_seconds':data['cooldown_seconds'],
                'max_age_seconds':data['max_age_seconds'], 'max_attempts':requirement['max_attempts'],
                'max_total_cost_minor':requirement['max_total_cost_minor'], 'currency':requirement['currency']}})
    return result


def _context(db, p, task, gap_id):
    from .native_goals import assessment
    c, run, gap = _scope(db,p,task,gap_id)
    need(gap.status == 'needs_review', '原缺口尚未形成待核查冲突')
    spec = task.data['goal_spec']; req = spec['evidence']
    need(spec['observation_mode'] == 'allow_new' and req['source'] == 'observations' and req['time']['mode'] == 'latest', REASONS['goal_not_allowed'])
    evidence, _ = assessment(db,p,task,lock_sources=False)
    conflicts = _conflicts(db,p,task,gap,evidence); fields = [v['field'] for v in conflicts]
    conflict_hash = digest({'entity_id':gap.entity_id,'fields':fields,'conflicts':conflicts})
    value = {'schema':'goal-conflict-context-v259', 'workspace_id':p.workspace_id, 'actor_id':p.user_id,
        'task_id':task.id,'task_version':task.version,'instance_id':c['instance_id'],'release_id':c['release_id'],
        'business_id':c['business_id'],'goal_context_hash':digest(c),'query_id':run.id,
        'query_request_hash':digest(run.data['request']),'query_record_hash':_row_hash(run),
        'gap_id':gap.id,'gap_version':gap.version,'gap_record_hash':_row_hash(gap),'entity_id':gap.entity_id,
        'fields':fields,'conflicts':conflicts,'conflict_hash':conflict_hash,
        'old_conflict_status':'needs_review','field_truth_verified':False}
    value['context_hash'] = digest(value)
    policies = _policies(db,p,gap.entity_id,fields)
    reason = None if p.role == 'admin' and policies else '请由管理员选择仍有效且覆盖原范围的已批准监测策略'
    return {**value, 'monitor_policies':policies, 'can_request':reason is None, 'reason':reason}


def _accepted_key(task_id, goal_hash, conflict_hash):
    return digest({'task_id':task_id,'goal_context_hash':goal_hash,'conflict_hash':conflict_hash})


def _link(identity, p, task_id, body, raw_hash, request_hash, accepted_key):
    return {'schema':'goal-conflict-observation-link-v259','command_id':identity,'actor_id':p.user_id,'task_id':task_id,
        'request_id':body.request_id,'raw_sha256':raw_hash,'request_hash':request_hash,'accepted_key':accepted_key}


def _origin(row):
    return {'schema':'goal-conflict-observation-origin-v259','id':row.id,'version':row.version,
        'workspace_id':row.workspace_id,'actor_id':row.actor_id,'task_id':row.task_id,'request_id':row.request_id,
        'raw_sha256':row.raw_sha256,'context_hash':row.context_hash,'conflict_hash':row.conflict_hash,
        'accepted_key':row.accepted_key,'outcome':row.outcome,'original_query_id':row.original_query_id,
        'original_gap_id':row.original_gap_id,'branch_query_id':row.branch_query_id,'branch_gap_id':row.branch_gap_id,
        'observation_plan_id':row.observation_plan_id,'policy_id':row.policy_id,
        'created_at':row.created_at.astimezone(UTC).isoformat(),
        'data':{k:v for k,v in row.data.items() if k != 'completed_audit_id'}}


def _preserved(db, p, originals):
    query = _row(db,p,m.DataRun,originals['query_id'],internal=True)
    gap = _row(db,p,m.DataGap,originals['gap_id'],internal=True)
    need(_row_hash(query) == originals['query_record_hash'] and _row_hash(gap) == originals['gap_record_hash'])
    for ref in originals['plans']:
        need(_row_hash(_row(db,p,m.ObservationPlan,ref['id'],internal=True)) == ref['record_hash'])
    return query, gap


def _action_proof(row,plan,action,job,actor_id):
    return {'schema':'goal-conflict-observation-acquisition-v259','command_id':row.id,
        'workspace_id':row.workspace_id,'task_id':row.task_id,'actor_id':actor_id,
        'requester_id':row.actor_id,'request_id':row.request_id,'raw_sha256':row.raw_sha256,
        'observation_plan_id':plan.id,'acquisition_id':action.id,'policy_id':row.policy_id,
        'branch_query_id':row.branch_query_id,'branch_gap_id':row.branch_gap_id,
        'accepted_key':row.accepted_key,'entity_id':action.entity_id,'source_id':action.capability_id,
        'source_hash':action.data['source_hash'],'fields':copy.deepcopy(action.data['selected_fields']),
        'acquisition_key':action.idempotency_key,'job_id':job.id,'job_key':job.idempotency_key,
        'job_actor_id':job.data['actor_id']}


def _actions(db,p,row,plan):
    actions=list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
        m.Acquisition.data['observation_plan_id'].as_string()==plan.id).limit(2)))
    anchors=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==ACTION,m.Audit.object_id==row.id).limit(2)))
    selected=[item.get('acquisition_id') for item in plan.data.get('history',[]) if item.get('kind')=='selected']
    need(len(actions)<=1 and len(anchors)==len(actions))
    if not actions:
        need(plan.data.get('current_acquisition_id') is None and not selected)
        orphan=db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,
            m.Job.kind=='dispatch_acquisition',m.Job.data[KEY]['command_id'].as_string()==row.id).limit(1))
        need(orphan is None);return []
    action=actions[0];anchor=anchors[0]
    need(plan.data.get('current_acquisition_id')==action.id and selected==[action.id])
    jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,
        m.Job.kind=='dispatch_acquisition',m.Job.idempotency_key=='dispatch:'+action.id).limit(2)))
    need(len(jobs)==1)
    job=jobs[0]
    need(job.data.get('acquisition_id')==action.id and digest(job.data.get(KEY))==digest(row.data['link'])
        and digest(anchor.data)==digest(_action_proof(row,plan,action,job,anchor.actor_id)))
    return actions


def freeze_action(db,p,plan,action,job):
    """Same-transaction independent evidence of the sole real dispatch intent."""
    rows=list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,
        Command.observation_plan_id==plan.id,Command.outcome=='accepted').limit(2)))
    need(len(rows)==1);row=rows[0]
    _proof(db,p,COMPLETED,row.id,row.data['completed_audit_id'],row.actor_id,_origin(row))
    need(digest(plan.data.get(KEY))==digest(row.data['link']) and digest(action.data.get(KEY))==digest(row.data['link']))
    job.data={**job.data,KEY:copy.deepcopy(row.data['link'])};db.flush()
    need(not db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==ACTION,m.Audit.object_id==row.id).limit(1)))
    _audit(db,p,ACTION,row.id,_action_proof(row,plan,action,job,p.user_id))
    _actions(db,p,row,plan)


def _checked(db, p, row):
    try:
        data = row.data; body, original, raw_hash = parse(data['raw'].encode('utf-8'))
        need(row.version == 1 and row.workspace_id == p.workspace_id and row.raw_sha256 == raw_hash
            and row.request_id == body.request_id and row.context_hash == body.context_hash
            and row.original_gap_id == body.gap_id and row.policy_id == body.monitor_policy_id
            and digest(body.model_dump()) == data['body_hash'])
        _proof(db,p,REQUEST,row.id,data['request_audit_id'],row.actor_id,data['request_proof'])
        need(digest(data['request_proof']) == data['request_hash'] and data['request_proof']['raw'] == original
            and data['request_proof']['body_hash'] == data['body_hash']
            and data['request_proof']['task_id'] == row.task_id and data['request_proof']['actor_id'] == row.actor_id)
        _proof(db,p,COMPLETED,row.id,data['completed_audit_id'],row.actor_id,_origin(row))
        need(row.record_hash == digest(_origin(row)))
        task = _row(db,p,m.Task,row.task_id,internal=True)
        from .native_goals import saved_context
        c = saved_context(db,p,task)
        need(digest(c) == data['goal_context_hash'])
        if row.outcome == 'accepted':
            context = data['context']
            need(context['context_hash'] == row.context_hash and context['conflict_hash'] == row.conflict_hash
                and row.accepted_key == _accepted_key(task.id,digest(c),row.conflict_hash) and data['rejection'] is None)
            _preserved(db,p,data['originals'])
            branch = _row(db,p,m.DataRun,row.branch_query_id,internal=True)
            gap = _row(db,p,m.DataGap,row.branch_gap_id,internal=True)
            plan = _row(db,p,m.ObservationPlan,row.observation_plan_id,internal=True)
            link = data['link']
            need(link['command_id'] == row.id and link['accepted_key'] == row.accepted_key
                and digest(branch.data.get(KEY)) == digest(link) and digest(gap.data.get(KEY)) == digest(link)
                and digest(plan.data.get(KEY)) == digest(link)
                and branch.data.get('native_goal_task_id') == task.id and branch.data.get('native_goal_context_hash') == digest(c)
                and digest(branch.data['request']) == data['branch']['request_hash'] == branch.data['native_goal_request_hash']
                and branch.data.get('data_gap_ids') == data['branch']['gap_ids']
                and digest(branch.data['goal_delegation']) == digest({'task_id':task.id,'goal_snapshot':c['goal_snapshot']})
                and gap.query_id == branch.id and gap.entity_id == context['entity_id']
                and digest(gap.data['request']) == data['branch']['gap_request_hash']
                and plan.policy_id == row.policy_id and plan.entity_id == gap.entity_id
                and plan.data['observation_request']['query_id'] == branch.id
                and plan.data['observation_request']['gap_id'] == gap.id
                and plan.data['observation_request']['fields'] == context['fields'])
            actions = _actions(db,p,row,plan)
            for action in actions:
                need(digest(action.data.get(KEY)) == digest(link) and action.entity_id == gap.entity_id
                    and action.data.get('policy_id') == row.policy_id and action.data.get('data_gap_id') == gap.id
                    and set(action.data.get('selected_fields',[])).issubset(context['fields']))
                if action.status=='succeeded':
                    closes=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
                        m.Audit.action=='acquisition.close',m.Audit.object_id==action.id).limit(2)))
                    need(len(closes)==1 and closes[0].data.get('verified_collection') is True
                        and closes[0].data.get('observation_id')==action.data.get('closing_observation_id'))
        else:
            need(row.outcome == 'rejected' and row.accepted_key is None and row.branch_query_id is None
                and row.branch_gap_id is None and row.observation_plan_id is None and type(data['rejection']) is dict
                and set(data['rejection']) == {'code','reason'} and data['rejection']['code'] in REASONS
                and data['rejection']['reason'] == REASONS[data['rejection']['code']])
        return row
    except HTTPException as error:
        if error.status_code in {401,403}: raise
        raise HTTPException(409,ERROR) from None
    except (ValueError,KeyError,TypeError,AttributeError): raise HTTPException(409,ERROR) from None


def _inventory(db,p,task_id):
    rows = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,Command.task_id == task_id).limit(LIMIT+1)))
    need(len(rows) <= LIMIT)
    for action in (REQUEST,COMPLETED):
        proofs = list(db.scalars(select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == action,m.Audit.data['task_id'].as_string() == task_id).limit(LIMIT+1)))
        need(len(proofs) == len(set(proofs)) == len(rows) and set(proofs) == {r.id for r in rows})
    for row in rows: _checked(db,p,row)
    accepted = [r for r in rows if r.outcome == 'accepted']
    for cls, column in [(m.DataRun,'branch_query_id'),(m.DataGap,'branch_gap_id'),(m.ObservationPlan,'observation_plan_id')]:
        traces = list(db.scalars(select(cls.id).where(cls.workspace_id == p.workspace_id,
            cls.data[KEY]['task_id'].as_string() == task_id).limit(LIMIT+1)))
        need(len(traces) == len(set(traces)) == len(accepted) and set(traces) == {getattr(r,column) for r in accepted})
    marked_actions=list(db.scalars(select(m.Acquisition.id).where(m.Acquisition.workspace_id==p.workspace_id,
        m.Acquisition.data[KEY]['task_id'].as_string()==task_id).limit(LIMIT+1)))
    expected_actions=[]
    for row in accepted:
        expected_actions.extend(db.scalars(select(m.Acquisition.id).where(m.Acquisition.workspace_id==p.workspace_id,
            m.Acquisition.data['observation_plan_id'].as_string()==row.observation_plan_id)))
    need(len(marked_actions)==len(set(marked_actions)) and set(marked_actions)==set(expected_actions))
    action_proofs=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==ACTION,m.Audit.data['task_id'].as_string()==task_id).limit(LIMIT+1)))
    need(len(action_proofs)==len(expected_actions) and len({a.object_id for a in action_proofs})==len(action_proofs)
        and {a.object_id for a in action_proofs}.issubset({r.id for r in accepted}))
    dispatches=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,
        m.Job.data[KEY]['task_id'].as_string()==task_id).limit(LIMIT+1)))
    need(len(dispatches)<=LIMIT and all(j.kind=='dispatch_acquisition' and j.data.get('acquisition_id') in expected_actions
        and j.data[KEY]['command_id'] in {r.id for r in accepted} for j in dispatches))
    return rows


def _existing(db,p,request_id):
    rows = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id,Command.request_id == request_id).limit(2)))
    traces = list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.actor_id == p.user_id,m.Audit.action.in_([REQUEST,COMPLETED]),
        m.Audit.data['request_id'].as_string() == request_id).limit(3)))
    lingering = False
    for cls in (m.DataRun,m.DataGap,m.ObservationPlan,m.Acquisition):
        if db.scalar(select(cls.id).where(cls.workspace_id == p.workspace_id,
            cls.data[KEY]['actor_id'].as_string() == p.user_id,
            cls.data[KEY]['request_id'].as_string() == request_id).limit(1)): lingering = True
    action_trace=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==ACTION,m.Audit.data['requester_id'].as_string()==p.user_id,
        m.Audit.data['request_id'].as_string()==request_id).limit(1))
    job_trace=db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,
        m.Job.data[KEY]['actor_id'].as_string()==p.user_id,m.Job.data[KEY]['request_id'].as_string()==request_id).limit(1))
    if not rows: need(not traces and not lingering and action_trace is None and job_trace is None); return None
    need(len(rows) == 1 and len(traces) == 2)
    _inventory(db,p,rows[0].task_id)
    return rows[0]


def _match(row,task_id,original,raw_hash):
    need(row.task_id == task_id and row.raw_sha256 == raw_hash and row.data['raw'] == original,
        '原申请号已经绑定另一份Task或正文，不能改写或重新采集')


def receipt(row):
    return {'schema':'goal-conflict-observation-receipt-v259','id':row.id,'version':row.version,
        'workspace_id':row.workspace_id,'actor_id':row.actor_id,'task_id':row.task_id,
        'request_id':row.request_id,'raw_sha256':row.raw_sha256,'body_hash':row.data['body_hash'],
        'created_at':row.created_at.astimezone(UTC).isoformat(),'outcome':row.outcome,
        'context_hash':row.context_hash,'conflict_hash':row.conflict_hash,
        'original_query_id':row.original_query_id,'original_gap_id':row.original_gap_id,
        'branch_query_id':row.branch_query_id,'branch_gap_id':row.branch_gap_id,
        'observation_plan_id':row.observation_plan_id,'rejection':copy.deepcopy(row.data['rejection']),
        'record_hash':row.record_hash}



def _verify_task(db,p,task):
    rows = _inventory(db,p,task.id)
    accepted = sorted((r for r in rows if r.outcome == 'accepted'),key=lambda r:(r.created_at,r.id))
    for previous,current in zip(accepted,accepted[1:]): need(current.original_query_id == previous.branch_query_id)
    if accepted: need(task.data.get('native_goal_query_id') == accepted[-1].branch_query_id)
    return rows


def historical_check_gaps(db,p,task,report):
    """Project the current branch without rewriting its older Goal receipt.

    None preserves the legacy/current-query gate. Only an independently
    accepted and completely cold-checked fork can explain an older query.
    """
    rows=_verify_task(db,p,task)
    accepted=[row for row in rows if row.outcome=='accepted']
    if not accepted:return None
    items=report.get('gaps',[])
    need(type(items) is list and len(items)<=LIMIT)
    old=[_row(db,p,m.DataGap,item['id'],internal=True) for item in items]
    current_id=task.data.get('native_goal_query_id')
    if not old or all(gap.query_id==current_id for gap in old):return None
    need(len({gap.id for gap in old})==len(old) and len({gap.query_id for gap in old})==1)
    origin=next((row for row in accepted if row.original_query_id==old[0].query_id),None)
    need(origin is not None and origin.original_gap_id in {gap.id for gap in old})
    from .native_goals import saved_context
    c=saved_context(db,p,task)
    need(digest(report.get('goal_snapshot'))==digest(c['goal_snapshot']))
    source=_row(db,p,m.DataRun,origin.original_query_id,internal=True)
    original_ids=source.data.get('data_gap_ids',[])
    need(type(original_ids) is list and all(gap.id in original_ids and gap.entity_id in c['entity_ids'] for gap in old))
    latest=next((row for row in accepted if row.branch_query_id==current_id),None)
    need(latest is not None)
    current=_row(db,p,m.DataRun,current_id,internal=True)
    identifiers=current.data.get('data_gap_ids')
    need(type(identifiers) is list and len(identifiers)<=LIMIT and len(identifiers)==len(set(identifiers))
        and identifiers==latest.data['branch']['gap_ids'])
    actual=list(db.scalars(select(m.DataGap.id).where(m.DataGap.workspace_id==p.workspace_id,
        m.DataGap.query_id==current.id).limit(LIMIT+1)))
    need(len(actual)==len(identifiers) and set(actual)==set(identifiers))
    projected=[]
    for identity in identifiers:
        gap=_row(db,p,m.DataGap,identity,internal=True)
        need(gap.query_id==current.id and gap.entity_id in c['entity_ids']
            and gap.data.get('business_task_id')==task.id)
        projected.append({'id':gap.id,'status':gap.status,
            'reason':gap.data.get('resolution_reason') if gap.status=='resolved' else gap.data.get('decision_reason'),
            'business_task_id':gap.data.get('business_task_id'),'acquisition_ids':copy.deepcopy(gap.data.get('acquisition_ids',[]))})
    return projected


def _originals(db,p,run,gap):
    plans = list(db.scalars(select(m.ObservationPlan).where(m.ObservationPlan.workspace_id == p.workspace_id,
        m.ObservationPlan.data['observation_request']['query_id'].as_string() == run.id).limit(LIMIT+1)))
    need(len(plans) <= LIMIT)
    return {'query_id':run.id,'query_record_hash':_row_hash(run),'gap_id':gap.id,'gap_record_hash':_row_hash(gap),
        'plans':[{'id':r.id,'record_hash':_row_hash(r)} for r in sorted(plans,key=lambda r:r.id)]}


def _busy(db,p,task):
    queries = list(db.scalars(select(m.DataRun.id).where(m.DataRun.workspace_id == p.workspace_id,
        m.DataRun.data['native_goal_task_id'].as_string() == task.id).limit(LIMIT+1)))
    need(len(queries) <= LIMIT)
    gaps = list(db.scalars(select(m.DataGap.id).where(m.DataGap.workspace_id == p.workspace_id,m.DataGap.query_id.in_(queries)).limit(LIMIT+1))) if queries else []
    plans = list(db.scalars(select(m.ObservationPlan.id).where(m.ObservationPlan.workspace_id == p.workspace_id,
        m.ObservationPlan.data['observation_request']['goal_delegation']['task_id'].as_string() == task.id).limit(LIMIT+1)))
    need(len(gaps) <= LIMIT and len(plans) <= LIMIT)
    from sqlalchemy import or_
    return bool(db.scalar(select(m.Acquisition.id).where(m.Acquisition.workspace_id == p.workspace_id,
        m.Acquisition.status.in_(OPEN),or_(m.Acquisition.data['data_gap_id'].as_string().in_(gaps),
            m.Acquisition.data['observation_plan_id'].as_string().in_(plans),
            m.Acquisition.data[KEY]['task_id'].as_string() == task.id)).limit(1)))


def _no_newer(db,p,context):
    from .temporal import instant
    from .observation_integrity import checked_report
    times={v['field']:instant(v['observed_at']) for v in context['conflicts']}
    cutoff=min(times.values())
    known={c['observation_id'] for v in context['conflicts'] for c in v['candidates']}
    rows = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        m.Observation.entity_id == context['entity_id'],m.Observation.observed_at > cutoff,
        m.Observation.data['candidate_only'].as_boolean().is_distinct_from(True)).limit(LIMIT+1)))
    need(len(rows) <= LIMIT)
    for row in rows:
        if row.id in known:continue
        if row.status == 'processed':
            report = checked_report(db,p,row)
            if report.get('review') == 'rejected': continue
            if any(field in report['attributes'] and row.observed_at>at for field,at in times.items()):
                raise HTTPException(409,'已有更晚的登记或待收件结果，请先核对当前原Goal，未新增设备动作')
        elif row.status not in {'failed','cancelled','rejected'}:
            raise HTTPException(409,'有更晚的输入尚在处理，请先核对原结果，未新增设备动作')


def _saved_request(db,p,identity,task,body,original,raw_hash,context,originals,goal_hash):
    value = {'schema':'goal-conflict-observation-request-proof-v259','command_id':identity,
        'workspace_id':p.workspace_id,'actor_id':p.user_id,'task_id':task.id,'request_id':body.request_id,
        'raw':original,'raw_sha256':raw_hash,'body_hash':digest(body.model_dump()),
        'context':copy.deepcopy(context),'originals':copy.deepcopy(originals),'goal_context_hash':goal_hash}
    proof = _audit(db,p,REQUEST,identity,value)
    return value,proof


def _save_command(db,p,identity,task,body,original,raw_hash,request_proof,request_audit,*,context,originals,branch=None,plan=None,rejection=None):
    accepted = rejection is None
    row = Command(id=identity,version=1,created_at=m.now(),workspace_id=p.workspace_id,actor_id=p.user_id,
        task_id=task.id,request_id=body.request_id,raw_sha256=raw_hash,context_hash=body.context_hash,
        conflict_hash=context['conflict_hash'] if context is not None else None,
        accepted_key=_accepted_key(task.id,request_proof['goal_context_hash'],context['conflict_hash']) if accepted else None,
        outcome='accepted' if accepted else 'rejected',original_query_id=originals['query_id'],original_gap_id=body.gap_id,
        branch_query_id=branch[0].id if accepted else None,branch_gap_id=branch[1].id if accepted else None,
        observation_plan_id=plan.id if accepted else None,policy_id=body.monitor_policy_id,record_hash='0'*64,
        data={'schema':'goal-conflict-observation-command-record-v259','raw':original,'body_hash':digest(body.model_dump()),
            'request_hash':digest(request_proof),'request_proof':request_proof,'request_audit_id':request_audit.id,
            'goal_context_hash':request_proof['goal_context_hash'],'context':copy.deepcopy(context),
            'originals':copy.deepcopy(originals),'rejection':{'code':rejection,'reason':REASONS[rejection]} if rejection else None,
            'branch':{'request_hash':digest(branch[0].data['request']),'gap_request_hash':digest(branch[1].data['request']),
                'gap_ids':copy.deepcopy(branch[0].data['data_gap_ids'])} if accepted else None,
            'link':copy.deepcopy(plan.data[KEY]) if accepted else None})
    db.add(row);db.flush()
    origin = _origin(row);row.record_hash = digest(origin)
    proof = _audit(db,p,COMPLETED,row.id,origin)
    row.data = {**row.data,'completed_audit_id':proof.id};db.flush()
    return row


def _prepare_plan(db,p,policy,task,context,branch,link):
    from .temporal import instant
    from .observation_requests import deadline
    from .sensing_selection import evidence
    run,gap = branch
    # This is the same frozen ObservationRequest scope, narrowed only to the
    # selected conflicting fields. The final command is signed before any
    # selection, reservation or dispatch Job can be created.
    origin = {'gap_id':gap.id,'query_id':run.id,'entity_id':gap.entity_id,'fields':copy.deepcopy(context['fields']),
        'request':copy.deepcopy(gap.data['request']),'query_request_hash':digest(run.data['request']),
        'goal_delegation':copy.deepcopy(run.data['goal_delegation'])}
    end = min(m.now()+timedelta(seconds=policy.data['selection_requirement']['plan_timeout_seconds']),instant(policy.data['valid_until']))
    if deadline(origin): end = min(end,deadline(origin))
    initial = evidence(db,p,policy,gap.entity_id,fields=context['fields'])
    plan = m.ObservationPlan(workspace_id=p.workspace_id,policy_id=policy.id,entity_id=gap.entity_id,
        data={'policy_hash':policy.data['approved_hash'],'deadline':end.isoformat(),'actor_id':policy.approved_by,
            'initial_evidence':initial,'observation_request':origin,KEY:copy.deepcopy(link)})
    db.add(plan);db.flush();return plan


def submit(db,p,task_id,raw):
    body,original,raw_hash = parse(raw)
    prior = _existing(db,p,body.request_id)
    if prior: _match(prior,task_id,original,raw_hash);return prior
    require(p,'admin');lock_current_actor(db,p);_nonce(db,p,body.request_id)
    prior = _existing(db,p,body.request_id)
    if prior: _match(prior,task_id,original,raw_hash);return prior
    task = _row(db,p,m.Task,task_id,lock=True)
    rows = _verify_task(db,p,task)
    from .native_goals import saved_context,guard,fork_conflict_observation
    c = saved_context(db,p,task)
    # Validate scope before saving any rejection; never issue receipts for a
    # cross-Task gap, an invented Goal or a foreign monitoring policy.
    _,run,gap = _scope(db,p,task,body.gap_id,current=False)
    gap = _row(db,p,m.DataGap,gap.id,lock=True,internal=True)
    policy = _row(db,p,m.MonitorPolicy,body.monitor_policy_id,lock=True)
    old = next((r for r in rows if r.outcome=='accepted' and r.original_gap_id==gap.id),None)
    code = 'already_requested' if old else None; context = copy.deepcopy(old.data['context']) if old else None
    if not old:
        try:
            guard(db,p,task)
            context = _context(db,p,task,body.gap_id)
            if context['context_hash'] != body.context_hash: code = 'context_changed'
            elif policy.version != body.monitor_policy_version: code = 'policy_changed'
            elif not any(v['id']==policy.id and v['version']==policy.version for v in context['monitor_policies']): code = 'policy_changed'
            elif any(r.outcome=='accepted' and r.accepted_key==_accepted_key(task.id,digest(c),context['conflict_hash']) for r in rows): code = 'already_requested'
            elif _busy(db,p,task): code = 'busy_action'
            else: _no_newer(db,p,context)
        except HTTPException as error:
            if error.status_code in {401,403,404}: raise
            code = 'goal_not_allowed' if context is None else 'source_unavailable'
    originals = _originals(db,p,run,gap)
    identity = m.uid()
    request_proof,request_audit = _saved_request(db,p,identity,task,body,original,raw_hash,context,originals,digest(c))
    if code:
        return _save_command(db,p,identity,task,body,original,raw_hash,request_proof,request_audit,
            context=context,originals=originals,rejection=code)
    link = _link(identity,p,task.id,body,raw_hash,digest(request_proof),_accepted_key(task.id,digest(c),context['conflict_hash']))
    try:
        with db.begin_nested():
            branch = fork_conflict_observation(db,p,task,run,gap,link)
            plan = _prepare_plan(db,p,policy,task,context,branch,link)
            row = _save_command(db,p,identity,task,body,original,raw_hash,request_proof,request_audit,
                context=context,originals=originals,branch=branch,plan=plan)
            from .monitor_scheduling_service_v253 import action_pin,KEY as schedule_key
            from .observation_requests import guard as origin_guard
            origin_guard(db,p,plan.data['observation_request'],dispatch=True)
            schedule = action_pin(db,p,policy,plan.data['observation_request'])
            if schedule: plan.data = {**plan.data,schedule_key:copy.deepcopy(schedule)}
            from .sensing_selection import refresh
            refresh(db,p,policy,plan)
            branch[1].data = {**branch[1].data,'observation_plan_id':plan.id,
                'acquisition_ids':[a.id for a in db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
                    m.Acquisition.data['observation_plan_id'].as_string()==plan.id))]}
            db.flush();_inventory(db,p,task.id)
        return row
    except HTTPException as error:
        if error.status_code in {401,403,404}: raise
        db.expire_all();task = _row(db,p,m.Task,task_id,lock=True)
        return _save_command(db,p,identity,task,body,original,raw_hash,request_proof,request_audit,
            context=context,originals=originals,rejection='preparation_failed')
    except (ValueError,KeyError,TypeError,AttributeError):
        db.expire_all();task = _row(db,p,m.Task,task_id,lock=True)
        return _save_command(db,p,identity,task,body,original,raw_hash,request_proof,request_audit,
            context=context,originals=originals,rejection='preparation_failed')


def context(db,p,task_id,gap_id):
    task = _row(db,p,m.Task,task_id)
    rows = _verify_task(db,p,task)
    previous = next((row for row in rows if row.outcome=='accepted' and row.original_gap_id==gap_id and row.actor_id==p.user_id),None)
    if previous:
        return {**copy.deepcopy(previous.data['context']),'can_request':False,'reason':REASONS['already_requested']}
    return _context(db,p,task,gap_id)


def history(db,p,task_id,*,before_id=None,limit=20):
    need(type(limit) is int and 1<=limit<=50,'历史页大小必须为1至50',422)
    task=_row(db,p,m.Task,task_id)
    rows=[row for row in _verify_task(db,p,task) if row.actor_id==p.user_id]
    rows.sort(key=lambda row:(row.created_at,row.id),reverse=True)
    if before_id is not None:
        cursor=next((row for row in rows if row.id==before_id),None)
        need(cursor is not None,'历史游标不属于当前账号与Task',404)
        rows=[row for row in rows if (row.created_at,row.id)<(cursor.created_at,cursor.id)]
    more=len(rows)>limit;page=rows[:limit]
    return {'schema':'goal-conflict-observation-history-v259','workspace_id':p.workspace_id,
        'actor_id':p.user_id,'task_id':task_id,'before_id':before_id,'items':[receipt(row) for row in page],
        'has_more':more,'next_cursor':page[-1].id if more else None}


def recovery(db,p,task_id,request_id,raw_sha256):
    need(type(request_id) is str and 12<=len(request_id)<=100 and '\0' not in request_id and sha(raw_sha256),'原申请号或原文摘要格式无效',422)
    try: request_id.encode('utf-8')
    except UnicodeError: raise HTTPException(422,'原申请号格式无效') from None
    _row(db,p,m.Task,task_id)
    row = _existing(db,p,request_id)
    if row: need(row.task_id==task_id and row.raw_sha256==raw_sha256,'原号已绑定另一份Task或原文')
    return {'schema':'goal-conflict-observation-request-v259','workspace_id':p.workspace_id,'actor_id':p.user_id,
        'task_id':task_id,'request_id':request_id,'raw_sha256':raw_sha256,'found':row is not None,
        'outcome':row.outcome if row else None,'receipt':receipt(row) if row else None,
        'original_raw':row.data['raw'] if row else None}


def detail(db,p,task_id,command_id):
    task = _row(db,p,m.Task,task_id);_verify_task(db,p,task)
    row = _checked(db,p,_row(db,p,Command,command_id))
    need(row.task_id==task_id,'此申请不属于原Task',404)
    plan = _row(db,p,m.ObservationPlan,row.observation_plan_id,internal=True) if row.outcome=='accepted' else None
    actions = list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
        m.Acquisition.data['observation_plan_id'].as_string()==plan.id).limit(2))) if plan else []
    need(len(actions)<=1);action = actions[0] if actions else None
    observations = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id==p.workspace_id,
        m.Observation.data['acquisition_id'].as_string()==action.id).limit(LIMIT+1))) if action else []
    need(len(observations)<=LIMIT)
    received=[];blocked=False
    from .observation_integrity import checked_report
    for obs in observations:
        if obs.status!='processed':continue
        events=list(db.scalars(select(m.Event).where(m.Event.workspace_id==p.workspace_id,
            m.Event.kind=='observation.data',m.Event.idempotency_key=='data:'+obs.id).limit(2)))
        if len(events)>1:blocked=True;continue
        try:report=checked_report(db,p,obs)
        except HTTPException as error:
            if error.status_code in {401,403}:raise
            blocked=True;continue
        if events and events[0].status=='received':
            try:_ack(db,p,obs,report);received.append(obs.id)
            except HTTPException as error:
                if error.status_code in {401,403}:raise
                blocked=True
        elif events and events[0].status=='blocked_delivery':blocked=True
    state = ('blocked_integrity' if blocked else 'not_started' if not plan else 'received' if action and action.status=='succeeded' and received else
        'stopped' if plan.status=='cancelled' else 'waiting_resource' if plan.status=='waiting_resource' else
        'failed' if plan.status=='escalated' or action and action.status in {'failed','rejected','cancelled'} else
        'awaiting_observation' if action and action.status in {'dispatching','unknown','awaiting_observation'} else 'awaiting_dispatch')
    reason = row.data['rejection']['reason'] if row.outcome=='rejected' else str((action.data.get('reason') if action else None) or plan.data.get('reason') or '')
    if state=='received':reason='本次新观测已独立接收；原同刻冲突及Goal仍按原规则待人工核查，未自动充分或验收'
    if blocked:reason='本次新观测的原处理或独立收件依据无法核对；原申请与原件保留，请核对原交付，未重派设备'
    return {'schema':'goal-conflict-observation-detail-v259','receipt':receipt(row),'execution':{
        'command_id':row.id,'task_id':task.id,'branch_query_id':row.branch_query_id,'branch_gap_id':row.branch_gap_id,
        'observation_plan_id':row.observation_plan_id,'plan_status':plan.status if plan else None,
        'acquisition_id':action.id if action else None,'acquisition_status':action.status if action else None,
        'observation_ids':[v.id for v in observations],'received_observation_ids':received,
        'state':state,'reason':reason,'device_requests_limit':1,'old_conflict_resolved':False,'goal_acceptance_automatic':False}}



def check_plan(db,p,plan):
    """Cold plan dispatch discriminator; None only for genuinely old plans."""
    rows = list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,
        Command.observation_plan_id==plan.id,Command.outcome=='accepted').limit(2)))
    proofs = list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==COMPLETED,m.Audit.data['observation_plan_id'].as_string()==plan.id).limit(2)))
    marker = plan.data.get(KEY)
    if not rows:
        need(marker is None and not proofs);return None
    need(len(rows)==1 and len(proofs)==1)
    row = _checked(db,p,rows[0]);need(digest(marker)==digest(row.data['link']))
    return {'command':row,'context':row.data['context'],'link':row.data['link']}


def verify_origin(db,p,value):
    """A new branch can use only its original once-only plan and exact fields."""
    run = _row(db,p,m.DataRun,value['query_id'],internal=True)
    rows = list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,
        Command.branch_query_id==run.id,Command.outcome=='accepted').limit(2)))
    if not rows: need(KEY not in run.data);return None
    need(len(rows)==1);row = _checked(db,p,rows[0])
    need(value['gap_id']==row.branch_gap_id and value['entity_id']==row.data['context']['entity_id']
        and value['fields']==row.data['context']['fields'] and digest(value['request'])==row.data['branch']['gap_request_hash'])
    return row


def adopted_plan(db,p,policy,value):
    row = verify_origin(db,p,value)
    if row is None:return None
    need(row.policy_id==policy.id)
    return _row(db,p,m.ObservationPlan,row.observation_plan_id,lock=True,internal=True)


def lock_requester(db,p,acq):
    """Before Task/Policy locks, pin the requesting admin for new dispatch only."""
    if not acq.data.get('observation_plan_id'):return
    plan = _row(db,p,m.ObservationPlan,acq.data['observation_plan_id'],internal=True)
    bound = check_plan(db,p,plan)
    if bound is None:return
    row = bound['command']
    member = db.execute(select(m.User,m.Membership).join(m.Membership,m.Membership.user_id==m.User.id).where(
        m.User.id==row.actor_id,m.User.active.is_(True),m.Membership.workspace_id==p.workspace_id,
        m.Membership.role=='admin').with_for_update(read=True,of=(m.User,m.Membership))).first()
    need(member is not None,'原明确复测申请人的管理权限已撤回，未下发新的设备动作')


def require_new_action(db,p,plan,*,acquisition=None):
    bound = check_plan(db,p,plan)
    if bound is None:return None
    row = bound['command'];task = _row(db,p,m.Task,row.task_id,internal=True)
    _verify_task(db,p,task)
    from .native_goals import guard
    guard(db,p,task)
    need(task.data.get('native_goal_query_id')==row.branch_query_id)
    allowed = db.scalar(select(m.User.id).join(m.Membership,m.Membership.user_id==m.User.id).where(
        m.User.id==row.actor_id,m.User.active.is_(True),m.Membership.workspace_id==p.workspace_id,m.Membership.role=='admin'))
    need(allowed is not None,'原明确复测申请人的管理权限已撤回，未下发新的设备动作')
    # Current matching matters only before a new HTTP admission. It is never
    # imposed on an already-issued receipt or on collection of its new data.
    context = _context(db,p,task,row.branch_gap_id)
    need(context['conflict_hash']==row.conflict_hash,'原同刻冲突已非当前范围，请先核对原Goal，未新增设备动作')
    _no_newer(db,p,row.data['context'])
    actions = list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
        m.Acquisition.data['observation_plan_id'].as_string()==plan.id).limit(2)))
    need(len(actions)<=1)
    if acquisition is None: need(not actions,'此一次性计划已有原请求，未安排替代采集')
    else: need(len(actions)==1 and actions[0].id==acquisition.id and not acquisition.data.get('dispatch_started_at'))
    return bound


def refresh_owned_gap(db,p,task,run,gap,current):
    """Normal Goal recheck, preserving the old query and its old conflict gap."""
    rows = list(db.scalars(select(Command).where(Command.workspace_id==p.workspace_id,
        Command.branch_query_id==run.id,Command.outcome=='accepted').limit(2)))
    if not rows: need(KEY not in run.data);return False
    need(len(rows)==1);row = _checked(db,p,rows[0])
    need(gap.query_id==run.id and gap.id in row.data['branch']['gap_ids'])
    run.status=current['status']
    from .data_gaps import receipts_ready,record
    if current['status']=='sufficient' and receipts_ready(db,p,current):
        if gap.status!='resolved':
            gap.status='resolved';gap.version+=1
            gap.data={**gap.data,'resolution_reason':'当前原Goal最新资料与独立收件充分；原旧同刻冲突仍保留待核查','resolved_at':m.now().isoformat()}
            record(db,p,gap,'verified',reason=gap.data['resolution_reason'])
    else:
        if gap.status not in {'needs_review','cancelled'}:
            gap.status='needs_review';gap.version+=1
        gap.data={**gap.data,'decision_reason':'明确一次复测进行中或尚未满足原Goal；未重试新设备，旧冲突保留'}
    if gap.id==row.branch_gap_id:
        policy = _row(db,p,m.MonitorPolicy,row.policy_id,lock=True,internal=True)
        plan = _row(db,p,m.ObservationPlan,row.observation_plan_id,lock=True,internal=True)
        from .sensing_selection import refresh
        # Resume only a still-unissued intent; issued/unknown work is reconciled
        # by the existing acquisition path and cannot fall through to a retry.
        actions = list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
            m.Acquisition.data['observation_plan_id'].as_string()==plan.id).limit(2)))
        need(len(actions)<=1)
        if not actions and plan.status not in {'cancelled','completed','escalated'}:refresh(db,p,policy,plan)
        gap.data={**gap.data,'acquisition_ids':[a.id for a in actions],'observation_plan_id':plan.id}
    return True
