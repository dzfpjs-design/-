"""Independent synthetic, pure contracts; no I/O, PG, runtime or model-quality proof."""
import copy
from uuid import NAMESPACE_URL, uuid5

import pytest

from generic_agents import business_decision_contracts_v284 as contract


AUTHORITY_FLAGS = ('can_dispatch', 'permissions_verified', 'source_authenticity_verified',
    'current_outcome_verified', 'goal_completion_verified', 'data_reception_verified')


def identity(name):
    return str(uuid5(NAMESPACE_URL, 'synthetic-decision-v284:' + name))


def time_scope(at='2026-11-01T12:00:00+00:00'):
    return {'start': at, 'end': at}


def citation(source):
    return {'source_ref': source['source_ref'], 'quote_start': 0,
        'quote_end': len(source['text']), 'quote': source['text']}


def example():
    """Synthetic health-vs-sampling case, independent of legacy test helpers."""
    entity = identity('weather')
    sources = [{'source_ref': 'source_health', 'kind': 'capability', 'source_id': identity('health_cap'),
        'content_hash': 'a' * 64, 'text': 'get_status declares device health only.', 'readability': 'readable'},
        {'source_ref': 'source_sampling', 'kind': 'capability', 'source_id': identity('sample_cap'),
        'content_hash': 'b' * 64, 'text': 'read_telemetry requests temperature and humidity; its request receipt is not an observation.',
        'readability': 'readable'}]
    requirements = [{'requirement_ref': ref, 'entity_id': entity, 'field': field, 'value_type': 'number',
        'unit': unit, 'semantics': 'instantaneous', 'time_scope': {
            'start': '2026-11-01T11:59:00+00:00', 'end': '2026-11-01T12:01:00+00:00'},
        'required_kind': 'observation', 'time_mode': 'bounded'} for ref, field, unit in [
            ('need_temperature', 'temperature_c', 'Cel'), ('need_humidity', 'relative_humidity_pct', '%')]]
    ports = [{'port_ref': 'target_temperature', 'pointer': '/request/temperature_c',
        'field': 'temperature_c', 'value_type': 'number', 'unit': 'Cel'},
        {'port_ref': 'target_humidity', 'pointer': '/request/relative_humidity_pct',
        'field': 'relative_humidity_pct', 'value_type': 'number', 'unit': '%'}]
    caps = [{'capability_ref': ref, 'capability_id': source['source_id'],
        'capability_hash': source['content_hash'], 'release_version': '1.0.0',
        'input_schema_hash': 'c' * 64, 'output_schema_hash': 'd' * 64, 'source_ref': source['source_ref'],
        'action_kind': 'information_acquisition', 'output_ports': [{'port_ref': 'receipt',
            'pointer': '/receipt/status', 'field': 'request_status', 'value_type': 'string', 'unit': '1'}],
        'information_targets': [] if index == 0 else copy.deepcopy(ports)}
        for index, (ref, source) in enumerate(zip(['cap_health', 'cap_sampling'], sources))]
    candidates = [{'candidate_ref': 'candidate_1', 'proposal': 'Read device health.',
        'capability_refs': ['cap_health'], 'requirement_mappings': [],
        'claims': [{'claim_ref': 'claim_numbers', 'statement': 'Health supplies the requested numbers.'}]},
        {'candidate_ref': 'candidate_2', 'proposal': 'Request the two declared simulated observations.',
        'capability_refs': ['cap_sampling'], 'requirement_mappings': [
            {'requirement_ref': req['requirement_ref'], 'capability_ref': 'cap_sampling',
                'delivery': 'requested_observation', 'port_ref': port['port_ref']}
            for req, port in zip(requirements, ports)],
        'claims': [{'claim_ref': 'claim_request', 'statement': 'The declared observations can be requested.'}]}]
    facts = [{'fact_ref': ref, 'kind': 'capability_declaration', 'entity_id': entity, 'field': field,
        'unit': None, 'semantics': 'statement', 'time_scope': {'start': None, 'end': None},
        'value': text, 'source_refs': [citation(source)]} for ref, field, text, source in [
            ('fact_health', 'health_contract', 'health only', sources[0]),
            ('fact_sampling', 'sampling_contract', 'can request two numerical observations', sources[1])]]
    inputs = {'schema': contract.INPUT, 'decision_intent': 'information_acquisition',
        'scope': {'task_id': identity('task'), 'activation_id': 'synthetic_review',
            'goal_snapshot_hash': 'e' * 64, 'entity_ids': [entity]}, 'requirements': requirements,
        'sources': sources, 'capabilities': caps, 'candidates': candidates, 'facts': facts, 'known_conflicts': []}
    model = {'schema': contract.MODEL, 'candidate_reviews': [
        {'candidate_ref': candidate['candidate_ref'], 'suitability': 'unsuitable' if index == 0 else 'suitable',
            'reasoning': 'Health lacks numerical targets.' if index == 0 else 'The declared targets cover both fields.',
            'supporting_fact_refs': [facts[index]['fact_ref']], 'blocking_conditions': []}
        for index, candidate in enumerate(candidates)], 'source_reviews': [
        {'source_ref': source['source_ref'], 'assessment': 'reviewed', 'reason': 'Read the original source.'}
        for source in sources], 'conflicts': [], 'recommendation': {'candidate_ref': 'candidate_2',
            'reason': 'Request missing observations for review; independent execution and receipts remain required.',
            'supporting_fact_refs': ['fact_sampling']}}
    return inputs, model


def add_observation(inputs, model, name, field, unit, number, at='2026-11-01T12:00:00+00:00'):
    source = {'source_ref': 'source_' + name, 'kind': 'observation', 'source_id': identity(name),
        'content_hash': 'f' * 64, 'text': field + '=' + str(number), 'readability': 'readable'}
    fact = {'fact_ref': 'fact_' + name, 'kind': 'observation', 'entity_id': inputs['scope']['entity_ids'][0],
        'field': field, 'unit': unit, 'semantics': 'instantaneous', 'time_scope': time_scope(at),
        'value': number, 'source_refs': [citation(source)]}
    inputs['sources'].append(source)
    inputs['facts'].append(fact)
    model['source_reviews'].append({'source_ref': source['source_ref'], 'assessment': 'reviewed',
        'reason': 'Read the original synthetic observation.'})
    return fact


def operational_case(intent='operational_action', observed=False):
    inputs, model = example()
    inputs['decision_intent'] = intent
    cap = inputs['capabilities'][1]
    cap['action_kind'] = 'operational_action' if intent == 'operational_action' else 'analysis'
    cap['output_ports'] = cap['information_targets']
    cap['information_targets'] = []
    for mapping in inputs['candidates'][1]['requirement_mappings']:
        mapping['delivery'] = 'output'
    if observed:
        add_observation(inputs, model, 'temperature', 'temperature_c', 'Cel', 25)
        add_observation(inputs, model, 'humidity', 'relative_humidity_pct', '%', 60)
        model['candidate_reviews'][1]['supporting_fact_refs'].extend(['fact_temperature', 'fact_humidity'])
        model['recommendation']['supporting_fact_refs'] = list(model['candidate_reviews'][1]['supporting_fact_refs'])
    return inputs, model


def conflict_case():
    inputs, model = operational_case(observed=True)
    other = add_observation(inputs, model, 'other_temperature', 'temperature_c', 'Cel', 29)
    conflict = {'conflict_ref': 'conflict_temperature', 'fact_refs': ['fact_temperature', other['fact_ref']],
        'reason': 'Exclusive numerical values at the same original field/unit/time.'}
    inputs['known_conflicts'] = [copy.deepcopy(conflict)]
    model['conflicts'] = [conflict]
    return inputs, model


def test_platform_binds_original_input_and_keeps_model_opinion_separate_from_current_facts():
    inputs, model = example()
    before = copy.deepcopy((inputs, model))
    result = contract.derive_result(inputs, model)
    review = result['mechanical_review']
    assert result['schema'] == contract.RESULT
    assert result['input_hash'] == contract.input_hash(contract.check_input(inputs))
    assert result['model_value'] == model and 'input_hash' not in model
    assert result['review_state'] == 'ready_for_review'
    assert review['fact_consistency'] == 'unknown' and not review['current_facts_sufficient']
    assert review['missing_requirement_refs'] == ['need_temperature', 'need_humidity']
    assert (inputs, model) == before and contract.check_result(inputs, result) == result
    assert all(result[key] is False for key in AUTHORITY_FLAGS)


def test_coverage_and_unselected_bad_candidate_are_mechanical_and_original_ordered():
    inputs, model = example()
    rows = contract.derive_result(inputs, model)['mechanical_review']['candidates']
    assert rows[0]['capability_refs'] == ['cap_health']
    assert rows[0]['applicability'] == 'inapplicable'
    assert rows[0]['uncovered_requirement_refs'] == rows[0]['requirement_refs']
    assert rows[1]['capability_refs'] == ['cap_sampling']
    assert rows[1]['covered_requirement_refs'] == rows[1]['requirement_refs']
    assert rows[1]['unknown_requirement_refs'] == rows[1]['uncovered_requirement_refs'] == []
    assert rows[1]['applicability'] == 'applicable' and rows[1]['support'] == 'supported'
    model['candidate_reviews'][0]['blocking_conditions'] = ['Health cannot supply the numerical targets.']
    assert contract.derive_result(inputs, model)['review_state'] == 'ready_for_review'


def test_source_assignments_come_only_from_original_citations_and_empty_packet_stays_empty():
    inputs, model = example()
    packet = {'source_ref': 'source_packet', 'kind': 'packet', 'source_id': identity('packet'),
        'content_hash': '8' * 64, 'text': 'Obtain temperature and humidity readings.', 'readability': 'readable'}
    inputs['sources'].append(packet)
    model['source_reviews'].append({'source_ref': 'source_packet', 'assessment': 'reviewed', 'reason': 'Read selected context.'})
    result = contract.derive_result(inputs, model)
    assert [row['assigned_fact_refs'] for row in result['mechanical_review']['sources']] == [
        ['fact_health'], ['fact_sampling'], []]
    assert result['review_state'] == 'ready_for_review'
    assert not [fact for fact in inputs['facts'] if fact['kind'] == 'observation']
    inputs['facts'][1]['source_refs'].append(citation(packet))
    assert contract.derive_result(inputs, model)['mechanical_review']['sources'][-1]['assigned_fact_refs'] == ['fact_sampling']


@pytest.mark.parametrize('damage', ['candidate_omitted', 'candidate_reordered', 'candidate_duplicate',
    'source_omitted', 'source_reordered', 'source_duplicate', 'candidate_fact_unknown',
    'candidate_fact_duplicate', 'recommendation_fact_unknown', 'recommendation_fact_duplicate',
    'recommendation_candidate_unknown', 'source_unknown', 'fake_quote'])
def test_original_reference_errors_are_rejected(damage):
    inputs, model = example()
    if damage == 'candidate_omitted': model['candidate_reviews'].pop(0)
    elif damage == 'candidate_reordered': model['candidate_reviews'].reverse()
    elif damage == 'candidate_duplicate': model['candidate_reviews'][0] = copy.deepcopy(model['candidate_reviews'][1])
    elif damage == 'source_omitted': model['source_reviews'].pop(0)
    elif damage == 'source_reordered': model['source_reviews'].reverse()
    elif damage == 'source_duplicate': model['source_reviews'][0] = copy.deepcopy(model['source_reviews'][1])
    elif damage == 'candidate_fact_unknown': model['candidate_reviews'][1]['supporting_fact_refs'] = ['invented_fact']
    elif damage == 'candidate_fact_duplicate': model['candidate_reviews'][1]['supporting_fact_refs'] *= 2
    elif damage == 'recommendation_fact_unknown': model['recommendation']['supporting_fact_refs'] = ['invented_fact']
    elif damage == 'recommendation_fact_duplicate': model['recommendation']['supporting_fact_refs'] *= 2
    elif damage == 'recommendation_candidate_unknown': model['recommendation']['candidate_ref'] = 'invented_candidate'
    elif damage == 'source_unknown': model['source_reviews'][0]['source_ref'] = 'invented_source'
    else: inputs['facts'][0]['source_refs'][0]['quote'] = 'Invented quote.'
    with pytest.raises(contract.DecisionContractError):
        contract.derive_result(inputs, model)


@pytest.mark.parametrize('extra', ['input_hash', 'knowledge_gaps', 'candidate_assessments', 'source_coverage', 'summary'])
def test_old_or_fabricated_model_fields_are_forbidden(extra):
    inputs, model = example()
    model[extra] = contract.input_hash(inputs) if extra == 'input_hash' else []
    with pytest.raises(contract.DecisionContractError): contract.check_model_value(inputs, model)


@pytest.mark.parametrize('location,field', [('candidate', 'capability_refs'), ('candidate', 'requirement_refs'),
    ('source', 'assigned_fact_refs'), ('source', 'fact_refs')])
def test_model_does_not_copy_mechanical_arrays(location, field):
    inputs, model = example()
    row = model['candidate_reviews'][1] if location == 'candidate' else model['source_reviews'][0]
    row[field] = []
    with pytest.raises(contract.DecisionContractError): contract.check_model_value(inputs, model)


@pytest.mark.parametrize('readability', ['partial', 'unreadable'])
def test_false_review_of_unreadable_original_source_is_rejected(readability):
    inputs, model = example()
    inputs['sources'][1]['readability'] = readability
    with pytest.raises(contract.DecisionContractError, match='unreadable'):
        contract.check_model_value(inputs, model)
    model['source_reviews'][1]['assessment'] = 'unreadable' if readability == 'unreadable' else 'incomparable'
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'needs_information'
    assert result['mechanical_review']['current_facts_sufficient'] is False


@pytest.mark.parametrize('defect,applicability', [('unsuitable', 'inapplicable'), ('uncertain', 'unknown'),
    ('unmapped', 'inapplicable'), ('unknown_unit', 'unknown'), ('unknown_semantics', 'unknown'),
    ('blocked', 'applicable'), ('unsupported', 'applicable'), ('recommendation_not_candidate_support', 'applicable'),
    ('empty_recommendation_support', 'applicable')])
def test_original_but_inconsistent_recommendation_is_retained_for_review(defect, applicability):
    inputs, model = example()
    row = model['candidate_reviews'][1]
    if defect in {'unsuitable', 'uncertain'}: row['suitability'] = defect
    elif defect == 'unmapped': inputs['candidates'][1]['requirement_mappings'].pop()
    elif defect == 'unknown_unit':
        inputs['requirements'][0]['unit'] = None
        inputs['capabilities'][1]['information_targets'][0]['unit'] = None
    elif defect == 'unknown_semantics': inputs['requirements'][0]['semantics'] = 'unknown'
    elif defect == 'blocked': row['blocking_conditions'] = ['A necessary field condition has not been established.']
    elif defect == 'unsupported': row['supporting_fact_refs'] = []
    elif defect == 'recommendation_not_candidate_support': model['recommendation']['supporting_fact_refs'] = ['fact_health']
    else: model['recommendation']['supporting_fact_refs'] = []
    before = copy.deepcopy(model)
    assert contract.check_model_value(inputs, model) == model
    result = contract.derive_result(inputs, model)
    assert result['model_value'] == before and result['review_state'] == 'review_required'
    assert result['mechanical_review']['candidates'][1]['applicability'] == applicability
    assert result['mechanical_review']['conflicts'] == []


def test_unknown_and_missing_mappings_are_different_and_requirement_order_is_preserved():
    inputs, model = example()
    inputs['requirements'][0]['unit'] = None
    inputs['capabilities'][1]['information_targets'][0]['unit'] = None
    inputs['candidates'][1]['requirement_mappings'].pop()
    row = contract.derive_result(inputs, model)['mechanical_review']['candidates'][1]
    assert row['requirement_refs'] == ['need_temperature', 'need_humidity']
    assert row['unknown_requirement_refs'] == ['need_temperature']
    assert row['uncovered_requirement_refs'] == ['need_humidity']
    assert row['covered_requirement_refs'] == []


def test_each_candidate_capability_needs_its_own_original_declaration_source():
    inputs, model = example()
    candidate = inputs['candidates'][1]
    candidate['capability_refs'].append('cap_health')
    result = contract.derive_result(inputs, model)
    assert result['mechanical_review']['candidates'][1]['support'] == 'insufficient'
    assert result['review_state'] == 'review_required'
    model['candidate_reviews'][1]['supporting_fact_refs'].append('fact_health')
    assert contract.derive_result(inputs, model)['review_state'] == 'review_required'
    model['recommendation']['supporting_fact_refs'].append('fact_health')
    assert contract.derive_result(inputs, model)['review_state'] == 'ready_for_review'
    model['candidate_reviews'][1]['supporting_fact_refs'] = ['fact_health']
    assert contract.derive_result(inputs, model)['mechanical_review']['candidates'][1]['support'] == 'insufficient'


def test_acquisition_recommendation_itself_must_cite_selected_capability_declarations():
    inputs, model = example()
    model['candidate_reviews'][1]['supporting_fact_refs'].append('fact_health')
    model['recommendation']['supporting_fact_refs'] = ['fact_health']
    before = copy.deepcopy(model)
    result = contract.derive_result(inputs, model)
    assert result['mechanical_review']['candidates'][1]['support'] == 'supported'
    assert result['model_value'] == before and result['review_state'] == 'review_required'
    model['recommendation']['supporting_fact_refs'] = ['fact_sampling']
    assert contract.derive_result(inputs, model)['review_state'] == 'ready_for_review'


@pytest.mark.parametrize('kind', ['inference', 'document_statement'])
def test_generic_noninference_or_inference_at_capability_source_is_not_a_declaration(kind):
    inputs, model = example()
    inputs['facts'][1]['kind'] = kind
    result = contract.derive_result(inputs, model)
    assert result['mechanical_review']['candidates'][1]['support'] == 'insufficient'
    assert result['review_state'] == 'review_required'


def test_capability_or_future_observation_does_not_become_a_current_requirement_fact():
    inputs, model = example()
    inputs['facts'][1].update(field='temperature_c', unit='Cel', semantics='instantaneous',
        time_scope=time_scope(), value=25)
    add_observation(inputs, model, 'future_humidity', 'relative_humidity_pct', '%', 60,
        at='2030-01-01T12:00:00+00:00')
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'ready_for_review'
    assert result['mechanical_review']['missing_requirement_refs'] == ['need_temperature', 'need_humidity']
    assert result['mechanical_review']['current_facts_sufficient'] is False


@pytest.mark.parametrize('intent', ['operational_action', 'business_conclusion'])
def test_nonacquisition_requires_current_facts_and_preserves_false_authority(intent):
    inputs, model = operational_case(intent)
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'needs_information'
    assert result['mechanical_review']['current_facts_sufficient'] is False
    inputs, model = operational_case(intent, observed=True)
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'ready_for_review'
    assert result['mechanical_review']['current_facts_sufficient'] is True
    assert all(result[key] is False for key in AUTHORITY_FLAGS)


@pytest.mark.parametrize('omission', ['candidate', 'recommendation', 'both'])
def test_nonacquisition_existing_matching_facts_must_support_both_candidate_and_recommendation(omission):
    inputs, model = operational_case(observed=True)
    if omission in {'candidate', 'both'}:
        model['candidate_reviews'][1]['supporting_fact_refs'].remove('fact_humidity')
    if omission in {'recommendation', 'both'}:
        model['recommendation']['supporting_fact_refs'].remove('fact_humidity')
    result = contract.derive_result(inputs, model)
    assert result['mechanical_review']['current_facts_sufficient'] is True
    assert result['review_state'] == 'review_required'


def test_equivalent_duplicate_facts_need_not_all_be_copied_into_support():
    inputs, model = operational_case(observed=True)
    add_observation(inputs, model, 'same_temperature', 'temperature_c', 'Cel', 25.0)
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == 'ready_for_review'
    assert result['mechanical_review']['conflicts'] == []


def test_matching_fact_must_be_shared_rather_than_different_equivalent_refs_in_each_opinion():
    inputs, model = operational_case(observed=True)
    other = add_observation(inputs, model, 'same_temperature', 'temperature_c', 'Cel', 25.0)
    refs = model['recommendation']['supporting_fact_refs']
    refs[refs.index('fact_temperature')] = other['fact_ref']
    # Both original facts match, but the legacy intersection must be nonempty.
    assert contract.derive_result(inputs, model)['review_state'] == 'review_required'
    model['candidate_reviews'][1]['supporting_fact_refs'].append(other['fact_ref'])
    assert contract.derive_result(inputs, model)['review_state'] == 'ready_for_review'


def test_intent_mismatch_is_preserved_as_an_inapplicable_recommendation_for_review():
    inputs, model = operational_case()
    inputs['decision_intent'] = 'information_acquisition'
    result = contract.derive_result(inputs, model)
    assert result['mechanical_review']['candidates'][1]['applicability'] == 'inapplicable'
    assert result['review_state'] == 'review_required'


@pytest.mark.parametrize('observed', [False, True])
def test_valid_null_recommendation_ready_only_with_sufficient_consistent_current_facts(observed):
    inputs, model = operational_case(observed=observed)
    model['recommendation'].update(candidate_ref=None, supporting_fact_refs=[])
    result = contract.derive_result(inputs, model)
    assert result['review_state'] == ('ready_for_review' if observed else 'needs_information')
    assert result['model_value']['recommendation']['candidate_ref'] is None
    assert result['mechanical_review']['current_facts_sufficient'] is observed


def test_true_conflict_retains_all_original_scope_sides_citations_and_prevents_ready():
    inputs, model = conflict_case()
    result = contract.derive_result(inputs, model)
    review = result['mechanical_review']
    assert result['review_state'] == 'conflicting_evidence'
    assert review['fact_consistency'] == 'contradictory' and review['current_facts_sufficient'] is False
    conflict = review['conflicts'][0]
    assert conflict['fact_refs'] == ['fact_temperature', 'fact_other_temperature']
    assert [side['value'] for side in conflict['sides']] == [25, 29]
    assert all(side['source_refs'][0]['quote'] for side in conflict['sides'])
    assert conflict['entity_id'] == inputs['scope']['entity_ids'][0]
    assert conflict['field'] == 'temperature_c' and conflict['unit'] == 'Cel'
    assert conflict['time_scope'] == time_scope()


def test_unitless_same_time_boolean_conflict_preserves_true_false_and_false_authority():
    inputs, model = operational_case(observed=True)
    inputs['requirements'][0].update(field='status_online', value_type='boolean', unit=None)
    inputs['capabilities'][1]['output_ports'][0].update(field='status_online', value_type='boolean', unit=None)
    original = inputs['facts'][-2]
    original.update(field='status_online', unit=None, value=True)
    original_source = inputs['sources'][-2]
    original_source['text'] = 'status_online=True'
    original['source_refs'] = [citation(original_source)]
    other = add_observation(inputs, model, 'other_status', 'status_online', None, False)
    conflict = {'conflict_ref': 'conflict_status', 'fact_refs': [original['fact_ref'], other['fact_ref']],
        'reason': 'The same declared boolean status has exclusive values at the same instant.'}
    inputs['known_conflicts'] = [copy.deepcopy(conflict)]
    model['conflicts'] = [conflict]
    result = contract.derive_result(inputs, model)
    view = result['mechanical_review']['conflicts'][0]
    assert result['review_state'] == 'conflicting_evidence'
    assert view['unit'] is None and view['time_scope'] == time_scope()
    assert [side['value'] for side in view['sides']] == [True, False]
    assert all(side['source_refs'][0]['quote'] for side in view['sides'])
    assert all(result[key] is False for key in AUTHORITY_FLAGS)
    assert contract.check_result(inputs, result) == result


def test_unitless_timeless_archive_numeric_conflict_preserves_original_values_and_scope():
    inputs, model = conflict_case()
    for requirement in inputs['requirements']:
        requirement.update(required_kind='document_statement', time_mode='not_required', unit=None,
            semantics='statement', time_scope={'start': None, 'end': None})
    for port in inputs['capabilities'][1]['output_ports']:
        port['unit'] = None
    for source in inputs['sources']:
        if source['kind'] == 'observation': source['kind'] = 'archive_record'
    for fact in inputs['facts']:
        if fact['kind'] == 'observation':
            fact.update(kind='document_statement', unit=None, semantics='statement',
                time_scope={'start': None, 'end': None})
    result = contract.derive_result(inputs, model)
    view = result['mechanical_review']['conflicts'][0]
    assert result['review_state'] == 'conflicting_evidence'
    assert view['unit'] is None and view['semantics'] == 'statement'
    assert view['time_scope'] == {'start': None, 'end': None}
    assert [side['value'] for side in view['sides']] == [25, 29]
    assert view['fact_refs'] == model['conflicts'][0]['fact_refs']
    assert all(side['source_refs'][0]['quote'] for side in view['sides'])
    assert all(result[key] is False for key in AUTHORITY_FLAGS)
    assert contract.check_result(inputs, result) == result


def test_unitless_bounded_numeric_values_remain_noncomparable_and_cannot_invent_conflict():
    inputs, model = conflict_case()
    inputs['known_conflicts'] = []
    for fact in inputs['facts']:
        if fact['field'] == 'temperature_c': fact['unit'] = None
    with pytest.raises(contract.DecisionContractError, match='unknown or incomparable'):
        contract.derive_result(inputs, model)


@pytest.mark.parametrize('damage', ['known_omitted', 'known_rewritten', 'machine_omitted',
    'duplicate_conflict', 'same_value', 'unknown_ref', 'different_time'])
def test_hidden_known_or_actual_conflicts_and_fabricated_conflicts_are_rejected(damage):
    inputs, model = conflict_case()
    if damage == 'known_omitted': model['conflicts'] = []
    elif damage == 'known_rewritten': model['conflicts'][0]['reason'] = 'Rewritten known conflict.'
    elif damage == 'machine_omitted':
        inputs['known_conflicts'] = []
        model['conflicts'] = []
    elif damage == 'duplicate_conflict': model['conflicts'].append(copy.deepcopy(model['conflicts'][0]))
    elif damage == 'same_value': inputs['facts'][-1]['value'] = 25
    elif damage == 'unknown_ref': model['conflicts'][0]['fact_refs'][-1] = 'invented_fact'
    else:
        inputs['known_conflicts'] = []
        inputs['facts'][-1]['time_scope'] = time_scope('2026-11-01T13:00:00+00:00')
    with pytest.raises(contract.DecisionContractError): contract.derive_result(inputs, model)


@pytest.mark.parametrize('defect', ['unknown_unit', 'unknown_semantics', 'unknown_time', 'overlapping_time',
    'wrong_scalar', 'short_interval'])
def test_legacy_current_fact_unknowns_remain_unknown_and_insufficient(defect):
    inputs, model = operational_case(observed=True)
    fact = inputs['facts'][-2]
    if defect == 'unknown_unit': fact['unit'] = None
    elif defect == 'unknown_semantics': fact['semantics'] = 'unknown'
    elif defect == 'unknown_time': fact['time_scope'] = {'start': None, 'end': None}
    elif defect == 'overlapping_time':
        other = add_observation(inputs, model, 'uncertain_temperature', 'temperature_c', 'Cel', 25)
        other['time_scope'] = {'start': '2026-11-01T11:59:30+00:00', 'end': '2026-11-01T12:00:30+00:00'}
    elif defect == 'wrong_scalar': fact['value'] = True
    else:
        inputs['requirements'][0]['semantics'] = 'interval_mean'
        fact['semantics'] = 'interval_mean'
    result = contract.derive_result(inputs, model)
    review = result['mechanical_review']
    assert review['fact_consistency'] == 'unknown' and review['current_facts_sufficient'] is False
    assert result['review_state'] == 'needs_information'


def test_explicit_timeless_archive_records_remain_supported_without_invented_measurement_time():
    inputs, model = operational_case(observed=True)
    for requirement in inputs['requirements']:
        requirement.update(required_kind='document_statement', time_mode='not_required',
            time_scope={'start': None, 'end': None}, semantics='statement', unit=None)
    for source in inputs['sources']:
        if source['kind'] == 'observation': source['kind'] = 'archive_record'
    for fact in inputs['facts']:
        if fact['kind'] == 'observation':
            fact.update(kind='document_statement', semantics='statement', unit=None,
                time_scope={'start': None, 'end': None})
    for port in inputs['capabilities'][1]['output_ports']: port['unit'] = None
    result = contract.derive_result(inputs, model)
    assert result['mechanical_review']['current_facts_sufficient'] is True
    assert result['review_state'] == 'ready_for_review'


@pytest.mark.parametrize('flag', AUTHORITY_FLAGS)
@pytest.mark.parametrize('value', [True, 0, 1, None])
def test_result_cannot_gain_any_authority_or_coerce_false_flags(flag, value):
    inputs, model = example()
    result = contract.derive_result(inputs, model)
    result[flag] = value
    with pytest.raises(contract.DecisionContractError): contract.check_result(inputs, result)


@pytest.mark.parametrize('damage', ['hash', 'state', 'coverage', 'source_assignment', 'consistency',
    'sufficiency', 'blocking_conditions', 'conflict_value', 'extra_field'])
def test_platform_derivations_are_recomputed_and_cannot_be_mutated(damage):
    inputs, model = conflict_case() if damage == 'conflict_value' else example()
    result = contract.derive_result(inputs, model)
    review = result['mechanical_review']
    if damage == 'hash': result['input_hash'] = '0' * 64
    elif damage == 'state': result['review_state'] = 'review_required'
    elif damage == 'coverage': review['candidates'][0]['covered_requirement_refs'] = ['need_temperature']
    elif damage == 'source_assignment': review['sources'][0]['assigned_fact_refs'] = ['fact_sampling']
    elif damage == 'consistency': review['fact_consistency'] = 'consistent'
    elif damage == 'sufficiency': review['current_facts_sufficient'] = True
    elif damage == 'blocking_conditions': review['candidates'][1]['blocking_conditions'] = ['Invented condition.']
    elif damage == 'conflict_value': review['conflicts'][0]['sides'][0]['value'] = 29
    else: result['receipt'] = 'invented'
    with pytest.raises(contract.DecisionContractError): contract.check_result(inputs, result)


def test_derived_result_and_schema_values_are_detached_and_input_change_invalidates_binding():
    inputs, model = example()
    result = contract.derive_result(inputs, model)
    result['model_value']['candidate_reviews'][0]['reasoning'] = 'Changed returned copy.'
    result['mechanical_review']['candidates'][1]['capability_refs'].clear()
    assert model['candidate_reviews'][0]['reasoning'] != 'Changed returned copy.'
    assert inputs['candidates'][1]['capability_refs'] == ['cap_sampling']
    result = contract.derive_result(inputs, model)
    inputs['scope']['activation_id'] = 'another_original_activation'
    with pytest.raises(contract.DecisionContractError): contract.check_result(inputs, result)
    for getter in (contract.input_schema, contract.model_value_schema, contract.output_schema):
        schema = getter()
        stack = [schema]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get('type') == 'object':
                    assert item['additionalProperties'] is False
                    assert set(item['required']) == set(item['properties'])
                stack.extend(item.values())
            elif isinstance(item, list): stack.extend(item)
        schema['properties'].clear()
        assert getter()['properties']


@pytest.mark.parametrize('field', ['reasoning', 'blocking_conditions'])
def test_candidate_text_is_nonempty_and_blocking_conditions_are_bounded(field):
    inputs, model = example()
    model['candidate_reviews'][1][field] = '   ' if field == 'reasoning' else ['Condition.'] * 17
    with pytest.raises(contract.DecisionContractError): contract.check_model_value(inputs, model)
