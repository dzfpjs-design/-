"""Synthetic human-value protocol tests; no DB, provider or user-flow claim."""
import copy
import socket
import subprocess

import pytest

from generic_agents import report_evidence_manual_v5 as manual
from generic_agents import report_evidence_wire_v3 as wire
from generic_agents.contracts import digest
from tests.test_report_evidence_wire_v3_pure import reduce_case, identity


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Human adapter cannot do I/O')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    raw, args = reduce_case()
    model = wire.build_reduce_projection(raw, **args)
    value = manual.manual_from_projection(model['result'], '私人核对，尚未批准。')
    scope = {'schema': manual.CONTEXT, 'actor_id': identity(1), 'workspace_id': identity(2),
        'draft_id': identity(3), 'request_id': identity(4), 'accepted_revision': 3,
        'model_result_hash': digest(model['result']), 'source_hash': args['binding']['source_hash'],
        'catalog_hash': args['binding']['catalog_hash']}
    inputs = {key: args[key] for key in ('catalog', 'allowed_span_refs', 'context')}
    return raw, model, value, {**inputs, 'review_context': scope}


def test_human_value_is_exact_inverse_without_model_ids_or_provider_wire_proof():
    raw, model, value, inputs = scene(); before = copy.deepcopy((raw, model, value, inputs))
    assert value['result'] == raw['result']
    projection = manual.build_manual_projection(value, **inputs)
    assert manual.check_manual_projection(projection, value, **inputs) == projection
    assert projection['result']['schema'] == manual.MANUAL_CANDIDATE
    assert projection['result']['global_claims'][0]['key'] != model['result']['global_claims'][0]['key']
    assert not {'binding', 'wire_sha256', 'receipt', 'generation_hash'} & set(projection)
    assert (raw, model, value, inputs) == before


@pytest.mark.parametrize('field', ['actor_id', 'workspace_id', 'draft_id', 'request_id', 'accepted_revision', 'model_result_hash'])
def test_saved_human_projection_cannot_be_moved_or_self_resigned_to_another_revision(field):
    _, _, value, inputs = scene(); projection = manual.build_manual_projection(value, **inputs)
    changed = copy.deepcopy(inputs)
    changed['review_context'][field] = 4 if field == 'accepted_revision' else 'f' * 64 if field == 'model_result_hash' else identity(90)
    with pytest.raises(ValueError): manual.check_manual_projection(projection, value, **changed)
    rehashed = copy.deepcopy(projection); rehashed['result']['global_claims'][0]['key'] = 'invented'
    rehashed['projection_hash'] = digest({k: v for k, v in rehashed.items() if k != 'projection_hash'})
    with pytest.raises(ValueError): manual.check_manual_projection(rehashed, value, **inputs)


@pytest.mark.parametrize('bad', [False, -1, 1.0, '0', 99])
def test_human_edges_are_explicit_strict_business_local_indices(bad):
    _, _, value, inputs = scene(); value['result']['businesses'][0]['steps'][0]['next_index'] = bad
    with pytest.raises(ValueError): manual.build_manual_projection(value, **inputs)


@pytest.mark.parametrize('mode', ['extra_wrapper', 'extra_key', 'missing_edge', 'self_edge', 'source', 'note', 'raw_proof'])
def test_manual_protocol_never_repairs_invalid_keys_missing_edges_or_source(mode):
    _, _, value, inputs = scene()
    if mode == 'extra_wrapper': value['can_apply'] = True
    elif mode == 'extra_key': value['result']['global_claims'][0]['key'] = 'user_invented'
    elif mode == 'missing_edge': value['result']['businesses'][0]['steps'][0].pop('next_index')
    elif mode == 'self_edge': value['result']['businesses'][0]['steps'][0]['next_index'] = 0
    elif mode == 'source': inputs['review_context']['source_hash'] = (
        'f' if inputs['review_context']['source_hash'][0] != 'f' else 'a') * 64
    elif mode == 'note': value['note'] = 'x' * 12001
    elif mode == 'raw_proof': value['wire_sha256'] = 'a' * 64
    with pytest.raises(ValueError): manual.build_manual_projection(value, **inputs)


@pytest.mark.parametrize('mode', ['missing_step', 'missing_person', 'duplicate_key', 'unknown_schema'])
def test_inverse_does_not_turn_missing_or_ambiguous_targets_into_end(mode):
    _, model, _, _ = scene(); result = copy.deepcopy(model['result']); business = result['businesses'][0]
    if mode == 'missing_step': business['steps'][0]['next_key'] = 'missing'
    elif mode == 'missing_person': business['interactions'][0]['from_participant'] = 'missing'
    elif mode == 'duplicate_key': business['steps'][1]['key'] = business['steps'][0]['key']
    else: result['schema'] = 'report-evidence-candidate-v4'
    with pytest.raises(ValueError): manual.manual_from_projection(result)


def test_human_edits_get_distinct_ids_without_mutating_original_or_dropping_cycles():
    _, model, value, inputs = scene(); original = copy.deepcopy(model)
    business = value['result']['businesses'][0]
    business['steps'][0]['next_index'] = 1
    business['steps'][1]['next_index'] = 0
    business['steps'][0]['branch_condition'] = ''
    business['steps'][0]['else_index'] = None
    first = manual.build_manual_projection(value, **inputs)
    steps = first['result']['businesses'][0]['steps']
    assert steps[0]['next_key'] == steps[1]['key'] and steps[1]['next_key'] == steps[0]['key']
    later = copy.deepcopy(value); later['note'] += '补充人工说明。'
    second = manual.build_manual_projection(later, **inputs)
    assert second['result']['businesses'][0]['steps'][0]['key'] != steps[0]['key']
    assert model == original
