"""Explicit read-boundary spies; no real SQL, IAM, model, device or E2E claim.

Source inventory, graph reads and publication are separately declared boundaries.
Projection, schema pointers, scalar matching, conflicts and historical equality
are real. Reception functions are spies representing an independently verified
receipt, never a substitute production receiver.
"""
import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import business_decision_frame_v276 as subject
from generic_agents import instance_data_receipts as receipts
from generic_agents import observation_integrity as integrity
from generic_agents import state_receipt_guard_v245 as reception
from generic_agents.contracts import digest
from generic_agents.models import Capability, MonitorPolicy, Observation


def ident(index):
    return str(UUID(int=index))


class ReadDB:
    @property
    def no_autoflush(self):
        return nullcontext()

    def __getattr__(self, name):
        raise AssertionError('Unexpected IO: ' + name)


@pytest.fixture
def scene(monkeypatch):
    real_capabilities = subject._capability_pins
    task = NS(id=ident(1), data={'entity_ids': [ident(2)]})
    p = NS(user_id=ident(3), workspace_id=ident(4), role='viewer')
    job = NS(id=ident(5)); cap = NS(id=ident(6)); release = NS(id=ident(7))
    field = {'entity_id': ident(2), 'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel',
        'schema': {'type': 'number', 'unit': 'Cel'}}
    at = '2026-11-01T12:00:00+00:00'
    receipt = {'data_reception': {'receipt_hash': 'a' * 64}}
    record = {'observation_id': ident(10), 'entity_id': ident(2), 'version': 1, 'source_id': ident(11),
        'attributes': {'temperature_c': 27}, 'observed_at': at}
    report = {'review': 'unreviewed', 'attributes': {'temperature_c': 27}, 'field_units': {'temperature_c': 'Cel'},
        'extracted': {'attributes': {'temperature_c': 27}, 'field_units': {'temperature_c': 'Cel'}}}
    observation = {'id': ident(10), 'version': 1, 'source_id': ident(11), 'entity_id': ident(2),
        'data': {'extracted': copy.deepcopy(report['extracted'])}, 'observed_at': at}
    proof = {'kind': 'observation', 'record': record, 'observation': observation, 'report': report,
        'reception': receipt, 'origin': {'field_units': {'temperature_c': 'Cel'}}}
    query_report = {'evidence': [], 'observation_evidence': [record], 'conflicts': [],
        'evidence_requirement': {'source': 'observations'},
        'resolved_time': {'mode': 'latest', 'start': '2026-11-01T11:50:00+00:00', 'end': at}}
    original_sources = {'schema': 'business-decision-sources-v276', 'task_id': task.id, 'release_id': release.id,
        'package_id': ident(12), 'package_hash': 'b' * 64,
        'data': {'packet_id': ident(13), 'evidence_id': ident(14), 'sha256': 'c' * 64,
            'provenance': {'query': {'data': {'result': {'status': 'sufficient', 'result': query_report}}}, 'sources': [proof]},
            'context_result': {'candidates': []}},
        'documents': {'packet_id': ident(15), 'evidence_id': ident(16), 'sha256': 'd' * 64,
            'entries': [{'partial': False}], 'text': 'Original selected planning text, not a measurement.'},
        'evidence_ids': [ident(14), ident(16)]}
    original_sources['sources_hash'] = digest(original_sources)
    selected = {'capability_ref': 'sampling', 'capability_id': ident(20), 'capability_hash': 'e' * 64,
        'release_version': '2.0.0', 'delivery': 'requested_observation',
        'ports': [{'field': 'temperature_c', 'pointer': '/requested_observation/temperature_c'}]}
    inputs = {'candidate_specs': [{'candidate_ref': 'candidate_sampling', 'proposal': 'Request a verified sample.',
        'claims': [], 'capability_pins': [selected]}], 'decision_intent': 'information_acquisition'}
    capability = {**selected, 'data': {'kind': 'tool', 'allowed_agent_roles': ['business'],
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
        'owner': 'must remain local', 'execution': {'adapter': 'workflow_service', 'operation': 'resense_from_context',
            'side_effect': True, 'endpoint': 'https://private.invalid', 'credential': 'must remain local'}},
        'publication': {'id': ident(30)}, 'action_kind': 'information_acquisition',
        'ports': [{'port_ref': 'port_1', **selected['ports'][0], 'value_type': 'number', 'unit': 'Cel'}]}
    identity = {'task_id': task.id, 'job_id': job.id, 'activation_id': 'decision', 'actor_id': ident(31)}
    field_pins = {'package': {'id': ident(12), 'hash': 'b' * 64, 'data': {'dependencies': [ident(20)]}},
        'release': {'id': release.id}, 'fields': [field], 'entities': [{'id': ident(2)}],
        'object_snapshots_hash': 'f' * 64, 'goal_snapshot_hash': '1' * 64}
    policies = [{'context_hash': '2' * 64, 'context': {'gap': {'entity_id': ident(2), 'fields': ['temperature_c']}}}]
    observations = {ident(10): NS(**copy.deepcopy(observation))}
    monkeypatch.setattr(subject.sources, '_reader_iam', lambda *_args: None)
    monkeypatch.setattr(subject.sources, 'resolve', lambda *_args: copy.deepcopy(original_sources))
    def checked_sources(_db, _p, _task, value):
        assert value == original_sources
        return copy.deepcopy(value)
    monkeypatch.setattr(subject.sources, 'checked_frozen', checked_sources)
    monkeypatch.setattr(subject, '_identity', lambda *_args, **_kwargs: (task, release, {}, copy.deepcopy(identity)))
    monkeypatch.setattr(subject, '_field_pins', lambda *_args, **_kwargs: copy.deepcopy(field_pins))
    monkeypatch.setattr(subject, '_capability_pins', lambda *_args, **_kwargs: ([copy.deepcopy(capability)], copy.deepcopy(policies)))
    def fresh(_db, _p, model, identifier):
        assert model is Observation
        return observations[identifier]
    monkeypatch.setattr(subject.sources, 'fresh', fresh)
    monkeypatch.setattr(reception, 'check', lambda *_args: {'status': 'received', 'receipt_hash': 'a' * 64})
    monkeypatch.setattr(receipts, 'observation_receipt', lambda *_args: copy.deepcopy(receipt))
    monkeypatch.setattr(integrity, 'checked_report', lambda _db, _p, obs: next(
        item['report'] for item in original_sources['data']['provenance']['sources'] if item['observation']['id'] == obs.id))
    return NS(db=ReadDB(), p=p, task=task, job=job, cap=cap, inputs=inputs, sources=original_sources,
        field_pins=field_pins, capability=capability, identity=identity, report=query_report, proof=proof,
        observations=observations, receipt=receipt, real_capabilities=real_capabilities)


def test_original_scalar_frame_and_separate_historical_reader(scene, monkeypatch):
    s = scene; before = copy.deepcopy((s.sources, s.inputs))
    value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)
    assert set(value) == {'decision_input', 'sources', 'capability_pins'}
    assert value['decision_input']['scope']['activation_id'] == 'decision'
    assert value['decision_input']['requirements'][0]['time_scope']['end'] == '2026-11-01T12:00:00+00:00'
    assert value['decision_input']['requirements'][0]['time_mode'] == 'bounded'
    assert [fact['value'] for fact in value['decision_input']['facts'] if fact['kind'] == 'observation'] == [27]
    serialized = json.dumps(value['decision_input'])
    assert 'private.invalid' not in serialized and 'credential' not in serialized and 'owner' not in serialized
    monkeypatch.setattr(subject, 'build_frame', lambda *_args: pytest.fail('historical checking rebuilt live frame'))
    checked = subject.checked_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs, value)
    assert checked == value and s.p.user_id != s.identity['actor_id']
    assert before == (s.sources, s.inputs)


def test_complete_original_packet_text_can_be_reviewed_without_inventing_fact_sufficiency(scene):
    s = scene; text = 'Complete original selected packet bytes; absent values remain absent.'
    s.sources['data']['text'] = text
    s.report['observation_evidence'].clear(); s.sources['data']['provenance']['sources'].clear()
    value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)['decision_input']
    packet = next(row for row in value['sources'] if row['source_ref'] == 'data_packet')
    assert packet['text'] == text and packet['readability'] == 'readable'
    assert {fact['kind'] for fact in value['facts']} == {'capability_declaration'}


def test_document_request_never_becomes_a_measured_fact(scene):
    s = scene
    s.report['observation_evidence'].clear(); s.sources['data']['provenance']['sources'].clear()
    value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)['decision_input']
    assert {fact['kind'] for fact in value['facts']} == {'capability_declaration'}
    assert value['requirements'] and value['sources'][1]['source_ref'] == 'document_packet'


def test_package_unit_does_not_fill_missing_original_measurement_units(scene):
    s = scene
    s.proof['report']['field_units'] = {}
    s.proof['report']['extracted']['field_units'] = {}
    s.proof['origin']['field_units'] = {}
    s.proof['observation']['data']['extracted']['field_units'] = {}
    s.observations[ident(10)].data['extracted']['field_units'] = {}
    value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)['decision_input']
    fact = next(item for item in value['facts'] if item['kind'] == 'observation')
    assert fact['unit'] is None and value['requirements'][0]['unit'] == 'Cel'


def test_original_numeric_conflict_is_preserved_and_automatically_projected(scene):
    s = scene; other = copy.deepcopy(s.proof)
    other['observation']['id'] = other['record']['observation_id'] = ident(40)
    other['record']['attributes']['temperature_c'] = 30
    other['report']['attributes']['temperature_c'] = 30
    other['report']['extracted']['attributes']['temperature_c'] = 30
    other['observation']['data']['extracted']['attributes']['temperature_c'] = 30
    s.observations[ident(40)] = NS(**copy.deepcopy(other['observation']))
    s.sources['data']['provenance']['sources'].append(other)
    s.report['observation_evidence'].append(other['record'])
    s.report['conflicts'].append({'entity_id': ident(2), 'field': 'temperature_c',
        'observed_at': s.proof['record']['observed_at'], 'observation_ids': [ident(10), ident(40)]})
    value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)['decision_input']
    assert len(value['known_conflicts']) == 1
    assert value['known_conflicts'][0]['fact_refs'] == ['fact_1', 'fact_2']


@pytest.mark.parametrize('damage', ['unprojectable_conflict', 'unacknowledged', 'unknown_unit_conflict', 'changed_original'])
def test_missing_original_proof_is_rejected_instead_of_falling_back(scene, monkeypatch, damage):
    s = scene
    if damage == 'unacknowledged':
        monkeypatch.setattr(reception, 'check', lambda *_args: {'status': 'waiting_data'})
    elif damage == 'changed_original':
        s.observations[ident(10)].version = 2
    else:
        s.report['conflicts'] = [{'entity_id': ident(2), 'field': 'temperature_c', 'observation_ids': [ident(10), ident(99)]}]
        if damage == 'unknown_unit_conflict': s.field_pins['fields'][0]['unit'] = None
    with pytest.raises(HTTPException):
        subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)


def test_rehashed_pins_or_rewritten_decision_cannot_replace_the_original(scene):
    s = scene; value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)
    value['capability_pins']['capabilities'][0]['data']['execution']['operation'] = 'other_operation'
    pins = value['capability_pins']; pins['pins_hash'] = digest({key: item for key, item in pins.items() if key != 'pins_hash'})
    with pytest.raises(HTTPException):
        subject.checked_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs, value)


def output_schema():
    return {'type': 'object', 'required': ['result'], 'properties': {'result': {'type': 'object',
        'required': ['temperature_c'], 'properties': {'temperature_c': {'type': 'number', 'unit': 'Cel'}}}}}


@pytest.mark.parametrize('damage', ['wrong_unit', 'wrong_type', 'receipt_status', 'missing_required', 'reference', 'alias'])
def test_output_port_must_be_an_original_exact_typed_quantity(damage):
    schema = output_schema(); spec = {'field': 'temperature_c', 'pointer': '/result/temperature_c'}
    field = {'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'}
    if damage == 'wrong_unit': schema['properties']['result']['properties']['temperature_c']['unit'] = 'K'
    elif damage == 'wrong_type': schema['properties']['result']['properties']['temperature_c']['type'] = 'boolean'
    elif damage == 'receipt_status': spec['pointer'] = '/service_receipt/status'
    elif damage == 'missing_required': schema['properties']['result']['required'] = []
    elif damage == 'reference': schema['properties']['result']['properties']['temperature_c'] = {'$ref': '#/anything'}
    else: spec['field'] = 'other_quantity'
    with pytest.raises((HTTPException, ValueError)): subject._output_port(schema, spec, [field])


def test_exact_output_port_and_inline_closed_specs():
    port = subject._output_port(output_schema(), {'field': 'temperature_c', 'pointer': '/result/temperature_c'},
        [{'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'}])
    assert port['unit'] == 'Cel' and port['value_type'] == 'number'
    schema = subject.candidate_specs_schema()
    assert '$defs' not in json.dumps(schema) and '$ref' not in json.dumps(schema)
    assert schema['minItems'] == 1 and schema['maxItems'] == 16
    assert schema['items']['additionalProperties'] is False


@pytest.mark.parametrize('status,allowed', [('sufficient', True), ('insufficient', False), ('conflict', True)])
def test_no_gap_future_target_is_only_a_package_declaration(scene, monkeypatch, status, allowed):
    s = scene
    s.sources['data']['provenance']['query']['data']['result']['status'] = status
    data = {'kind': 'tool', 'allowed_agent_roles': ['business'],
        'execution': {'adapter': 'workflow_service', 'operation': subject.resense.OP, 'side_effect': True},
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}}
    cap = NS(id=ident(20), kind='tool', content_hash=digest(data), release_version='2.0.0', data=data)
    spec = copy.deepcopy(s.inputs['candidate_specs'][0]); pin = spec['capability_pins'][0]
    pin['capability_hash'] = cap.content_hash
    monkeypatch.setattr(subject.sources, 'fresh', lambda *_args: cap)
    monkeypatch.setattr(subject, '_publication', lambda *_args, **_kwargs: {'id': ident(71)})
    monkeypatch.setattr(subject.resense, 'validate_manifest', lambda *_args: None)
    monkeypatch.setattr(subject, '_policy_pin', lambda *_args, **_kwargs: pytest.fail('no-gap declaration invented policy authority'))
    def build():
        return s.real_capabilities(s.db, s.p, s.task, s.sources, [spec], s.field_pins['fields'],
            {'data': {'dependencies': [cap.id]}}, live=True)
    if not allowed:
        with pytest.raises(HTTPException): build()
    else:
        pins, policies = build()
        assert policies == [] and pins[0]['delivery'] == 'requested_observation'
        assert pins[0]['ports'][0]['value_type'] == 'number' and pins[0]['ports'][0]['unit'] == 'Cel'


@pytest.mark.parametrize('status,mapped', [('sufficient', True), ('conflict', False)])
def test_no_context_mapping_is_only_a_declaration_for_sufficient_facts(scene, monkeypatch, status, mapped):
    s = scene
    s.sources['data']['provenance']['query']['data']['result']['status'] = status
    s.sources['data']['text'] = 'Complete original selected data packet.'
    monkeypatch.setattr(subject, '_capability_pins', lambda *_args, **_kwargs: ([copy.deepcopy(s.capability)], []))
    frame = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)
    assert frame['capability_pins']['policies'] == []
    links = frame['decision_input']['candidates'][0]['requirement_mappings']
    assert bool(links) is mapped
    if mapped:
        assert links == [{'requirement_ref': 'requirement_1', 'capability_ref': 'sampling',
            'delivery': 'requested_observation', 'port_ref': 'port_1'}]
    if mapped:
        # Synthetic model review only; actual source publication/receipt IO is
        # the fixture boundary. Exercise the real complete-fact derivation.
        from generic_agents import business_decision_contracts_v274 as decision
        value = frame['decision_input']; candidate = value['candidates'][0]
        facts = [row['fact_ref'] for row in value['facts']]
        supporting_sources = {citation['source_ref'] for row in value['facts'] for citation in row['source_refs']}
        model = {'schema': decision.MODEL, 'input_hash': decision.input_hash(value),
            'summary': 'The required original observation is present; no acquisition has been executed.',
            'candidate_assessments': [{'candidate_ref': candidate['candidate_ref'],
                'candidate_applicability': 'applicable', 'applicability_reason': 'Declared future fields match.',
                'evidence_integrity': 'supported', 'evidence_reason': 'The original facts and declaration are present.',
                'capability_refs': candidate['capability_refs'],
                'requirement_refs': [row['requirement_ref'] for row in value['requirements']],
                'unmet_requirement_refs': [], 'fact_refs': facts,
                'source_refs': [row['source_ref'] for row in value['sources'] if row['source_ref'] in supporting_sources],
                'refutations': [], 'missing_information': []}],
            'source_coverage': [{'source_ref': source['source_ref'], 'status': 'reviewed',
                'fact_refs': [fact['fact_ref'] for fact in value['facts']
                    if any(c['source_ref'] == source['source_ref'] for c in fact['source_refs'])],
                'reason': 'The full selected source was reviewed.'} for source in value['sources']],
            'conflicts': [], 'knowledge_gaps': [], 'recommendation': {
                'candidate_ref': candidate['candidate_ref'], 'reason': 'A declared future option; current facts are sufficient.',
                'fact_refs': facts}}
        result = decision.derive_result(value, model)
        assert result['current_facts_sufficient'] is True and result['fact_consistency'] == 'consistent'
        assert result['review_state'] == 'ready_for_review' and result['permissions_verified'] is False
        assert result['can_dispatch'] is False and result['data_reception_verified'] is False


@pytest.mark.parametrize('side_effect,expected', [(False, 'analysis'), (True, 'operational_action')])
def test_unknown_registered_adapter_never_self_declares_information_acquisition(scene, monkeypatch, side_effect, expected):
    s = scene
    data = {'kind': 'tool', 'allowed_agent_roles': ['business'],
        'execution': {'adapter': 'installed_other_adapter', 'side_effect': side_effect},
        'input_schema': {'type': 'object'}, 'output_schema': output_schema(), 'specification': {}}
    cap = NS(id=ident(70), kind='tool', content_hash=digest(data), release_version='1.0.0', data=data)
    spec = {'candidate_ref': 'candidate_other', 'proposal': 'Use the actual declaration.', 'claims': [],
        'capability_pins': [{'capability_ref': 'actual_other', 'capability_id': cap.id,
            'capability_hash': cap.content_hash, 'release_version': cap.release_version, 'delivery': 'output', 'ports': []}]}
    monkeypatch.setattr(subject.sources, 'fresh', lambda *_args: cap)
    monkeypatch.setattr(subject, '_publication', lambda *_args, **_kwargs: {'id': ident(71)})
    pins, policies = s.real_capabilities(s.db, s.p, s.task, s.sources, [spec], s.field_pins['fields'],
        {'data': {'dependencies': [cap.id]}}, live=True)
    assert pins[0]['action_kind'] == expected and policies == []
    spec['capability_pins'][0]['delivery'] = 'requested_observation'
    spec['capability_pins'][0]['ports'] = [{'field': 'temperature_c', 'pointer': '/requested_observation/temperature_c'}]
    with pytest.raises(HTTPException):
        s.real_capabilities(s.db, s.p, s.task, s.sources, [spec], s.field_pins['fields'],
            {'data': {'dependencies': [cap.id]}}, live=True)


@pytest.mark.parametrize('side_effect,expected', [(False, 'analysis'), (True, 'operational_action')])
def test_registered_specialist_is_a_declaration_without_information_or_invocation_permission(scene, monkeypatch, side_effect, expected):
    s = scene
    data = {'kind': 'specialist_model', 'allowed_agent_roles': ['data', 'perception'],
        'execution': {'adapter': 'local_specialist', 'side_effect': side_effect},
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}}
    cap = NS(id=ident(75), kind='specialist_model', content_hash=digest(data), release_version='1.0.0', data=data)
    spec = {'candidate_ref': 'candidate_specialist', 'proposal': 'Use the actual professional model declaration.', 'claims': [],
        'capability_pins': [{'capability_ref': 'professional_model', 'capability_id': cap.id,
            'capability_hash': cap.content_hash, 'release_version': cap.release_version, 'delivery': 'output', 'ports': []}]}
    monkeypatch.setattr(subject.sources, 'fresh', lambda *_args: cap)
    monkeypatch.setattr(subject, '_publication', lambda *_args, **_kwargs: {'id': ident(76)})
    pins, _ = s.real_capabilities(s.db, s.p, s.task, s.sources, [spec], s.field_pins['fields'],
        {'data': {'dependencies': [cap.id]}}, live=True)
    assert pins[0]['action_kind'] == expected and pins[0]['ports'] == []
    assert pins[0]['data']['allowed_agent_roles'] == ['data', 'perception']


def test_historical_policy_does_not_read_the_current_policy_row(monkeypatch):
    # A cold-policy unit only: original Reader/source proof is an explicit boundary.
    context = {'context_hash': 'a' * 64, 'scope': {'task_id': ident(1), 'release_id': ident(2), 'package_id': ident(3)},
        'gap': {'entity_id': ident(4), 'fields': ['temperature_c'], 'observation_request': {
            'goal_delegation': {'task_id': ident(1)}, 'fields': ['temperature_c'],
            'request': {'evidence_requirement': {'source': 'observations', 'time': {'mode': 'latest'}}}}}}
    data = {'entity_ids': [ident(4)], 'fields': ['temperature_c'], 'source_ids': [ident(7)],
        'selection_requirement': {'allowed_actions': ['observe']}}
    data['approved_hash'] = digest(data)
    context['policy'] = {'policy_id': ident(5), 'version_at_read': 1, 'approved_by': ident(6),
        'approved_hash': data['approved_hash'], 'source_pins': [{'source_id': ident(7), 'source_hash': 'b' * 64}]}
    cap = NS(id=ident(7), kind='source', content_hash='b' * 64, data={'specification': {
        subject.managed_sources.BINDING_KEY: {'mapping': {'scope': {'release_id': ident(2), 'package_id': ident(3),
            'entity_type': 'plot'}, 'links': {'temperature_c': {'field_key': 'temperature_c', 'unit': 'Cel'}}}},
        'observation_capability': {'fields': ['temperature_c']}}})
    source = {'id': cap.id, 'hash': cap.content_hash, 'data': cap.data, 'publication': {'id': ident(8)},
        'fields': ['temperature_c'], 'units': {'temperature_c': 'Cel'}}
    original = {'context_hash': context['context_hash'], 'context': copy.deepcopy(context),
        'policy': {'id': ident(5), 'version': 1, 'approved_by': ident(6), 'data': data}, 'sources': [source]}
    monkeypatch.setattr(subject.reader, '_check_context', lambda *_args: None)
    def fresh(_db, _p, model, _identity):
        assert model is not MonitorPolicy, 'cold verification read mutable policy'
        return cap if model is Capability else NS(entity_type='plot')
    monkeypatch.setattr(subject.sources, 'fresh', fresh)
    monkeypatch.setattr(subject, '_publication', lambda *_args, **_kwargs: {'id': ident(8)})
    monkeypatch.setattr(subject.managed_sources, 'validate_source', lambda *_args: None)
    monkeypatch.setattr(subject.managed_sources, 'checked_binding', lambda value: value)
    assert subject._policy_pin(ReadDB(), NS(), NS(id=ident(1), data={'entity_ids': [ident(4)]}), context,
        live=False, original=original) == original


@pytest.mark.parametrize('source,required_kind', [('archive', 'document_statement'), ('either', 'observed_or_recorded')])
def test_archive_mode_preserves_explicit_unitless_record_and_no_time_requirement(scene, source, required_kind):
    s = scene; field = s.field_pins['fields'][0]
    field.update(field='active', value_type='boolean', unit=None, schema={'type': 'boolean'})
    record = {'entity_id': ident(2), 'attributes': {'active': True}, 'observed_at': None, 'source': 'resource_entity'}
    s.report['evidence'] = [record]; s.report['observation_evidence'] = []
    s.report['evidence_requirement']['source'] = source
    s.report['resolved_time'] = {'mode': 'archive', 'start': None, 'end': None}
    s.sources['data']['provenance']['sources'] = [{'kind': 'archive', 'record': record, 'entity': {'id': ident(2)}}]
    value = subject.build_frame(s.db, s.p, s.task, s.job, s.cap, s.inputs)['decision_input']
    requirement = value['requirements'][0]
    assert requirement['required_kind'] == required_kind and requirement['time_mode'] == 'not_required'
    assert requirement['unit'] is None and requirement['value_type'] == 'boolean'
    record_fact = next(fact for fact in value['facts'] if fact['kind'] == 'document_statement')
    assert record_fact['value'] is True and record_fact['semantics'] == 'statement'
    archive_source = next(item for item in value['sources'] if item['kind'] == 'archive_record')
    assert archive_source['source_id'] == ident(2)


def test_new_frame_protocol_does_not_bypass_original_adapter_or_actor_identity(monkeypatch):
    # Explicit IO spies; real identity conjunction, not IAM/source proof.
    p=NS(user_id=ident(901),workspace_id=ident(902),role='operator')
    inputs={'same':'arguments'};task=NS(id=ident(903));release=NS(id=ident(904))
    job=NS(id=ident(905),kind=subject.scope.KIND,status='running',data={'activation_id':'actual_activation',
        'task_id':task.id,'actor_id':p.user_id,'inputs':inputs,'input_hash':digest(inputs)})
    cap=NS(id=ident(906),content_hash='a'*64,release_version='1.0.0',data={
        'execution':{'adapter':'business_decision_v284'},'input_schema':{},'output_schema':{}})
    node={'actor_id':p.user_id,'execution':{'capability_id':cap.id,'capability_hash':cap.content_hash,
        'adapter':'business_decision_v284','agent_role':'business','release_version':'1.0.0',
        'input_schema':{},'output_schema':{}}}
    activation={'id':'actual_activation','status':'ready','epoch':0,'inputs_snapshot':{'arguments':inputs}}
    state={'status':'running','eligible_ability_ids':['actual_activation'],'cancel_epoch':0,'graph_hash':'b'*64}
    monkeypatch.setattr(subject.sources,'fresh',lambda _db,_p,model,_id:task if model is subject.Task else job if model is subject.Job else cap)
    monkeypatch.setattr(subject.scope,'_frozen',lambda *_:(task,release,{}, {},state,activation,node))
    monkeypatch.setattr(subject.scope,'_job_matches',lambda *_:{'phase':'running'})
    publications=[];monkeypatch.setattr(subject,'_publication',lambda *a,**kw:publications.append((a,kw)))
    monkeypatch.setattr(subject.workflow,'business_binding',lambda *_:{'original':'binding'})
    with pytest.raises(HTTPException):subject._identity(ReadDB(),p,task,job,cap,inputs,live=True)
    assert not publications
    got=subject._identity(ReadDB(),p,task,job,cap,inputs,live=True,_server_protocol='business_decision_v284')
    assert got[0] is task and got[3]['capability_hash']==cap.content_hash and len(publications)==1
    node['actor_id']=ident(999)
    with pytest.raises(HTTPException):subject._identity(ReadDB(),p,task,job,cap,inputs,live=True,_server_protocol='business_decision_v284')
    assert len(publications)==1


@pytest.mark.parametrize('protocol',['client_python_module',None,{},'business_decision_v284\x00'])
def test_unknown_frame_protocol_is_rejected_before_any_source_or_DB_access(protocol):
    with pytest.raises(HTTPException):subject.build_frame(ReadDB(),None,None,None,None,{},_server_protocol=protocol)
    with pytest.raises(HTTPException):subject.checked_frame(ReadDB(),None,None,None,None,{}, {},_server_protocol=protocol)
