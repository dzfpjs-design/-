"""UNINTEGRATED pure wire protocols and deterministic platform-ID adapters.

Public functions: build/check_map_projection(wire/projection, *, binding,
expected_binding, wire_sha256, catalog, allowed_span_refs), and the equivalent
build/check_reduce_projection with context and map_inputs. Each map_inputs item
is exactly {wire,binding,expected_binding,wire_sha256,allowed_span_refs,projection}.

The caller must authenticate, verify the original journal bytes/receipt and get
expected_binding plus each per-Map allowlist from its immutable authorized plan.
wire_sha256 is that verified ORIGINAL BYTE hash, never a JSON reserialization.
This module cannot prove provider delivery, permissions, byte authenticity,
semantic correctness or a usable user flow. It does no network, DB, journal,
file, budget, Job, review or API work. Hashes alone do not authorize anything.

Explicit multi-step cycles retain the old semantics; self-edges remain invalid.
This is not a DAG/execution proof. No old model/validator is changed. Old V4 is
used only as a labelled internal validation view, never as the original wire.
"""
import copy
import json
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import digest
from .report_evidence_journal import JournalBinding
from .report_evidence_plan import EvidenceClaim, check_evidence_source, resolve_evidence, validate_map_result
from .report_evidence_semantics import (SpanCitation, SpanObject, SpanRelationship, SpanEvidence,
    validate_evidence_merge)
from .report_full_reading_semantics import Closed


DERIVATION = 'report-evidence-platform-ids-v1'
MAP_WIRE = 'report-evidence-map-v3'
MAP_PROJECTION = 'report-evidence-map-projection-v3'
CANDIDATE_WIRE = 'report-evidence-candidate-wire-v5'
CANDIDATE_PROJECTION = 'report-evidence-candidate-projection-v5'
REDUCE_PROJECTION = 'report-evidence-reduce-projection-v5'
MAX_WIRE_VALUE_BYTES = 256 * 1024
MAX_PROJECTION_BYTES = 8 * 1024 * 1024
_HASH = re.compile(r'^[a-f0-9]{64}$')
Kind = EvidenceClaim.model_fields['kind'].annotation
ClaimStatus = EvidenceClaim.model_fields['status'].annotation
ClaimRefs = EvidenceClaim.model_fields['span_refs'].annotation
Index = Annotated[int, Field(strict=True, ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=100)]
Rule = Annotated[str, Field(max_length=1000)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class MapClaimWireV3(Closed):
    kind: Kind
    statement: str = Field(min_length=1, max_length=2000)
    span_refs: ClaimRefs = Field(max_length=16)
    status: ClaimStatus
    explanation: str = Field(max_length=1000)

    @model_validator(mode='after')
    def original_five_constraints(self):
        # Explicit internal validation input; it is never original model wire.
        EvidenceClaim.model_validate({'key': 'wire_constraint_check', **self.model_dump()})
        return self


class MapResultWireV3(Closed):
    schema: Literal['report-evidence-map-v3']
    claims: list[MapClaimWireV3] = Field(max_length=80)


class ParticipantWireV5(Closed):
    agent: Text
    responsibility: str = Field(min_length=1, max_length=1000)
    evidence: SpanEvidence


class InteractionWireV5(Closed):
    from_participant_index: Index
    to_participant_index: Index
    purpose: str = Field(min_length=1, max_length=1000)
    condition: Rule
    step_indices: list[Index] = Field(max_length=16)
    evidence: SpanEvidence


class ConditionWireV5(Closed):
    kind: Literal['precondition', 'trigger', 'stop', 'exception']
    statement: str = Field(min_length=1, max_length=1000)
    step_indices: list[Index] = Field(max_length=16)
    evidence: SpanEvidence


class CoordinationWireV5(Closed):
    kind: Literal['sequence', 'branch', 'parallel', 'unspecified']
    step_indices: list[Index] = Field(max_length=16)
    start_rule: Rule
    wait_rule: Rule
    join_rule: Rule
    evidence: SpanEvidence


class StepWireV5(Closed):
    name: str = Field(min_length=1, max_length=120)
    purpose: Rule
    agent: Literal['业务智能体', '多模感知智能体', '数据管理智能体', '人工确认']
    role: str = Field(max_length=100)
    output: str = Field(min_length=1, max_length=1000)
    next_index: Index | None
    branch_condition: str = Field(max_length=500)
    else_index: Index | None
    citations: list[SpanCitation] = Field(max_length=4)
    inference_reason: str = Field(max_length=500)


class BusinessWireV5(Closed):
    operation: Literal['upsert']
    original_name: Literal['']
    name: Text
    trigger: Rule
    output: Rule
    steps: list[StepWireV5] = Field(max_length=16)
    citations: list[SpanCitation] = Field(max_length=4)
    inference_reason: str = Field(max_length=500)
    participants: list[ParticipantWireV5] = Field(max_length=40)
    interactions: list[InteractionWireV5] = Field(max_length=40)
    conditions: list[ConditionWireV5] = Field(max_length=40)
    coordination: list[CoordinationWireV5] = Field(max_length=40)


class CandidateWireV5(Closed):
    schema: Literal['report-evidence-candidate-wire-v5']
    source_hash: Hash
    catalog_hash: Hash
    domain: str = Field(max_length=200)
    global_claims: list[MapClaimWireV3] = Field(max_length=160)
    objects: list[SpanObject] = Field(max_length=40)
    relationships: list[SpanRelationship] = Field(max_length=30)
    businesses: list[BusinessWireV5] = Field(max_length=20)
    questions: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(max_length=30)


class ReconciliationWireV5(Closed):
    input_position: Annotated[int, Field(strict=True, ge=0, le=1919)]
    disposition: Literal['kept', 'duplicate', 'uncertain']
    output_kind: Literal['global_claims', 'businesses', 'objects', 'relationships', 'questions']
    output_index: Index
    reason: str = Field(min_length=1, max_length=1000)


class ReduceResultWireV5(Closed):
    result: CandidateWireV5
    reconciliation: list[ReconciliationWireV5] = Field(max_length=1920)


def _size(value, maximum):
    try:
        size = len(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8'))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Wire/projection is not safe JSON') from None
    if size > maximum: raise ValueError('Wire/projection capacity exceeded')


def _wire(value, model):
    if type(value) is not dict: raise ValueError('Wire must be an original JSON object')
    result = model.model_validate(value).model_dump()
    if result != value: raise ValueError('Wire changed during validation')
    _size(result, MAX_WIRE_VALUE_BYTES)
    return result


def _binding(binding, expected_binding, wire_sha256, catalog, allowed_span_refs, stage):
    if type(binding) is not dict or type(expected_binding) is not dict:
        raise ValueError('Expected and actual bindings are required')
    actual = JournalBinding.model_validate(binding).model_dump()
    expected = JournalBinding.model_validate(expected_binding).model_dump()
    if actual != binding or expected != expected_binding or actual != expected:
        raise ValueError('Actual binding differs from frozen expected binding')
    if type(wire_sha256) is not str or not _HASH.fullmatch(wire_sha256):
        raise ValueError('Original wire byte hash is required')
    if (stage == 'map' and not re.fullmatch(r'map-(?:[1-9]|1[0-9]|2[0-4])', actual['stage'])
            or stage == 'reduce' and actual['stage'] != 'reduce'):
        raise ValueError('Binding stage differs')
    check_evidence_source(catalog)
    resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    if (actual['source_hash'] != catalog['descriptor']['source_hash']
            or actual['catalog_hash'] != catalog['descriptor']['catalog_hash']):
        raise ValueError('Binding source/catalog differs')
    return actual


def _metadata(binding, wire_sha256, wire):
    return {'derivation_version': DERIVATION, 'binding': copy.deepcopy(binding),
        'binding_hash': digest(binding), 'wire_sha256': wire_sha256, 'wire_value_hash': digest(wire)}


def _id(prefix, metadata, path):
    return prefix + digest({'domain': DERIVATION, 'binding_hash': metadata['binding_hash'],
        'wire_sha256': metadata['wire_sha256'], 'path': path})[:32]


def _seal(value):
    _size(value, MAX_PROJECTION_BYTES)
    return {**value, 'projection_hash': digest(value)}


def build_map_projection(wire, *, binding, expected_binding, wire_sha256, catalog, allowed_span_refs):
    """Requires caller-verified bytes and a frozen per-batch allowlist."""
    actual = _binding(binding, expected_binding, wire_sha256, catalog, allowed_span_refs, 'map')
    value = _wire(wire, MapResultWireV3); metadata = _metadata(actual, wire_sha256, value)
    claims = [{'key': _id('mc_', metadata, f'/claims/{position}'), **copy.deepcopy(claim)}
        for position, claim in enumerate(value['claims'])]
    internal_v1_view = {'schema': 'report-evidence-map-v1', 'claims': claims}
    if validate_map_result(internal_v1_view, catalog, allowed_span_refs) != internal_v1_view:
        raise ValueError('Internal Map validation view changed')
    return _seal({'schema': MAP_PROJECTION, **metadata, 'allowed_span_refs_hash': digest(allowed_span_refs), 'claims': claims})


def check_map_projection(projection, wire, **inputs):
    expected = build_map_projection(wire, **inputs)
    if type(projection) is not dict or projection != expected:
        raise ValueError('Map projection does not cold-rebuild from original wire and frozen context')
    return expected


def _index(value, items):
    if type(value) is not int or not 0 <= value < len(items):
        raise ValueError('Index is outside its business-local array')
    return items[value]


def _indices(values, items):
    if len(values) != len(set(values)): raise ValueError('Duplicate explicit indices')
    return [_index(value, items) for value in values]


def candidate_with_ids(value, id_for_path):
    """Internal structural adapter; the caller validates wire, scope and meaning.

    An explicit callback lets human drafts use a separate identity domain,
    without pretending that a human value hash is a provider wire byte hash.
    """
    result = copy.deepcopy(value)
    result['schema'] = CANDIDATE_PROJECTION
    for position, claim in enumerate(result['global_claims']):
        claim['key'] = id_for_path('gc_', f'/result/global_claims/{position}')
    for business_index, business in enumerate(result['businesses']):
        base = f'/result/businesses/{business_index}'
        steps = [id_for_path('st_', base + f'/steps/{i}') for i in range(len(business['steps']))]
        people = [id_for_path('pa_', base + f'/participants/{i}') for i in range(len(business['participants']))]
        for i, step in enumerate(business['steps']):
            step['key'] = steps[i]
            for source, target in (('next_index', 'next_key'), ('else_index', 'else_key')):
                index = step.pop(source)
                step[target] = 'end' if index is None else _index(index, steps)
        for i, participant in enumerate(business['participants']): participant['key'] = people[i]
        for collection, prefix in (('interactions', 'in_'), ('conditions', 'co_'), ('coordination', 'cd_')):
            for i, item in enumerate(business[collection]):
                item['key'] = id_for_path(prefix, base + f'/{collection}/{i}')
                item['step_keys'] = _indices(item.pop('step_indices'), steps)
                if collection == 'interactions':
                    item['from_participant'] = _index(item.pop('from_participant_index'), people)
                    item['to_participant'] = _index(item.pop('to_participant_index'), people)
    return result


def _candidate(value, metadata):
    return candidate_with_ids(value, lambda prefix, path: _id(prefix, metadata, path))


def _ordered_refs(catalog, used):
    refs = []
    for source in catalog['descriptor']['ordered_source_refs']:
        number = 1
        while (ref := f'{source}/s{number}') in catalog['index']:
            if ref in used: refs.append(ref)
            number += 1
    if set(refs) != used: raise ValueError('Reduce evidence is outside frozen catalog')
    return refs


def _map_inputs(map_inputs, binding, catalog):
    if type(map_inputs) is not list or not 1 <= len(map_inputs) <= 24:
        raise ValueError('Reduce requires one through twenty-four complete Maps')
    inputs, proofs, calls, generation = {}, [], {binding['reserved_call_id']}, None
    fields = {'wire', 'binding', 'expected_binding', 'wire_sha256', 'allowed_span_refs', 'projection'}
    for position, item in enumerate(map_inputs):
        if type(item) is not dict or set(item) != fields: raise ValueError('Map input context is not closed')
        projection = check_map_projection(item['projection'], item['wire'], binding=item['binding'],
            expected_binding=item['expected_binding'], wire_sha256=item['wire_sha256'], catalog=catalog,
            allowed_span_refs=item['allowed_span_refs'])
        actual = projection['binding']
        if (actual['stage'] != f'map-{position + 1}' or any(actual[key] != binding[key]
                for key in ('actor_id', 'workspace_id', 'draft_id', 'source_hash', 'catalog_hash'))
                or actual['reserved_call_id'] in calls):
            raise ValueError('Map order, scope or original call differs')
        if generation is not None and generation != actual['generation_hash']:
            raise ValueError('Mixed Map generation contracts')
        generation = actual['generation_hash']; calls.add(actual['reserved_call_id'])
        proofs.append({'stage': actual['stage'], 'wire_sha256': projection['wire_sha256'],
            'projection_hash': projection['projection_hash']})
        for claim_index, claim in enumerate(projection['claims']):
            inputs[f'map-{position + 1}/claims/{claim_index}'] = copy.deepcopy(claim)
    if not inputs: raise ValueError('All Maps are empty; no reducer result is supported')
    return inputs, proofs


def build_reduce_projection(wire, *, binding, expected_binding, wire_sha256, catalog,
                            allowed_span_refs, context, map_inputs):
    actual = _binding(binding, expected_binding, wire_sha256, catalog, allowed_span_refs, 'reduce')
    value = _wire(wire, ReduceResultWireV5); metadata = _metadata(actual, wire_sha256, value)
    inputs, proofs = _map_inputs(map_inputs, actual, catalog); order = list(inputs)
    used = {ref for claim in inputs.values() for ref in claim['span_refs']}
    if allowed_span_refs != _ordered_refs(catalog, used):
        raise ValueError('Reduce allowlist is not exactly the provided Maps actual evidence union')
    result = _candidate(value['result'], metadata)
    positions = [item['input_position'] for item in value['reconciliation']]
    if len(positions) != len(set(positions)) or set(positions) != set(range(len(order))):
        raise ValueError('Reconciliation must cover every fixed input position exactly once')
    reconciliation = [{'input_id': _index(item['input_position'], order),
        **{key: copy.deepcopy(field) for key, field in item.items() if key != 'input_position'}}
        for item in value['reconciliation']]
    internal_v4_view = {'result': {**copy.deepcopy(result), 'schema': 'report-evidence-candidate-v4'},
        'reconciliation': reconciliation}
    if validate_evidence_merge(internal_v4_view, catalog, allowed_span_refs, context, inputs) != internal_v4_view:
        raise ValueError('Internal V4 validation view changed')
    return _seal({'schema': REDUCE_PROJECTION, **metadata, 'map_proofs': proofs, 'input_order': order,
        'input_items_hash': digest(inputs), 'allowed_span_refs_hash': digest(allowed_span_refs), 'context_hash': digest(context),
        'result': result, 'reconciliation': reconciliation})


def check_reduce_projection(projection, wire, **inputs):
    expected = build_reduce_projection(wire, **inputs)
    if type(projection) is not dict or projection != expected:
        raise ValueError('Reduce projection does not cold-rebuild from original wire and frozen context')
    return expected
