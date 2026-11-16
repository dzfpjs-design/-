"""Closed human draft contracts; no approval, SDK or execution authority."""
from typing import Annotated, Literal

from pydantic import Field, StrictStr, field_validator

from . import same_goal_advice_contracts_v302 as advice
from .contracts import digest
from .temporal import instant

Closed, ID, Hash, Pin = advice.Closed, advice.ID, advice.Hash, advice.CapabilityPin
Positive, Text = advice.Positive, advice.Text
CONTEXT = 'same-goal-continuation-context-v312'
SOURCE = 'same-goal-continuation-source-v312'
PREVIEW = 'same-goal-continuation-preview-v312'
PLAN = 'same-goal-continuation-plan-v312'
REVISION = 'same-goal-continuation-revision-v312'
COMMAND = 'same-goal-continuation-command-v312'
VIEW = 'same-goal-continuation-view-v312'
RECEIPT = 'same-goal-continuation-receipt-v312'
ACTION_PREVIEW = 'same_goal_continuation.previewed_v312'  # Reserved; preview itself is read-only.
ACTION_SAVE = 'same_goal_continuation.saved_v312'
ACTION_REVISE = 'same_goal_continuation.revised_v312'
ACTION_WITHDRAW = 'same_goal_continuation.withdrawn_v312'
ACTIONS = frozenset({ACTION_SAVE, ACTION_REVISE, ACTION_WITHDRAW})
FLAGS = {'can_dispatch': False, 'execution_approved': False,
    'goal_completion_verified': False, 'cloud_send_authorized': False}
Intent = Literal['information_acquisition', 'operational_action', 'business_conclusion']


class DraftFields(Closed):
    route: Annotated[StrictStr, Field(max_length=64)] | None = None
    candidate_ref: Annotated[StrictStr, Field(max_length=128)] | None = None
    selected_tools: list[Pin] = Field(default_factory=list, max_length=6)
    purpose: Annotated[StrictStr, Field(max_length=2000)] = ''
    work_description: Annotated[StrictStr, Field(max_length=4000)] = ''
    expected_result: Annotated[StrictStr, Field(max_length=2000)] = ''
    assignee_id: ID | None = None
    due_at: Annotated[StrictStr, Field(max_length=64)] | None = None

    @field_validator('due_at')
    @classmethod
    def aware_due_at(cls, value):
        if value is not None: instant(value)
        return value

    @field_validator('selected_tools')
    @classmethod
    def unique_tools(cls, values):
        advice.need(len({item.id for item in values}) == len(values))
        return values


class PreviewRequest(Closed):
    request_id: ID
    expected_task_version: Positive
    source_hash: Hash
    draft: DraftFields
    reason: Text


class SaveRequest(PreviewRequest):
    preview_hash: Hash


class ReviseRequest(SaveRequest):
    expected_continuation_version: Positive


class WithdrawRequest(Closed):
    request_id: ID
    expected_continuation_version: Positive
    reason: Text


def dump(model, value):
    return advice._parse(model, value)


def draft(value):
    return dump(DraftFields, value)


def has_human_content(value):
    value = draft(value)
    return (any(value[key].strip() for key in ('purpose', 'work_description', 'expected_result'))
        or any(value[key] is not None and bool(value[key].strip()) for key in ('route', 'candidate_ref'))
        or bool(value['selected_tools']) or value['assignee_id'] is not None or value['due_at'] is not None)


def record_hash(value):
    return digest({key: item for key, item in value.items() if key != 'record_hash'})
