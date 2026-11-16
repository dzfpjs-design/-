"""Closed human commands; no public origin token or source override."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import Field,StrictInt,StrictStr,StrictBool,model_validator
from .contracts import Contract

Positive=Annotated[StrictInt,Field(ge=1)]
Reason=Annotated[StrictStr,Field(min_length=1,max_length=2000)]


class Pause(Contract):
    schema:Literal['signal-approval-pause-v247']
    request_id:UUID
    expected_version:Positive
    reason:Reason

    @model_validator(mode='after')
    def checked_text(self):
        if not self.reason.strip() or '\x00' in self.reason:raise ValueError('请填写明确说明')
        self.reason.encode('utf-8')
        return self


class Enable(Pause):
    schema:Literal['signal-approval-enable-v247']
    release_id:UUID
    source_id:UUID
    extractor_id:UUID
    entity_ids:list[UUID]=Field(min_length=1,max_length=20)
    business_id:Annotated[StrictStr,Field(min_length=1,max_length=100)]
    valid_for_hours:Annotated[StrictInt,Field(ge=1,le=24)]
    max_tasks:Annotated[StrictInt,Field(ge=1,le=100)]
    options_hash:Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$')]
    confirm_approval_drafts:StrictBool

    @model_validator(mode='after')
    def unique_objects(self):
        if self.confirm_approval_drafts is not True:raise ValueError('请明确只建立审批草稿')
        if len(set(self.entity_ids))!=len(self.entity_ids):raise ValueError('对象不能重复')
        return self


class Retry(Pause):
    schema:Literal['signal-approval-retry-v247']


COMMANDS={'enable':Enable,'pause':Pause,'retry':Retry}
