"""Unintegrated synthetic protocol evidence only; root executes all tests."""
import copy
import hashlib
import json
import socket
import subprocess
from uuid import UUID

import pytest

from generic_agents import report_evidence_wire_v3 as adapter
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import MapResult, EvidenceClaim
from generic_agents.report_evidence_semantics import FullResultV4
from tests.test_report_evidence_semantics_pure import catalog, example, CONTEXT


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure adapter cannot connect or launch')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def identity(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def bound(c, stage, call):
    return {'schema': 'report-evidence-journal-binding-v1', 'actor_id': identity(1),
        'workspace_id': identity(2), 'draft_id': identity(3), 'stage': stage,
        'reserved_call_id': identity(call), 'source_hash': c['descriptor']['source_hash'],
        'catalog_hash': c['descriptor']['catalog_hash'], 'generation_hash': ('d' if stage == 'reduce' else 'c') * 64,
        'dispatch_hash': 'e' * 64, 'messages_hash': hashlib.sha256(stage.encode()).hexdigest()}


def byte_hash(value):
    # A distinct verified-byte fixture; deliberately not canonical JSON digest.
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, indent=2).encode()).hexdigest()


def map_case(c=None, stage='map-1', ref='w:p1/s1', call=10):
    c = catalog() if c is None else c
    wire = {'schema': adapter.MAP_WIRE, 'claims': [{'kind': '职责', 'statement': ' 合成责任＋+。 ',
        'span_refs': [ref], 'status': 'explicit', 'explanation': ''}]}
    binding = bound(c, stage, call)
    args = {'binding': binding, 'expected_binding': copy.deepcopy(binding), 'wire_sha256': byte_hash(wire),
        'catalog': c, 'allowed_span_refs': [ref]}
    return wire, args


def candidate_wire(c):
    value = example(c); value['schema'] = adapter.CANDIDATE_WIRE
    for claim in value['global_claims']: claim.pop('key')
    for business in value['businesses']:
        steps = {step['key']: i for i, step in enumerate(business['steps'])}
        people = {person['key']: i for i, person in enumerate(business['participants'])}
        for step in business['steps']:
            step.pop('key')
            for old, new in [('next_key', 'next_index'), ('else_key', 'else_index')]:
                ref = step.pop(old); step[new] = None if ref == 'end' else steps[ref]
        for person in business['participants']: person.pop('key')
        for collection in ('interactions', 'conditions', 'coordination'):
            for item in business[collection]:
                item.pop('key'); item['step_indices'] = [steps[ref] for ref in item.pop('step_keys')]
                if collection == 'interactions':
                    item['from_participant_index'] = people[item.pop('from_participant')]
                    item['to_participant_index'] = people[item.pop('to_participant')]
    return value


def reduce_case():
    c = catalog(); maps = []
    for i, ref in enumerate(['w:p1/s1', 'w:p2/s1', 'o:p1/s1'], 1):
        wire, args = map_case(c, f'map-{i}', ref, 10 + i)
        maps.append({'wire': wire, **{key: copy.deepcopy(value) for key, value in args.items() if key != 'catalog'},
            'projection': adapter.build_map_projection(wire, **args)})
    wire = {'result': candidate_wire(c), 'reconciliation': [
        {'input_position': i, 'disposition': 'kept', 'output_kind': kind, 'output_index': index,
            'reason': '合成输入保留完整原引用与不确定性。'}
        for i, kind, index in [(0, 'objects', 0), (1, 'objects', 1), (2, 'global_claims', 0)]]}
    binding = bound(c, 'reduce', 99)
    return wire, {'binding': binding, 'expected_binding': copy.deepcopy(binding),
        'wire_sha256': byte_hash(wire), 'catalog': c, 'allowed_span_refs': ['w:p1/s1', 'w:p2/s1', 'o:p1/s1'],
        'context': copy.deepcopy(CONTEXT), 'map_inputs': maps}


def test_actual_wire_json_schemas_have_no_model_generated_technical_ids_and_require_explicit_nullable_edges():
    forbidden = {'key', 'next_key', 'else_key', 'from_participant', 'to_participant', 'step_keys', 'input_id'}
    def visit(node):
        if type(node) is dict:
            assert not set(node.get('properties', {})) & forbidden
            for value in node.values(): visit(value)
        elif type(node) is list:
            for value in node: visit(value)
    visit(adapter.MapResultWireV3.model_json_schema()); schema = adapter.ReduceResultWireV5.model_json_schema(); visit(schema)
    step = schema['$defs']['StepWireV5']
    assert {'next_index', 'else_index'} <= set(step['required'])
    for field in ('next_index', 'else_index'):
        assert {'type': 'null'} in step['properties'][field]['anyOf'] and 'default' not in step['properties'][field]


def test_map_cold_rebuild_preserves_original_five_fields_and_distinguishes_byte_hash_from_value_hash():
    wire, args = map_case(); wire['claims'].append(copy.deepcopy(wire['claims'][0])); args['wire_sha256'] = byte_hash(wire)
    before = copy.deepcopy([wire, args]); projection = adapter.build_map_projection(wire, **args)
    assert adapter.check_map_projection(copy.deepcopy(projection), copy.deepcopy(wire), **copy.deepcopy(args)) == projection
    assert projection['wire_value_hash'] == digest(wire) != projection['wire_sha256']
    assert projection['claims'][0]['key'] != projection['claims'][1]['key']
    for original, claim in zip(wire['claims'], projection['claims']):
        assert {key: value for key, value in claim.items() if key != 'key'} == original
        assert len(claim['key']) == 35
    assert [wire, args] == before


def test_complete_reduce_has_all_platform_ids_exact_local_edges_and_original_reconciliation():
    wire, args = reduce_case(); before = copy.deepcopy([wire, args])
    projection = adapter.build_reduce_projection(wire, **args)
    assert adapter.check_reduce_projection(projection, wire, **args) == projection
    result = projection['result']; business = result['businesses'][0]; steps = business['steps']; people = business['participants']
    assert result['schema'] == adapter.CANDIDATE_PROJECTION and wire['result']['schema'] == adapter.CANDIDATE_WIRE
    assert steps[0]['next_key'] == steps[1]['key'] and steps[1]['next_key'] == 'end'
    assert business['interactions'][0]['from_participant'] == people[0]['key']
    assert business['interactions'][0]['to_participant'] == people[1]['key']
    for collection in ('conditions', 'coordination', 'interactions'):
        assert business[collection][0]['step_keys'] == [steps[i]['key'] for i in wire['result']['businesses'][0][collection][0]['step_indices']]
    assert projection['input_order'] == [f'map-{i}/claims/0' for i in (1, 2, 3)]
    assert [item['input_id'] for item in projection['reconciliation']] == projection['input_order']
    assert [wire, args] == before


@pytest.mark.parametrize('field', ['actor_id', 'workspace_id', 'draft_id', 'reserved_call_id', 'source_hash',
    'catalog_hash', 'generation_hash', 'dispatch_hash', 'messages_hash'])
def test_actual_binding_change_cannot_pass_unchanged_frozen_expectation(field):
    wire, args = map_case(); args['binding'][field] = identity(88) if field.endswith('_id') else 'f' * 64
    with pytest.raises(ValueError): adapter.build_map_projection(wire, **args)


@pytest.mark.parametrize('change', ['id', 'wire_hash', 'version', 'extra'])
def test_public_rehash_of_a_changed_projection_cannot_replace_cold_original_proof(change):
    wire, args = map_case(); projection = adapter.build_map_projection(wire, **args)
    if change == 'id': projection['claims'][0]['key'] = 'self_created'
    elif change == 'wire_hash': projection['wire_sha256'] = 'f' * 64
    elif change == 'version': projection['derivation_version'] = 'unknown'
    else: projection['extra'] = True
    projection['projection_hash'] = digest({key: value for key, value in projection.items() if key != 'projection_hash'})
    with pytest.raises(ValueError): adapter.check_map_projection(projection, wire, **args)


@pytest.mark.parametrize('mode', ['extra_key', 'kind', 'status', 'outside_batch', 'duplicate_refs', 'claims_capacity', 'byte_capacity'])
def test_map_wire_is_strict_and_never_repairs_ids_enums_refs_or_capacity(mode):
    wire, args = map_case(); claim = wire['claims'][0]
    if mode == 'extra_key': claim['key'] = 'ignored'
    elif mode == 'kind': claim['kind'] = '流程'
    elif mode == 'status': claim['status'] = 'confirmed'
    elif mode == 'outside_batch': claim['span_refs'] = ['o:p1/s1']
    elif mode == 'duplicate_refs': claim['span_refs'] *= 2
    elif mode == 'claims_capacity': wire['claims'] *= 81
    else: wire['claims'] = [{**copy.deepcopy(claim), 'statement': '甲' * 2000} for _ in range(80)]
    before = copy.deepcopy(wire)
    with pytest.raises(ValueError): adapter.build_map_projection(wire, **args)
    assert wire == before


@pytest.mark.parametrize('value', [True, False, -1, 2, 0.0, '0'])
def test_business_indices_are_strict_and_not_truthy_end_fallbacks(value):
    wire, args = reduce_case(); wire['result']['businesses'][0]['steps'][0]['next_index'] = value
    with pytest.raises(ValueError): adapter.build_reduce_projection(wire, **args)


@pytest.mark.parametrize('mode', ['self', 'missing_edge', 'extra_key', 'duplicate_step_refs', 'bad_person', 'same_person', 'unconditioned_else'])
def test_no_missing_or_invalid_relationship_is_invented_or_silently_removed(mode):
    wire, args = reduce_case(); business = wire['result']['businesses'][0]
    if mode == 'self': business['steps'][0]['next_index'] = 0
    elif mode == 'missing_edge': business['steps'][0].pop('else_index')
    elif mode == 'extra_key': business['participants'][0]['key'] = 'old_key'
    elif mode == 'duplicate_step_refs': business['coordination'][0]['step_indices'] = [0, 0]
    elif mode == 'bad_person': business['interactions'][0]['from_participant_index'] = 2
    elif mode == 'same_person': business['interactions'][0]['to_participant_index'] = 0
    else: business['steps'][0]['else_index'] = 1
    before = copy.deepcopy(wire)
    with pytest.raises(ValueError): adapter.build_reduce_projection(wire, **args)
    assert wire == before


def test_explicit_multi_node_cycle_is_preserved_and_indices_are_business_local():
    wire, args = reduce_case(); first = wire['result']['businesses'][0]
    first['steps'][1]['next_index'] = 0
    second = copy.deepcopy(first); second['name'] = '另一登记核对'; wire['result']['businesses'].append(second)
    args['wire_sha256'] = byte_hash(wire); result = adapter.build_reduce_projection(wire, **args)['result']
    a, b = result['businesses']
    for business in (a, b):
        assert business['steps'][0]['next_key'] == business['steps'][1]['key']
        assert business['steps'][1]['next_key'] == business['steps'][0]['key']
        assert business['interactions'][0]['from_participant'] == business['participants'][0]['key']
    assert a['steps'][0]['key'] != b['steps'][0]['key']


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'range', 'bool', 'output_range', 'lost_ref', 'status_upgrade'])
def test_reconciliation_and_evidence_cannot_be_dropped_retargeted_or_upgraded(mode):
    wire, args = reduce_case()
    if mode == 'missing': wire['reconciliation'].pop()
    elif mode == 'duplicate': wire['reconciliation'][1]['input_position'] = 0
    elif mode == 'range': wire['reconciliation'][0]['input_position'] = 3
    elif mode == 'bool': wire['reconciliation'][0]['input_position'] = False
    elif mode == 'output_range': wire['reconciliation'][0]['output_index'] = 99
    elif mode == 'lost_ref': wire['reconciliation'][0].update(output_kind='global_claims', output_index=0)
    else:
        item = args['map_inputs'][2]; item['wire']['claims'][0].update(status='inferred', explanation='仍是推断。')
        item['wire_sha256'] = byte_hash(item['wire'])
        item['projection'] = adapter.build_map_projection(item['wire'], catalog=args['catalog'],
            **{key: value for key, value in item.items() if key not in {'projection', 'wire'}})
    with pytest.raises(ValueError): adapter.build_reduce_projection(wire, **args)


@pytest.mark.parametrize('mode', ['map_order', 'map_allowlist', 'reduce_extra_ref', 'mixed_generation', 'same_call', 'map_capacity', 'global_capacity', 'reconcile_capacity'])
def test_frozen_per_batch_and_reduce_scope_proofs_and_capacities_are_not_interchangeable(mode):
    wire, args = reduce_case()
    if mode == 'map_order': args['map_inputs'].reverse()
    elif mode == 'map_allowlist': args['map_inputs'][0]['allowed_span_refs'] = args['allowed_span_refs']
    elif mode == 'reduce_extra_ref': args['allowed_span_refs'].append('w:p3/s1')
    elif mode == 'mixed_generation':
        item = args['map_inputs'][1]
        for key in ('binding', 'expected_binding'): item[key]['generation_hash'] = 'a' * 64
        item['projection'] = adapter.build_map_projection(item['wire'], catalog=args['catalog'],
            **{key: value for key, value in item.items() if key not in {'projection', 'wire'}})
    elif mode == 'same_call': args['binding']['reserved_call_id'] = args['expected_binding']['reserved_call_id'] = args['map_inputs'][0]['binding']['reserved_call_id']
    elif mode == 'map_capacity': args['map_inputs'] *= 9
    elif mode == 'global_capacity': wire['result']['global_claims'] *= 161
    else: wire['reconciliation'] *= 641
    with pytest.raises(ValueError): adapter.build_reduce_projection(wire, **args)


def test_reduce_reordering_or_self_signed_changed_id_is_detected_by_full_cold_rebuild():
    wire, args = reduce_case(); projection = adapter.build_reduce_projection(wire, **args)
    changed = copy.deepcopy(projection); changed['result']['businesses'][0]['steps'][0]['key'] = 'self_created'
    changed['projection_hash'] = digest({key: value for key, value in changed.items() if key != 'projection_hash'})
    with pytest.raises(ValueError): adapter.check_reduce_projection(changed, wire, **args)
    changed_wire = copy.deepcopy(wire); changed_wire['result']['businesses'][0]['steps'].reverse()
    with pytest.raises(ValueError): adapter.check_reduce_projection(projection, changed_wire, **args)
    changed_context = copy.deepcopy(args); changed_context['context']['businesses'] = ['无关上下文变化']
    with pytest.raises(ValueError): adapter.check_reduce_projection(projection, wire, **changed_context)


def test_old_v1_v2_map_and_v4_global_key_contracts_remain_required_and_limited():
    c = catalog(); original = example(c); bad = copy.deepcopy(original)
    assert FullResultV4.model_validate(original).model_dump() == original
    bad['global_claims'][0]['key'] = 'x' * 44
    with pytest.raises(ValueError): FullResultV4.model_validate(bad)
    claim = copy.deepcopy(original['global_claims'][0]); claim.pop('key')
    with pytest.raises(ValueError): EvidenceClaim.model_validate(claim)
    with pytest.raises(ValueError): MapResult.model_validate({'schema': adapter.MAP_WIRE, 'claims': [claim]})
