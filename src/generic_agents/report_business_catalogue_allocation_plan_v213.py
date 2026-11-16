"""Pure fixed-key batch planning over caller-held, independently checked source.

The embedded inventory is SERVER ONLY. Only batch_disclosure() is intended as
one component of a future provider request, whose actual bytes/context/cost
still require the unchanged native gates. Hashes prove neither ownership nor
human review. In particular callers must supply the actual partial name origin
when the durable draft has that origin mode; None is not an approval bypass.
"""
import copy
import re
from functools import wraps

from . import report_business_catalogue_edit_v211 as edit
from . import report_business_catalogue_wire_v211 as source_wire
from . import report_indexed_inputs_v178 as codec

SCHEMA = 'report-business-catalogue-allocation-plan-v213'
BATCH = 'report-business-catalogue-allocation-batch-v213'
DISCLOSURE = 'report-business-catalogue-allocation-disclosure-v213'
EVIDENCE = 'report-business-catalogue-allocation-batch-evidence-v213'
MAX_BATCH_ITEMS = 80
MAX_DISCLOSURE_BYTES = 900000
MAX_PLAN_BYTES = 64 * 1024 * 1024
_HASH = re.compile(r'^[a-f0-9]{64}$')
_DRAFT_FIELDS = {'id', 'workspace_id', 'actor_id', 'version', 'record_hash', 'origin_hash', 'document_hash'}
FLAGS = {'machine_only': True, 'human_review_verified': False, 'current_iam_verified': False,
    'source_ownership_verified': False, 'actual_paid_verified': False,
    'semantic_quality_verified': False, 'human_confirmed': False,
    'can_quote': False, 'can_apply': False, 'can_confirm': False,
    'can_publish': False, 'dispatch_allowed': False}


class AllocationPlanError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('业务归属批计划不符合合同，原稿保留：' + code)


def need(condition, code):
    if not condition:
        raise AllocationPlanError(code)


def checked(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AllocationPlanError:
            raise
        except (TypeError, ValueError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise AllocationPlanError('invalid_structure') from None
    return invoke


def _positions(values, count):
    need(type(values) is list and len(values) <= count, 'positions')
    previous = -1
    for value in values:
        need(type(value) is int and previous < value < count, 'positions')
        previous = value
    return copy.deepcopy(values)


def _draft(reference, document):
    need(type(reference) is dict and set(reference) == _DRAFT_FIELDS, 'draft_ref')
    need(all(type(reference[key]) is str and 0 < len(reference[key]) <= 128
        and reference[key] == reference[key].strip() and '\x00' not in reference[key]
        for key in ('id', 'workspace_id', 'actor_id')), 'draft_identity')
    need(type(reference['version']) is int and 1 <= reference['version'] <= 2147483647, 'draft_version')
    need(all(type(reference[key]) is str and _HASH.fullmatch(reference[key]) is not None
        for key in ('record_hash', 'origin_hash', 'document_hash')), 'draft_hash')
    need(reference['document_hash'] == codec.digest(document), 'draft_document_hash')
    return copy.deepcopy(reference)


def _name_origin(value, count):
    if value is None:
        return None
    need(type(value) is dict and value.get('schema') == 'report-business-catalogue-partial-projection-v212'
        and type(value.get('input_count')) is int and value['input_count'] == count
        and type(value.get('issues')) is list, 'name_origin')
    # This compact origin is a frozen input, not a replacement for source cold validation.
    origin = {'schema': value['schema'], 'input_count': count,
        'issues': copy.deepcopy(value['issues']),
        'draft_seed': edit.validate_document(value['draft_seed'], count)}
    codec.canonical(origin)
    return origin


@checked
def catalogue_validation(document, input_count, *, name_origin=None):
    """Only directory-name readiness; ordinary unassigned inputs are valid work.

    No claim of actual review or of a trusted origin mode is made here.
    """
    origin = _name_origin(name_origin, input_count)
    validation = (edit.partial_validation(document, origin) if origin is not None
        else edit.validation(document, input_count))
    blockers = [copy.deepcopy(row) for row in validation['issues']
        if row['code'] in {'no_businesses', 'business_name', 'partial_name_requires_human_decision'}]
    return {**validation, 'catalogue_blockers': blockers,
        'catalogue_structurally_ready': not blockers, 'human_review_verified': False}


def decision_key(position):
    need(type(position) is int and 0 <= position < codec.MAX_INPUTS, 'decision_position')
    return f'p{position:06d}'


def _core(pins, reference, review_hash, businesses, positions, claims, catalog):
    references, seen = [], set()
    for position in positions:
        for ref in claims[position]['span_refs']:
            if ref not in seen:
                seen.add(ref)
                references.append([ref, catalog['index'][ref]['text']])
    return {'schema': DISCLOSURE, 'source_pins': copy.deepcopy(pins),
        'base_draft_ref_hash': codec.digest(reference), 'catalogue_review_hash': review_hash,
        'businesses': copy.deepcopy(businesses),
        'claims': [{'decision_key': decision_key(position), 'claim': copy.deepcopy(claims[position])}
            for position in positions], 'spans': references}


def _batch(ordinal, positions, pins, reference, review_hash, businesses, claims, catalog):
    core = _core(pins, reference, review_hash, businesses, positions, claims, catalog)
    value = {'schema': BATCH, 'batch_ordinal': ordinal, 'input_positions': list(positions),
        'decision_keys': [decision_key(position) for position in positions],
        'disclosure_hash': codec.digest(core)}
    # Fixed-length envelope hashes make this an exact DISCLOSURE byte screen,
    # still deliberately not an estimate of the complete provider HTTP request.
    value['batch_hash'] = codec.digest({**value, 'source_pins': pins,
        'base_draft_ref': reference, 'catalogue_review_hash': review_hash})
    value['disclosure_bytes'] = len(codec.canonical({**core, 'batch_hash': value['batch_hash'],
        'plan_hash': '0' * 64, 'disclosure_hash': value['disclosure_hash']}).encode('utf-8'))
    return value


@checked
def build_plan(*, indexed, input_claims, source_snapshot_hash, catalog, input_order,
        draft_ref, document, selection=None, max_batch_items=MAX_BATCH_ITEMS,
        max_disclosure_bytes=MAX_DISCLOSURE_BYTES, name_origin=None):
    frozen = source_wire._source(indexed, input_claims, source_snapshot_hash, catalog)
    count = frozen['input_count']
    need(type(input_order) is list and len(input_order) == count
        and all(type(value) is str and value.strip() == value and 0 < len(value) <= 512
            and '\x00' not in value for value in input_order)
        and len(set(input_order)) == count, 'original_input_order')
    document = edit.validate_document(document, count)
    reference = _draft(draft_ref, document)
    origin = _name_origin(name_origin, count)
    validation = catalogue_validation(document, count, name_origin=origin)
    need(validation['catalogue_structurally_ready'], 'catalogue_requires_review')
    need(type(max_batch_items) is int and 1 <= max_batch_items <= MAX_BATCH_ITEMS, 'batch_capacity')
    need(type(max_disclosure_bytes) is int and 1 <= max_disclosure_bytes <= MAX_DISCLOSURE_BYTES,
        'disclosure_capacity')
    if selection is None:
        selection = {'mode': 'unassigned_and_pending', 'positions': []}
    need(type(selection) is dict and set(selection) == {'mode', 'positions'}
        and type(selection['mode']) is str
        and selection['mode'] in {'unassigned_and_pending', 'explicit'}, 'selection')
    explicit = _positions(selection['positions'], count)
    if selection['mode'] == 'unassigned_and_pending':
        need(not explicit, 'default_selection_has_positions')
        targets = sorted(set(validation['unassigned_positions'])
            | set(validation['pending_ownership_positions']))
    else:
        targets = explicit
    pins = {'source_snapshot_hash': source_snapshot_hash, 'indexed_inputs_hash': frozen['inputs_hash'],
        'input_claims_hash': frozen['input_claims_hash'], 'input_order_hash': codec.digest(input_order),
        'catalog_hash': catalog['descriptor']['catalog_hash'], 'input_count': count}
    businesses = copy.deepcopy(document['businesses'])
    review_hash = codec.digest({'base_draft_ref': reference, 'businesses': businesses,
        'name_origin_hash': codec.digest(origin)})
    batches, current = [], []
    for position in targets:
        proposed = current + [position]
        batch = _batch(len(batches), proposed, pins, reference, review_hash, businesses, input_claims, catalog)
        if len(proposed) > max_batch_items or batch['disclosure_bytes'] > max_disclosure_bytes:
            need(bool(current), 'single_input_disclosure_capacity')
            batches.append(_batch(len(batches), current, pins, reference, review_hash,
                businesses, input_claims, catalog))
            current = [position]
            batch = _batch(len(batches), current, pins, reference, review_hash, businesses, input_claims, catalog)
            need(batch['disclosure_bytes'] <= max_disclosure_bytes, 'single_input_disclosure_capacity')
        else:
            current = proposed
    if current:
        batches.append(_batch(len(batches), current, pins, reference, review_hash, businesses, input_claims, catalog))
    value = {'schema': SCHEMA, 'status': 'ready' if targets else 'no_work', 'source_pins': pins,
        'base_draft_ref': reference, 'base_document': document, 'name_origin': origin,
        'catalogue_review_hash': review_hash, 'businesses': businesses,
        'selection': copy.deepcopy(selection), 'target_positions': targets,
        'max_batch_items': max_batch_items, 'max_disclosure_bytes': max_disclosure_bytes,
        'batches': batches, 'source': {'indexed': frozen, 'input_claims': copy.deepcopy(input_claims),
            'source_snapshot_hash': source_snapshot_hash, 'catalog': copy.deepcopy(catalog),
            'input_order': copy.deepcopy(input_order)}, 'flags': copy.deepcopy(FLAGS)}
    need(len(codec.canonical(value).encode('utf-8')) <= MAX_PLAN_BYTES, 'plan_capacity')
    return {**value, 'plan_hash': codec.digest(value)}


@checked
def check_plan(plan, **original_inputs):
    """Rebuild against independent caller inputs, not just a resealed plan."""
    rebuilt = build_plan(**original_inputs)
    need(codec._same(plan, rebuilt), 'plan_mismatch')
    return rebuilt


@checked
def check_embedded_plan(plan):
    """Self-consistency only; a source gate MUST also call check_plan(actual...)."""
    need(type(plan) is dict and type(plan.get('source')) is dict, 'plan_structure')
    return check_plan(plan, **plan['source'], draft_ref=plan['base_draft_ref'],
        document=plan['base_document'], selection=plan['selection'], max_batch_items=plan['max_batch_items'],
        max_disclosure_bytes=plan['max_disclosure_bytes'], name_origin=plan['name_origin'])


def _selected(plan, batch_ordinal):
    need(type(batch_ordinal) is int and 0 <= batch_ordinal < len(plan['batches']), 'batch_ordinal')
    return plan['batches'][batch_ordinal]


@checked
def batch_disclosure(plan, batch_ordinal):
    frozen = check_embedded_plan(plan)
    batch = _selected(frozen, batch_ordinal)
    core = _core(frozen['source_pins'], frozen['base_draft_ref'], frozen['catalogue_review_hash'],
        frozen['businesses'], batch['input_positions'], frozen['source']['input_claims'], frozen['source']['catalog'])
    return {**core, 'batch_hash': batch['batch_hash'], 'plan_hash': frozen['plan_hash'],
        'disclosure_hash': batch['disclosure_hash']}


@checked
def batch_evidence(plan, batch_ordinal):
    """SERVER ONLY: exact original identity and complete before-state for review."""
    frozen = check_embedded_plan(plan)
    batch = _selected(frozen, batch_ordinal)
    return {'schema': EVIDENCE, 'plan_hash': frozen['plan_hash'], 'batch_hash': batch['batch_hash'],
        'base_draft_ref': copy.deepcopy(frozen['base_draft_ref']),
        'entries': [{'decision_key': decision_key(position), 'input_position': position,
            'input_id': frozen['source']['input_order'][position],
            'claim': copy.deepcopy(frozen['source']['input_claims'][position]),
            'source_spans': [{'span_ref': ref,
                'record': copy.deepcopy(frozen['source']['catalog']['index'][ref])}
                for ref in frozen['source']['input_claims'][position]['span_refs']],
            'before_groups': [copy.deepcopy(group) for group in frozen['base_document']['allocation_groups']
                if position in group['input_positions']],
            'before_questions': [copy.deepcopy(question) for question in frozen['base_document']['questions']
                if position in question['input_positions']]}
            for position in batch['input_positions']]}
