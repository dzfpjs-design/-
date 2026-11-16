"""Strict fixed-key classification and an UNAPPLIED review diff, never execution.

Raw model decisions remain unchanged. Platform-derived group/question records
are separate and require the current source, human review and CAS write gates.
No old group decoder, partial-answer fallback, I/O, repair or confirmation lives
here. A self-consistent plan cannot establish provenance: the server source gate
must independently call plan.check_plan with actual cold source and saved draft.
"""
import copy
import hashlib
import json
import re

from . import report_business_catalogue_allocation_plan_v213 as planning
from . import report_business_catalogue_edit_v211 as edit
from . import report_indexed_inputs_v178 as codec
from .report_evidence_plan import _safe

WIRE = 'report-business-catalogue-allocation-output-v213'
PROJECTION = 'report-business-catalogue-allocation-projection-v213'
APPLY = 'report-business-catalogue-allocation-apply-proposal-v213'
MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_PROJECTION_BYTES = 16 * 1024 * 1024
_KEY = re.compile(r'^p[0-9]{6}$')


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
        'additionalProperties': False}


def _text(value, maximum):
    planning.need(type(value) is str and bool(value.strip()) and len(value) <= maximum, 'decision_text')
    _safe(value)


@planning.checked
def schema_for_keys(decision_keys, business_keys):
    """Shape-only builder; actual ownership/source is never asserted here."""
    planning.need(type(decision_keys) is list and 1 <= len(decision_keys) <= planning.MAX_BATCH_ITEMS
        and all(type(key) is str and _KEY.fullmatch(key) is not None
            and int(key[1:]) < codec.MAX_INPUTS for key in decision_keys)
        and decision_keys == sorted(set(decision_keys)), 'decision_keys')
    planning.need(type(business_keys) is list and 1 <= len(business_keys) <= 64
        and all(type(key) is str and edit.KEY.fullmatch(key) is not None for key in business_keys)
        and len(set(business_keys)) == len(business_keys), 'business_keys')
    options = []
    for disposition in ('assign', 'background', 'pending'):
        question_kind = ['ownership', 'missing_info'] if disposition == 'pending' else ['missing_info']
        question = _object({'kind': {'type': 'string', 'enum': question_kind},
            'question': {'type': 'string', 'minLength': 1, 'maxLength': 4000}})
        recipients = {'type': 'array', 'uniqueItems': True, 'maxItems': len(business_keys),
            'items': {'type': 'string', 'enum': list(business_keys)}}
        if disposition == 'assign':
            recipients['minItems'] = 1
        if disposition == 'background':
            recipients['maxItems'] = 0
        questions = {'type': 'array', 'maxItems': 4, 'items': question}
        if disposition == 'pending':
            questions['minItems'] = 1
            questions['contains'] = _object({'kind': {'type': 'string', 'const': 'ownership'},
                'question': {'type': 'string', 'minLength': 1, 'maxLength': 4000}})
        options.append(_object({'disposition': {'type': 'string', 'const': disposition},
            'business_keys': recipients, 'reason': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
            'questions': questions}))
    return _object({'schema': {'type': 'string', 'const': WIRE},
        'decisions': _object({key: {'oneOf': copy.deepcopy(options)} for key in decision_keys})})


@planning.checked
def output_schema(plan, batch_ordinal):
    frozen = planning.check_embedded_plan(plan)
    batch = planning._selected(frozen, batch_ordinal)
    return schema_for_keys(batch['decision_keys'], [row['key'] for row in frozen['businesses']])


def _parse(raw_bytes, frozen, batch):
    planning.need(type(raw_bytes) is bytes and 0 < len(raw_bytes) <= MAX_WIRE_BYTES, 'wire_bytes')
    def pairs(items):
        result = {}
        for key, item in items:
            planning.need(key not in result, 'duplicate_json_key')
            result[key] = item
        return result
    def constant(_value):
        raise planning.AllocationPlanError('nonfinite_json')
    value = json.loads(raw_bytes.decode('utf-8', errors='strict'), object_pairs_hook=pairs,
        parse_constant=constant)
    _safe(value)
    planning.need(type(value) is dict and set(value) == {'schema', 'decisions'}
        and value['schema'] == WIRE and type(value['decisions']) is dict
        and set(value['decisions']) == set(batch['decision_keys']), 'exact_decisions')
    available = {row['key'] for row in frozen['businesses']}
    for key in batch['decision_keys']:
        decision = value['decisions'][key]
        planning.need(type(decision) is dict
            and set(decision) == {'disposition', 'business_keys', 'reason', 'questions'}, 'decision_fields')
        disposition, recipients, questions = (decision[field]
            for field in ('disposition', 'business_keys', 'questions'))
        planning.need(type(disposition) is str and disposition in {'assign', 'background', 'pending'},
            'disposition')
        planning.need(type(recipients) is list and len(recipients) <= len(available)
            and all(type(item) is str and item in available for item in recipients)
            and len(set(recipients)) == len(recipients), 'decision_recipients')
        planning.need((disposition != 'assign' or bool(recipients))
            and (disposition != 'background' or not recipients), 'decision_arity')
        _text(decision['reason'], 2000)
        planning.need(type(questions) is list and len(questions) <= 4, 'decision_questions')
        for question in questions:
            planning.need(type(question) is dict and set(question) == {'kind', 'question'}
                and type(question['kind']) is str
                and question['kind'] in {'ownership', 'missing_info'}, 'decision_question')
            planning.need(disposition == 'pending' or question['kind'] == 'missing_info',
                'ownership_disposition')
            _text(question['question'], 4000)
        planning.need(disposition != 'pending'
            or any(question['kind'] == 'ownership' for question in questions), 'pending_ownership_question')
    return value


@planning.checked
def parse_wire(raw_bytes, *, plan, batch_ordinal):
    frozen = planning.check_embedded_plan(plan)
    return copy.deepcopy(_parse(raw_bytes, frozen, planning._selected(frozen, batch_ordinal)))


def _derived_key(prefix, identity):
    return prefix + codec.digest(identity)[:32]


@planning.checked
def build_projection(raw_bytes, *, plan, batch_ordinal):
    frozen = planning.check_embedded_plan(plan)
    batch = planning._selected(frozen, batch_ordinal)
    raw = _parse(raw_bytes, frozen, batch)
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    source, rows = frozen['source'], []
    for position, key in zip(batch['input_positions'], batch['decision_keys']):
        decision = raw['decisions'][key]
        identity = {'plan_hash': frozen['plan_hash'], 'raw_arguments_sha256': raw_hash, 'decision_key': key}
        kind = ('pending' if decision['disposition'] == 'pending' else 'background'
            if decision['disposition'] == 'background' else 'business'
            if len(decision['business_keys']) == 1 else 'shared')
        questions, origins = [], []
        for index, question in enumerate(decision['questions']):
            question_key = _derived_key('allocq_', {**identity, 'question_index': index})
            questions.append({'key': question_key, 'kind': question['kind'], 'input_positions': [position],
                'candidate_business_keys': copy.deepcopy(decision['business_keys']),
                'question': question['question'], 'status': 'open', 'resolution': ''})
            origins.append({'key': question_key, 'decision_key': key, 'question_index': index,
                'platform_derived': True, 'model_question': copy.deepcopy(question)})
        rows.append({'decision_key': key, 'input_position': position, 'input_id': source['input_order'][position],
            'claim': copy.deepcopy(source['input_claims'][position]),
            'source_spans': [{'span_ref': ref, 'record': copy.deepcopy(source['catalog']['index'][ref])}
                for ref in source['input_claims'][position]['span_refs']],
            'before_groups': [copy.deepcopy(group) for group in frozen['base_document']['allocation_groups']
                if position in group['input_positions']],
            'before_questions': [copy.deepcopy(question) for question in frozen['base_document']['questions']
                if position in question['input_positions']],
            'raw_decision': copy.deepcopy(decision), 'proposed_group': {
                'key': _derived_key('allocg_', identity), 'kind': kind,
                'business_keys': copy.deepcopy(decision['business_keys']), 'input_positions': [position],
                'reason': decision['reason'], 'question_keys': [question['key'] for question in questions]},
            'proposed_questions': questions, 'question_origins': origins,
            'platform_derived': True, 'review_required': True})
    value = {'schema': PROJECTION, 'source_pins': copy.deepcopy(frozen['source_pins']),
        'plan_hash': frozen['plan_hash'], 'batch_hash': batch['batch_hash'], 'batch_ordinal': batch_ordinal,
        'base_draft_ref': copy.deepcopy(frozen['base_draft_ref']),
        'catalogue_review_hash': frozen['catalogue_review_hash'],
        'raw_arguments_sha256': raw_hash, 'raw_wire': copy.deepcopy(raw),
        'output_schema_hash': codec.digest(schema_for_keys(batch['decision_keys'],
            [business['key'] for business in frozen['businesses']])),
        'decisions': rows, 'batch_decisions_complete': True, 'review_required': True,
        'flags': copy.deepcopy(planning.FLAGS)}
    planning.need(len(codec.canonical(value).encode('utf-8')) <= MAX_PROJECTION_BYTES, 'projection_capacity')
    return {**value, 'projection_hash': codec.digest(value)}


@planning.checked
def check_projection(projection, raw_bytes, *, plan, batch_ordinal):
    rebuilt = build_projection(raw_bytes, plan=plan, batch_ordinal=batch_ordinal)
    planning.need(codec._same(projection, rebuilt), 'projection_mismatch')
    return rebuilt


def _available_key(proposed, available):
    key, suffix = proposed, 0
    while key in available:
        suffix += 1
        key = proposed + '_' + str(suffix)
    available.add(key)
    return key


@planning.checked
def construct_apply_document(projection, raw_bytes, *, plan, batch_ordinal, document,
        draft_ref, selected_decision_keys):
    """Construct only. Current authorization, review and atomic save are external.

    Old questions/resolutions remain byte-for-byte even when overlapping new
    suggestions. Such unresolved ownership still blocks the normal confirmation.
    """
    frozen = planning.check_embedded_plan(plan)
    projected = check_projection(projection, raw_bytes, plan=frozen, batch_ordinal=batch_ordinal)
    current = edit.validate_document(document, frozen['source_pins']['input_count'])
    reference = planning._draft(draft_ref, current)
    planning.need(codec._same(reference, frozen['base_draft_ref'])
        and codec._same(current, frozen['base_document']), 'stale_draft')
    available = {row['decision_key']: row for row in projected['decisions']}
    planning.need(type(selected_decision_keys) is list and bool(selected_decision_keys)
        and all(type(key) is str and key in available for key in selected_decision_keys)
        and selected_decision_keys == sorted(set(selected_decision_keys)), 'selected_decisions')
    selected = {available[key]['input_position'] for key in selected_decision_keys}
    updated = copy.deepcopy(current)
    groups = []
    for group in current['allocation_groups']:
        if not selected.intersection(group['input_positions']):
            groups.append(copy.deepcopy(group))
            continue
        remaining = [position for position in group['input_positions'] if position not in selected]
        if remaining:
            groups.append({**copy.deepcopy(group), 'input_positions': remaining})
    group_keys = {group['key'] for group in groups}
    question_keys = {question['key'] for question in updated['questions']}
    question_origins, changes = [], []
    for key in selected_decision_keys:
        row = available[key]
        group, questions = copy.deepcopy(row['proposed_group']), copy.deepcopy(row['proposed_questions'])
        group['key'] = _available_key(group['key'], group_keys)
        remapped = {}
        for question in questions:
            old_key = question['key']
            question['key'] = _available_key(old_key, question_keys)
            remapped[old_key] = question['key']
        group['question_keys'] = [remapped[key] for key in group['question_keys']]
        groups.append(group)
        updated['questions'].extend(questions)
        question_origins.extend([{**copy.deepcopy(origin), 'key': remapped[origin['key']]}
            for origin in row['question_origins']])
        changes.append({'decision_key': key, 'input_position': row['input_position'],
            'before_groups': copy.deepcopy(row['before_groups']), 'after_group': copy.deepcopy(group),
            'added_questions': copy.deepcopy(questions), 'platform_derived': True})
    updated['allocation_groups'] = groups
    updated = edit.validate_document(updated, frozen['source_pins']['input_count'])
    value = {'schema': APPLY, 'plan_hash': frozen['plan_hash'],
        'projection_hash': projected['projection_hash'], 'base_draft_ref': reference,
        'selected_decision_keys': copy.deepcopy(selected_decision_keys),
        'before_document_hash': codec.digest(current), 'after_document_hash': codec.digest(updated),
        'document': updated, 'changes': changes, 'question_origins': question_origins,
        'validation': planning.catalogue_validation(updated, frozen['source_pins']['input_count'],
            name_origin=frozen['name_origin']), 'review_required': True, 'flags': copy.deepcopy(planning.FLAGS)}
    return {**value, 'apply_proposal_hash': codec.digest(value)}
