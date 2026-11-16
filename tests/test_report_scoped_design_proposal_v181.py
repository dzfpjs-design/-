"""Synthetic pure grouping only; not API/IAM, real source or AI quality evidence."""
import copy
import socket
import subprocess

import pytest

from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents import report_scoped_design_plan_v180 as planner
from generic_agents import report_scoped_design_proposal_v181 as subject
from generic_agents.report_evidence_plan import build_evidence_source

SNAPSHOT = 'a' * 64


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('A pure proposal cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def claim(kind, statement, ref):
    return {'kind': kind, 'statement': statement, 'span_refs': [ref], 'status': 'explicit',
        'explanation': '', 'raw_extension': {'number': 1.0, 'zero': -0.0, 'flag': True}}


def scene():
    catalog = build_evidence_source('b' * 64, [
        ['w:p1', '设备调度业务。设备调度需人工核对。'],
        ['w:p2', '资产盘点业务。资产盘点应记录原编号。'],
        ['w:p3', '设备调度与资产盘点有关联，具体责任仍需人工核对。'],
        ['w:p4', '另有原始资料，不能自动排除或者归作背景。']])
    claims = [claim('业务', '设备调度', 'w:p1/s1'), claim('步骤', '核对新申请', 'w:p1/s1'),
        claim('业务', '资产盘点', 'w:p2/s1'), claim('条件', '条件待核对', 'w:p2/s1'),
        claim('职责', '两业务责任待核对', 'w:p3/s1'), claim('其他', '独立资料', 'w:p4/s1'),
        claim('其他', '设备调度只在模型声明中出现', 'w:p4/s1'),
        claim('步骤', '跨业务步骤待核对', 'w:p3/s1')]
    return claims, codec.build_indexed_inputs(claims, SNAPSHOT), catalog


def build(claims, indexed, catalog, **kwargs):
    return subject.build_proposal(indexed, SNAPSHOT, input_claims=claims, catalog=catalog, **kwargs)


def reseal(value):
    value['proposal_hash'] = codec.digest({key: item for key, item in value.items() if key != 'proposal_hash'})


def test_dynamic_business_seeds_batch_related_claims_and_keep_uncertain_inputs_without_mutation():
    claims, indexed, catalog = scene(); before = codec.canonical([claims, indexed, catalog])
    value = build(claims, indexed, catalog)
    assert value == subject.check_proposal(value, indexed, SNAPSHOT, input_claims=claims, catalog=catalog)
    assert [scope['name'] for scope in value['ledger']['scopes']] == ['设备调度', '资产盘点']
    assert [scope['claim_positions'] for scope in value['ledger']['scopes']] == [[0, 1], [2, 3]]
    assert value['unassigned_positions'] == [4, 5, 6, 7]
    assert [item['input_position'] for item in value['conflicts']] == [4, 7]
    assert value['ledger']['shared'] == value['ledger']['background'] == []
    assert [item['input_position'] for item in value['suggestions']] == list(range(8))
    assert [item['claim_hash'] for item in value['suggestions']] == indexed['ordered_claim_hashes']
    assert value['seed_provenance'][0]['origin'] == 'machine_business_claim'
    assert all(item['evidence_status'] == 'inferred' for item in value['seed_provenance'])
    assert all(value[key] is False for key in ['source_anchors_verified', 'ownership_coverage_verified',
        'semantic_quality_verified', 'human_confirmed', 'can_apply', 'can_publish', 'dispatch_allowed'])
    assert set(value['ledger']) == planner._BASE_KEYS
    assert codec.canonical([claims, indexed, catalog]) == before


def test_literal_name_uses_original_referenced_text_not_machine_statement_or_unrelated_catalog_text():
    claims, indexed, catalog = scene(); value = build(claims, indexed, catalog)
    assert value['suggestions'][6]['status'] == 'unassigned'
    assert value['suggestions'][6]['matches'] == []
    assert value['suggestions'][4]['matches'][0]['name_span_refs'] == ['w:p3/s1']
    assert value['suggestions'][4]['matches'][0]['rules'] == ['original_name_literal']
    without_catalog = build(claims, indexed, None)
    assert without_catalog['catalog_hash'] is None
    assert without_catalog['suggestions'][4]['status'] == 'unassigned'
    assert without_catalog['ledger']['scopes'][0]['claim_positions'] == [0, 1]


def test_custom_editable_seed_batches_same_span_without_becoming_human_confirmation():
    claims, indexed, catalog = scene()
    seeds = [{'scope_id': 'custom:service', 'name': '自定义工单范围', 'claim_positions': [0]}]
    before = codec.canonical(seeds)
    value = build(claims, indexed, catalog, scope_seeds=seeds)
    assert value['ledger']['scopes'] == [{**seeds[0], 'claim_positions': [0, 1], 'context_positions': []}]
    assert value['unassigned_positions'] == [2, 3, 4, 5, 6, 7]
    assert value['seed_mode'] == 'caller_supplied_unverified'
    assert value['seed_provenance'][0]['origin'] == 'caller_supplied_unverified'
    assert value['human_confirmed'] is False
    assert 'confirmation' not in value['ledger']
    assert codec.canonical(seeds) == before
    with pytest.raises(planner.ScopedDesignPlanError):
        planner.validate_scope_ledger(indexed, SNAPSHOT, value['ledger'], input_claims=claims)


def test_overlap_between_seeds_is_a_conflict_not_automatic_shared_or_forced_seed_ownership():
    claims, indexed, catalog = scene()
    seeds = [{'scope_id': 'first', 'name': '范围甲', 'claim_positions': [0]},
        {'scope_id': 'second', 'name': '范围乙', 'claim_positions': [1]}]
    value = build(claims, indexed, catalog, scope_seeds=seeds)
    assert value['conflicts'][:2] == [{'input_position': 0, 'candidate_scope_ids': ['first', 'second']},
        {'input_position': 1, 'candidate_scope_ids': ['first', 'second']}]
    assert all(not scope['claim_positions'] for scope in value['ledger']['scopes'])
    assert value['ledger']['shared'] == value['ledger']['background'] == []
    assert value['unassigned_positions'] == list(range(8))


def test_empty_seeds_and_no_business_claims_are_savable_empty_base_drafts_not_default_businesses():
    claims, indexed, catalog = scene()
    explicit_empty = build(claims, indexed, catalog, scope_seeds=[])
    assert explicit_empty['scope_seeds'] == explicit_empty['ledger']['scopes'] == []
    assert explicit_empty['unassigned_positions'] == list(range(8))
    for item in claims:
        item['kind'] = '职责'
    indexed = codec.build_indexed_inputs(claims, SNAPSHOT)
    automatic_empty = build(claims, indexed, catalog)
    assert automatic_empty['scope_seeds'] == automatic_empty['ledger']['scopes'] == []
    assert automatic_empty['unassigned_positions'] == list(range(8))
    assert automatic_empty['ledger']['background'] == []


def test_ascii_name_matching_is_literal_case_sensitive_and_not_a_substring_of_an_identifier():
    catalog = build_evidence_source('b' * 64, [['w:p1', 'TRAIL 与 AIMode'],
        ['w:p2', 'AI 独立名称'], ['w:p3', 'ai 小写名称']])
    claims = [claim('其他', '模型声明 AI 不能替代原文', f'w:p{i}/s1') for i in range(1, 4)]
    indexed = codec.build_indexed_inputs(claims, SNAPSHOT)
    value = build(claims, indexed, catalog,
        scope_seeds=[{'scope_id': 'dynamic', 'name': 'AI', 'claim_positions': []}])
    assert value['ledger']['scopes'][0]['claim_positions'] == [1]
    assert value['unassigned_positions'] == [0, 2]


def test_long_business_statement_is_not_silently_clipped_into_a_business_name():
    claims = [claim('业务', '完整原始声明' * 40, 'w:p1/s1')]
    indexed = codec.build_indexed_inputs(claims, SNAPSHOT); before = codec.canonical([claims, indexed])
    value = build(claims, indexed, None)
    assert value['scope_seeds'][0]['name'] == '待命名业务候选 1'
    assert value['seed_provenance'][0]['naming'] == 'placeholder'
    assert value['seed_provenance'][0]['claim_hashes'] == indexed['ordered_claim_hashes']
    assert codec.canonical([claims, indexed]) == before


def test_identical_business_label_keeps_each_original_claim_and_its_provenance():
    claims, _, catalog = scene(); claims[2]['statement'] = claims[0]['statement']
    indexed = codec.build_indexed_inputs(claims, SNAPSHOT)
    value = build(claims, indexed, catalog)
    assert len(value['scope_seeds']) == 1
    assert value['scope_seeds'][0]['claim_positions'] == [0, 2]
    assert value['seed_provenance'][0]['claim_hashes'] == [indexed['ordered_claim_hashes'][i] for i in [0, 2]]
    assert len(value['suggestions']) == value['input_count'] == 8
    assert value['semantic_quality_verified'] is False


def test_automatic_seed_overflow_keeps_all_candidates_and_every_unassigned_input_without_first_n_cutoff():
    claims = [claim('业务', '动态业务 ' + str(i), f'w:p{i + 1}/s1') for i in range(planner.MAX_SCOPES + 1)]
    indexed = codec.build_indexed_inputs(claims, SNAPSHOT)
    value = build(claims, indexed, None)
    assert len(value['scope_seeds']) == len(value['seed_provenance']) == len(claims)
    assert value['ledger']['scopes'] == []
    assert value['unassigned_positions'] == list(range(len(claims)))
    assert value['blocked_reasons'] == ['seed_candidates_capacity']
    assert value['ledger']['shared'] == value['ledger']['background'] == []


@pytest.mark.parametrize('change', [
    lambda seed: seed.update(claim_positions=[True]),
    lambda seed: seed.update(claim_positions=[8]),
    lambda seed: seed.update(claim_positions=[0, 0]),
    lambda seed: seed.update(claim_positions=[1, 0]),
    lambda seed: seed.update(scope_id=' scope '),
    lambda seed: seed.update(name=''),
    lambda seed: seed.update(confirmation={'status': 'confirmed'}),
])
def test_bad_seed_shape_types_and_positions_are_rejected_not_repaired(change):
    claims, indexed, catalog = scene()
    seed = {'scope_id': 'dynamic', 'name': '当前业务', 'claim_positions': [0]}; change(seed)
    with pytest.raises(subject.ScopedDesignProposalError):
        build(claims, indexed, catalog, scope_seeds=[seed])


@pytest.mark.parametrize('change', [
    lambda value: value.update(unassigned_positions=[]),
    lambda value: value['ledger']['background'].append({'input_position': 5, 'reason': '不得偷偷排除'}),
    lambda value: value['suggestions'][0].update(input_position=0.0),
    lambda value: value['suggestions'][0].update(evidence_status='explicit'),
    lambda value: value.update(human_confirmed=True),
    lambda value: value.update(private_note='不应混入公开建议'),
])
def test_self_resealed_tampering_cannot_change_classification_evidence_or_authority(change):
    claims, indexed, catalog = scene(); value = build(claims, indexed, catalog)
    change(value); reseal(value)
    with pytest.raises(subject.ScopedDesignProposalError):
        subject.check_proposal(value, indexed, SNAPSHOT, input_claims=claims, catalog=catalog)


def test_independent_input_and_catalog_mismatch_refuses_the_proposal():
    claims, indexed, catalog = scene()
    changed = copy.deepcopy(claims); changed[0]['statement'] = '替换的声明'
    with pytest.raises(subject.ScopedDesignProposalError):
        build(changed, indexed, catalog)
    changed = copy.deepcopy(claims); changed[0]['span_refs'] = ['w:p1/s99']
    with pytest.raises(subject.ScopedDesignProposalError):
        build(changed, codec.build_indexed_inputs(changed, SNAPSHOT), catalog)
    changed_catalog = copy.deepcopy(catalog); changed_catalog['index']['w:p1/s1']['text'] = '被改写原文'
    with pytest.raises(subject.ScopedDesignProposalError):
        build(claims, indexed, changed_catalog)
    with pytest.raises(subject.ScopedDesignProposalError):
        subject.build_proposal(indexed, 'c' * 64, input_claims=claims, catalog=catalog)
