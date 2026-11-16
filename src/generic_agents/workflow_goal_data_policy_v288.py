"""Pure proposed routing over an original V196 report and frozen V288 policy.

This module has no service registration, database, clock, SDK or receiver. A
well-formed/self-hashed DTO does not prove its producer, readable scope, values,
types, units, freshness, policy approval or ACK. The future server wrapper must
verify those actual records and current authority. ``data_ready`` means only
that the original reader reports sufficient evidence with coherent coverage.
No original input, Goal, query, time window or context is rewritten here.
"""
import copy
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException

from .contracts import DataRequest, GoalSpec, digest
from .workflow_goal_evaluation_contracts_v288 import check_preparation_policy
from . import workflow_task_context_v196 as reader

OP = 'plan_approved_goal_data_v288'  # Metadata only; not an installed service.
RESULT = 'workflow-goal-data-route-v288'
FLAGS = {key: False for key in ('can_dispatch', 'permissions_verified', 'source_authenticity_verified',
    'current_outcome_verified', 'goal_completion_verified', 'data_reception_verified')}
REASONS = ('reader_reports_sufficient', 'approved_latest_context', 'preparation_policy_missing',
    'invalid_preparation_policy', 'invalid_goal_spec', 'invalid_reader_result', 'unsupported_entity_scope',
    'invalid_goal_fields', 'unsupported_goal_query', 'fixed_window_requires_review', 'reader_waiting_or_blocked',
    'conflicting_evidence', 'evidence_status_unknown', 'reader_scope_mismatch', 'field_coverage_mismatch',
    'reader_evidence_inconsistent', 'acquisition_not_permitted', 'goal_disallows_acquisition',
    'unsupported_evidence_source', 'context_not_unique', 'gap_not_unique', 'gap_not_open',
    'goal_snapshot_mismatch', 'query_request_mismatch', 'time_requirement_mismatch')
_BAD = (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError, UnicodeError)
_INVALID = (HTTPException,) + _BAD


def _hash(value):
    try:
        return digest(value)
    except _BAD:
        return None


def _same(left, right):
    return digest(left) == digest(right)


def _ids(values):
    return (type(values) is list and len(values) == len(set(values))
        and all(type(value) is str and str(UUID(value)) == value for value in values))


def _fields(values):
    return (type(values) is list and bool(values) and len(values) == len(set(values))
        and all(type(value) is str and 0 < len(value) <= 100 and bool(value.strip()) for value in values))


def _stamp(value):
    if type(value) is not str:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


def output_schema():
    sha = {'type': ['string', 'null'], 'pattern': '^[0-9a-f]{64}$'}
    ident = {'type': 'string', 'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'}
    context = {'type': ['object', 'null'], 'additionalProperties': False,
        'properties': {**{key: copy.deepcopy(ident) for key in ('query_id', 'gap_id', 'entity_id', 'policy_id')},
            **{key: {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
                for key in ('context_hash', 'query_request_hash', 'approved_hash')}},
        'required': ['context_hash', 'query_id', 'query_request_hash', 'gap_id', 'entity_id', 'policy_id', 'approved_hash']}
    properties = {'schema': {'const': RESULT}, 'route': {'enum': ['data_ready', 'acquire', 'review_required']},
        'reason_code': {'enum': list(REASONS)}, 'context_ref': context,
        **{key: copy.deepcopy(sha) for key in ('reader_result_hash', 'goal_spec_hash', 'preparation_policy_hash')},
        **{key: {'const': False} for key in FLAGS}}
    complete_hashes = {key: {'type': 'string'} for key in (
        'reader_result_hash', 'goal_spec_hash', 'preparation_policy_hash')}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False,
        'allOf': [{'if': {'properties': {'route': {'const': 'acquire'}}},
            'then': {'properties': {**copy.deepcopy(complete_hashes), 'context_ref': {'type': 'object'},
                'reason_code': {'const': 'approved_latest_context'}}},
            'else': {'properties': {'context_ref': {'type': 'null'}}}},
            {'if': {'properties': {'route': {'const': 'data_ready'}}},
                'then': {'properties': {**copy.deepcopy(complete_hashes), 'reason_code': {'const': 'reader_reports_sufficient'}}}},
            {'if': {'properties': {'route': {'const': 'review_required'}}},
                'then': {'properties': {'reason_code': {'enum': list(REASONS[2:])}}}}]}


def _goal_context(context, goal, policy):
    """Coherence only: original V196 request/snapshot, never new time authority."""
    observation = context['gap']['observation_request']; request = observation['request']
    delegation = observation['goal_delegation']; snapshot = delegation['goal_snapshot']
    if request.get('tool_steps') or request.get('relation_query') is not None:
        return 'unsupported_goal_query'
    if (delegation['task_id'] != context['scope']['task_id'] or not _same(snapshot['spec'], goal)
            or snapshot['hash'] != digest(goal) or type(snapshot['version']) is not int or snapshot['version'] < 1
            or context['scope']['goal_snapshot_hash'] != digest(snapshot)):
        return 'goal_snapshot_mismatch'
    prepared = {'resolved_time', 'reference_at', 'clarifications'}
    original = {key: value for key, value in request.items() if key not in prepared}
    normalized = DataRequest.model_validate(original, strict=True).model_dump(mode='json')
    if (not _same(original, normalized) or request['mode'] != 'structured'
            or request['model_profile_id'] is not None or request['tool_steps'] != []
            or request['allow_external_tools'] is not False or request.get('relation_query') is not None
            or request.get('clarifications') != []
            or request['entity_ids'] != goal['entity_ids'] or set(request['fields']) != set(goal['required_fields'])
            or not _fields(request['fields']) or not _same(request['evidence_requirement'], goal['evidence'])
            # V196's original Gap request can be an authorized projection of
            # Q0 (including a different full-field order). Preserve its own
            # raw hash, separately from Q0's immutable request hash.
            or context['query']['request_hash'] != observation['query_request_hash']):
        return 'query_request_mismatch'
    window = request['resolved_time']; requirement = goal['evidence']['time']
    if (not _same(window, snapshot['time']) or window.get('mode') != 'latest'
            or not _same(window.get('original'), requirement)
            or window.get('timezone') != policy['timezone']
            or window.get('max_age_seconds') != policy['max_age_seconds']
            or not all(_stamp(window.get(key)) for key in ('start', 'end', 'reference_at'))
            or not _stamp(request.get('reference_at'))):
        return 'time_requirement_mismatch'
    return None


def route(reader_result, goal_spec, preparation_policy):
    """Return a deterministic proposal; no current clock or execution authority.

    The shared V288 checker owns frozen time-policy semantics. In particular,
    the new server Goal builder supplies its explicit Asia/Shanghai default;
    this function never adds a timezone to an old Goal or slides its window.
    """
    base = {'schema': RESULT, 'route': 'review_required', 'reason_code': 'invalid_reader_result',
        'context_ref': None, 'reader_result_hash': _hash(reader_result), 'goal_spec_hash': _hash(goal_spec),
        'preparation_policy_hash': None, **FLAGS}
    def result(reason, proposed='review_required', context=None):
        return {**base, 'route': proposed, 'reason_code': reason, 'context_ref': copy.deepcopy(context)}
    try:
        goal = GoalSpec.model_validate(goal_spec, strict=True).model_dump(mode='json')
        if not _same(goal, goal_spec):
            return result('invalid_goal_spec')
        if not _ids(goal['entity_ids']) or len(goal['entity_ids']) != 1:
            return result('unsupported_entity_scope')
        if not _fields(goal['required_fields']):
            return result('invalid_goal_fields')
        if goal['relation_query'] is not None or goal['data_query'] is not None:
            return result('unsupported_goal_query')
    except _BAD:
        return result('invalid_goal_spec')
    if preparation_policy is None:
        return result('preparation_policy_missing')
    try:
        policy = check_preparation_policy(goal_spec, preparation_policy)
        base['preparation_policy_hash'] = policy['policy_hash']
    except _INVALID:
        return result('invalid_preparation_policy')
    try:
        value = reader.checked_result(reader_result)
        package = value['evidence_package']
        if package is None or value['status'] in {'waiting_goal', 'blocked'}:
            return result('reader_waiting_or_blocked')
        if package['evidence_status'] == 'conflict':
            return result('conflicting_evidence')
        if package['entity_ids'] != goal['entity_ids'] or not _ids(package['entity_ids']):
            return result('reader_scope_mismatch')
        fields = goal['required_fields']
        if not _fields(package['fields']) or set(package['fields']) != set(fields):
            return result('field_coverage_mismatch')
        refs = package['evidence_refs']
        if any(ref['entity_id'] != goal['entity_ids'][0] for ref in refs):
            return result('reader_scope_mismatch')
        if package['evidence_status'] == 'sufficient':
            if (value['status'] != 'no_gap' or value['candidates'] or not refs
                    or any(gap['status'] not in {'resolved', 'cancelled'} for gap in package['gap_refs'])
                    or any(ref['kind'] == 'observation'
                        and (ref['observation_id'] is None or not _stamp(ref['observed_at'])) for ref in refs)
                    or any(ref['kind'] != 'observation'
                        for ref in refs if goal['evidence']['source'] == 'observations')
                    or any(ref['kind'] != 'archive' for ref in refs if goal['evidence']['source'] == 'archive')):
                return result('reader_evidence_inconsistent')
            return result('reader_reports_sufficient', 'data_ready')
        if package['evidence_status'] != 'insufficient':
            return result('evidence_status_unknown')
        if policy['time_basis'] != 'rolling_latest':
            return result('fixed_window_requires_review')
        if policy['allow_acquisition'] is not True:
            return result('acquisition_not_permitted')
        if goal['observation_mode'] != 'allow_new':
            return result('goal_disallows_acquisition')
        if goal['evidence']['source'] not in {'observations', 'either'}:
            return result('unsupported_evidence_source')
        if value['status'] != 'ready' or len(value['candidates']) != 1 or value['resense_context'] is None:
            return result('context_not_unique')
        context = value['resense_context']
        if not _same(context, value['candidates'][0]):
            return result('context_not_unique')
        gaps = package['gap_refs']
        if len(gaps) != 1 or gaps[0]['gap_id'] != context['gap']['gap_id']:
            return result('gap_not_unique')
        gap = gaps[0]
        if gap['status'] not in {'received', 'open'}:
            return result('gap_not_open')
        if (gap['version_at_read'] != context['gap']['version_at_read']
                or gap['request_hash'] != digest(context['gap']['observation_request']['request'])):
            return result('query_request_mismatch')
        if gap['entity_id'] != context['gap']['entity_id'] or gap['entity_id'] != goal['entity_ids'][0]:
            return result('reader_scope_mismatch')
        if (not _fields(context['gap']['fields']) or set(context['gap']['fields']) != set(fields)
                or not _fields(gap['fields']) or set(gap['fields']) != set(fields)):
            return result('field_coverage_mismatch')
        reason = _goal_context(context, goal, policy)
        if reason:
            return result(reason)
        reference = {'context_hash': context['context_hash'], 'query_id': context['query']['query_id'],
            'query_request_hash': context['query']['request_hash'], 'gap_id': context['gap']['gap_id'],
            'entity_id': context['gap']['entity_id'], 'policy_id': context['policy']['policy_id'],
            'approved_hash': context['policy']['approved_hash']}
        return result('approved_latest_context', 'acquire', reference)
    except _INVALID:
        return result('invalid_reader_result')
