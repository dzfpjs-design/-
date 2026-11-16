"""Closed command contracts; execution time and generation are server-owned."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, StrictStr, field_validator

from .contracts import Contract, digest

SCHEMA = 'goal-evaluation-append-command-v297'
APPROVAL = 'goal-evaluation-append-approval-v297'
JOB_SCHEMA = 'goal-evaluation-append-job-v297'
KIND = 'append_goal_evaluation_v297'
ACTION = 'goal_evaluation.append_accepted_v297'
ID = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
HASH = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]


class Closed(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)


class AppendRequest(Closed):
    request_id: ID
    expected_goal_hash: HASH
    expected_mature_head_hash: HASH
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000, pattern=r'\S')]

    @field_validator('request_id')
    @classmethod
    def canonical_nonce(cls, value):
        if str(UUID(value)) != value: raise ValueError('追加请求号必须为规范UUID')
        return value


class AppendRecord(Closed):
    schema: Literal['goal-evaluation-append-command-v297']
    command_id: ID
    workspace_id: ID
    task_id: ID
    actor_id: ID
    request_id: ID
    body: AppendRequest
    body_hash: HASH
    goal_version: Annotated[StrictInt, Field(ge=1)]
    goal_hash: HASH
    goal_snapshot_hash: HASH
    native_goal_context_hash: HASH
    scope: dict
    scope_hash: HASH
    source_binding_hash: HASH
    capability_id: ID
    capability_hash: HASH
    release_version: StrictStr
    inputs: dict
    input_hash: HASH
    original_query_ref: dict
    base_evaluation_ref: dict
    base_mature_head_hash: HASH
    base_goal_check_job_id: ID
    base_goal_check_memory_id: ID
    base_goal_check_report_hash: HASH
    job_id: ID
    invocation_key: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    accepted_audit_id: ID
    accepted_at: StrictStr
    record_hash: HASH


def invocation_key(task_id, request_id):
    return 'goal-evaluation-append:' + task_id + ':' + request_id


def body_hash(task_id, body):
    return digest({'task_id': task_id, 'body': body})

