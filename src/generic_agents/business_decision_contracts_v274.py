"""Uninstalled, pure decision proposal contracts; no execution or provenance grant.

This first slice checks declared scalar types, exact scopes and references,
not the authenticity or semantic entailment of facts. A future server builder must authenticate every
source, capability, observation and scope. Exact units/time windows only: no
conversion, approximate matching, permission, Goal or receipt is manufactured.
Verification flags mean this proposal proves no current outcome, Goal
completion or data reception. They do not verify the truth of free text;
actual status remains in the independent server records.
"""
import copy
import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError

INPUT = 'business-decision-input-v274'
MODEL = 'business-decision-model-value-v274'
RESULT = 'business-decision-result-v274'
Ref = Annotated[StrictStr, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
ID = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Hash = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[StrictStr, Field(min_length=1, max_length=2000, pattern=r'\S')]
Unit = Annotated[StrictStr, Field(min_length=1, max_length=100, pattern=r'\S')] | None
Intent = Literal['information_acquisition', 'operational_action', 'business_conclusion']
ValueType = Literal['number', 'integer', 'string', 'boolean']
Scalar = Annotated[StrictStr, Field(max_length=4000)] | StrictInt | StrictFloat | StrictBool | None


class DecisionContractError(ValueError):
    pass


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Window(Closed):
    start: StrictStr | None
    end: StrictStr | None


class Scope(Closed):
    task_id: ID
    activation_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    goal_snapshot_hash: Hash
    entity_ids: list[ID] = Field(min_length=1, max_length=30)


class Requirement(Closed):
    requirement_ref: Ref
    entity_id: ID
    field: Ref
    value_type: ValueType
    unit: Unit
    semantics: Literal['instantaneous', 'interval_mean', 'statement', 'unknown']
    time_scope: Window
    required_kind: Literal['observation', 'document_statement', 'capability_declaration', 'observed_or_recorded']
    time_mode: Literal['bounded', 'not_required']


class Source(Closed):
    source_ref: Ref
    kind: Literal['evidence', 'packet', 'observation', 'archive_record', 'capability']
    source_id: ID
    content_hash: Hash
    text: Annotated[StrictStr, Field(max_length=20000)] | None
    readability: Literal['readable', 'partial', 'unreadable']


class Port(Closed):
    port_ref: Ref
    pointer: Annotated[StrictStr, Field(max_length=300)]
    field: Ref
    value_type: ValueType
    unit: Unit


class Capability(Closed):
    capability_ref: Ref
    capability_id: ID
    capability_hash: Hash
    release_version: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    input_schema_hash: Hash
    output_schema_hash: Hash
    source_ref: Ref
    action_kind: Literal['information_acquisition', 'operational_action', 'analysis']
    output_ports: list[Port] = Field(max_length=30)
    information_targets: list[Port] = Field(max_length=30)


class Mapping(Closed):
    requirement_ref: Ref
    capability_ref: Ref
    delivery: Literal['output', 'requested_observation']
    port_ref: Ref


class Claim(Closed):
    claim_ref: Ref
    statement: Text


class Candidate(Closed):
    candidate_ref: Ref
    proposal: Text
    capability_refs: list[Ref] = Field(min_length=1, max_length=8)
    requirement_mappings: list[Mapping] = Field(max_length=30)
    claims: list[Claim] = Field(max_length=16)


class Citation(Closed):
    source_ref: Ref
    quote_start: Annotated[StrictInt, Field(ge=0)] | None
    quote_end: Annotated[StrictInt, Field(ge=1)] | None
    quote: Annotated[StrictStr, Field(min_length=1, max_length=1200)] | None


class Fact(Closed):
    fact_ref: Ref
    kind: Literal['observation', 'document_statement', 'capability_declaration', 'inference']
    entity_id: ID
    field: Ref
    unit: Unit
    semantics: Literal['instantaneous', 'interval_mean', 'statement', 'unknown']
    time_scope: Window
    value: Scalar
    source_refs: list[Citation] = Field(min_length=1, max_length=16)


class Conflict(Closed):
    conflict_ref: Ref
    fact_refs: list[Ref] = Field(min_length=2, max_length=8)
    reason: Text


class DecisionInput(Closed):
    schema: Literal['business-decision-input-v274']
    decision_intent: Intent
    scope: Scope
    requirements: list[Requirement] = Field(min_length=1, max_length=30)
    sources: list[Source] = Field(min_length=1, max_length=32)
    capabilities: list[Capability] = Field(min_length=1, max_length=16)
    candidates: list[Candidate] = Field(min_length=1, max_length=16)
    facts: list[Fact] = Field(max_length=128)
    known_conflicts: list[Conflict] = Field(max_length=32)


class Refutation(Closed):
    claim_ref: Ref
    fact_refs: list[Ref] = Field(min_length=1, max_length=16)
    reason: Text


class Assessment(Closed):
    candidate_ref: Ref
    candidate_applicability: Literal['applicable', 'inapplicable', 'unknown']
    applicability_reason: Text
    evidence_integrity: Literal['supported', 'contradictory', 'insufficient']
    evidence_reason: Text
    capability_refs: list[Ref] = Field(min_length=1, max_length=8)
    requirement_refs: list[Ref] = Field(min_length=1, max_length=30)
    unmet_requirement_refs: list[Ref] = Field(max_length=30)
    fact_refs: list[Ref] = Field(max_length=128)
    source_refs: list[Ref] = Field(min_length=1, max_length=32)
    refutations: list[Refutation] = Field(max_length=16)
    missing_information: list[Text] = Field(max_length=64)


class Coverage(Closed):
    source_ref: Ref
    status: Literal['reviewed', 'unreadable', 'incomparable']
    fact_refs: list[Ref] = Field(max_length=128)
    reason: Text


class Gap(Closed):
    requirement_ref: Ref
    reason: Text


class Recommendation(Closed):
    candidate_ref: Ref | None
    reason: Text
    fact_refs: list[Ref] = Field(max_length=128)


class ModelValue(Closed):
    schema: Literal['business-decision-model-value-v274']
    input_hash: Hash
    summary: Text
    candidate_assessments: list[Assessment] = Field(min_length=1, max_length=16)
    source_coverage: list[Coverage] = Field(min_length=1, max_length=32)
    conflicts: list[Conflict] = Field(max_length=32)
    knowledge_gaps: list[Gap] = Field(max_length=30)
    recommendation: Recommendation


class ConflictSide(Closed):
    fact_ref: Ref
    value: Scalar
    source_refs: list[Citation] = Field(min_length=1, max_length=16)


class ConflictView(Closed):
    conflict_ref: Ref
    fact_refs: list[Ref] = Field(min_length=2, max_length=8)
    entity_id: ID
    field: Ref
    unit: StrictStr
    semantics: Literal['instantaneous', 'interval_mean', 'statement']
    time_scope: Window
    sides: list[ConflictSide] = Field(min_length=2, max_length=8)
    reason: Text


class DecisionResult(Closed):
    schema: Literal['business-decision-result-v274']
    input_hash: Hash
    decision_intent: Intent
    scope: Scope
    model_value: ModelValue
    review_state: Literal['ready_for_review', 'needs_information', 'conflicting_evidence']
    fact_consistency: Literal['consistent', 'contradictory', 'unknown']
    current_facts_sufficient: StrictBool
    missing_requirement_refs: list[Ref] = Field(max_length=30)
    conflicts: list[ConflictView] = Field(max_length=32)
    can_dispatch: Literal[False]
    permissions_verified: Literal[False]
    source_authenticity_verified: Literal[False]
    current_outcome_verified: Literal[False]
    goal_completion_verified: Literal[False]
    data_reception_verified: Literal[False]


def _need(ok, message):
    if not ok:
        raise DecisionContractError(message)


def _json(value):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        result.encode('utf-8', errors='strict')
        return result
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise DecisionContractError('value must be finite UTF-8 JSON') from None


def input_hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _parse(model, raw):
    _json(raw)
    try:
        return model.model_validate(copy.deepcopy(raw)).model_dump(mode='json')
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise DecisionContractError('closed V274 contract does not match') from None


def _index(rows, key):
    result = {row[key]: row for row in rows}
    _need(len(result) == len(rows), 'duplicate original reference')
    return result


def _refs(values, allowed):
    _need(len(set(values)) == len(values) and set(values) <= set(allowed), 'duplicate or unknown reference')


def _window(value):
    def instant(raw):
        if raw is None:
            return None
        try:
            at = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            _need(at.tzinfo is not None and at.utcoffset() is not None, 'time requires timezone')
            return at
        except (ValueError, TypeError):
            raise DecisionContractError('invalid time scope') from None
    start, end = instant(value['start']), instant(value['end'])
    _need(start is None or end is None or start <= end, 'reversed time scope')
    return start, end


def _comparable(left, right):
    # Exact declared scalar disagreement only, never an inferred conversion.
    kinds = {'observation', 'document_statement'}
    left_type, right_type = type(left['value']), type(right['value'])
    same_type = ((left_type in {int, float} and right_type in {int, float})
        or left_type is right_type and left_type in {str, bool})
    timeless_records = (left['kind'] == right['kind'] == 'document_statement'
        and left['semantics'] == right['semantics'] == 'statement'
        and _window(left['time_scope']) == _window(right['time_scope']) == (None, None))
    known_unit = (left['unit'] is not None or left_type in {str, bool} or timeless_records)
    known_time = timeless_records or (None not in _window(left['time_scope'])
        and _window(left['time_scope']) == _window(right['time_scope']))
    return (left['kind'] in kinds and right['kind'] in kinds and same_type and known_unit
        and left['semantics'] != 'unknown'
        and all(left[key] == right[key] for key in ('entity_id', 'field', 'unit', 'semantics'))
        and known_time)


def _same_value(left, right):
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    return type(left) is type(right) and left == right


def _uncertain_pair(left, right):
    if (left['kind'] not in {'observation', 'document_statement'}
            or right['kind'] not in {'observation', 'document_statement'}
            or left['entity_id'] != right['entity_id'] or left['field'] != right['field']):
        return False
    a, b = _window(left['time_scope']); c, d = _window(right['time_scope'])
    if None not in (a, b, c, d) and (b < c or d < a):
        return False  # Different nonoverlapping observations can describe change.
    return not _comparable(left, right)


def _conflict(row, facts):
    _refs(row['fact_refs'], facts)
    sides = [facts[ref] for ref in row['fact_refs']]
    _need(all(_comparable(sides[0], fact) for fact in sides[1:]), 'conflict scope is unknown or incomparable')
    _need(any(not _same_value(sides[0]['value'], fact['value']) for fact in sides[1:]), 'conflict has no exclusive original values')
    return {'conflict_ref': row['conflict_ref'], 'fact_refs': row['fact_refs'],
        **{key: copy.deepcopy(sides[0][key]) for key in ('entity_id', 'field', 'unit', 'semantics', 'time_scope')},
        'sides': [{key: copy.deepcopy(fact[key]) for key in ('fact_ref', 'value', 'source_refs')} for fact in sides],
        'reason': row['reason']}


def check_input(raw):
    value = _parse(DecisionInput, raw)
    entities = value['scope']['entity_ids']; _refs(entities, entities)
    requirements = _index(value['requirements'], 'requirement_ref')
    sources = _index(value['sources'], 'source_ref')
    _need(len({(row['kind'], row['source_id']) for row in sources.values()}) == len(sources),
        'same original source identity cannot have multiple aliases')
    caps = _index(value['capabilities'], 'capability_ref')
    candidates = _index(value['candidates'], 'candidate_ref')
    facts = _index(value['facts'], 'fact_ref')
    _index(value['known_conflicts'], 'conflict_ref')
    _need(len({row['capability_id'] for row in caps.values()}) == len(caps), 'capability identity duplicated')
    for requirement in requirements.values():
        _need(requirement['entity_id'] in entities, 'requirement outside original entity scope')
        _window(requirement['time_scope'])
        if requirement['time_mode'] == 'not_required':
            _need(requirement['required_kind'] in {'document_statement', 'observed_or_recorded'}
                and requirement['semantics'] == 'statement'
                and _window(requirement['time_scope']) == (None, None),
                'unbounded record requirements must be explicit, never an unknown observation time')
    for cap in caps.values():
        source = sources.get(cap['source_ref'])
        _need(source is not None and source['kind'] == 'capability'
            and source['source_id'] == cap['capability_id'] and source['content_hash'] == cap['capability_hash'],
            'capability registration reference changed')
        for ports in (cap['output_ports'], cap['information_targets']):
            _index(ports, 'port_ref')
            for port in ports:
                pointer = port['pointer']
                _need(not pointer or pointer.startswith('/') and all('~' not in part.replace('~0', '').replace('~1', '')
                    for part in pointer.split('/')), 'invalid original port pointer')
        _need(not cap['information_targets'] or cap['action_kind'] == 'information_acquisition', 'operational capability cannot be relabelled as acquisition')
    for candidate in candidates.values():
        _refs(candidate['capability_refs'], caps); _index(candidate['claims'], 'claim_ref')
        mapped = []
        for link in candidate['requirement_mappings']:
            _need(link['requirement_ref'] in requirements and link['capability_ref'] in candidate['capability_refs'], 'candidate mapping outside original scope')
            cap = caps[link['capability_ref']]
            ports = cap['output_ports'] if link['delivery'] == 'output' else cap['information_targets']
            port = _index(ports, 'port_ref').get(link['port_ref']); requirement = requirements[link['requirement_ref']]
            _need(port is not None and all(port[key] == requirement[key] for key in ('field', 'unit', 'value_type')),
                'candidate port does not provide declared field/unit/type')
            mapped.append((link['requirement_ref'], link['capability_ref'], link['delivery'], link['port_ref']))
        _need(len(mapped) == len(set(mapped)), 'duplicate candidate mapping')
    for fact in facts.values():
        _need(fact['entity_id'] in entities, 'fact outside original entity scope'); _window(fact['time_scope'])
        seen = set()
        for citation in fact['source_refs']:
            source = sources.get(citation['source_ref']); _need(source is not None, 'unknown fact source')
            _need(citation['source_ref'] not in seen, 'duplicate fact source'); seen.add(citation['source_ref'])
            if source['text'] is None:
                _need(all(citation[key] is None for key in ('quote_start', 'quote_end', 'quote')), 'invented source quote')
            else:
                start, end = citation['quote_start'], citation['quote_end']
                _need(type(start) is int and type(end) is int and 0 <= start < end <= len(source['text'])
                    and source['text'][start:end] == citation['quote'], 'quote does not match original selected text')
        if fact['kind'] == 'observation':
            _need(any(sources[item['source_ref']]['kind'] == 'observation' for item in fact['source_refs']), 'observation cannot be invented from a proposal')
        if fact['kind'] == 'capability_declaration':
            _need(any(sources[item['source_ref']]['kind'] == 'capability' for item in fact['source_refs']), 'missing actual capability source declaration')
    for conflict in value['known_conflicts']:
        _conflict(conflict, facts)
    return value


def _value_matches(value_type, value):
    if value_type == 'number':
        return type(value) in {int, float}
    if value_type == 'integer':
        return type(value) is int or type(value) is float and value.is_integer()
    return type(value) is (str if value_type == 'string' else bool)


def _matching_fact_refs(requirement, facts):
    """Match original type, source kind and the requested temporal semantics."""
    start, end = _window(requirement['time_scope'])
    result = []
    for fact in facts:
        at, until = _window(fact['time_scope'])
        either = requirement['required_kind'] == 'observed_or_recorded'
        kind_matches = (fact['kind'] == requirement['required_kind']
            or either and fact['kind'] in {'observation', 'document_statement'})
        no_time = requirement['time_mode'] == 'not_required'
        recorded = fact['kind'] == 'document_statement' and fact['semantics'] == 'statement'
        if no_time:
            time_matches = recorded or (fact['kind'] == 'observation'
                and fact['semantics'] != 'unknown' and None not in (at, until))
            semantics_match = time_matches
        else:
            exact = requirement['semantics'] in {'interval_mean', 'statement'}
            time_matches = (None not in (start, end, at, until)
                and ((at == start and until == end) if exact else (start <= at == until <= end)))
            semantics_match = fact['semantics'] == requirement['semantics'] != 'unknown'
        unit_known = (requirement['unit'] is not None
            or requirement['value_type'] in {'string', 'boolean'} or no_time and recorded)
        if (kind_matches and _value_matches(requirement['value_type'], fact['value'])
                and unit_known and fact['entity_id'] == requirement['entity_id']
                and fact['field'] == requirement['field'] and fact['unit'] == requirement['unit']
                and semantics_match and time_matches):
            result.append(fact['fact_ref'])
    return result


def _missing(value):
    return [requirement['requirement_ref'] for requirement in value['requirements']
        if not _matching_fact_refs(requirement, value['facts'])]


def check_model_value(inputs, raw):
    value = check_input(inputs); model = _parse(ModelValue, raw)
    _need(model['input_hash'] == input_hash(value), 'original decision input changed')
    candidates = _index(value['candidates'], 'candidate_ref'); caps = _index(value['capabilities'], 'capability_ref')
    facts = _index(value['facts'], 'fact_ref'); sources = _index(value['sources'], 'source_ref')
    requirements = _index(value['requirements'], 'requirement_ref')
    reqs = list(requirements)
    _need([row['candidate_ref'] for row in model['candidate_assessments']] == list(candidates), 'original candidates omitted, reordered or replaced')
    _need([row['source_ref'] for row in model['source_coverage']] == list(sources), 'original sources omitted, reordered or replaced')
    for covered in model['source_coverage']:
        source = sources[covered['source_ref']]
        expected = [fact['fact_ref'] for fact in value['facts'] if any(c['source_ref'] == source['source_ref'] for c in fact['source_refs'])]
        _need(covered['fact_refs'] == expected, 'source facts omitted or substituted')
        _need(covered['status'] != 'reviewed' or source['readability'] == 'readable', 'unreadable source cannot be declared reviewed')
    assessments = _index(model['candidate_assessments'], 'candidate_ref')
    for row in assessments.values():
        candidate = candidates[row['candidate_ref']]
        _need(row['capability_refs'] == candidate['capability_refs'] and row['requirement_refs'] == reqs, 'original applicability scope changed')
        _refs(row['fact_refs'], facts); _refs(row['source_refs'], sources); _refs(row['unmet_requirement_refs'], reqs)
        needed = {caps[ref]['source_ref'] for ref in candidate['capability_refs']}
        needed.update(c['source_ref'] for ref in row['fact_refs'] for c in facts[ref]['source_refs'])
        references = set(row['source_refs'])
        contextual = references - needed
        _need(needed <= references and all(sources[ref]['kind'] in {'packet', 'evidence'}
            and sources[ref]['readability'] == 'readable'
            and next(item['status'] for item in model['source_coverage'] if item['source_ref'] == ref) == 'reviewed'
            for ref in contextual), 'assessment source references do not match original facts/capabilities or reviewed context')
        def declared_requirement(ref):
            requirement = requirements[ref]
            unit_known = (requirement['unit'] is not None
                or requirement['value_type'] in {'string', 'boolean'}
                or requirement['time_mode'] == 'not_required')
            return unit_known and requirement['semantics'] != 'unknown'
        covered = {link['requirement_ref'] for link in candidate['requirement_mappings']
            if declared_requirement(link['requirement_ref'])}
        if row['candidate_applicability'] == 'applicable':
            _need(set(reqs) <= covered and not row['unmet_requirement_refs'], 'applicable candidate lacks actual declared field ports')
        else:
            _need(set(reqs) - covered <= set(row['unmet_requirement_refs']), 'unprovided requirements cannot be hidden')
        claims = _index(candidate['claims'], 'claim_ref')
        for refutation in row['refutations']:
            _need(refutation['claim_ref'] in claims, 'invented original candidate claim')
            _refs(refutation['fact_refs'], facts)
            _need(set(refutation['fact_refs']) <= set(row['fact_refs']), 'refutation facts absent from assessment')
        _need((row['evidence_integrity'] == 'contradictory') == bool(row['refutations']), 'candidate refutation is not a global fact conflict')
        if row['evidence_integrity'] == 'supported':
            _need(any(facts[ref]['kind'] != 'inference' for ref in row['fact_refs']), 'support cannot consist only of assumptions/inference')
            _need(all(any(facts[ref]['kind'] != 'inference' and any(c['source_ref'] == caps[cap_ref]['source_ref']
                for c in facts[ref]['source_refs']) for ref in row['fact_refs'])
                for cap_ref in candidate['capability_refs']), 'supported capability lacks its original declaration facts')
    conflicts = _index(model['conflicts'], 'conflict_ref')
    for known in value['known_conflicts']:
        _need(conflicts.get(known['conflict_ref']) == known, 'known original conflict cannot be omitted or rewritten')
    for conflict in model['conflicts']:
        _conflict(conflict, facts)
    for index, left in enumerate(value['facts']):
        for right in value['facts'][index + 1:]:
            if _comparable(left, right) and not _same_value(left['value'], right['value']):
                _need(any({left['fact_ref'], right['fact_ref']} <= set(row['fact_refs']) for row in model['conflicts']), 'exclusive original facts cannot be silently omitted')
    gaps = _index(model['knowledge_gaps'], 'requirement_ref')
    _need(set(gaps) == set(_missing(value)), 'missing current facts cannot be hidden or fabricated')
    recommendation = model['recommendation']; _refs(recommendation['fact_refs'], facts)
    selected = recommendation['candidate_ref']
    if selected is not None:
        _need(selected in assessments, 'recommendation is not an original candidate')
        row = assessments[selected]
        _need(row['candidate_applicability'] == 'applicable' and row['evidence_integrity'] == 'supported'
            and not row['missing_information'] and recommendation['fact_refs']
            and set(recommendation['fact_refs']) <= set(row['fact_refs']), 'selected candidate is inapplicable, unsupported or incomplete')
        kinds = {caps[ref]['action_kind'] for ref in candidates[selected]['capability_refs']}
        intent = value['decision_intent']
        _need((intent == 'information_acquisition' and kinds == {'information_acquisition'})
            or (intent == 'operational_action' and 'operational_action' in kinds)
            or (intent == 'business_conclusion' and kinds == {'analysis'}), 'intent cannot relabel an operational action as information gathering')
        if intent != 'information_acquisition':
            for requirement in value['requirements']:
                matches = set(_matching_fact_refs(requirement, value['facts']))
                if matches:
                    _need(bool(matches & set(row['fact_refs']) & set(recommendation['fact_refs'])),
                        'selected assessment and recommendation omit a matching original requirement fact')
    return model


def derive_result(inputs, model_value):
    value = check_input(inputs); model = check_model_value(value, model_value)
    missing = _missing(value); facts = _index(value['facts'], 'fact_ref')
    conflicts = [_conflict(row, facts) for row in model['conflicts']]
    complete = all(row['status'] == 'reviewed' for row in model['source_coverage'])
    def incomplete_fact(fact):
        if fact['kind'] not in {'observation', 'document_statement'}:
            return False
        record_requirement = any(req['time_mode'] == 'not_required'
            and fact['fact_ref'] in _matching_fact_refs(req, [fact]) for req in value['requirements'])
        unit_known = fact['unit'] is not None or type(fact['value']) in {str, bool} or record_requirement
        time_known = None not in _window(fact['time_scope']) or record_requirement
        return not unit_known or not time_known or fact['semantics'] == 'unknown'
    unknown = bool(missing) or not value['facts'] or not complete or any(incomplete_fact(fact) for fact in value['facts'])
    unknown = unknown or any(_uncertain_pair(left, right) for index, left in enumerate(value['facts'])
        for right in value['facts'][index + 1:])
    consistency = 'contradictory' if conflicts else 'unknown' if unknown else 'consistent'
    ready = model['recommendation']['candidate_ref'] is not None and complete and not conflicts
    if value['decision_intent'] != 'information_acquisition':
        ready = ready and not missing and consistency == 'consistent'
    state = 'conflicting_evidence' if conflicts else 'ready_for_review' if ready else 'needs_information'
    return _parse(DecisionResult, {'schema': RESULT, 'input_hash': input_hash(value),
        'decision_intent': value['decision_intent'], 'scope': value['scope'], 'model_value': model,
        'review_state': state, 'fact_consistency': consistency,
        'current_facts_sufficient': not missing and complete and consistency == 'consistent',
        'missing_requirement_refs': missing, 'conflicts': conflicts, 'can_dispatch': False,
        'permissions_verified': False, 'source_authenticity_verified': False,
        'current_outcome_verified': False, 'goal_completion_verified': False, 'data_reception_verified': False})


def check_result(inputs, raw):
    _need(type(raw) is dict, 'result must be an original JSON object')
    for key in ('can_dispatch', 'permissions_verified', 'source_authenticity_verified',
            'current_outcome_verified', 'goal_completion_verified', 'data_reception_verified'):
        _need(type(raw.get(key)) is bool and raw[key] is False, 'decision proposals never grant execution or verified outcomes')
    value = _parse(DecisionResult, raw)
    expected = derive_result(inputs, value['model_value'])
    _need(_json(value) == _json(expected), 'platform-derived decision status or original evidence changed')
    return value


def input_schema():
    return copy.deepcopy(DecisionInput.model_json_schema())


def model_value_schema():
    return copy.deepcopy(ModelValue.model_json_schema())


def output_schema():
    return copy.deepcopy(DecisionResult.model_json_schema())
