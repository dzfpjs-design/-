"""One human-approved saved-raster Task and its independent reassessment.

Readers never repair inventory or dispatch work. Current-source qualification is
separate from the immutable human/command history. The caller owns the commit.
"""
from copy import deepcopy
import hashlib

from fastapi import HTTPException
from sqlalchemy import func, select

from .contracts import digest
from .models import (Audit, Capability, DomainInstance, Entity, EvidenceAssessment,
                     GeoPair, Invocation, Job, Memory, Observation, Task, WorkflowScene, uid)
from .relations import lock_current_actor
from .security import require
from .services import audit, check_version, published
from .typed_payload_codec_v131 import canonical_json
from .saved_imagery_contracts_v163 import Draft, Update, CreateTask, Reassess, Stop, canonical_uuid, parse_wire
from .saved_imagery_models_v163 import SavedImageryReassessment as Plan, SavedImageryCommand as Command, SavedImageryAudit as State

ERROR = '影像补充记录、原请求或独立状态库存不完整；原稿和原记录保留'
COMMAND_ACTION = 'saved_imagery.command_v163'
MARKER = 'saved_imagery_reassessment_v163'
MODELS = {'create': Draft, 'update': Update, 'create_task': CreateTask, 'reassess': Reassess, 'stop': Stop}
SNAPSHOT = {'id', 'workspace_id', 'instance_id', 'assessment_id', 'entity_id', 'owner_id',
            'status', 'task_id', 'reassessment_id', 'version', 'data'}
DATA = {'schema', 'draft', 'assessment', 'selection', 'task_request', 'task_creation_hash',
        'task_owner_id', 'reassessment_request', 'reassessment_creation_hash', 'stop_reason'}
COMMAND_FIELDS = ('id', 'workspace_id', 'instance_id', 'actor_id', 'request_id', 'operation',
                  'method', 'path', 'wire_sha256', 'body_hash', 'target_id', 'outcome')


class SavedImageryInventoryError(HTTPException):
    def __init__(self):
        super().__init__(409, ERROR)


def need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def hash_value(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _uuid(value):
    try:
        return canonical_uuid(value)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '请保留原小写UUID标识') from None


def _row(db, p, model, identity, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None, '原记录不存在或不在当前工作区', 404)
    return row


def snapshot(row):
    return {key: deepcopy(getattr(row, key)) for key in SNAPSHOT}


def _draft(body, release_id):
    return {'release_id': release_id, **{key: deepcopy(getattr(body, key)) for key in
        ('business_id', 'comparison_scene_ids', 'comparison_confirmed', 'title', 'reason')}}


def _path(identity, operation, plan_id):
    base = '/api/workspace/instances/' + identity + '/saved-imagery-reassessments'
    if operation == 'create':
        return 'POST', base
    canonical_uuid(plan_id)
    return ('PUT', base + '/' + plan_id) if operation == 'update' else (
        'POST', base + '/' + plan_id + '/' + operation.replace('_', '-'))


def _result(saved, operation):
    return {key: saved[key] for key in ('id', 'version', 'status', 'task_id', 'reassessment_id')} | {'operation': operation}


def _shape(saved):
    need(type(saved) is dict and set(saved) == SNAPSHOT)
    for key in ('id', 'workspace_id', 'instance_id', 'assessment_id', 'entity_id', 'owner_id'):
        canonical_uuid(saved[key])
    for key in ('task_id', 'reassessment_id'):
        if saved[key] is not None:
            canonical_uuid(saved[key])
    need(type(saved['version']) is int and saved['version'] >= 1
         and saved['status'] in {'draft', 'task_created', 'reassessment_requested', 'stopped'})
    data = saved['data']; need(type(data) is dict and set(data) == DATA and data['schema'] == 'saved-imagery-record-v163')
    draft = data['draft']; need(type(draft) is dict and set(draft) == {
        'release_id', 'business_id', 'comparison_scene_ids', 'comparison_confirmed', 'title', 'reason'})
    Draft.model_validate({'request_id': 'inventory_check', 'assessment_id': saved['assessment_id'], **draft}, strict=True)
    original = data['assessment']
    need(type(original) is dict and set(original) == {'id', 'version', 'request', 'request_hash', 'result', 'result_hash'}
         and original['id'] == saved['assessment_id'] and type(original['version']) is int
         and original['version'] >= 1 and digest(original['request']) == original['request_hash']
         and digest(original['result']) == original['result_hash']
         and original['request']['instance_id'] == saved['instance_id']
         and original['request']['release_id'] == draft['release_id']
         and original['request']['entity_ids'] == [saved['entity_id']])
    selection = data['selection']
    if saved['task_id'] is None:
        need(selection is None and data['task_request'] is None and data['task_creation_hash'] is None
             and data['task_owner_id'] is None and saved['reassessment_id'] is None)
    else:
        need(type(selection) is dict and set(selection) == {'release_id', 'configuration_hash', 'package_id',
            'package_hash', 'business', 'capability', 'inputs'} and selection['release_id'] == draft['release_id'])
        _business_shape(selection['business'])
        cap = selection['capability']
        need(type(cap) is dict and set(cap) == {'id', 'content_hash', 'release_version', 'data'}
             and digest(cap['data']) == cap['content_hash'] and selection['business']['id'] == draft['business_id'])
        canonical_uuid(cap['id']); canonical_uuid(selection['package_id'])
        from .workflow_geo_pairs import manifest
        ex = cap['data']['execution']
        need(same(cap['data'], manifest(ex['threshold'], ex['alignment']).model_dump())
             and selection['business']['flow']['steps'][0]['execution']['capability_id'] == cap['id']
             and selection['business']['flow']['steps'][0]['execution']['capability_hash'] == cap['content_hash']
             and [item['id'] for item in selection['inputs']] == draft['comparison_scene_ids']
             and len(selection['inputs']) == 2 and draft['comparison_confirmed'] is True)
        canonical_uuid(data['task_owner_id'])
        from .workspace_api import NewTask, creation_body_hash
        body = NewTask.model_validate(data['task_request'])
        need(body.request_id == 'saved-imagery-task-' + saved['id']
             and body.entity_ids == [saved['entity_id']] and body.release_id == draft['release_id']
             and body.business_id == draft['business_id'] and body.comparison_scene_ids == draft['comparison_scene_ids']
             and body.comparison_confirmed is True and body.assessment_id is None and body.goal_spec is None
             and body.signal_id is None and body.advice_id is None and not body.graph_inputs
             and not body.image_ids and not body.scene_ids and not body.audio_ids
             and creation_body_hash(body) == data['task_creation_hash'])
    if saved['reassessment_id'] is None:
        need(data['reassessment_request'] is None and data['reassessment_creation_hash'] is None)
    else:
        need(saved['task_id'] is not None and type(data['reassessment_request']) is dict)
        _follow_request(saved, data['reassessment_request'])
    if saved['status'] == 'draft': need(saved['task_id'] is None)
    if saved['status'] == 'task_created': need(saved['task_id'] is not None and saved['reassessment_id'] is None)
    if saved['status'] == 'reassessment_requested': need(saved['reassessment_id'] is not None)
    need(type(data['stop_reason']) is str and (bool(data['stop_reason'].strip()) if saved['status'] == 'stopped' else data['stop_reason'] == ''))
    canonical_json(saved)


def _business_shape(business):
    need(type(business) is dict and business.get('available') is True
         and business.get('execution_mode') == 'registered_ability_workflow')
    flow = business.get('flow'); need(type(flow) is dict)
    steps = flow.get('steps'); need(type(steps) is list and len(steps) == 2)
    first, last = steps
    need(type(first) is dict and type(last) is dict and type(first.get('execution')) is dict
         and type(first.get('id')) is str and type(last.get('id')) is str
         and type(first.get('condition', '')) is str and type(last.get('condition', '')) is str)
    need(first['id'] != last['id'] and first['agent'] in {'多模感知智能体', '数据管理智能体'}
         and first['execution']['adapter'] == 'local_geo_pair' and first['execution']['mode'] == '执行前确认'
         and last['agent'] == '人工确认' and not last.get('execution')
         and not first.get('planning_tools') and not last.get('planning_tools')
         and not first.get('condition', '').strip() and not last.get('condition', '').strip()
         and first.get('nextId', 'auto') in {'auto', last['id']}
         and last.get('nextId', 'auto') in {'auto', 'end'})


def _follow_request(saved, body):
    req = saved['data']['assessment']['request']
    need(body['request_id'] == 'saved-imagery-reassess-' + saved['id'] and body['parent_id'] == saved['assessment_id']
         and body['instance_id'] == saved['instance_id'] and body['release_id'] == req['release_id']
         and same(body['entity_ids'], req['entity_ids'])
         and all(same(body[key], req[key]) for key in ('purpose', 'goal', 'required_modalities', 'lookback_hours'))
         and body.get('question_id') == req.get('question_id'))


def _transition(previous, saved, operation, body):
    _shape(saved)
    if operation == 'create':
        need(previous is None and saved['version'] == 1 and saved['status'] == 'draft'
             and saved['assessment_id'] == body.assessment_id and same(saved['data']['draft'], _draft(body, body.release_id)))
        return
    need(previous is not None and body.version == previous['version'] and saved['version'] == previous['version'] + 1)
    expected = deepcopy(previous); expected['version'] += 1
    if operation == 'update':
        need(previous['status'] == 'draft')
        expected['data']['draft'] = _draft(body, previous['data']['draft']['release_id'])
    elif operation == 'create_task':
        need(previous['status'] == 'draft' and saved['task_id'] is not None)
        expected['status'] = 'task_created'; expected['task_id'] = saved['task_id']
        for key in ('selection', 'task_request', 'task_creation_hash', 'task_owner_id'):
            expected['data'][key] = saved['data'][key]
    elif operation == 'reassess':
        need(previous['status'] == 'task_created' and saved['reassessment_id'] is not None)
        expected['status'] = 'reassessment_requested'; expected['reassessment_id'] = saved['reassessment_id']
        for key in ('reassessment_request', 'reassessment_creation_hash'):
            expected['data'][key] = saved['data'][key]
    elif operation == 'stop':
        need(previous['status'] != 'stopped')
        expected['status'] = 'stopped'; expected['data']['stop_reason'] = body.reason
    else: need(False)
    need(same(expected, saved))


def _command_payload(row):
    return {key: getattr(row, key) for key in COMMAND_FIELDS} | {'data': row.data}


def _command_pin(row):
    return {'schema': 'saved-imagery-command-audit-v163', **_command_payload(row), 'command_hash': row.command_hash}


def inventory(db, p):
    """Full workspace inventory, including orphan state/command/audit rows."""
    try:
        rows = list(db.scalars(select(Plan).where(Plan.workspace_id == p.workspace_id).execution_options(populate_existing=True)))
        states = list(db.scalars(select(State).where(State.workspace_id == p.workspace_id).execution_options(populate_existing=True)))
        commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id).execution_options(populate_existing=True)))
        issued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == COMMAND_ACTION)
            .execution_options(populate_existing=True)))
        plans = {row.id: row for row in rows}; need(len(plans) == len(rows))
        audits = {}; groups = {}; seen = set(); nonces = set(); roots = set()
        for item in issued: audits.setdefault(item.object_id, []).append(item)
        need(set(audits) == {item.id for item in commands})
        by_command = {item.command_id: item for item in states}; need(len(by_command) == len(states))
        bodies = {}
        for command in commands:
            need(command.version == 1 and type(command.version) is int and command.outcome == 'saved'
                 and command.operation in MODELS and command.target_id in plans)
            for key in ('id', 'workspace_id', 'instance_id', 'actor_id', 'target_id'): canonical_uuid(getattr(command, key))
            data = command.data
            need(type(data) is dict and set(data) == {'schema', 'wire_text', 'result', 'snapshot_hash', 'state_audit_id'}
                 and data['schema'] == 'saved-imagery-command-v163' and type(data['wire_text']) is str)
            body = parse_wire(data['wire_text'], MODELS[command.operation]); bodies[command.id] = body
            need(command.request_id == body.request_id
                 and (command.method, command.path) == _path(command.instance_id, command.operation, command.target_id)
                 and command.wire_sha256 == hashlib.sha256(data['wire_text'].encode('utf-8')).hexdigest()
                 and command.body_hash == hash_value(body.model_dump()) and command.command_hash == hash_value(_command_payload(command)))
            records = audits[command.id]; need(len(records) == 1)
            need(records[0].actor_id == command.actor_id and same(records[0].data, _command_pin(command)))
            state = by_command.get(command.id)
            need(state is not None and data['state_audit_id'] == state.id and state.plan_id == command.target_id
                 and state.instance_id == command.instance_id and state.actor_id == command.actor_id
                 and state.operation == command.operation and state.version == 1 and type(state.version) is int
                 and type(state.record_version) is int and state.record_version >= 1
                 and type(state.data) is dict and set(state.data) == {'schema', 'snapshot'}
                 and state.data['schema'] == 'saved-imagery-state-v163')
            saved = state.data['snapshot']; _shape(saved)
            need(saved['id'] == state.plan_id and saved['workspace_id'] == p.workspace_id
                 and saved['instance_id'] == state.instance_id and saved['version'] == state.record_version
                 and hash_value(saved) == state.record_hash == data['snapshot_hash']
                 and same(data['result'], _result(saved, command.operation)))
            nonce = (command.actor_id, command.request_id)
            need(nonce not in nonces); nonces.add(nonce)
            groups.setdefault(state.plan_id, []).append(state); seen.add(command.id)
        need(seen == set(by_command))
        for row in rows:
            current = snapshot(row); _shape(current)
            need(row.record_hash == hash_value(current))
            root = (row.instance_id, row.assessment_id, row.entity_id); need(root not in roots); roots.add(root)
            chain = sorted(groups.get(row.id, []), key=lambda item: item.record_version)
            need(len(chain) == row.version); previous = None; previous_hash = None
            follow_actor = None
            for version, state in enumerate(chain, 1):
                need(state.record_version == version and state.previous_hash == previous_hash)
                _transition(previous, state.data['snapshot'], state.operation, bodies[state.command_id])
                if version == 1:
                    need(state.actor_id == row.owner_id)
                if state.operation == 'create_task':
                    need(state.actor_id == state.data['snapshot']['data']['task_owner_id'])
                if state.operation == 'reassess': follow_actor = state.actor_id
                previous = state.data['snapshot']; previous_hash = state.record_hash
            need(previous is not None and same(previous, current) and previous_hash == row.record_hash)
            _links(db, p, row, follow_actor=follow_actor)
        _reverse_links(db, p, plans)
        return rows, commands, states
    except SavedImageryInventoryError: raise
    except HTTPException as error: raise SavedImageryInventoryError() from error
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise SavedImageryInventoryError() from None


def _marker(row):
    return {'schema': 'saved-imagery-task-origin-v163', 'plan_id': row.id, 'assessment_id': row.assessment_id,
            'instance_id': row.instance_id, 'entity_id': row.entity_id,
            'selection_hash': hash_value(row.data['selection']), 'creation_hash': row.data['task_creation_hash']}


def _links(db, p, row, *, follow_actor=None):
    if row.task_id is not None:
        task = _row(db, p, Task, row.task_id)
        data = row.data; body = data['task_request']
        need(task.owner_id == data['task_owner_id'] and task.package_id == data['selection']['package_id']
             and same(task.data.get(MARKER), _marker(row))
             and task.data.get('creation_request_id') == body['request_id']
             and task.data.get('creation_hash') == data['task_creation_hash']
             and task.data.get('instance_id') == row.instance_id
             and task.data.get('instance_release_id') == body['release_id']
             and task.data.get('business_id') == body['business_id']
             and task.data.get('entity_ids') == [row.entity_id]
             and same(task.data.get('comparison_scene_snapshots'), data['selection']['inputs'])
             and task.data.get('comparison_confirmed') is True and not task.data.get('graph_runtime'))
        objects = task.data.get('object_snapshots')
        need(type(objects) is list and len(objects) == 1 and objects[0]['id'] == row.entity_id)
        original_ref = data['assessment']['request']['object_refs']
        need(len(original_ref) == 1 and original_ref[0]['id'] == row.entity_id
             and original_ref[0]['signature'] == digest({'version': objects[0]['version'],
                 'status': None, 'data': objects[0]['attributes']}), '原任务对象快照与原核查依据不一致')
        created = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == task.id, Audit.action == 'workflow.created')))
        need(len(created) == 1 and created[0].actor_id == task.owner_id
             and same(created[0].data, {'release': body['release_id'], 'business': body['business_id']}))
    if row.reassessment_id is not None:
        follow = _row(db, p, EvidenceAssessment, row.reassessment_id)
        from .evidence_assessment import checked_request
        checked_request(follow)
        need(follow_actor is not None and follow.owner_id == follow_actor
             and follow.instance_id == row.instance_id and follow.request_id == row.data['reassessment_request']['request_id']
             and follow.data.get('creation_hash') == row.data['reassessment_creation_hash']
             and follow.data.get(MARKER) == {'plan_id': row.id, 'task_id': row.task_id})
        _follow_request(snapshot(row), follow.data['request'])


def _reverse_links(db, p, plans):
    for model, field in ((Task, 'task_id'), (EvidenceAssessment, 'reassessment_id')):
        rows = db.scalars(select(model).where(model.workspace_id == p.workspace_id,
            model.data[MARKER].is_not(None)).execution_options(populate_existing=True))
        for row in rows:
            marker = row.data.get(MARKER); need(type(marker) is dict and marker.get('plan_id') in plans)
            need(getattr(plans[marker['plan_id']], field) == row.id)


def _assessment(db, p, identity, assessment_id):
    from .evidence_assessment import checked
    row = checked(_row(db, p, EvidenceAssessment, assessment_id))
    need(row.instance_id == identity and row.status == 'completed', '请选择本实例已完成的原资料核查')
    req = row.data['request']; need(len(req['entity_ids']) == 1, '本片仅补充一个对象的已保存影像处理')
    values = [item for item in row.data['result']['objects'] if item['object']['id'] == req['entity_ids'][0]]
    need(len(values) == 1 and any(gap['kind'] == 'missing_saved_processing' and gap.get('modality') == 'imagery'
        for gap in values[0]['gaps']), '原核查没有此对象缺少影像处理记录的缺口')
    return row


def _assessment_snapshot(row):
    return {'id': row.id, 'version': row.version, **{key: deepcopy(row.data[key]) for key in
        ('request', 'request_hash', 'result', 'result_hash')}}


def _current_origin(db, p, identity, assessment, *, no_processing=False):
    from .evidence_assessment import scoped_objects, ref, collect
    req = assessment.data['request']
    instance, release, objects = scoped_objects(db, p, identity, req['entity_ids'])
    need(release.id == req['release_id'] and same([ref(item) for item in objects], req['object_refs']),
         '实例版本或对象依据已变化；原记录保留，请重新核查')
    if no_processing:
        records, _, truncated = collect(db, p, req)
        need(not truncated, '当前资料达到核查上限，不能据此安排重复处理')
        need(not any(item['modality'] == 'imagery' or 'imagery' in item.get('modalities', []) for item in records),
             '此对象已有影像处理记录；请先核查原任务，不重复处理同一缺口')
    return instance, release


def _business(db, p, instance, release, business_id):
    from .workflow_abilities import eligible, pin, ROLES
    from .workflow_geo_pairs import validate_manifest
    from .contracts import Manifest
    business = next((item for item in release.data['snapshot']['businesses'] if item['id'] == business_id), None)
    need(business is not None and business_id in instance.data.get('enabled_business_ids', []), '业务不存在、未启用或已暂停')
    _business_shape(business)
    first = business['flow']['steps'][0]; execution = first['execution']
    cap = published(db, p, execution['capability_id'])
    eligible(cap, ROLES[first['agent']])
    try:
        validate_manifest(Manifest.model_validate(cap.data))
    except (ValueError, OSError):
        raise HTTPException(409, '本地RGB运行环境或固定配方不可用，请在资源页核对；不会下载或转云') from None
    policy = release.data['snapshot']['automation']['policies'][business['name']]
    need(same(pin(db, p, first, policy), execution) and cap.content_hash == execution['capability_hash'],
         '业务执行设置或本地能力版本已变化')
    published(db, p, release.package_id, 'domain_package')
    package = _row(db, p, Capability, release.package_id)
    need(cap.id in package.data['dependencies'] and package.content_hash == digest(package.data), '能力不在原发布包内')
    return business, cap, package


def _selection(db, p, row, *, no_processing=True):
    assessment = _assessment(db, p, row.instance_id, row.assessment_id)
    need(same(_assessment_snapshot(assessment)['request'], row.data['assessment']['request'])
         and same(assessment.data['result'], row.data['assessment']['result']), '原核查条件或结果发生变化')
    instance, release = _current_origin(db, p, row.instance_id, assessment, no_processing=no_processing)
    draft = row.data['draft']; need(release.id == draft['release_id'], '请保留原稿，不能换用另一个发布版')
    need(draft['title'].strip() and draft['reason'].strip(), '请填写处理任务名称和本次补充理由')
    business, cap, package = _business(db, p, instance, release, draft['business_id'])
    from .workflow_scenes import snapshots
    from .workflow_geo_pairs import validate_pair
    inputs = snapshots(db, p, row.instance_id, draft['comparison_scene_ids'])
    validate_pair(inputs, draft['comparison_confirmed'])
    return {'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'package_id': package.id, 'package_hash': package.content_hash, 'business': deepcopy(business),
        'capability': {'id': cap.id, 'content_hash': cap.content_hash, 'release_version': cap.release_version, 'data': deepcopy(cap.data)},
        'inputs': deepcopy(inputs)}


def _business_summary(business, cap):
    execution = cap['data']['execution']
    return {'id': business['id'], 'name': business['name'], 'capability_id': cap['id'],
        'capability_name': cap['data']['description'], 'threshold': execution['threshold'],
        'alignment': execution['alignment'], 'max_attempts': cap['data']['budget'].get('max_attempts', 2),
        'confirm_before_execute': True}


def _scene_summary(identity, value):
    source = value['snapshot']; config = source['configuration']
    return {'id': value['id'], 'name': source['name'], 'scene_id': source['scene_id'],
        'snapshot_hash': value['snapshot_hash'], 'input_sha256': source['input_sha256'],
        'roi': deepcopy(source['roi']), 'rgb_bands': deepcopy(config['rgb_bands']),
        'observed_at': config.get('observed_at'),
        'original_path': '/api/geo/scenes/' + source['scene_id'] + '/original',
        'preview_path': '/api/workspace/instances/' + identity + '/input-scenes/' + value['id'] + '/preview'}


def _assessment_summary(assessment):
    req = assessment.data['request']; obj = req['objects'][0]
    return {'id': assessment.id, 'version': assessment.version, 'status': assessment.status,
        **{key: deepcopy(req[key]) for key in ('purpose', 'goal', 'required_modalities', 'lookback_hours', 'window_start', 'window_end')},
        'entity_id': req['entity_ids'][0], 'entity_name': obj['name']}


def _source_issue(function, *args, **kwargs):
    try:
        return function(*args, **kwargs), None
    except SavedImageryInventoryError: raise
    except HTTPException as error:
        if error.status_code not in {404, 409, 422}: raise
        return None, str(error.detail)


def options(db, p, identity, assessment_id):
    _uuid(identity); _uuid(assessment_id)
    _row(db, p, DomainInstance, identity)
    rows, _, _ = inventory(db, p)
    assessment = _assessment(db, p, identity, assessment_id)
    existing = [row for row in rows if row.instance_id == identity and row.assessment_id == assessment_id
        and row.entity_id == assessment.data['request']['entity_ids'][0]]
    need(len(existing) <= 1)
    current, issue = _source_issue(_current_origin, db, p, identity, assessment, no_processing=True)
    businesses = []; scenes = []; reasons = [] if issue is None else [issue]
    release_id = current[1].id if current else None
    if current:
        instance, release = current
        available = release.data['snapshot']['businesses']
        need(len(available) <= 200, '可用业务超过显示上限，请缩小当前配置', 413)
        for value in available:
            result, _ = _source_issue(_business, db, p, instance, release, value['id'])
            if result:
                business, cap, _ = result
                businesses.append(_business_summary(business, {'id': cap.id, 'data': cap.data}))
        inputs = list(db.scalars(select(WorkflowScene).where(WorkflowScene.workspace_id == p.workspace_id,
            WorkflowScene.instance_id == identity).order_by(WorkflowScene.id).limit(201)))
        need(len(inputs) <= 200, '已关联影像超过显示上限，请按实例整理后继续', 413)
        from .workflow_scenes import snapshots
        for item in inputs:
            checked, error = _source_issue(snapshots, db, p, identity, [item.id])
            if checked: scenes.append(_scene_summary(identity, checked[0]))
            elif error: reasons.append('一项已关联影像的原件或固定设置暂不可用')
        if not businesses: reasons.append('当前没有仅含本地RGB比较和人工核查、且执行前确认的已启用业务')
        if len(scenes) < 2: reasons.append('需要两份同实例、可核对原件的A/B影像')
    return {'schema': 'saved-imagery-options-v163', 'instance_id': identity,
        'assessment': _assessment_summary(assessment), 'current_release_id': release_id,
        'business_options': businesses, 'scene_options': scenes, 'existing_id': existing[0].id if existing else None,
        'can_create': bool(p.role in {'admin', 'operator'} and current and businesses and len(scenes) >= 2 and not existing),
        'reasons': list(dict.fromkeys(reasons))}


def _human_review(db, p, task, selection):
    need(task.status == 'workflow_completed', '请先在原任务保存并完成真实人工核查')
    runtime = task.data['workflow_runtime']; steps = selection['business']['flow']['steps']
    receipts = runtime['receipts']; need(len(receipts) == 2)
    machine, human = receipts
    need(machine['step_id'] == steps[0]['id'] and machine.get('execution_mode') == 'registered_ability'
         and human['step_id'] == steps[1]['id'] and human.get('execution_mode') is None
         and type(human.get('note')) is str and bool(human['note'].strip())
         and runtime['current_step_id'] is None and human.get('branch') is None)
    # complete-step hashes the original text, then stores note.strip(). The
    # original lexical body cannot be reconstructed from this display receipt.
    # Its saved typed receipt must instead agree with the independent Memory.
    need(type(human.get('request_hash')) is str and len(human['request_hash']) == 64
         and not set(human['request_hash']) - set('0123456789abcdef'))
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.step_completed')))
    need(len(audits) == 1 and audits[0].actor_id == human['actor']
         and same(audits[0].data, {'step': steps[1]['id'], 'next': 'end'}), '原人工完成审计无法核对')
    memories = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
        Memory.data['type'].as_string() == 'workflow_result', Memory.data['task_id'].as_string() == task.id)))
    need(len(memories) == 1 and memories[0].id == runtime.get('data_receipt_id')
         and memories[0].status == 'candidate' and same(memories[0].data['receipts'], receipts)
         and same(memories[0].data['comparison_scene_snapshots'], selection['inputs'])
         and same(memories[0].data['object_snapshots'], task.data['object_snapshots'])
         and memories[0].data['instance_release_id'] == selection['release_id']
         and memories[0].data['entity_state_updated'] is False, '原人工核查保存回执无法核对')


def _stage(db, p, row):
    stage = {'status': 'draft', 'label': '补充处理草稿', 'issue': None, 'processing_status': None,
        'data_received': False, 'review_status': 'waiting', 'observation_id': None, 'metrics': None,
        'candidate_only': True, 'source_time_verified': False, 'physical_state_verified': False, 'entity_state_updated': False}
    task = None; follow = None
    if row.task_id is not None:
        task = _row(db, p, Task, row.task_id)
        stage['processing_status'] = task.status
        runtime = task.data['workflow_runtime']
        if task.status == 'workflow_waiting' and not runtime['receipts']:
            stage.update(status='waiting_confirmation', label='请打开原任务明确确认执行')
        elif task.status in {'workflow_queued', 'workflow_running'}:
            stage.update(status='processing', label='原任务正在处理已保存影像')
        elif task.status in {'workflow_failed', 'workflow_blocked', 'workflow_cancelled'}:
            stage.update(status='blocked', label='请在原任务核对处理状态', issue='原任务未完成；不创建替代任务或重放成功调用')
        else:
            stage.update(status='awaiting_reception', label='核对原处理及现代数据接收')
        pairs = list(db.scalars(select(GeoPair).where(GeoPair.workspace_id == p.workspace_id,
            GeoPair.data['workflow_task_id'].as_string() == task.id)))
        need(len(pairs) <= 1, '同一补充任务出现多份处理记录，不能继续核查')
        if pairs and pairs[0].status == 'succeeded':
            from .native_geo_pair_provenance_v137 import checked_pair, checked_observation
            context = checked_pair(db, p, pairs[0].id)
            need(context['task'].id == task.id and context['task'].data['entity_ids'] == [row.entity_id])
            packet_key = 'GeoPair:' + pairs[0].id + ':' + row.entity_id
            observations = list(db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
                Observation.source_record_id == packet_key)))
            need(len(observations) <= 1, '原影像结果存在重复归集记录，不能继续核查')
            if observations:
                observation = observations[0]; checked_observation(db, p, observation)
                from .instance_data_receipts import observation_receipt
                received = observation_receipt(db, p, observation).get('data_reception')
                need(type(received) is dict and received.get('schema') == 'instance-native-geo-pair-data-reception-v1'
                     and received.get('declaration_status') == 'not_declared', '原处理没有本版完整现代数据接收回执')
                stage['data_received'] = True; stage['observation_id'] = observation.id
                stage['metrics'] = {key: pairs[0].data['result'][key] for key in ('candidate_pixels', 'unknown_pixels')}
                stage.update(status='awaiting_review', label='请在原任务保存人工核查说明')
                if task.status == 'workflow_completed':
                    _human_review(db, p, task, row.data['selection'])
                    stage.update(status='ready_to_reassess', label='原处理、数据接收与人工核查已保存', review_status='confirmed')
    if row.reassessment_id is not None:
        from .evidence_assessment import checked
        follow = checked(_row(db, p, EvidenceAssessment, row.reassessment_id))
        if stage['data_received'] is True and stage['review_status'] == 'confirmed':
            stage.update(status='completed' if follow.status == 'completed' else 'rechecking',
                label='新旧核查与剩余缺口已保存' if follow.status == 'completed' else '正在按原条件重新核查')
            if follow.status in {'failed', 'cancelled'}:
                stage.update(status='blocked', label='后继核查未完成，原请求保留')
        else:
            stage.update(status='blocked', label='后继核查保留；原处理、接收或人工依据需核对',
                issue='不能仅凭后继核查完成替代原现代接收和已保存人工核查')
    if row.status == 'stopped':
        stage.update(status='stopped', label='已停止后续协调；原任务与已保存结果保留')
    return stage, task, follow


def _follow_summary(row, entity_id, *, readable=True):
    if row is None: return None
    req = row.data['request']; obj = None
    if row.status == 'completed' and readable:
        matches = [item for item in row.data['result']['objects'] if item['object']['id'] == entity_id]
        need(len(matches) == 1); obj = matches[0]
        need(obj['current_state_sufficient'] is False, '后继核查不能把工程候选变为当前现场状态')
    return {'id': row.id, 'version': row.version, 'status': row.status,
        'window_start': req['window_start'], 'window_end': req['window_end'],
        'processing_records': obj['processing_records'] if obj else None,
        'gaps': [{'kind': item['kind'], 'modality': item.get('modality'), 'message': item.get('message', '请核对原来源')}
                 for item in obj['gaps']] if obj else [], 'current_state_sufficient': False}


def detail(db, p, identity, plan_id):
    _uuid(identity); _uuid(plan_id); _row(db, p, DomainInstance, identity)
    rows, commands, states = inventory(db, p)
    row = next((item for item in rows if item.id == plan_id and item.instance_id == identity), None)
    need(row is not None, '此实例没有这项影像补充记录', 404)
    writable = p.role == 'admin' or p.role == 'operator' and p.user_id == row.owner_id
    current = None; issue = None
    if row.status not in {'stopped', 'reassessment_requested'}:
        current, issue = _source_issue(_selection, db, p, row, no_processing=row.task_id is None)
        if current is not None and row.data['selection'] is not None:
            if not same(current, row.data['selection']):
                current = None; issue = '原批准的业务、能力或固定影像设置已变化，不能继续本协调'
    projected, stage_issue = _source_issue(_stage, db, p, row)
    if projected is None:
        task = _row(db, p, Task, row.task_id) if row.task_id else None
        follow = _row(db, p, EvidenceAssessment, row.reassessment_id) if row.reassessment_id else None
        stage = {'status': 'stopped' if row.status == 'stopped' else 'blocked',
            'label': '已停止后续协调' if row.status == 'stopped' else '原处理或接收依据需核对',
            'issue': stage_issue, 'processing_status': task.status if task else None,
            'data_received': False, 'review_status': 'waiting', 'observation_id': None, 'metrics': None,
            'candidate_only': True, 'source_time_verified': False, 'physical_state_verified': False, 'entity_state_updated': False}
    else: stage, task, follow = projected
    selection = row.data['selection']
    public_selection = None if selection is None else {'release_id': selection['release_id'],
        'business': _business_summary(selection['business'], selection['capability']),
        'scenes': [_scene_summary(identity, value) for value in selection['inputs']]}
    by_id = {item.id: item for item in commands}
    history = []
    for state in sorted((item for item in states if item.plan_id == row.id), key=lambda item: item.record_version):
        command = by_id[state.command_id]
        parsed = parse_wire(command.data['wire_text'], MODELS[command.operation])
        history.append({'version': state.record_version, 'operation': state.operation, 'actor_id': state.actor_id,
            'reason': parsed.reason if state.operation == 'stop' else state.data['snapshot']['data']['draft']['reason']})
    return {'schema': 'saved-imagery-detail-v163', 'id': row.id, 'version': row.version, 'status': row.status,
        'owner_id': row.owner_id, 'instance_id': identity, 'assessment_id': row.assessment_id, 'entity_id': row.entity_id,
        'draft': deepcopy(row.data['draft']), 'selection': public_selection, 'stage': stage,
        'task': {'id': task.id, 'title': task.title, 'status': task.status, 'version': task.version} if task else None,
        'reassessment': _follow_summary(follow, row.entity_id, readable=projected is not None), 'history': history,
        'can_edit': bool(writable and row.status == 'draft'),
        'can_create_task': bool(writable and row.status == 'draft' and current is not None),
        'can_reassess': bool(writable and row.status == 'task_created' and current is not None
            and stage['status'] == 'ready_to_reassess'),
        'can_stop': bool(writable and row.status != 'stopped'),
        'reasons': list(dict.fromkeys(value for value in (issue, stage_issue) if value))}


def _envelope(request_id, sha, result=None):
    return {'schema': 'saved-imagery-request-v163', 'request_id': request_id,
        'wire_sha256': sha, 'outcome': 'unknown' if result is None else 'saved', 'result': deepcopy(result)}


def request_result(db, p, identity, request_id, expected_sha256):
    _uuid(identity); _row(db, p, DomainInstance, identity)
    need(type(request_id) is str and 12 <= len(request_id) <= 100
         and all(char in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in request_id),
         '请保留原请求编号', 422)
    need(type(expected_sha256) is str and len(expected_sha256) == 64
         and not set(expected_sha256) - set('0123456789abcdef'), '请保留原请求SHA', 422)
    _, commands, _ = inventory(db, p)
    found = [item for item in commands if item.actor_id == p.user_id and item.request_id == request_id]
    need(len(found) <= 1)
    if not found: return _envelope(request_id, expected_sha256)
    need(found[0].instance_id == identity, '此原请求编号属于另一实例，不能在此恢复或重新使用')
    need(found[0].wire_sha256 == expected_sha256, '同一请求编号绑定了不同原文，请保留原稿核对')
    return _envelope(request_id, expected_sha256, found[0].data['result'])


def _new(db, p, identity, body):
    assessment = _assessment(db, p, identity, body.assessment_id)
    _, release = _current_origin(db, p, identity, assessment)
    need(release.id == body.release_id, '实例版本已变化，不能替换原发布版')
    entity_id = assessment.data['request']['entity_ids'][0]
    existing = db.scalar(select(Plan.id).where(Plan.workspace_id == p.workspace_id,
        Plan.instance_id == identity, Plan.assessment_id == assessment.id, Plan.entity_id == entity_id))
    need(existing is None, '此对象的原缺口已有补充记录，请找回原记录')
    row = Plan(id=uid(), workspace_id=p.workspace_id, instance_id=identity, assessment_id=assessment.id,
        entity_id=entity_id, owner_id=p.user_id, status='draft', task_id=None, reassessment_id=None,
        version=1, record_hash='', data={'schema': 'saved-imagery-record-v163',
            'draft': _draft(body, body.release_id), 'assessment': _assessment_snapshot(assessment), 'selection': None,
            'task_request': None, 'task_creation_hash': None, 'task_owner_id': None,
            'reassessment_request': None, 'reassessment_creation_hash': None, 'stop_reason': ''})
    return row


def _create_task(db, p, row):
    selection = _selection(db, p, row)
    from .workspace_api import NewTask, create_task, creation_body_hash
    draft = row.data['draft']; request_id = 'saved-imagery-task-' + row.id
    need(db.scalar(select(Task.id).where(Task.workspace_id == p.workspace_id,
        Task.data['instance_id'].as_string() == row.instance_id,
        Task.data['creation_request_id'].as_string() == request_id)) is None,
        '原任务编号已有记录，不能收养其他入口创建的任务')
    body = NewTask(release_id=draft['release_id'], business_id=draft['business_id'], entity_ids=[row.entity_id],
        title=draft['title'], request_id=request_id, comparison_scene_ids=draft['comparison_scene_ids'], comparison_confirmed=True)
    outcome = {}; task = create_task(row.instance_id, body, p, db, outcome=outcome)
    need(outcome.get('created') is True and task.status == 'workflow_waiting'
         and task.data['workflow_runtime']['current_step_id'] == selection['business']['flow']['steps'][0]['id']
         and task.data['workflow_runtime']['receipts'] == [] and not task.data['workflow_runtime'].get('automatic'),
         '新任务没有停在真实执行前确认；本次创建必须回滚')
    need(db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
        Job.data['task_id'].as_string() == task.id).limit(1)) is None,
        '任务创建已排工作，不能冒称仅创建待确认任务')
    need(db.scalar(select(Invocation.id).where(Invocation.workspace_id == p.workspace_id,
        Invocation.data['inputs']['task_id'].as_string() == task.id).limit(1)) is None,
        '任务创建已发生调用，不能冒称尚未执行')
    row.task_id = task.id; row.status = 'task_created'
    row.data = {**row.data, 'selection': selection, 'task_request': body.model_dump(),
        'task_creation_hash': creation_body_hash(body), 'task_owner_id': task.owner_id}
    task.data = {**task.data, MARKER: _marker(row)}


def _claim_follow(db, p, row, body):
    """Bind only an actually inserted assessment, never an idempotent prior."""
    from .evidence_assessment_api import create_saved
    outcome = {}
    follow = create_saved(body, p, db, outcome=outcome)
    need(outcome.get('created') is True, '原后继核查编号已有记录，不能收养或重新创建')
    need(follow.workspace_id == p.workspace_id and follow.instance_id == row.instance_id
         and follow.owner_id == p.user_id and follow.request_id == body.request_id,
         '新后继核查的实际来源范围无法核对')
    row.reassessment_id = follow.id; row.status = 'reassessment_requested'
    row.data = {**row.data, 'reassessment_request': body.model_dump(), 'reassessment_creation_hash': follow.data['creation_hash']}
    follow.data = {**follow.data, MARKER: {'plan_id': row.id, 'task_id': row.task_id}}


def _reassess(db, p, row):
    from .evidence_assessment_api import Request, lock_request
    # The ordinary creator owns this advisory before any EntityReleaseContext
    # resource locks. Take that exact shared lock before _selection/_stage too;
    # its later reentrant acquisition cannot invert resource -> request order.
    request_id = 'saved-imagery-reassess-' + row.id
    lock_request(db, p, request_id)
    current = _selection(db, p, row, no_processing=False)
    need(same(current, row.data['selection']), '批准时的固定业务、能力或影像设置已变化')
    stage, _, _ = _stage(db, p, row)
    need(stage['status'] == 'ready_to_reassess' and stage['data_received'] is True
         and stage['review_status'] == 'confirmed', '请先完成原处理、现代接收及保存人工核查')
    req = row.data['assessment']['request']
    body = Request(instance_id=row.instance_id, release_id=req['release_id'], entity_ids=req['entity_ids'],
        purpose=req['purpose'], goal=req['goal'], required_modalities=req['required_modalities'],
        lookback_hours=req['lookback_hours'], parent_id=row.assessment_id, question_id=req.get('question_id'), request_id=request_id)
    _claim_follow(db, p, row, body)


def mutate(db, p, identity, operation, plan_id, body, wire, method, path):
    """One transaction, no commit; a failure leaves no signed success/nonce."""
    _uuid(identity); need(operation in MODELS, '未声明的影像补充操作', 422)
    if operation != 'create': _uuid(plan_id)
    require(p, 'admin', 'operator')
    # Ordinary create_saved owns this advisory before INSERT's Instance FK
    # KEY SHARE. Take it before our Instance FOR UPDATE, or an ordinary
    # creator and this coordinator can deadlock while each holds the other.
    # The fixed nonce is derivable without reading or locking the Plan.
    if operation == 'reassess':
        from .evidence_assessment_api import lock_request
        lock_request(db, p, 'saved-imagery-reassess-' + plan_id)
    # Match create_task's existing Instance -> actor order. No actor-first
    # wrapper may wait for this Instance while another writer holds it.
    _row(db, p, DomainInstance, identity, True); lock_current_actor(db, p)
    try:
        parsed = parse_wire(wire, MODELS[operation])
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    need(type(body) is MODELS[operation] and same(body.model_dump(), parsed.model_dump()), '原wire与实际命令不一致', 422)
    need((method, path) == _path(identity, operation, plan_id), '原方法或路径与命令目标不一致', 422)
    sha = hashlib.sha256(wire.encode('utf-8')).hexdigest()
    nonce_key = hash_value({'schema': 'saved-imagery-nonce-v163', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'request_id': body.request_id})
    db.execute(select(func.pg_advisory_xact_lock(int(nonce_key[:15], 16))))
    rows, commands, _ = inventory(db, p)
    previous = next((item for item in commands if item.actor_id == p.user_id and item.request_id == body.request_id), None)
    if previous is not None:
        need(previous.instance_id == identity, '此原请求编号属于另一实例，不能在此恢复或重新使用')
        need(previous.method == method and previous.path == path and previous.wire_sha256 == sha
             and previous.operation == operation and previous.body_hash == hash_value(body.model_dump()),
             '同一请求编号不能更换方法、目标或原文')
        return _envelope(body.request_id, sha, previous.data['result'])
    if operation == 'create':
        need(plan_id is None, '新建命令不能指定旧目标', 422)
        row = _new(db, p, identity, body); old_hash = None
    else:
        row = _row(db, p, Plan, plan_id, True)
        need(row.instance_id == identity and any(item.id == row.id for item in rows))
        need(p.role == 'admin' or row.owner_id == p.user_id, '请由原填写人或管理员操作', 403)
        check_version(row, body.version); old_hash = row.record_hash
        if operation == 'update':
            need(row.status == 'draft', '只有未创建任务的草稿可以修改')
            row.data = {**row.data, 'draft': _draft(body, row.data['draft']['release_id'])}
        elif operation == 'create_task':
            need(row.status == 'draft' and row.task_id is None, '原补充记录已创建任务或停止')
            _create_task(db, p, row)
        elif operation == 'reassess':
            need(row.status == 'task_created' and row.reassessment_id is None, '请恢复原后继核查，不能重复创建')
            _reassess(db, p, row)
        else:
            need(row.status != 'stopped', '原协调已经停止，历史保留')
            row.status = 'stopped'; row.data = {**row.data, 'stop_reason': body.reason}
        row.version += 1
    saved = snapshot(row); _shape(saved); row.record_hash = hash_value(saved)
    command_id = uid(); state_id = uid(); result = _result(saved, operation)
    command = Command(id=command_id, workspace_id=p.workspace_id, instance_id=identity,
        actor_id=p.user_id, request_id=body.request_id, operation=operation, method=method, path=path,
        wire_sha256=sha, body_hash=hash_value(body.model_dump()), target_id=row.id, outcome='saved', version=1,
        command_hash='', data={'schema': 'saved-imagery-command-v163', 'wire_text': wire,
            'result': result, 'snapshot_hash': row.record_hash, 'state_audit_id': state_id})
    command.command_hash = hash_value(_command_payload(command))
    state = State(id=state_id, workspace_id=p.workspace_id, instance_id=identity, plan_id=row.id,
        actor_id=p.user_id, operation=operation, record_version=row.version, record_hash=row.record_hash,
        previous_hash=old_hash, command_id=command_id, version=1,
        data={'schema': 'saved-imagery-state-v163', 'snapshot': saved})
    # No ORM relationships are needed for these immutable IDs. Flush parents
    # explicitly before the State's real FKs; all remain in this transaction.
    db.add(row); db.flush(); db.add(command); db.flush(); db.add(state)
    audit(db, p, COMMAND_ACTION, command_id, _command_pin(command)); db.flush()
    inventory(db, p)
    return _envelope(body.request_id, sha, result)
