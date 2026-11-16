"""Explicit nested adoption contracts beside the unchanged legacy family.

This module does not invent qualification, capability, entry or service grants.
The shared adoption store owns commands/audits/configuration CAS. Fresh source,
members, published capabilities and automation are checked at their real gates.
"""
import copy
from collections import OrderedDict
from threading import RLock
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator, model_serializer

from . import business_flow_nested_plan_v192 as plans
from . import business_flow_nested_execution_v195 as compiler
from . import business_flow_nested_review_v195 as review
from . import business_flow_review_store as reviews
from .contracts import digest
from .report_reading_drafts import SafeContract


BINDING = 'business-flow-nested-execution-binding-v195'
ORIGIN = 'business-flow-nested-execution-origin-v195'
LEDGER = 'business-flow-nested-execution-ledger-v195'
PREVIEW = 'business-flow-nested-execution-preview-v195'
RESULT = 'business-flow-nested-execution-adoption-result-v195'
REQUEST = 'business-flow-nested-execution-original-result-v195'
CONTEXT = 'business-flow-nested-execution-context-v195'
SOURCE = 'business-flow-nested-execution-source-v195'
FLOW = 'business-flow-nested-execution-flow-v195'
ACTIONS = {'observe', 'state', 'data', 'learn', 'supplement', 'write'}
KEYS = {'schema', 'business_name', 'qualification_id', 'qualification_hash', 'flow_index', 'flow', 'graph',
    'role_bindings', 'capability_pins', 'event_source_bindings', 'event_admission_policy', 'source_materials',
    'source', 'execution_bindings', 'causal_bindings', 'translation', 'node_actions', 'external_node_ids',
    'review_mode', 'simulation_only', 'entry_policy', 'binding_hash'}
EXTRACTOR_KEYS = {'observation_extractors', 'observation_extractor_pins'}
AGENTS = {'human': '人工确认', 'business_agent': '业务智能体', 'perception_agent': '多模感知智能体',
    'data_agent': '数据管理智能体', 'task_center': '任务中心', 'execution_service': '执行服务'}
# Only successful, pure frozen-value checks are retained. The full typed JSON
# bytes are the key; no DB row, source permission or mutable result is cached.
# Eight keys of at most 1 MiB bound the retained payload to 8 MiB per process.
_BINDING_CACHE_ENTRIES = 8
_BINDING_CACHE_KEY_BYTES = 1024 * 1024
_BINDING_CHECKED = OrderedDict()
_BINDING_CACHE_LOCK = RLock()


def need(ok, message='递归采用合同、真实绑定或来源无法核对；未取得执行资格', code=409):
    if not ok:
        raise HTTPException(code, message)


class ObservationExtractor(SafeContract):
    capability_id: UUID
    capability_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    actor_id: UUID


class DraftObservationExtractor(ObservationExtractor):
    actor_id: UUID | Literal['']


class Preview(SafeContract):
    schema: Literal['business-flow-nested-execution-preview-command-v195']
    qualification_id: UUID
    qualification_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    flow_index: int = Field(strict=True, ge=0, le=15)
    business_name: str = Field(strict=True, min_length=1, max_length=200)
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    execution_bindings: dict
    causal_bindings: dict
    role_bindings: dict[str, UUID]
    node_actions: dict[str, Literal['observe', 'state', 'data', 'learn', 'supplement', 'write']]
    external_node_ids: list[str] = Field(max_length=128)
    entry_mode: Literal['declared_manual', 'engineering_manual', 'authorized_anomaly_handoff']
    observation_extractors: list[ObservationExtractor] | None = Field(default=None, min_length=1, max_length=8)

    @field_validator('observation_extractors')
    @classmethod
    def explicit_extractors(cls, value):
        if value is None or len({item.capability_id for item in value}) != len(value):
            raise ValueError('请明确选择互不重复的本地观测提取工具')
        return value

    @model_serializer(mode='wrap')
    def serialize(self, handler):
        value = handler(self)
        if self.observation_extractors is None:
            value.pop('observation_extractors', None)
        return value


class Adopt(Preview):
    schema: Literal['business-flow-nested-execution-adopt-command-v195']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    consent: Literal[True]
    share_source: Literal[True]

    @field_validator('consent', 'share_source', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('请明确采用所显示的递归设计及共享依据；工程模式仍不代表人审或生产许可')
        return value


def preview_body(body):
    data = body.model_dump(mode='json')
    return Preview.model_validate({key: 'business-flow-nested-execution-preview-command-v195'
        if key == 'schema' else data[key] for key in Preview.model_fields if key in data})


def package_value(package):
    need(type(package) is dict and set(package) == {'schema', 'qualification', 'scope_binding', 'material_snapshot'}
        and package['schema'] == SOURCE)
    q = package['qualification']
    need(type(q) is dict and q.get('schema') == review.RESULT)
    try:
        fields = review.mode_fields(q['review_mode'])
        need(all(type(q.get(key)) is type(value) and q[key] == value for key, value in fields.items())
            and q.get('status') == review.status(q['review_mode'])
            and all(q.get(key) is False for key in reviews.FLAGS))
        value = plans.check_plan(q['value'], scope_binding=package['scope_binding'])
        review.check_value(value, q['source_binding'], mode=q['review_mode'])
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '递归核对记录的明确模式或完整值无法核对') from None
    return value, q


def selected(package, flow_index):
    value, q = package_value(package)
    need(type(flow_index) is int and 0 <= flow_index < len(value['business']['flows']),
        '请选择此记录中真实存在的递归流程', 422)
    return value, q, value['business']['flows'][flow_index]


def allowed_entry_modes(package, flow_index):
    _, q, flow = selected(package, flow_index)
    if q['review_mode'] == 'engineering_simulation':
        return ['engineering_manual']
    kind = flow['trigger']['kind']
    return ['declared_manual'] if kind == 'manual' else ['authorized_anomaly_handoff'] if kind == 'event' else []


def entry_policy(package, flow_index, mode):
    _, _, flow = selected(package, flow_index)
    need(type(mode) is str and mode in allowed_entry_modes(package, flow_index),
        '启动方式与原触发或工程模式不符；没有将事件流程默认改成人工启动', 422)
    return {'mode': mode, 'source_trigger_kind': flow['trigger']['kind']}


def derive_flow(package, flow_index, translation):
    value, q, flow = selected(package, flow_index)
    need(type(translation) is dict and translation.get('schema') == compiler.SCHEMA
        and translation.get('source_plan_hash') == digest(value) and translation.get('flow_index') == flow_index)
    mapping = translation['source_mapping']
    actions = mapping['actions']
    return {'trigger': flow['trigger']['description'], 'output': '\n'.join(flow['completion']['outputs']),
        'steps': [{'id': item['graph_node_id'], 'name': item['source_action']['name'],
            'purpose': item['source_action']['responsibility'], 'agent': AGENTS[item['source_action']['actor']],
            'role': item['source_action']['role'], 'output': '\n'.join(item['source_action']['outputs'])}
            for item in actions], 'confirmedAt': None, 'confirmedSignature': None,
        'businessFlowSource': {'schema': FLOW, 'qualification_id': q['id'], 'qualification_hash': q['qualification_hash'],
            'flow_index': flow_index, 'source_plan_hash': digest(value), 'source_flow_hash': digest(flow),
            'source_mapping_hash': digest(mapping)}, 'source_flow': copy.deepcopy(flow),
        'source_mapping': copy.deepcopy(mapping), 'execution_display': 'typed_graph'}


def service_delegations(translation):
    """Recover closed service metadata from an actually recompiled translation.

    This is not an authority check. The common capability gate still verifies
    each registered service capability and its exact current hash and contract.
    """
    need(type(translation) is dict and translation.get('schema') == compiler.SCHEMA)
    result = {}
    for action in translation['source_mapping']['actions']:
        declared = action.get('service_delegation')
        if declared is None:
            continue
        need(type(declared) is dict and reviews._same(declared,
            compiler.service_delegation(declared.get('service_actor'), declared.get('operation')))
            and action['graph_node_id'] not in result)
        result[action['graph_node_id']] = copy.deepcopy(declared)
    return result


def _selection(body):
    return [item.model_dump(mode='json') for item in body.observation_extractors or []]


def check_command_extractors(body, binding):
    need(reviews._same(_selection(body), binding.get('observation_extractors', [])))


def checked_extractor_selection(selection, *, draft=False):
    need(type(selection) is list and 1 <= len(selection) <= 8, code=422)
    contract = DraftObservationExtractor if draft else ObservationExtractor
    normalized = [contract.model_validate(item).model_dump(mode='json') for item in selection]
    need(reviews._same(selection, normalized)
        and len({item['capability_id'] for item in normalized}) == len(selection), code=422)
    return normalized


def is_local_extractor(cap):
    execution = cap.data['execution']
    policy = cap.data['data_policy']
    return (cap.kind == 'tool' and cap.status == 'published'
        and set(execution) in ({'adapter', 'handler'}, {'adapter', 'handler', 'side_effect'})
        and execution['adapter'] == 'builtin' and execution['handler'] == 'numeric_rules'
        and execution.get('side_effect', False) is False and policy.get('deployment') == 'local'
        and not policy.get('cloud_allowed') and not policy.get('external_allowed')
        and cap.content_hash == cap.validated_hash == digest(cap.data))


def _extractor_scope(binding):
    need(binding['simulation_only'] is True and binding['review_mode'] == 'engineering_simulation'
        and binding['entry_policy']['mode'] == 'engineering_manual'
        and any(item['operation'] in {'resense_with_receipt', 'resense_from_context'}
            for item in service_delegations(binding['translation']).values()),
        '本地模拟提取工具必须绑定明确采用的工程补采流程', 422)


def _resolve_extractors(db, p, selection, *, lock=False):
    from . import authored_workflow_store_v208 as common
    from .authored_service_workflow_store_v270 import _extractor_capability
    from .models import Capability
    from .services import scoped
    result = {}
    for item in sorted(selection, key=lambda item: item['capability_id']):
        common._actor(db, p, item['actor_id'], lock=lock)
        cap = scoped(db, Capability, item['capability_id'], p, lock)
        need(cap.content_hash == item['capability_hash'] and is_local_extractor(cap),
            '所选版本不是已登记的本地只读观测提取工具')
        result[cap.id] = _extractor_capability(db, p, cap)
    return result


def verify_extractors(db, p, binding, *, lock=False):
    if 'observation_extractors' in binding:
        actual = _resolve_extractors(db, p, binding['observation_extractors'], lock=lock)
        need(reviews._same(actual, binding['observation_extractor_pins']))


def _check_extractors(value):
    if 'observation_extractors' not in value:
        return
    from .authored_service_workflow_graph_v270 import PIN_KEYS
    from .contracts import check_schema
    _extractor_scope(value)
    selection = value['observation_extractors']
    pins = value['observation_extractor_pins']
    normalized = checked_extractor_selection(selection)
    identities = {item['capability_id'] for item in normalized}
    need(type(pins) is dict and set(pins) == identities)
    for item in normalized:
        pin = pins[item['capability_id']]
        need(type(pin) is dict and set(pin) == PIN_KEYS
            and pin['capability_id'] == item['capability_id']
            and pin['capability_hash'] == item['capability_hash'] and pin['agent_role'] == 'perception'
            and pin['input_bindings'] == {} and pin['arguments'] == {} and pin['adapter'] == 'builtin'
            and type(pin['release_version']) is str and bool(pin['release_version'])
            and type(pin['max_attempts']) is int and 1 <= pin['max_attempts'] <= 5)
        check_schema(pin['input_schema'])
        check_schema(pin['output_schema'])


def check_binding(value):
    """Reuse pure consistency only; callers still check every current grant.

    Strict JSON validation runs before copying or consulting the cache. The
    detached current value keeps its original dictionary order for a miss and
    cannot change underneath validation or share a mutable cached result.
    """
    try:
        need(type(value) is dict and set(value) in (KEYS, KEYS | EXTRACTOR_KEYS) and value['schema'] == BINDING)
        reviews._bytes(value, 16 * 1024 * 1024)
        current = copy.deepcopy(value)
        key = reviews._bytes(current, 16 * 1024 * 1024)
        cacheable = len(key) <= _BINDING_CACHE_KEY_BYTES
        if cacheable:
            with _BINDING_CACHE_LOCK:
                if key in _BINDING_CHECKED:
                    _BINDING_CHECKED.move_to_end(key)
                    return current
        checked = _check_binding_uncached(current)
        if cacheable:
            with _BINDING_CACHE_LOCK:
                _BINDING_CHECKED[key] = None
                _BINDING_CHECKED.move_to_end(key)
                while len(_BINDING_CHECKED) > _BINDING_CACHE_ENTRIES:
                    _BINDING_CHECKED.popitem(last=False)
        return checked
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '递归执行绑定、原路径或明确来源无法核对；没有展平或改标') from None


def _check_binding_uncached(value):
    try:
        need(type(value) is dict and set(value) in (KEYS, KEYS | EXTRACTOR_KEYS) and value['schema'] == BINDING)
        reviews._bytes(value, 16 * 1024 * 1024)
        plan, q, flow = selected(value['source'], value['flow_index'])
        need(value['qualification_id'] == q['id'] and value['qualification_hash'] == q['qualification_hash']
            and value['review_mode'] == q['review_mode'] and value['simulation_only'] is q['simulation_only']
            and value['event_source_bindings'] == {} and value['event_admission_policy'] is None
            and type(value['business_name']) is str and bool(value['business_name'].strip())
            and value['business_name'] == value['business_name'].strip())
        need(value['entry_policy'] == entry_policy(value['source'], value['flow_index'], value['entry_policy']['mode']))
        translated = compiler.build_execution_graph(plan, value['flow_index'], value['execution_bindings'],
            scope_binding=value['source']['scope_binding'], causal_bindings=value['causal_bindings'])
        need(reviews._same(translated, value['translation']) and reviews._same(value['graph'], translated['graph'])
            and reviews._same(value['flow'], derive_flow(value['source'], value['flow_index'], translated)))
        actions = {item['graph_node_id']: item['source_action'] for item in translated['source_mapping']['actions']}
        roles = value['role_bindings']
        need(type(roles) is dict and set(roles) == {action['role'] for action in actions.values()})
        for node in value['graph']['nodes']:
            if node['kind'] not in {'human', 'ability'}:
                continue
            action = actions[node['id']]
            need(action['role'] in roles and roles[action['role']]['user_id'] == node['actor_id'])
        pins = {node['id']: node['execution'] for node in value['graph']['nodes'] if node['kind'] == 'ability'}
        need(reviews._same(value['capability_pins'], pins) and set(value['node_actions']) == set(pins)
            and all(action in ACTIONS for action in value['node_actions'].values())
            and value['binding_hash'] == digest({key: item for key, item in value.items() if key != 'binding_hash'}))
        _check_extractors(value)
        return copy.deepcopy(value)
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '递归执行绑定、原路径或明确来源无法核对；没有展平或改标') from None


def origin(identity, binding, command_hash):
    return {'schema': ORIGIN, 'adoption_id': identity, 'business_name': binding['business_name'],
        'binding_hash': binding['binding_hash'], 'command_hash': command_hash}


def make_preview(p, body, binding):
    value = {'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'command': preview_body(body).model_dump(mode='json'), 'binding': binding,
        'scope': '仅采用明确选定的递归执行设计并共享依据；不启动任务、不授予设备或生产权限',
        'unselected_flow_indices': [i for i in range(len(binding['source']['qualification']['value']['business']['flows']))
            if i != binding['flow_index']], 'review_mode': binding['review_mode'],
        'simulation_only': binding['simulation_only'], 'value_schema': plans.PLAN,
        'semantic_quality_verified': False, 'can_execute': False}
    return {**value, 'preview_hash': digest(value)}


def prepare(db, p, body):
    from . import business_flow_execution_adoption as shared
    from .report_full_semantic_graph_adoption import members, ability_capabilities
    shared.require(p, 'admin', 'operator')
    package = shared.sources.fresh_nested(db, p, str(body.qualification_id))
    plan, q, _ = selected(package, body.flow_index)
    need(q['qualification_hash'] == body.qualification_hash)
    policy = entry_policy(package, body.flow_index, body.entry_mode)
    translated = compiler.build_execution_graph(plan, body.flow_index, body.execution_bindings,
        scope_binding=package['scope_binding'], causal_bindings=body.causal_bindings)
    graph = translated['graph']; roles = members(db, p, body.role_bindings, lock=True)
    pins = ability_capabilities(db, p, graph, body.external_node_ids,
        service_delegations=service_delegations(translated))
    need(set(body.node_actions) == set(pins), '请逐个明确真实能力节点的自动化动作', 422)
    configuration = shared.current_configuration(db, p, lock=True)
    need(configuration.version == body.configuration_revision and digest(configuration.data) == body.configuration_hash,
        '配置版本已变化，请保留输入并重新预览')
    binding = {'schema': BINDING, 'business_name': body.business_name,
        'qualification_id': str(body.qualification_id), 'qualification_hash': body.qualification_hash,
        'flow_index': body.flow_index, 'flow': derive_flow(package, body.flow_index, translated), 'graph': graph,
        'role_bindings': roles, 'capability_pins': pins, 'event_source_bindings': {}, 'event_admission_policy': None,
        'source_materials': shared.sources.current_materials(db, p, package), 'source': package,
        'execution_bindings': copy.deepcopy(body.execution_bindings), 'causal_bindings': copy.deepcopy(body.causal_bindings),
        'translation': translated, 'node_actions': copy.deepcopy(body.node_actions),
        'external_node_ids': copy.deepcopy(body.external_node_ids), 'review_mode': q['review_mode'],
        'simulation_only': q['simulation_only'], 'entry_policy': policy}
    selection = _selection(body)
    if selection:
        _extractor_scope(binding)
        binding['observation_extractors'] = selection
        binding['observation_extractor_pins'] = _resolve_extractors(db, p, selection, lock=True)
    binding = check_binding(shared.seal(binding, 'binding_hash'))
    issued = origin('00000000-0000-4000-8000-000000000000', binding, '0' * 64)
    from .setup_api import validate_configuration
    validate_configuration(shared.proposed(configuration.data, body.business_name, binding, issued))
    return configuration, make_preview(p, body, binding)
