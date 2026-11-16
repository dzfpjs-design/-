"""Pure V284 review opinions and deterministic checks over the V274 input.

No I/O, execution, provenance grant, observation, receipt or permission is
created here. Model opinions are retained verbatim; mechanically derived
coverage and current-fact checks are kept in a separate, closed object.
These checks do not authenticate sources or verify free-text reasoning.
"""
import copy
from typing import Literal

from pydantic import Field, StrictBool, ValidationError

from . import business_decision_contracts_v274 as base

INPUT = base.INPUT
MODEL = 'business-decision-review-value-v284'
RESULT = 'business-decision-review-result-v284'
DecisionInput = base.DecisionInput
DecisionContractError = base.DecisionContractError
input_hash = base.input_hash
check_input = base.check_input


class CandidateReview(base.Closed):
    candidate_ref: base.Ref
    suitability: Literal['suitable', 'unsuitable', 'uncertain']
    reasoning: base.Text
    supporting_fact_refs: list[base.Ref] = Field(max_length=128)
    blocking_conditions: list[base.Text] = Field(max_length=16)


class SourceReview(base.Closed):
    source_ref: base.Ref
    assessment: Literal['reviewed', 'unreadable', 'incomparable']
    reason: base.Text


class Recommendation(base.Closed):
    candidate_ref: base.Ref | None
    reason: base.Text
    supporting_fact_refs: list[base.Ref] = Field(max_length=128)


class ModelValue(base.Closed):
    schema: Literal['business-decision-review-value-v284']
    candidate_reviews: list[CandidateReview] = Field(min_length=1, max_length=16)
    source_reviews: list[SourceReview] = Field(min_length=1, max_length=32)
    conflicts: list[base.Conflict] = Field(max_length=32)
    recommendation: Recommendation


class CandidateCheck(base.Closed):
    candidate_ref: base.Ref
    capability_refs: list[base.Ref] = Field(min_length=1, max_length=8)
    requirement_refs: list[base.Ref] = Field(min_length=1, max_length=30)
    covered_requirement_refs: list[base.Ref] = Field(max_length=30)
    uncovered_requirement_refs: list[base.Ref] = Field(max_length=30)
    unknown_requirement_refs: list[base.Ref] = Field(max_length=30)
    applicability: Literal['applicable', 'inapplicable', 'unknown']
    support: Literal['supported', 'insufficient']
    blocking_conditions: list[base.Text] = Field(max_length=16)


class SourceCheck(base.Closed):
    source_ref: base.Ref
    assigned_fact_refs: list[base.Ref] = Field(max_length=128)
    assessment: Literal['reviewed', 'unreadable', 'incomparable']


class ConflictView(base.Closed):
    # Unitless bool/string facts and explicitly timeless archive records are
    # comparable under the unchanged base._conflict rules. Preserve their
    # actual None unit instead of imposing the older result-only string type.
    conflict_ref: base.Ref
    fact_refs: list[base.Ref] = Field(min_length=2, max_length=8)
    entity_id: base.ID
    field: base.Ref
    unit: base.Unit
    semantics: Literal['instantaneous', 'interval_mean', 'statement']
    time_scope: base.Window
    sides: list[base.ConflictSide] = Field(min_length=2, max_length=8)
    reason: base.Text


class MechanicalReview(base.Closed):
    candidates: list[CandidateCheck] = Field(min_length=1, max_length=16)
    sources: list[SourceCheck] = Field(min_length=1, max_length=32)
    missing_requirement_refs: list[base.Ref] = Field(max_length=30)
    fact_consistency: Literal['consistent', 'contradictory', 'unknown']
    current_facts_sufficient: StrictBool
    conflicts: list[ConflictView] = Field(max_length=32)


class DecisionResult(base.Closed):
    schema: Literal['business-decision-review-result-v284']
    input_hash: base.Hash
    decision_intent: base.Intent
    scope: base.Scope
    model_value: ModelValue
    mechanical_review: MechanicalReview
    review_state: Literal['ready_for_review', 'needs_information', 'conflicting_evidence', 'review_required']
    can_dispatch: Literal[False]
    permissions_verified: Literal[False]
    source_authenticity_verified: Literal[False]
    current_outcome_verified: Literal[False]
    goal_completion_verified: Literal[False]
    data_reception_verified: Literal[False]


def _parse(model, raw):
    base._json(raw)
    try:
        return model.model_validate(copy.deepcopy(raw)).model_dump(mode='json')
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise DecisionContractError('closed V284 contract does not match') from None


def check_model_value(inputs, raw):
    """Validate original references, not the correctness of a recommendation."""
    value = check_input(inputs)
    model = _parse(ModelValue, raw)
    candidates = base._index(value['candidates'], 'candidate_ref')
    sources = base._index(value['sources'], 'source_ref')
    facts = base._index(value['facts'], 'fact_ref')
    base._need([row['candidate_ref'] for row in model['candidate_reviews']] == list(candidates),
        'original candidates omitted, reordered or replaced')
    base._need([row['source_ref'] for row in model['source_reviews']] == list(sources),
        'original sources omitted, reordered or replaced')
    for row in model['candidate_reviews']:
        base._refs(row['supporting_fact_refs'], facts)
    for row in model['source_reviews']:
        base._need(row['assessment'] != 'reviewed' or sources[row['source_ref']]['readability'] == 'readable',
            'unreadable source cannot be declared reviewed')
    conflicts = base._index(model['conflicts'], 'conflict_ref')
    for known in value['known_conflicts']:
        base._need(conflicts.get(known['conflict_ref']) == known,
            'known original conflict cannot be omitted or rewritten')
    for conflict in model['conflicts']:
        base._conflict(conflict, facts)
    for index, left in enumerate(value['facts']):
        for right in value['facts'][index + 1:]:
            if base._comparable(left, right) and not base._same_value(left['value'], right['value']):
                base._need(any({left['fact_ref'], right['fact_ref']} <= set(row['fact_refs'])
                    for row in model['conflicts']), 'exclusive original facts cannot be silently omitted')
    recommendation = model['recommendation']
    base._refs(recommendation['supporting_fact_refs'], facts)
    base._need(recommendation['candidate_ref'] is None or recommendation['candidate_ref'] in candidates,
        'recommendation is not an original candidate')
    return model


def _intent_matches(intent, candidate, caps):
    kinds = {caps[ref]['action_kind'] for ref in candidate['capability_refs']}
    return ((intent == 'information_acquisition' and kinds == {'information_acquisition'})
        or (intent == 'operational_action' and 'operational_action' in kinds)
        or (intent == 'business_conclusion' and kinds == {'analysis'}))


def _requirement_coverage(value, candidate):
    # check_input has already checked every mapping's original field/unit/type.
    mapped = {row['requirement_ref'] for row in candidate['requirement_mappings']}
    covered, uncovered, unknown = [], [], []
    for requirement in value['requirements']:
        ref = requirement['requirement_ref']
        unit_known = (requirement['unit'] is not None
            or requirement['value_type'] in {'string', 'boolean'}
            or requirement['time_mode'] == 'not_required')
        if ref not in mapped:
            uncovered.append(ref)
        elif not unit_known or requirement['semantics'] == 'unknown':
            unknown.append(ref)
        else:
            covered.append(ref)
    return covered, uncovered, unknown


def _declared_capabilities(candidate, refs, facts, caps):
    # A declaration for a different capability/source cannot prove this one.
    return all(any(facts[ref]['kind'] == 'capability_declaration'
        and any(citation['source_ref'] == caps[cap_ref]['source_ref']
            for citation in facts[ref]['source_refs']) for ref in refs)
        for cap_ref in candidate['capability_refs'])


def _requirements_supported(value, candidate_refs, recommendation_refs=None):
    # Preserve the legacy per-requirement intersection: one actual matching
    # fact shared by the candidate and recommendation is enough. Equivalent
    # duplicate facts do not create a new copying burden on the model.
    candidate_refs = set(candidate_refs)
    recommendation_refs = candidate_refs if recommendation_refs is None else set(recommendation_refs)
    for requirement in value['requirements']:
        matches = set(base._matching_fact_refs(requirement, value['facts']))
        if matches and not matches & candidate_refs & recommendation_refs:
            return False
    return True


def _current_facts(value, model, conflicts):
    """The V274 current-fact sufficiency/consistency algorithm, unchanged."""
    missing = base._missing(value)
    complete = all(row['assessment'] == 'reviewed' for row in model['source_reviews'])

    def incomplete_fact(fact):
        if fact['kind'] not in {'observation', 'document_statement'}:
            return False
        record_requirement = any(req['time_mode'] == 'not_required'
            and fact['fact_ref'] in base._matching_fact_refs(req, [fact]) for req in value['requirements'])
        unit_known = fact['unit'] is not None or type(fact['value']) in {str, bool} or record_requirement
        time_known = None not in base._window(fact['time_scope']) or record_requirement
        return not unit_known or not time_known or fact['semantics'] == 'unknown'

    unknown = bool(missing) or not value['facts'] or not complete or any(incomplete_fact(fact) for fact in value['facts'])
    unknown = unknown or any(base._uncertain_pair(left, right) for index, left in enumerate(value['facts'])
        for right in value['facts'][index + 1:])
    consistency = 'contradictory' if conflicts else 'unknown' if unknown else 'consistent'
    return missing, complete, consistency, not missing and complete and consistency == 'consistent'


def derive_result(inputs, raw):
    value = check_input(inputs)
    model = check_model_value(value, raw)
    facts = base._index(value['facts'], 'fact_ref')
    caps = base._index(value['capabilities'], 'capability_ref')
    reviews = base._index(model['candidate_reviews'], 'candidate_ref')
    conflicts = [base._conflict(row, facts) for row in model['conflicts']]
    missing, complete, consistency, sufficient = _current_facts(value, model, conflicts)
    candidate_checks = []
    for candidate in value['candidates']:
        row = reviews[candidate['candidate_ref']]
        covered, uncovered, unknown = _requirement_coverage(value, candidate)
        intent_matches = _intent_matches(value['decision_intent'], candidate, caps)
        if row['suitability'] == 'unsuitable':
            applicability = 'inapplicable'
        elif row['suitability'] == 'uncertain':
            applicability = 'unknown'
        elif uncovered or not intent_matches:
            applicability = 'inapplicable'
        elif unknown:
            applicability = 'unknown'
        else:
            applicability = 'applicable'
        supported = _declared_capabilities(candidate, row['supporting_fact_refs'], facts, caps)
        if value['decision_intent'] != 'information_acquisition':
            supported = supported and _requirements_supported(value, row['supporting_fact_refs'])
        candidate_checks.append({'candidate_ref': candidate['candidate_ref'],
            'capability_refs': candidate['capability_refs'],
            'requirement_refs': [req['requirement_ref'] for req in value['requirements']],
            'covered_requirement_refs': covered, 'uncovered_requirement_refs': uncovered,
            'unknown_requirement_refs': unknown, 'applicability': applicability,
            'support': 'supported' if supported else 'insufficient',
            'blocking_conditions': row['blocking_conditions']})
    source_checks = [{'source_ref': row['source_ref'],
        'assigned_fact_refs': [fact['fact_ref'] for fact in value['facts']
            if any(citation['source_ref'] == row['source_ref'] for citation in fact['source_refs'])],
        'assessment': row['assessment']} for row in model['source_reviews']]
    recommendation = model['recommendation']
    selected = recommendation['candidate_ref']
    selected_ok = True
    if selected is not None:
        check = next(row for row in candidate_checks if row['candidate_ref'] == selected)
        refs = recommendation['supporting_fact_refs']
        selected_ok = (check['applicability'] == 'applicable' and check['support'] == 'supported'
            and not check['blocking_conditions'] and bool(refs)
            and set(refs) <= set(reviews[selected]['supporting_fact_refs']))
        if value['decision_intent'] == 'information_acquisition':
            candidate = next(row for row in value['candidates'] if row['candidate_ref'] == selected)
            selected_ok = selected_ok and _declared_capabilities(candidate, refs, facts, caps)
        else:
            selected_ok = selected_ok and _requirements_supported(value,
                reviews[selected]['supporting_fact_refs'], refs)
    if conflicts:
        state = 'conflicting_evidence'
    elif selected is not None and not selected_ok:
        state = 'review_required'
    elif not complete:
        state = 'needs_information'
    elif selected is not None and value['decision_intent'] == 'information_acquisition':
        state = 'ready_for_review'
    elif sufficient:
        state = 'ready_for_review'
    else:
        state = 'needs_information'
    return _parse(DecisionResult, {'schema': RESULT, 'input_hash': input_hash(value),
        'decision_intent': value['decision_intent'], 'scope': value['scope'], 'model_value': model,
        'mechanical_review': {'candidates': candidate_checks, 'sources': source_checks,
            'missing_requirement_refs': missing, 'fact_consistency': consistency,
            'current_facts_sufficient': sufficient, 'conflicts': conflicts},
        'review_state': state, 'can_dispatch': False, 'permissions_verified': False,
        'source_authenticity_verified': False, 'current_outcome_verified': False,
        'goal_completion_verified': False, 'data_reception_verified': False})


def check_result(inputs, result):
    base._need(type(result) is dict, 'result must be an original JSON object')
    for key in ('can_dispatch', 'permissions_verified', 'source_authenticity_verified',
            'current_outcome_verified', 'goal_completion_verified', 'data_reception_verified'):
        base._need(type(result.get(key)) is bool and result[key] is False,
            'decision proposals never grant execution or verified outcomes')
    value = _parse(DecisionResult, result)
    expected = derive_result(inputs, value['model_value'])
    base._need(base._json(value) == base._json(expected),
        'platform-derived decision status or original evidence changed')
    return value


def input_schema():
    return copy.deepcopy(DecisionInput.model_json_schema())


def model_value_schema():
    return copy.deepcopy(ModelValue.model_json_schema())


def output_schema():
    return copy.deepcopy(DecisionResult.model_json_schema())
