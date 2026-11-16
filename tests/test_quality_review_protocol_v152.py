"""Closed quality-review commands and pure human-completeness checks.

These tests use no DB, source receipt, gold labels or material bytes. A complete
human declaration is not proof of source validity, representative quality or
permission to deploy a policy; the real store must establish those separately.
"""
from copy import deepcopy
import math
from uuid import UUID

from fastapi import HTTPException
import pytest

from generic_agents.quality_review_protocol_v152 import (
    ROOT, DRAFT_KEYS, QualityDraft, UpdateQuality, ControlQuality,
    approval_issues, canonical_uuid, identify, parse_command, read_command,
)


REQUEST = '152abc00-0000-4000-8000-00000000000a'
EVALUATION = '152abc00-0000-4000-8000-00000000000b'
SHADOW = '152abc00-0000-4000-8000-00000000000c'
REVIEW = '152abc00-0000-4000-8000-00000000000d'
FEEDBACK_A = '152abc00-0000-4000-8000-00000000000e'
FEEDBACK_B = '152abc00-0000-4000-8000-00000000000f'
FEEDBACK_OTHER = '152abc00-0000-4000-8000-000000000010'
MATERIAL_A = '152abc00-0000-4000-8000-000000000011'
MATERIAL_B = '152abc00-0000-4000-8000-000000000012'


def draft_body():
    return {'schema': 'threshold-quality-draft-v152', 'request_id': REQUEST,
        'evaluation_id': EVALUATION, 'expected_evaluation_version': 7,
        'shadow_id': SHADOW, 'expected_shadow_version': 4,
        'title': ' 原对象阈值审核 ', 'scope_statement': ' 仅此原对象与原来源 ',
        'sampling_statement': ' 逐条核对独立评估及全部未来观察 ',
        'limitations': ' 不证明设备校准或未覆盖季节的效果 ',
        'unresolved_items': [], 'representative_confirmation': True,
        'label_sources': [
            {'feedback_id': FEEDBACK_A, 'expected_version': 2,
             'material_refs': [{'material_id': MATERIAL_A, 'sha256': 'a' * 64,
                                'locator': ' 原件第2页第3行 '}]},
            {'feedback_id': FEEDBACK_B, 'expected_version': 3,
             'material_refs': [{'material_id': MATERIAL_B, 'sha256': 'b' * 64,
                                'locator': ' 样本表第4行 '}]},
        ]}


def update_body():
    return {**draft_body(), 'expected_version': 5}


def control_body():
    return {'schema': 'threshold-quality-control-v152', 'request_id': REQUEST,
        'expected_version': 5, 'reason': ' 明确审核理由，保留原人类措辞 ',
        'confirm': True}


def assert_original(actual, original):
    """Check mutation without Python's bool/int or int/float equality aliases."""
    assert type(actual) is type(original)
    if type(original) is dict:
        assert actual.keys() == original.keys()
        for key in original:
            assert_original(actual[key], original[key])
    elif type(original) is list:
        assert len(actual) == len(original)
        for left, right in zip(actual, original):
            assert_original(left, right)
    else:
        assert actual == original
        if type(original) is float and original == 0:
            assert math.copysign(1, actual) == math.copysign(1, original)


def rejected(model, body):
    original = deepcopy(body)
    with pytest.raises(HTTPException) as error:
        parse_command(model, body)
    assert error.value.status_code == 422
    assert type(error.value.detail) is str and error.value.detail
    assert_original(body, original)


def checked_draft(body=None):
    value = parse_command(QualityDraft, body or draft_body()).model_dump(by_alias=True)
    return {key: deepcopy(value[key]) for key in DRAFT_KEYS}


@pytest.mark.parametrize('model,factory', [
    (QualityDraft, draft_body), (UpdateQuality, update_body),
    (ControlQuality, control_body),
])
def test_closed_commands_preserve_original_human_text_and_typed_values(model, factory):
    body = factory(); original = deepcopy(body)
    parsed = parse_command(model, body)
    assert_original(parsed.model_dump(by_alias=True), original)
    assert_original(body, original)


def test_incomplete_declarations_can_be_saved_as_draft_without_becoming_approval():
    body = draft_body()
    for field in ['title', 'scope_statement', 'sampling_statement', 'limitations']:
        body[field] = ''
    body.update(label_sources=[], representative_confirmation=False,
                unresolved_items=['尚未补齐原件'])
    parsed = parse_command(QualityDraft, body)
    assert_original(parsed.model_dump(by_alias=True), body)
    issues = approval_issues(checked_draft(body), 'business_assessment', [FEEDBACK_A, FEEDBACK_B])
    assert type(issues) is list and all(type(issue) is str and issue for issue in issues)
    assert any('代表性' in issue for issue in issues)
    assert any('未解决' in issue for issue in issues)
    assert any('完整' in issue and '标签' in issue for issue in issues)


def test_all_command_fields_are_required_and_client_authority_or_time_fields_are_rejected():
    for model, factory in [(QualityDraft, draft_body), (UpdateQuality, update_body),
                           (ControlQuality, control_body)]:
        for field in factory():
            body = factory(); del body[field]
            rejected(model, body)
        for field, value in [('purpose', 'business_assessment'), ('production_eligible', True),
                             ('approval_hash', 'a' * 64), ('approved_at', '2026-10-21T10:00:00Z'),
                             ('observed_at', None), ('expires_at', '2026-10-22T10:00:00+08:00')]:
            body = factory(); body[field] = value
            rejected(model, body)
    rejected(QualityDraft, control_body())
    rejected(ControlQuality, draft_body())


@pytest.mark.parametrize('field', ['expected_evaluation_version', 'expected_shadow_version'])
@pytest.mark.parametrize('value', [True, False, 1.0, '1', 0, -1, None])
def test_draft_versions_never_coerce_or_default(field, value):
    body = draft_body(); body[field] = value
    rejected(QualityDraft, body)


@pytest.mark.parametrize('model,factory', [(UpdateQuality, update_body), (ControlQuality, control_body)])
@pytest.mark.parametrize('value', [True, 1.0, '1', 0, None])
def test_update_and_control_require_strict_saved_version(model, factory, value):
    body = factory(); body['expected_version'] = value
    rejected(model, body)


@pytest.mark.parametrize('value', [True, 1.0, '2', 0, None])
def test_label_reference_version_is_original_strict_integer(value):
    body = draft_body(); body['label_sources'][0]['expected_version'] = value
    rejected(QualityDraft, body)


@pytest.mark.parametrize('value', [1, 0, 'true', 'false', None])
def test_representative_confirmation_requires_boolean_without_claiming_truth(value):
    body = draft_body(); body['representative_confirmation'] = value
    rejected(QualityDraft, body)


@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_approval_or_revocation_confirmation_requires_actual_true(value):
    body = control_body(); body['confirm'] = value
    rejected(ControlQuality, body)


@pytest.mark.parametrize('value', [REQUEST.upper(), REQUEST.replace('-', ''), UUID(REQUEST),
                                   'not-a-uuid', True, None])
def test_every_identity_is_canonical_and_is_not_normalized(value):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        canonical_uuid(value)
    for field in ['request_id', 'evaluation_id', 'shadow_id']:
        body = draft_body(); body[field] = value
        rejected(QualityDraft, body)
    body = draft_body(); body['label_sources'][0]['feedback_id'] = value
    rejected(QualityDraft, body)
    body = draft_body(); body['label_sources'][0]['material_refs'][0]['material_id'] = value
    rejected(QualityDraft, body)


@pytest.mark.parametrize('value', ['', ' ', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64, 123, None])
def test_material_sha_is_exact_lower_hex_original_not_a_claim_of_readability(value):
    body = draft_body(); body['label_sources'][0]['material_refs'][0]['sha256'] = value
    rejected(QualityDraft, body)


@pytest.mark.parametrize('value', ['', ' \t', '位' * 2001, 1, None, '\ud800', '\x00'],
    ids=['empty', 'whitespace', 'over-limit', 'integer', 'null', 'surrogate', 'nul'])
def test_provided_material_location_is_explicit_bounded_and_valid_unicode(value):
    body = draft_body(); body['label_sources'][0]['material_refs'][0]['locator'] = value
    rejected(QualityDraft, body)


def test_nested_shapes_and_reference_lists_are_closed_and_cannot_duplicate_labels_or_materials():
    for target in ['label', 'material']:
        body = draft_body(); label = body['label_sources'][0]
        nested = label if target == 'label' else label['material_refs'][0]
        nested['verified'] = True
        rejected(QualityDraft, body)
    body = draft_body(); body['label_sources'].append(deepcopy(body['label_sources'][0]))
    rejected(QualityDraft, body)
    body = draft_body()
    body['label_sources'][0]['material_refs'].append(deepcopy(body['label_sources'][0]['material_refs'][0]))
    rejected(QualityDraft, body)
    body = draft_body(); body['label_sources'] = [
        {'feedback_id': '152abc00-0000-4000-8000-' + format(index, '012x'),
         'expected_version': 1, 'material_refs': []} for index in range(61)]
    rejected(QualityDraft, body)
    body = draft_body(); body['label_sources'][0]['material_refs'] = [
        {'material_id': '152abc00-0000-4000-8000-' + format(index, '012x'),
         'sha256': 'a' * 64, 'locator': '逐条原件位置'} for index in range(21)]
    rejected(QualityDraft, body)


@pytest.mark.parametrize('field,value', [
    ('title', '名' * 201), ('scope_statement', '范' * 2001),
    ('sampling_statement', '样' * 2001), ('limitations', '限' * 2001),
    ('title', 1), ('scope_statement', None), ('sampling_statement', '\ud800'),
    ('limitations', '\x00'), ('unresolved_items', [' \t']),
    ('unresolved_items', ['未解决'] * 21), ('unresolved_items', [True]),
])
def test_draft_text_bounds_preserve_labor_without_silently_trimming_or_coercing(field, value):
    body = draft_body(); body[field] = value
    rejected(QualityDraft, body)


@pytest.mark.parametrize('reason', ['', ' \n', '理' * 2001, True, None, '\ud800', '\x00'])
def test_control_has_meaningful_original_reason(reason):
    body = control_body(); body['reason'] = reason
    rejected(ControlQuality, body)


def test_utf8_wire_keeps_original_time_text_and_numeric_kinds_without_granting_client_time_authority():
    raw = ('{"request_id":"' + REQUEST + '","原说明":"2026-10-21T10:15:00+08:00",'
           '"proof":{"negative":-0.0,"float":1.0,"integer":1,"large":9007199254740993}}').encode('utf-8')
    parsed = read_command(raw)
    assert parsed['原说明'] == '2026-10-21T10:15:00+08:00'
    proof = parsed['proof']
    assert type(proof['negative']) is float and math.copysign(1, proof['negative']) == -1
    assert type(proof['float']) is float and type(proof['integer']) is int
    assert type(proof['large']) is int and proof['large'] == 9007199254740993
    parsed['proof']['negative'] = 0
    assert math.copysign(1, read_command(raw)['proof']['negative']) == -1
    # Generic wire parsing accepts JSON; the closed command rejects this body.
    rejected(QualityDraft, read_command(raw))


@pytest.mark.parametrize('raw', [
    ('{"request_id":"' + REQUEST + '","request_id":"' + REQUEST + '"}').encode(),
    ('{"request_id":"' + REQUEST + '","n":{"x":1,"\\u0078":2}}').encode(),
    ('{"request_id":"' + REQUEST + '","n":NaN}').encode(),
    ('{"request_id":"' + REQUEST + '","n":1e309}').encode(),
    b'{"request_id":"\xff"}', b'{"request_id":"\\ud800"}',
    b'{"request_id":"\x00"}', b'\xef\xbb\xbf{}', b'{}{}', b'',
    b' ' * (64 * 1024 + 1), '{}', bytearray(b'{}'), b'[]', b'null', b'{}',
])
def test_wire_rejects_ambiguity_invalid_utf8_nonfinite_or_nonoriginal_body(raw):
    with pytest.raises((ValueError, TypeError)):
        read_command(raw)


@pytest.mark.parametrize('action,model', [
    ('update', UpdateQuality), ('approve', ControlQuality),
    ('reject', ControlQuality), ('revoke', ControlQuality),
])
def test_route_dispatch_is_exact_and_retains_the_operation(action, model):
    assert identify('POST', ROOT) == (None, 'create', QualityDraft)
    assert identify('POST', ROOT + '/' + REVIEW + '/' + action) == (REVIEW, action, model)


@pytest.mark.parametrize('method,path', [
    ('GET', ROOT), ('PUT', ROOT), ('post', ROOT), ('POST', ROOT + '/'),
    ('POST', ROOT + '/' + REVIEW + '/approve/'),
    ('POST', ROOT + '/' + REVIEW + '/%61pprove'),
    ('POST', ROOT + '/' + REVIEW.upper() + '/revoke'),
    ('POST', ROOT + '/' + REVIEW + '/approve?confirm=true'),
    ('POST', ROOT + '/' + REVIEW + '/end'),
    ('POST', '/api/local-rules/evolution/threshold-shadows/' + SHADOW + '/end'),
])
def test_route_aliases_and_other_protocol_families_are_not_write_authority(method, path):
    with pytest.raises((ValueError, TypeError)):
        identify(method, path)


def test_complete_business_declarations_pass_only_the_pure_completeness_gate():
    draft = checked_draft(); original = deepcopy(draft)
    result = approval_issues(draft, 'business_assessment', [FEEDBACK_A, FEEDBACK_B])
    assert result == []
    assert_original(draft, original)


@pytest.mark.parametrize('field,expected', [
    ('title', '审核名称'), ('scope_statement', '适用范围'),
    ('sampling_statement', '抽样'), ('limitations', '限制'),
])
def test_approval_requires_each_human_statement_even_though_empty_draft_is_saved(field, expected):
    body = draft_body(); body[field] = ' \t'
    issues = approval_issues(checked_draft(body), 'business_assessment', [FEEDBACK_A, FEEDBACK_B])
    assert any(expected in issue for issue in issues)


def test_unconfirmed_representativeness_and_unresolved_items_cannot_be_approved():
    for changes, expected in [({'representative_confirmation': False}, '代表性'),
                              ({'unresolved_items': ['有待核对的标签']}, '未解决')]:
        body = draft_body(); body.update(changes)
        issues = approval_issues(checked_draft(body), 'business_assessment', [FEEDBACK_A, FEEDBACK_B])
        assert any(expected in issue for issue in issues)


@pytest.mark.parametrize('mode', ['missing', 'extra_training_reference', 'empty'])
def test_label_coverage_is_exact_without_discarding_a_missing_future_input(mode):
    body = draft_body()
    if mode == 'missing':body['label_sources'].pop()
    elif mode == 'extra_training_reference':
        extra = deepcopy(body['label_sources'][0]);extra['feedback_id'] = FEEDBACK_OTHER
        body['label_sources'].append(extra)
    else:body['label_sources'] = []
    issues = approval_issues(checked_draft(body), 'business_assessment', [FEEDBACK_A, FEEDBACK_B])
    assert any('标签' in issue and '完整' in issue for issue in issues)


def test_business_requires_material_for_every_label_but_mechanism_remains_distinct():
    body = draft_body(); body['label_sources'][1]['material_refs'] = []
    draft = checked_draft(body)
    assert any('原件' in issue for issue in approval_issues(draft, 'business_assessment', [FEEDBACK_A, FEEDBACK_B]))
    assert approval_issues(draft, 'mechanism_check', [FEEDBACK_A, FEEDBACK_B]) == []


@pytest.mark.parametrize('change', ['extra_key', 'missing_key', 'invalid_purpose',
                                    'empty_required', 'duplicate_required'])
def test_completeness_helper_rejects_wrong_outer_shape_or_undefined_reference_inventory(change):
    draft = checked_draft(); purpose = 'business_assessment'; required = [FEEDBACK_A, FEEDBACK_B]
    if change == 'extra_key':draft['approved_at'] = '2026-10-21T10:00:00Z'
    elif change == 'missing_key':del draft['limitations']
    elif change == 'invalid_purpose':purpose = 'production_approved'
    elif change == 'empty_required':required = []
    else:required = [FEEDBACK_A, FEEDBACK_A]
    with pytest.raises(ValueError):
        approval_issues(draft, purpose, required)
