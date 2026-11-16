"""Closed authored V288 commands; adoption is neither execution nor Goal proof."""
import copy
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator

from .authored_workflow_store_v208 import Hash, ID, OriginRef
from .business_decision_frame_v276 import CandidateSpec
from .contracts import Contract
from .layered_approval_contract_v175 import original_json
from . import workflow_goal_evaluation_contracts_v288 as evaluation

PREFIX = '/api/setup/authored-goal-data-workflow-graphs-v288'
PATH = PREFIX + '/adopt'
BINDING = 'authored-goal-data-workflow-binding-v288'
TEMPLATE = 'authored-goal-data-before-decision-template-v288'
PREVIEW = 'authored-goal-data-workflow-preview-v288'
RESULT = 'authored-goal-data-workflow-adoption-result-v288'
COMMAND = 'authored-goal-data-workflow-command-v288'
OPERATIONS = ('context_reader', 'local_prepare', 'resense', 'evaluation_packet', 'packet', 'reasoning')
NODE_ACTIONS = {'context_reader': 'data', 'local_prepare': 'write', 'resense': 'supplement',
    'evaluation_packet': 'data', 'packet': 'data', 'reasoning': 'data'}
EXTERNAL_NODE_IDS = ['reasoning']


def configured_policy(raw):
    """Check configuration shape; the runtime still checks the actual Goal.

    This is not permission to acquire. The adopted policy, real original Goal,
    approved acquisition policy and source ownership are checked independently.
    """
    value = evaluation.PreparationPolicy.model_validate(copy.deepcopy(raw)).model_dump(mode='json')
    evaluation._timezone(value['timezone'])
    if value['policy_hash'] != evaluation.policy_hash(value):
        raise ValueError('资料准备策略参数与固定摘要不一致')
    if value['time_basis'] == 'fixed_window' and value['allow_acquisition'] is not False:
        raise ValueError('固定历史窗口只能读取原窗口资料，不能开启新采集')
    if value['time_basis'] == 'rolling_latest' and value['max_age_seconds'] is None:
        raise ValueError('滚动最新资料必须明确有效时长')
    return copy.deepcopy(value)


class Pin(Contract):
    capability_id: ID
    capability_hash: Hash
    actor_id: ID


class Tools(Contract):
    context_reader: Pin
    local_prepare: Pin
    resense: Pin
    evaluation_packet: Pin
    packet: Pin
    reasoning: Pin


class Preview(Contract):
    schema: Literal['authored-goal-data-workflow-preview-command-v288']
    configuration_revision: Annotated[StrictInt, Field(ge=1)]
    configuration_hash: Hash
    business_name: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    tool_bindings: Tools
    preparation_policy: evaluation.PreparationPolicy
    candidate_specs: list[CandidateSpec] = Field(min_length=1, max_length=16)
    decision_intent: Literal['information_acquisition', 'operational_action', 'business_conclusion']
    observation_extractors: list[Pin] = Field(default_factory=list, max_length=16)
    reviewer_id: ID
    role_bindings: dict[str, ID]
    node_actions: dict[str, Literal['data', 'write', 'supplement']]
    external_node_ids: list[str] = Field(max_length=1)
    prior_origin: OriginRef | None
    simulation_only: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def simulation_scope(cls, value):
        if type(value) is not dict or value.get('simulation_only') is not True:
            raise ValueError('当前采用须明确模拟范围；采用不启动模型或设备')
        return value

    @model_validator(mode='after')
    def selected(self):
        if not self.business_name.strip() or '\x00' in self.business_name:
            raise ValueError('请选择当前已确认的原人工业务')
        if not 1 <= len(self.role_bindings) <= 16 or any(not key.strip() or '\x00' in key for key in self.role_bindings):
            raise ValueError('请选择全部原人工岗位')
        configured_policy(self.preparation_policy.model_dump(mode='json'))
        values = self.tool_bindings.model_dump(mode='json')
        if len({pin['capability_id'] for pin in values.values()}) != len(OPERATIONS):
            raise ValueError('六个不同语义端口须分别绑定真实已发布能力')
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
    schema: Literal['authored-goal-data-workflow-adopt-command-v288']
    request_id: ID
    preview_hash: Hash
    consent: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls, value):
        if type(value) is not dict or value.get('consent') is not True:
            raise ValueError('请明确采用本次资料准备配方；采用不启动执行')
        return value


def parse(raw, model):
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise HTTPException(413, '资料准备编制请求为空或超过64KiB')
    try:
        return original_json(raw, model)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '资料准备请求不符合闭合合同；未接受图、结果或伪造上下文') from None


def preview_body(body):
    result = {key: copy.deepcopy(body.model_dump(mode='json')[key]) for key in Preview.model_fields}
    result['schema'] = 'authored-goal-data-workflow-preview-command-v288'
    return result
