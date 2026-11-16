"""Pure explicit-scope planning; never a model call or a confirmation service.

The caller supplies the complete checked indexed input and a separately saved,
explicitly confirmed scope ledger. This module checks their internal binding;
it cannot prove who confirmed, source ownership, IAM, consent, model quality or
execution authority. Suggestions/classification and human exception review are
upstream work, not a requirement that a person manually classify every claim.

Every source position has exactly one OWNERSHIP classification. Shared claims
have explicit recipients; background stays complete in the global plan and is
copied to a task ONLY through that scope's explicit context_positions. Context
reuse is not repeated ownership. Nothing is truncated, silently excluded,
renumbered or promoted into an interpreted business fact. Fragment capacity is
not final provider HTTP capacity; all plans prohibit dispatch.
"""
import copy
from datetime import datetime
from functools import wraps
from uuid import UUID

from . import report_indexed_inputs_v178 as codec

SCHEMA = 'report-scoped-design-plan-v180'
LEDGER = 'report-scoped-design-scope-ledger-v180'
CONFIRMATION = 'report-scoped-design-scope-confirmation-v180'
TASK = 'report-scoped-design-task-v180'
PAYLOAD = 'report-scoped-design-task-payload-v180'
CONTEXT = 'report-scoped-design-context-v180'
SOURCE_MAP = 'report-scoped-design-source-map-v180'
CAPACITY = 'report-scoped-design-capacity-v180'
CAPACITY_POLICY = 'report-scoped-design-capacity-policy-v180'
MAX_SCOPES = 64
DEFAULT_CAPACITY = {'schema': CAPACITY_POLICY, 'max_scope_payload_bytes': 900000,
    'max_plan_bytes': 8 * 1024 * 1024, 'max_scopes': MAX_SCOPES}
_BASE_KEYS = {'schema', 'source_snapshot_hash', 'indexed_inputs_hash', 'input_claims_hash',
    'input_count', 'scopes', 'shared', 'background'}


class ScopedDesignPlanError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('业务范围、完整原编号归属或已确认记录无法核对；未猜测、裁剪、确认或发送：' + code)


def _need(condition, code):
    if not condition: raise ScopedDesignPlanError(code)


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except ScopedDesignPlanError: raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise ScopedDesignPlanError('invalid_structure') from None
    return checked


def _bytes(value): return codec.canonical(value).encode('utf-8', errors='strict')
def _same(left, right): return _bytes(left) == _bytes(right)


def _id(value):
    _need(type(value) is str and 1 <= len(value) <= 128 and value == value.strip()
        and all(ord(char) >= 32 for char in value), 'scope_id')
    return value


def _reason(value):
    _need(type(value) is str and 1 <= len(value) <= 2000 and bool(value.strip()), 'classification_reason')


def _positions(value, count, code):
    _need(type(value) is list and len(value) <= count, code)
    previous = -1
    for position in value:
        _need(type(position) is int and previous < position < count and position >= 0, code)
        previous = position
    return value


def _actual_inputs(indexed_inputs, source_snapshot_hash, input_claims):
    _need(type(indexed_inputs) is dict and type(input_claims) is list, 'complete_indexed_inputs')
    # These claims MUST come independently from the caller's cold source. The
    # manifest's own copied values are not an independent source for its proof.
    frozen = codec.check_indexed_inputs(indexed_inputs, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash)
    claims = copy.deepcopy(input_claims)
    for claim in claims:
        _need(type(claim.get('span_refs')) is list
            and all(type(ref) is str and bool(ref.strip()) for ref in claim['span_refs']), 'original_claim_refs')
    return frozen, claims


def _classifications(ledger, count):
    """Validate exact once ownership, separately tracking explicit context use."""
    scopes, shared, background = ledger['scopes'], ledger['shared'], ledger['background']
    _need(type(scopes) is list and 1 <= len(scopes) <= MAX_SCOPES, 'scope_count')
    _need(type(shared) is list and type(background) is list
        and len(shared) <= count and len(background) <= count, 'classification_lists')
    by_id, names, owners = {}, set(), {}
    for scope in scopes:
        _need(type(scope) is dict and set(scope) == {'scope_id', 'name', 'claim_positions', 'context_positions'},
            'scope_shape')
        scope_id = _id(scope['scope_id']); name = scope['name']
        _need(scope_id not in by_id and type(name) is str and 1 <= len(name) <= 200
            and bool(name.strip()) and name not in names, 'scope_identity')
        by_id[scope_id] = scope; names.add(name)
        _positions(scope['claim_positions'], count, 'scope_claim_positions')
        _positions(scope['context_positions'], count, 'scope_context_positions')
        for position in scope['claim_positions']:
            _need(position not in owners, 'ownership_repeated')
            owners[position] = {'classification': 'scope', 'scope_ids': [scope_id], 'reason': ''}
    previous = -1
    for item in shared:
        _need(type(item) is dict and set(item) == {'input_position', 'scope_ids', 'reason'}, 'shared_shape')
        position = item['input_position']
        _need(type(position) is int and previous < position < count and position >= 0, 'shared_positions')
        previous = position; recipients = item['scope_ids']
        _need(type(recipients) is list and 2 <= len(recipients) <= len(scopes)
            and all(type(scope_id) is str and scope_id in by_id for scope_id in recipients)
            and len(set(recipients)) == len(recipients), 'shared_scope_ids')
        _reason(item['reason']); _need(position not in owners, 'ownership_repeated')
        owners[position] = {'classification': 'shared', 'scope_ids': copy.deepcopy(recipients), 'reason': item['reason']}
    background_positions, previous = set(), -1
    for item in background:
        _need(type(item) is dict and set(item) == {'input_position', 'reason'}, 'background_shape')
        position = item['input_position']
        _need(type(position) is int and previous < position < count and position >= 0, 'background_positions')
        previous = position; _reason(item['reason']); _need(position not in owners, 'ownership_repeated')
        background_positions.add(position)
        owners[position] = {'classification': 'background', 'scope_ids': [], 'reason': item['reason']}
    _need(set(owners) == set(range(count)), 'ownership_incomplete')
    context_users = {position: [] for position in background_positions}
    for scope in scopes:
        for position in scope['context_positions']:
            _need(position in background_positions, 'context_requires_background')
            context_users[position].append(scope['scope_id'])
    return owners, context_users


@_checked
def validate_scope_ledger(indexed_inputs, source_snapshot_hash, scope_ledger, *, input_claims):
    frozen, _ = _actual_inputs(indexed_inputs, source_snapshot_hash, input_claims)
    _need(type(scope_ledger) is dict and set(scope_ledger) == _BASE_KEYS | {'confirmation'}, 'ledger_shape')
    codec.canonical(scope_ledger)
    base = {key: copy.deepcopy(scope_ledger[key]) for key in scope_ledger if key != 'confirmation'}
    _need(base['schema'] == LEDGER and base['source_snapshot_hash'] == source_snapshot_hash
        and base['indexed_inputs_hash'] == frozen['inputs_hash']
        and base['input_claims_hash'] == frozen['input_claims_hash']
        and type(base['input_count']) is int and base['input_count'] == frozen['input_count'], 'ledger_source_binding')
    _classifications(base, frozen['input_count'])
    confirmation = scope_ledger['confirmation']
    _need(type(confirmation) is dict and set(confirmation) == {
        'schema', 'status', 'request_id', 'actor_id', 'confirmed_at', 'ledger_hash'}
        and confirmation['schema'] == CONFIRMATION and confirmation['status'] == 'confirmed'
        and confirmation['ledger_hash'] == codec.digest(base), 'explicit_bound_confirmation')
    for key in ('request_id', 'actor_id'):
        _need(type(confirmation[key]) is str and str(UUID(confirmation[key])) == confirmation[key], 'confirmation_identity')
    _need(type(confirmation['confirmed_at']) is str, 'confirmation_time')
    when = datetime.fromisoformat(confirmation['confirmed_at'])
    _need(when.tzinfo is not None and when.utcoffset() is not None, 'confirmation_time')
    # This validates a supplied record, not its signer or persistent authority.
    return copy.deepcopy(scope_ledger)


def _capacity(policy):
    policy = copy.deepcopy(DEFAULT_CAPACITY if policy is None else policy)
    _need(type(policy) is dict and set(policy) == set(DEFAULT_CAPACITY)
        and policy['schema'] == CAPACITY_POLICY, 'capacity_policy')
    for key in ('max_scope_payload_bytes', 'max_plan_bytes', 'max_scopes'):
        _need(type(policy[key]) is int and 1 <= policy[key] <= DEFAULT_CAPACITY[key], 'capacity_policy_ceiling')
    return policy


def _entry(position, claims, owners):
    return {'input_position': position, 'claim': copy.deepcopy(claims[position]),
        'span_refs': copy.deepcopy(claims[position]['span_refs']),
        'source_anchor_status': 'unanchored' if not claims[position]['span_refs'] else 'references_supplied_unverified',
        'classification': owners[position]['classification'],
        'scope_ids': copy.deepcopy(owners[position]['scope_ids']), 'reason': owners[position]['reason']}


def _task(index, scope, claims, owners, context_users, frozen, ledger_hash, policy):
    scope_id = scope['scope_id']; count = len(claims)
    primary = copy.deepcopy(scope['claim_positions'])
    shared = [position for position in range(count) if owners[position]['classification'] == 'shared'
        and scope_id in owners[position]['scope_ids']]
    context = copy.deepcopy(scope['context_positions'])
    included_set = set(primary + shared + context)
    included = [position for position in range(count) if position in included_set]
    absent = [position for position in range(count) if position not in included_set]
    unanchored = [position for position in included if not claims[position]['span_refs']]
    payload = {'schema': PAYLOAD, 'source_snapshot_hash': frozen['source_snapshot_hash'],
        'indexed_inputs_hash': frozen['inputs_hash'], 'scope_ledger_hash': ledger_hash,
        'scope': {'scope_id': scope_id, 'name': scope['name']},
        'primary_claims': [_entry(position, claims, owners) for position in primary],
        'shared_claims': [_entry(position, claims, owners) for position in shared],
        'context': {'schema': CONTEXT, 'background_claims': [_entry(position, claims, owners) for position in context]},
        'included_positions': included, 'not_in_this_task': absent,
        'unanchored_input_positions': unanchored, 'source_anchors_verified': False,
        'evidence_scope': 'explicit_positions_only', 'whole_source_semantics_verified': False}
    size = len(_bytes(payload)); reasons = []
    if not primary and not shared: reasons.append('scope_without_assigned_claims')
    if size > policy['max_scope_payload_bytes']: reasons.append('scope_payload_capacity')
    value = {'schema': TASK, 'task_index': index, 'scope_id': scope_id, 'name': scope['name'],
        'primary_positions': primary, 'shared_positions': shared, 'context_positions': context,
        'payload': payload, 'payload_hash': codec.digest(payload), 'payload_bytes': size,
        'status': 'blocked' if reasons else 'planned', 'blocked_reasons': reasons,
        'provider_http_fit_verified': False, 'dispatch_allowed': False,
        'semantic_quality_verified': False, 'requires_independent_request_preparation': True}
    return {**value, 'task_hash': codec.digest(value)}


def _seal_plan(value, policy):
    # Measure the actual final canonical UTF8 plan, including capacity metadata
    # and the fixed-width hash. Never claim fragment measurement is HTTP fit.
    value['plan_hash'] = '0' * 64
    for _ in range(12):
        size = len(_bytes(value))
        reasons = value['capacity']['blocked_reasons']
        if size > policy['max_plan_bytes'] and 'plan_capacity' not in reasons:
            reasons.append('plan_capacity'); value['status'] = 'blocked'
        if value['capacity']['plan_bytes'] == size:
            value['plan_hash'] = codec.digest({key: item for key, item in value.items() if key != 'plan_hash'})
            _need(len(_bytes(value)) == size, 'final_plan_bytes')
            return value
        value['capacity']['plan_bytes'] = size
    raise ScopedDesignPlanError('capacity_measurement')


@_checked
def build_plan(indexed_inputs, source_snapshot_hash, scope_ledger, *, input_claims, capacity_policy=None):
    frozen, claims = _actual_inputs(indexed_inputs, source_snapshot_hash, input_claims)
    ledger = validate_scope_ledger(frozen, source_snapshot_hash, scope_ledger, input_claims=input_claims)
    owners, context_users = _classifications(ledger, frozen['input_count'])
    policy = _capacity(capacity_policy); ledger_hash = codec.digest(ledger)
    tasks = [_task(index, scope, claims, owners, context_users, frozen, ledger_hash, policy)
        for index, scope in enumerate(ledger['scopes'])]
    source_map = [{'input_position': position,
        'claim_hash': frozen['ordered_claim_hashes'][position], **copy.deepcopy(owners[position]),
        'source_anchor_status': 'unanchored' if not claims[position]['span_refs'] else 'references_supplied_unverified',
        'context_scope_ids': copy.deepcopy(context_users.get(position, []))}
        for position in range(frozen['input_count'])]
    reasons = []
    if len(tasks) > policy['max_scopes']: reasons.append('scope_count_capacity')
    if any(task['status'] == 'blocked' for task in tasks): reasons.append('task_blocked')
    value = {'schema': SCHEMA, 'source_snapshot_hash': source_snapshot_hash,
        'indexed_inputs': frozen, 'indexed_inputs_hash': frozen['inputs_hash'],
        'input_count': frozen['input_count'], 'input_claims_hash': frozen['input_claims_hash'],
        'unanchored_input_positions': [position for position in range(len(claims)) if not claims[position]['span_refs']],
        'source_anchors_verified': False,
        'scope_ledger': ledger, 'scope_ledger_hash': ledger_hash,
        'scope_ledger_content_hash': ledger['confirmation']['ledger_hash'],
        'scope_order': [scope['scope_id'] for scope in ledger['scopes']], 'tasks': tasks,
        'global_context': {'schema': CONTEXT,
            'background_claims': [_entry(position, claims, owners) for position in range(len(claims))
                if owners[position]['classification'] == 'background'],
            'shared_claims': [_entry(position, claims, owners) for position in range(len(claims))
                if owners[position]['classification'] == 'shared']},
        'source_map': {'schema': SOURCE_MAP, 'entries': source_map, 'source_map_hash': codec.digest(source_map),
            'ownership_coverage_verified': True, 'semantic_coverage_verified': False},
        'status': 'blocked' if reasons else 'planned',
        'capacity': {'schema': CAPACITY, 'policy': policy,
            'task_payload_bytes': [{'scope_id': task['scope_id'], 'bytes': task['payload_bytes']} for task in tasks],
            'plan_bytes': 0, 'blocked_reasons': reasons, 'provider_http_fit_verified': False},
        'confirmation_authority_verified': False, 'semantic_quality_verified': False,
        'provider_http_fit_verified': False, 'dispatch_allowed': False,
        'requires_independent_request_preparation': True, 'requires_independent_authorization': True}
    return _seal_plan(value, policy)


@_checked
def check_plan(value, indexed_inputs, source_snapshot_hash, scope_ledger, *, input_claims, capacity_policy=None):
    expected = build_plan(indexed_inputs, source_snapshot_hash, scope_ledger,
        input_claims=input_claims, capacity_policy=capacity_policy)
    _need(type(value) is dict and _same(value, expected), 'plan_mismatch')
    return expected
