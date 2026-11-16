"""Public synthetic adapter tests only; root owns execution.

No actual/private report, response, journal, API, provider, DB or approval.
The primary fixture is built through the real pure V6 projection validator;
malformed/capacity cases deliberately exercise refusal, not source proof.
"""
import copy
import json
import socket
import subprocess

import pytest

from generic_agents import report_evidence_manual_v5 as human
from generic_agents import report_evidence_manual_v6_adapter as subject
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from generic_agents.contracts import digest
from tests.test_report_evidence_wire_v3_pure import reduce_case, identity


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure human adapter cannot connect or launch')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    old, inputs = reduce_case()
    old['result']['domain'] = ' 合成领域🙂\n保留全角＋与+ '
    business = old['result']['businesses'][0]
    business['steps'][0]['purpose'] = ' 原职责🙂\n 保留字序与空白 '
    business['participants'][0]['responsibility'] = ' 跨业务登记职责，不授予账号权限 '
    business['interactions'][0]['condition'] = ' 原交接条件\n'
    business['coordination'][0]['kind'] = 'parallel'
    business['coordination'][0]['start_rule'] = ' 原并行说明，未证明可执行 '
    second = copy.deepcopy(business)
    second['name'] += '乙'
    second['steps'][1]['next_index'] = 0
    old['result']['businesses'].append(second)
    old['result']['global_claims'].append({'kind': '治理', 'statement': ' 全局含义未知 ',
        'span_refs': [], 'status': 'unknown', 'explanation': ' 原文仍需人工核对 '})
    old['result']['questions'].append(' 未知治理含义待核，未自动批准。 ')
    items = {f'map-{number}/claims/{index}': copy.deepcopy(claim)
        for number, group in enumerate(inputs['map_inputs'], 1)
        for index, claim in enumerate(group['projection']['claims'])}
    expected = {'catalog': inputs['catalog'], 'allowed_span_refs': inputs['allowed_span_refs'],
        'context': inputs['context'], 'input_order': list(items), 'input_items': items}
    encoded = wire6.encode_reduction_wire_v6(old)
    projection = wire6.build_reduction_projection(
        json.dumps(encoded, ensure_ascii=False).encode('utf-8'), **expected)
    return old, projection, expected


def test_v6_to_human_is_exact_for_all_semantics_and_does_not_relabel_model_or_proof():
    original_wire, projection, _ = scene()
    before = copy.deepcopy(projection)
    note = ' 私人末字🙂\n '
    value = subject.manual_from_projection_v6(projection['result'], note)
    assert set(value) == {'schema', 'result', 'note'}
    assert value['schema'] == human.MANUAL_VALUE and value['note'] == note
    assert value['result'] == original_wire['result']
    assert projection == before and projection['result']['schema'] == wire6.CANDIDATE
    assert subject.manual_from_projection_v6(projection['result'])['note'] == ''
    assert not {'binding', 'receipt', 'wire_value_hash', 'projection_hash', 'model_result_hash'} & set(value)
    value['result']['global_claims'][0]['statement'] = '后来人工输入'
    assert projection == before


def test_business_local_zero_none_units_and_explicit_cycle_keep_their_exact_targets():
    original, projection, _ = scene()
    value = subject.manual_from_projection_v6(projection['result'])
    first, second = value['result']['businesses']
    assert [s['next_index'] for s in first['steps']] == [1, None]
    assert [s['next_index'] for s in second['steps']] == [1, 0]
    for business in (first, second):
        assert business['interactions'][0]['from_participant_index'] == 0
        assert business['interactions'][0]['to_participant_index'] == 1
        assert business['interactions'][0]['step_indices'] == [0, 1]
        assert business['coordination'][0]['kind'] == 'parallel'
    assert value['result']['objects'] == original['result']['objects']
    assert value['result']['objects'][0]['fields'][0]['unit'] is None
    assert value['result']['objects'][1]['fields'][1]['unit'] == '株'
    assert value['result']['relationships'] == original['result']['relationships']


@pytest.mark.parametrize('mode', ['old', 'unknown', 'proof', 'quote', 'missing_field',
    'bad_bool', 'bad_unit', 'bad_key', 'duplicate_key', 'duplicate_global',
    'foreign_step', 'foreign_person', 'missing_step', 'null_end', 'auto_end',
    'repeated_steps', 'self_step', 'self_person', 'missing_condition_step'])
def test_malformed_or_ambiguous_inputs_reject_without_guessing_or_mutating(mode):
    _, projection, _ = scene()
    result = copy.deepcopy(projection['result']); b = result['businesses'][0]
    if mode == 'old': result['schema'] = 'report-evidence-candidate-projection-v5'
    elif mode == 'unknown': result['schema'] = 'report-evidence-candidate-projection-v99'
    elif mode == 'proof': result['projection_hash'] = 'a' * 64
    elif mode == 'quote': b['steps'][0]['citations'][0]['quote'] = 'not this closed citation type'
    elif mode == 'missing_field': del b['coordination']
    elif mode == 'bad_bool': result['objects'][0]['fields'][0]['required'] = 1
    elif mode == 'bad_unit': result['objects'][0]['fields'][0]['unit'] = False
    elif mode == 'bad_key': b['steps'][0]['key'] = 'invented'
    elif mode == 'duplicate_key': b['steps'][1]['key'] = b['steps'][0]['key']
    elif mode == 'duplicate_global': result['global_claims'][1]['key'] = result['global_claims'][0]['key']
    elif mode == 'foreign_step': b['steps'][0]['next_key'] = result['businesses'][1]['steps'][0]['key']
    elif mode == 'foreign_person': b['interactions'][0]['from_participant'] = result['businesses'][1]['participants'][0]['key']
    elif mode == 'missing_step': b['steps'][0]['next_key'] = 'missing'
    elif mode == 'null_end': b['steps'][0]['next_key'] = None
    elif mode == 'auto_end': b['steps'][0]['next_key'] = 'auto'
    elif mode == 'repeated_steps': b['conditions'][0]['step_keys'] *= 2
    elif mode == 'self_step': b['steps'][0]['next_key'] = b['steps'][0]['key']
    elif mode == 'self_person': b['interactions'][0]['to_participant'] = b['interactions'][0]['from_participant']
    else: b['conditions'][0]['step_keys'] = ['missing']
    before = copy.deepcopy(result)
    with pytest.raises(ValueError): subject.manual_from_projection_v6(result)
    assert result == before


@pytest.mark.parametrize('note', [None, False, 1, 'x' * 12001, '不可保存\x00', '\ud800'])
def test_note_is_strict_exact_and_never_normalized_or_coerced(note):
    _, projection, _ = scene()
    before = copy.deepcopy(projection)
    with pytest.raises(ValueError): subject.manual_from_projection_v6(projection['result'], note)
    assert projection == before


def test_existing_512kib_human_limit_refuses_whole_value_without_truncating():
    _, projection, _ = scene(); result = copy.deepcopy(projection['result'])
    claim = result['global_claims'][0]
    result['global_claims'] = [{**copy.deepcopy(claim), 'key': 'gc_' + f'{i:032x}',
        'statement': '字' * 2000, 'explanation': '核' * 1000} for i in range(160)]
    before = copy.deepcopy(result)
    with pytest.raises(ValueError, match='capacity exceeded'):
        subject.manual_from_projection_v6(result)
    assert result == before and len(result['global_claims']) == 160


def test_human_context_binds_original_v6_hash_in_its_independent_identity_domain():
    _, model, expected = scene(); value = subject.manual_from_projection_v6(model['result'], '人稿')
    original = copy.deepcopy(model)
    context = {'schema': human.CONTEXT, 'actor_id': identity(1), 'workspace_id': identity(2),
        'draft_id': identity(3), 'request_id': identity(4), 'accepted_revision': 2,
        'model_result_hash': digest(model['result']), 'source_hash': model['result']['source_hash'],
        'catalog_hash': model['result']['catalog_hash']}
    saved = human.build_manual_projection(value, review_context=context,
        **{key: expected[key] for key in ('catalog', 'allowed_span_refs', 'context')})
    assert saved['review_context']['model_result_hash'] == digest(model['result'])
    assert saved['result']['schema'] == human.MANUAL_CANDIDATE
    assert saved['result']['businesses'][0]['steps'][0]['key'] != model['result']['businesses'][0]['steps'][0]['key']
    assert model == original
    with pytest.raises(ValueError): human.manual_from_projection(model['result'])


def test_later_adapter_value_does_not_change_original_pending_command_or_nonce():
    _, projection, _ = scene(); value = subject.manual_from_projection_v6(projection['result'], '原末字A')
    pending = {'request_id': identity(8), 'revision': 9, 'value': copy.deepcopy(value)}
    before = copy.deepcopy(pending); command_hash = digest(pending)
    later = subject.manual_from_projection_v6(projection['result'], '后来末字B')
    later['result']['businesses'][0]['steps'][0]['purpose'] = '后来人稿'
    assert pending == before and digest(pending) == command_hash
    assert later['note'] == '后来末字B' and pending['value']['note'] == '原末字A'
