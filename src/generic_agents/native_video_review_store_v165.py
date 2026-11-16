"""Human-only opinions over received V164 video, with per-reviewer CAS.

No source row is written here. Command/Audit inventory is checked independently
of today's machine evidence, so unavailable evidence cannot erase human work.
"""
import copy
import re
from datetime import timezone
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select, text, or_, func

from . import models as m
from .contracts import digest
from .services import scoped, audit
from .native_video_review_models_v165 import NativeVideoReview as Review, NativeVideoReviewCommand as Command
from .native_video_review_contracts_v165 import Draft, Submit, parse_wire, wire_sha256, command_path, identity

ACTION = 'native_video.review_saved_v165'
VIEW_SCHEMA = 'native-video-review-view-v165'
REQUEST_SCHEMA = 'native-video-review-request-v165'
STATE_SCHEMA = 'native-video-review-state-v165'
ERROR = '视频核查的人工作业或原请求库存无法核对；原稿与原视频保留'
_SHA = re.compile(r'^[a-f0-9]{64}$')
_NONCE = re.compile(r'^[a-zA-Z0-9_-]{12,100}$')


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def same(left, right):
    return digest(left) == digest(right)


def _checked(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError, OverflowError):
            need(False)
    return call


def _utc(value):
    need(value is not None and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def _scope(instance_id, observation_id, context, receipt):
    return {'instance_id': instance_id, 'observation_id': observation_id,
        'run_id': context['run'].id, 'release_id': context['release'].id,
        'receipt_hash': receipt['receipt_hash']}


def _source(db, p, instance_id, observation_id):
    from .native_video_provenance_v164 import checked_observation
    from .instance_data_receipts import observation_receipt
    observation = scoped(db, m.Observation, observation_id, p)
    context = checked_observation(db, p, observation, live=False)
    result = observation_receipt(db, p, observation)
    receipt = result.get('data_reception')
    need(context['instance'].id == instance_id and type(receipt) is dict
        and receipt.get('schema') == 'instance-native-video-data-reception-v1')
    # Both automatic and manual producers return a verified frozen scope.
    # Manual captures pin it through their declaration rather than embedding
    # the full scope in the input Audit itself.
    frozen_scope = copy.deepcopy(context['frozen_scope'])
    context['review_source_pins'] = [
        {'id': frozen_scope['source_id'], 'kind': 'source', 'hash': frozen_scope['source_hash']},
        {'id': frozen_scope['package_id'], 'kind': 'domain_package', 'hash': frozen_scope['package_hash']},
        {'id': receipt['source_id'], 'kind': 'source', 'hash': receipt['source_hash']}]
    return context, _scope(instance_id, observation_id, context, receipt)


def _permission(db, p, context, *, lock=False):
    if lock:
        from .relations import lock_current_actor
        lock_current_actor(db, p)
    else:
        query = select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
            m.Membership.role == p.role, m.Membership.role.in_(['admin', 'operator']))
        if db.scalar(query) is None:
            raise HTTPException(403, '当前身份不能保存视频核查意见，原稿保留')
    instance_query = select(m.DomainInstance).where(m.DomainInstance.workspace_id == p.workspace_id,
        m.DomainInstance.id == context['instance'].id)
    if lock:
        instance_query = instance_query.with_for_update(read=True)
    instance = db.scalar(instance_query.execution_options(populate_existing=True))
    scope = context['frozen_scope']
    if (instance is None or instance.status != 'partially_active'
            or instance.active_release_id != context['release'].id
            or scope['business_id'] not in instance.data.get('enabled_business_ids', [])):
        raise HTTPException(409, '原实例版本或业务当前不可用；可读核查历史，不接受新的判断')
    businesses = [item for item in context['release'].data['snapshot']['businesses']
        if item['id'] == scope['business_id']]
    need(len(businesses) == 1 and businesses[0]['available'] is True)
    pins = copy.deepcopy(context['review_source_pins'])
    need(len(pins) == 3 and len({item['id'] for item in pins}) == 3)
    for pin in sorted(pins, key=lambda item: item['id']):
        query = select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
            m.Capability.id == pin['id'])
        if lock:
            query = query.with_for_update(read=True)
        capability = db.scalar(query.execution_options(populate_existing=True))
        if (capability is None or capability.status != 'published' or capability.kind != pin['kind']
                or capability.content_hash != pin['hash'] or capability.validated_hash != pin['hash']
                or digest(capability.data) != pin['hash']):
            raise HTTPException(409, '原视频来源或业务包当前已撤回或改变；可读核查历史，不能保存新的判断')


def _blank():
    return {'note': '', 'frame_ids': [], 'decision': None}


def _transition(previous, scope, allowed_frames, actor_id, body, operation, saved_at):
    version = 1 if previous is None else previous['version'] + 1
    need(type(body['expected_version']) is int and body['expected_version'] == version - 1
        and set(body['frame_ids']) <= set(allowed_frames))
    opinion = {key: copy.deepcopy(body[key]) for key in ('note', 'frame_ids', 'decision')}
    history = [] if previous is None else copy.deepcopy(previous['human_history'])
    history.append({'version': version, 'operation': operation, 'actor_id': actor_id,
        **opinion, 'saved_at': saved_at})
    return {'schema': STATE_SCHEMA, 'scope': copy.deepcopy(scope), 'actor_id': actor_id,
        'version': version, 'status': 'reviewed' if operation == 'submit' else 'draft',
        'draft': opinion, 'human_history': history, 'allowed_frame_ids': list(allowed_frames)}


def _snapshot(review):
    return {'id': review.id, 'workspace_id': review.workspace_id, 'instance_id': review.instance_id,
        'observation_id': review.observation_id, 'run_id': review.run_id, 'release_id': review.release_id,
        'actor_id': review.actor_id, 'receipt_hash': review.receipt_hash, 'version': review.version,
        'status': review.status, 'data': copy.deepcopy(review.data)}


def _command_pin(command):
    return {'id': command.id, 'workspace_id': command.workspace_id,
        'instance_id': command.instance_id, 'observation_id': command.observation_id,
        'actor_id': command.actor_id, 'review_id': command.review_id,
        'record_version': command.record_version, 'request_id': command.request_id,
        'operation': command.operation, 'method': command.method, 'path': command.path,
        'wire_sha256': command.wire_sha256, 'body_hash': command.body_hash,
        'saved_at': _utc(command.created_at), 'data': copy.deepcopy(command.data)}


def _audit_pin(command):
    return {'schema': 'native-video-review-command-audit-v165',
        **{key: getattr(command, key) for key in ('id', 'instance_id', 'observation_id', 'actor_id',
            'review_id', 'record_version', 'request_id', 'operation', 'method', 'path',
            'wire_sha256', 'body_hash', 'command_hash')},
        'record_hash': digest(command.data['record_snapshot'])}


def _result(command):
    return {'observation_id': command.observation_id, 'version': command.record_version,
        'operation': command.operation, 'status': 'reviewed' if command.operation == 'submit' else 'draft'}


def _checked_command(command, saved_audit):
    need(command.method == 'POST' and command.operation in ('draft', 'submit')
        and type(command.record_version) is int and command.record_version >= 1
        and command.path == command_path(command.instance_id, command.observation_id, command.operation)
        and type(command.data) is dict and set(command.data) == {
            'schema', 'wire', 'body', 'record_snapshot', 'result', 'previous_hash'}
        and command.data['schema'] == 'native-video-review-command-v165')
    raw = command.data['wire'].encode('utf-8', errors='strict')
    body = parse_wire(raw, Draft if command.operation == 'draft' else Submit).model_dump()
    need(wire_sha256(raw) == command.wire_sha256 and body['request_id'] == command.request_id
        and digest(body) == command.body_hash and same(body, command.data['body'])
        and same(command.data['result'], _result(command))
        and command.command_hash == digest(_command_pin(command))
        and saved_audit.workspace_id == command.workspace_id and saved_audit.actor_id == command.actor_id
        and saved_audit.action == ACTION and saved_audit.object_id == command.id
        and same(saved_audit.data, _audit_pin(command)))
    return body


def _inventory(db, p, instance_id, observation_id):
    """Both directions are checked even when every current source is unavailable."""
    _observation_lock(db, p, instance_id, observation_id, shared=True)
    reviews = list(db.scalars(select(Review).where(Review.workspace_id == p.workspace_id,
        Review.observation_id == observation_id)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        or_(Command.observation_id == observation_id,
            Command.review_id.in_([item.id for item in reviews])))))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, or_(m.Audit.data['observation_id'].as_string() == observation_id,
            m.Audit.data['review_id'].as_string().in_([item.id for item in reviews]),
            m.Audit.object_id.in_([item.id for item in commands])))))
    need(len({item.actor_id for item in reviews}) == len(reviews)
        and {item.id for item in commands} == {item.object_id for item in audits}
        and len(commands) == len(audits))
    audit_map = {item.object_id: item for item in audits}
    review_ids = {item.id for item in reviews}
    need(all(item.review_id in review_ids and item.instance_id == instance_id
        and item.observation_id == observation_id for item in commands))
    scopes, checked = [], []
    for review in reviews:
        need(review.instance_id == instance_id and type(review.version) is int and review.version >= 1)
        versions = sorted((item for item in commands if item.review_id == review.id), key=lambda item: item.record_version)
        need([item.record_version for item in versions] == list(range(1, review.version + 1)))
        previous, previous_hash = None, None
        for command in versions:
            need(command.actor_id == review.actor_id and command.data['previous_hash'] == previous_hash)
            body = _checked_command(command, audit_map[command.id])
            snapshot = command.data['record_snapshot']
            need(type(snapshot) is dict and set(snapshot) == {'id', 'workspace_id', 'instance_id',
                'observation_id', 'run_id', 'release_id', 'actor_id', 'receipt_hash', 'version', 'status', 'data'})
            state = snapshot['data']; scope = state['scope']
            need(type(scope) is dict and set(scope) == {'instance_id', 'observation_id', 'run_id', 'release_id', 'receipt_hash'}
                and scope['instance_id'] == instance_id and scope['observation_id'] == observation_id
                and scope['run_id'] == review.run_id and scope['release_id'] == review.release_id
                and scope['receipt_hash'] == review.receipt_hash
                and type(state['allowed_frame_ids']) is list
                and len(state['allowed_frame_ids']) == len(set(state['allowed_frame_ids'])) <= 120)
            for frame_id in state['allowed_frame_ids']:
                identity(frame_id)
            expected = _transition(previous, scope, state['allowed_frame_ids'], review.actor_id,
                body, command.operation, _utc(command.created_at))
            need(same(state, expected) and same(snapshot, {
                'id': review.id, 'workspace_id': p.workspace_id, **scope,
                'actor_id': review.actor_id, 'version': command.record_version,
                'status': expected['status'], 'data': expected})
                and (previous is None or same(scope, previous['scope']))
                and (previous is None or same(state['allowed_frame_ids'], previous['allowed_frame_ids'])))
            previous, previous_hash = state, digest(snapshot)
            checked.append(command)
        need(previous is not None and same(review.data, previous)
            and review.status == previous['status'] and review.record_hash == digest(_snapshot(review)))
        scopes.append(review.data['scope'])
    need(not scopes or all(same(item, scopes[0]) for item in scopes))
    return reviews, checked


def _lookup(db, p, request_id):
    need(type(request_id) is str and _NONCE.fullmatch(request_id) is not None)
    _nonce_lock(db, p, request_id, shared=True)
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id, Command.request_id == request_id).limit(2)))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.data['request_id'].as_string() == request_id,
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id)).limit(2)))
    need(len(commands) == len(audits) <= 1)
    if not commands:
        return None
    need(audits[0].object_id == commands[0].id)
    _checked_command(commands[0], audits[0])
    return commands[0]


def _envelope(request_id, fingerprint, command=None):
    return {'schema': REQUEST_SCHEMA, 'request_id': request_id, 'wire_sha256': fingerprint,
        'outcome': 'unknown' if command is None else 'saved',
        'result': None if command is None else copy.deepcopy(command.data['result'])}


def _actor_inventory(db, p, *, target=None, write=False):
    """Nonce absence cannot conceal damage in another owned review scope.

    Serialize discovery for this actor, then acquire the complete sorted scope
    set before inspecting any inventory. Never expand an already held target
    write lock in a different order.
    """
    _lock_value(db, {'native_video_review_actor_inventory_v166': p.user_id,
        'workspace_id': p.workspace_id}, shared=not write)
    reviews = list(db.scalars(select(Review).where(Review.workspace_id == p.workspace_id,
        Review.actor_id == p.user_id)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id)))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION,
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
    scopes = {(item.instance_id, item.observation_id) for item in [*reviews, *commands]}
    scopes.update((item.data['instance_id'], item.data['observation_id']) for item in audits)
    scopes.update((item.data['scope']['instance_id'], item.data['scope']['observation_id']) for item in reviews)
    scopes.update((item.data['record_snapshot']['instance_id'],
        item.data['record_snapshot']['observation_id']) for item in commands)
    if target is not None:
        scopes.add(target)
    ordered = sorted(scopes)
    for instance_id, observation_id in ordered:
        identity(instance_id); identity(observation_id)
        _observation_lock(db, p, instance_id, observation_id, shared=not write)
    for instance_id, observation_id in ordered:
        _inventory(db, p, instance_id, observation_id)


def _unknown_inventory(db, p, instance_id):
    _actor_inventory(db, p)


@_checked
def request_result(db, p, instance_id, request_id, expected_sha256):
    scoped(db, m.DomainInstance, instance_id, p)
    need(type(expected_sha256) is str and _SHA.fullmatch(expected_sha256) is not None)
    command = _lookup(db, p, request_id)
    if command is not None:
        need(command.instance_id == instance_id and command.wire_sha256 == expected_sha256)
        _inventory(db, p, instance_id, command.observation_id)
    else:
        _unknown_inventory(db, p, instance_id)
    return _envelope(request_id, expected_sha256, command)


@_checked
def detail(db, p, instance_id, observation_id):
    scoped(db, m.DomainInstance, instance_id, p)
    reviews, commands = _inventory(db, p, instance_id, observation_id)
    mine = next((item for item in reviews if item.actor_id == p.user_id), None)
    context, scope, source_issue = None, reviews[0].data['scope'] if reviews else None, None
    try:
        context, actual_scope = _source(db, p, instance_id, observation_id)
        need(scope is None or same(scope, actual_scope))
        scope = actual_scope
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, OSError):
        if scope is None:
            raise HTTPException(409, '原视频数据接收依据不可核，尚不能建立核查意见') from None
        source_issue = '原视频依据当前不可核；原人工意见保留，不能作为现场事实或新的判断依据'
        context = None
    can_write = context is not None
    if can_write:
        try:
            _permission(db, p, context)
        except HTTPException as error:
            can_write = False
            source_issue = str(error.detail)
    submitted = []
    for command in sorted(commands, key=lambda item: (item.created_at, item.id)):
        if command.operation == 'submit':
            item = command.data['record_snapshot']['data']['human_history'][-1]
            submitted.append({key: copy.deepcopy(value) for key, value in item.items() if key != 'operation'})
    actors = {item['actor_id'] for item in submitted}
    labels = dict(db.execute(select(m.User.id, m.User.username).where(m.User.id.in_(actors))).all()) if actors else {}
    for item in submitted:
        item['actor_label'] = labels.get(item['actor_id']) or '原核查人'
    frames, run = [], None
    if context is not None:
        actual = context['run']
        run = {'id': actual.id, 'name': actual.data['name'], 'status': actual.status,
            'input_sha256': actual.data['input_sha256'], 'gateway_capture': True,
            'original_path': '/api/videos/' + actual.id + '/original', 'candidate_only': True,
            'source_time_verified': False, 'physical_state_verified': False}
        frames = [{'id': frame.id, 'sample_index': frame.sample_index, 'seconds': frame.data['seconds'],
            'evidence_id': frame.evidence_id, 'sha256': frame.data['frame_sha256'],
            'width': frame.data['width'], 'height': frame.data['height'],
            'tracks': copy.deepcopy(frame.data['tracks']), 'utc': frame.data['utc'],
            'content_path': '/api/evidence/' + frame.evidence_id + '/content'} for frame in context['frames']]
    return {'schema': VIEW_SCHEMA, **scope, 'version': mine.version if mine else 0,
        'status': mine.status if mine else 'empty', 'owner_id': p.user_id if mine else None,
        'draft': copy.deepcopy(mine.data['draft']) if mine else _blank(),
        'submitted_history': submitted, 'latest_submitted': submitted[-1] if submitted else None,
        'human_history': copy.deepcopy(mine.data['human_history']) if mine else [],
        'source_available': context is not None, 'source_issue': source_issue,
        'run': run, 'frames': frames, 'can_save_draft': can_write, 'can_submit': can_write}


def _lock_value(db, key, *, shared):
    value = int(digest(key)[:16], 16) - (1 << 63)
    function = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    db.execute(text('SELECT ' + function + '(:key)'), {'key': value})


def _nonce_lock(db, p, request_id, *, shared=False):
    _lock_value(db, {'native_video_review_nonce_v165': request_id,
        'workspace_id': p.workspace_id, 'actor_id': p.user_id}, shared=shared)


def _observation_lock(db, p, instance_id, observation_id, *, shared=False):
    # Team history is one inventory even though each actor owns a separate row.
    _lock_value(db, {'native_video_review_observation_v165': observation_id,
        'workspace_id': p.workspace_id, 'instance_id': instance_id}, shared=shared)


@_checked
def mutate(db, p, instance_id, observation_id, operation, body, raw, method, path):
    need(operation in ('draft', 'submit') and method == 'POST'
        and path == command_path(instance_id, observation_id, operation)
        and same(parse_wire(raw, Draft if operation == 'draft' else Submit).model_dump(), body.model_dump()))
    fingerprint = wire_sha256(raw)
    _nonce_lock(db, p, body.request_id)
    original = _lookup(db, p, body.request_id)
    if original is not None:
        need(original.instance_id == instance_id and original.observation_id == observation_id
            and original.method == method and original.path == path
            and original.wire_sha256 == fingerprint and original.data['wire'] == raw.decode('utf-8'))
        _inventory(db, p, instance_id, observation_id)
        return _envelope(body.request_id, fingerprint, original)
    _actor_inventory(db, p, target=(instance_id, observation_id), write=True)
    reviews, _ = _inventory(db, p, instance_id, observation_id)
    mine = next((item for item in reviews if item.actor_id == p.user_id), None)
    actual_version = mine.version if mine else 0
    if body.expected_version != actual_version:
        raise HTTPException(409, '你自己的核查稿已有新版本；本机原稿保留，请读回对照后再保存')
    context, scope = _source(db, p, instance_id, observation_id)
    _permission(db, p, context, lock=True)
    need(not reviews or same(reviews[0].data['scope'], scope))
    allowed_frames = [frame.id for frame in context['frames']]
    need(mine is None or same(mine.data['allowed_frame_ids'], allowed_frames))
    saved_at = db.scalar(select(func.clock_timestamp()))
    value = body.model_dump()
    previous_hash = mine.record_hash if mine else None
    state = _transition(mine.data if mine else None, scope, allowed_frames, p.user_id,
        value, operation, _utc(saved_at))
    if mine is None:
        mine = Review(workspace_id=p.workspace_id, actor_id=p.user_id, instance_id=instance_id,
            observation_id=observation_id, run_id=scope['run_id'], release_id=scope['release_id'],
            receipt_hash=scope['receipt_hash'], status=state['status'], data=state, version=1,
            record_hash='')
        db.add(mine)
    else:
        mine.version = state['version']; mine.status = state['status']; mine.data = state
    db.flush()
    snapshot = _snapshot(mine)
    mine.record_hash = digest(snapshot)
    command = Command(workspace_id=p.workspace_id, actor_id=p.user_id, instance_id=instance_id,
        observation_id=observation_id, review_id=mine.id, record_version=mine.version,
        request_id=body.request_id, operation=operation, method=method, path=path,
        wire_sha256=fingerprint, body_hash=digest(value), created_at=saved_at,
        command_hash='', data={})
    db.add(command); db.flush()
    command.data = {'schema': 'native-video-review-command-v165', 'wire': raw.decode('utf-8'),
        'body': value, 'record_snapshot': snapshot, 'previous_hash': previous_hash,
        'result': _result(command)}
    command.command_hash = digest(_command_pin(command))
    audit(db, p, ACTION, command.id, _audit_pin(command))
    db.flush()
    _inventory(db, p, instance_id, observation_id)
    return _envelope(body.request_id, fingerprint, command)
