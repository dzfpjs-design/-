"""Closed, pure approval commands and state transitions. No execution authority."""
import copy
import hashlib
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ID = Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
SHA = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
ROLE = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,99}$')]


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Original(Closed):
    request_id: ID


class UnitCommand(Original):
    unit_id: ID
    expected_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    parent_id: ID | None = None
    enabled: bool = True


class RoleCommand(Original):
    grant_id: ID
    expected_version: int = Field(ge=0)
    unit_id: ID
    user_id: ID
    role_key: ROLE
    enabled: bool = True


class Stage(Closed):
    name: str = Field(min_length=1, max_length=200)
    role_key: ROLE
    scope_id: ID


class PolicyCommand(Original):
    instance_id: ID
    business_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    scope_id: ID
    stages: list[Stage] = Field(min_length=1, max_length=20)
    executor: Stage
    acceptance: Stage
    enabled: bool = True


class TaskCommand(Original):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('请填写本次操作的依据或原因')
        return value


class DraftValue(Closed):
    title: str = Field(min_length=1, max_length=300)
    note: str = Field(max_length=12000)
    graph_inputs: dict
    goal_spec: dict | None


class ReviseCommand(TaskCommand):
    value: DraftValue


class DecisionCommand(TaskCommand):
    submission_hash: SHA
    decision: Literal['approve', 'return']


class AcceptanceCommand(TaskCommand):
    basis_hash: SHA
    decision: Literal['accept', 'return']


def original_json(raw, command):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 1024 * 1024:
        raise ValueError('审批原请求大小不符合要求')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('原请求存在重复字段')
            result[key] = value
        return result
    text = raw.decode('utf-8', 'strict')
    value = json.loads(text, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非法数值')))
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8', 'strict')
    typed = command.model_validate(value)
    return typed, text, hashlib.sha256(raw).hexdigest()


def next_state(status, stage, count, operation):
    if type(stage) is not int or type(count) is not int or not 1 <= count <= 20 or not 0 <= stage <= count:
        raise ValueError('审批层级无法核对')
    if operation == 'submit' and status in {'draft', 'returned', 'withdrawn'}:
        return 'in_review', 0
    if operation == 'approve' and status == 'in_review' and stage < count:
        return ('approved' if stage + 1 == count else 'in_review'), stage + 1
    if operation == 'return' and status == 'in_review' and stage < count:
        return 'returned', stage
    if operation == 'withdraw' and status in {'in_review', 'approved'}:
        return 'withdrawn', stage
    if operation == 'start' and status == 'approved' and stage == count:
        return 'executing', stage
    if operation == 'accept' and status == 'executing':
        return 'accepted', stage
    if operation == 'acceptance_return' and status == 'executing':
        return 'acceptance_returned', stage
    raise ValueError('当前审批状态不允许本次操作')


def independent(actor, owner, approvers=(), executors=(), *, acceptance=False):
    if actor == owner or acceptance and actor in set(approvers) | set(executors):
        raise ValueError('必须由独立责任人员办理，管理员也不能代替此职责隔离')


def content(value):
    checked = DraftValue.model_validate(value).model_dump()
    if not checked['title'].strip():
        raise ValueError('请填写任务名称')
    if checked['goal_spec'] is not None:
        from .contracts import GoalSpec
        checked['goal_spec'] = GoalSpec.model_validate(checked['goal_spec']).model_dump()
    return copy.deepcopy(checked)
