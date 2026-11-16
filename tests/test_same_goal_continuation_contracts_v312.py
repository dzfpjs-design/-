"""Pure closed human draft contracts; no DB, authorization or runtime proof."""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_contracts_v312 as c
from tests.test_same_goal_advice_contracts_v302 import ref


def request():
    return {'request_id': ref('continuation-request')['id'], 'expected_task_version': 1,
        'source_hash': 'a' * 64, 'draft': {}, 'reason': '保存原目标范围的人工草案'}


def test_empty_draft_is_closed_incomplete_and_not_human_work():
    value = c.draft({})
    assert value == {'route': None, 'candidate_ref': None, 'selected_tools': [], 'purpose': '',
        'work_description': '', 'expected_result': '', 'assignee_id': None, 'due_at': None}
    assert not c.has_human_content(value)
    assert c.dump(c.PreviewRequest, request())['draft'] == value
    assert all(flag is False for flag in c.FLAGS.values())


@pytest.mark.parametrize('field,value', [('route', 'operational_action'), ('candidate_ref', 'candidate_one'),
    ('purpose', '后续核查'), ('work_description', '逐项核对原件'), ('expected_result', '保存独立复核意见'),
    ('assignee_id', ref('assignee')['id']), ('due_at', '2026-11-03T18:00:00+08:00'),
    ('selected_tools', [{'id': ref('tool')['id'], 'content_hash': 'b' * 64, 'release_version': '1.0.0'}])])
def test_partial_human_work_is_preserved_without_claiming_complete_or_executable(field, value):
    original = {field: value}; before = copy.deepcopy(original)
    result = c.draft(original)
    assert result[field] == value and c.has_human_content(result) is True and original == before
    assert not {'approved', 'invocation_id', 'can_dispatch'} & set(result)


@pytest.mark.parametrize('value', ['', ' ', '\r\n\t'])
def test_whitespace_is_not_human_content(value):
    assert c.has_human_content({'purpose': value, 'work_description': value, 'expected_result': value}) is False


@pytest.mark.parametrize('field,value', [('due_at', '2026-11-03T18:00:00'), ('due_at', 'not-a-date'),
    ('route', 'a' * 65), ('candidate_ref', 'a' * 129), ('due_at', 'a' * 65),
    ('assignee_id', 'not-an-id'), ('route', True), ('purpose', 'a' * 2001),
    ('work_description', 'a' * 4001), ('expected_result', 'a' * 2001), ('approved', True)])
def test_invalid_or_authority_fields_are_not_normalized_into_a_draft(field, value):
    if field == 'due_at' and value in {'2026-11-03T18:00:00', 'not-a-date'}:
        with pytest.raises(HTTPException) as error: c.draft({field: value})
        assert error.value.status_code == 422
        return
    with pytest.raises(ValueError): c.draft({field: value})


def test_six_distinct_actual_pins_maximum_and_no_duplicate_identity():
    pins = [{'id': ref('tool-' + str(index))['id'], 'content_hash': 'b' * 64, 'release_version': '1.0.0'} for index in range(7)]
    assert c.draft({'selected_tools': pins[:6]})['selected_tools'] == pins[:6]
    with pytest.raises(ValueError): c.draft({'selected_tools': pins})
    with pytest.raises(ValueError): c.draft({'selected_tools': [pins[0], pins[0]]})


def test_preview_save_revision_withdraw_have_separate_closed_cas_contracts():
    value = request(); preview = c.dump(c.PreviewRequest, value)
    saved = c.dump(c.SaveRequest, {**value, 'preview_hash': 'c' * 64})
    revised = c.dump(c.ReviseRequest, {**saved, 'expected_continuation_version': 2})
    withdrawn = c.dump(c.WithdrawRequest, {'request_id': value['request_id'],
        'expected_continuation_version': 3, 'reason': '仅撤回未批准草案'})
    assert preview['request_id'] == saved['request_id'] == revised['request_id'] == withdrawn['request_id']
    for kind, invalid in [(c.SaveRequest, value), (c.ReviseRequest, saved),
            (c.WithdrawRequest, {**withdrawn, 'draft': {}}),
            (c.PreviewRequest, {**value, 'expected_task_version': True})]:
        with pytest.raises(ValueError): c.dump(kind, invalid)
    assert c.ACTION_PREVIEW not in c.ACTIONS


def test_human_spelling_and_past_or_future_due_dates_are_never_rewritten_or_used_as_execution_gates():
    for when in ['2000-01-01T18:00:00+08:00', '2099-01-01T18:00:00+08:00']:
        value = {'purpose': '  原文理由\r\n', 'due_at': when}
        assert c.draft(value)['purpose'] == value['purpose']
        assert c.draft(value)['due_at'] == when and c.has_human_content(value)
