"""Pure V261 request checks; no confirmation, source or Plan acceptance proof."""
import copy
import json

import pytest
from pydantic import ValidationError

from generic_agents import catalogue_current_confirm_contracts_v261 as contract
from generic_agents.catalogue_current_context_contracts_v260 import ConfigurationBasis, DraftRef
from generic_agents.report_scoped_design_plan_v180 import MAX_SCOPES


def body():
    return {'schema': contract.SCHEMA,
        'request_id': '11111111-1111-4111-8111-111111111111',
        'binding_id': '22222222-2222-4222-8222-222222222222',
        'binding_record_hash': 'a' * 64,
        'working_draft_ref': {'id': '33333333-3333-4333-8333-333333333333',
            'workspace_id': '44444444-4444-4444-8444-444444444444',
            'actor_id': '55555555-5555-4555-8555-555555555555',
            'version': 7, 'record_hash': 'b' * 64, 'origin_hash': 'c' * 64,
            'document_hash': 'd' * 64},
        'target_configuration': {'revision': 35, 'configuration_hash': 'e' * 64},
        'expected_context_hash': 'f' * 64,
        'scope_classifications': [
            {'scope_id': 'business_a', 'kind': 'runtime_business', 'reason': '明确列入运行期。'},
            {'scope_id': 'business_b', 'kind': 'platform_support', 'reason': '仅平台支撑。'}],
        'runtime_scope_ids': ['business_a'], 'confirm': True,
        'reason': '保留原文与全部位置；仅明确选择的范围进入后续候选流程。'}


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def assign(value, path, replacement):
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


def test_exact_body_preserves_unicode_and_complete_existing_refs_without_authority():
    original = body(); original['reason'] = '  明确核对🌲，不把模型建议当事实。\n'
    spelling = b' \n' + raw(original) + b'\t'
    parsed, exact = contract.parse(spelling)
    assert parsed.model_dump() == original and exact.encode('utf-8') == spelling
    assert type(parsed.working_draft_ref) is DraftRef
    assert type(parsed.target_configuration) is ConfigurationBasis
    assert contract.MAX_SCOPES == MAX_SCOPES
    assert not {'plan_id', 'source_context', 'actor_id', 'release_authorized'}.intersection(parsed.model_dump())


@pytest.mark.parametrize('path,replacement', [
    (('working_draft_ref', 'version'), True),
    (('working_draft_ref', 'version'), 7.0),
    (('working_draft_ref', 'version'), '7'),
    (('target_configuration', 'revision'), False),
    (('target_configuration', 'revision'), 0),
])
def test_versions_are_positive_strict_integers(path, replacement):
    value = body(); assign(value, path, replacement)
    with pytest.raises(ValidationError):
        contract.ConfirmBody.model_validate(value)


@pytest.mark.parametrize('path,replacement', [
    (('request_id',), 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'),
    (('binding_id',), '22222222222242228222222222222222'),
    (('working_draft_ref', 'actor_id'), 'not-an-owner-id'),
    (('binding_record_hash',), 'A' * 64),
    (('target_configuration', 'configuration_hash'), 'e' * 63),
])
def test_ids_and_hashes_cannot_be_normalized_or_substituted(path, replacement):
    value = body(); assign(value, path, replacement)
    with pytest.raises(ValidationError):
        contract.ConfirmBody.model_validate(value)


@pytest.mark.parametrize('target', ['body', 'classification', 'draft', 'configuration'])
def test_extra_fields_cannot_supply_a_plan_or_an_authority(target):
    value = body()
    item = {'body': value, 'classification': value['scope_classifications'][0],
        'draft': value['working_draft_ref'], 'configuration': value['target_configuration']}[target]
    item['plan_id'] = '66666666-6666-4666-8666-666666666666'
    with pytest.raises(ValidationError):
        contract.ConfirmBody.model_validate(value)


@pytest.mark.parametrize('damage', [
    'duplicate-classification', 'unsorted-classifications', 'duplicate-selection',
    'unsorted-selection', 'empty-selection', 'unknown-selection', 'nonruntime-selection',
    'unresolved-selection', 'unknown-kind',
])
def test_classification_and_runtime_selection_require_exact_order_and_membership(damage):
    value = body()
    if damage == 'duplicate-classification':
        value['scope_classifications'].append(copy.deepcopy(value['scope_classifications'][0]))
    elif damage == 'unsorted-classifications':
        value['scope_classifications'].reverse()
    elif damage == 'duplicate-selection':
        value['runtime_scope_ids'] *= 2
    elif damage == 'unsorted-selection':
        value['scope_classifications'][1]['kind'] = 'runtime_business'
        value['runtime_scope_ids'] = ['business_b', 'business_a']
    elif damage == 'empty-selection':
        value['runtime_scope_ids'] = []
    elif damage == 'unknown-selection':
        value['runtime_scope_ids'] = ['business_missing']
    elif damage == 'nonruntime-selection':
        value['runtime_scope_ids'] = ['business_b']
    elif damage == 'unresolved-selection':
        value['scope_classifications'][0]['kind'] = 'unresolved'
    else:
        value['scope_classifications'][0]['kind'] = 'model-approved'
    with pytest.raises(ValidationError):
        contract.ConfirmBody.model_validate(value)


@pytest.mark.parametrize('reason', [' \t\n', '说明\x00正文', '\ud800', '长' * 2001])
def test_reasons_reject_blank_nul_surrogates_and_overlong_human_text(reason):
    value = body(); value['reason'] = reason
    with pytest.raises((ValidationError, UnicodeError)):
        contract.ConfirmBody.model_validate(value)
    value = body(); value['scope_classifications'][0]['reason'] = reason
    with pytest.raises((ValidationError, UnicodeError)):
        contract.ConfirmBody.model_validate(value)


@pytest.mark.parametrize('consent', [False, 1, 'true'])
def test_confirmation_is_only_the_literal_boolean_true(consent):
    value = body(); value['confirm'] = consent
    with pytest.raises(ValidationError):
        contract.ConfirmBody.model_validate(value)


@pytest.mark.parametrize('damage', ['duplicate-root', 'duplicate-nested', 'nan', 'overflow',
    'invalid-utf8', 'nul-key', 'surrogate', 'deep-json'])
def test_bounded_raw_parser_never_erases_malformed_original_json(damage):
    value = body(); valid = raw(value)
    if damage == 'duplicate-root':
        payload = b'{"confirm":true,' + valid[1:]
    elif damage == 'duplicate-nested':
        payload = valid.replace(b'"revision":35', b'"revision":35,"revision":35', 1)
    elif damage == 'nan':
        payload = valid.replace(b'"revision":35', b'"revision":NaN', 1)
    elif damage == 'overflow':
        payload = valid.replace(b'"revision":35', b'"revision":1e9999', 1)
    elif damage == 'invalid-utf8':
        payload = b'\xff' + valid
    elif damage == 'nul-key':
        payload = valid[:-1] + b',"bad\\u0000key":1}'
    elif damage == 'surrogate':
        payload = valid.replace('明确列入运行期。'.encode(), b'\\ud800', 1)
    else:
        payload = b'[' * 26 + b'0' + b']' * 26
    with pytest.raises(ValueError):
        contract.parse(payload)


def test_raw_byte_limit_is_exact_and_does_not_count_characters_or_silently_trim():
    valid = raw(body())
    exact = valid + b' ' * (contract.MAX_RAW - len(valid))
    parsed, original = contract.parse(exact)
    assert parsed.confirm is True and len(original.encode('utf-8')) == contract.MAX_RAW
    for invalid in (exact + b' ', b'', bytearray(valid)):
        with pytest.raises(ValueError):
            contract.parse(invalid)


def test_scope_capacity_uses_original_planner_limit_without_truncating_inventory():
    value = body()
    value['scope_classifications'] = [{'scope_id': 'scope_%03d' % i,
        'kind': 'runtime_business', 'reason': '本范围明确列入运行期。'} for i in range(MAX_SCOPES)]
    value['runtime_scope_ids'] = [v['scope_id'] for v in value['scope_classifications']]
    parsed, _ = contract.parse(raw(value))
    assert len(parsed.scope_classifications) == len(parsed.runtime_scope_ids) == MAX_SCOPES
    value['scope_classifications'].append({'scope_id': 'scope_999',
        'kind': 'runtime_business', 'reason': '不得截断。'})
    with pytest.raises(ValidationError):
        contract.ConfirmBody.model_validate(value)
