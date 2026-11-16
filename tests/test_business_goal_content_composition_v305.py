"""Pure composition parity, not source, IAM, lease, device or cloud proof.

The legacy wrapper uses an explicit _facts IO spy. The actual pure helper,
old DecisionInput checker and new no-activation CloudDecisionInput checks run.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import business_goal_ready_frame_v288 as f
from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents.contracts import digest


def uid(number): return str(UUID(int=number))


def scene():
    at = '2026-11-02T12:00:00+00:00'; entity = uid(1)
    report = {'status': 'sufficient', 'result': {'status': 'sufficient',
        'evidence_requirement': {'source': 'observations'}, 'resolved_time': {'mode': 'latest',
            'start': '2026-11-02T11:00:00+00:00', 'end': at}}}
    field = {'entity_id': entity, 'field': 'temperature', 'value_type': 'number', 'unit': 'Cel'}
    observations = [{'source_ref': 'observation_1', 'kind': 'observation', 'source_id': uid(2),
        'content_hash': 'a' * 64, 'text': 'Original temperature 27 Cel.', 'readability': 'readable'}]
    fact = {'fact_ref': 'fact_1', 'kind': 'observation', 'entity_id': entity, 'field': 'temperature',
        'unit': 'Cel', 'semantics': 'instantaneous', 'time_scope': {'start': at, 'end': at}, 'value': 27,
        'source_refs': [{'source_ref': observations[0]['source_ref'], 'quote_start': 0,
            'quote_end': len(observations[0]['text']), 'quote': observations[0]['text']}]}
    pins, specs = [], []
    for number, delivery in enumerate(('requested_observation', 'output'), 1):
        pointer = '/requested_observation/temperature' if number == 1 else '/temperature'
        selected = {'capability_ref': 'cap_' + str(number), 'capability_id': uid(10 + number),
            'capability_hash': str(number) * 64, 'release_version': '1.0.0', 'delivery': delivery,
            'ports': [{'field': 'temperature', 'pointer': pointer}]}
        pin = {**copy.deepcopy(selected), 'action_kind': 'information_acquisition' if number == 1 else 'analysis',
            'ports': [{'port_ref': 'port_1', 'field': 'temperature', 'pointer': pointer, 'value_type': 'number', 'unit': 'Cel'}],
            'data': {'kind': 'tool', 'allowed_agent_roles': ['business'], 'input_schema': {'type': 'object'},
                'output_schema': {'type': 'object', 'properties': {'temperature': {'type': 'number'}}},
                'owner': 'must remain local', 'execution': {'adapter': 'workflow_service' if number == 1 else 'local_analysis',
                    'operation': 'resense_from_context' if number == 1 else 'summarize', 'side_effect': number == 1,
                    'endpoint': 'https://must-stay-local.invalid', 'credential': 'private-config-never-copied'}}}
        pins.append(pin)
        specs.append({'candidate_ref': 'candidate_' + str(number), 'proposal': 'Check original capability ' + str(number),
            'claims': [{'claim_ref': 'claim_' + str(number), 'statement': 'Proposal to be reviewed.'}],
            'capability_pins': [selected]})
    data_source = {'source_ref': 'data_packet', 'kind': 'packet', 'source_id': uid(20),
        'content_hash': 'b' * 64, 'text': 'Original saved evaluation text.', 'readability': 'readable'}
    document_source = {'source_ref': 'document_packet', 'kind': 'packet', 'source_id': uid(21),
        'content_hash': 'c' * 64, 'text': 'Original approved excerpt.', 'readability': 'partial'}
    return NS(report=report, fields=[field], entities=[{'id': entity}], candidate_specs=specs,
        capability_pins={'capabilities': pins, 'policies': []}, data_source=data_source,
        document_source=document_source, facts=[fact], observation_sources=observations, conflicts=[])


def arguments(value):
    return {key: getattr(value, key) for key in ('report', 'fields', 'entities', 'candidate_specs',
        'capability_pins', 'data_source', 'document_source', 'facts', 'observation_sources', 'conflicts')}


def cloud(value, content):
    result = c.CloudDecisionInput.model_validate({'schema': c.CLOUD_INPUT,
        'decision_intent': 'information_acquisition', 'scope': {'goal_snapshot_hash': 'd' * 64,
            'entity_ids': [item['id'] for item in value.entities]}, **content}).model_dump(mode='json')
    c._check_decision(result)
    return result


def test_legacy_wrapper_keeps_actual_scope_and_checked_output_equal_to_pure_content(monkeypatch):
    x = scene(); db, p = object(), object(); task = NS(id=uid(30)); called = []
    original = {'data': {'evaluation_record': {'evaluation_result': x.report}, 'packet_id': x.data_source['source_id'],
        'sha256': x.data_source['content_hash'], 'text': x.data_source['text']},
        'documents': {'packet_id': x.document_source['source_id'], 'sha256': x.document_source['content_hash'],
            'text': x.document_source['text'], 'entries': [{'partial': True}]}}
    pins = {'fields': x.fields, 'entities': x.entities, **x.capability_pins,
        'scope': {'activation_id': 'actual-original-graph-activation'}, 'goal_snapshot_hash': 'd' * 64}
    inputs = {'candidate_specs': x.candidate_specs, 'decision_intent': 'information_acquisition'}
    def actual_facts(actual_db, actual_p, actual_sources, fields):
        assert actual_db is db and actual_p is p and actual_sources is original and fields is x.fields
        called.append('original_facts_IO'); return copy.deepcopy((x.facts, x.observation_sources, x.conflicts))
    monkeypatch.setattr(f, '_facts', actual_facts)
    content = f.compose_content(**arguments(x))
    wrapped = f._compose(db, p, task, inputs, original, pins)
    assert called == ['original_facts_IO'] and {key: wrapped[key] for key in content} == content
    assert wrapped['scope'] == {'task_id': task.id, 'activation_id': 'actual-original-graph-activation',
        'goal_snapshot_hash': 'd' * 64, 'entity_ids': [uid(1)]}
    assert wrapped['schema'] == f.decision.INPUT
    assert wrapped['requirements'] == [{'requirement_ref': 'requirement_1', 'entity_id': uid(1),
        'field': 'temperature', 'value_type': 'number', 'unit': 'Cel', 'semantics': 'instantaneous',
        'time_scope': {'start': '2026-11-02T11:00:00+00:00', 'end': '2026-11-02T12:00:00+00:00'},
        'time_mode': 'bounded', 'required_kind': 'observation'}]
    assert [item['capability_refs'] for item in wrapped['candidates']] == [['cap_1'], ['cap_2']]
    assert [item['requirement_mappings'] for item in wrapped['candidates']] == [
        [{'requirement_ref': 'requirement_1', 'capability_ref': 'cap_1', 'delivery': 'requested_observation', 'port_ref': 'port_1'}],
        [{'requirement_ref': 'requirement_1', 'capability_ref': 'cap_2', 'delivery': 'output', 'port_ref': 'port_1'}]]


def test_pure_content_has_no_scope_or_grant_and_can_use_real_new_evidence_without_a_fake_packet(monkeypatch):
    x = scene(); x.document_source.update(kind='evidence', source_id=uid(99), source_ref='document_evidence')
    monkeypatch.setattr(f, '_facts', lambda *args: pytest.fail('Pure composition performed source IO'))
    monkeypatch.setattr(f, '_identity', lambda *args, **kwargs: pytest.fail('Pure composition tried an activation'))
    content = f.compose_content(**arguments(x))
    assert set(content) == {'requirements', 'sources', 'capabilities', 'candidates', 'facts', 'known_conflicts'}
    result = cloud(x, content)
    assert result['scope'] == {'goal_snapshot_hash': 'd' * 64, 'entity_ids': [uid(1)]}
    assert content['sources'][1] == x.document_source and content['sources'][1]['source_id'] == uid(99)
    serialized = json.dumps(result)
    assert not any(word in serialized for word in ('activation_id', 'permissions_verified', 'can_dispatch',
        'must-stay-local', 'private-config-never-copied', 'must remain local'))
    assert content['facts'][0] == x.facts[0]


def test_all_content_is_detached_and_callers_facts_candidates_ports_quotes_and_conflicts_are_unchanged():
    x = scene(); second = copy.deepcopy(x.facts[0]); second.update(fact_ref='fact_2', value=28)
    x.facts.append(second); x.conflicts.append({'conflict_ref': 'conflict_1', 'fact_refs': ['fact_1', 'fact_2'],
        'reason': 'Same original instant and unit, conflicting values.'})
    before = copy.deepcopy(arguments(x)); content = f.compose_content(**arguments(x)); cloud(x, content)
    assert arguments(x) == before and content['known_conflicts'] == x.conflicts
    content['facts'][0]['source_refs'][0]['quote'] = 'changed return value'
    content['sources'][0]['text'] = 'changed return source'
    content['capabilities'][0]['information_targets'][0]['unit'] = 'K'
    content['candidates'][0]['claims'][0]['statement'] = 'changed return candidate'
    content['known_conflicts'][0]['fact_refs'].append('changed_ref')
    assert arguments(x) == before
    assert f.compose_content(**arguments(x))['known_conflicts'] == before['conflicts']


@pytest.mark.parametrize('envelope_status,gap_fields,expected', [
    ('sufficient', [], True), ('insufficient', [], False), ('insufficient', ['other'], False),
    ('insufficient', ['temperature'], True)])
def test_requested_observation_keeps_the_original_sufficient_or_actual_gap_mapping_rule(envelope_status, gap_fields, expected):
    x = scene(); x.report['status'] = envelope_status
    if gap_fields: x.capability_pins['policies'] = [{'context': {'gap': {'entity_id': uid(1), 'fields': gap_fields}}}]
    content = f.compose_content(**arguments(x)); cloud(x, content)
    assert bool(content['candidates'][0]['requirement_mappings']) is expected
    assert content['candidates'][1]['requirement_mappings']  # Actual output mapping does not depend on a gap.


@pytest.mark.parametrize('field,value', [('unit', 'K'), ('field', 'unrelated'), ('value_type', 'string')])
def test_candidate_ports_never_claim_coverage_for_a_different_unit_field_or_scalar_type(field, value):
    x = scene(); x.capability_pins['capabilities'][0]['ports'][0][field] = value
    content = f.compose_content(**arguments(x)); cloud(x, content)
    assert content['candidates'][0]['requirement_mappings'] == []
    assert content['requirements'][0]['unit'] == 'Cel' and content['facts'][0]['unit'] == 'Cel'


@pytest.mark.parametrize('requested', ['archive', 'either'])
def test_archive_requirements_preserve_statement_semantics_without_an_invented_clock(requested):
    x = scene(); x.report['result']['resolved_time'] = {'mode': 'archive'}
    x.report['result']['evidence_requirement']['source'] = requested
    content = f.compose_content(**arguments(x)); cloud(x, content)
    assert content['requirements'][0]['semantics'] == 'statement'
    assert content['requirements'][0]['time_mode'] == 'not_required'
    assert content['requirements'][0]['time_scope'] == {'start': None, 'end': None}
    assert content['requirements'][0]['required_kind'] == ('document_statement' if requested == 'archive' else 'observed_or_recorded')


def test_archive_mode_does_not_satisfy_an_observation_only_requirement():
    x = scene(); x.report['result']['resolved_time'] = {'mode': 'archive'}
    with pytest.raises(HTTPException): f.compose_content(**arguments(x))


@pytest.mark.parametrize('fault', ['duplicate_candidate', 'invalid_quote', 'invalid_conflict'])
def test_new_scope_keeps_original_candidate_reference_quote_and_conflict_validation(fault):
    x = scene()
    if fault == 'duplicate_candidate': x.candidate_specs[1]['candidate_ref'] = x.candidate_specs[0]['candidate_ref']
    elif fault == 'invalid_quote': x.facts[0]['source_refs'][0]['quote'] = 'not the original selected text'
    else: x.conflicts = [{'conflict_ref': 'conflict_1', 'fact_refs': ['fact_1', 'unknown'], 'reason': 'invalid refs'}]
    with pytest.raises((HTTPException, ValueError)):
        cloud(x, f.compose_content(**arguments(x)))


@pytest.mark.parametrize('method', [f.build_frame, f.checked_frame])
def test_fixed_old_adapter_gate_is_not_widened_for_the_pure_helper(method):
    kwargs = {'_server_protocol': c.ADAPTER}
    args = (None, None, None, None, None, {}) + (({},) if method is f.checked_frame else ())
    with pytest.raises(HTTPException): method(*args, **kwargs)
