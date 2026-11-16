"""Fixed requests and immutable proposal shape for one local read tool."""
import copy
from typing import Literal

from pydantic import Field, field_validator

from . import same_goal_continuation_contracts_v312 as drafts
from . import same_goal_continuation_grant_contracts_v313 as grants
from .contracts import digest

Closed, ID, Hash, Positive = grants.Closed, grants.ID, grants.Hash, grants.Positive
KIND = 'execute_goal_continuation_read_v314'
JOB = 'same-goal-continuation-execution-job-v314'
APPROVAL = 'same-goal-continuation-read-approval-v314'
CONTEXT = 'same-goal-continuation-execution-context-v314'
PROPOSAL = 'same-goal-continuation-execution-proposal-v314'
PREVIEW = 'same-goal-continuation-execution-preview-v314'
PLAN = 'same-goal-continuation-execution-plan-v314'
INDEX = 'continuation_execution_bindings_v314'
COMMAND = 'same-goal-continuation-execution-command-v314'
VIEW = 'same-goal-continuation-execution-view-v314'
RECEIPT = 'same-goal-continuation-execution-receipt-v314'
ACTION_SUBMIT = 'same_goal_continuation.execution_submitted_v314'
ACTION_DECIDE = 'same_goal_continuation.execution_decided_v314'
ACTION_START = 'same_goal_continuation.execution_started_v314'
ACTION_CANCEL = 'same_goal_continuation.execution_cancelled_v314'
ACTION_REVIEW = 'same_goal_continuation.execution_reviewed_v314'
ACTION_RECOVER = 'same_goal_continuation.execution_recovered_v314'
ACTIONS = frozenset({ACTION_SUBMIT, ACTION_DECIDE, ACTION_START, ACTION_CANCEL, ACTION_REVIEW, ACTION_RECOVER})
RESOURCES = {'calls': 1, 'steps': 1, 'cost_minor_units': 0}
FLAGS = dict(grants.FLAGS)
dump, record_hash = grants.dump, grants.record_hash
need = drafts.advice.need


class GrantRef(Closed):
    id: ID
    record_hash: Hash
    version: Literal[1]

    @field_validator('version', mode='before')
    @classmethod
    def strict_version(cls, value):
        need(type(value) is int and value == 1)
        return value


class PreviewRequest(Closed):
    request_id: ID
    expected_task_version: Positive
    expected_continuation_version: Positive
    revision_hash: Hash
    source_hash: Hash
    grant_ref: GrantRef
    supersedes_plan_id: ID | None = None
    plan_origin: Literal['human_revision']
    rationale: grants.Reason

    @field_validator('rationale')
    @classmethod
    def meaningful(cls, value):
        need(bool(value.strip()) and '\x00' not in value)
        return value


class SubmitRequest(PreviewRequest):
    preview_hash: Hash


class StartRequest(Closed):
    request_id: ID
    expected_execution_version: Positive
    reason: grants.Reason

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        need(bool(value.strip()) and '\x00' not in value)
        return value


class CancelRequest(StartRequest):
    pass


class RecoverRequest(StartRequest):
    pass


class DecideRequest(StartRequest):
    submission_hash: Hash
    decision: Literal['approve', 'return']


class ReviewRequest(StartRequest):
    output_hash: Hash
    decision: Literal['adopt', 'return']


class ContinuationRef(Closed):
    id: ID
    version: Positive
    revision_hash: Hash
    source_hash: Hash


class Proposal(Closed):
    schema_: Literal[PROPOSAL] = Field(alias='schema')
    workspace_id: ID
    task_id: ID
    advice_plan_id: ID
    continuation_ref: ContinuationRef
    grant_ref: GrantRef
    supersedes_plan_id: ID | None
    grant: dict
    source_hash: Hash
    source: dict
    draft: dict
    plan_origin: Literal['human_revision']
    rationale: grants.Reason
    original_candidate_ref: str | None
    submitter_id: ID
    business_actor_id: ID
    resources: dict
    record_hash: Hash


def check_proposal(value):
    """Validate sealed structure. Actual grant, draft and policy remain IO proofs."""
    drafts.advice.base._json(value)
    Proposal.model_validate(value)
    need(value['record_hash'] == record_hash(value) and digest(value['resources']) == digest(RESOURCES))
    need(bool(value['rationale'].strip()) and '\x00' not in value['rationale'])
    draft = value['draft']
    fields = drafts.draft(draft['draft'])
    need(digest(fields) == digest(draft['draft']) and draft['schema'] == drafts.REVISION
        and all(fields[key].strip() and '\x00' not in fields[key]
            for key in ('purpose', 'work_description', 'expected_result'))
        and fields['route'] in {None, 'business_conclusion'}
        and value['original_candidate_ref'] == fields['candidate_ref'])
    cont = value['continuation_ref']; grant = value['grant']; source = value['source']
    need(draft['record_hash'] == record_hash(draft) == cont['revision_hash']
        and draft['continuation_id'] == cont['id'] and type(draft['version']) is int
        and draft['version'] == cont['version'] and draft['source_hash'] == cont['source_hash']
        and value['source_hash'] == digest(source) == cont['source_hash']
        and source['decision_intent'] == 'business_conclusion')
    need(grant['schema'] == grants.GRANT and grant['record_hash'] == record_hash(grant) == value['grant_ref']['record_hash']
        and grant['grant_id'] == value['grant_ref']['id'] and grant['continuation_id'] == cont['id']
        and grant['workspace_id'] == value['workspace_id'] and grant['task_id'] == value['task_id']
        and grant['advice_plan_id'] == value['advice_plan_id'] and grant['source_hash'] == value['source_hash']
        and digest(grant['source']) == value['source_hash'])
    basis = grant['basis']
    need(basis['schema'] == grants.BASIS and basis['record_hash'] == record_hash(basis)
        and digest(basis['continuation_ref']) == digest(cont)
        and basis['input_hash'] == digest(basis['inputs'])
        and basis['input_schema_hash'] == digest(grants.input_schema())
        and basis['output_schema_hash'] == digest(grants.output_schema())
        and basis['agent_role'] == 'data' and type(basis['max_calls']) is int and basis['max_calls'] == 1
        and all(basis[key] is False for key in ('side_effect', 'external_allowed', 'cloud_allowed')))
    return copy.deepcopy(value)
