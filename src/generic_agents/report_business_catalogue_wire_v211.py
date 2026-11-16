"""Pure machine catalogue over ALL caller-held, cold-checked Map claims.

Hashes and original text are platform projections, never model assertions.
This codec proves structural coverage of those Map claims, not report-wide
extraction completeness, source ownership, semantic correctness or the absence
of a hidden business merge. In particular an ``explicit`` name is at most a
literal occurrence in the supplied source, not a verified business fact.

No I/O, model call, confirmation, adoption, publication, V166 conversion or
response repair occurs here. The consumer must retain original response bytes
before decoding, including when CatalogueWireError is raised.
"""
import copy
import hashlib
import json
import re
from functools import wraps

from . import report_indexed_inputs_v178 as codec
from . import report_scoped_design_plan_v180 as planner
from .report_evidence_plan import check_evidence_source

WIRE = 'report-business-catalogue-output-v211'
PROJECTION = 'report-business-catalogue-projection-v211'
MAX_BUSINESSES = planner.MAX_SCOPES
MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_PROJECTION_BYTES = 16 * 1024 * 1024
_KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_:-]{0,127}$')
_FIELDS = {'schema', 'input_count', 'businesses', 'allocation_groups', 'questions', 'merge_suggestions'}
_BUSINESS = {'key', 'name', 'purpose', 'boundary', 'seed_positions', 'name_basis', 'reason'}
_GROUP = {'kind', 'business_keys', 'input_positions', 'reason', 'question_keys'}
_QUESTION = {'key', 'kind', 'input_positions', 'candidate_business_keys', 'question'}
_MERGE = {'business_keys', 'reason'}


class CatalogueWireError(ValueError):
    """A failed answer must be retained by its caller, never repaired/retried."""
    response_must_be_retained = True

    def __init__(self, code):
        self.code = code
        super().__init__('业务目录候选未通过完整来源与分派合同；须保留原回答，未修写、确认或调用：' + code)


def _need(condition, code):
    if not condition:
        raise CatalogueWireError(code)


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CatalogueWireError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise CatalogueWireError('invalid_structure') from None
    return checked


def _text(value, maximum, code, *, empty=False):
    _need(type(value) is str and len(value) <= maximum
        and (empty or bool(value.strip())), code)
    _need('\x00' not in value and all(ord(c) >= 32 or c in '\n\r\t' for c in value), code)
    value.encode('utf-8', errors='strict')


def _keys(value, available, code, *, minimum=0):
    _need(type(value) is list and minimum <= len(value) <= len(available)
        and all(type(key) is str and key in available for key in value)
        and len(value) == len(set(value)), code)


def _positions(value, count, code):
    _need(type(value) is list and 1 <= len(value) <= count, code)
    previous = -1
    for position in value:
        _need(type(position) is int and 0 <= position < count and previous < position, code)
        previous = position


def _source(indexed, input_claims, source_snapshot_hash, catalog=None):
    frozen = codec.check_indexed_inputs(indexed, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash)
    for claim in input_claims:
        refs = claim.get('span_refs')
        _need(type(refs) is list and all(type(ref) is str and bool(ref.strip()) for ref in refs)
            and len(refs) == len(set(refs)), 'original_span_refs')
        _text(claim.get('statement'), 2000, 'original_statement')
        _need(claim.get('status') in {'explicit', 'inferred', 'unknown'}, 'original_status')
    if catalog is not None:
        check_evidence_source(catalog)
        _need(all(ref in catalog['index'] for claim in input_claims for ref in claim['span_refs']),
            'original_reference_missing')
    return frozen


def _string(maximum, *, minimum=1):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum}


def _object(properties):
    return {'type': 'object', 'additionalProperties': False,
        'properties': properties, 'required': list(properties)}


def _literal_name(name, business, input_claims, catalog):
    # Preserve exact text across adjacent spans of ONE original body block.
    # Never concatenate separate blocks or skip a gap to invent an occurrence.
    refs = {ref for position in business['seed_positions'] for ref in input_claims[position]['span_refs']}
    records = sorted((catalog['index'][ref] for ref in refs),
        key=lambda row: (row['source_ref'], row['start'], row['end']))
    source_ref, end, text = None, None, ''
    for row in records:
        text = text + row['text'] if source_ref == row['source_ref'] and end == row['start'] else row['text']
        source_ref, end = row['source_ref'], row['end']
        if name in text:
            return True
    return False


@_checked
def schema_for_count(count):
    """Shape only, never source identity or a substitute for output_schema."""
    _need(type(count) is int and 1 <= count <= codec.MAX_INPUTS, 'schema_input_count')
    businesses = min(MAX_BUSINESSES, count)
    key = {**_string(128), 'pattern': _KEY.pattern}
    positions = {'type': 'array', 'minItems': 1, 'maxItems': count, 'uniqueItems': True,
        'items': {'type': 'integer', 'minimum': 0, 'maximum': count - 1}}
    keys = {'type': 'array', 'maxItems': businesses, 'uniqueItems': True, 'items': key}
    return _object({
        'schema': {'type': 'string', 'const': WIRE},
        'input_count': {'type': 'integer', 'const': count, 'minimum': count, 'maximum': count},
        'businesses': {'type': 'array', 'maxItems': businesses, 'items': _object({
            'key': key, 'name': _string(200), 'purpose': _string(2000, minimum=0),
            'boundary': _string(2000, minimum=0), 'seed_positions': positions,
            'name_basis': {'type': 'string', 'enum': ['explicit', 'inferred']}, 'reason': _string(2000)})},
        'allocation_groups': {'type': 'array', 'minItems': 1, 'maxItems': count, 'items': _object({
            'kind': {'type': 'string', 'enum': ['business', 'shared', 'background', 'pending']},
            'business_keys': keys, 'input_positions': positions, 'reason': _string(2000),
            'question_keys': {'type': 'array', 'maxItems': count * 4,
                'uniqueItems': True, 'items': key}})},
        'questions': {'type': 'array', 'maxItems': count * 4, 'items': _object({
            'key': key, 'kind': {'type': 'string', 'enum': ['ownership', 'missing_info']},
            'input_positions': positions, 'candidate_business_keys': keys, 'question': _string(2000)})},
        'merge_suggestions': {'type': 'array', 'maxItems': MAX_BUSINESSES,
            'items': _object({'business_keys': {**keys, 'minItems': 2}, 'reason': _string(2000)})}})


@_checked
def output_schema(indexed, *, input_claims, source_snapshot_hash):
    """Exact dynamic local schema; provider translation must not weaken this."""
    frozen = _source(indexed, input_claims, source_snapshot_hash)
    return schema_for_count(frozen['input_count'])


@_checked
def validate_candidate(value, *, indexed, input_claims, source_snapshot_hash, catalog):
    """Check candidate facts only against independently supplied original inputs.

    Original claim kind/status are machine input too. Seed-to-owner consistency
    cannot prove that several semantically distinct businesses were not hidden
    under a single suggested name. No automatic merge is performed here.
    """
    _need(catalog is not None, 'original_catalog_required')
    frozen = _source(indexed, input_claims, source_snapshot_hash, catalog)
    codec.canonical(value)
    _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == WIRE,
        'wire_fields')
    count = frozen['input_count']
    _need(type(value['input_count']) is int and value['input_count'] == count, 'input_count')
    rows = value['businesses']
    _need(type(rows) is list and len(rows) <= min(MAX_BUSINESSES, count), 'business_capacity')
    by_business, names, seeds = {}, set(), set()
    for row in rows:
        _need(type(row) is dict and set(row) == _BUSINESS, 'business_fields')
        key = row['key']
        _need(type(key) is str and _KEY.fullmatch(key) is not None and key not in by_business,
            'business_key')
        _text(row['name'], 200, 'business_name')
        _need(row['name'] == row['name'].strip() and row['name'] not in names, 'business_name')
        for field in ('purpose', 'boundary'):
            _text(row[field], 2000, 'business_' + field, empty=True)
        _text(row['reason'], 2000, 'business_reason')
        _positions(row['seed_positions'], count, 'seed_positions')
        _need(not seeds.intersection(row['seed_positions']), 'reused_business_seed')
        _need(row['name_basis'] in {'explicit', 'inferred'}, 'name_basis')
        if row['name_basis'] == 'explicit':
            _need(_literal_name(row['name'], row, input_claims, catalog),
                'explicit_name_not_literal')
        seeds.update(row['seed_positions']); names.add(row['name']); by_business[key] = row
    questions = value['questions']
    _need(type(questions) is list and len(questions) <= count * 4, 'question_capacity')
    by_question = {}
    for row in questions:
        _need(type(row) is dict and set(row) == _QUESTION, 'question_fields')
        key = row['key']
        _need(type(key) is str and _KEY.fullmatch(key) is not None and key not in by_question,
            'question_key')
        _need(row['kind'] in {'ownership', 'missing_info'}, 'question_kind')
        _positions(row['input_positions'], count, 'question_positions')
        _keys(row['candidate_business_keys'], by_business, 'question_business_keys')
        _text(row['question'], 2000, 'question_text'); by_question[key] = row
    groups = value['allocation_groups']
    _need(type(groups) is list and 1 <= len(groups) <= count, 'allocation_capacity')
    owners, linked_questions = {}, {key: set() for key in by_question}
    for index, group in enumerate(groups):
        _need(type(group) is dict and set(group) == _GROUP, 'allocation_fields')
        kind = group['kind']
        _need(kind in {'business', 'shared', 'background', 'pending'}, 'allocation_kind')
        _positions(group['input_positions'], count, 'allocation_positions')
        _keys(group['business_keys'], by_business, 'allocation_business_keys')
        _keys(group['question_keys'], by_question, 'allocation_question_keys')
        _text(group['reason'], 2000, 'allocation_reason')
        _need((kind != 'business' or len(group['business_keys']) == 1)
            and (kind != 'shared' or len(group['business_keys']) >= 2)
            and (kind != 'background' or not group['business_keys']), 'allocation_recipients')
        for question_key in group['question_keys']:
            question = by_question[question_key]
            common = set(group['input_positions']).intersection(question['input_positions'])
            _need(bool(common), 'question_group_without_position')
            linked_questions[question_key].update(common)
        for position in group['input_positions']:
            _need(position not in owners, 'duplicate_input_ownership')
            owners[position] = (index, group)
            if kind == 'pending':
                _need(any(by_question[key]['kind'] == 'ownership'
                    and position in by_question[key]['input_positions']
                    and set(group['business_keys']).issubset(by_question[key]['candidate_business_keys'])
                    for key in group['question_keys']), 'pending_requires_ownership_question')
    _need(set(owners) == set(range(count)), 'incomplete_input_coverage')
    for key, question in by_question.items():
        _need(linked_questions[key] == set(question['input_positions']), 'unlinked_question_positions')
        if question['kind'] == 'ownership':
            _need(all(owners[position][1]['kind'] == 'pending'
                for position in question['input_positions']), 'ownership_question_is_not_pending')
    for key, business in by_business.items():
        _need(all(owners[position][1]['kind'] == 'business'
            and owners[position][1]['business_keys'] == [key]
            for position in business['seed_positions']), 'business_seed_owner_mismatch')
        if not business['purpose'].strip() or not business['boundary'].strip():
            _need(any(q['kind'] == 'missing_info' and (key in q['candidate_business_keys']
                or bool(set(q['input_positions']).intersection(business['seed_positions'])))
                for q in questions), 'missing_business_information_not_questioned')
    merges = value['merge_suggestions']
    _need(type(merges) is list and len(merges) <= MAX_BUSINESSES, 'merge_capacity')
    merged_sets = set()
    for merge in merges:
        _need(type(merge) is dict and set(merge) == _MERGE, 'merge_fields')
        _keys(merge['business_keys'], by_business, 'merge_business_keys', minimum=2)
        _text(merge['reason'], 2000, 'merge_reason')
        identity = frozenset(merge['business_keys'])
        _need(identity not in merged_sets, 'duplicate_merge_suggestion'); merged_sets.add(identity)
    _need(len(codec.canonical(value).encode('utf-8')) <= MAX_WIRE_BYTES, 'wire_capacity')
    return copy.deepcopy(value)


@_checked
def parse_wire(raw_arguments_bytes, *, indexed, input_claims, source_snapshot_hash, catalog):
    _need(type(raw_arguments_bytes) is bytes and 0 < len(raw_arguments_bytes) <= MAX_WIRE_BYTES,
        'wire_bytes')
    def pairs(items):
        result = {}
        for key, item in items:
            _need(key not in result, 'duplicate_json_key'); result[key] = item
        return result
    def constant(_value):
        raise CatalogueWireError('nonfinite_json')
    value = json.loads(raw_arguments_bytes.decode('utf-8', errors='strict'),
        object_pairs_hook=pairs, parse_constant=constant)
    return validate_candidate(value, indexed=indexed, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash, catalog=catalog)


@_checked
def build_catalogue_projection(raw_arguments_bytes, *, indexed, input_claims, source_snapshot_hash, catalog):
    """Project groups to exact per-input evidence and an UNCONFIRMED V181 draft.

    ``scope_confirmation_blocked=False`` only means no structural ownership
    gap. It is not permission or human confirmation. Same-count raw model JSON
    cannot authenticate its source snapshot: the consumer MUST check the
    original request, reservation and capture binding before calling this.
    Merge suggestions never
    alter keys/owners; missing-info questions stay separate from ownership gaps.
    """
    _need(catalog is not None, 'original_catalog_required')
    value = parse_wire(raw_arguments_bytes, indexed=indexed, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash, catalog=catalog)
    scopes = {row['key']: {'scope_id': row['key'], 'name': row['name'],
        'claim_positions': [], 'context_positions': []} for row in value['businesses']}
    shared, background, pending, assignments = [], [], [], {}
    by_question = {question['key']: question for question in value['questions']}
    for group_index, group in enumerate(value['allocation_groups']):
        for position in group['input_positions']:
            kind = group['kind']; keys = group['business_keys']
            if kind == 'business':
                scopes[keys[0]]['claim_positions'].append(position)
            elif kind == 'shared':
                shared.append({'input_position': position, 'scope_ids': copy.deepcopy(keys),
                    'reason': group['reason']})
            elif kind == 'background':
                background.append({'input_position': position, 'reason': group['reason']})
            else:
                pending.append(position)
            claim = input_claims[position]
            assignments[position] = {'input_position': position, 'claim_hash': indexed['ordered_claim_hashes'][position],
                'group_index': group_index, 'kind': kind, 'business_keys': copy.deepcopy(keys),
                'question_keys': [key for key in group['question_keys']
                    if position in by_question[key]['input_positions']], 'reason': group['reason'],
                'machine_only': True, 'claim': copy.deepcopy(claim), 'span_refs': copy.deepcopy(claim['span_refs']),
                'source_spans': [{'span_ref': ref, 'record': copy.deepcopy(catalog['index'][ref])}
                    for ref in claim['span_refs']]}
    for scope in scopes.values():
        scope['claim_positions'].sort()
    pending.sort(); shared.sort(key=lambda row: row['input_position']); background.sort(key=lambda row: row['input_position'])
    ledger = {'schema': planner.LEDGER, 'source_snapshot_hash': source_snapshot_hash,
        'indexed_inputs_hash': indexed['inputs_hash'], 'input_claims_hash': indexed['input_claims_hash'],
        'input_count': indexed['input_count'], 'scopes': list(scopes.values()),
        'shared': shared, 'background': background}
    blockers = []
    if not scopes:
        blockers.append('no_business_candidates')
    if pending:
        blockers.append('pending_ownership')
    result = {'schema': PROJECTION, 'wire_schema': WIRE,
        'source_snapshot_hash': source_snapshot_hash, 'indexed_inputs_hash': indexed['inputs_hash'],
        'input_claims_hash': indexed['input_claims_hash'], 'input_count': indexed['input_count'],
        'catalog_hash': catalog['descriptor']['catalog_hash'],
        'raw_arguments_sha256': hashlib.sha256(raw_arguments_bytes).hexdigest(),
        'output_schema_hash': codec.digest(output_schema(indexed, input_claims=input_claims,
            source_snapshot_hash=source_snapshot_hash)), 'wire': value,
        'businesses': [{**copy.deepcopy(row), 'machine_only': True} for row in value['businesses']],
        'assignments': [assignments[position] for position in range(indexed['input_count'])],
        'ledger': ledger, 'ledger_hash': codec.digest(ledger), 'pending_positions': pending,
        'questions': [{**copy.deepcopy(row), 'machine_only': True} for row in value['questions']],
        'missing_info_questions': [{**copy.deepcopy(row), 'machine_only': True}
            for row in value['questions'] if row['kind'] == 'missing_info'],
        'merge_suggestions': [{**copy.deepcopy(row), 'machine_only': True}
            for row in value['merge_suggestions']], 'input_coverage_complete': True,
        'ownership_complete': not pending, 'scope_confirmation_blocked': bool(blockers),
        'scope_confirmation_blockers': blockers, 'machine_only': True,
        'source_anchors_verified': False, 'report_coverage_verified': False,
        'semantic_quality_verified': False, 'human_confirmed': False,
        'can_confirm': False, 'can_apply': False, 'can_publish': False, 'dispatch_allowed': False}
    _need(len(codec.canonical(result).encode('utf-8')) <= MAX_PROJECTION_BYTES, 'projection_capacity')
    return {**result, 'projection_hash': codec.digest(result)}


@_checked
def check_catalogue_projection(projection, raw_arguments_bytes, **original_inputs):
    expected = build_catalogue_projection(raw_arguments_bytes, **original_inputs)
    _need(codec.canonical(projection) == codec.canonical(expected), 'projection_mismatch')
    return expected
