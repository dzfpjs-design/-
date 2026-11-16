"""Closed human-authored V276/V277 recipe commands, not execution grants."""
import copy
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator

from .authored_workflow_store_v208 import Hash, ID, OriginRef
from .business_decision_frame_v276 import CandidateSpec
from .contracts import Contract
from .layered_approval_contract_v175 import original_json

PREFIX = '/api/setup/authored-decision-workflow-graphs'
PATH = PREFIX + '/adopt'
BINDING = 'authored-decision-workflow-binding-v278'
TEMPLATE = 'authored-decision-human-conditional-template-v278'
PREVIEW = 'authored-decision-workflow-preview-v278'
RESULT = 'authored-decision-workflow-adoption-result-v278'
COMMAND = 'authored-decision-workflow-command-v278'
OPERATIONS = ('data_packet', 'packet', 'reasoning', 'planner', 'resense')
NODE_ACTIONS = {'data_packet': 'data', 'packet': 'data', 'reasoning': 'data', 'planner': 'write', 'resense': 'supplement'}
EXTERNAL_NODE_IDS = ['reasoning']


class Pin(Contract):
    capability_id: ID
    capability_hash: Hash
    actor_id: ID


class Tools(Contract):
    data_packet: Pin
    packet: Pin
    reasoning: Pin
    planner: Pin
    resense: Pin


class Preview(Contract):
    schema: Literal['authored-decision-workflow-preview-command-v278']
    configuration_revision: Annotated[StrictInt, Field(ge=1)]
    configuration_hash: Hash
    business_name: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    tool_bindings: Tools
    candidate_specs: list[CandidateSpec] = Field(min_length=1, max_length=16)
    decision_intent: Literal['information_acquisition']
    observation_extractors: list[Pin] = Field(default_factory=list, max_length=16)
    role_bindings: dict[str, ID]
    node_actions: dict[str, Literal['data', 'write', 'supplement']]
    external_node_ids: list[str] = Field(max_length=1)
    prior_origin: OriginRef | None
    simulation_only: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def simulation_scope(cls, value):
        if type(value) is not dict or value.get('simulation_only') is not True:
            raise ValueError('V278 首片须明确模拟范围；采用不启动模型或设备')
        return value

    @model_validator(mode='after')
    def selected(self):
        if not self.business_name.strip() or '\x00' in self.business_name:
            raise ValueError('请选择当前已确认的原人工业务')
        if not 1 <= len(self.role_bindings) <= 16 or any(not key.strip() or '\x00' in key for key in self.role_bindings):
            raise ValueError('请选择全部原人工岗位')
        values = self.tool_bindings.model_dump(mode='json')
        if len({pin['capability_id'] for pin in values.values()}) != len(OPERATIONS):
            raise ValueError('五个不同语义端口须分别绑定真实已发布能力')
        extractors = [pin.capability_id for pin in self.observation_extractors]
        if len(extractors) != len(set(extractors)) or set(extractors).intersection(pin['capability_id'] for pin in values.values()):
            raise ValueError('异步提取声明不能重复或冒充图节点')
        candidates = [item.model_dump(mode='json') for item in self.candidate_specs]
        if len({item['candidate_ref'] for item in candidates}) != len(candidates):
            raise ValueError('候选引用不能重复')
        refs, identities = {}, {}
        for candidate in candidates:
            if len({item['claim_ref'] for item in candidate['claims']}) != len(candidate['claims']):
                raise ValueError('同一候选的主张引用不能重复')
            selected = candidate['capability_pins']
            if len({item['capability_ref'] for item in selected}) != len(selected):
                raise ValueError('同一候选不能重复绑定能力')
            for pin in selected:
                ref, identity = pin['capability_ref'], pin['capability_id']
                if ref in refs and refs[ref] != pin or identity in identities and identities[identity] != ref:
                    raise ValueError('同一真实能力须保留同一引用与完整声明，不能换别名')
                refs[ref], identities[identity] = pin, ref
        if len(identities) > 16 or self.node_actions != NODE_ACTIONS or self.external_node_ids != EXTERNAL_NODE_IDS:
            raise ValueError('请保留候选容量、固定动作及唯一模型外发节点')
        return self


class Adopt(Preview):
    schema: Literal['authored-decision-workflow-adopt-command-v278']
    request_id: ID
    preview_hash: Hash
    consent: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls, value):
        if type(value) is not dict or value.get('consent') is not True:
            raise ValueError('请明确采用本次决策配方；采用不启动执行')
        return value


def parse(raw, model):
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise HTTPException(413, '决策编制请求为空或超过64KiB')
    try:
        return original_json(raw, model)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '决策编制请求不符合闭合合同；未接受原始图、模型结果或伪造上下文') from None


def preview_body(body):
    result = {key: copy.deepcopy(body.model_dump(mode='json')[key]) for key in Preview.model_fields}
    result['schema'] = 'authored-decision-workflow-preview-command-v278'
    return result
