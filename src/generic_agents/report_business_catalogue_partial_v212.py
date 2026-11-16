"""Independent historical machine suggestions from a FAILED V211 answer.

This is not a replacement decoder or a way to settle the original call as a
success. The caller must authenticate its original terminal failed activity,
HTTP200/stop capture, immutable source and paid ledger before using this pure
codec. Same-count raw JSON cannot authenticate its source. No I/O, model call,
repair of raw bytes, confirmation, adoption or financial transition occurs.

All source positions survive. Ambiguous occurrences have NO chosen winner.
The separately named draft_seed is a platform-derived, editable partial human
document, never an amended model answer. Original name_basis stays unchanged.
"""
import copy
import hashlib
import json
from functools import wraps

from . import report_business_catalogue_wire_v211 as complete
from . import report_business_catalogue_edit_v211 as editor
from . import report_indexed_inputs_v178 as codec

SCHEMA = 'report-business-catalogue-partial-projection-v212'
MAX_PROJECTION_BYTES = complete.MAX_PROJECTION_BYTES


class PartialCatalogueError(ValueError):
    response_must_be_retained = True

    def __init__(self, code):
        self.code = code
        super().__init__('历史目录回答无法安全展开为独立部分建议；保留原失败与原回答：' + code)


def _need(condition, code):
    if not condition:
        raise PartialCatalogueError(code)


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PartialCatalogueError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise PartialCatalogueError('unsafe_structure_or_source') from None
    return checked


def _load(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= complete.MAX_WIRE_BYTES, 'raw_bytes')
    def pairs(items):
        result = {}
        for key, item in items:
            _need(key not in result, 'duplicate_json_key'); result[key] = item
        return result
    def constant(_):
        raise PartialCatalogueError('nonfinite_json')
    return json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)


def _shape(value, count):
    """Closed fields, identities and safe bounds; no coverage/name promotion."""
    codec.canonical(value)
    _need(type(value) is dict and set(value) == complete._FIELDS and value['schema'] == complete.WIRE,
        'wire_fields')
    _need(type(value['input_count']) is int and value['input_count'] == count, 'input_count')
    businesses = value['businesses']
    _need(type(businesses) is list and len(businesses) <= complete.MAX_BUSINESSES, 'business_capacity')
    by_business, names = {}, set()
    for row in businesses:
        _need(type(row) is dict and set(row) == complete._BUSINESS, 'business_fields')
        key = row['key']
        _need(type(key) is str and complete._KEY.fullmatch(key) is not None and key not in by_business,
            'business_key')
        complete._text(row['name'], 200, 'business_name')
        _need(row['name'] == row['name'].strip() and row['name'] not in names, 'business_name_identity')
        complete._text(row['purpose'], 2000, 'purpose', empty=True)
        complete._text(row['boundary'], 2000, 'boundary', empty=True)
        complete._text(row['reason'], 2000, 'business_reason')
        complete._positions(row['seed_positions'], count, 'seed_positions')
        _need(type(row['name_basis']) is str and row['name_basis'] in {'explicit', 'inferred'}, 'name_basis')
        by_business[key] = row; names.add(row['name'])
    questions = value['questions']
    _need(type(questions) is list and len(questions) <= count * 4, 'question_capacity')
    by_question = {}
    for row in questions:
        _need(type(row) is dict and set(row) == complete._QUESTION, 'question_fields')
        key = row['key']
        _need(type(key) is str and complete._KEY.fullmatch(key) is not None and key not in by_question,
            'question_key')
        _need(type(row['kind']) is str and row['kind'] in {'ownership', 'missing_info'}, 'question_kind')
        complete._positions(row['input_positions'], count, 'question_positions')
        complete._keys(row['candidate_business_keys'], by_business, 'question_business_keys')
        complete._text(row['question'], 2000, 'question_text'); by_question[key] = row
    groups = value['allocation_groups']
    _need(type(groups) is list and 1 <= len(groups) <= codec.MAX_INPUTS, 'group_capacity')
    for row in groups:
        _need(type(row) is dict and set(row) == complete._GROUP, 'group_fields')
        _need(type(row['kind']) is str and row['kind'] in {'business', 'shared', 'background', 'pending'}, 'group_kind')
        if row['kind'] == 'background':
            # A background group with ANY recipients is already excluded as
            # contradictory below. Preserve even undefined-but-well-formed
            # labels as raw proposals, never resolve/create a business for them.
            keys = row['business_keys']
            _need(type(keys) is list and len(keys) <= complete.MAX_BUSINESSES
                and all(type(key) is str and complete._KEY.fullmatch(key) is not None for key in keys)
                and len(keys) == len(set(keys)), 'background_recipient_labels')
        else:
            complete._keys(row['business_keys'], by_business, 'group_business_keys')
        complete._keys(row['question_keys'], by_question, 'group_question_keys')
        complete._text(row['reason'], 2000, 'group_reason')
        positions = row['input_positions']
        # Duplicates/order are diagnosed, not silently repaired into a valid
        # model group. Every original occurrence is retained below.
        _need(type(positions) is list and 1 <= len(positions) <= codec.MAX_INPUTS
            and all(type(p) is int and 0 <= p < count for p in positions), 'group_positions')
    merges = value['merge_suggestions']
    _need(type(merges) is list and len(merges) <= complete.MAX_BUSINESSES, 'merge_capacity')
    identities = set()
    for merge in merges:
        _need(type(merge) is dict and set(merge) == complete._MERGE, 'merge_fields')
        complete._keys(merge['business_keys'], by_business, 'merge_business_keys', minimum=2)
        complete._text(merge['reason'], 2000, 'merge_reason')
        identity = frozenset(merge['business_keys'])
        _need(identity not in identities, 'duplicate_merge_suggestion'); identities.add(identity)
    return by_business, by_question


def _issue(code, path, message, positions, *, business=None, question_key=None):
    return {'code': code, 'path': path, 'message': message, 'origin': 'platform_diagnostic',
        'blocks_confirmation': True, 'input_positions': copy.deepcopy(positions),
        'business_key': business['key'] if business is not None else None,
        'original_name': business['name'] if business is not None else None,
        'name_basis': business['name_basis'] if business is not None else None,
        'original_name_basis': business['name_basis'] if business is not None else None,
        'question_key': question_key}


def _question_key(business, used):
    identity = {'business_key': business['key'], 'name': business['name'], 'code': 'business_name_not_literal'}
    base = 'partial_name_' + codec.digest(identity)[:32]
    key, counter = base, 0
    while key in used:
        counter += 1; key = base + '_' + str(counter)
    used.add(key)
    return key


@_checked
def build_partial_projection(raw_bytes, *, indexed, input_claims, source_snapshot_hash, catalog):
    """Safe suggestions only; original full success must use its own entry.

    input_id is explicitly a platform positional label because this API does
    not receive V174 input_order. A caller may separately project the REAL Map
    identifier from its independently checked source; this codec never guesses.
    """
    _need(catalog is not None, 'original_catalog_required')
    frozen = complete._source(indexed, input_claims, source_snapshot_hash, catalog)
    value = _load(raw_bytes); count = frozen['input_count']
    by_business, by_question = _shape(value, count)
    # Negative admission only. We never modify the answer and run it through
    # the complete decoder again, or return a complete-success projection.
    try:
        complete.build_catalogue_projection(raw_bytes, indexed=indexed, input_claims=input_claims,
            source_snapshot_hash=source_snapshot_hash, catalog=catalog)
    except complete.CatalogueWireError as error:
        complete_error = error.code
    else:
        raise PartialCatalogueError('complete_answer_has_separate_entry')
    memberships = [[] for _ in range(count)]
    issues, invalid_positions = [], set()
    for group_index, group in enumerate(value['allocation_groups']):
        positions = group['input_positions']
        recipients_valid = ((group['kind'] != 'business' or len(group['business_keys']) == 1)
            and (group['kind'] != 'shared' or len(group['business_keys']) >= 2)
            and (group['kind'] != 'background' or not group['business_keys']))
        if not recipients_valid:
            invalid_positions.update(positions)
            issues.append({**_issue('group_recipients',
                f'/raw_wire/allocation_groups/{group_index}/business_keys',
                '原分组类型与接收业务数量矛盾，保留原类型及接收业务；这些位置未自动分派。', sorted(set(positions))),
                'group_index': group_index, 'original_kind': group['kind'],
                'original_business_keys': copy.deepcopy(group['business_keys'])})
        if any(left >= right for left, right in zip(positions, positions[1:])):
            issues.append(_issue('group_positions_not_strictly_ascending',
                f'/raw_wire/allocation_groups/{group_index}/input_positions',
                '原模型位置未严格升序，原组保留；派生草稿按原位置展示，不改变原回答。', sorted(set(positions))))
        for occurrence_index, position in enumerate(positions):
            memberships[position].append({'group_index': group_index, 'occurrence_index': occurrence_index,
                **{key: copy.deepcopy(group[key]) for key in ('kind', 'business_keys', 'reason', 'question_keys')}})
    missing = [p for p, members in enumerate(memberships) if not members]
    conflicting = [p for p, members in enumerate(memberships) if len(members) > 1]
    pending = [p for p, members in enumerate(memberships) if len(members) == 1 and members[0]['kind'] == 'pending']
    if missing:
        issues.append(_issue('missing_input_assignments', '/raw_wire/allocation_groups',
            '原模型没有分派这些原位置，未补归属或归作背景。', missing))
    if conflicting:
        issues.append(_issue('conflicting_input_assignments', '/raw_wire/allocation_groups',
            '这些原位置有多次分派，全部原建议保留，未选择胜出归属。', conflicting))
    derived_questions = [{**copy.deepcopy(row), 'status': 'open', 'resolution': ''} for row in value['questions']]
    used_questions = set(by_question)
    for business_index, business in enumerate(value['businesses']):
        if business['name_basis'] == 'explicit' and not complete._literal_name(business['name'], business, input_claims, catalog):
            key = _question_key(business, used_questions)
            issues.append(_issue('business_name_not_literal', f'/raw_wire/businesses/{business_index}/name',
                '原机器将名称标为明示，但其种子原引用内未逐字找到；原name_basis未改变。',
                business['seed_positions'], business=business, question_key=key))
            derived_questions.append({'key': key, 'kind': 'missing_info',
                'input_positions': copy.deepcopy(business['seed_positions']),
                'candidate_business_keys': [business['key']],
                'question': '原机器将“' + business['name'] + '”标为明示，但种子原引用内未逐字找到。'
                    '请改名、移除此业务，或明确采用为用户拟定名称并说明理由；这不是原模型补答。',
                'status': 'open', 'resolution': ''})
    # A partial projection is narrowly useful for diagnosed coverage/name/order
    # failures, not an opaque escape hatch for any rejected complete protocol.
    _need(bool(issues), 'no_supported_partial_diagnostic')
    seed_groups = []
    for index, group in enumerate(value['allocation_groups']):
        unique = sorted(p for p in group['input_positions'] if len(memberships[p]) == 1 and p not in invalid_positions)
        if unique:
            seed_groups.append({'key': f'group_{index + 1:04d}', **copy.deepcopy(group), 'input_positions': unique})
    draft_seed = {'schema': editor.SCHEMA,
        'businesses': [{key: copy.deepcopy(row[key]) for key in ('key', 'name', 'purpose', 'boundary')}
            for row in value['businesses']], 'allocation_groups': seed_groups, 'questions': derived_questions}
    editor.validate_document(draft_seed, count)
    assignments = []
    for position, members in enumerate(memberships):
        proposed = len(members) == 1 and members[0]['kind'] != 'pending' and position not in invalid_positions
        status = 'proposed' if proposed else 'conflicting' if len(members) > 1 else 'unassigned'
        chosen = members[0] if len(members) == 1 else None
        claim = input_claims[position]
        assignments.append({'input_position': position, 'input_id': f'position_{position:04d}',
            'input_id_origin': 'platform_position', 'claim_hash': frozen['ordered_claim_hashes'][position],
            'statement': claim['statement'], 'status': claim['status'], 'claim': copy.deepcopy(claim),
            'span_refs': copy.deepcopy(claim['span_refs']), 'source_spans': [
                {'span_ref': ref, 'record': copy.deepcopy(catalog['index'][ref])} for ref in claim['span_refs']],
            'assignment_status': status, 'proposed_memberships': copy.deepcopy(members),
            'kind': chosen['kind'] if chosen else None,
            'business_keys': copy.deepcopy(chosen['business_keys']) if chosen else [],
            'question_keys': copy.deepcopy(chosen['question_keys']) if chosen else [],
            'reason': chosen['reason'] if chosen else '',
            'assignment_issue_codes': (['group_recipients'] if position in invalid_positions else [])
                + (['conflicting_input_assignments'] if len(members) > 1 else [])
                + (['missing_input_assignments'] if not members else [])
                + (['pending_ownership'] if position in pending else []), 'machine_only': True})
    scopes = [{'scope_id': row['key'], 'name': row['name'], 'context_positions': [],
        'claim_positions': [p for p, members in enumerate(memberships) if len(members) == 1
            and p not in invalid_positions and members[0]['kind'] == 'business'
            and members[0]['business_keys'] == [row['key']]],
        'machine_only': True} for row in value['businesses']]
    raw_occurrences = sum(len(members) for members in memberships)
    unassigned = sorted(set(missing + conflicting + pending).union(invalid_positions))
    result = {'schema': SCHEMA, 'original_wire_schema': complete.WIRE,
        'source_snapshot_hash': source_snapshot_hash, 'indexed_inputs_hash': frozen['inputs_hash'],
        'input_claims_hash': frozen['input_claims_hash'], 'input_count': count,
        'catalog_hash': catalog['descriptor']['catalog_hash'],
        'raw_arguments_sha256': hashlib.sha256(raw_bytes).hexdigest(), 'raw_wire': copy.deepcopy(value),
        'complete_validation_error': complete_error, 'businesses': [
            {**copy.deepcopy(row), 'machine_only': True} for row in value['businesses']],
        'scopes': scopes, 'assignments': assignments, 'questions': copy.deepcopy(value['questions']),
        'merge_suggestions': copy.deepcopy(value['merge_suggestions']), 'issues': issues,
        'missing_positions': missing, 'conflict_positions': conflicting, 'pending_positions': pending,
        'invalid_proposal_positions': sorted(invalid_positions),
        'unassigned_positions': unassigned, 'draft_seed': draft_seed,
        'draft_seed_hash': codec.digest(draft_seed), 'inventory_complete': True,
        'input_coverage_complete': not missing, 'allocation_complete': not unassigned,
        'stats': {'input_count': count, 'raw_membership_count': raw_occurrences,
            'unique_mentioned_count': count - len(missing),
            'duplicate_occurrence_count': raw_occurrences - (count - len(missing)),
            'proposed_count': sum(row['assignment_status'] == 'proposed' for row in assignments),
            'conflicting_count': len(conflicting), 'missing_count': len(missing),
            'pending_count': len(pending), 'invalid_proposal_count': len(invalid_positions),
            'invalid_unique_count': sum(len(memberships[p]) == 1 for p in invalid_positions),
            'unassigned_count': len(unassigned)},
        'machine_only': True, 'model_answer_valid': False, 'original_call_success': False,
        'source_anchors_verified': False, 'report_coverage_verified': False,
        'semantic_quality_verified': False, 'human_confirmed': False,
        'can_confirm': False, 'can_apply': False, 'can_publish': False, 'dispatch_allowed': False}
    _need(len(codec.canonical(result).encode('utf-8')) <= MAX_PROJECTION_BYTES, 'projection_capacity')
    return {**result, 'projection_hash': codec.digest(result)}


@_checked
def check_partial_projection(projection, raw_bytes, **original_inputs):
    expected = build_partial_projection(raw_bytes, **original_inputs)
    _need(codec.canonical(projection) == codec.canonical(expected), 'projection_mismatch')
    return expected
