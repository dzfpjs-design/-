"""Explicit published entry policy and transaction-bound automation handoff.

Configuration review does not itself start a Task. The real Subscription and
leased occurrence are checked again before this private token can admit the
ordinary Task helper. Booleans or caller JSON are never handoff authority.
"""
import copy
import re
from fastapi import HTTPException

from . import models as m
from .contracts import digest
from .services import scoped, validate

KEY = 'business_automation_admission_v1'
SCHEMA = 'business-automation-admission-v1'
PROVIDER = 'received_camera_frame_v1'
_ISSUER = object()
_TOKEN_FIELDS = ('subscription_id', 'job_id', 'unit', 'source_target', 'body_hash',
    'instance_id', 'release_id', 'business_id', 'actor_id', 'binding_hash', 'admission_hash')


def need(value, message='原自动化授权、真实发生或任务启动依据无法核对'):
    if not value: raise HTTPException(409, message)


def _seal(value): return {**value, 'admission_hash': digest(value)}


def _manual(binding):
    # This family is produced only by the authored human-flow template. Its
    # store requires explicitly confirmed manual-only policy at adoption;
    # verify_plan verifies that original binding before we ever call here.
    # It has no anomaly entry mode. Document ports still fail this provider's
    # independent input-compatibility check below.
    if binding.get('schema') == 'authored-workflow-binding-v208': return True
    from .authored_image_workflow_graph_v243 import BINDING as IMAGE_BINDING, TEMPLATE as IMAGE_TEMPLATE
    if binding.get('schema') == IMAGE_BINDING:
        # The authored image recipe is a separately checked, fixed entry
        # family. Its sealed confirmed policy grants the actual trigger; it
        # need not also grant manual starts for an automatic-only business.
        return binding.get('template') == IMAGE_TEMPLATE
    return (binding.get('schema') == 'business-flow-execution-binding-v1'
        and binding.get('flow', {}).get('source_flow', {}).get('trigger', {}).get('kind') == 'manual')


def _build(snapshot):
    from .setup_automation import policy_hash
    from .instance_semantic_runtime_dispatch import modern
    plan = snapshot.get('semantic_runtime_plan', {})
    if plan.get('schema') not in {'instance-business-flow-graph-runtime-v1', 'instance-authored-workflow-runtime-v208'}:
        return None
    plan = modern(snapshot).verify_plan(snapshot)
    by_name = {item['binding']['business_name']: item['binding'] for item in plan['graph_adoptions']}
    declarations = []
    for business in snapshot['businesses']:
        binding = by_name.get(business['name'])
        rule = snapshot.get('automation', {}).get('policies', {}).get(business['name'])
        if not business.get('available') or not binding or not _manual(binding) or type(rule) is not dict: continue
        review = rule.get('flow_review', {})
        if (review.get('status') != 'confirmed' or review.get('policy_hash') != policy_hash(rule)
                or not re.fullmatch(r'[a-f0-9]{64}', review.get('flow_hash', ''))): continue
        triggers = rule.get('triggers', [])
        if type(triggers) is not list or any(value not in {'人工发起','新观测到达','定时运行','异常事件'} for value in triggers): continue
        allowed = [key for key, label in (('new_data','新观测到达'),('daily_time','定时运行')) if label in triggers]
        if not allowed: continue
        time = rule.get('time') if 'daily_time' in allowed else None
        if time is not None and (type(time) is not str or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', time)): continue
        graph = binding['graph']
        properties = graph.get('input_schema', {}).get('properties', {})
        if 'task_image_ids' not in properties or any(key in properties for key in
                ('task_audio_ref','task_sound_ref','task_scene_refs','task_comparison_refs')): continue
        try: validate(properties['task_image_ids'], ['00000000-0000-0000-0000-000000000001'])
        except HTTPException: continue
        # Match the ordinary Task input descriptor: the server supplies its
        # real object/task/media fields, but this provider supplies no user
        # answers. Preserve root constraints/definitions so allOf/oneOf/$ref
        # requirements cannot hide additional mandatory user inputs.
        from .workflow_graph import SYSTEM_INPUTS
        user_schema = copy.deepcopy(graph['input_schema'])
        user_schema['properties'] = {key: value for key, value in properties.items() if key not in SYSTEM_INPUTS}
        user_schema['required'] = [key for key in user_schema.get('required', []) if key not in SYSTEM_INPUTS]
        try: validate(user_schema, {})
        except HTTPException: continue
        from .runtime_task_documents import ports
        if ports(graph): continue
        actions = binding.get('node_actions', {})
        abilities = {node['id'] for node in graph['nodes'] if node['kind'] == 'ability'}
        if set(actions) != abilities or any(rule.get('rules', {}).get(action, {}).get('mode') != '自动运行'
                                           for action in actions.values()): continue
        caps = [{'id': node['execution']['capability_id'], 'hash': node['execution']['capability_hash']}
                for node in graph['nodes'] if node['kind'] == 'ability']
        declarations.append(_seal({'business_id': business['id'], 'business_name': business['name'],
            'binding_hash': binding['binding_hash'], 'graph_hash': digest(graph),
            'flow_hash': review['flow_hash'], 'policy_hash': policy_hash(rule),
            'capabilities_hash': digest(sorted(caps, key=lambda item: item['id'])),
            'roles_hash': digest(binding['role_bindings']), 'allowed_triggers': allowed,
            'time': time, 'provider_ids': [PROVIDER]}))
    if not declarations: return None
    return _seal({'schema': SCHEMA, 'businesses': sorted(declarations, key=lambda item: item['business_id'])})


def compile_admission(snapshot, configuration):
    """Called only for a new compilation, never to upgrade old releases."""
    from .setup_automation import review_state
    value = _build(snapshot)
    if value:
        for item in value['businesses']:
            need(review_state(configuration, item['business_name'])['status'] == 'confirmed',
                 '自动入口须重新明确确认当前业务流程与规则')
    return value


def policy(snapshot, business):
    if KEY not in snapshot: return None
    expected = _build(snapshot)
    need(expected is not None and snapshot[KEY] == expected)
    actual = next((item for item in snapshot['businesses'] if item['id'] == business.get('id')), None)
    need(actual == business)
    found = [item for item in expected['businesses'] if item['business_id'] == business['id']]
    need(len(found) <= 1)
    return copy.deepcopy(found[0]) if found else None


class AutomationHandoff:
    """Server-only identity, bound to this Session and current transaction."""
    __slots__ = (*_TOKEN_FIELDS, '_issuer', '_db', '_transaction', '_hash')

    def __init__(self, issuer, db, **fields):
        need(issuer is _ISSUER and set(fields) == set(_TOKEN_FIELDS))
        for key, value in fields.items(): setattr(self, key, copy.deepcopy(value))
        self._issuer, self._db, self._transaction = issuer, db, db.get_transaction()
        self._hash = digest(fields)


def _token(db, token):
    need(type(token) is AutomationHandoff and token._issuer is _ISSUER and token._db is db
         and token._transaction is not None and token._transaction is db.get_transaction()
         and token._hash == digest({key: getattr(token, key) for key in _TOKEN_FIELDS}),
         '自动入口仅接受本次真实授权事务的可信交接，不能用布尔值或旧事务代替')


def entry_matches(binding, token):
    need(type(token) is AutomationHandoff and token._issuer is _ISSUER)
    _token(token._db, token)
    need(_manual(binding) and token.binding_hash == binding['binding_hash'])
    return True


def _image(db, p, body, unit, instance_id, *, physical):
    from .business_automation_sources import KEY as SOURCE_KEY, checked_unit
    need(not (body.signal_id or body.assessment_id or body.advice_id or body.audio_ids
              or body.scene_ids or body.comparison_scene_ids) and len(body.image_ids) == 1
         and body.entity_ids == [unit['source_pin']['scope']['entity_id']])
    image = scoped(db, m.Evidence, body.image_ids[0], p)
    need(image.data.get('purpose') == 'workflow_input_image' and image.data.get('instance_id') == instance_id
         and image.data.get(SOURCE_KEY) == {'unit': unit}
         and image.sha256 == unit['source_pin']['image']['sha256'])
    if physical:
        from .workflow_data_quality_v219 import read_original, settings
        _, original, content = checked_unit(db, p, unit)
        need(image.media_type == original.media_type and image.size_bytes == original.size_bytes
             and read_original(settings().storage_dir, p.workspace_id, image) == content)
    return image


def prepare_handoff(db, p, subscription, job, body, instance, release):
    from . import business_automation_service as service
    from .workspace_api import creation_body_hash
    actual, _, state = service.checked_occurrence(db, p, job)
    need(actual.id == subscription.id)
    current_instance, current_release, origin = service.assert_dispatch_live(db, p, subscription, job)
    need(current_instance.id == instance.id and current_release.id == release.id
         and body.release_id == release.id and body.business_id == subscription.business_id)
    unit = state['source_unit']; need(type(unit) is dict and unit['target_hash'] == digest(origin['source_target']))
    _image(db, p, body, unit, instance.id, physical=True)
    business = next(item for item in release.data['snapshot']['businesses'] if item['id'] == body.business_id)
    declaration = policy(release.data['snapshot'], business)
    need(declaration is not None and origin['policy'] == declaration
         and origin['selection']['trigger_id'] in declaration['allowed_triggers'])
    return AutomationHandoff(_ISSUER, db, subscription_id=subscription.id, job_id=job.id,
        unit=unit, source_target=origin['source_target'], body_hash=creation_body_hash(body),
        instance_id=instance.id, release_id=release.id, business_id=body.business_id, actor_id=p.user_id,
        binding_hash=declaration['binding_hash'], admission_hash=declaration['admission_hash'])


def validate_handoff(db, p, token, body, instance, release):
    from .business_automation_models import BusinessAutomationSubscription as Subscription
    from . import business_automation_service as service
    from .workspace_api import creation_body_hash
    _token(db, token)
    need(token.actor_id == p.user_id and token.instance_id == instance.id and token.release_id == release.id
         and token.business_id == body.business_id and token.body_hash == creation_body_hash(body))
    subscription = scoped(db, Subscription, token.subscription_id, p)
    job = scoped(db, m.Job, token.job_id, p)
    actual, _, state = service.checked_occurrence(db, p, job)
    need(actual.id == subscription.id and state['source_unit'] == token.unit)
    _, current_release, origin = service.assert_dispatch_live(db, p, subscription, job)
    need(current_release.id == release.id and origin['source_target'] == token.source_target)
    _image(db, p, body, token.unit, instance.id, physical=True)
    return token


def attach_task(db, p, task, token, body):
    from . import business_automation_service as service
    _token(db, token)
    need(task.workspace_id == p.workspace_id and task.owner_id == token.actor_id)
    service.attach_task_witness(db, p, task, token, body)


def verify_task(db, p, task):
    """Historical proof; current source health or paused grant is irrelevant."""
    from . import business_automation_service as service
    from .entity_release_compat import verify_task_binding_snapshot
    from .workspace_api import NewTask, creation_body_hash
    witness = service.checked_task_witness(db, p, task)
    if witness is None: return None
    subscription, unit = witness['subscription'], witness['unit']
    body = NewTask.model_validate(witness['body'])
    need(creation_body_hash(body) == witness['body_hash'] == task.data['creation_hash']
         and task.data['instance_id'] == subscription.instance_id
         and task.data['instance_release_id'] == subscription.release_id
         and task.data['business_id'] == subscription.business_id
         and task.data['entity_ids'] == [unit['source_pin']['scope']['entity_id']])
    release = scoped(db, m.InstanceRelease, subscription.release_id, p)
    binding = verify_task_binding_snapshot(task, release)
    need(binding is not None)
    _image(db, p, body, unit, subscription.instance_id, physical=True)
    return subscription
