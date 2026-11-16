"""A separate explicit graph-routing family; legacy human DTOs stay unchanged."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .contracts import Contract, GoalSpec
from .models import DomainInstance
from .services import scoped
from .setup_api import DB, P
from . import signal_graph_routing_v134 as service

router = APIRouter(prefix='/{identity}/signal-graph-routing-v134')
Reason = Annotated[StrictStr, Field(min_length=1, max_length=2000)]


class Preview(Contract):
    schema: Literal['signal-graph-routing-preview-command-v134']
    expected_version: StrictInt = Field(ge=1)
    release_id: UUID
    source_id: UUID
    extractor_id: UUID
    entity_ids: list[UUID] = Field(min_length=1, max_length=30)
    business_id: StrictStr = Field(pattern=r'^business_[a-f0-9]{16}$')
    entity_binding: Literal['each_trigger_object']
    entity_input_fields: list[StrictStr] = Field(max_length=100)
    graph_inputs: dict = Field(strict=True)
    goal_spec: GoalSpec
    max_tasks: StrictInt = Field(ge=1, le=100)
    reason: Reason

    @model_validator(mode='after')
    def explicit_template(self):
        if not self.reason.strip() or '\x00' in self.reason:
            raise ValueError('请填写有效授权说明')
        self.reason.encode('utf-8')
        if len(set(self.entity_ids)) != len(self.entity_ids) or len(set(self.entity_input_fields)) != len(self.entity_input_fields):
            raise ValueError('对象和对象输入字段不能重复')
        if any(not field or '\x00' in field for field in self.entity_input_fields):
            raise ValueError('对象输入字段无效')
        if set(self.entity_input_fields) & set(self.graph_inputs):
            raise ValueError('对象替换字段不能同时作为常量输入')
        goal = self.goal_spec
        if (set(goal.entity_ids) != {str(value) for value in self.entity_ids}
            or goal.deadline is not None or goal.observation_mode != 'existing_only'
            or goal.data_query is not None or goal.relation_query is not None
            or not {'max_steps', 'max_tool_calls'} <= goal.model_fields_set):
            raise ValueError('请明确原对象范围、累计步骤与调用额度；截止时间随本次授权到期，不额外授权查询、关系或补采')
        if not any(item.kind == 'evidence_sufficient' for item in goal.criteria):
            raise ValueError('原目标必须要求证据充分')
        if not any(item.kind == 'human_confirmation' for item in goal.criteria):
            raise ValueError('异常流程必须保留明确人工验收')
        return self


class Enable(Preview):
    schema: Literal['signal-graph-routing-enable-command-v134']
    request_id: UUID
    preview_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_graph_execution: StrictBool

    @model_validator(mode='after')
    def explicit_consent(self):
        if self.confirm_graph_execution is not True:
            raise ValueError('请明确确认启动这份完整业务流程及其本机执行范围')
        return self


class Pause(Contract):
    schema: Literal['signal-graph-routing-pause-command-v134']
    request_id: UUID
    expected_version: StrictInt = Field(ge=1)
    reason: Reason

    @model_validator(mode='after')
    def text(self):
        if not self.reason.strip() or '\x00' in self.reason:
            raise ValueError('请填写有效暂停说明')
        self.reason.encode('utf-8')
        return self


def wire(value):
    from .report_full_semantic_graph_wire import canonical_wire
    return canonical_wire(value)


@router.get('/options')
def options(identity: UUID, p: P, db: DB):
    return wire(service.options(db, p, scoped(db, DomainInstance, str(identity), p)))


@router.post('/preview')
def preview(identity: UUID, body: Preview, p: P, db: DB):
    return wire(service.preview(db, p, scoped(db, DomainInstance, str(identity), p), body.model_dump(mode='json')))


@router.get('')
def listing(identity: UUID, p: P, db: DB):
    return wire(service.reply(db, p, scoped(db, DomainInstance, str(identity), p)))


@router.get('/requests/{request_id}')
def request(identity: UUID, request_id: UUID, p: P, db: DB):
    return wire(service.saved_request(db, p, scoped(db, DomainInstance, str(identity), p), str(request_id)))


@router.post('/enable', status_code=201)
def enable(identity: UUID, body: Enable, p: P, db: DB):
    value = service.command(db, p, str(identity), 'enable', body.model_dump(mode='json'))
    db.commit()
    return wire(value)


@router.post('/{route_id}/pause')
def pause(identity: UUID, route_id: UUID, body: Pause, p: P, db: DB):
    value = service.command(db, p, str(identity), 'pause', body.model_dump(mode='json'), str(route_id))
    db.commit()
    return wire(value)
