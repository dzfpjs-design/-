"""Closed authored-service recipe commands, independent of report recovery.

Design: preserve V208's independently saved human flow and adoption ledger;
freeze real published tools and typed candidate plans; accept neither raw graphs
nor model results. V1 is explicitly simulation-only and grants no execution.
"""
import copy
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator

from .authored_workflow_store_v208 import Hash, ID, OriginRef
from .contracts import Contract
from .layered_approval_contract_v175 import original_json


PREFIX = '/api/setup/authored-service-workflow-graphs'
PATH = PREFIX + '/adopt'
BINDING = 'authored-service-workflow-binding-v270'
TEMPLATE = 'authored-service-human-linear-template-v270'
PREVIEW = 'authored-service-workflow-preview-v270'
RESULT = 'authored-service-workflow-adoption-result-v270'
COMMAND = 'authored-service-workflow-command-v270'
OPERATIONS = ('packet', 'reasoning', 'planner', 'context_reader', 'resense')
NODE_ACTIONS = {'packet': 'data', 'reasoning': 'data', 'planner': 'write',
    'context_reader': 'data', 'resense': 'supplement'}
EXTERNAL_NODE_IDS = ['reasoning', 'planner']


class Pin(Contract):
    capability_id: ID
    capability_hash: Hash
    actor_id: ID


class Tools(Contract):
    packet: Pin
    reasoning: Pin
    planner: Pin
    context_reader: Pin
    resense: Pin


class PlanStep(Contract):
    capability_id: ID
    capability_hash: Hash
    agent_role: Literal['business', 'perception', 'data']
    arguments: dict
    allow_external_inputs: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit_external(cls, value):
        if type(value) is not dict or value.get('allow_external_inputs') is not True:
            raise ValueError('须明确允许该候选动作外发已声明输入')
        return value


class CandidatePlan(Contract):
    candidate_ref: Annotated[str, Field(strict=True, pattern=r'^candidate_[1-9][0-9]?$')]
    steps: list[PlanStep] = Field(min_length=1, max_length=8)


class Preview(Contract):
    schema: Literal['authored-service-workflow-preview-command-v270']
    configuration_revision: Annotated[StrictInt, Field(ge=1)]
    configuration_hash: Hash
    business_name: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    tool_bindings: Tools
    candidate_proposals: list[Annotated[str, Field(strict=True, min_length=1, max_length=2000)]] = Field(min_length=1, max_length=16)
    candidate_plans: list[CandidatePlan] = Field(min_length=1, max_length=16)
    observation_extractors: list[Pin] = Field(default_factory=list, max_length=16)
    role_bindings: dict[str, ID]
    node_actions: dict[str, Literal['data', 'write', 'supplement']]
    external_node_ids: list[str] = Field(max_length=2)
    prior_origin: OriginRef | None
    simulation_only: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def simulation_scope(cls, value):
        if type(value) is not dict or value.get('simulation_only') is not True:
            raise ValueError('V270 首版仅接受明确的模拟执行范围')
        return value

    @model_validator(mode='after')
    def selected(self):
        if not self.business_name.strip() or '\x00' in self.business_name:
            raise ValueError('请选择当前已确认的原人工业务')
        if not 1 <= len(self.role_bindings) <= 16 or any(not key.strip() or '\x00' in key for key in self.role_bindings):
            raise ValueError('请选择全部原人工岗位')
        if any(not value.strip() or '\x00' in value for value in self.candidate_proposals):
            raise ValueError('候选说明不能为空')
        refs = ['candidate_' + str(i) for i in range(1, len(self.candidate_proposals) + 1)]
        if [plan.candidate_ref for plan in self.candidate_plans] != refs:
            raise ValueError('候选动作必须逐项对应完整候选说明，不能换号或重复')
        pins = self.tool_bindings.model_dump(mode='json')
        if len({pin['capability_id'] for pin in pins.values()}) != len(OPERATIONS):
            raise ValueError('五个不同语义端口须选择分别符合合同的已发布能力')
        extractor_ids = [pin.capability_id for pin in self.observation_extractors]
        if len(extractor_ids) != len(set(extractor_ids)) or set(extractor_ids).intersection(pin['capability_id'] for pin in pins.values()):
            raise ValueError('异步感知提取声明不得重复或冒充固定图节点')
        if self.node_actions != NODE_ACTIONS or self.external_node_ids != EXTERNAL_NODE_IDS:
            raise ValueError('须明确本配方固定节点的动作和实际外发范围')
        return self


class Adopt(Preview):
    schema: Literal['authored-service-workflow-adopt-command-v270']
    request_id: ID
    preview_hash: Hash
    consent: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls, value):
        if type(value) is not dict or value.get('consent') is not True:
            raise ValueError('请明确采用本次模拟服务流程；采用不启动服务或模型')
        return value


def parse(raw, model):
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise HTTPException(413, '服务编制请求为空或超过64KiB；未保存或调用')
    try:
        return original_json(raw, model)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '服务编制请求不符合固定合同；未接受任意执行图、模型结果或来源上下文') from None


def preview_body(body):
    result = {key: copy.deepcopy(body.model_dump(mode='json')[key]) for key in Preview.model_fields}
    result['schema'] = 'authored-service-workflow-preview-command-v270'
    return result
