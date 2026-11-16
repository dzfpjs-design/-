"""Pure declared-input semantics, not source authentication or runtime proof."""
import copy
from uuid import NAMESPACE_URL, uuid5

import pytest

from generic_agents import business_decision_contracts_v274 as contract


def identity(name):
    return str(uuid5(NAMESPACE_URL, 'decision-v274:' + name))


def time_scope(at='2026-11-01T12:00:00+00:00'):
    return {'start': at, 'end': at}


def citation(source):
    return {'source_ref': source['source_ref'], 'quote_start': 0,
        'quote_end': len(source['text']), 'quote': source['text']}


def example():
    entity = identity('weather')
    sources = [{'source_ref': 'source_health', 'kind': 'capability', 'source_id': identity('health_cap'),
        'content_hash': 'a' * 64, 'text': 'get_status declares device health only.', 'readability': 'readable'},
        {'source_ref': 'source_sampling', 'kind': 'capability', 'source_id': identity('sample_cap'),
        'content_hash': 'b' * 64, 'text': 'read_telemetry can request temperature and humidity; a request receipt is not an observation.',
        'readability': 'readable'}]
    requirements = [{'requirement_ref': ref, 'entity_id': entity, 'field': field, 'value_type': 'number', 'unit': unit,
        'semantics': 'instantaneous', 'time_scope': {'start': '2026-11-01T11:59:00+00:00',
            'end': '2026-11-01T12:01:00+00:00'}, 'required_kind': 'observation', 'time_mode': 'bounded'}
        for ref, field, unit in [('need_temperature', 'temperature_c', 'Cel'), ('need_humidity', 'relative_humidity_pct', '%')]]
    ports = [{'port_ref': 'target_temperature', 'pointer': '/request/temperature_c', 'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'},
        {'port_ref': 'target_humidity', 'pointer': '/request/relative_humidity_pct', 'field': 'relative_humidity_pct', 'value_type': 'number', 'unit': '%'}]
    caps = [{'capability_ref': ref, 'capability_id': source['source_id'], 'capability_hash': source['content_hash'],
        'release_version': '1.0.0', 'input_schema_hash': 'c' * 64, 'output_schema_hash': 'd' * 64,
        'source_ref': source['source_ref'], 'action_kind': 'information_acquisition',
        'output_ports': [{'port_ref': 'receipt', 'pointer': '/receipt/status', 'field': 'request_status', 'value_type': 'string', 'unit': '1'}],
        'information_targets': [] if index == 0 else copy.deepcopy(ports)}
        for index, (ref, source) in enumerate(zip(['cap_health', 'cap_sampling'], sources))]
    candidates = [{'candidate_ref': 'candidate_1', 'proposal': 'Read device health.', 'capability_refs': ['cap_health'],
        'requirement_mappings': [], 'claims': [{'claim_ref': 'claim_numbers', 'statement': 'Health alone supplies the requested numbers.'}]},
        {'candidate_ref': 'candidate_2', 'proposal': 'Request the declared simulated temperature and humidity observations.',
        'capability_refs': ['cap_sampling'], 'requirement_mappings': [
            {'requirement_ref': req['requirement_ref'], 'capability_ref': 'cap_sampling',
                'delivery': 'requested_observation', 'port_ref': port['port_ref']} for req, port in zip(requirements, ports)],
        'claims': [{'claim_ref': 'claim_request', 'statement': 'This capability can request the declared observations.'}]}]
    facts = [{'fact_ref': ref, 'kind': 'capability_declaration', 'entity_id': entity,
        'field': field, 'unit': None, 'semantics': 'statement', 'time_scope': {'start': None, 'end': None},
        'value': text, 'source_refs': [citation(source)]} for ref, field, text, source in [
            ('fact_health', 'health_contract', 'health only', sources[0]),
            ('fact_sampling', 'sampling_contract', 'can request two numerical observations', sources[1])]]
    inputs = {'schema': contract.INPUT, 'decision_intent': 'information_acquisition',
        'scope': {'task_id': identity('task'), 'activation_id': 'reasoning_activation',
            'goal_snapshot_hash': 'e' * 64, 'entity_ids': [entity]}, 'requirements': requirements,
        'sources': sources, 'capabilities': caps, 'candidates': candidates, 'facts': facts, 'known_conflicts': []}
    value = {'schema': contract.MODEL, 'input_hash': contract.input_hash(inputs),
        'summary': 'Recommend an observation request for review; no current measured values have arrived.',
        'candidate_assessments': [{'candidate_ref': candidate['candidate_ref'],
            'candidate_applicability': 'inapplicable' if index == 0 else 'applicable',
            'applicability_reason': 'Health has no required numerical target.' if index == 0 else 'Declared targets cover both requested fields.',
            'evidence_integrity': 'supported', 'evidence_reason': 'Original registration declaration supports this assessment.',
            'capability_refs': candidate['capability_refs'], 'requirement_refs': [req['requirement_ref'] for req in requirements],
            'unmet_requirement_refs': [req['requirement_ref'] for req in requirements] if index == 0 else [],
            'fact_refs': [facts[index]['fact_ref']], 'source_refs': [sources[index]['source_ref']],
            'refutations': [], 'missing_information': []} for index, candidate in enumerate(candidates)],
        'source_coverage': [{'source_ref': source['source_ref'], 'status': 'reviewed',
            'fact_refs': [facts[index]['fact_ref']], 'reason': 'The complete declared source was considered.'}
            for index, source in enumerate(sources)], 'conflicts': [],
        'knowledge_gaps': [{'requirement_ref': req['requirement_ref'], 'reason': 'No current observation has been received.'}
            for req in requirements], 'recommendation': {'candidate_ref': 'candidate_2',
                'reason': 'Request the missing observations; keep actual execution and receipt gates separate.', 'fact_refs': ['fact_sampling']}}
    return inputs, value


def bind(inputs, value):
    value['input_hash'] = contract.input_hash(inputs)
    return inputs, value


def add_observation(inputs, value, name, field, unit, number, at='2026-11-01T12:00:00+00:00'):
    source = {'source_ref': 'source_' + name, 'kind': 'observation', 'source_id': identity(name),
        'content_hash': 'f' * 64, 'text': field + '=' + str(number), 'readability': 'readable'}
    fact = {'fact_ref': 'fact_' + name, 'kind': 'observation', 'entity_id': inputs['scope']['entity_ids'][0],
        'field': field, 'unit': unit, 'semantics': 'instantaneous', 'time_scope': time_scope(at),
        'value': number, 'source_refs': [citation(source)]}
    inputs['sources'].append(source); inputs['facts'].append(fact)
    value['source_coverage'].append({'source_ref': source['source_ref'], 'status': 'reviewed',
        'fact_refs': [fact['fact_ref']], 'reason': 'Included the declared original observation.'})
    return fact


def operational_case(intent='operational_action', observed=False):
    inputs, value = example(); inputs['decision_intent'] = intent
    cap = inputs['capabilities'][1]
    cap['action_kind'] = 'operational_action' if intent == 'operational_action' else 'analysis'
    cap['output_ports'] = cap['information_targets']; cap['information_targets'] = []
    for mapping in inputs['candidates'][1]['requirement_mappings']:
        mapping['delivery'] = 'output'
    if observed:
        add_observation(inputs, value, 'temperature', 'temperature_c', 'Cel', 25)
        add_observation(inputs, value, 'humidity', 'relative_humidity_pct', '%', 60)
        value['knowledge_gaps'] = []
        selected = value['candidate_assessments'][1]
        selected['fact_refs'].extend(['fact_temperature', 'fact_humidity'])
        selected['source_refs'].extend(['source_temperature', 'source_humidity'])
        value['recommendation']['fact_refs'] = list(selected['fact_refs'])
        value['summary'] = 'Declared current numeric observations are available; this remains a proposal, not an approved outcome.'
    return bind(inputs, value)


def test_empty_observations_can_support_information_request_but_never_current_facts_or_permissions():
    inputs, value = example(); before = copy.deepcopy((inputs, value))
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'ready_for_review' and result['fact_consistency'] == 'unknown'
    assert not result['current_facts_sufficient'] and len(result['missing_requirement_refs']) == 2
    assert not any(result[key] for key in ('can_dispatch', 'permissions_verified', 'source_authenticity_verified',
        'current_outcome_verified', 'goal_completion_verified', 'data_reception_verified'))
    assert contract.check_result(inputs, result) == result and (inputs, value) == before


def test_unselected_candidate_refutation_does_not_invent_global_fact_conflict():
    inputs, value = example(); row = value['candidate_assessments'][0]
    row['evidence_integrity'] = 'contradictory'
    row['refutations'] = [{'claim_ref': 'claim_numbers', 'fact_refs': ['fact_health'],
        'reason': 'The original health-only declaration refutes the candidate assumption.'}]
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'ready_for_review' and result['conflicts'] == []


@pytest.mark.parametrize('intent', ['operational_action', 'business_conclusion'])
def test_actual_intent_requires_current_facts_before_review_ready(intent):
    inputs, value = operational_case(intent)
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'needs_information' and not result['current_facts_sufficient']
    inputs, value = operational_case(intent, observed=True)
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'ready_for_review' and result['current_facts_sufficient']
    assert result['current_outcome_verified'] is False and result['can_dispatch'] is False


def test_changing_only_intent_cannot_disguise_an_operational_capability_as_acquisition():
    inputs, value = operational_case(); inputs['decision_intent'] = 'information_acquisition'; bind(inputs, value)
    with pytest.raises(contract.DecisionContractError):
        contract.derive_result(inputs, value)


@pytest.mark.parametrize('damage', ['candidate_omitted', 'candidate_reordered', 'source_omitted', 'fact_omitted',
    'unknown_ref', 'capability_swapped', 'selected_inapplicable', 'selected_unknown', 'selected_missing', 'fake_quote', 'old_schema'])
def test_original_candidates_sources_quotes_and_selection_cannot_be_rewritten(damage):
    inputs, value = example()
    if damage == 'candidate_omitted': value['candidate_assessments'].pop(0)
    elif damage == 'candidate_reordered': value['candidate_assessments'].reverse()
    elif damage == 'source_omitted': value['source_coverage'].pop(0)
    elif damage == 'fact_omitted': value['source_coverage'][0]['fact_refs'] = []
    elif damage == 'unknown_ref': value['candidate_assessments'][1]['fact_refs'] = ['invented_fact']
    elif damage == 'capability_swapped': value['candidate_assessments'][1]['capability_refs'] = ['cap_health']
    elif damage == 'selected_inapplicable': value['recommendation']['candidate_ref'] = 'candidate_1'
    elif damage == 'selected_unknown': value['candidate_assessments'][1]['candidate_applicability'] = 'unknown'
    elif damage == 'selected_missing': value['candidate_assessments'][1]['missing_information'] = ['Missing a required premise.']
    elif damage == 'fake_quote': inputs['facts'][0]['source_refs'][0]['quote'] = 'invented quote'; bind(inputs, value)
    else: value['schema'] = 'business-reasoning-result-v197'
    with pytest.raises(contract.DecisionContractError): contract.derive_result(inputs, value)


def conflict_case():
    inputs, value = operational_case(observed=True)
    other = add_observation(inputs, value, 'other_temperature', 'temperature_c', 'Cel', 29)
    conflict = {'conflict_ref': 'conflict_temperature', 'fact_refs': ['fact_temperature', other['fact_ref']],
        'reason': 'Two exclusive numerical values occur at the same declared object/field/unit/time.'}
    inputs['known_conflicts'] = [copy.deepcopy(conflict)]; value['conflicts'] = [conflict]
    return bind(inputs, value)


def test_original_global_conflict_keeps_both_values_sources_and_scope_and_blocks_ready():
    inputs, value = conflict_case(); result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'conflicting_evidence' and result['fact_consistency'] == 'contradictory'
    conflict = result['conflicts'][0]
    assert [side['value'] for side in conflict['sides']] == [25, 29]
    assert all(side['source_refs'][0]['quote'] for side in conflict['sides'])
    assert conflict['field'] == 'temperature_c' and conflict['unit'] == 'Cel'
    assert conflict['entity_id'] == inputs['scope']['entity_ids'][0] and conflict['time_scope'] == time_scope()
    assert result['current_facts_sufficient'] is False


@pytest.mark.parametrize('known', [True, False])
def test_neither_known_nor_machine_comparable_original_conflicts_can_be_omitted(known):
    inputs, value = conflict_case(); value['conflicts'] = []
    if not known: inputs['known_conflicts'] = []; bind(inputs, value)
    with pytest.raises(contract.DecisionContractError): contract.derive_result(inputs, value)


def test_different_observation_times_do_not_automatically_become_conflict():
    inputs, value = operational_case(observed=True)
    add_observation(inputs, value, 'later_temperature', 'temperature_c', 'Cel', 29, '2026-11-01T13:00:00+00:00')
    bind(inputs, value)
    assert contract.derive_result(inputs, value)['conflicts'] == []
    value['conflicts'] = [{'conflict_ref': 'invented_conflict', 'fact_refs': ['fact_temperature', 'fact_later_temperature'], 'reason': 'Invented simultaneity.'}]
    with pytest.raises(contract.DecisionContractError): contract.derive_result(inputs, value)


def test_unknown_units_or_overlapping_nonidentical_windows_remain_pending_not_converted():
    inputs, value = operational_case(observed=True)
    other = add_observation(inputs, value, 'uncertain_temperature', 'temperature_c', None, 77)
    bind(inputs, value)
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'needs_information' and result['current_facts_sufficient'] is False
    other['unit'] = 'Cel'; other['time_scope'] = {'start': '2026-11-01T11:59:30+00:00', 'end': '2026-11-01T12:00:30+00:00'}
    bind(inputs, value)
    result = contract.derive_result(inputs, value)
    assert result['fact_consistency'] == 'unknown' and result['current_facts_sufficient'] is False


def test_integer_and_float_equal_values_are_not_a_contradiction():
    inputs, value = operational_case(observed=True)
    add_observation(inputs, value, 'same_temperature', 'temperature_c', 'Cel', 25.0); bind(inputs, value)
    assert contract.derive_result(inputs, value)['conflicts'] == []


@pytest.mark.parametrize('fake_value', ['not measured', True, False, None])
def test_nonnumeric_observation_cannot_satisfy_required_weather_value(fake_value):
    inputs, value = operational_case(observed=True)
    inputs['facts'][-2]['value'] = fake_value
    value['knowledge_gaps'] = [{'requirement_ref': 'need_temperature', 'reason': 'The current numeric temperature is missing.'}]
    bind(inputs, value)
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'needs_information' and result['current_facts_sufficient'] is False


def test_shorter_mean_cannot_satisfy_the_requested_full_interval_mean():
    inputs, value = operational_case(observed=True)
    inputs['requirements'][0]['semantics'] = 'interval_mean'
    inputs['facts'][-2]['semantics'] = 'interval_mean'
    value['knowledge_gaps'] = [{'requirement_ref': 'need_temperature', 'reason': 'The requested complete interval mean is missing.'}]
    bind(inputs, value)
    assert contract.derive_result(inputs, value)['review_state'] == 'needs_information'


def test_two_unknown_units_do_not_prove_applicability_by_comparing_equal_nulls():
    inputs, value = example()
    inputs['requirements'][0]['unit'] = None
    inputs['capabilities'][1]['information_targets'][0]['unit'] = None
    bind(inputs, value)
    with pytest.raises(contract.DecisionContractError):
        contract.derive_result(inputs, value)


@pytest.mark.parametrize('value_type,actual,valid', [('integer', True, False), ('integer', 25.5, False),
    ('integer', 25, True), ('integer', 25.0, True), ('number', False, False), ('number', 'not measured', False)])
def test_closed_scalar_type_controls_the_original_requirement_match(value_type, actual, valid):
    inputs, value = operational_case(observed=True)
    inputs['requirements'][0]['value_type'] = value_type
    inputs['capabilities'][1]['output_ports'][0]['value_type'] = value_type
    inputs['facts'][-2]['value'] = actual
    if not valid:
        value['knowledge_gaps'] = [{'requirement_ref': 'need_temperature', 'reason': 'Declared observation does not match the required value type.'}]
    bind(inputs, value)
    result = contract.derive_result(inputs, value)
    assert result['current_facts_sufficient'] is valid


def test_candidate_mapping_cannot_exchange_a_boolean_port_for_a_numeric_requirement():
    inputs, value = example()
    inputs['capabilities'][1]['information_targets'][0]['value_type'] = 'boolean'
    bind(inputs, value)
    with pytest.raises(contract.DecisionContractError, match='field/unit/type'):
        contract.derive_result(inputs, value)


@pytest.mark.parametrize('omission', ['both_only_capability', 'recommendation_only_capability', 'humidity_missing'])
def test_nonacquisition_recommendation_must_cite_original_matching_facts_not_just_capability(omission):
    inputs, value = operational_case(observed=True)
    selected = value['candidate_assessments'][1]
    if omission == 'both_only_capability':
        selected['fact_refs'] = ['fact_sampling']; selected['source_refs'] = ['source_sampling']
        value['recommendation']['fact_refs'] = ['fact_sampling']
    elif omission == 'recommendation_only_capability':
        value['recommendation']['fact_refs'] = ['fact_sampling']
    else:
        selected['fact_refs'].remove('fact_humidity'); selected['source_refs'].remove('source_humidity')
        value['recommendation']['fact_refs'].remove('fact_humidity')
    with pytest.raises(contract.DecisionContractError, match='matching original requirement fact'):
        contract.derive_result(inputs, value)


def test_instantaneous_requirement_cannot_be_satisfied_by_an_approximate_time_interval():
    inputs, value = operational_case(observed=True)
    inputs['facts'][-2]['time_scope'] = {'start': '2026-11-01T11:59:30+00:00', 'end': '2026-11-01T12:00:30+00:00'}
    value['knowledge_gaps'] = [{'requirement_ref': 'need_temperature', 'reason': 'The exact observation instant is unknown.'}]
    bind(inputs, value)
    result = contract.derive_result(inputs, value)
    assert result['review_state'] == 'needs_information' and result['current_facts_sufficient'] is False


@pytest.mark.parametrize('flag,value', [('can_dispatch', True), ('permissions_verified', 0),
    ('source_authenticity_verified', True), ('current_outcome_verified', True),
    ('goal_completion_verified', True), ('data_reception_verified', True)])
def test_proposal_cannot_gain_any_execution_receipt_goal_or_truth_authority(flag, value):
    inputs, model = example(); result = contract.derive_result(inputs, model); result[flag] = value
    with pytest.raises(contract.DecisionContractError): contract.check_result(inputs, result)


def test_self_rehashed_result_cannot_change_a_conflict_value_or_derived_readiness():
    inputs, model = conflict_case(); result = contract.derive_result(inputs, model)
    result['conflicts'][0]['sides'][0]['value'] = 29
    with pytest.raises(contract.DecisionContractError): contract.check_result(inputs, result)
    result = contract.derive_result(inputs, model); result['review_state'] = 'ready_for_review'
    with pytest.raises(contract.DecisionContractError): contract.check_result(inputs, result)


def test_false_verification_flags_do_not_claim_to_verify_natural_language_truth():
    inputs, model = example()
    model['summary'] = 'Unverified model prose claims the observations and business outcome already succeeded.'
    result = contract.derive_result(inputs, model)
    assert result['model_value']['summary'] == model['summary']
    assert result['current_outcome_verified'] is False
    assert result['goal_completion_verified'] is False and result['data_reception_verified'] is False


def test_all_object_schemas_are_closed_and_getters_return_detached_values():
    for getter in (contract.input_schema, contract.model_value_schema, contract.output_schema):
        value = getter(); stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get('type') == 'object':
                    assert item['additionalProperties'] is False
                    assert set(item['required']) == set(item['properties'])
                stack.extend(item.values())
            elif isinstance(item, list): stack.extend(item)
        value['properties'].clear()
        assert getter()['properties']


@pytest.mark.parametrize('different_body', [False, True])
def test_same_original_source_cannot_be_counted_again_under_a_new_alias(different_body):
    inputs, _ = example()
    alias = copy.deepcopy(inputs['sources'][1])
    alias['source_ref'] = 'source_sampling_alias'
    if different_body:
        alias['content_hash'] = '9' * 64
        alias['text'] = 'A different purported body under the same original identity.'
    inputs['sources'].append(alias)
    with pytest.raises(contract.DecisionContractError, match='multiple aliases'):
        contract.check_input(inputs)


def test_invalid_unicode_cannot_be_accepted_then_crash_input_hash_or_delivery():
    inputs, _ = example()
    invalid = chr(0xD800)
    inputs['sources'][0]['text'] = invalid
    inputs['facts'][0]['value'] = invalid
    inputs['facts'][0]['source_refs'][0].update(quote_start=0, quote_end=1, quote=invalid)
    with pytest.raises(contract.DecisionContractError, match='UTF-8'):
        contract.check_input(inputs)


def test_selected_document_context_can_be_cited_without_inventing_an_observation():
    inputs, model = example()
    source = {'source_ref': 'source_documents', 'kind': 'packet', 'source_id': identity('documents'),
        'content_hash': '8' * 64, 'text': 'The requested work is to obtain temperature and humidity readings.',
        'readability': 'readable'}
    inputs['sources'].append(source)
    model['source_coverage'].append({'source_ref': source['source_ref'], 'status': 'reviewed',
        'fact_refs': [], 'reason': 'Reviewed the original selected requirement context.'})
    model['candidate_assessments'][1]['source_refs'].append(source['source_ref'])
    bind(inputs, model)
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'ready_for_review'
    assert result['current_facts_sufficient'] is False
    assert not [fact for fact in inputs['facts'] if fact['kind'] == 'observation']


@pytest.mark.parametrize('readability, status', [('partial', 'incomparable'), ('unreadable', 'unreadable')])
def test_unreviewable_document_context_cannot_support_a_selected_assessment(readability, status):
    inputs, model = example()
    source = {'source_ref': 'source_documents', 'kind': 'packet', 'source_id': identity('documents'),
        'content_hash': '8' * 64, 'text': 'An incomplete original source.', 'readability': readability}
    inputs['sources'].append(source)
    model['source_coverage'].append({'source_ref': source['source_ref'], 'status': status,
        'fact_refs': [], 'reason': 'Original context is not fully reviewable.'})
    model['candidate_assessments'][1]['source_refs'].append(source['source_ref'])
    bind(inputs, model)
    with pytest.raises(contract.DecisionContractError, match='reviewed context'):
        contract.check_model_value(inputs, model)


@pytest.mark.parametrize('recorded', [False, True])
def test_either_source_matches_each_original_record_kind_without_changing_its_identity(recorded):
    inputs, model = operational_case(observed=True)
    for req in inputs['requirements']:
        req['required_kind'] = 'observed_or_recorded'
    if recorded:
        for fact in inputs['facts']:
            if fact['kind'] == 'observation':
                fact['kind'] = 'document_statement'
    bind(inputs, model)
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'ready_for_review'
    assert result['current_facts_sufficient'] is True


def test_explicit_timeless_archive_values_are_records_not_invented_measurement_times():
    inputs, model = operational_case(observed=True)
    for req in inputs['requirements']:
        req.update(required_kind='document_statement', time_mode='not_required',
            time_scope={'start': None, 'end': None}, semantics='statement', unit=None)
    for source in inputs['sources']:
        if source['kind'] == 'observation': source['kind'] = 'archive_record'
    for fact in inputs['facts']:
        if fact['kind'] == 'observation':
            fact.update(kind='document_statement', time_scope={'start': None, 'end': None},
                semantics='statement', unit=None)
    for cap in inputs['capabilities']:
        for port in cap['output_ports']: port['unit'] = None
    bind(inputs, model)
    result = contract.derive_result(inputs, model)
    assert result['current_facts_sufficient'] is True
    assert result['review_state'] == 'ready_for_review'
    assert result['current_outcome_verified'] is False


def test_no_time_requirement_does_not_turn_an_unclocked_observation_into_a_record():
    inputs, model = operational_case(observed=True)
    for req in inputs['requirements']:
        req.update(required_kind='observed_or_recorded', time_mode='not_required',
            time_scope={'start': None, 'end': None}, semantics='statement')
    for fact in inputs['facts']:
        if fact['kind'] == 'observation': fact['time_scope'] = {'start': None, 'end': None}
    model['recommendation'].update(candidate_ref=None, fact_refs=[])
    model['knowledge_gaps'] = [{'requirement_ref': req['requirement_ref'],
        'reason': 'The original observation has no reliable acquisition time.'} for req in inputs['requirements']]
    bind(inputs, model)
    result = contract.derive_result(inputs, model)
    assert result['current_facts_sufficient'] is False
    assert result['review_state'] == 'needs_information'
