"""Task-bound layered authority. No provider or device call is made here.

Every state-changing command, audit and Task update belongs to the caller's
transaction. Historical receipts are checked before current write permission.
"""
import copy
import hashlib
import json
from contextlib import contextmanager
from sqlalchemy import select, text, func
from fastapi import HTTPException
from .contracts import digest
from .models import (Task, DomainInstance, InstanceRelease, User, Membership,
    Audit, Invocation, Memory, Job, Plan, now, uid)
from .services import scoped, require, check_version
from .relations import lock_current_actor
from .layered_approval_models_v175 import (ApprovalUnitV175 as Unit,
    ApprovalRoleV175 as Role, ApprovalPolicyV175 as Policy,
    ApprovalPolicyVersionV175 as PolicyVersion, ApprovalManagedTaskV175 as Managed,
    ApprovalSubmissionV175 as Submission, ApprovalCommandV175 as Command)
from .layered_approval_contract_v175 import next_state, independent, content, PolicyCommand
from .report_text_parse_scope import report_text_parse_scope

SCHEMA = 'layered-approval-v175'
STATES = {'draft', 'in_review', 'returned', 'withdrawn', 'approved', 'executing',
    'acceptance_returned', 'accepted'}
CONTEXT_KEYS = ('instance_id', 'instance_release_id', 'business_id', 'entity_ids',
    'object_snapshots', 'object_definition_bindings', 'object_definition_bindings_hash',
    'material_snapshots', 'image_snapshots', 'scene_snapshots', 'comparison_scene_snapshots',
    'comparison_confirmed', 'audio_snapshots', 'sound_snapshots', 'pixel_pair_snapshot', 'perception_signal', 'perception_event',
    'evidence_assessment', 'business_advice', 'creation_hash', 'execution_mode')


def need(value, message='审批原记录、版本或执行依据无法核对；历史与原请求保留', code=409):
    if not value:
        raise HTTPException(code, message)


def seal(row, value):
    body = copy.deepcopy(value)
    row.data = {**body, 'record_hash': digest(body)}


def checked(row):
    need(row is not None and type(row.data) is dict)
    body = {k: v for k, v in row.data.items() if k != 'record_hash'}
    need(row.data.get('record_hash') == digest(body))
    return copy.deepcopy(body)


def authority(row):
    """Authority including anchored actor enrollment; transport progress is separate."""
    value = checked(row)
    return {'id': row.id, 'workspace_id': row.workspace_id, 'task_id': row.task_id,
        'policy_id': row.policy_id, 'policy_version_id': row.policy_version_id,
        'owner_id': row.owner_id, 'status': row.status, 'version': row.version,
        'data': {k: v for k, v in value.items()
            if k not in {'command_ids', 'dispatches'}}}


def public_result(value):
    return {k: copy.deepcopy(v) for k, v in value.items()
        if k not in {'approval_state', 'approval_state_hash', 'previous_state_hash'}}


@contextmanager
def initializing(db, row, previous_state_hash):
    # Only a server-owned, uncommitted explicit-start transaction may expose
    # its interim state to goal/graph schedulers before recording the command.
    key = 'layered_approval_initializing_v175'
    need(key not in db.info)
    db.info[key] = {'managed_id': row.id, 'state_hash': digest(authority(row)),
        'previous_state_hash': previous_state_hash}
    try:
        yield
    finally:
        db.info.pop(key, None)


def nonce_lock(db, p, request_id):
    key = int(digest([SCHEMA, p.workspace_id, request_id])[:16], 16)
    if key >= 2 ** 63:
        key -= 2 ** 64
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def checked_command(db, row):
    value = checked(row)
    need(set(value) == {'schema', 'result', 'result_hash', 'audit_id', 'audit_hash'}
        and value['schema'] == 'layered-approval-command-v175'
        and hashlib.sha256(row.exact_body.encode('utf-8')).hexdigest() == row.body_sha256
        and digest(value['result']) == value['result_hash'])
    audit = db.get(Audit, value['audit_id'])
    expected = {'command_id': row.id, 'request_id': row.request_id,
        'operation': row.operation, 'subject_id': row.subject_id,
        'body_sha256': row.body_sha256, 'result_hash': value['result_hash']}
    need(audit is not None and audit.workspace_id == row.workspace_id
        and audit.actor_id == row.actor_id and audit.action == 'layered_approval.command'
        and audit.object_id == row.subject_id and audit.data == expected
        and digest(expected) == value['audit_hash'])
    return copy.deepcopy(value['result'])


def prior(db, p, operation, subject_id, request_id, raw, sha):
    nonce_lock(db, p, request_id)
    row = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id))
    if row is None:
        return None
    need(row.actor_id == p.user_id, '请由原操作账号核对这项审批请求', 403)
    need(row.operation == operation and row.subject_id == subject_id
        and row.exact_body == raw and row.body_sha256 == sha,
        '同一原请求不能更换操作、任务、账号或正文')
    return public_result(checked_command(db, row))


def record(db, p, operation, subject_id, request_id, raw, sha, result, managed=None,
        previous_state_hash=None):
    result = copy.deepcopy(result)
    command_id = uid()
    if managed is not None:
        need(previous_state_hash is not None)
        data = checked(managed)
        data['command_ids'].append(command_id)
        seal(managed, data)
        state = authority(managed)
        result = {**result, 'approval_state': state, 'approval_state_hash': digest(state),
            'previous_state_hash': previous_state_hash}
    row = Command(id=command_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=request_id, operation=operation, subject_id=subject_id,
        exact_body=raw, body_sha256=sha, data={})
    evidence = {'command_id': row.id, 'request_id': request_id, 'operation': operation,
        'subject_id': subject_id, 'body_sha256': sha, 'result_hash': digest(result)}
    audit = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action='layered_approval.command', object_id=subject_id, data=evidence)
    seal(row, {'schema': 'layered-approval-command-v175', 'result': result,
        'result_hash': digest(result), 'audit_id': audit.id, 'audit_hash': digest(evidence)})
    db.add_all([row, audit])
    db.flush()
    return public_result(result)


def request_result(db, p, request_id, sha):
    row = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id))
    need(row is not None, '尚未找到原审批回执；不能据此认定操作已完成', 404)
    need(row.actor_id == p.user_id, '请由原操作账号核对这项审批请求', 403)
    need(row.body_sha256 == sha, '原审批正文摘要不同，原请求保留')
    return {'schema': 'layered-approval-original-result-v175', 'request_id': request_id,
        'operation': row.operation, 'subject_id': row.subject_id,
        'body_sha256': sha, 'result': public_result(checked_command(db, row))}


def unit(db, p, identity, *, current=True, lock_sources=True):
    query = select(Unit).where(Unit.id == identity,
        Unit.workspace_id == p.workspace_id).execution_options(populate_existing=True)
    if lock_sources:
        query = query.with_for_update(read=True)
    row = db.scalar(query)
    need(row is not None, '组织范围不存在')
    data = checked(row)
    need(data == {'schema': 'approval-unit-v175', 'name': row.name,
        'parent_id': row.parent_id, 'enabled': row.enabled})
    if current:
        need(row.enabled, '所需组织范围已停用，原任务与历史保留')
    return row


def ancestors(db, p, identity, *, current=True, lock_sources=True):
    values = []
    while identity is not None:
        need(identity not in values and len(values) < 100, '组织范围层级存在循环或过深')
        values.append(identity)
        identity = unit(db, p, identity, current=current, lock_sources=lock_sources).parent_id
    return values


def real_member(db, p, identity, *, lock_sources=True):
    query = select(User).join(Membership, Membership.user_id == User.id).where(
        User.id == identity, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator']))
    if lock_sources:
        query = query.with_for_update(read=True, of=[User, Membership])
    row = db.scalar(query)
    need(row is not None, '请选择当前工作区内仍有办理权限的真实账号', 403)
    return row


def role_allowed(db, p, stage, *, actor_id=None, lock_sources=True):
    require(p, 'admin', 'operator')
    actor_id = actor_id or p.user_id
    paths = ancestors(db, p, stage['scope_id'], lock_sources=lock_sources)
    query = select(Role).where(Role.workspace_id == p.workspace_id,
        Role.user_id == actor_id, Role.role_key == stage['role_key'],
        Role.unit_id.in_(paths), Role.enabled.is_(True)).order_by(Role.id)
    if lock_sources:
        query = query.with_for_update(read=True)
    row = db.scalar(query)
    need(row is not None, '当前账号不具备本级业务角色或组织范围授权', 403)
    value = checked(row)
    need(value == {'schema': 'approval-role-v175', 'unit_id': row.unit_id,
        'user_id': row.user_id, 'role_key': row.role_key, 'enabled': row.enabled})
    real_member(db, p, actor_id, lock_sources=lock_sources)
    return row


def policy_value(db, p, policy, *, current=True):
    data = checked(policy)
    versions = db.scalars(select(PolicyVersion).where(PolicyVersion.policy_id == policy.id,
        PolicyVersion.workspace_id == p.workspace_id).order_by(PolicyVersion.revision)).all()
    need(data.get('schema') == 'approval-policy-binding-v175' and versions
        and data.get('version_ids') == [v.id for v in versions]
        and data.get('current_version_id') == versions[-1].id)
    latest = versions[-1]
    value = checked(latest)
    spec = PolicyCommand.model_validate(value['spec']).model_dump()
    need(value == {'schema': 'approval-policy-version-v175', 'spec': spec}
        and spec['instance_id'] == policy.instance_id and spec['business_id'] == policy.business_id
        and policy.enabled is spec['enabled'] and latest.revision == policy.version)
    if current:
        need(policy.enabled, '审批策略已停用，不能建立新的批准或执行')
    return latest, spec


def policy_for(db, p, instance_id, business_id):
    return db.scalar(select(Policy).where(Policy.workspace_id == p.workspace_id,
        Policy.instance_id == instance_id, Policy.business_id == business_id))


def current_policy(db, p, identity, *, lock_sources=True):
    # Keep the live stop switch stable until the actual dispatch transaction
    # finishes. A cached ORM binding is not current authorization.
    query = select(Policy).where(Policy.id == identity,
        Policy.workspace_id == p.workspace_id).execution_options(populate_existing=True)
    if lock_sources:
        query = query.with_for_update(read=True)
    row = db.scalar(query)
    need(row is not None)
    return row


def settings(db, p):
    require(p, 'admin', 'operator', 'viewer')
    units = db.scalars(select(Unit).where(Unit.workspace_id == p.workspace_id).order_by(Unit.created_at, Unit.id)).all()
    grants = db.scalars(select(Role).where(Role.workspace_id == p.workspace_id).order_by(Role.created_at, Role.id)).all()
    policies = db.scalars(select(Policy).where(Policy.workspace_id == p.workspace_id).order_by(Policy.created_at, Policy.id)).all()
    members = db.execute(select(User.id, User.username, Membership.role).join(Membership,
        Membership.user_id == User.id).where(User.active.is_(True), Membership.workspace_id == p.workspace_id)).all()
    return {'schema': 'approval-settings-view-v175',
        'units': [{'id': r.id, 'version': r.version, **checked(r)} for r in units],
        'grants': [{'id': r.id, 'version': r.version, **checked(r)} for r in grants],
        'policies': [{'id': r.id, 'version': r.version, 'policy_version_id': policy_value(db, p, r, current=False)[0].id,
            'spec': policy_value(db, p, r, current=False)[1]} for r in policies],
        'members': [{'id': r.id, 'username': r.username, 'system_role': r.role} for r in members],
        'can_manage': p.role == 'admin'}


def configure(db, p, kind, body, raw, sha):
    subject = (body.unit_id if kind == 'unit' else body.grant_id if kind == 'role'
        else body.instance_id + '/' + body.business_id)
    old = prior(db, p, kind, subject, body.request_id, raw, sha)
    if old is not None:
        return old
    require(p, 'admin')
    lock_current_actor(db, p)
    if kind == 'unit':
        row = db.scalar(select(Unit).where(Unit.id == body.unit_id,
            Unit.workspace_id == p.workspace_id).with_for_update())
        if row is None:
            need(body.expected_version == 0, '组织范围不存在，请核对原请求')
            if body.parent_id:
                need(body.unit_id not in ancestors(db, p, body.parent_id))
            row = Unit(id=body.unit_id, workspace_id=p.workspace_id,
                name=body.name, parent_id=body.parent_id, enabled=body.enabled, data={})
            db.add(row)
        else:
            checked(row); check_version(row, body.expected_version)
            need(row.parent_id == body.parent_id, '组织层级已用于历史授权，不能静默改挂；请新建范围')
            row.name = body.name; row.enabled = body.enabled; row.version += 1
        seal(row, {'schema': 'approval-unit-v175', 'name': row.name,
            'parent_id': row.parent_id, 'enabled': row.enabled})
    elif kind == 'role':
        unit(db, p, body.unit_id); real_member(db, p, body.user_id)
        row = db.scalar(select(Role).where(Role.id == body.grant_id,
            Role.workspace_id == p.workspace_id).with_for_update())
        if row is None:
            need(body.expected_version == 0)
            need(db.scalar(select(Role.id).where(Role.workspace_id == p.workspace_id,
                Role.unit_id == body.unit_id, Role.user_id == body.user_id,
                Role.role_key == body.role_key)) is None, '相同范围与角色授权已存在，请更新原记录')
            row = Role(id=body.grant_id, workspace_id=p.workspace_id, unit_id=body.unit_id,
                user_id=body.user_id, role_key=body.role_key, enabled=body.enabled, data={})
            db.add(row)
        else:
            checked(row); check_version(row, body.expected_version)
            need((row.unit_id, row.user_id, row.role_key) == (body.unit_id, body.user_id, body.role_key),
                '不能改写原授权身份，请新建授权并停用原记录')
            row.enabled = body.enabled; row.version += 1
        seal(row, {'schema': 'approval-role-v175', 'unit_id': row.unit_id,
            'user_id': row.user_id, 'role_key': row.role_key, 'enabled': row.enabled})
    else:
        instance = scoped(db, DomainInstance, body.instance_id, p, True)
        release = scoped(db, InstanceRelease, instance.active_release_id, p)
        need(any(b['id'] == body.business_id for b in release.data['snapshot']['businesses']), '请从真实实例版本选择业务')
        path = ancestors(db, p, body.scope_id)
        for stage in [*[s.model_dump() for s in body.stages], body.executor.model_dump(), body.acceptance.model_dump()]:
            need(stage['scope_id'] in path, '审批岗位范围必须覆盖策略对应的组织范围')
        row = policy_for(db, p, body.instance_id, body.business_id)
        if row is None:
            need(body.expected_version == 0)
            row = Policy(id=uid(), workspace_id=p.workspace_id, instance_id=body.instance_id,
                business_id=body.business_id, enabled=body.enabled, version=1, data={})
            db.add(row); db.flush(); ids = []
        else:
            _, _ = policy_value(db, p, row, current=False)
            check_version(row, body.expected_version)
            ids = checked(row)['version_ids']; row.version += 1; row.enabled = body.enabled
        version = PolicyVersion(id=uid(), workspace_id=p.workspace_id, policy_id=row.id,
            revision=row.version, actor_id=p.user_id, data={})
        seal(version, {'schema': 'approval-policy-version-v175', 'spec': body.model_dump()})
        db.add(version)
        seal(row, {'schema': 'approval-policy-binding-v175', 'current_version_id': version.id,
            'version_ids': [*ids, version.id]})
    db.flush()
    return record(db, p, kind, subject, body.request_id, raw, sha,
        {'schema': 'approval-setting-receipt-v175', 'operation': kind,
            'id': row.id, 'version': row.version, 'request_id': body.request_id})


def context(task):
    return {'task_id': task.id, 'package_id': task.package_id,
        **{key: copy.deepcopy(task.data[key]) for key in CONTEXT_KEYS if key in task.data}}


def validated_value(db, p, task, value, *, allow_deferred=False):
    """Check declared inputs before freezing approval; never create a run/Job."""
    result = content(value)
    from .workspace_api import saved_execution
    _, release, business = saved_execution(db, p, task.id)
    if business.get('execution_mode') == 'confirmed_graph_workflow':
        from .workflow_graph import graph_for, initialization_inputs
        from jsonschema import Draft202012Validator
        graph = graph_for(release, business)
        from . import runtime_task_documents as document_sources
        document_sources.validate_edit_scope(db, p, task, graph)
        values = initialization_inputs(task, result['graph_inputs'])
        properties = graph['input_schema'].get('properties', {})
        values = {k: v for k, v in values.items() if k in properties or k in result['graph_inputs']}
        schema = (document_sources.creation_schema(graph['input_schema'])
            if allow_deferred else graph['input_schema'])
        need(not list(Draft202012Validator(schema).iter_errors(values)),
            '提交的流程输入不符合本业务已确认的字段要求', 422)
        document_sources.validate_inputs(db, p, task, graph, result['graph_inputs'],
            allow_deferred=allow_deferred)
        from .layered_case_use_origin_v210 import current_inputs
        current_inputs(db, p, task, graph, result['graph_inputs'])
    else:
        need(not result['graph_inputs'], '此业务不接受完整协同图的任务输入', 422)
    if result['goal_spec'] is not None:
        from .goals import validate_goal
        spec, _ = validate_goal(db, p, task, result['goal_spec'])
        need(any(c['kind'] == 'evidence_sufficient' for c in spec['criteria'])
            and all(c['kind'] in {'evidence_sufficient', 'field_value', 'human_confirmation',
                'capability_success', 'tool_value', 'query_value'} for c in spec['criteria']),
            '目标必须包含原资料充分性，且完成标准须已接通', 422)
        result['goal_spec'] = spec
    return result


def validate_document_sources(db, p, task, value, *, lock_sources=True):
    """A live decision/start/result gate; old graphs have no new source port."""
    from .video_workflow_handoff_v347 import KEY as video_key,verify_task as verify_video_handoff
    if video_key in task.data:verify_video_handoff(db,p,task,lock_sources=lock_sources,require_active=True)
    if task.data.get('execution_mode') != 'confirmed_graph_workflow':
        return
    from .workspace_api import saved_execution
    _, release, business = saved_execution(db, p, task.id)
    if business.get('execution_mode') == 'confirmed_graph_workflow':
        from .workflow_graph import graph_for
        from .runtime_task_documents import validate_inputs
        validate_inputs(db, p, task, graph_for(release, business), value['graph_inputs'])
        from .layered_case_use_origin_v210 import current_inputs
        current_inputs(db, p, task, graph_for(release, business), value['graph_inputs'], lock_sources=lock_sources)


def verify_authority(db, p, task, row, commands):
    """Rebuild the authority anchor from independent registration/command audits."""
    data = checked(row)
    audit = scoped(db, Audit, data.get('registration_audit_id'), p)
    origin = audit.data
    need(type(origin) is dict and set(origin) == {'managed_id', 'policy_id',
        'policy_version_id', 'context_hash', 'creation_hash', 'initial_state', 'initial_state_hash'}
        and audit.actor_id == row.owner_id and audit.action == 'layered_approval.registered'
        and audit.object_id == task.id and origin['managed_id'] == row.id
        and origin['policy_id'] == row.policy_id and origin['context_hash'] == data['context_hash']
        and origin['creation_hash'] == task.data.get('creation_hash'))
    previous = origin['initial_state']
    need(type(previous) is dict and digest(previous) == origin['initial_state_hash']
        and previous.get('id') == row.id and previous.get('workspace_id') == p.workspace_id
        and previous.get('task_id') == task.id and previous.get('owner_id') == row.owner_id
        and previous.get('policy_id') == row.policy_id
        and previous.get('policy_version_id') == origin['policy_version_id']
        and previous.get('status') == 'draft' and previous.get('version') == 1
        and previous.get('data', {}).get('registration_audit_id') == audit.id
        and previous['data'].get('context_hash') == data['context_hash'])
    by_id = {command.id: command for command in commands}
    for number, identity in enumerate(data['command_ids'], 2):
        command = by_id[identity]
        value = checked_command(db, command)
        state = value.get('approval_state')
        need(type(state) is dict and value.get('previous_state_hash') == digest(previous)
            and value.get('approval_state_hash') == digest(state)
            and type(state.get('version')) is int and state['version'] == number
            and state.get('id') == row.id and state.get('workspace_id') == p.workspace_id
            and state.get('task_id') == task.id and state.get('owner_id') == row.owner_id
            and state.get('policy_id') == row.policy_id
            and state.get('data', {}).get('context_hash') == data['context_hash']
            and value.get('managed_id') == row.id and value.get('task_id') == task.id
            and value.get('version') == number and value.get('status') == state.get('status')
            and value.get('operation') == command.operation.removeprefix('task_')
            and value.get('request_id') == command.request_id,
            '审批状态与独立原命令、顺序或审计快照不一致，不能执行')
        previous = state
    transition = db.info.get('layered_approval_initializing_v175')
    current = authority(row)
    need(transition is None or type(transition) is dict, '原启动事务的过渡状态无法核对')
    # A target may read a different, already accepted source Task during start.
    # Only the original target row may use its provisional authority anchor;
    # every other row still needs its complete independently saved command chain.
    if transition is not None and transition.get('managed_id') == row.id:
        need(transition == {'managed_id': row.id, 'state_hash': digest(current),
            'previous_state_hash': digest(previous)} and row.version == previous['version'] + 1
            and previous['status'] == 'approved' and current['status'] == 'executing'
            and previous['data'].get('execution') is None
            and current['data'].get('execution', {}).get('initialized') is False,
            '原启动事务的过渡状态无法核对')
    else:
        need(digest(current) == digest(previous), '审批状态未与独立已保存命令相符，不能执行')
    actors = db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'layered_approval.execution_actor')).all()
    need(type(data.get('execution_actor_audit_ids')) is list
        and len(set(data['execution_actor_audit_ids'])) == len(data['execution_actor_audit_ids'])
        and set(data['execution_actor_audit_ids']) == {actor.id for actor in actors},
        '实际执行岗位的独立审计登记缺失，不能降级为未参与执行')
    expected = {data['execution']['actor_id']} if data.get('execution') else set()
    for actor in actors:
        proof = checked(actor)
        need(proof.get('schema') == 'layered-approval-execution-actor-v175'
            and proof.get('managed_id') == row.id and proof.get('context_hash') == data['context_hash']
            and proof.get('actor_id') == actor.actor_id and data.get('execution') is not None
            and proof.get('submission_hash') == data['execution']['submission_hash'])
        expected.add(actor.actor_id)
    need(type(data.get('executor_ids')) is list and set(data['executor_ids']) == expected
        and len(data['executor_ids']) == len(expected), '实际执行人登记与独立审计不一致')
    observed = {receipt.get('actor') for receipt in task.data.get('workflow_runtime', {}).get('receipts', [])}
    observed.discard(None)
    steps = db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action.in_(['workflow.step_completed',
            'workflow.ability_completed', 'workflow.graph.human_completed',
            'workflow.graph.node_completed']))).all()
    observed.update(step.actor_id for step in steps)
    need(observed <= expected, '真实流程回执或步骤审计的执行人未完整登记，不能验收')


def verify_submission(submission, item, result):
    state = result.get('approval_state', {})
    frozen = state.get('data', {})
    need(result.get('submission_hash') == submission.data.get('record_hash')
        and frozen.get('current_submission_id') == submission.id
        and digest(frozen.get('draft_value')) == digest(item.get('value'))
        and digest(frozen.get('policy_snapshot')) == digest(item.get('policy_snapshot'))
        and state.get('policy_version_id') == item.get('policy_version_id')
        and frozen.get('context_hash') == item.get('context_hash'),
        '提交原件与独立原提交命令的内容、策略或摘要不一致')


def managed(db, p, task, *, lock=False):
    query = select(Managed).where(Managed.task_id == task.id, Managed.workspace_id == p.workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        need('layered_approval_v175' not in task.data, '任务的永久审批登记缺失，不能降级执行')
        policy = policy_for(db, p, task.data.get('instance_id'), task.data.get('business_id'))
        if policy is not None:
            versions = db.scalars(select(PolicyVersion).where(PolicyVersion.policy_id == policy.id,
                PolicyVersion.created_at <= task.created_at).order_by(PolicyVersion.revision)).all()
            if versions:
                need(checked(versions[-1])['spec']['enabled'] is not True,
                    '此任务创建时已受审批策略管理，永久登记缺失，不能降级执行')
        return None
    data = checked(row)
    need(data.get('schema') == SCHEMA and row.status in STATES and data.get('status') == row.status
        and row.owner_id == task.owner_id and data.get('context') == context(task)
        and data.get('context_hash') == digest(context(task))
        and type(data.get('command_ids')) is list and len(set(data['command_ids'])) == len(data['command_ids'])
        and data.get('draft_value', {}).get('title') == task.title)
    commands = db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.subject_id == task.id, Command.operation.like('task_%'))).all()
    need(set(data['command_ids']) == {r.id for r in commands})
    verify_authority(db, p, task, row, commands)
    rounds = db.scalars(select(Submission).where(Submission.managed_id == row.id,
        Submission.workspace_id == p.workspace_id).order_by(Submission.round_number)).all()
    need(data.get('submission_ids') == [s.id for s in rounds])
    by_id = {command.id: command for command in commands}
    submissions = [checked_command(db, by_id[identity]) for identity in data['command_ids']
        if by_id[identity].operation == 'task_submit']
    need(len(submissions) == len(rounds), '提交轮次与独立原命令数量不一致')
    for i, submission in enumerate(rounds):
        item = checked(submission)
        need(submission.round_number == i + 1 and item.get('task_id') == task.id
            and item.get('context_hash') == data['context_hash'])
        verify_submission(submission, item, submissions[i])
    need(data.get('current_submission_id') == (rounds[-1].id if rounds else None))
    return row


def register(db, p, task, body):
    """Called under the same instance lock as create_task; never queues work."""
    policy = policy_for(db, p, task.data['instance_id'], task.data['business_id'])
    if policy is None or not policy.enabled:
        return False
    version, spec = policy_value(db, p, policy)
    ancestors(db, p, spec['scope_id'])
    frozen = context(task)
    row = Managed(id=uid(), workspace_id=p.workspace_id, task_id=task.id,
        owner_id=task.owner_id, policy_id=policy.id, policy_version_id=version.id,
        status='draft', version=1, data={})
    value = {'title': task.title, 'note': '', 'graph_inputs': copy.deepcopy(body.graph_inputs),
        'goal_spec': body.goal_spec.model_dump() if body.goal_spec else None}
    registration_id = uid()
    seal(row, {'schema': SCHEMA, 'status': 'draft', 'context': frozen,
        'registration_audit_id': registration_id,
        'context_hash': digest(frozen), 'scope_id': spec['scope_id'],
        'policy_snapshot': copy.deepcopy(version.data), 'draft_value': value,
        'draft_history': [], 'submission_ids': [], 'current_submission_id': None,
        'current_stage': 0, 'decisions': [], 'decision_history': [], 'execution': None, 'executor_ids': [],
        'execution_actor_audit_ids': [],
        'acceptance': None, 'acceptance_history': [], 'supplements': [], 'command_ids': []})
    db.add(row)
    task.status = 'approval_draft'
    task.data = {**task.data, 'layered_approval_v175': row.id}
    initial_state = authority(row)
    db.add(Audit(id=registration_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        action='layered_approval.registered', object_id=task.id,
        data={'managed_id': row.id, 'policy_id': policy.id, 'policy_version_id': version.id,
            'context_hash': digest(frozen), 'creation_hash': task.data['creation_hash'],
            'initial_state': initial_state, 'initial_state_hash': digest(initial_state)}))
    db.flush()
    return True


def current_submission(db, p, row):
    data = checked(row)
    submission = scoped(db, Submission, data['current_submission_id'], p)
    item = checked(submission)
    need(submission.managed_id == row.id and item['context_hash'] == data['context_hash'])
    version = scoped(db, PolicyVersion, item['policy_version_id'], p)
    need(digest(item['policy_snapshot']) == digest(version.data))
    return submission, item, checked(version)['spec']


def guard(db, p, task, *, record_actor=False, lock_sources=True):
    """Live gate for both schedulers and final dispatch; historical GET skips it."""
    need(not record_actor or lock_sources is True, '只读查看不能登记执行或取得执行权限')
    row = managed(db, p, task)
    if row is None:
        return
    data = checked(row)
    need(row.status == 'executing' and data['execution'] is not None,
        '任务尚未逐级批准并明确启动，或已退回、撤回或验收，不能执行')
    policy = current_policy(db, p, row.policy_id, lock_sources=lock_sources)
    policy_value(db, p, policy)
    _, item, spec = current_submission(db, p, row)
    need(data['current_stage'] == len(spec['stages']) and digest(item['value']) == digest(data['draft_value'])
        and data['execution']['submission_hash'] == row_hash(db, p, row))
    validate_document_sources(db, p, task, data['draft_value'], lock_sources=lock_sources)
    if data['execution'].get('initialized') is True:
        need(digest(task.data.get('goal_spec')) == digest(data['draft_value']['goal_spec']),
            '执行目标与已批准提交不一致，不能继续')
        if task.data.get('execution_mode') == 'confirmed_graph_workflow':
            from .workspace_api import saved_execution
            from .workflow_graph import graph_for, initialization_inputs
            _, release, business = saved_execution(db, p, task.id)
            graph = graph_for(release, business)
            supplied = data['draft_value']['graph_inputs']
            values = initialization_inputs(task, supplied)
            properties = graph['input_schema'].get('properties', {})
            values = {k: v for k, v in values.items() if k in properties or k in supplied}
            need(digest(task.data.get('graph_runtime', {}).get('inputs')) == digest(values),
                '执行流程输入与已批准提交不一致，不能继续')
    ancestors(db, p, spec['scope_id'], lock_sources=lock_sources)
    real_member(db, p, p.user_id, lock_sources=lock_sources)
    role_allowed(db, p, spec['executor'], actor_id=data['execution']['actor_id'], lock_sources=lock_sources)
    if record_actor:
        role_allowed(db, p, spec['executor'])
        if p.user_id not in data['executor_ids']:
            previous_state_hash = digest(authority(row))
            actor_audit = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
                action='layered_approval.execution_actor', object_id=task.id, data={})
            seal(actor_audit, {'schema': 'layered-approval-execution-actor-v175',
                'managed_id': row.id, 'context_hash': data['context_hash'],
                'actor_id': p.user_id, 'submission_hash': data['execution']['submission_hash'],
                'at': now().isoformat()})
            db.add(actor_audit)
            data['executor_ids'].append(p.user_id)
            data['execution_actor_audit_ids'].append(actor_audit.id)
            seal(row, data); row.version += 1
            nonce = uid()
            body = {'request_id': nonce, 'actor_id': p.user_id, 'task_id': task.id,
                'execution_actor_audit_id': actor_audit.id,
                'submission_hash': data['execution']['submission_hash']}
            raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            record(db, p, 'task_execution_actor', task.id, nonce, raw,
                hashlib.sha256(raw.encode()).hexdigest(), {
                    'schema': 'layered-approval-task-receipt-v175', 'task_id': task.id,
                    'managed_id': row.id, 'request_id': nonce, 'operation': 'execution_actor',
                    'version': row.version, 'task_version': task.version, 'status': row.status,
                    'current_stage': data['current_stage'],
                    'submission_hash': data['execution']['submission_hash']}, row,
                previous_state_hash=previous_state_hash)


def approval_starter(db, p, task):
    """Internal explicit-start seam; never impersonate the Task owner."""
    guard(db, p, task)
    row = managed(db, p, task)
    need(row is not None and checked(row)['execution']['actor_id'] == p.user_id
        and checked(row)['execution'].get('initialized') is False,
        '只有本次明确启动的执行账号可初始化已批准目标', 403)


def before_workflow_dispatch(db, p, key):
    """Fence ordinary workflow/legacy plan transport after invoke's commit.

    Graph and native plan/query transports retain their own stronger final
    checks. This hook binds only real leased Jobs, never client input IDs.
    """
    from .leases import ownership
    lease = ownership(db)
    if not lease.get('job_id'):
        return False
    job = scoped(db, Job, lease['job_id'], p)
    if job.kind not in {'execute_workflow_step', 'execute_plan'}:
        return False
    need(job.status == 'running' and job.lease_token == lease.get('lease_token')
        and job.lease_until is not None
        and job.lease_until > db.scalar(select(func.clock_timestamp()))
        and job.data.get('actor_id') == p.user_id, '实际任务租约或执行账号已变化')
    if job.kind == 'execute_workflow_step':
        need(job.data.get('invocation_key') == key)
        task_id = job.data.get('task_id')
    else:
        task_id = scoped(db, Plan, job.data.get('plan_id'), p).task_id
    task = scoped(db, Task, task_id, p, True)
    row = managed(db, p, task, lock=True)
    if row is None:
        return False
    guard(db, p, task, record_actor=True)
    call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key))
    need(call is not None and call.status == 'running'
        and call.data.get('job_id') == job.id
        and call.data.get('lease_token') == lease['lease_token'])
    data = checked(row); dispatches = data.get('dispatches', {})
    need(key not in dispatches, '原调用已经获准发送，响应未知时不得自动重发')
    dispatches[key] = {'job_id': job.id, 'invocation_id': call.id,
        'actor_id': p.user_id, 'submission_hash': row_hash(db, p, row),
        'at': db.scalar(select(func.clock_timestamp())).isoformat()}
    data['dispatches'] = dispatches; seal(row, data)
    return True


def row_hash(db, p, row):
    return current_submission(db, p, row)[0].data['record_hash']


def old_acceptance_guard(db, p, task):
    need(managed(db, p, task) is None,
        '此任务须通过独立验收岗位办理，不能借旧验收或计划批准入口绕过层级审批')


def old_definition_guard(db, p, task):
    need(managed(db, p, task) is None,
        '此任务的目标属于已批准提交；变更须修改并重新提交审批')


def acceptance_hash(value):
    # query_evidence.evaluated_at is an inspection clock, not changed evidence.
    # Use the same stable original-content binding as native goal acceptance,
    # while retaining the complete fresh inspection for display and history.
    body = copy.deepcopy(value)
    if body['evidence'] is not None:
        from .business_tools import evidence_hash
        body['evidence'] = {'content_hash': evidence_hash(body['evidence']),
            'status': body['evidence']['status']}
    return digest(body)


@report_text_parse_scope()
def outcome(db, p, task, row, *, lock_sources=True):
    # Reuse only immutable case-origin proofs during this one calculation.
    # Current grants, Use state, native parse/ACK and acceptance stay live.
    # No cache survives the call or a transaction/Use-version change.
    from .layered_case_use_origin_v210 import verification_scope
    with verification_scope(db):
        return _outcome(db, p, task, row, lock_sources=lock_sources)


def _outcome(db, p, task, row, *, lock_sources=True):
    guard(db, p, task, lock_sources=lock_sources)
    from .workspace_api import execution
    _, release, business = execution(db, p, task.id, lock_sources=lock_sources)
    graph_supplement = None
    formal_continuation = None
    if task.status != 'workflow_completed':
        from .workflow_graph_supplement_evidence import verify
        graph_supplement = verify(db, p, task, lock_sources=lock_sources)
        if graph_supplement is None:
            from .formal_continuation_proof_v346 import verify as verify_formal
            formal_continuation = verify_formal(db, p, task, lock_sources=lock_sources)
    need(task.status == 'workflow_completed' or graph_supplement is not None or formal_continuation is not None,
        '业务流程尚未形成完整结果，不能验收')
    from .native_goals import verify_acceptance_inputs, assessment
    verify_acceptance_inputs(db, p, task, lock_sources=lock_sources)
    if 'graph_runtime' in task.data:
        from .workflow_graph import graph_for, invoke_pure, graph_api
        graph = graph_for(release, business)
        invoke_pure(graph_api.check_run, graph, task.data['graph_runtime'])
        need(task.data['graph_runtime']['status'] == 'succeeded' or graph_supplement is not None or formal_continuation is not None)
        for activation_id,item in task.data.get('graph_jobs', {}).items():
            if formal_continuation is not None and activation_id==formal_continuation['retired_activation_id']:
                need(item.get('phase')=='retired_for_new_evaluation')
                continue
            call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
                Invocation.idempotency_key == item['invocation_key']))
            need(item.get('phase') == 'succeeded' and call is not None and call.status == 'succeeded'
                and item.get('invocation_id') == call.id and call.data.get('input_hash') == item['input_hash'],
                '原能力调用仍有未知、失败或关联不一致，不能验收')
        from .workflow_document_task_integrity_v207 import verify_task_results
        verify_task_results(db, p, task, graph)
        from .authored_image_result_integrity_v243 import verify_task_results as verify_image_results
        from .workflow_graph import business_binding
        verify_image_results(db,p,task,graph,business_binding(release,business))
    evidence, evaluation = None, None
    if task.data.get('native_goal_context'):
        evidence, evaluation = assessment(db, p, task, accepted=True, lock_sources=lock_sources,
            graph_supplement=graph_supplement)
        need(evaluation.get('completed') is True, '原业务目标与完成标准尚未全部满足')
        if formal_continuation is not None:
            need(evaluation.get('formal_continuation',{}).get('completion_proof_hash')==formal_continuation['basis_hash'],
                '续段与目标核对的依据不一致，请重新核对')
    value = {'schema': 'layered-approval-acceptance-basis-v175', 'task_id': task.id,
        'task_version': task.version, 'submission_hash': row_hash(db, p, row),
        'workflow_runtime': copy.deepcopy(task.data['workflow_runtime']),
        'graph_state_hash': task.data.get('graph_runtime', {}).get('state_hash'),
        'evidence': evidence, 'evaluation': evaluation,
        'supplements': checked(row)['supplements'], 'physical_state_verified': False}
    if graph_supplement is not None:
        value['graph_supplement'] = graph_supplement
    if formal_continuation is not None:
        value['formal_continuation'] = formal_continuation
    return {**value, 'basis_hash': acceptance_hash(value)}


@report_text_parse_scope()
def detail(db, p, task, *, with_basis=True):
    row = managed(db, p, task)
    if row is None:
        return None
    data = checked(row)
    policy = checked(scoped(db, PolicyVersion, row.policy_version_id, p))['spec']
    if data['current_submission_id']:
        _, _, policy = current_submission(db, p, row)
    basis, basis_issue = None, None
    if with_basis and row.status == 'executing':
        from .workflow_graph_supplement_evidence import candidate as supplement_candidate
        from .formal_continuation_contracts_v345 import INDEX as formal_index
        if task.status == 'workflow_completed' or supplement_candidate(task) or task.data.get(formal_index):
            try:
                basis = outcome(db, p, task, row, lock_sources=False)
            except HTTPException as error:
                basis_issue = str(error.detail)
    submissions = [scoped(db, Submission, identity, p) for identity in data['submission_ids']]
    permissions, reasons = {}, {}
    policy_enabled = scoped(db, Policy, row.policy_id, p).enabled
    owner = p.user_id == row.owner_id
    states = {'revise': owner and row.status in {'draft', 'returned', 'withdrawn'},
        'submit': owner and row.status in {'draft', 'returned', 'withdrawn'},
        'decide': row.status == 'in_review' and data['current_stage'] < len(policy['stages']),
        'withdraw': owner and row.status in {'in_review', 'approved'},
        'start': row.status == 'approved',
        'resubmit_acceptance': row.status == 'acceptance_returned'
            and (owner or p.user_id in data['executor_ids']),
        'accept': row.status == 'executing' and basis is not None}
    for operation, valid in states.items():
        try:
            need(valid, '当前状态或责任岗位不允许此操作', 403)
            require(p, 'admin', 'operator'); real_member(db, p, p.user_id, lock_sources=False)
            if operation not in {'revise', 'withdraw'}:
                need(policy_enabled, '审批策略已停用')
            if operation == 'decide':
                role_allowed(db, p, policy['stages'][data['current_stage']], lock_sources=False)
                independent(p.user_id, row.owner_id)
                need(p.user_id not in [d['actor_id'] for d in data['decisions']],
                    '各级审批须由不同人员办理', 403)
            if operation == 'start':
                role_allowed(db, p, policy['executor'], lock_sources=False)
            if operation == 'accept':
                role_allowed(db, p, policy['acceptance'], lock_sources=False)
                independent(p.user_id, row.owner_id,
                    [d['actor_id'] for d in data['decision_history']], data['executor_ids'], acceptance=True)
            permissions[operation] = True
        except (HTTPException, ValueError) as error:
            permissions[operation] = False
            reasons[operation] = str(error.detail if isinstance(error, HTTPException) else error)
    return {'schema': 'layered-approval-task-view-v175', 'task_id': task.id,
        'managed_id': row.id, 'version': row.version, 'task_version': task.version,
        'status': row.status, 'owner_id': row.owner_id, 'scope_id': data['scope_id'],
        'policy': copy.deepcopy(policy), 'value': data['draft_value'],
        'draft_history': data['draft_history'], 'current_stage': data['current_stage'],
        'submission_hash': row_hash(db, p, row) if submissions else None,
        'submissions': [{'id': s.id, 'round_number': s.round_number,
            'submission_hash': s.data['record_hash'], **checked(s)} for s in submissions],
        'decisions': data['decisions'], 'decision_history': data['decision_history'], 'execution': data['execution'],
        'acceptance': data['acceptance'], 'acceptance_history': data['acceptance_history'],
        'supplements': data['supplements'], 'acceptance_basis': basis,
        'acceptance_issue': basis_issue, 'can_manage': row.owner_id == p.user_id,
        'permissions': permissions, 'blocked_reasons': reasons,
        'current_user_id': p.user_id, 'system_role': p.role}


@report_text_parse_scope()
def perform(db, p, task_id, operation, body, raw, sha):
    kind = 'task_' + operation
    old = prior(db, p, kind, task_id, body.request_id, raw, sha)
    if old is not None:
        return old
    require(p, 'admin', 'operator')
    from .signal_dual_handoffs import prelock_task
    prelock_task(db, p, task_id)
    task = scoped(db, Task, task_id, p, True)
    row = managed(db, p, task, lock=True)
    need(row is not None, '此任务没有层级审批登记，原流程保持原合同')
    check_version(row, body.expected_version)
    lock_current_actor(db, p)
    data = checked(row)
    previous_state_hash = digest(authority(row))
    policy = current_policy(db, p, row.policy_id)
    policy_version, current_spec = policy_value(db, p, policy, current=False)
    if operation not in {'revise', 'withdraw'}:
        need(policy.enabled, '审批策略已停用；历史、修改稿与撤回操作仍保留')
    spec = (current_submission(db, p, row)[2] if data['current_submission_id']
        else checked(scoped(db, PolicyVersion, row.policy_version_id, p))['spec'])
    stamp = {'request_id': body.request_id, 'actor_id': p.user_id,
        'reason': body.reason, 'at': now().isoformat()}
    if operation == 'revise':
        need(p.user_id == row.owner_id and row.status in {'draft', 'returned', 'withdrawn'},
            '只能由原发起人在草稿、退回或撤回状态另存修改', 403)
        value = validated_value(db, p, task, body.value.model_dump(), allow_deferred=True)
        data['draft_history'].append({'version': row.version, 'value': data['draft_value'], **stamp})
        data['draft_value'] = value; task.title = value['title']
        row.status = 'draft'
    elif operation == 'submit':
        need(p.user_id == row.owner_id, '请由原发起人提交', 403)
        next_state(row.status, data['current_stage'], len(spec['stages']), 'submit')
        need(current_spec['scope_id'] == data['scope_id'], '策略的组织范围已改变，请保留原任务并重新发起')
        for stage in [*current_spec['stages'], current_spec['executor'], current_spec['acceptance']]:
            need(stage['scope_id'] in ancestors(db, p, data['scope_id']))
        row.policy_version_id = policy_version.id
        data['draft_value'] = validated_value(db, p, task, data['draft_value'])
        payload = {'schema': 'approval-submission-v175', 'task_id': task.id,
            'context_hash': data['context_hash'], 'value': copy.deepcopy(data['draft_value']),
            'policy_version_id': policy_version.id, 'policy_snapshot': copy.deepcopy(policy_version.data), **stamp}
        submission = Submission(id=uid(), workspace_id=p.workspace_id,
            managed_id=row.id, round_number=len(data['submission_ids']) + 1, data={})
        seal(submission, payload); db.add(submission)
        data['submission_ids'].append(submission.id); data['current_submission_id'] = submission.id
        data['current_stage'] = 0; data['decisions'] = []; data['policy_snapshot'] = copy.deepcopy(policy_version.data)
        row.status = 'in_review'
    elif operation == 'decide':
        validate_document_sources(db, p, task, data['draft_value'])
        need(body.submission_hash == row_hash(db, p, row), '本次决定引用的提交版本已经变化')
        need(row.status == 'in_review' and data['current_stage'] < len(spec['stages']))
        role_allowed(db, p, spec['stages'][data['current_stage']])
        try:
            independent(p.user_id, row.owner_id)
        except ValueError as error:
            need(False, str(error), 403)
        need(p.user_id not in [d['actor_id'] for d in data['decisions']], '各审批层须由不同人员办理', 403)
        decision = {**stamp, 'decision': body.decision,
            'stage': data['current_stage'], 'submission_id': data['current_submission_id'],
            'submission_hash': body.submission_hash}
        data['decisions'].append(decision); data['decision_history'].append(copy.deepcopy(decision))
        row.status, data['current_stage'] = next_state(row.status, data['current_stage'],
            len(spec['stages']), body.decision)
    elif operation == 'withdraw':
        need(p.user_id == row.owner_id, '请由原提交人撤回', 403)
        row.status, data['current_stage'] = next_state(row.status, data['current_stage'], len(spec['stages']), 'withdraw')
        decision = {**stamp, 'decision': 'withdraw', 'submission_id': data['current_submission_id']}
        data['decisions'].append(decision); data['decision_history'].append(copy.deepcopy(decision))
    elif operation == 'start':
        role_allowed(db, p, spec['executor'])
        validate_document_sources(db, p, task, data['draft_value'])
        row.status, _ = next_state(row.status, data['current_stage'], len(spec['stages']), 'start')
        need(data['execution'] is None, '原启动已保存，不能另起执行')
        data['execution'] = {**stamp, 'submission_hash': row_hash(db, p, row), 'initialized': False}
        data['executor_ids'] = [p.user_id]
    elif operation == 'resubmit_acceptance':
        need(row.status == 'acceptance_returned' and (p.user_id == row.owner_id or p.user_id in data['executor_ids']),
            '仅原发起人或实际执行人可补充整改说明并再次送验', 403)
        validate_document_sources(db, p, task, data['draft_value'])
        data['supplements'].append(stamp)
        row.status = 'executing'
    elif operation == 'accept':
        need(row.status == 'executing')
        role_allowed(db, p, spec['acceptance'])
        validate_document_sources(db, p, task, data['draft_value'])
        try:
            independent(p.user_id, row.owner_id, [d['actor_id'] for d in data['decision_history']],
                data['executor_ids'], acceptance=True)
        except ValueError as error:
            need(False, str(error), 403)
        basis = outcome(db, p, task, row)
        need(body.basis_hash == basis['basis_hash'], '执行结果或验收依据已变化，请重新核对')
        value = {**stamp, 'decision': body.decision, 'basis': basis}
        data['acceptance_history'].append(value); data['acceptance'] = value
        row.status = 'accepted' if body.decision == 'accept' else 'acceptance_returned'
        if body.decision == 'accept':
            db.add(Memory(workspace_id=p.workspace_id, task_id=task.id, status='candidate',
                text='层级审批任务已保存独立人工验收，未自动发布经验或证明物理状态',
                data={'type': 'layered_approval_acceptance_v175', 'task_id': task.id,
                    'submission_hash': row_hash(db, p, row), 'acceptance': copy.deepcopy(value),
                    'physical_state_verified': False, 'auto_publish': False}))
            if task.data.get('native_goal_context'):
                from .native_goal_time import cancel
                cancel(db, p, task, '独立人工验收已保存，不继续旧目标协调')
    else:
        need(False, '未知审批操作', 422)
    data['status'] = row.status; seal(row, data)
    row.version += 1; task.version += 1
    if operation != 'start' and data['execution'] is None:
        task.status = 'approval_' + row.status
    if operation == 'start':
        # Final approval did not queue anything. This original explicit start
        # initializes all goal/graph/ability work in the same transaction.
        with initializing(db, row, previous_state_hash):
            task.status = 'workflow_waiting'
            from .workspace_api import saved_execution
            _, release, business = saved_execution(db, p, task.id, True)
            from .instance_api import active
            instance, current_release = active(db, p, task.data['instance_id'])
            need(current_release.id == release.id and business['id'] in instance.data['enabled_business_ids'],
                '原实例版本或业务启用条件已变化，未启动')
            if data['draft_value']['goal_spec'] is not None:
                from .native_goals import define, GoalDefinition
                from .contracts import GoalSpec
                define(db, p, task, GoalDefinition(request_id='approval_' + body.request_id,
                    expected_version=task.version, reason=body.reason,
                    goal_spec=GoalSpec.model_validate(data['draft_value']['goal_spec'])), _approval_start=True)
            if business.get('execution_mode') == 'confirmed_graph_workflow':
                from .workflow_graph import initialize
                initialize(db, p, task, release, business, data['draft_value']['graph_inputs'])
            else:
                need(not data['draft_value']['graph_inputs'])
                from .workflow_abilities import continue_flow
                continue_flow(db, p, task, release, business)
            started = checked(row)
            started['execution']['initialized'] = True
            seal(row, started)
    result = {'schema': 'layered-approval-task-receipt-v175', 'task_id': task.id,
        'managed_id': row.id, 'request_id': body.request_id, 'operation': operation,
        'version': row.version, 'task_version': task.version, 'status': row.status,
        'current_stage': checked(row)['current_stage'],
        'submission_hash': row_hash(db, p, row) if data['current_submission_id'] else None}
    return record(db, p, kind, task.id, body.request_id, raw, sha, result, row,
        previous_state_hash=previous_state_hash)
