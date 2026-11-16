"""Closed image recipe commands; old V208 request types remain unchanged."""
from typing import Literal, Annotated
from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from .contracts import Contract
from .authored_workflow_store_v208 import ID, Hash, OriginRef
from .layered_approval_contract_v175 import original_json

PREFIX='/api/setup/authored-image-workflow-graphs'
PATH=PREFIX+'/adopt'
BINDING='authored-image-workflow-binding-v243'
TEMPLATE='authored-image-technical-human-linear-template-v243'
PREVIEW='authored-image-workflow-preview-v243'
RESULT='authored-image-workflow-adoption-result-v243'
COMMAND='authored-image-workflow-command-v243'
OPERATIONS=('quality','dedup','report')


class Pin(Contract):
    capability_id: ID
    capability_hash: Hash
    actor_id: ID


class Tools(Contract):
    quality: Pin
    dedup: Pin
    report: Pin


class Preview(Contract):
    schema: Literal['authored-image-workflow-preview-command-v243']
    configuration_revision: Annotated[StrictInt,Field(ge=1)]
    configuration_hash: Hash
    business_name: Annotated[str,Field(strict=True,min_length=1,max_length=100)]
    tool_bindings: Tools
    role_bindings: dict[str,ID]
    node_actions: dict[str,Literal['data']]
    external_node_ids: list[str]=Field(max_length=0)
    prior_origin: OriginRef | None

    @model_validator(mode='after')
    def selected(self):
        if not self.business_name.strip() or '\x00' in self.business_name:raise ValueError('请选择原业务')
        if not 1<=len(self.role_bindings)<=16 or any(not key.strip() or '\x00' in key for key in self.role_bindings):raise ValueError('请明确原人工岗位')
        if self.node_actions!={operation:'data' for operation in OPERATIONS}:raise ValueError('三项技术动作均须为data')
        return self


class Adopt(Preview):
    schema: Literal['authored-image-workflow-adopt-command-v243']
    request_id: ID
    preview_hash: Hash
    consent: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls,value):
        if type(value) is not dict or value.get('consent') is not True:raise ValueError('请明确采用本次图像技术复核设计')
        return value


class ToolsCommand(Contract):
    schema: Literal['authored-image-tools-prepare-command-v243']
    request_id: ID
    confirm_local: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls,value):
        if type(value) is not dict or value.get('confirm_local') is not True:raise ValueError('请明确确认仅在本地登记工具')
        return value


def parse(raw,model):
    if type(raw) is not bytes or not 0<len(raw)<= (16384 if model is ToolsCommand else 65536):
        raise HTTPException(413,'图像编制请求为空或超过合同大小，未登记或采用')
    try:return original_json(raw,model)
    except (ValueError,TypeError,UnicodeError,RecursionError,OverflowError):
        raise HTTPException(422,'图像编制请求不符合固定合同，未接受任意图或执行参数') from None


def preview_body(body):
    value={key:body.model_dump(mode='json')[key] for key in Preview.model_fields}
    value['schema']='authored-image-workflow-preview-command-v243'
    return value
