"""Pure, closed pins for a new same-Goal evaluation; no data or authority IO.

The server must authenticate the frozen V288 binding, original Q0, new Qe,
producer ownership and independent observation receipts. A matching hash is
only an integrity check. These helpers never read a clock, issue a query,
change a Goal/Task/DataRun, dispatch, receive data or accept a Goal.
"""
import copy
import hashlib
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from .contracts import GoalSpec
from .typed_payload_codec_v131 import TypedPayloadError, canonical_json

POLICY = 'goal-data-preparation-policy-v288'
RECORD = 'workflow-goal-evaluation-record-v288'
MAX_BYTES = 4 * 1024 * 1024
ID = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Hash = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[StrictStr, Field(min_length=1, max_length=200, pattern=r'\S')]
Positive = Annotated[StrictInt, Field(ge=1)]
Age = Annotated[StrictInt, Field(ge=1, le=31536000)]
Timestamp = Annotated[StrictStr, Field(min_length=20, max_length=50)]
FieldName = Annotated[StrictStr, Field(min_length=1, max_length=100, pattern=r'\S')]
FLAGS = {
    'can_dispatch': False, 'can_accept': False, 'permissions_verified': False,
    'source_authenticity_verified': False, 'current_outcome_verified': False,
    'goal_completion_verified': False, 'data_reception_verified': False,
    'semantic_quality_verified': False,
}
ERROR = 'V288 Goal evaluation policy, time, scope or original pins do not match'


class GoalEvaluationContractError(ValueError):
    pass


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PreparationPolicy(Closed):
    schema: Literal['goal-data-preparation-policy-v288']
    time_basis: Literal['rolling_latest', 'fixed_window']
    allow_acquisition: StrictBool
    max_age_seconds: Age | None
    timezone: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    policy_hash: Hash


class EvaluationScope(Closed):
    # Matches the original V196 scope shape. No client marker enables V288.
    workspace_id: ID
    task_id: ID
    instance_id: ID
    release_id: ID
    package_id: ID
    business_id: Text
    graph_hash: Hash
    source_binding_hash: Hash
    goal_snapshot_hash: Hash
    native_goal_context_hash: Hash


class GoalSnapshot(Closed):
    version: Positive
    hash: Hash
    spec: dict[str, Any]
    time: dict[str, Any]


class QueryRef(Closed):
    query_id: ID
    version: Positive
    request_hash: Hash
    result_hash: Hash
    data_hash: Hash


class ProducerPin(Closed):
    # The current SDK is still running when it creates the record. Its output
    # hash would include this record hash, so neither output nor future audit
    # belongs here. Historical readers independently wait for real completion.
    workspace_id: ID
    task_id: ID
    scope_hash: Hash
    capability_id: ID
    capability_hash: Hash
    invocation_id: ID
    job_id: ID
    actor_id: ID
    lease_token: ID
    activation_id: Text
    node_id: Text
    cancel_epoch: Annotated[StrictInt, Field(ge=0)]
    input_hash: Hash
    request_key_hash: Hash


class EvaluationTrigger(Closed):
    kind: Literal['data_ready', 'acquisition_completed']
    local_plan_hash: Hash
    reader_invocation_id: ID
    reader_result_hash: Hash
    reader_context_hash: Hash | None
    resense_invocation_id: ID | None
    resense_receipt_hash: Hash | None


class AckRef(Closed):
    workspace_id: ID
    # task_id is the current consumer; the original acquisition's business
    # Task is separate provenance and is never rewritten to claim ownership.
    task_id: ID
    origin_task_id: ID | None
    query_admitted: StrictBool
    entity_id: ID
    fields: list[FieldName] = Field(max_length=30)
    acquisition_id: ID
    acquisition_hash: Hash
    observation_id: ID
    observation_hash: Hash
    source_id: ID
    source_hash: Hash
    extractor_id: ID
    extractor_hash: Hash
    event_id: ID
    event_hash: Hash
    memory_id: ID
    memory_hash: Hash
    data_reception_hash: Hash
    observed_at: Timestamp
    received_at: Timestamp
    simulation_only: StrictBool
    test_only: StrictBool


class MqttTestOriginV297(Closed):
    audit_id: ID
    declaration_hash: Hash


class MqttOriginPinsV297(Closed):
    schema: Literal['mqtt-passive-origin-pins-v297']
    receipt_id: ID
    gateway_id: ID
    packet_key: Hash
    source_record_id: Text
    input_sha256: Hash
    packet_evidence_id: ID
    packet_evidence_sha256: Hash
    packet_size_bytes: Annotated[StrictInt, Field(ge=1, le=65536)]
    config_hash: Hash
    gateway_version: Positive
    topic: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    received_at: Timestamp
    receipt_created_at: Timestamp
    decoded_hash: Hash
    binding_id: ID
    binding_revision: Positive
    binding_hash: Hash
    instance_id: ID
    release_id: ID
    package_id: ID
    entity_id: ID
    field_links_hash: Hash
    field_units_hash: Hash
    delivery_id: ID
    delivery_target_hash: Hash
    route_id: ID
    route_version: Positive
    authorization_id: ID
    authorization_hash: Hash
    saved_audit_id: ID
    saved_audit_hash: Hash
    submitted_audit_id: ID
    submitted_audit_hash: Hash
    test_origin: MqttTestOriginV297 | None


class MqttAckRefV297(Closed):
    origin_kind: Literal['mqtt_passive_v297']
    workspace_id: ID
    task_id: ID
    origin_task_id: None
    query_admitted: StrictBool
    entity_id: ID
    fields: list[FieldName] = Field(max_length=30)
    observation_id: ID
    observation_hash: Hash
    source_id: ID
    source_hash: Hash
    extractor_id: ID
    extractor_hash: Hash
    event_id: ID
    event_hash: Hash
    memory_id: ID
    memory_hash: Hash
    data_reception_hash: Hash
    observed_at: Timestamp
    received_at: Timestamp
    simulation_only: StrictBool
    test_only: StrictBool
    mqtt_origin: MqttOriginPinsV297


class EvidenceRef(Closed):
    evidence_id: ID
    sha256: Hash
    size_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_BYTES)]
    media_type: Annotated[StrictStr, Field(min_length=1, max_length=150)]


class QueryControls(Closed):
    cached_only: Literal[True]
    learning: Literal[False]
    emit_gaps: Literal[False]
    include_trusted_cases: Literal[False]


class GoalEvaluationRecord(Closed):
    schema: Literal['workflow-goal-evaluation-record-v288']
    evaluation_id: ID
    scope: EvaluationScope
    goal_snapshot: GoalSnapshot
    preparation_policy: PreparationPolicy
    generation: Positive
    request_id: ID
    request_key: Text
    original_query_ref: QueryRef
    evaluation_query_ref: QueryRef
    evaluation_request: dict[str, Any]
    evaluation_result: dict[str, Any]
    query_controls: QueryControls
    cut_at: Timestamp
    cut_checked_at: Timestamp
    derived_window: dict[str, Any]
    trigger: EvaluationTrigger
    producer: ProducerPin
    ack_refs: list[AckRef | MqttAckRefV297] = Field(max_length=1000)
    evidence_ref: EvidenceRef
    simulation_only: StrictBool
    test_only: StrictBool
    can_dispatch: Literal[False]
    can_accept: Literal[False]
    permissions_verified: Literal[False]
    source_authenticity_verified: Literal[False]
    current_outcome_verified: Literal[False]
    goal_completion_verified: Literal[False]
    data_reception_verified: Literal[False]
    semantic_quality_verified: Literal[False]
    record_hash: Hash


def _need(value):
    if not value:
        raise GoalEvaluationContractError(ERROR)


def _json(value):
    try:
        result = canonical_json(value)
        _need(len(result.encode('utf-8')) <= MAX_BYTES)
        return result
    except (TypedPayloadError, TypeError, ValueError, UnicodeError, RecursionError):
        raise GoalEvaluationContractError(ERROR) from None


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _parse(model, value):
    _json(value)
    try:
        return model.model_validate(copy.deepcopy(value)).model_dump(mode='json')
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise GoalEvaluationContractError(ERROR) from None


def _goal(value):
    _json(value)
    try:
        return GoalSpec.model_validate(copy.deepcopy(value), strict=True).model_dump(mode='json')
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise GoalEvaluationContractError(ERROR) from None


def _instant(value):
    try:
        _need(type(value) is str and 20 <= len(value) <= 50 and 'T' in value)
        actual = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _need(actual.tzinfo is not None and actual.utcoffset() is not None)
        return actual.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise GoalEvaluationContractError(ERROR) from None


def _timezone(value):
    try:
        _need(type(value) is str and bool(value) and len(value) <= 100)
        ZoneInfo(value)
        return value
    except (ZoneInfoNotFoundError, ValueError, TypeError, UnicodeError):
        raise GoalEvaluationContractError(ERROR) from None


def policy_hash(value):
    """Hash original policy parameters; the hash grants no permissions."""
    _need(type(value) is dict)
    return _hash({key: item for key, item in value.items() if key != 'policy_hash'})


def check_preparation_policy(goal_spec, raw):
    """Match actual Goal parameters without changing its resolved initial time."""
    goal = _goal(goal_spec)
    value = _parse(PreparationPolicy, raw)
    time = goal['evidence']['time']
    _timezone(value['timezone'])
    # New Task construction supplies the business timezone. This pure checker
    # cannot silently fill an absent old Goal timezone and enable new sensing.
    _need(time['timezone'] is not None and value['timezone'] == _timezone(time['timezone'])
        and value['max_age_seconds'] == time['max_age_seconds']
        and value['policy_hash'] == policy_hash(value))
    if time['mode'] == 'latest':
        _need(value['time_basis'] == 'rolling_latest' and time['max_age_seconds'] is not None
            and all(time[key] is None for key in ('start', 'end', 'at', 'relative', 'days')))
        if value['allow_acquisition']:
            _need(goal['observation_mode'] == 'allow_new'
                and goal['evidence']['source'] in {'observations', 'either'})
    else:
        _need(value['time_basis'] == 'fixed_window' and value['allow_acquisition'] is False)
    return copy.deepcopy(value)


def make_preparation_policy(goal_spec, *, allow_acquisition=False):
    """The caller must already have authenticated the new frozen binding."""
    _need(type(allow_acquisition) is bool)
    goal = _goal(goal_spec)
    time = goal['evidence']['time']
    value = {'schema': POLICY,
        'time_basis': 'rolling_latest' if time['mode'] == 'latest' else 'fixed_window',
        'allow_acquisition': allow_acquisition, 'max_age_seconds': time['max_age_seconds'],
        'timezone': time['timezone']}
    value['policy_hash'] = policy_hash(value)
    return check_preparation_policy(goal_spec, value)


def _resolved(time, reference):
    # Explicit reference prevents temporal.resolve from reading its clock. Use
    # the platform's real fixed-calendar/DST rules rather than a second parser.
    from fastapi import HTTPException
    from .temporal import resolve
    try:
        return resolve(copy.deepcopy(time), reference=reference)
    except (HTTPException, ValidationError, ValueError, TypeError, KeyError, OverflowError):
        raise GoalEvaluationContractError(ERROR) from None


def derive_evaluation_window(goal_spec, preparation_policy, *, cut_at, trusted_now, initial_window=None):
    """Freeze latest at a server-supplied cut, never at a model/local clock.

    A fixed evaluation must supply the exact original resolved Goal window.
    Its cut filters future evidence, without moving the historical window.
    """
    goal = _goal(goal_spec)
    policy = check_preparation_policy(goal_spec, preparation_policy)
    cut, checked = _instant(cut_at), _instant(trusted_now)
    _need(cut <= checked)
    time = goal['evidence']['time']
    if policy['time_basis'] == 'rolling_latest':
        _need(initial_window is None)
        return _resolved(time, cut)
    _need(type(initial_window) is dict and type(initial_window.get('reference_at')) is str)
    _json(initial_window)
    reference = _instant(initial_window['reference_at'])
    _need(reference <= cut and _json(initial_window) == _json(_resolved(time, reference)))
    return copy.deepcopy(initial_window)


def record_hash(value):
    """Hash exact JSON storage types, including 1 versus 1.0, without authority."""
    _need(type(value) is dict)
    return _hash({key: item for key, item in value.items() if key != 'record_hash'})


def _unique(values):
    _need(len(values) == len(set(values)))


def _check_request(value, goal, window, policy, cut):
    request = value['evaluation_request']
    ref = value['evaluation_query_ref']
    _need(request.get('mode') == 'structured'
        and 'model_profile_id' in request and request['model_profile_id'] is None
        and request.get('tool_steps') == []
        and request.get('allow_external_tools') is False
        and request.get('clarifications') == []
        and _json(request.get('entity_ids')) == _json(goal['entity_ids'])
        and _json(request.get('fields')) == _json(goal['required_fields'])
        and _json(request.get('evidence_requirement')) == _json(goal['evidence'])
        and _json(request.get('relation_query')) == _json(goal['relation_query'])
        and request.get('business_timezone') == policy['timezone']
        and _json(request.get('resolved_time')) == _json(window)
        and _instant(request.get('reference_at')) == cut
        and ref['request_hash'] == _hash(request)
        and ref['result_hash'] == _hash(value['evaluation_result']))
    # These are actual server-side call controls, not user/request fields.
    # The wrapper authenticates that its real local data_agent used them.
    _need(value['query_controls'] == {'cached_only': True, 'learning': False, 'emit_gaps': False, 'include_trusted_cases': False})
    # A request pin alone cannot establish that the real query used its cut.
    # Retain the original full data_agent state; do not rewrite a report whose
    # actual latest selector ignored resolved_time and rolled its own window.
    state = value['evaluation_result']
    report = state.get('result')
    need = state.get('need')
    _need(type(report) is dict and type(need) is dict
        and _json(state.get('request')) == _json(request)
        and _json(need.get('entity_ids')) == _json(goal['entity_ids'])
        and _json(need.get('fields')) == _json(goal['required_fields'])
        and state.get('status') == report.get('status')
        and _json(report.get('resolved_time')) == _json(window)
        and _json(report.get('evidence_requirement')) == _json(goal['evidence'])
        and _instant(report.get('evaluated_at')) == cut)


def check_record(raw, *, expected_scope, goal_snapshot, preparation_policy, trusted_now):
    """Check stored consistency against independently supplied original pins.

    Neither this function nor a self-consistent record authenticates a source,
    producer completion, receipt, DataRun, Evidence bytes or execution grant.
    Historical readers must fetch those real records and their real audits.
    """
    if type(raw) is dict and raw.get('schema') == 'workflow-goal-evaluation-record-v299':
        from .workflow_goal_evaluation_contracts_v299 import check_record as append_record
        return append_record(raw, expected_scope=expected_scope, goal_snapshot=goal_snapshot,
            preparation_policy=preparation_policy, trusted_now=trusted_now)
    _need(type(raw) is dict and all(raw.get(key) is False for key in FLAGS)
        and type(raw.get('query_controls')) is dict
        and raw['query_controls'].get('cached_only') is True
        and raw['query_controls'].get('learning') is False
        and raw['query_controls'].get('emit_gaps') is False
        and raw['query_controls'].get('include_trusted_cases') is False)
    value = _parse(GoalEvaluationRecord, raw)
    expected = _parse(EvaluationScope, expected_scope)
    snapshot = _parse(GoalSnapshot, goal_snapshot)
    _need(_json(value['scope']) == _json(expected)
        and _json(value['goal_snapshot']) == _json(snapshot)
        and _hash(snapshot) == expected['goal_snapshot_hash']
        and _hash(snapshot['spec']) == snapshot['hash'])
    goal = _goal(snapshot['spec'])
    policy = check_preparation_policy(snapshot['spec'], preparation_policy)
    _need(_json(value['preparation_policy']) == _json(policy))
    cut, checked, now = (_instant(value['cut_at']), _instant(value['cut_checked_at']), _instant(trusted_now))
    _need(cut <= checked <= now)
    if goal['deadline'] is not None:
        _need(cut < _instant(goal['deadline']))
    initial = snapshot['time']
    _need(type(initial.get('reference_at')) is str)
    initial_cut = _instant(initial['reference_at'])
    _need(initial_cut <= cut and _json(initial) == _json(_resolved(goal['evidence']['time'], initial_cut)))
    window = derive_evaluation_window(snapshot['spec'], policy,
        cut_at=value['cut_at'], trusted_now=value['cut_checked_at'],
        **({'initial_window': initial} if policy['time_basis'] == 'fixed_window' else {}))
    _need(_json(value['derived_window']) == _json(window)
        and value['original_query_ref']['query_id'] != value['evaluation_query_ref']['query_id']
        and value['record_hash'] == record_hash(value))
    producer = value['producer']
    _need(value['evaluation_id'] == producer['invocation_id']
        and value['request_id'] == producer['job_id']
        and producer['workspace_id'] == expected['workspace_id']
        and producer['task_id'] == expected['task_id']
        and producer['scope_hash'] == _hash(expected)
        and producer['request_key_hash'] == _hash(value['request_key']))
    trigger = value['trigger']
    _need(producer['invocation_id'] != trigger['reader_invocation_id'])
    if trigger['kind'] == 'acquisition_completed':
        _need(policy['time_basis'] == 'rolling_latest' and policy['allow_acquisition'] is True
            and trigger['reader_context_hash'] is not None
            and trigger['resense_invocation_id'] is not None
            and trigger['resense_receipt_hash'] is not None and bool(value['ack_refs'])
            and any(ack['origin_task_id'] == expected['task_id'] for ack in value['ack_refs'])
            and trigger['resense_invocation_id'] not in {
                trigger['reader_invocation_id'], producer['invocation_id']})
    else:
        _need(trigger['resense_invocation_id'] is None and trigger['resense_receipt_hash'] is None)
    _check_request(value, goal, window, policy, cut)
    for key in ('observation_id', 'event_id', 'memory_id'):
        _unique([item[key] for item in value['ack_refs']])
    _unique([item['acquisition_id'] for item in value['ack_refs'] if 'origin_kind' not in item])
    # Exact latest bounds; no future receipt or cross-consumer reference is
    # made valid merely by recomputing this record's hash. Existing sources
    # can have a different origin Task; the actual reader must prove IAM and
    # the original reception chain. New resense ownership is checked there.
    from .temporal import matches
    admitted = value['evaluation_result']['result'].get('observation_evidence')
    _need(type(admitted) is list and len(admitted) <= 1000
        and all(type(item) is dict and type(item.get('observation_id')) is str for item in admitted))
    admitted_ids = [item['observation_id'] for item in admitted]
    _unique(admitted_ids)
    _need(set(admitted_ids) == {ack['observation_id'] for ack in value['ack_refs'] if ack['query_admitted']})
    for ack in value['ack_refs']:
        if ack.get('origin_kind') == 'mqtt_passive_v297':
            pins = ack['mqtt_origin']
            declared_test = pins['test_origin'] is not None
            _need(ack['origin_task_id'] is None and ack['query_admitted'] is True
                and pins['instance_id'] == expected['instance_id'] and pins['release_id'] == expected['release_id']
                and pins['package_id'] == expected['package_id'] and pins['entity_id'] == ack['entity_id']
                and pins['input_sha256'] == pins['packet_evidence_sha256']
                and ack['simulation_only'] is declared_test and ack['test_only'] is declared_test
                and _instant(pins['received_at']) <= _instant(pins['receipt_created_at']) <= cut)
        _unique(ack['fields'])
        observed, received = _instant(ack['observed_at']), _instant(ack['received_at'])
        _need(ack['workspace_id'] == expected['workspace_id'] and ack['task_id'] == expected['task_id']
            and ack['entity_id'] in goal['entity_ids']
            and set(ack['fields']) <= set(goal['required_fields'])
            and observed <= received <= cut
            and ack['query_admitted'] is (ack['observation_id'] in admitted_ids)
            and (bool(ack['fields']) and matches(observed, window, evaluated_at=cut)
                if ack['query_admitted'] else trigger['kind'] == 'acquisition_completed')
            and (not ack['simulation_only'] or value['simulation_only'])
            and (not ack['test_only'] or value['test_only']))
    return copy.deepcopy(value)


def seal_record(unsigned, *, expected_scope, goal_snapshot, preparation_policy, trusted_now):
    """Seal already captured real refs; create no observation or permission."""
    _need(type(unsigned) is dict and 'record_hash' not in unsigned)
    value = copy.deepcopy(unsigned)
    value['record_hash'] = record_hash(value)
    return check_record(value, expected_scope=expected_scope, goal_snapshot=goal_snapshot,
        preparation_policy=preparation_policy, trusted_now=trusted_now)


def preparation_policy_schema():
    return copy.deepcopy(PreparationPolicy.model_json_schema())


def record_schema():
    return copy.deepcopy(GoalEvaluationRecord.model_json_schema())
