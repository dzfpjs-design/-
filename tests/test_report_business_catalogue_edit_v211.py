"""Pure human document contracts; never source, paid delivery or confirmation."""
import copy
import json
import socket
import subprocess
from uuid import uuid4

import pytest

from generic_agents import report_business_catalogue_edit_v211 as edit
from generic_agents import report_business_catalogue_store_v211 as store
from generic_agents import report_business_catalogue_wire_v211 as wire
from generic_agents import report_business_catalogue_partial_v212 as partial
from generic_agents.contracts import digest
from tests.test_report_business_catalogue_v211 import scene
from tests.test_report_business_catalogue_partial_v212 import partial_scene


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure editor tests cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def document():
    value, bound = scene()
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    projection = wire.build_catalogue_projection(raw, **bound)
    return edit.from_projection(projection), bound, projection


def complete_document():
    value, bound, projection = document()
    group = next(row for row in value['allocation_groups'] if row['kind'] == 'pending')
    group.update(kind='business', business_keys=['b1'])
    question = next(row for row in value['questions'] if row['kind'] == 'ownership')
    question.update(status='resolved', resolution='明确归入第一项业务，仅表示人工范围决定。')
    return value, bound, projection


def action(kind, source, target):
    return {'kind': kind, 'source_business_keys': source, 'target_business_keys': target,
        'reason': '明确记录本次结构调整，原稿与原资料保留。'}


def test_machine_origin_is_unchanged_while_human_document_can_be_empty_and_partial():
    value, bound, projection = document(); before = copy.deepcopy(projection)
    assert set(value) == {'schema', 'businesses', 'allocation_groups', 'questions'}
    assert all('seed_positions' not in business and 'name_basis' not in business for business in value['businesses'])
    empty = {'schema': edit.SCHEMA, 'businesses': [], 'allocation_groups': [], 'questions': []}
    assert edit.validate_document(empty, 8) == empty
    check = edit.validation(empty, 8)
    assert check['unassigned_positions'] == list(range(8))
    assert any(issue['blocks_confirmation'] for issue in check['issues'])
    partial = copy.deepcopy(value); partial['businesses'][0]['name'] = ''
    partial['allocation_groups'][0]['input_positions'].remove(7)
    assert edit.validate_document(partial, 8) == partial
    assert edit.validation(partial, 8)['unassigned_positions'] == [7]
    assert projection == before


def test_full_intent_and_open_missing_information_survive_valid_ledger_without_invented_answers():
    value, bound, _ = complete_document()
    purpose = '完整人工业务目的😀\n' * 300
    value['businesses'][0]['purpose'] = purpose
    value['businesses'][0]['boundary'] = '保留边界、禁止事项与需要人工判断的部分。\n' * 100
    before = copy.deepcopy(value); check = edit.validation(value, 8)
    assert check['unassigned_positions'] == check['pending_ownership_positions'] == []
    assert check['open_missing_info_keys'] == ['q_info']
    assert not any(issue['blocks_confirmation'] for issue in check['issues'])
    ledger = edit.scope_ledger(value, indexed=bound['indexed'], input_claims=bound['input_claims'],
        source_snapshot_hash=bound['source_snapshot_hash'])
    assert ledger['shared'] == [{'input_position': 4, 'scope_ids': ['b1', 'b2'],
        'reason': '本组为机器建议，需核对原文后确认'}]
    owned = [position for scope in ledger['scopes'] for position in scope['claim_positions']]
    owned += [row['input_position'] for row in ledger['shared'] + ledger['background']]
    assert sorted(owned) == list(range(8)) and len(owned) == len(set(owned))
    assert 'confirmation' not in ledger and value == before
    assert value['businesses'][0]['purpose'] == purpose


def test_one_original_position_can_be_shared_by_two_human_businesses_without_copying_identity():
    value = {'schema': edit.SCHEMA, 'businesses': [
        {'key': 'a', 'name': '设备调度', 'purpose': '', 'boundary': ''},
        {'key': 'b', 'name': '资产盘点', 'purpose': '', 'boundary': ''}],
        'allocation_groups': [{'key': 'g', 'kind': 'shared', 'business_keys': ['a', 'b'],
            'input_positions': [0], 'reason': '原声明同时涉及两项业务，保留同一原位置。', 'question_keys': []}],
        'questions': []}
    assert edit.validate_document(value, 1) == value
    assert not edit.validation(value, 1)['issues']
    repeated = copy.deepcopy(value); repeated['allocation_groups'].append({**value['allocation_groups'][0], 'key': 'g2'})
    with pytest.raises(ValueError): edit.validate_document(repeated, 1)


@pytest.mark.parametrize('fault', ['bool_position', 'float_position', 'negative', 'out_of_range',
    'descending', 'duplicate', 'unknown_business', 'unknown_question', 'extra', 'duplicate_key'])
def test_unknown_duplicate_or_changed_position_identity_is_rejected_not_repaired(fault):
    value, _, _ = document(); group = value['allocation_groups'][0]
    if fault == 'bool_position': group['input_positions'][0] = False
    elif fault == 'float_position': group['input_positions'][0] = 0.0
    elif fault == 'negative': group['input_positions'][0] = -1
    elif fault == 'out_of_range': group['input_positions'].append(8)
    elif fault == 'descending': group['input_positions'].reverse()
    elif fault == 'duplicate': value['allocation_groups'][1]['input_positions'] = [0, 2, 3]
    elif fault == 'unknown_business': group['business_keys'] = ['unknown']
    elif fault == 'unknown_question': group['question_keys'] = ['unknown']
    elif fault == 'extra': value['approved'] = True
    else: value['businesses'][1]['key'] = value['businesses'][0]['key']
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): edit.validate_document(value, 8)
    assert value == before


def test_pending_ownership_is_not_silently_resolved_by_assignment_or_empty_resolution():
    value, _, _ = document()
    pending = next(row for row in value['allocation_groups'] if row['kind'] == 'pending')
    pending.update(kind='business', business_keys=['b1'])
    assert edit.validation(value, 8)['pending_ownership_positions'] == [6]
    question = next(row for row in value['questions'] if row['kind'] == 'ownership')
    question['status'] = 'resolved'
    assert any(issue['code'] == 'resolution_missing' for issue in edit.validation(value, 8)['issues'])
    question['resolution'] = '已明确归属；不表示现场事实核实。'
    assert not any(issue['blocks_confirmation'] for issue in edit.validation(value, 8)['issues'])


def test_structural_actions_replay_real_intermediate_keys_and_do_not_invent_a_merge():
    old, _, _ = document(); final = copy.deepcopy(old)
    actions = [action('add', [], ['new']), action('merge', ['b1', 'new'], ['b1'])]
    assert edit.validate_actions(actions, old, final) == actions
    changed = copy.deepcopy(old); changed['businesses'].append({'key': 'new', 'name': '', 'purpose': '', 'boundary': ''})
    with pytest.raises(ValueError): edit.validate_actions([], old, changed)
    assert edit.validate_actions([action('split', ['b1'], ['b1', 'new'])], old, changed)
    erased = copy.deepcopy(old); erased['businesses'] = []
    assert edit.validate_actions([action('delete', ['b1', 'b2'], [])], old, erased)
    with pytest.raises(ValueError): edit.validate_actions([action('merge', ['unknown', 'b1'], ['b1'])], old, final)
    assert edit.validate_actions([action('undo', ['b1', 'b2'], ['b1', 'b2'])], old, final)


def test_unrepresentable_legacy_reason_remains_saved_but_confirmation_is_explicitly_blocked():
    value, bound, _ = complete_document()
    group = next(row for row in value['allocation_groups'] if row['kind'] == 'shared')
    group['reason'] = '原完整理由' * 500
    assert edit.validate_document(value, 8) == value
    assert any(issue['code'] == 'plan_reason_capacity' for issue in edit.validation(value, 8)['issues'])
    with pytest.raises(ValueError): edit.scope_ledger(value, indexed=bound['indexed'],
        input_claims=bound['input_claims'], source_snapshot_hash=bound['source_snapshot_hash'])


def test_original_utf8_command_and_internal_children_are_closed_and_deterministic():
    nonce = str(uuid4()); draft_id = str(uuid4())
    value = {'request_id': nonce, 'expected_version': 1, 'expected_record_hash': 'a' * 64,
        'document_hash': 'b' * 64, 'confirm': True}
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    assert store.parse_wire(raw, 'confirm') == value
    assert store._child_requests(nonce) == store._child_requests(nonce)
    assert len(set(store._child_requests(nonce))) == 2 and nonce not in store._child_requests(nonce)
    assert store.command_path(draft_id, 'confirm').endswith('/' + draft_id + '/confirm')
    for invalid in (raw.replace(b'"expected_version": 1', b'"expected_version": true'),
            b'{"request_id":"' + nonce.encode() + b'","request_id":"' + nonce.encode() + b'"}'):
        with pytest.raises(ValueError): store.parse_wire(invalid, 'confirm')
    assert digest(value) == digest(store.parse_wire(raw, 'confirm'))


def test_current_origin_comparison_omits_only_command_inventory_version_not_any_content_or_job_identity():
    # Pure comparison data, deliberately not a source fixture or a fake paid
    # consumer. _load_origin/_check_origin remain mandatory production gates.
    original = {'metadata': {'consumer_version': 4, 'projection_hash': 'a' * 64,
        'source_snapshot_hash': 'b' * 64, 'input_claims_hash': 'c' * 64,
        'consumer_id': str(uuid4()), 'capture_hash': 'd' * 64},
        'source_context': {'opaque': 'all original saved declarations'},
        'indexed_inputs': {'opaque': 'all original numbered inputs'},
        'raw_arguments': '{"opaque":"original whole model content"}',
        'projection': {'opaque': 'original full machine proposal'}, 'request_hash': 'e' * 64,
        'job_identity': {'id': str(uuid4()), 'actor_id': str(uuid4())},
        'capture_proof': {'opaque': 'original independent receipt/journal/capture'}}
    before = copy.deepcopy(original); advanced = copy.deepcopy(original)
    advanced['metadata']['consumer_version'] = 5
    assert store.same(store._origin_content(original), store._origin_content(advanced))
    assert original == before and original['metadata']['consumer_version'] == 4
    for field in ('source_context', 'indexed_inputs', 'raw_arguments', 'projection',
            'request_hash', 'job_identity', 'capture_proof'):
        changed = copy.deepcopy(advanced); changed[field] = {'changed': True}
        assert not store.same(store._origin_content(original), store._origin_content(changed)), field
    for field in ('projection_hash', 'source_snapshot_hash', 'input_claims_hash', 'consumer_id', 'capture_hash'):
        changed = copy.deepcopy(advanced); changed['metadata'][field] = 'f' * 64
        assert not store.same(store._origin_content(original), store._origin_content(changed)), field


def partial_projection():
    value, bound = partial_scene()
    return partial.build_partial_projection(json.dumps(value, ensure_ascii=False).encode('utf-8'), **bound)


def test_partial_seed_is_a_separate_human_document_and_keeps_every_unresolved_position():
    projection = partial_projection(); before = copy.deepcopy(projection)
    origin = {'metadata': {'origin_mode': store.PARTIAL, 'input_count': projection['input_count']},
        'projection': projection}
    document = store._seed(origin)
    assert document == edit.validate_document(projection['draft_seed'], projection['input_count'])
    assert document is not projection['draft_seed'] and projection == before and 'wire' not in projection
    validation = store._validation(document, origin)
    assert set(validation['unassigned_positions']) | set(validation['pending_ownership_positions'])
    assert any(issue['blocks_confirmation'] for issue in validation['issues'])
    assert projection['model_answer_valid'] is False and projection['original_call_success'] is False
    with pytest.raises((ValueError, KeyError)):
        store._seed({'metadata': {'input_count': projection['input_count']}, 'projection': projection})


def test_partial_nonliteral_name_needs_a_persisted_explicit_human_naming_decision_not_an_unrelated_resolution():
    projection = partial_projection(); seed = copy.deepcopy(projection['draft_seed'])
    issue = next(row for row in projection['issues'] if row['code'] == 'business_name_not_literal')
    blocked = lambda document: any(row['code'] == 'partial_name_requires_human_decision'
        for row in edit.partial_validation(document, projection)['issues'])
    assert blocked(seed)
    adopted = copy.deepcopy(seed)
    question = next(row for row in adopted['questions'] if row['key'] == issue['question_key'])
    question.update(status='resolved', resolution='此名称由我明确指定，不再当成原文明示。')
    assert not blocked(adopted)
    assert any(row['blocks_confirmation'] for row in edit.partial_validation(adopted, projection)['issues'])
    unrelated = copy.deepcopy(adopted)
    next(row for row in unrelated['questions'] if row['key'] == issue['question_key'])['question'] = '另一个无关问题已解决。'
    assert blocked(unrelated)
    absent = copy.deepcopy(adopted); absent['questions'] = [row for row in absent['questions'] if row['key'] != issue['question_key']]
    assert blocked(absent)
    renamed = copy.deepcopy(seed)
    next(row for row in renamed['businesses'] if row['key'] == issue['business_key'])['name'] = '人工调整后的业务名称'
    assert not blocked(renamed)
    empty = {'schema': edit.SCHEMA, 'businesses': [], 'allocation_groups': [], 'questions': []}
    assert not blocked(empty)
    assert projection['draft_seed'] == seed


def test_origin_mode_is_explicit_for_partial_while_complete_original_raw_requests_stay_compatible():
    original = {'request_id': str(uuid4()), 'consumer_id': str(uuid4()), 'expected_projection_hash': 'a' * 64}
    for mode in (None, store.COMPLETE, store.PARTIAL):
        value = copy.deepcopy(original)
        if mode is not None: value['origin_mode'] = mode
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        assert store.parse_wire(raw, 'create') == value
        assert ('origin_mode' in store.parse_wire(raw, 'create')) is (mode is not None)
    for mode in ('partial', 'failed', 'complete ', True, None, {}, []):
        value = {**original, 'origin_mode': mode}
        with pytest.raises(ValueError): store.parse_wire(json.dumps(value).encode(), 'create')
    first = {'metadata': {'consumer_version': 1, 'origin_mode': store.PARTIAL}, 'capture_proof': {'raw': 'kept'}}
    other = copy.deepcopy(first); other['metadata']['origin_mode'] = store.COMPLETE
    assert not store.same(store._origin_content(first), store._origin_content(other))
