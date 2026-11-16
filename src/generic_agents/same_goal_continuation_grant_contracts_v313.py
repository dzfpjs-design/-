"""Closed additive read-tool contracts, not approval or execution authority."""
import copy
from typing import Annotated, Literal

from pydantic import Field, StrictStr, field_validator

from . import same_goal_continuation_contracts_v312 as draft
from .contracts import Manifest, digest

Closed, ID, Hash, Pin, Positive = draft.Closed, draft.ID, draft.Hash, draft.Pin, draft.Positive
CONTEXT = 'same-goal-continuation-grant-context-v313'
GRANT = 'same-goal-continuation-grant-v313'
BASIS = 'same-goal-continuation-grant-basis-v313'
COMMAND = 'same-goal-continuation-grant-command-v313'
VIEW = 'same-goal-continuation-grant-view-v313'
RECEIPT = 'same-goal-continuation-grant-receipt-v313'
ACTION_ISSUE = 'same_goal_continuation.grant_issued_v313'
ACTION_REVOKE = 'same_goal_continuation.grant_revoked_v313'
ACTIONS = frozenset({ACTION_ISSUE, ACTION_REVOKE})
FLAGS = dict(draft.FLAGS)
SCOPE = 'goal_continuation_v313'
SUBJECT_KIND = 'goal_continuation_read_v313'
MANIFEST_SCHEMA = 'goal-continuation-aggregate-tool-v313'
NAMESPACE = 'goal_continuation_aggregate_v313'
BINDINGS = ('observation_records', 'archive_records')
OPERATIONS = ('mean', 'sum', 'min', 'max', 'count')
Operation = Literal['mean', 'sum', 'min', 'max', 'count']
FieldName = Annotated[StrictStr, Field(min_length=1, max_length=100)]
Reason = Annotated[StrictStr, Field(min_length=1, max_length=2000)]
NAMES = frozenset('aggregate_' + binding + '_v313' for binding in BINDINGS)
MAX_RECORDS = 1000


class PolicyRef(Closed):
    policy_id: ID
    version_id: ID
    revision: Positive
    record_hash: Hash


class IssueRequest(Closed):
    request_id: ID
    expected_task_version: Positive
    expected_continuation_version: Positive
    revision_hash: Hash
    source_hash: Hash
    capability_pin: Pin
    field: FieldName
    operation: Operation
    policy_ref: PolicyRef
    validity_seconds: Literal[7200] = 7200
    reason: Reason

    @field_validator('validity_seconds', mode='before')
    @classmethod
    def fixed_validity(cls, value):
        draft.advice.need(type(value) is int and value == 7200)
        return value

    @field_validator('field', 'reason')
    @classmethod
    def meaningful(cls, value):
        draft.advice.need(bool(value.strip()) and '\x00' not in value)
        return value


class RevokeRequest(Closed):
    request_id: ID
    expected_grant_version: Positive
    reason: Reason

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        draft.advice.need(bool(value.strip()) and '\x00' not in value)
        return value


def dump(model, value): return draft.dump(model, value)
def record_hash(value): return draft.record_hash(value)


def input_schema():
    record = {'type': 'object', 'additionalProperties': False,
        'required': ['entity_id', 'version', 'attributes'], 'properties': {
            'entity_id': {'type': 'string', 'format': 'uuid'},
            'version': {'type': 'integer', 'minimum': 1},
            'attributes': {'type': 'object', 'maxProperties': 1,
                'additionalProperties': {'type': ['number', 'null']}}}}
    return {'type': 'object', 'additionalProperties': False,
        'required': ['field', 'operation', 'records'], 'properties': {
            'field': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'operation': {'type': 'string', 'enum': list(OPERATIONS)},
            'records': {'type': 'array', 'minItems': 1, 'maxItems': MAX_RECORDS,
                'items': record}}}


def output_schema():
    record = {'type': 'object', 'additionalProperties': False,
        'required': ['entity_id', 'version'], 'properties': {
            'entity_id': {'type': 'string', 'format': 'uuid'},
            'version': {'type': 'integer', 'minimum': 1}}}
    return {'type': 'object', 'additionalProperties': False,
        'required': ['field', 'operation', 'value', 'sample_count', 'missing_count', 'method', 'provenance'],
        'properties': {'field': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'operation': {'type': 'string', 'enum': list(OPERATIONS)},
            'value': {'type': ['number', 'null']},
            'sample_count': {'type': 'integer', 'minimum': 0, 'maximum': MAX_RECORDS},
            'missing_count': {'type': 'integer', 'minimum': 0, 'maximum': MAX_RECORDS},
            'method': {'const': 'registered_archive_aggregate'},
            'provenance': {'type': 'array', 'minItems': 1, 'maxItems': MAX_RECORDS,
                'items': record}}}


def manifest(binding='observation_records'):
    """A registrable manifest; registration/publication still require real IAM."""
    draft.advice.need(type(binding) is str and binding in BINDINGS)
    label = '已核观测' if binding == 'observation_records' else '已核档案'
    return Manifest.model_validate({'kind': 'tool', 'namespace': NAMESPACE,
        'name': 'aggregate_' + binding + '_v313', 'version': '1.0.0',
        'owner': '通用三智能体平台', 'description': label + '数值汇总（原目标增量授权，只读）',
        'input_schema': input_schema(), 'output_schema': output_schema(),
        'allowed_agent_roles': ['data'],
        'execution': {'adapter': 'builtin', 'handler': 'aggregate_values',
            'dispatch_scope': SCOPE, 'side_effect': False, 'input_bindings': {'records': binding}},
        'data_policy': {'cloud_allowed': False, 'external_allowed': False},
        'budget': {'max_attempts': 1, 'max_concurrency': 1}, 'dependencies': [],
        'specification': {'schema': MANIFEST_SCHEMA}}).model_dump(mode='json')


def applies(value):
    if isinstance(value, Manifest): value = value.model_dump(mode='json')
    if type(value) is not dict: return False
    execution = value.get('execution'); spec = value.get('specification')
    return (type(execution) is dict and execution.get('dispatch_scope') == SCOPE
        or type(spec) is dict and spec.get('schema') == MANIFEST_SCHEMA
        or value.get('namespace') == NAMESPACE
        or type(value.get('name')) is str and value['name'] in NAMES)


def validate_manifest(value):
    """Pure exact provider facet. Never call generic binding validation here."""
    if isinstance(value, Manifest): value = value.model_dump(mode='json')
    draft.advice.base._json(value)
    actual = Manifest.model_validate(copy.deepcopy(value)).model_dump(mode='json')
    # Compare the original too: Pydantic coercion is not a provider contract.
    bindings = actual['execution'].get('input_bindings')
    draft.advice.need(type(bindings) is dict and set(bindings) == {'records'})
    binding = bindings['records']
    expected = manifest(binding)
    draft.advice.need(digest(value) == digest(actual) == digest(expected),
        '仅支持固定本地只读汇总、data岗位和服务器绑定记录；不得混入图能力、云、外发或自定义处理器')
    return actual
