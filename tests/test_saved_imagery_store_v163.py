"""Focused pure V163 rules and history checks, not PostgreSQL acceptance.

The snapshots below are deliberately synthetic and establish no assessment,
source, approval, Task execution, human decision or modern receipt authority.
Business/shape/transition functions are real. Two narrowly named read seams
exercise only marker matching and original-request response control flow; they
do not replace or claim to test inventory(), authorization, SQL locks or files.
Root separately verifies the real HTTP/Worker/Gateway/Memory/ACK journey.
"""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from generic_agents.contracts import digest
from generic_agents.saved_imagery_contracts_v163 import Draft, Update, Stop
from generic_agents import saved_imagery_store_v163 as store


WORKSPACE = '163def00-0000-4000-8000-000000000001'
INSTANCE = '163def00-0000-4000-8000-000000000002'
OTHER_INSTANCE = '163def00-0000-4000-8000-000000000003'
ASSESSMENT = '163def00-0000-4000-8000-000000000004'
ENTITY = '163def00-0000-4000-8000-000000000005'
OWNER = '163def00-0000-4000-8000-000000000006'
OTHER_OWNER = '163def00-0000-4000-8000-000000000007'
PLAN = '163def00-0000-4000-8000-000000000008'
RELEASE = '163def00-0000-4000-8000-000000000009'
TASK = '163def00-0000-4000-8000-00000000000a'
PACKAGE = '163def00-0000-4000-8000-00000000000b'
BUSINESS = 'business_0123456789abcdef'
REQUEST = 'saved-imagery-pure-original'


def business():
    """Structural fixture only: no published capability or package is implied."""
    return {'id': BUSINESS, 'available': True,
        'execution_mode': 'registered_ability_workflow', 'flow': {'steps': [
            {'id': 'compare', 'agent': '多模感知智能体', 'condition': '',
             'nextId': 'review', 'execution': {
                 'adapter': 'local_geo_pair', 'mode': '执行前确认'}},
            {'id': 'review', 'agent': '人工确认', 'condition': '',
             'nextId': 'end'},
        ]}}


def draft_body():
    return {'request_id': REQUEST, 'assessment_id': ASSESSMENT, 'release_id': RELEASE,
        'business_id': None, 'comparison_scene_ids': [], 'comparison_confirmed': False,
        'title': '', 'reason': ''}


def draft_snapshot():
    request = {'instance_id': INSTANCE, 'release_id': RELEASE,
        'entity_ids': [ENTITY], 'purpose': '仅为纯结构测试构造的原条件',
        'goal': 'current_state', 'required_modalities': ['imagery'],
        'lookback_hours': 24, 'question_id': None}
    result = {'objects': [{'object': {'id': ENTITY}, 'gaps': [
        {'kind': 'missing_saved_processing', 'modality': 'imagery'}],
        'current_state_sufficient': False}], 'records': []}
    return {'id': PLAN, 'workspace_id': WORKSPACE, 'instance_id': INSTANCE,
        'assessment_id': ASSESSMENT, 'entity_id': ENTITY, 'owner_id': OWNER,
        'status': 'draft', 'task_id': None, 'reassessment_id': None, 'version': 1,
        'data': {'schema': 'saved-imagery-record-v163',
            'draft': {key: deepcopy(value) for key, value in draft_body().items()
                if key not in {'request_id', 'assessment_id'}},
            'assessment': {'id': ASSESSMENT, 'version': 1, 'request': request,
                'request_hash': digest(request), 'result': result, 'result_hash': digest(result)},
            'selection': None, 'task_request': None, 'task_creation_hash': None,
            'task_owner_id': None, 'reassessment_request': None,
            'reassessment_creation_hash': None, 'stop_reason': ''}}


def assign(value, path, replacement):
    current = value
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = deepcopy(replacement)


def rejected(function, *args):
    with pytest.raises(HTTPException) as error:
        function(*args)
    assert error.value.status_code == 409


@pytest.mark.parametrize('agent,next_id', [
    ('多模感知智能体', 'review'), ('数据管理智能体', 'auto'),
])
def test_only_the_confirmed_rgb_then_terminal_human_structure_passes(agent, next_id):
    value = business(); first = value['flow']['steps'][0]
    first['agent'] = agent; first['nextId'] = next_id
    original = deepcopy(value)
    assert store._business_shape(value) is None
    assert store.same(value, original)


@pytest.mark.parametrize('path,replacement', [
    (('available',), False),
    (('execution_mode',), 'confirmed_graph_workflow'),
    (('flow', 'steps', 0, 'execution', 'mode'), '自动运行'),
    (('flow', 'steps', 0, 'execution', 'adapter'), 'local_learned_geo_pair'),
    (('flow', 'steps', 0, 'execution', 'adapter'), 'http'),
    (('flow', 'steps', 0, 'agent'), '人工确认'),
    (('flow', 'steps', 0, 'condition'), '满足条件才运行'),
    (('flow', 'steps', 0, 'nextId'), 'end'),
    (('flow', 'steps', 0, 'planning_tools'), ['some-tool']),
    (('flow', 'steps', 1, 'agent'), '数据管理智能体'),
    (('flow', 'steps', 1, 'condition'), '按另一分支完成'),
    (('flow', 'steps', 1, 'nextId'), 'compare'),
    (('flow', 'steps', 1, 'planning_tools'), ['some-tool']),
    (('flow', 'steps', 1, 'execution'), {'adapter': 'local_geo_pair'}),
    (('flow', 'steps', 1, 'id'), 'compare'),
])
def test_automatic_external_hidden_machine_and_human_bypass_structures_are_rejected(path, replacement):
    value = business(); assign(value, path, replacement); original = deepcopy(value)
    rejected(store._business_shape, value)
    assert store.same(value, original)


@pytest.mark.parametrize('count', [0, 1, 3])
def test_scope_is_exactly_two_steps_not_a_prefix_check(count):
    value = business()
    value['flow']['steps'] = value['flow']['steps'][:count] if count < 2 else [
        *value['flow']['steps'], {'id': 'extra', 'agent': '人工确认'}]
    rejected(store._business_shape, value)


def test_synthetic_empty_draft_shape_is_valid_but_has_no_execution_or_receipt_claim():
    saved = draft_snapshot(); original = deepcopy(saved)
    assert store._shape(saved) is None
    assert store.same(saved, original)
    assert saved['task_id'] is None and saved['reassessment_id'] is None
    assert all(saved['data'][key] is None for key in (
        'selection', 'task_request', 'task_creation_hash', 'reassessment_request'))


@pytest.mark.parametrize('path,value', [
    (('version',), True), (('version',), 1.0), (('version',), 0),
    (('data', 'assessment', 'version'), True),
    (('data', 'assessment', 'version'), 1.0),
    (('data', 'assessment', 'version'), 0),
    (('data', 'task_owner_id'), OWNER),
    (('data', 'task_request'), {}),
    (('data', 'reassessment_request'), {}),
    (('data', 'selection'), {}),
    (('status',), 'completed'),
])
def test_closed_historical_shape_rejects_typed_versions_and_partial_authority(path, value):
    saved = draft_snapshot(); assign(saved, path, value)
    rejected(store._shape, saved)


def test_real_transition_functions_keep_prior_human_text_and_snapshot_unchanged():
    created = draft_snapshot(); original = deepcopy(created)
    store._transition(None, created, 'create', Draft.model_validate(draft_body()))
    body = Update.model_validate({'request_id': REQUEST, 'version': 1,
        'business_id': BUSINESS, 'comparison_scene_ids': [], 'comparison_confirmed': False,
        'title': ' 新草稿标题 ', 'reason': ' 保留未完整选择的理由 '})
    updated = deepcopy(created); updated['version'] = 2
    updated['data']['draft'] = store._draft(body, RELEASE)
    store._transition(created, updated, 'update', body)
    stop = Stop(request_id=REQUEST, version=2, reason=' 停止本协调，不冒称取消原任务 ')
    stopped = deepcopy(updated); stopped['version'] = 3; stopped['status'] = 'stopped'
    stopped['data']['stop_reason'] = stop.reason
    store._transition(updated, stopped, 'stop', stop)
    assert store.same(created, original)
    assert updated['data']['draft']['reason'] == body.reason
    assert stopped['data']['stop_reason'] == stop.reason


@pytest.mark.parametrize('change', ['owner', 'version', 'draft', 'original_conditions'])
def test_update_transition_cannot_rewrite_other_history_even_with_self_consistent_request_hash(change):
    previous = draft_snapshot()
    body = Update.model_validate({'request_id': REQUEST, 'version': 1,
        'business_id': None, 'comparison_scene_ids': [], 'comparison_confirmed': False,
        'title': '', 'reason': ''})
    saved = deepcopy(previous); saved['version'] = 2
    if change == 'owner': saved['owner_id'] = OTHER_OWNER
    elif change == 'version': saved['version'] = 3
    elif change == 'draft': saved['data']['draft']['title'] = '不是原命令的标题'
    else:
        original = saved['data']['assessment']
        original['request']['purpose'] = '联合重封也不能改原目的'
        original['request_hash'] = digest(original['request'])
    rejected(store._transition, previous, saved, 'update', body)


@pytest.mark.parametrize('operation', ['update', 'stop', 'create_task', 'reassess', 'unknown'])
def test_stopped_coordination_has_no_new_transition_or_implicit_revival(operation):
    previous = draft_snapshot(); previous['status'] = 'stopped'
    previous['data']['stop_reason'] = '已明确停止'
    saved = deepcopy(previous); saved['version'] += 1
    body = Stop(request_id=REQUEST, version=previous['version'], reason='不能继续')
    if operation == 'update':
        body = Update.model_validate({'request_id': REQUEST, 'version': previous['version'],
            'business_id': None, 'comparison_scene_ids': [], 'comparison_confirmed': False,
            'title': '', 'reason': ''})
    rejected(store._transition, previous, saved, operation, body)


def follow_request(saved):
    original = saved['data']['assessment']['request']
    return {'request_id': 'saved-imagery-reassess-' + saved['id'],
        'parent_id': saved['assessment_id'], 'instance_id': saved['instance_id'],
        'release_id': original['release_id'], 'entity_ids': deepcopy(original['entity_ids']),
        **{key: deepcopy(original[key]) for key in (
            'purpose', 'goal', 'required_modalities', 'lookback_hours', 'question_id')}}


@pytest.mark.parametrize('field,value', [
    ('request_id', 'replacement-assessment-request'), ('parent_id', PLAN),
    ('instance_id', OTHER_INSTANCE), ('release_id', ASSESSMENT),
    ('entity_ids', [OTHER_OWNER]), ('purpose', '另一个目的'),
    ('goal', 'saved_processing'), ('required_modalities', ['imagery', 'video']),
    ('lookback_hours', 24.0), ('lookback_hours', True), ('question_id', PLAN),
])
def test_followup_cannot_switch_scope_criteria_or_numeric_kind(field, value):
    saved = draft_snapshot(); body = follow_request(saved)
    assert store._follow_request(saved, body) is None
    body[field] = value
    rejected(store._follow_request, saved, body)


def marker_link():
    """Only the marker predicate's inputs; deliberately not valid source proof."""
    objects = [{'id': ENTITY, 'version': 2,
        'attributes': {'name': '纯测试对象', 'count': 7}}]
    original_ref = {'type': 'Entity', 'id': ENTITY, 'signature': digest({
        'version': objects[0]['version'], 'status': None,
        'data': objects[0]['attributes']})}
    plan = SimpleNamespace(id=PLAN, instance_id=INSTANCE, assessment_id=ASSESSMENT,
        entity_id=ENTITY, task_id=TASK, reassessment_id=None,
        data={'selection': {'package_id': PACKAGE, 'inputs': []},
              'assessment': {'request': {'object_refs': [original_ref]}},
              'task_creation_hash': 'a' * 64, 'task_owner_id': OWNER,
              'task_request': {'request_id': 'saved-imagery-task-' + PLAN,
                  'release_id': RELEASE, 'business_id': BUSINESS}})
    marker = {'schema': 'saved-imagery-task-origin-v163', 'plan_id': PLAN,
        'assessment_id': ASSESSMENT, 'instance_id': INSTANCE, 'entity_id': ENTITY,
        'selection_hash': store.hash_value(plan.data['selection']), 'creation_hash': 'a' * 64}
    task = SimpleNamespace(id=TASK, owner_id=OWNER, package_id=PACKAGE, data={
        store.MARKER: deepcopy(marker), 'creation_request_id': 'saved-imagery-task-' + PLAN,
        'creation_hash': 'a' * 64, 'instance_id': INSTANCE, 'instance_release_id': RELEASE,
        'business_id': BUSINESS, 'entity_ids': [ENTITY], 'comparison_scene_snapshots': [],
        'comparison_confirmed': True, 'object_snapshots': deepcopy(objects)})
    return plan, task, marker


@pytest.mark.parametrize('fault', [None, 'extra', 'missing', 'other_plan',
    'selection_hash', 'creation_hash', 'object_attributes', 'object_version'])
def test_real_task_marker_match_is_closed_and_bound_to_this_plan(monkeypatch, fault):
    plan, task, marker = marker_link()
    principal = SimpleNamespace(workspace_id=WORKSPACE)

    class CreatedAuditReadSeam:
        """Synthetic one-query return, not a persisted workflow-created proof."""
        reads = 0

        def scalars(self, query):
            assert query.column_descriptions[0]['entity'] is store.Audit
            self.reads += 1
            return [SimpleNamespace(workspace_id=WORKSPACE, object_id=TASK,
                action='workflow.created', actor_id=OWNER,
                data={'release': RELEASE, 'business': BUSINESS})]

    db = CreatedAuditReadSeam()

    def existing_row(actual_db, actual_principal, model, identity, lock=False):
        assert actual_db is db and actual_principal is principal
        assert model is store.Task and identity == TASK and lock is False
        return task

    monkeypatch.setattr(store, '_row', existing_row)
    assert store._marker(plan) == marker
    assert store._links(db, principal, plan) is None
    assert db.reads == 1
    if fault is None: return
    actual = task.data[store.MARKER]
    if fault == 'extra': actual['approved'] = True
    elif fault == 'missing': del actual['selection_hash']
    elif fault == 'other_plan': actual['plan_id'] = ASSESSMENT
    elif fault == 'selection_hash': actual['selection_hash'] = 'b' * 64
    elif fault == 'creation_hash': actual['creation_hash'] = 'b' * 64
    elif fault == 'object_attributes':
        task.data['object_snapshots'][0]['attributes']['count'] = 8
    else:
        task.data['object_snapshots'][0]['version'] += 1
    rejected(store._links, db, principal, plan)


@pytest.mark.parametrize('left,right', [(True, 1), (1, 1.0), (-0.0, 0.0)])
def test_typed_history_and_hash_do_not_alias_boolean_integer_float_or_signed_zero(left, right):
    assert not store.same({'value': left}, {'value': right})
    assert store.hash_value({'value': left}) != store.hash_value({'value': right})


@pytest.mark.parametrize('case', ['saved', 'cross_instance', 'other_owner', 'wrong_sha'])
def test_global_actor_nonce_lookup_never_leaks_other_instance_result_or_adopts_other_actor(monkeypatch, case):
    """Control-flow seam only: inventory and instance SQL are tested elsewhere."""
    db = object(); principal = SimpleNamespace(user_id=OWNER)
    result = {'id': PLAN, 'version': 1, 'operation': 'create', 'status': 'draft',
        'task_id': None, 'reassessment_id': None}
    original = deepcopy(result)
    command = SimpleNamespace(actor_id=OWNER, request_id=REQUEST, instance_id=INSTANCE,
        wire_sha256='a' * 64, data={'result': result})
    if case == 'other_owner': command.actor_id = OTHER_OWNER
    monkeypatch.setattr(store, 'inventory', lambda actual_db, actual_p: ([], [command], []))

    def actual_instance(actual_db, actual_p, model, identity, lock=False):
        assert actual_db is db and actual_p is principal and model is store.DomainInstance
        assert identity in {INSTANCE, OTHER_INSTANCE} and lock is False
        return SimpleNamespace(id=identity)

    monkeypatch.setattr(store, '_row', actual_instance)
    if case == 'saved':
        value = store.request_result(db, principal, INSTANCE, REQUEST, 'a' * 64)
        assert value == {'schema': 'saved-imagery-request-v163', 'request_id': REQUEST,
            'wire_sha256': 'a' * 64, 'outcome': 'saved', 'result': original}
        value['result']['version'] = 999
    elif case == 'other_owner':
        assert store.request_result(db, principal, INSTANCE, REQUEST, 'a' * 64) == {
            'schema': 'saved-imagery-request-v163', 'request_id': REQUEST,
            'wire_sha256': 'a' * 64, 'outcome': 'unknown', 'result': None}
    else:
        identity = OTHER_INSTANCE if case == 'cross_instance' else INSTANCE
        sha = 'b' * 64 if case == 'wrong_sha' else 'a' * 64
        rejected(store.request_result, db, principal, identity, REQUEST, sha)
    assert store.same(command.data['result'], original)
