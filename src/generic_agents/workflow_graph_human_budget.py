"""Independently approved allowance for exact human activations in a frozen graph."""
import copy
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import or_, select

from . import layered_approval_store_v175 as approval
from .contracts import digest
from .goals import snapshot
from .layered_approval_contract_v175 import Closed, ID, SHA
from .models import Audit, uid
from .relations import lock_current_actor
from .security import require
from .services import check_version

INDEX = 'graph_human_budget_v1'
VIEW = 'workflow-graph-human-budget-view-v1'
USE = 'workflow-graph-human-budget-use-v1'
SUBMITTED = 'workflow.graph.human_budget_submitted'
DECIDED = 'workflow.graph.human_budget_decided'
COMPLETED = 'workflow.graph.human_completed'
MAX_COMMANDS = 128
MAX_PATH = 12
ACTIVE = frozenset({'ready', 'waiting_guard', 'waiting_event', 'waiting_join'})
need = approval.need


class Submit(Closed):
    request_id: ID
    expected_task_version: StrictInt = Field(ge=1)
    path_hash: SHA
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('请填写人工步骤补充额度的办理依据')
        return value


class Decide(Closed):
    request_id: ID
    expected_task_version: StrictInt = Field(ge=1)
    submission_hash: SHA
    decision: Literal['approve', 'return']
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        return Submit.meaningful(value)


def _rows(db, p, *where):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, *where)
        .order_by(Audit.id).limit(MAX_COMMANDS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_COMMANDS, '人工步骤补充额度历史超过核对上限')
    return rows


def _ref(event):
    return {'id': event.id, 'record_hash': digest(event.data), 'request_id': event.data['request']['request_id']}


def _index(task):
    refs = task.data.get(INDEX, [])
    need(type(refs) is list and len(refs) <= MAX_COMMANDS)
    need(all(type(ref) is dict and set(ref) == {'id', 'record_hash', 'request_id'}
        and all(type(ref[key]) is str for key in ref) for ref in refs))
    need(len({ref['id'] for ref in refs}) == len({ref['request_id'] for ref in refs}) == len(refs))
    return refs


def nonce_record(task, request_id):
    return next((copy.deepcopy(ref) for ref in _index(task) if ref['request_id'] == str(request_id)), None)


def _pin(task):
    state = task.data['graph_runtime']
    goal = snapshot(task)
    return {'goal_hash': goal['hash'], 'goal_version': goal['version'], 'goal_snapshot_hash': digest(goal),
        'goal_context_hash': task.data['native_goal_context_hash'], 'release_id': task.data['instance_release_id'],
        'graph_hash': state['graph_hash'], 'cancel_epoch': state['cancel_epoch']}


def _policy(db, p, task, *, lock):
    managed = approval.managed(db, p, task)
    need(managed is not None, '原任务没有可复用的正式分级审批政策')
    current = approval.current_policy(db, p, managed.policy_id, lock_sources=lock)
    version, spec = approval.policy_value(db, p, current)
    need(current.instance_id == task.data['instance_id'] and current.business_id == task.data['business_id'])
    return {'ref': {'id': version.id, 'record_hash': digest(version.data)}, 'spec': spec}


def _path(task, graph):
    """Only a single actual ready human and its direct success path qualify."""
    state = task.data['graph_runtime']
    need(state['graph_hash'] == digest(graph), '原流程图版本无法核对')
    ready = state['ready_human_ids']
    active = [identity for identity, item in state['activations'].items() if item['status'] in ACTIVE]
    if len(ready) != 1 or active != ready or state['eligible_ability_ids']:
        return []
    current = state['activations'][ready[0]]
    if current['status'] != 'ready' or current['scope_id'] is not None or current['branch_id'] is not None:
        return []
    nodes = {node['id']: node for node in graph['nodes']}
    node = nodes[current['node_id']]
    identity = current['id']
    next_id = state['next_activation']
    path, visited = [], set()
    while node['kind'] == 'human':
        if node['id'] in visited or len(path) >= MAX_PATH or identity in {item['activation_id'] for item in path}:
            return []
        visited.add(node['id'])
        path.append({'activation_id': identity, 'node_id': node['id'], 'role': node['role']})
        edges = [edge for edge in graph['edges'] if edge['from'] == node['id']]
        if len(edges) != 1 or edges[0]['on'] != 'success' or edges[0]['branch_id'] is not None or edges[0]['loop_id'] is not None:
            return []
        node = nodes[edges[0]['to']]
        identity = 'a' + str(next_id)
        if identity in state['activations']:
            return []
        next_id += 1
    return path if node['kind'] == 'end' else []


def _proposal(db, p, task, graph):
    from .native_goals import total_steps
    path = _path(task, graph)
    if not path:
        return None
    remaining = max(0, task.data['goal_spec']['max_steps'] - total_steps(db, p, task))
    extra = [item['activation_id'] for item in path[remaining:]]
    if not extra:
        return None
    actors = {node['id']: node['actor_id'] for node in graph['nodes'] if node['kind'] == 'human'}
    return {'pin': _pin(task), 'path': path, 'extra_activation_ids': extra,
        'actors': {item['activation_id']: actors[item['node_id']] for item in path}}


def _command(event, task):
    value = event.data
    need(type(value) is dict and set(value) == {'schema', 'task_id', 'actor_id', 'request',
        'request_hash', 'detail', 'record_hash'} and value['schema'] == 'workflow-graph-human-budget-command-v1'
        and event.object_id == value['task_id'] == task.id and event.actor_id == value['actor_id']
        and value['record_hash'] == digest({key: item for key, item in value.items() if key != 'record_hash'}))
    model = Submit if event.action == SUBMITTED else Decide
    try:
        request = model.model_validate(value['request']).model_dump(mode='json')
    except (ValueError, TypeError):
        raise HTTPException(409, '原补充额度命令格式无法核对') from None
    need(digest(request) == digest(value['request']) == value['request_hash'])
    return value


def _inventory(db, p, task):
    refs = _index(task)
    rows = _rows(db, p, Audit.action.in_([SUBMITTED, DECIDED]),
        or_(Audit.object_id == task.id, Audit.data['task_id'].as_string() == task.id,
            Audit.id.in_([ref['id'] for ref in refs])))
    index = {event.id: event for event in rows}
    need(len(rows) == len(refs) == len(index), '补充额度命令与独立审计记录不一致')
    records = {}
    for ref in refs:
        need(ref['id'] in index)
        event = index[ref['id']]
        value = _command(event, task)
        need(_ref(event) == ref)
        detail = value['detail']
        if event.action == SUBMITTED:
            need(set(detail) == {'proposal', 'policy'} and value['record_hash'] not in records
                and not any(row['status'] in {'in_review', 'approved'} for row in records.values()))
            proposal = detail['proposal']
            pin = _pin(task)
            saved_epoch = proposal['pin']['cancel_epoch']
            need(set(proposal) == {'pin', 'path', 'extra_activation_ids', 'actors'}
                and digest(proposal) == value['request']['path_hash']
                and type(saved_epoch) is int and 0 <= saved_epoch <= pin['cancel_epoch']
                and digest(proposal['pin']) == digest({**pin, 'cancel_epoch': saved_epoch}))
            path = proposal['path']; extra = proposal['extra_activation_ids']
            need(type(path) is list and 0 < len(path) <= MAX_PATH
                and all(type(item) is dict and set(item) == {'activation_id', 'node_id', 'role'} for item in path)
                and len({item['activation_id'] for item in path}) == len(path)
                and type(proposal['actors']) is dict
                and set(proposal['actors']) == {item['activation_id'] for item in path}
                and all(type(actor) is str and bool(actor) for actor in proposal['actors'].values())
                and type(extra) is list and 0 < len(extra) <= len(path)
                and extra == [item['activation_id'] for item in path[-len(extra):]])
            policy = detail['policy']
            need(set(policy) == {'ref', 'spec'} and type(policy['spec']['stages']) is list
                and bool(policy['spec']['stages']))
            records[value['record_hash']] = {'submission': value, 'status': 'in_review', 'decisions': []}
            continue
        identity = value['request']['submission_hash']
        need(identity in records)
        row = records[identity]
        stage = len(row['decisions'])
        stages = row['submission']['detail']['policy']['spec']['stages']
        need(row['status'] == 'in_review' and stage < len(stages) and set(detail) == {'stage', 'role_ref'}
            and type(detail['stage']) is int and detail['stage'] == stage
            and value['actor_id'] not in {task.owner_id, row['submission']['actor_id'],
                *(item['actor_id'] for item in row['decisions'])})
        row['decisions'].append(value)
        if value['request']['decision'] == 'return':
            row['status'] = 'returned'
        elif len(row['decisions']) == len(stages):
            row['status'] = 'approved'
    return records


def _uses(db, p, task, records):
    receipts = task.data.get('workflow_runtime', {}).get('receipts', [])
    used = [receipt for receipt in receipts if receipt.get('human_budget') is not None]
    audits = _rows(db, p, Audit.action == COMPLETED, Audit.object_id == task.id,
        Audit.data['human_budget'].as_string().is_not(None))
    need(len(used) == len(audits))
    events = {event.data['activation_id']: event for event in audits}
    seen, used_by_submission = {}, {}
    for receipt in used:
        proof = receipt['human_budget']
        need(type(proof) is dict and set(proof) == {'schema', 'submission_hash', 'activation_id', 'node_id'}
            and proof['schema'] == USE and proof['submission_hash'] in records
            and proof['activation_id'] == receipt['activation_id'] not in seen)
        row = records[proof['submission_hash']]
        proposal = row['submission']['detail']['proposal']
        activation = task.data['graph_runtime']['activations'].get(proof['activation_id'])
        event = events.get(proof['activation_id'])
        need(row['status'] == 'approved' and proof['activation_id'] in proposal['extra_activation_ids']
            and any(item['activation_id'] == proof['activation_id'] and item['node_id'] == proof['node_id']
                for item in proposal['path'])
            and activation is not None and activation['node_id'] == proof['node_id']
            and activation['status'] == 'completed' and activation['epoch'] == proposal['pin']['cancel_epoch']
            and digest(activation['output']) == digest(receipt['output'])
            and receipt['execution_mode'] == 'human_graph_result' and event is not None
            and event.actor_id == receipt['actor'] == proposal['actors'][proof['activation_id']]
            and event.data == {'activation_id': proof['activation_id'],
                'human_budget': proof, 'receipt_hash': digest(receipt), 'request_id': receipt['request_id']},
            '人工步骤额度使用凭证无法核对')
        seen[proof['activation_id']] = {'audit_id': event.id, 'receipt_hash': digest(receipt)}
        used_by_submission.setdefault(proof['submission_hash'], []).append(proof['activation_id'])
    need(len(seen) == len(events))
    for identity, row in records.items():
        if row['status'] != 'approved':
            continue
        proposal = row['submission']['detail']['proposal']
        extra = proposal['extra_activation_ids']
        recorded = used_by_submission.get(identity, [])
        need(recorded == extra[:len(recorded)])
        activations = task.data['graph_runtime']['activations']
        for activation_id in extra:
            activation = activations.get(activation_id)
            if activation is not None and activation['status'] == 'completed':
                need(activation_id in recorded, '已完成补充额度节点缺少独立使用凭证')
    return seen


def _checked_inventory(db, p, task):
    try:
        records = _inventory(db, p, task)
        return records, _uses(db, p, task, records)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise HTTPException(409, '原人工步骤额度或使用记录无法核对') from None


def verify(db, p, task):
    records, uses = _checked_inventory(db, p, task)
    return digest({'commands': _index(task), 'uses': uses}) if records else None


def _available(check):
    try:
        check()
        return True
    except HTTPException as error:
        if error.status_code not in {403, 404, 409, 422}:
            raise
        return False


def _stage_role(db, p, task, row, *, lock):
    submission = row['submission']
    need(p.user_id not in {task.owner_id, submission['actor_id'], *(item['actor_id'] for item in row['decisions'])},
        '请由本级独立责任人员审批', 403)
    policy = _policy(db, p, task, lock=lock)
    need(digest(policy) == digest(submission['detail']['policy']), '原审批政策已变化')
    stage = len(row['decisions'])
    return approval.role_allowed(db, p, policy['spec']['stages'][stage], lock_sources=lock)


def read(db, p, task, graph):
    records, _ = _checked_inventory(db, p, task)
    proposal = _proposal(db, p, task, graph)
    pending = next((row for row in records.values() if row['status'] == 'in_review'), None)
    writer = p.role in {'admin', 'operator'}
    can_submit = bool(writer and proposal and (p.role == 'admin' or p.user_id == task.owner_id)
        and not any(row['status'] in {'in_review', 'approved'} for row in records.values())
        and _available(lambda: _policy(db, p, task, lock=False)))
    can_decide = bool(writer and pending and _available(lambda: _stage_role(db, p, task, pending, lock=False)))
    return {'schema': VIEW, 'task_id': task.id, 'task_version': task.version,
        'path': proposal['path'] if proposal else [], 'path_hash': digest(proposal) if proposal else None,
        'records': [{'submission_hash': identity, 'status': row['status'],
            'stage': sum(item['request']['decision'] == 'approve' for item in row['decisions']),
            'path': row['submission']['detail']['proposal']['path'], 'reason': row['submission']['request']['reason']}
            for identity, row in records.items()],
        'permissions': {'can_submit': can_submit, 'can_decide': can_decide}}


def _body(model, body):
    return (body if isinstance(body, model) else model.model_validate(body)).model_dump(mode='json')


def _replay(db, p, task, request, action):
    ref = nonce_record(task, request['request_id'])
    if ref is None:
        return False
    events = _rows(db, p, Audit.id == ref['id'])
    need(len(events) == 1 and events[0].action == action and events[0].actor_id == p.user_id)
    need(digest(request) == _command(events[0], task)['request_hash'],
        '请求编号已用于其他补充额度正文')
    return True


def _save(db, p, task, request, detail, action):
    refs = _index(task)
    need(len(refs) < MAX_COMMANDS)
    value = {'schema': 'workflow-graph-human-budget-command-v1', 'task_id': task.id,
        'actor_id': p.user_id, 'request': request, 'request_hash': digest(request), 'detail': detail}
    value['record_hash'] = digest(value)
    event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=action, object_id=task.id, data=value)
    db.add(event)
    task.data = {**task.data, INDEX: [*refs, _ref(event)]}
    task.version += 1
    db.flush()


def _prepare(db, p, task, request):
    require(p, 'admin', 'operator')
    lock_current_actor(db, p)
    records, _ = _checked_inventory(db, p, task)
    request_id = request['request_id']
    need(not any(row['request_id'] == request_id for row in task.data['graph_runtime']['commands'])
        and all(request_id not in task.data.get(key, {}) for key in (
            'graph_retry_requests', 'graph_wait_retries', 'graph_simulation_reviews_v202')),
        '该请求编号已用于原流程图命令')
    return records


def submit(db, p, task, graph, body):
    from .native_goals import guard
    request = _body(Submit, body)
    records = _prepare(db, p, task, request)
    if _replay(db, p, task, request, SUBMITTED):
        return read(db, p, task, graph)
    check_version(task, request['expected_task_version'])
    need(p.role == 'admin' or p.user_id == task.owner_id, '请由原任务负责人提交', 403)
    guard(db, p, task)
    need(not any(row['status'] in {'in_review', 'approved'} for row in records.values()))
    proposal = _proposal(db, p, task, graph)
    need(proposal is not None and digest(proposal) == request['path_hash'], '剩余人工路径或步骤用量已变化')
    _save(db, p, task, request, {'proposal': proposal, 'policy': _policy(db, p, task, lock=True)}, SUBMITTED)
    return read(db, p, task, graph)


def decide(db, p, task, graph, body):
    from .native_goals import guard
    request = _body(Decide, body)
    records = _prepare(db, p, task, request)
    if _replay(db, p, task, request, DECIDED):
        return read(db, p, task, graph)
    check_version(task, request['expected_task_version'])
    row = records.get(request['submission_hash'])
    need(row is not None and row['status'] == 'in_review')
    if request['decision'] == 'approve':
        guard(db, p, task)
        proposal = _proposal(db, p, task, graph)
        need(proposal is not None and digest(proposal) == row['submission']['request']['path_hash'],
            '剩余人工路径或步骤用量已变化，请退回原申请重新核对')
    role = _stage_role(db, p, task, row, lock=True)
    _save(db, p, task, request, {'stage': len(row['decisions']),
        'role_ref': {'id': role.id, 'record_hash': digest(role.data)}}, DECIDED)
    return read(db, p, task, graph)


def authorize(db, p, task, activation_id, *, steps=None, lock_sources=True):
    """Return the proof reserved for this exact activation, if one exists."""
    need(type(lock_sources) is bool and (steps is None or type(steps) is int and steps >= 0))
    records, uses = _checked_inventory(db, p, task)
    approved = [(identity, row) for identity, row in records.items() if row['status'] == 'approved']
    if not approved:
        return None
    identity, row = approved[0]
    submission = row['submission']
    proposal = submission['detail']['proposal']
    state = task.data['graph_runtime']
    remaining = [key for key in proposal['extra_activation_ids'] if key not in uses]
    if not remaining or activation_id != remaining[0] or activation_id not in state['ready_human_ids']:
        return None
    activation = state['activations'][activation_id]
    entry = next(item for item in proposal['path'] if item['activation_id'] == activation_id)
    need(activation['node_id'] == entry['node_id'] and activation['status'] == 'ready'
        and activation['scope_id'] is None and activation['branch_id'] is None
        and activation['epoch'] == state['cancel_epoch'] == proposal['pin']['cancel_epoch'])
    need(p.user_id == proposal['actors'][activation_id], '请由原流程图为此节点指定的人员办理', 403)
    policy = _policy(db, p, task, lock=lock_sources)
    need(digest(policy) == digest(submission['detail']['policy']), '原审批政策已变化')
    for stage, decision in zip(policy['spec']['stages'], row['decisions']):
        role = approval.role_allowed(db, p, stage, actor_id=decision['actor_id'], lock_sources=lock_sources)
        need(decision['detail']['role_ref'] == {'id': role.id, 'record_hash': digest(role.data)})
    return {'schema': USE, 'submission_hash': identity, 'activation_id': activation_id,
        'node_id': entry['node_id']}
