"""Two closed source intents and bounded content checks; no execution grant.

Scope/registry are server inputs. A self-consistent hash is not a saved query
or source authorization. Pixel intent/IR remain the original V252 contract.
Sound timestamps are explicit administrative fields, never recording time.
"""
import copy
import json
import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal, Union
from uuid import UUID

from jsonschema import Draft202012Validator, ValidationError as SchemaInvalid
from pydantic import Field, StrictStr, field_validator, model_validator

from .contracts import Contract, digest
from . import pixel_question_contracts_v252 as pixel

PLAN = 'multimodal-query-plan-v262'
INTENT = 'multimodal-query-intent-v262'
REGISTRY = 'multimodal-query-registry-v262'
SNAPSHOT = 'multimodal-query-source-snapshot-v262'
FAMILY = 'multimodal_query_v262'
MAX_RECORDS = 1000
MAX_BYTES = 16 * 1024 * 1024
FACETS = {'pixel': ('workflow_pixel_change_candidate_v250', 'native_pixel_results_v250'),
    'task_sound': ('workflow_environment_sound_candidate_v1', 'native_sound_results')}
PIN_KEYS = {'business_id', 'step_id', 'node_id', 'capability_id', 'capability_hash', 'release_version', 'output_schema_hash'}
DESCRIPTOR_KEYS = {'facet_id', 'source_family', 'data_plan_key', 'data_plan_hash', 'bindings'}
REGISTRY_KEYS = {'schema', 'workspace_id', 'instance_id', 'authorization_release_id', 'configuration_hash', 'facets', 'registry_hash'}
SNAPSHOT_KEYS = {'schema', 'workspace_id', 'captured_by', 'scope', 'as_of', 'registry',
    'facets', 'record_count', 'expected_sources_verified', 'snapshot_hash'}
SHA = re.compile(r'[0-9a-f]{64}')


class MultimodalQueryInvalid(ValueError):
    pass


def need(ok, message='多模态查询合同或冻结来源不符'):
    if not ok:
        raise MultimodalQueryInvalid(message)


def identity(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def hashed(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def integer(value, low=0, high=MAX_RECORDS):
    return type(value) is int and low <= value <= high


def wire(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise MultimodalQueryInvalid('来源必须是有限、可保存的JSON内容') from None
    need(len(raw) <= MAX_BYTES, '两个来源的完整冻结内容超过16MiB，未返回截断总体')
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str:
            need('\x00' not in item, '查询内容不能包含不可保存的空字符')
        elif type(item) is dict:
            pending.extend(item.keys()); pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return raw


def instant(value):
    need(type(value) is str)
    try:
        parsed = datetime.fromisoformat(value)
        need(parsed.tzinfo is not None and parsed.utcoffset() is not None)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise MultimodalQueryInvalid('冻结截面时间必须带明确时区') from None


class Scope(Contract):
    instance_id: StrictStr
    authorization_release_id: StrictStr
    task_id: StrictStr | None = None
    entity_id: StrictStr | None = None

    @field_validator('instance_id', 'authorization_release_id', 'task_id', 'entity_id')
    @classmethod
    def canonical(cls, value):
        if value is not None and not identity(value):
            raise ValueError('范围标识必须是标准UUID')
        return value


def sound_intent_schema():
    obj = pixel.object_schema
    return obj({'operation': {'enum': ['read', 'count', 'sum']},
        'metric': {'enum': [None, 'candidate_window_count', 'candidate_class_count']},
        'group_by': {'enum': ['none', 'registration_day', 'task_id', 'entity_id']},
        'time': obj({'basis': {'enum': ['machine_result_registered', 'independent_data_received', 'model_completed']},
            'days': {'type': ['array', 'null'], 'items': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
                'minItems': 1, 'maxItems': 31, 'uniqueItems': True}, 'timezone': {'const': 'Asia/Shanghai'}}),
        'filters': obj({'reception': {'enum': ['any', 'received', 'waiting_data', 'producer_failed',
            'local_handoff_pending', 'blocked_integrity']}}),
        'grounding': pixel.intent_schema()['properties']['grounding'],
        'limitations': pixel.intent_schema()['properties']['limitations']})


class PixelIntent(Contract):
    facet: Literal['pixel']
    question_quote: StrictStr = Field(min_length=1, max_length=4000)
    intent: dict

    @field_validator('intent', mode='before')
    @classmethod
    def closed_native_intent(cls, value):
        if type(value) is not dict:
            raise ValueError('像素意图必须为原闭合对象')
        try:Draft202012Validator(pixel.intent_schema()).validate(value)
        except SchemaInvalid as error:raise ValueError('像素意图不符合原V252合同') from error
        return copy.deepcopy(value)


class SoundIntent(Contract):
    facet: Literal['task_sound']
    question_quote: StrictStr = Field(min_length=1, max_length=4000)
    intent: dict

    @field_validator('intent', mode='before')
    @classmethod
    def closed_sound_intent(cls, value):
        if type(value) is not dict:
            raise ValueError('声音意图必须为闭合对象')
        try:Draft202012Validator(sound_intent_schema()).validate(value)
        except SchemaInvalid as error:raise ValueError('声音意图不符合固定行政时间与候选数量合同') from error
        if value['operation'] in {'read', 'count'}:
            need(value['metric'] is None)
        else:
            need(value['metric'] is not None and value['filters']['reception'] == 'received',
                '声音候选数量只能采用独立接收成果；等待值不能填零')
        need(value['group_by'] != 'registration_day' or value['time']['basis'] == 'machine_result_registered',
            '登记日分组不能冒充接收日、处理日或实际采集日')
        for day in value['time']['days'] or []:
            need(date.fromisoformat(day).isoformat() == day, '日期不是有效的行政时间日期')
        return copy.deepcopy(value)


class ModelIntent(Contract):
    schema: Literal['multimodal-query-intent-v262']
    subqueries: list[Annotated[Union[PixelIntent, SoundIntent], Field(discriminator='facet')]] = Field(min_length=1, max_length=2)
    unmet_requirements: list[StrictStr] = Field(max_length=8)

    @model_validator(mode='after')
    def unique_facets(self):
        need(len({query.facet for query in self.subqueries}) == len(self.subqueries), '同一来源只能有一项明确子计划')
        need(all(bool(item.strip()) and len(item) <= 2000 for item in self.unmet_requirements))
        wire(self.model_dump(mode='json'))
        return self


def bind_plan(value, question, scope, registry, *, reference_day=None):
    """Server injects scope/pins; string grounding is not semantic coverage.

    The whole question hash and unmet requirements remain visible. Exact
    fragments are not rewritten to evade a legacy checker, and an accepted
    structural plan neither proves all requirements covered nor grants a read.
    """
    model = ModelIntent.model_validate(value)
    scope = Scope.model_validate(scope).model_dump(mode='json'); registry = checked_registry(registry)
    need(type(question) is str and bool(question.strip()) and 1 <= len(question) <= 4000)
    need(registry['instance_id'] == scope['instance_id']
        and registry['authorization_release_id'] == scope['authorization_release_id'])
    queries = []
    for subquery in model.subqueries:
        need(subquery.facet in registry['facets'], '本次真实授权目录没有此来源，不能降级为零记录')
        fragment = subquery.question_quote
        need(bool(fragment.strip()) and len(fragment) <= len(question) and fragment in question,
            '子问题须为完整原问题中的非空连续原串，不能重写、拼接或同义改写')
        intent = subquery.intent
        if subquery.facet == 'pixel':
            # The V252 validator accepts its real candidate envelope, not a
            # bare intent. This is a server-side validation adapter only; the
            # explanation is not a fabricated provider reply or raw capture.
            candidate = {'status': 'candidate', 'ir': copy.deepcopy(intent),
                'explanation': '服务器协议适配：核对多模态像素子计划，不是额外模型回答'}
            ir = pixel.validate_intent(candidate, fragment, scope, reference_day=reference_day)
        else:
            for name, quote in intent['grounding'].items():
                if name == 'filter_quotes':
                    need(all(type(item) is str and bool(item) and item in fragment for item in quote))
                elif quote is not None:
                    need(type(quote) is str and bool(quote) and quote in fragment, '规划依据不在原问题分句中')
            need(intent['time']['days'] is None or intent['grounding']['time_quote'] is not None,
                '行政时间筛选缺少原问题依据；未知采集时间需澄清')
            need(intent['operation'] != 'sum' or intent['grounding']['metric_quote'] is not None)
            ir = {'schema': 'task-sound-query-ir-v262', 'scope': copy.deepcopy(scope), **copy.deepcopy(intent)}
        queries.append({'facet': subquery.facet, 'question_quote': fragment, 'ir': ir})
    result = {'schema': PLAN, 'scope': scope, 'registry_hash': registry['registry_hash'],
        'question_hash': digest(question), 'subqueries': queries,
        'unmet_requirements': list(model.unmet_requirements)}
    wire(result)
    return {**result, 'plan_hash': digest(result)}


def checked_registry(value):
    need(type(value) is dict and set(value) == REGISTRY_KEYS and value['schema'] == REGISTRY
        and all(identity(value[key]) for key in ('workspace_id', 'instance_id', 'authorization_release_id'))
        and hashed(value['configuration_hash']) and type(value['facets']) is dict
        and bool(value['facets']) and set(value['facets']) <= set(FACETS))
    for name, descriptor in value['facets'].items():
        need(type(descriptor) is dict and set(descriptor) == DESCRIPTOR_KEYS
            and descriptor['facet_id'] == name and descriptor['source_family'] == FACETS[name][0]
            and descriptor['data_plan_key'] == FACETS[name][1] and hashed(descriptor['data_plan_hash'])
            and type(descriptor['bindings']) is list and bool(descriptor['bindings']))
        seen = set()
        for pin in descriptor['bindings']:
            need(type(pin) is dict and set(pin) == PIN_KEYS
                and identity(pin['capability_id']) and hashed(pin['capability_hash']) and hashed(pin['output_schema_hash'])
                and all(type(pin[key]) is str and bool(pin[key]) for key in ('business_id', 'step_id', 'release_version'))
                and (pin['node_id'] is None or type(pin['node_id']) is str and bool(pin['node_id'])))
            key = (pin['business_id'], pin['step_id'], pin['node_id'])
            need(key not in seen); seen.add(key)
    need(value['registry_hash'] == digest({key: item for key, item in value.items() if key != 'registry_hash'}))
    return copy.deepcopy(value)


def checked_snapshot(value):
    """Check the outer content/budget; provider checks and real Audit are separate."""
    need(type(value) is dict and set(value) == SNAPSHOT_KEYS and value['schema'] == SNAPSHOT
        and identity(value['workspace_id']) and identity(value['captured_by'])
        and value['expected_sources_verified'] is False and integer(value['record_count']))
    scope = Scope.model_validate(value['scope']).model_dump(mode='json')
    need(scope == value['scope']); registry = checked_registry(value['registry'])
    need(registry['workspace_id'] == value['workspace_id'] and registry['instance_id'] == scope['instance_id']
        and registry['authorization_release_id'] == scope['authorization_release_id']
        and type(value['facets']) is dict and set(value['facets']) == set(registry['facets']))
    stamp = instant(value['as_of']); total = scanned = 0
    for name, snapshot in value['facets'].items():
        need(type(snapshot) is dict and snapshot['workspace_id'] == value['workspace_id']
            and snapshot['instance_id'] == scope['instance_id'] and snapshot['captured_by'] == value['captured_by']
            and snapshot['scope'] == {'task_id': scope['task_id'], 'entity_id': scope['entity_id']}
            and type(snapshot['records']) is list)
        clock = snapshot['captured_at'] if name == 'pixel' else snapshot['as_of']
        need(instant(clock) == stamp, '两个来源不是同一事务截面；截面不代表采集时刻')
        total += len(snapshot['records'])
        scanned += len(snapshot['inventory']['run_ids'] if name == 'pixel' else snapshot['inventory']['scanned_run_ids'])
    need(total == value['record_count'] and scanned <= MAX_RECORDS, '两个来源合计超过1000项完整库存，未截断总体')
    wire(value)
    need(value['snapshot_hash'] == digest({key: item for key, item in value.items() if key != 'snapshot_hash'}))
    return copy.deepcopy(value)
