"""Read an exact continuation proposal and recheck authority for new work."""
import copy

from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_grant_sources_v313 as source
from . import same_goal_continuation_grant_store_v313 as grants
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope
from .contracts import digest
from .goals import snapshot
from .models import Capability
from .relations import lock_current_actor
from .security import require
from .services import check_version, validate
from .temporal import instant

need, checked = source.need, source.checked


def _budget(db, p, task, execution_plan_id):
    from .same_goal_continuation_execution_budget_v314 import check_new_action
    check_new_action(db, p, task, execution_plan_id=execution_plan_id)


@checked
def policy(db, p, task, ref, *, lock=True):
    current = source._policy(db, p, {'task': task}, lock=lock)
    need(current is not None and digest(current['ref']) == digest(ref),
        '增量办理的审批政策版本已变化，请重新申请授权')
    return current


def _continuation_ref(loaded):
    draft = loaded['continuation']
    return {'id': draft.id, 'version': draft.version, 'revision_hash': loaded['revision']['record_hash'],
        'source_hash': loaded['source_hash']}


def _grant_ref(saved):
    return {'id': saved['plan'].id, 'version': saved['plan'].version,
        'record_hash': saved['grant']['record_hash']}


def _draft_fields(loaded):
    fields = loaded['revision']['draft']
    need(fields['route'] in {None, 'business_conclusion'}
        and all(fields[key].strip() and '\x00' not in fields[key]
            for key in ('purpose', 'work_description', 'expected_result')),
        '请先保存办理目的、具体工作和期望结果')
    return fields


def _checked_grant(db, p, loaded, saved, *, lock, current_data=None):
    task = loaded['task']; grant = saved['grant']; basis = grant['basis']
    at = source._clock(db)
    need(saved['plan'].status == 'active' and saved['plan'].version == 1
        and instant(grant['issued_at']) <= at < instant(grant['expires_at']),
        '工具授权已撤销或过期，请重新申请')
    need(digest(basis['continuation_ref']) == digest(_continuation_ref(loaded))
        and grant['source_hash'] == loaded['source_hash'] == digest(grant['source']),
        '工具授权不属于当前草案版本')
    goal = snapshot(task)
    need(digest(basis['goal']) == digest({'hash': goal['hash'], 'version': goal['version'],
        'snapshot_hash': digest(goal), 'native_context_hash': task.data['native_goal_context_hash']}),
        '原目标版本或执行范围已变化')
    release = loaded['selected']['release']
    package = source.provider._fresh(db, p, Capability, task.package_id)
    need(package.id == release.package_id and package.content_hash == package.validated_hash == digest(package.data)
        and digest(basis['release']) == digest({'id': release.id, 'configuration_hash': release.configuration_hash,
            'package_id': package.id, 'package_hash': package.content_hash,
            'original_graph_hash': loaded['source']['original_graph_hash']}))
    capability = source._cap(db, p, basis['capability_pin']['id'], lock=lock)
    binding = capability.data['execution']['input_bindings']['records']
    need(digest(source._pin(capability)) == digest(basis['capability_pin'])
        and binding == basis['records_binding']
        and digest(capability.data['input_schema']) == basis['input_schema_hash']
        and digest(capability.data['output_schema']) == basis['output_schema_hash'])
    current_policy = policy(db, p, task, basis['policy']['ref'], lock=lock)
    need(digest(current_policy['spec']) == digest(basis['policy']['spec']))
    data = current_data if current_data is not None else source._data(db, p, loaded, lock=lock)
    need(digest(data['evaluation']) == digest(basis['evaluation']),
        '当前正式资料评估与授权时的版本不同，请重新核对数据和授权')
    fields = [field for field in source._fields(db, p, loaded) if field['key'] == basis['field']['key']]
    need(len(fields) == 1 and digest(source._public_field(fields[0])) == digest(basis['field']))
    records, provenance = source._records(loaded, data, binding, fields[0])
    inputs = {'field': fields[0]['key'], 'operation': basis['operation'], 'records': records}
    validate(capability.data['input_schema'], inputs)
    need(digest(inputs) == basis['input_hash'] == digest(basis['inputs'])
        and digest(provenance) == digest(basis['provenance'])
        and data['record']['simulation_only'] is basis['simulation_only']
        and data['record']['test_only'] is basis['test_only'],
        '数据原件或计算输入与授权时不同，请重新核对')
    return {'task': task, 'draft': loaded['continuation'], 'grant_plan': saved['plan'],
        'grant': grant, 'policy': current_policy, 'capability': capability}


def _saved_grants(db, p, loaded):
    return grants._inventory(db, p, loaded['task'], loaded['source']['advice_plan_id'],
        loaded['continuation'].id)


def _select(saved, loaded, ref):
    row = grants._find(saved, ref['id'], loaded['source']['advice_plan_id'], loaded['continuation'].id)
    need(digest(_grant_ref(row)) == digest(ref), '工具授权版本已变化')
    return row


def _execution_inventory(db, p, task):
    from .same_goal_continuation_execution_store_v314 import _inventory
    return _inventory(db, p, task)


def _replacement_phase(db, p, row, at):
    plan, job = row['plan'], row['job']
    if job is not None and job.lease_until is not None and job.lease_until > at:
        return None
    if plan.status in {'returned', 'result_returned', 'cancelled'}:
        return plan.status
    if plan.status != 'queued' or job is None or job.status != 'failed':
        return None
    from .same_goal_continuation_execution_store_v314 import _invocations
    calls = _invocations(db, p, row)
    # A saved successful output belongs to original-number recovery. An
    # unresolved invocation cannot be abandoned by creating a correction.
    if any(call.status != 'failed' for call in calls):
        return None
    return 'failed'


def _replacement_scope(row, loaded, basis):
    original = row['proposal']; old_basis = original['grant']['basis']
    return (row['plan'].task_id == loaded['task'].id == original['task_id']
        and original['advice_plan_id'] == loaded['source']['advice_plan_id']
        and original['continuation_ref']['id'] == loaded['continuation'].id
        and original['source_hash'] == loaded['source_hash']
        and digest(old_basis['goal']) == digest(basis['goal']))


def _replacement(db, p, loaded, grant, identity, *, current_plan_id=None):
    if identity is None:
        return
    rows = _execution_inventory(db, p, loaded['task'])
    index = {row['plan'].id: row for row in rows}
    need(len(index) == len(rows))
    children = [row['plan'].id for row in rows
        if row['proposal']['supersedes_plan_id'] == identity]
    need(not children or children == [current_plan_id],
        '该退回记录已有后续方案，请继续办理后续方案')
    visited = {current_plan_id}
    at = source._clock(db)
    target = identity
    while target is not None:
        need(target not in visited and target in index, '替代方案关联无效或存在循环')
        visited.add(target)
        previous = index[target]
        need(_replacement_scope(previous, loaded, grant['basis'])
            and _replacement_phase(db, p, previous, at) is not None,
            '原办理记录范围不符或尚未结束，不能发起替代方案')
        if previous['start'] is not None:
            need(previous['proposal']['grant_ref']['id'] != grant['grant_id'],
                '原工具授权已有一次执行预留，请申请新的授权')
        target = previous['proposal']['supersedes_plan_id']


@checked
@authored_goal_binding_parse_scope()
def build_proposal(db, p, task_id, advice_id, continuation_id, body, *, lock=False):
    require(p, 'admin', 'operator')
    body = body.model_dump(mode='json') if isinstance(body, c.PreviewRequest) else body
    model = c.SubmitRequest if 'preview_hash' in body else c.PreviewRequest
    value = c.dump(model, body)
    with db.no_autoflush:
        source.drafts._reader(db, p, write=True)
        loaded = source._base(db, p, task_id, advice_id, continuation_id, lock=lock)
        task = loaded['task']; draft = loaded['continuation']
        if lock:
            lock_current_actor(db, p)
        business_actor = draft.data['business_actor_id']
        need(p.role == 'admin' or p.user_id == business_actor, '请由原业务岗位或管理员提交办理方案')
        check_version(task, value['expected_task_version'])
        check_version(draft, value['expected_continuation_version'])
        need(loaded['source_hash'] == value['source_hash']
            and loaded['revision']['record_hash'] == value['revision_hash'])
        fields = _draft_fields(loaded)
        source._current(db, p, loaded, lock=lock)
        saved = _select(_saved_grants(db, p, loaded), loaded, value['grant_ref'])
        actual = _checked_grant(db, p, loaded, saved, lock=lock)
        _replacement(db, p, loaded, actual['grant'], value['supersedes_plan_id'])
        _budget(db, p, task, None)
        proposal = {'schema': c.PROPOSAL, 'workspace_id': p.workspace_id, 'task_id': task.id,
            'advice_plan_id': advice_id, 'continuation_ref': _continuation_ref(loaded),
            'grant_ref': value['grant_ref'], 'grant': copy.deepcopy(actual['grant']),
            'supersedes_plan_id': value['supersedes_plan_id'],
            'source_hash': loaded['source_hash'], 'source': copy.deepcopy(loaded['source']),
            'draft': copy.deepcopy(loaded['revision']), 'plan_origin': value['plan_origin'],
            'rationale': value['rationale'], 'original_candidate_ref': fields['candidate_ref'],
            'submitter_id': p.user_id, 'business_actor_id': business_actor, 'resources': dict(c.RESOURCES)}
        proposal['record_hash'] = c.record_hash(proposal)
        if model is c.SubmitRequest:
            need(value['preview_hash'] == proposal['record_hash'], '提案与已核对的预览不同，请重新预览')
        return {key: actual[key] for key in ('task', 'draft', 'grant_plan', 'grant')} | {
            'proposal': c.check_proposal(proposal), 'preview_hash': proposal['record_hash']}


@checked
@authored_goal_binding_parse_scope()
def live(db, p, task, proposal, execution_plan_id=None, lock=True):
    require(p, 'admin', 'operator')
    value = c.check_proposal(proposal)
    need(value['task_id'] == task.id and value['workspace_id'] == task.workspace_id == p.workspace_id)
    with db.no_autoflush:
        source.drafts._reader(db, p, write=True)
        loaded = source._base(db, p, task.id, value['advice_plan_id'], value['continuation_ref']['id'], lock=lock)
        if lock:
            lock_current_actor(db, p)
        need(digest(_continuation_ref(loaded)) == digest(value['continuation_ref'])
            and digest(loaded['revision']) == digest(value['draft'])
            and digest(loaded['source']) == digest(value['source'])
            and loaded['continuation'].data['business_actor_id'] == value['business_actor_id'])
        source._current(db, p, loaded, lock=lock)
        saved = _select(_saved_grants(db, p, loaded), loaded, value['grant_ref'])
        need(digest(saved['grant']) == digest(value['grant']))
        actual = _checked_grant(db, p, loaded, saved, lock=lock)
        _replacement(db, p, loaded, actual['grant'], value['supersedes_plan_id'],
            current_plan_id=execution_plan_id)
        _budget(db, p, actual['task'], execution_plan_id)
        return actual


def proposal_view(proposal):
    value = c.check_proposal(proposal); basis = value['grant']['basis']
    return {'schema': c.PREVIEW, 'task_id': value['task_id'], 'advice_plan_id': value['advice_plan_id'],
        'continuation_id': value['continuation_ref']['id'], 'continuation_ref': value['continuation_ref'],
        'grant_ref': value['grant_ref'], 'preview_hash': value['record_hash'], 'draft': value['draft']['draft'],
        'supersedes_plan_id': value['supersedes_plan_id'],
        'rationale': value['rationale'], 'plan_origin': value['plan_origin'],
        'original_candidate_ref': value['original_candidate_ref'], 'field': basis['field'],
        'operation': basis['operation'], 'record_count': len(basis['records']),
        'missing_count': sum(row['attributes'].get(basis['field']['key']) is None for row in basis['records']),
        'data_as_of': basis['evaluation']['cut_at'], 'simulation_only': basis['simulation_only'],
        'test_only': basis['test_only'], 'policy_ref': basis['policy']['ref'], 'resources': value['resources']}


@checked
@authored_goal_binding_parse_scope()
def context(db, p, task_id, advice_id, continuation_id):
    with db.no_autoflush:
        loaded = source._base(db, p, task_id, advice_id, continuation_id)
        task = loaded['task']; draft = loaded['continuation']; blocks = []
        writer = p.role == 'admin' or p.role == 'operator' and p.user_id == draft.data['business_actor_id']
        if not writer:
            blocks.append({'code': 'submitter_required', 'message': '请由原业务岗位或管理员提交办理方案'})
        inventory = _saved_grants(db, p, loaded)
        scoped = [row for row in inventory if row['grant']['continuation_id'] == draft.id
            and row['grant']['advice_plan_id'] == advice_id]
        data = None
        if writer and any(row['plan'].status == 'active' for row in scoped):
            def current_data():
                _draft_fields(loaded)
                source._current(db, p, loaded, lock=False)
                _budget(db, p, task, None)
                return source._data(db, p, loaded)
            data = source._availability(blocks, 'execution_unavailable', '当前草案、目标或资料尚未满足提交条件', current_data)
        choices = []
        for row in scoped:
            view = grants._view(db, p, row)
            available = None
            if data is not None and row['plan'].status == 'active' and not view['expired']:
                available = source._availability(blocks, 'grant_unavailable', '工具授权与当前草案、资料或政策不一致',
                    lambda: _checked_grant(db, p, loaded, row, lock=False, current_data=data))
            choices.append({**view, 'eligible': available is not None})
        allowed = any(row['eligible'] for row in choices)
        if not allowed and not blocks:
            blocks.append({'code': 'grant_required', 'message': '请先为当前草案申请有效的只读工具授权'})
        executions = _execution_inventory(db, p, task)
        replaced = {row['proposal']['supersedes_plan_id'] for row in executions}
        goal = snapshot(task)
        goal_pin = {'hash': goal['hash'], 'version': goal['version'], 'snapshot_hash': digest(goal),
            'native_context_hash': task.data['native_goal_context_hash']}
        at = source._clock(db)
        candidates = []
        labels = {'returned': '审批退回', 'result_returned': '结果退回',
            'cancelled': '已取消', 'failed': '执行失败'}
        for row in executions:
            plan_id = row['plan'].id
            if plan_id in replaced or not _replacement_scope(row, loaded, {'goal': goal_pin}):
                continue
            phase = _replacement_phase(db, p, row, at)
            if phase is not None:
                candidates.append({'plan_id': plan_id, 'phase': phase, 'label': labels[phase]})
        return {'schema': c.CONTEXT, 'task_id': task.id, 'advice_plan_id': advice_id,
            'continuation_id': draft.id, 'task_version': task.version, 'continuation_version': draft.version,
            'revision_hash': loaded['revision']['record_hash'], 'source_hash': loaded['source_hash'],
            'draft': copy.deepcopy(loaded['revision']['draft']), 'grants': choices,
            'returned_candidates': candidates,
            'permissions': {'can_preview': allowed, 'can_submit': allowed}, 'blocked_reasons': blocks, **c.FLAGS}
