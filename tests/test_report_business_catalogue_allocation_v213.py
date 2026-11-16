"""Synthetic no-I/O contract/intent preservation, not paid or user-quality proof."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_catalogue_allocation_plan_v213 as planning
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_business_catalogue_edit_v211 as edit
from generic_agents import report_business_catalogue_partial_v212 as partial
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents.report_evidence_plan import build_evidence_source
from tests.test_report_business_catalogue_v211 import scene, single, raw


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure allocation cannot connect, execute or spend')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def inputs(count=8, *, populated=True):
    candidate, bound = scene() if count == 8 else single(count)
    document = {'schema': edit.SCHEMA, 'businesses': [
        {key: copy.deepcopy(business[key]) for key in ('key', 'name', 'purpose', 'boundary')}
        for business in candidate['businesses']], 'allocation_groups': [], 'questions': []}
    if populated:
        document = edit.from_projection({'wire': candidate, 'input_count': count})
    reference = {'id': 'draft-real-caller-id', 'workspace_id': 'workspace', 'actor_id': 'actor',
        'version': 3, 'record_hash': 'c' * 64, 'origin_hash': 'd' * 64,
        'document_hash': codec.digest(document)}
    return {**bound, 'input_order': [f'map-original-{i // 3:03d}/global_claims/{i % 3}'
        for i in range(count)], 'draft_ref': reference, 'document': document}


def explicit(bound, positions):
    return planning.build_plan(**bound, selection={'mode': 'explicit', 'positions': positions})


def answer(plan, ordinal=0):
    keys = plan['batches'][ordinal]['decision_keys']
    first = plan['businesses'][0]['key']
    return {'schema': wire.WIRE, 'decisions': {key: {'disposition': 'assign',
        'business_keys': [first], 'reason': '与已审业务职责对应，仍需人核对', 'questions': []} for key in keys}}


def replace_document(bound, document):
    return {**bound, 'document': document,
        'draft_ref': {**bound['draft_ref'], 'document_hash': codec.digest(document)}}


def test_targets_follow_current_saved_human_work_not_old_missing_statistics():
    bound = inputs()
    document = copy.deepcopy(bound['document'])
    document['allocation_groups'] = [group for group in document['allocation_groups']
        if 5 not in group['input_positions']]
    # A manually created unresolved question can reopen an otherwise owned item.
    document['questions'].append({'key': 'human_owner', 'kind': 'ownership', 'input_positions': [1],
        'candidate_business_keys': ['b1'], 'question': '人工仍需核对责任', 'status': 'open', 'resolution': ''})
    bound = replace_document(bound, document)
    before = codec.canonical(bound)
    plan = planning.build_plan(**bound)
    assert plan['target_positions'] == [1, 5, 6]
    assert plan['source_pins']['input_count'] == 8
    assert plan['source']['input_order'] == bound['input_order']
    assert planning.check_plan(plan, **bound) == plan
    assert codec.canonical(bound) == before
    assert plan['flags']['human_review_verified'] is False
    assert not any(value for key, value in plan['flags'].items() if key != 'machine_only')


@pytest.mark.parametrize('count,expected_sizes', [(1, [1]), (9, [9]), (81, [80, 1]), (163, [80, 80, 3])])
def test_dynamic_inventory_is_partitioned_exactly_with_global_fixed_keys(count, expected_sizes):
    bound = inputs(count, populated=False)
    plan = planning.build_plan(**bound)
    assert [len(batch['input_positions']) for batch in plan['batches']] == expected_sizes
    assert [position for batch in plan['batches'] for position in batch['input_positions']] == list(range(count))
    assert [key for batch in plan['batches'] for key in batch['decision_keys']] == [f'p{i:06d}' for i in range(count)]
    assert plan['source_pins']['input_order_hash'] == codec.digest(bound['input_order'])
    last = planning.batch_evidence(plan, len(plan['batches']) - 1)
    assert last['entries'][-1]['input_id'] == bound['input_order'][-1]


def test_no_work_stays_readable_and_never_silently_selects_completed_inputs():
    bound = inputs(3)
    plan = planning.build_plan(**bound)
    assert plan['status'] == 'no_work' and plan['target_positions'] == [] and plan['batches'] == []
    with pytest.raises(planning.AllocationPlanError):
        wire.output_schema(plan, 0)
    assert explicit(bound, [2])['target_positions'] == [2]


def test_http_disclosure_keeps_whole_selected_claims_and_all_exact_spans_but_no_map_ids():
    bound = inputs(3, populated=False)
    text = '原片段  😀\n' * 90
    bound['catalog'] = build_evidence_source('b' * 64, [['w:p1', text], ['w:p2', '不能披露的别批文字'], ['w:p3', '第三条']])
    # Use actual catalogue index rather than inventing source/page metadata.
    refs = [key for key in bound['catalog']['index'] if key.startswith('w:p1/')]
    bound['input_claims'][0]['span_refs'] = refs
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], bound['source_snapshot_hash'])
    plan = explicit(bound, [0])
    disclosure = planning.batch_disclosure(plan, 0)
    assert disclosure['claims'] == [{'decision_key': 'p000000', 'claim': bound['input_claims'][0]}]
    assert disclosure['spans'] == [[ref, bound['catalog']['index'][ref]['text']] for ref in refs]
    encoded = codec.canonical(disclosure)
    assert '不能披露的别批文字' not in encoded
    assert not any(identifier in encoded for identifier in bound['input_order'])
    assert 'input_order' not in disclosure and 'base_document' not in disclosure and 'source' not in disclosure
    assert len(encoded.encode('utf-8')) == plan['batches'][0]['disclosure_bytes']
    evidence = planning.batch_evidence(plan, 0)
    assert evidence['entries'][0]['input_id'] == bound['input_order'][0]
    disclosure['claims'][0]['claim']['statement'] = '不能反向篡改'
    evidence['entries'][0]['claim']['statement'] = '也不能反向篡改'
    assert plan['source']['input_claims'] == bound['input_claims']


def test_byte_partition_is_whole_items_and_never_drops_oversize_references():
    bound = inputs(4, populated=False)
    for claim in bound['input_claims']:
        claim['raw_extension']['large'] = '完整声明附属字段😀' * 400
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], bound['source_snapshot_hash'])
    one = explicit(bound, [0])
    ceiling = one['batches'][0]['disclosure_bytes'] + 100
    plan = planning.build_plan(**bound, max_disclosure_bytes=ceiling)
    assert [batch['input_positions'] for batch in plan['batches']] == [[0], [1], [2], [3]]
    for ordinal in range(4):
        assert len(codec.canonical(planning.batch_disclosure(plan, ordinal)).encode('utf-8')) <= ceiling
    with pytest.raises(planning.AllocationPlanError, match='single_input_disclosure_capacity'):
        planning.build_plan(**bound, max_disclosure_bytes=one['batches'][0]['disclosure_bytes'] - 1)


@pytest.mark.parametrize('alter', [
    lambda b: b.update(input_order=['same'] * len(b['input_order'])),
    lambda b: b['input_order'].pop(),
    lambda b: b['draft_ref'].update(version=True),
    lambda b: b['draft_ref'].update(document_hash='f' * 64),
    lambda b: b['draft_ref'].update(unknown='no'),
    lambda b: b['document']['businesses'][0].update(name=' '),
])
def test_invalid_source_identity_or_unreviewable_registry_fails_without_mutating_work(alter):
    bound = inputs()
    alter(bound)
    unchanged = codec.canonical(bound)
    with pytest.raises(planning.AllocationPlanError):
        planning.build_plan(**bound)
    assert codec.canonical(bound) == unchanged


@pytest.mark.parametrize('selection', [
    {'mode': 'explicit', 'positions': [True]}, {'mode': 'explicit', 'positions': [1.0]},
    {'mode': 'explicit', 'positions': [2, 1]}, {'mode': 'explicit', 'positions': [1, 1]},
    {'mode': 'explicit', 'positions': [8]}, {'mode': 'unassigned_and_pending', 'positions': [1]},
    {'mode': 'explicit', 'positions': [], 'unknown': True},
])
def test_target_selector_never_coerces_sorts_duplicates_or_out_of_range(selection):
    with pytest.raises(planning.AllocationPlanError):
        planning.build_plan(**inputs(), selection=selection)


def test_partial_original_name_diagnostics_block_until_real_human_adjustment():
    candidate, bound = scene()
    candidate['businesses'][0]['name'] = '并非原文的机器新名称'
    origin = partial.build_partial_projection(raw(candidate), **bound)
    source = inputs()
    source = replace_document(source, origin['draft_seed'])
    with pytest.raises(planning.AllocationPlanError, match='catalogue_requires_review'):
        planning.build_plan(**source, name_origin=origin)
    document = copy.deepcopy(source['document'])
    diagnostic = next(issue for issue in origin['issues'] if issue['code'] == 'business_name_not_literal')
    question = next(question for question in document['questions'] if question['key'] == diagnostic['question_key'])
    question.update(status='resolved', resolution='人工保留为本项目自定业务名；不是原文明示名称')
    adopted = planning.build_plan(**replace_document(source, document), name_origin=origin)
    assert adopted['flags']['human_review_verified'] is False
    question['question'] = '无关的已解决问题'
    with pytest.raises(planning.AllocationPlanError, match='catalogue_requires_review'):
        planning.build_plan(**replace_document(source, document), name_origin=origin)


def test_cold_plan_rebuild_binds_same_count_different_source_and_actual_original_ids():
    bound = inputs()
    plan = explicit(bound, [1, 6])
    original = {**bound, 'selection': {'mode': 'explicit', 'positions': [1, 6]}}
    for field, changed in [('source_snapshot_hash', 'f' * 64),
            ('input_order', list(reversed(bound['input_order']))),
            ('draft_ref', {**bound['draft_ref'], 'version': 4})]:
        other = {**original, field: changed}
        if field == 'source_snapshot_hash':
            other['indexed'] = codec.build_indexed_inputs(other['input_claims'], changed)
        with pytest.raises(planning.AllocationPlanError):
            planning.check_plan(plan, **other)
    forged = copy.deepcopy(plan)
    forged['source']['input_order'][0] = 'invented-original-id'
    forged['plan_hash'] = codec.digest({key: value for key, value in forged.items() if key != 'plan_hash'})
    with pytest.raises(planning.AllocationPlanError):
        planning.check_plan(forged, **original)


def test_complete_key_schema_and_platform_mapping_preserve_raw_and_leave_everything_for_review():
    bound = inputs()
    plan = explicit(bound, [0, 1, 2, 6])
    value = answer(plan)
    value['decisions']['p000001'].update(business_keys=['b1', 'b2'])
    value['decisions']['p000002'].update(disposition='background', business_keys=[])
    value['decisions']['p000006'].update(disposition='pending', business_keys=['b2'],
        questions=[{'kind': 'ownership', 'question': '这项职责由谁负责？'}])
    original = raw(value)
    schema = wire.output_schema(plan, 0)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    projected = wire.build_projection(original, plan=plan, batch_ordinal=0)
    assert wire.check_projection(projected, original, plan=plan, batch_ordinal=0) == projected
    assert projected['raw_wire'] == value
    assert [row['proposed_group']['kind'] for row in projected['decisions']] == ['business', 'shared', 'background', 'pending']
    assert projected['raw_arguments_sha256'] == hashlib.sha256(original).hexdigest()
    assert all(row['review_required'] and row['platform_derived'] for row in projected['decisions'])
    assert projected['decisions'][3]['proposed_questions'][0]['status'] == 'open'
    assert not projected['flags']['can_apply'] and not projected['flags']['human_confirmed']


@pytest.mark.parametrize('alter', [
    lambda v: v['decisions'].pop('p000001'),
    lambda v: v['decisions'].update(p000007=copy.deepcopy(v['decisions']['p000001'])),
    lambda v: v.update(source_snapshot_hash='a' * 64),
    lambda v: v['decisions']['p000001'].update(input_position=1),
    lambda v: v['decisions']['p000001'].update(name='模型改名'),
    lambda v: v['decisions']['p000001'].update(disposition=True),
    lambda v: v['decisions']['p000001'].update(business_keys=[True]),
    lambda v: v['decisions']['p000001'].update(business_keys=['unknown']),
    lambda v: v['decisions']['p000001'].update(business_keys=['b1', 'b1']),
    lambda v: v['decisions']['p000001'].update(business_keys=[]),
    lambda v: v['decisions']['p000001'].update(disposition='background'),
    lambda v: v['decisions']['p000001'].update(disposition='pending'),
    lambda v: v['decisions']['p000001'].update(reason=' '),
    lambda v: v['decisions']['p000001'].update(questions=[{'kind': 'ownership', 'question': '还不知道归属'}]),
    lambda v: v['decisions']['p000001'].update(questions=[{'kind': 'missing_info', 'question': '需补资料', 'page': 8}]),
])
def test_structural_or_coverage_failure_never_becomes_partial_or_repaired_success(alter):
    plan = explicit(inputs(), [1, 6])
    value = answer(plan)
    alter(value)
    original = raw(value)
    with pytest.raises(planning.AllocationPlanError):
        wire.build_projection(original, plan=plan, batch_ordinal=0)
    assert original == raw(value)


@pytest.mark.parametrize('raw_bytes', [
    b'{"schema":"x","schema":"y","decisions":{}}',
    b'{"schema":"report-business-catalogue-allocation-output-v213","decisions":{"p000001":{},"p000001":{}}}',
    b'{"schema":"x","decisions":NaN}', b'{} trailing', b'{"schema":', b'\xff',
])
def test_raw_json_duplicate_keys_nonfinite_trailing_and_invalid_utf8_fail_closed(raw_bytes):
    with pytest.raises(planning.AllocationPlanError):
        wire.parse_wire(raw_bytes, plan=explicit(inputs(), [1]), batch_ordinal=0)


def test_apply_is_only_a_diff_preserves_unselected_human_intent_questions_and_resolutions():
    bound = inputs()
    document = copy.deepcopy(bound['document'])
    document['allocation_groups'][0]['reason'] = '人工已编辑的完整理由😀\n不能被模型覆盖'
    document['questions'][0].update(status='resolved', resolution='人工明确的处理决定，必须留存')
    bound = replace_document(bound, document)
    plan = explicit(bound, [1, 6])
    value = answer(plan)
    value['decisions']['p000001']['questions'] = [{'kind': 'missing_info', 'question': '需补什么资料？'}]
    projected = wire.build_projection(raw(value), plan=plan, batch_ordinal=0)
    before = codec.canonical([bound, plan, projected])
    result = wire.construct_apply_document(projected, raw(value), plan=plan, batch_ordinal=0,
        document=bound['document'], draft_ref=bound['draft_ref'], selected_decision_keys=['p000001'])
    updated = result['document']
    assert updated['businesses'] == document['businesses']
    assert updated['questions'][:len(document['questions'])] == document['questions']
    retained = next(group for group in updated['allocation_groups'] if group['key'] == document['allocation_groups'][0]['key'])
    assert retained == {**document['allocation_groups'][0], 'input_positions': [0, 7]}
    assert [group for group in updated['allocation_groups'] if 6 in group['input_positions']] == [
        group for group in document['allocation_groups'] if 6 in group['input_positions']]
    assert result['selected_decision_keys'] == ['p000001']
    assert result['question_origins'][0]['platform_derived']
    assert result['flags']['can_apply'] is False and result['flags']['can_confirm'] is False
    assert codec.canonical([bound, plan, projected]) == before
    result['document']['businesses'][0]['name'] = '不能反向改变原稿'
    assert bound['document']['businesses'][0]['name'] == document['businesses'][0]['name']


def test_assigning_pending_position_does_not_resolve_a_human_question_or_unlock_confirmation():
    bound = inputs()
    plan = explicit(bound, [6])
    value = answer(plan)
    # Same text as an old question is intentionally a NEW unresolved suggestion.
    value['decisions']['p000006']['questions'] = [{'kind': 'missing_info',
        'question': bound['document']['questions'][1]['question']}]
    projected = wire.build_projection(raw(value), plan=plan, batch_ordinal=0)
    result = wire.construct_apply_document(projected, raw(value), plan=plan, batch_ordinal=0,
        document=bound['document'], draft_ref=bound['draft_ref'], selected_decision_keys=['p000006'])
    assert result['document']['questions'][:2] == bound['document']['questions']
    assert len(result['document']['questions']) == 3
    assert result['document']['questions'][2]['status'] == 'open'
    assert result['validation']['pending_ownership_positions'] == [6]


def test_projection_tampering_changed_draft_or_empty_selection_never_writes_or_rebases():
    bound = inputs()
    plan = explicit(bound, [1])
    value = answer(plan)
    original = raw(value)
    projected = wire.build_projection(original, plan=plan, batch_ordinal=0)
    forged = copy.deepcopy(projected)
    forged['decisions'][0]['input_id'] = 'borrowed-real-looking-map-id'
    forged['projection_hash'] = codec.digest({key: item for key, item in forged.items() if key != 'projection_hash'})
    with pytest.raises(planning.AllocationPlanError):
        wire.check_projection(forged, original, plan=plan, batch_ordinal=0)
    changed = copy.deepcopy(bound['document'])
    changed['businesses'][0]['boundary'] += '\n人工新保存的边界'
    with pytest.raises(planning.AllocationPlanError, match='stale_draft'):
        wire.construct_apply_document(projected, original, plan=plan, batch_ordinal=0,
            document=changed, draft_ref={**bound['draft_ref'], 'version': 4,
                'document_hash': codec.digest(changed)}, selected_decision_keys=['p000001'])
    for selection in ([], ['p000001', 'p000001'], ['p000007'], [True]):
        with pytest.raises(planning.AllocationPlanError):
            wire.construct_apply_document(projected, original, plan=plan, batch_ordinal=0,
                document=bound['document'], draft_ref=bound['draft_ref'], selected_decision_keys=selection)


def test_old_full_and_partial_families_are_not_allocation_answers():
    candidate, _bound = scene()
    plan = explicit(inputs(), [1])
    for value in (candidate, {'schema': partial.SCHEMA, 'raw_wire': candidate}):
        with pytest.raises(planning.AllocationPlanError):
            wire.build_projection(raw(value), plan=plan, batch_ordinal=0)


def test_same_length_different_batch_and_independent_source_do_not_share_a_projection():
    bound = inputs()
    first, second = explicit(bound, [1]), explicit(bound, [6])
    value = answer(first)
    projected = wire.build_projection(raw(value), plan=first, batch_ordinal=0)
    with pytest.raises(planning.AllocationPlanError):
        wire.check_projection(projected, raw(value), plan=second, batch_ordinal=0)
    changed = {**bound, 'source_snapshot_hash': 'f' * 64}
    changed['indexed'] = codec.build_indexed_inputs(changed['input_claims'], changed['source_snapshot_hash'])
    other = explicit(changed, [1])
    with pytest.raises(planning.AllocationPlanError):
        wire.check_projection(projected, raw(value), plan=other, batch_ordinal=0)


