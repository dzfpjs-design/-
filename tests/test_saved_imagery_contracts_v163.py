"""Pure V163 command validation against the actual contracts module.

No Session, Task, Job, Gateway, original file or receipt is manufactured here.
Valid drafts do not prove executable business membership, distinct original
bytes, current authority or evidence sufficiency. Exact-wire persistence,
actor/nonce recovery, transactional creation and real processing belong to the
store/API and Root's PostgreSQL/HTTP acceptance, not this protocol suite.
"""
from copy import deepcopy
import json

import pytest

from generic_agents.saved_imagery_contracts_v163 import (
    ERROR, Draft, Update, CreateTask, Reassess, Stop, parse_wire,
)


REQUEST = 'saved-imagery-original_163'
ASSESSMENT = '163abc00-0000-4000-8000-000000000001'
RELEASE = '163abc00-0000-4000-8000-000000000002'
SCENE_A = '163abc00-0000-4000-8000-000000000003'
SCENE_B = '163abc00-0000-4000-8000-000000000004'
BUSINESS = 'business_0123456789abcdef'
MAX_BYTES = 16 * 1024


def choices():
    return {'business_id': BUSINESS, 'comparison_scene_ids': [SCENE_A, SCENE_B],
        'comparison_confirmed': True, 'title': ' A/B 原影像补充处理 ',
        'reason': ' 仅处理已保存原件；不声明新增观测或现场变化。 '}


def draft_body():
    return {'request_id': REQUEST, 'assessment_id': ASSESSMENT,
        'release_id': RELEASE, **choices()}


def update_body():
    return {'request_id': REQUEST, **choices(), 'version': 2}


def create_task_body():
    return {'request_id': REQUEST, 'version': 2, 'confirm_create_task': True}


def reassess_body():
    return {'request_id': REQUEST, 'version': 3, 'confirm_reassess': True}


def stop_body():
    return {'request_id': REQUEST, 'version': 3,
        'reason': ' 停止后续协调；原任务仍需沿原入口取消。 '}


COMMANDS = [(Draft, draft_body), (Update, update_body),
    (CreateTask, create_task_body), (Reassess, reassess_body), (Stop, stop_body)]


def wire(body):
    return json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def assert_typed_equal(actual, expected):
    assert type(actual) is type(expected)
    if type(expected) is dict:
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_typed_equal(actual[key], expected[key])
    elif type(expected) is list:
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            assert_typed_equal(left, right)
    else:
        assert actual == expected


def reject_wire(original, model=Draft):
    with pytest.raises(ValueError) as failure:
        parse_wire(original, model)
    assert failure.value.args == (ERROR,)


def reject_body(body, model):
    original = deepcopy(body)
    reject_wire(wire(body), model)
    assert_typed_equal(body, original)


@pytest.mark.parametrize('model,factory', COMMANDS)
def test_actual_command_models_preserve_every_original_typed_field(model, factory):
    body = factory(); original = deepcopy(body)
    parsed = parse_wire(wire(body), model)
    assert type(parsed) is model
    assert_typed_equal(parsed.model_dump(), original)
    assert_typed_equal(body, original)


@pytest.mark.parametrize('scenes', [[], [SCENE_A], [SCENE_A, SCENE_B]])
def test_incomplete_drafts_can_be_saved_without_execution_authority(scenes):
    body = draft_body()
    body.update(business_id=None, comparison_scene_ids=scenes,
        comparison_confirmed=False, title='', reason='')
    assert_typed_equal(parse_wire(wire(body), Draft).model_dump(), body)
    update = {key: value for key, value in body.items()
        if key not in {'assessment_id', 'release_id'}}
    update['version'] = 1
    assert_typed_equal(parse_wire(wire(update), Update).model_dump(), update)


def test_compiled_business_identifier_is_not_mistaken_for_a_uuid():
    body = draft_body()
    assert parse_wire(wire(body), Draft).business_id == BUSINESS
    body['business_id'] = None
    assert parse_wire(wire(body), Draft).business_id is None


@pytest.mark.parametrize('model,factory', COMMANDS)
def test_each_command_is_closed_and_requires_its_explicit_fields(model, factory):
    for field in factory():
        body = factory(); del body[field]
        reject_body(body, model)
    for field, value in [('schema', 'saved-imagery-client-authority-v163'),
            ('task_id', SCENE_A), ('approved', True), ('current_state_sufficient', True)]:
        reject_body({**factory(), field: value}, model)


@pytest.mark.parametrize('field,value', [
    ('assessment_id', ASSESSMENT), ('release_id', RELEASE),
    ('entity_ids', [SCENE_A]), ('goal_spec', {}), ('graph_inputs', {}),
])
def test_update_cannot_replace_original_scope_or_add_task_execution_fields(field, value):
    reject_body({**update_body(), field: value}, Update)


@pytest.mark.parametrize('model,factory', [
    (Update, update_body), (CreateTask, create_task_body),
    (Reassess, reassess_body), (Stop, stop_body),
])
@pytest.mark.parametrize('version', [True, False, 1.0, '1', 0, -1, None, [], {}])
def test_cas_version_is_an_actual_positive_integer(model, factory, version):
    body = factory(); body['version'] = version
    reject_body(body, model)


@pytest.mark.parametrize('model,factory,field', [
    (Draft, draft_body, 'comparison_confirmed'),
    (Update, update_body, 'comparison_confirmed'),
    (CreateTask, create_task_body, 'confirm_create_task'),
    (Reassess, reassess_body, 'confirm_reassess'),
])
@pytest.mark.parametrize('value', [1, 0, 1.0, 'true', None])
def test_boolean_confirmation_never_coerces_numeric_or_text_values(model, factory, field, value):
    body = factory(); body[field] = value
    reject_body(body, model)


@pytest.mark.parametrize('model,factory,field', [
    (CreateTask, create_task_body, 'confirm_create_task'),
    (Reassess, reassess_body, 'confirm_reassess'),
])
def test_creating_a_task_or_reassessment_needs_actual_true(model, factory, field):
    body = factory(); body[field] = False
    reject_body(body, model)


@pytest.mark.parametrize('request_id', ['a' * 12, 'A_9-' * 25])
def test_original_request_identifier_accepts_its_exact_boundary(request_id):
    body = create_task_body(); body['request_id'] = request_id
    assert parse_wire(wire(body), CreateTask).request_id == request_id


@pytest.mark.parametrize('request_id', [
    'a' * 11, 'a' * 101, '', ' request12345', 'request12345 ',
    'request/12345', 'request.12345', '原请求编号123456789', 123456789012, True, None,
])
def test_original_request_identifier_is_never_defaulted_trimmed_or_coerced(request_id):
    body = create_task_body(); body['request_id'] = request_id
    reject_body(body, CreateTask)


@pytest.mark.parametrize('field', ['assessment_id', 'release_id'])
@pytest.mark.parametrize('value', [
    ASSESSMENT.upper(), ASSESSMENT.replace('-', ''), 'not-an-id',
    ' ' + ASSESSMENT, ASSESSMENT + ' ', None, 1,
])
def test_original_assessment_and_release_require_canonical_uuid_text(field, value):
    body = draft_body(); body[field] = value
    reject_body(body, Draft)


@pytest.mark.parametrize('scenes', [
    [SCENE_A, SCENE_A], [SCENE_A, SCENE_B, ASSESSMENT],
    [SCENE_A.upper()], ['not-a-scene'], [None], [1], SCENE_A, None,
])
def test_scene_selection_is_bounded_unique_and_typed_without_guessing_originals(scenes):
    body = draft_body(); body['comparison_scene_ids'] = scenes
    reject_body(body, Draft)


@pytest.mark.parametrize('field,limit', [('title', 300), ('reason', 1000)])
def test_human_draft_text_has_real_character_bounds_without_trimming(field, limit):
    body = draft_body(); body[field] = '字' * limit
    assert getattr(parse_wire(wire(body), Draft), field) == body[field]
    body[field] += '字'
    reject_body(body, Draft)
    for value in (None, 1, True, ['原稿']):
        body[field] = value
        reject_body(body, Draft)


@pytest.mark.parametrize('reason', ['', ' \t\r\n ', None, 1, '字' * 1001])
def test_stop_requires_a_saved_meaningful_reason_but_grants_no_task_cancel(reason):
    body = stop_body(); body['reason'] = reason
    reject_body(body, Stop)


def test_wire_length_is_utf8_bytes_and_exact_limit_is_allowed():
    raw = wire(draft_body())
    boundary = ' ' * (MAX_BYTES - len(raw.encode('utf-8'))) + raw
    assert len(boundary.encode('utf-8')) == MAX_BYTES
    assert_typed_equal(parse_wire(boundary, Draft).model_dump(), draft_body())
    reject_wire(' ' + boundary)
    character_bounded = ' ' * (MAX_BYTES - len(raw)) + raw
    assert len(character_bounded) == MAX_BYTES
    assert len(character_bounded.encode('utf-8')) > MAX_BYTES
    reject_wire(character_bounded)


@pytest.mark.parametrize('extra_key', ['title', 'ti\\u0074le'])
def test_duplicate_keys_including_escaped_aliases_do_not_replace_original_input(extra_key):
    raw = wire(draft_body())
    reject_wire(raw[:-1] + ',"' + extra_key + '":"替换原稿"}')


@pytest.mark.parametrize('raw', [
    '', ' ', 'null', 'true', '1', '1.0', '"原请求"', '[]',
    '{"request_id":"saved-imagery-original_163",}', '{} trailing',
])
def test_wire_requires_one_complete_json_object(raw):
    reject_wire(raw)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_nonfinite_numeric_tokens_are_never_accepted_as_versions(token):
    raw = wire(create_task_body()).replace('"version":2', '"version":' + token)
    reject_wire(raw, CreateTask)


@pytest.mark.parametrize('text', ['原稿\x00尾', '原稿\ud800尾', '原稿\udfff尾'])
def test_nul_and_invalid_unicode_in_human_input_fail_with_safe_protocol_error(text):
    body = draft_body(); body['title'] = text
    reject_wire(json.dumps(body, ensure_ascii=True))
    reject_wire(json.dumps(body, ensure_ascii=False))


@pytest.mark.parametrize('original', [None, b'{}', {}, [], 1, True])
def test_wire_itself_must_be_original_text(original):
    reject_wire(original)


def test_whitespace_escaped_chinese_and_literal_chinese_parse_without_rewriting_human_fields():
    body = draft_body(); body['reason'] = ' 原文“范围A\\B”\n第二行；e\u0301与é分别保留 '
    escaped = json.dumps(body, ensure_ascii=True, indent=2)
    literal = wire(body)
    assert escaped != literal
    assert_typed_equal(parse_wire(escaped, Draft).model_dump(), body)
    assert_typed_equal(parse_wire(literal, Draft).model_dump(), body)
    # Equivalence of parsed DTOs is deliberately not an exact-wire SHA oracle.


@pytest.mark.parametrize('model', [None, dict, object, []])
def test_parser_does_not_accept_an_unregistered_model_as_a_looser_protocol(model):
    reject_wire(wire(draft_body()), model)
