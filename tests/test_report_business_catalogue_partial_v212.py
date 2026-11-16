"""Public synthetic partial suggestions; no real source, call or paid authority."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_business_catalogue_partial_v212 as subject
from generic_agents import report_business_catalogue_wire_v211 as complete
from generic_agents import report_business_catalogue_edit_v211 as editor
from generic_agents import report_indexed_inputs_v178 as codec
from tests.test_report_business_catalogue_v211 import scene, single, raw


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical pure suggestions cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def partial_scene():
    value, bound = scene()
    value['businesses'][0]['name'] = '原引用没有逐字出现的拟定名称'
    value['allocation_groups'][0]['input_positions'] = [0, 0, 7]
    return value, bound


def test_partial_keeps_every_original_claim_and_occurrence_without_winner_or_name_repair():
    value, bound = partial_scene(); before = codec.canonical([value, bound]); original = raw(value)
    result = subject.build_partial_projection(original, **bound)
    assert result == subject.check_partial_projection(result, original, **bound)
    assert result['schema'] == subject.SCHEMA != complete.PROJECTION
    assert result['raw_wire'] == value and 'wire' not in result and 'ledger' not in result
    assert result['raw_arguments_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['missing_positions'] == [1] and result['conflict_positions'] == [0]
    assert result['pending_positions'] == [6] and result['unassigned_positions'] == [0, 1, 6]
    assert result['stats'] == {'input_count': 8, 'raw_membership_count': 8, 'unique_mentioned_count': 7,
        'duplicate_occurrence_count': 1, 'proposed_count': 5, 'conflicting_count': 1,
        'missing_count': 1, 'pending_count': 1, 'invalid_proposal_count': 0,
        'invalid_unique_count': 0, 'unassigned_count': 3}
    assert len(result['assignments']) == 8
    for position, assignment in enumerate(result['assignments']):
        original_claim = bound['input_claims'][position]
        assert assignment['input_position'] == position
        assert assignment['input_id'] == f'position_{position:04d}'
        assert assignment['input_id_origin'] == 'platform_position'
        assert codec.canonical(assignment['claim']) == codec.canonical(original_claim)
        assert assignment['statement'] == original_claim['statement']
        assert assignment['status'] == original_claim['status']
        assert assignment['span_refs'] == original_claim['span_refs']
        assert assignment['source_spans'] == [{'span_ref': ref, 'record': bound['catalog']['index'][ref]}
            for ref in original_claim['span_refs']]
    conflict = result['assignments'][0]
    assert conflict['assignment_status'] == 'conflicting' and conflict['business_keys'] == []
    assert [(m['group_index'], m['occurrence_index']) for m in conflict['proposed_memberships']] == [(0, 0), (0, 1)]
    assert result['assignments'][1]['assignment_status'] == 'unassigned'
    assert result['assignments'][1]['proposed_memberships'] == []
    seed = result['draft_seed']
    assert editor.validate_document(seed, 8) == seed
    assert seed['allocation_groups'][0]['input_positions'] == [7]
    owned = {p for g in seed['allocation_groups'] for p in g['input_positions']}
    assert 0 not in owned and 1 not in owned
    name_issue = next(issue for issue in result['issues'] if issue['code'] == 'business_name_not_literal')
    assert name_issue['business_key'] == 'b1' and name_issue['name_basis'] == name_issue['original_name_basis'] == 'explicit'
    assert result['businesses'][0]['name_basis'] == result['raw_wire']['businesses'][0]['name_basis'] == 'explicit'
    question = next(q for q in seed['questions'] if q['key'] == name_issue['question_key'])
    assert question['kind'] == 'missing_info' and question['status'] == 'open' and question['resolution'] == ''
    assert question['candidate_business_keys'] == ['b1'] and question['input_positions'] == [0]
    assert seed['questions'][:len(value['questions'])] == [{**q, 'status': 'open', 'resolution': ''} for q in value['questions']]
    assert all(result[field] is False for field in ('model_answer_valid', 'original_call_success',
        'semantic_quality_verified', 'human_confirmed', 'can_confirm', 'can_apply', 'can_publish',
        'dispatch_allowed', 'source_anchors_verified', 'report_coverage_verified'))
    assert result['machine_only'] is result['inventory_complete'] is True
    assert codec.canonical([value, bound]) == before


def test_duplicate_even_with_identical_proposal_in_two_groups_stays_unassigned_everywhere():
    value, bound = scene()
    value['allocation_groups'].append(copy.deepcopy(value['allocation_groups'][0]))
    original = raw(value); result = subject.build_partial_projection(original, **bound)
    assert result['conflict_positions'] == [0, 1, 7]
    assert result['stats']['duplicate_occurrence_count'] == 3
    for position in (0, 1, 7):
        assert result['assignments'][position]['assignment_status'] == 'conflicting'
        assert len(result['assignments'][position]['proposed_memberships']) == 2
        assert all(position not in g['input_positions'] for g in result['draft_seed']['allocation_groups'])
    assert result['raw_wire']['allocation_groups'] == value['allocation_groups']
    assert original == raw(value)


def test_known_recipient_arity_contradictions_keep_all_proposals_but_never_create_ownership():
    value, bound = single(12)
    value['businesses'].append({**copy.deepcopy(value['businesses'][0]), 'key': 'b2',
        'name': '另一项待核业务', 'name_basis': 'inferred', 'seed_positions': [1]})
    def group(kind, keys, positions):
        return {'kind': kind, 'business_keys': keys, 'input_positions': positions,
            'reason': '公开合成分派，不证明业务含义正确', 'question_keys': []}
    value['allocation_groups'] = [group('business', ['b1'], [0, 1]),
        group('shared', ['b1'], [2, 3, 4]), group('shared', ['b2'], [5]),
        group('shared', ['b1'], [6, 7]), group('background', ['b1'], [8, 9]),
        group('business', ['b2'], [3, 10, 11])]
    content = raw(value); before = codec.canonical([value, bound])
    result = subject.build_partial_projection(content, **bound)
    assert result == subject.check_partial_projection(result, content, **bound)
    assert result['raw_wire'] == value
    assert result['invalid_proposal_positions'] == list(range(2, 10))
    assert result['conflict_positions'] == [3] and result['missing_positions'] == []
    assert result['unassigned_positions'] == list(range(2, 10))
    assert result['stats']['proposed_count'] == 4
    assert result['stats']['invalid_proposal_count'] == 8
    assert result['stats']['invalid_unique_count'] == 7
    assert result['stats']['unassigned_count'] == 8
    invalid = result['assignments'][2]
    assert invalid['assignment_status'] == 'unassigned' and invalid['kind'] == 'shared'
    assert invalid['business_keys'] == ['b1'] and invalid['assignment_issue_codes'] == ['group_recipients']
    assert invalid['proposed_memberships'][0]['kind'] == 'shared'
    background = result['assignments'][8]
    assert background['assignment_status'] == 'unassigned' and background['kind'] == 'background'
    assert background['business_keys'] == ['b1']  # original contradiction is NOT cleared
    conflict = result['assignments'][3]
    assert conflict['assignment_status'] == 'conflicting' and len(conflict['proposed_memberships']) == 2
    assert conflict['assignment_issue_codes'] == ['group_recipients', 'conflicting_input_assignments']
    assert [g['input_positions'] for g in result['draft_seed']['allocation_groups']] == [[0, 1], [10, 11]]
    issues = [issue for issue in result['issues'] if issue['code'] == 'group_recipients']
    assert len(issues) == 4 and [i['original_kind'] for i in issues] == ['shared', 'shared', 'shared', 'background']
    assert all(i['origin'] == 'platform_diagnostic' and i['blocks_confirmation'] for i in issues)
    validation = editor.validation(result['draft_seed'], 12)
    assert validation['unassigned_positions'] == list(range(2, 10))
    assert result['allocation_complete'] is False and result['original_call_success'] is False
    assert codec.canonical([value, bound]) == before


def test_background_undefined_safe_label_is_preserved_only_as_invalid_unassigned_proposal():
    value, bound = single(3)
    value['allocation_groups'][0]['input_positions'] = [0, 2]
    value['allocation_groups'].append({'kind': 'background', 'business_keys': ['not_registered'],
        'input_positions': [1], 'reason': '公开合成矛盾，不补业务、不清除原接收者', 'question_keys': []})
    original = raw(value); result = subject.build_partial_projection(original, **bound)
    assert result['raw_wire'] == value
    assert result['invalid_proposal_positions'] == result['unassigned_positions'] == [1]
    assignment = result['assignments'][1]
    assert assignment['assignment_status'] == 'unassigned' and assignment['kind'] == 'background'
    assert assignment['business_keys'] == ['not_registered']
    assert assignment['proposed_memberships'][0]['business_keys'] == ['not_registered']
    assert result['stats']['proposed_count'] == 2 and result['stats']['invalid_unique_count'] == 1
    assert [b['key'] for b in result['draft_seed']['businesses']] == ['b1']
    assert [g['input_positions'] for g in result['draft_seed']['allocation_groups']] == [[0, 2]]
    assert result['original_call_success'] is False and original == raw(value)


@pytest.mark.parametrize('kind,keys', [
    ('business', ['not_registered']), ('shared', ['b1', 'not_registered']),
    ('pending', ['not_registered']), ('background', ['bad key']),
    ('background', [True]), ('background', [1.0]),
    ('background', ['not_registered', 'not_registered']),
])
def test_background_exception_never_allows_unknown_operational_business_or_unsafe_labels(kind, keys):
    value, bound = single(3)
    value['allocation_groups'][0]['input_positions'] = [0, 2]
    value['allocation_groups'].append({'kind': kind, 'business_keys': keys,
        'input_positions': [1], 'reason': '原标签只可诊断，不授权或创建业务', 'question_keys': []})
    original = raw(value)
    with pytest.raises(subject.PartialCatalogueError): subject.build_partial_projection(original, **bound)
    assert original == raw(value)


def test_explicit_name_diagnostic_has_stable_collision_safe_platform_question_and_original_questions_unchanged():
    value, bound = single(1); value['businesses'][0]['name'] = '未在原文出现的名称'
    first = subject.build_partial_projection(raw(value), **bound)
    generated = first['issues'][0]['question_key']
    value['questions'] = [{'key': generated, 'kind': 'missing_info', 'input_positions': [0],
        'candidate_business_keys': ['b1'], 'question': '原机器自己的问题，不得被平台覆盖'}]
    value['allocation_groups'][0]['question_keys'] = [generated]
    result = subject.build_partial_projection(raw(value), **bound)
    again = subject.build_partial_projection(raw(value), **bound)
    issue = next(i for i in result['issues'] if i['code'] == 'business_name_not_literal')
    assert issue['question_key'] != generated and issue == again['issues'][0]
    assert result['raw_wire']['questions'] == value['questions']
    assert result['draft_seed']['questions'][0]['key'] == generated
    assert result['draft_seed']['questions'][1]['key'] == issue['question_key']
    assert len({q['key'] for q in result['draft_seed']['questions']}) == 2
    assert result['allocation_complete'] is True and result['model_answer_valid'] is False


@pytest.mark.parametrize('count', [3, 19, 920])
def test_dynamic_inventory_does_not_assume_this_reports_804_or_116_counts(count):
    value, bound = single(count)
    value['allocation_groups'][0]['input_positions'] = [0, 0, *range(2, count)]
    result = subject.build_partial_projection(raw(value), **bound)
    assert result['input_count'] == count and len(result['assignments']) == count
    assert result['conflict_positions'] == [0] and result['missing_positions'] == [1]
    assert result['stats']['proposed_count'] == count - 2
    assert result['stats']['unassigned_count'] == 2
    assert result['draft_seed']['allocation_groups'][0]['input_positions'] == list(range(2, count))


def test_a_valid_complete_answer_uses_its_original_entry_even_if_ownership_is_pending():
    value, bound = scene(); content = raw(value)
    assert complete.build_catalogue_projection(content, **bound)['pending_positions'] == [6]
    with pytest.raises(subject.PartialCatalogueError) as error:
        subject.build_partial_projection(content, **bound)
    assert error.value.code == 'complete_answer_has_separate_entry'


@pytest.mark.parametrize('change', [
    lambda v: v.update(input_count=True),
    lambda v: v.update(input_count=8.0),
    lambda v: v.update(source_snapshot_hash='a' * 64),
    lambda v: v.update(schema='report-object-semantic-candidate-wire-v166'),
    lambda v: v['businesses'][1].update(key='b1'),
    lambda v: v['businesses'][0].update(span_refs=['w:p999/s1']),
    lambda v: v['businesses'][0].update(name_basis='confirmed'),
    lambda v: v['allocation_groups'][0].update(input_positions=[False, 1]),
    lambda v: v['allocation_groups'][0].update(input_positions=[0.0, 1]),
    lambda v: v['allocation_groups'][0].update(input_positions=[-1, 1]),
    lambda v: v['allocation_groups'][0].update(input_positions=[0, 8]),
    lambda v: v['allocation_groups'][0].update(business_keys=['unknown']),
    lambda v: v['allocation_groups'][0].update(question_keys=['unknown']),
    lambda v: v['questions'][0].update(candidate_business_keys=['unknown']),
    lambda v: v['questions'][0].update(input_positions=[True]),
    lambda v: v['merge_suggestions'][0].update(business_keys=['b1', 'unknown']),
])
def test_unsafe_structure_is_not_turned_into_a_partial_success(change):
    value, bound = partial_scene(); change(value); original = raw(value)
    before = codec.canonical([value, bound])
    with pytest.raises(subject.PartialCatalogueError) as error:
        subject.build_partial_projection(original, **bound)
    assert error.value.response_must_be_retained is True
    assert original == raw(value) and codec.canonical([value, bound]) == before


def test_historical_projection_cold_check_rejects_resealed_changes_or_wrong_source_without_mutating_inputs():
    value, bound = partial_scene(); content = raw(value); original = codec.canonical([value, bound])
    projection = subject.build_partial_projection(content, **bound)
    projection['assignments'][0]['claim']['raw_extension']['nested'][1]['v'] = 99
    projection['draft_seed']['businesses'][0]['name'] = '篡改后的名称'
    projection['projection_hash'] = codec.digest({k: v for k, v in projection.items() if k != 'projection_hash'})
    with pytest.raises(subject.PartialCatalogueError): subject.check_partial_projection(projection, content, **bound)
    assert codec.canonical([value, bound]) == original
    wrong = {**bound, 'source_snapshot_hash': 'c' * 64}
    with pytest.raises(subject.PartialCatalogueError): subject.build_partial_projection(content, **wrong)
    damaged = copy.deepcopy(bound); damaged['catalog']['index']['w:p1/s1']['text'] = '改写原文'
    with pytest.raises(subject.PartialCatalogueError): subject.build_partial_projection(content, **damaged)
    for body in (b'{"schema":"x","schema":"y"}', b'{} trailing', b'{"x":NaN}', b'\xff'):
        with pytest.raises(subject.PartialCatalogueError): subject.build_partial_projection(body, **bound)


def test_sixty_fifth_business_is_not_truncated_into_a_partial_draft():
    value, bound = single(65)
    value['businesses'] = [{**copy.deepcopy(value['businesses'][0]), 'key': f'b{i}',
        'name': f'第{i}项待核业务', 'name_basis': 'inferred', 'seed_positions': [i]} for i in range(65)]
    original = raw(value)
    with pytest.raises(subject.PartialCatalogueError): subject.build_partial_projection(original, **bound)
    assert original == raw(value) and len(value['businesses']) == 65
