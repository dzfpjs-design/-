"""Metadata-only, bounded query proposals; quotations establish traceability only.

This module has no dispatch or persistence entry point. Callers must preserve the
raw answer, check their frozen context and obtain explicit meaning confirmation
before adopting a candidate or executing the compiled local request.
"""
import copy
import math
import re
from typing import Literal

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from pydantic import ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError, model_validator

from .contracts import Contract, DataRequest, DataToolStep, EvidenceRequirement, GoalSpec, digest

CATALOG_SCHEMA = 'native-query-proposal-catalog-v1'
METHODS = {'keyword', 'fulltext', 'aggregate'}
RANKED_LIMITATION = '计算只针对全文检索实际返回的证据记录，受最多返回条数和最低分限制，不代表全部匹配或现实总体。'
COUNT_LIMITATION = '计数单位为证据记录，不是不同对象或实物数量。'
MEANING_LIMITATION = '原文引用只证明出处，不能证明完整理解；采用前须人工核对字段、关系、来源、时间、计算和比较含义。'


class StrictContract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)


class ProposedRelationQuery(StrictContract):
    relation_types: list[StrictStr] = Field(min_length=1, max_length=5)
    root_ids: list[StrictStr] = Field(min_length=1, max_length=10)
    direction: Literal['outgoing', 'incoming']
    max_hops: StrictInt = Field(ge=1, le=3)
    include_roots: StrictBool
    at: StrictStr | None = Field(max_length=50)


class ResultCriterion(StrictContract):
    kind: Literal['field_value', 'query_value']
    field: StrictStr = Field(min_length=1, max_length=100)
    operator: Literal['eq', 'gt', 'gte', 'lt', 'lte']
    expected: StrictStr | StrictInt | StrictFloat | StrictBool

    @model_validator(mode='after')
    def finite(self):
        if isinstance(self.expected, float) and not math.isfinite(self.expected):
            raise ValueError('比较值必须为有限数值')
        if isinstance(self.expected, str) and len(self.expected) > 2000:
            raise ValueError('比较文字过长')
        return self


class Claim(StrictContract):
    kind: Literal['field', 'relation', 'root', 'filter', 'operation', 'result']
    key: StrictStr = Field(min_length=1, max_length=100)
    quote: StrictStr = Field(min_length=1, max_length=4000)


class Proposal(StrictContract):
    status: Literal['candidate', 'needs_clarification']
    summary: StrictStr = Field(min_length=1, max_length=2000)
    fields: list[StrictStr] = Field(max_length=30)
    relation_query: ProposedRelationQuery | None
    tool_steps: list[DataToolStep] = Field(max_length=6)
    result_criterion: ResultCriterion | None
    claims: list[Claim] = Field(max_length=60)
    questions: list[StrictStr] = Field(max_length=10)
    limitations: list[StrictStr] = Field(max_length=10)

    @model_validator(mode='after')
    def readiness(self):
        if any(not value.strip() or len(value) > 1000 for value in self.questions + self.limitations):
            raise ValueError('澄清问题和限制须为有界非空文字')
        if self.status == 'candidate' and (not self.fields or self.questions):
            raise ValueError('候选须包含字段且没有未决澄清问题')
        if self.status == 'needs_clarification' and not self.questions:
            raise ValueError('待澄清结果须说明问题')
        return self


def _fail(message, code=422):
    raise HTTPException(code, message)


def _kind(prop):
    kind = prop.get('type')
    if isinstance(kind, list):
        kinds = [item for item in kind if item != 'null']
        return kinds[0] if len(kinds) == 1 else None
    return kind


def build_context_catalog(db, p, task):
    """Read only the original selected entities and metadata of their types."""
    from .models import Entity
    from .services import scoped, published
    from .workspace_api import saved_execution
    from .native_query_catalog import catalog
    from .business_tools import arguments_schema
    from .instance_relation_types import relation_metadata
    _, release, business = saved_execution(db, p, task.id)
    package = published(db, p, task.package_id, 'domain_package')
    spec = package.data['specification']
    if (release.package_id != package.id or digest(release.data['snapshot']) != release.configuration_hash
        or spec.get('configuration_hash') != release.configuration_hash or not business['available']):
        _fail('原任务固定业务版本与查询目录无法核对', 409)
    ids = task.data.get('entity_ids', [])
    if not ids or len(ids) > 30 or len(ids) != len(set(ids)):
        _fail('原任务对象范围无效', 409)
    entities = [scoped(db, Entity, identity, p) for identity in ids]
    if any(row.schema_capability_id != package.id for row in entities):
        _fail('查询对象属于其他业务版本', 409)
    types = {row.entity_type for row in entities}
    definitions = {item['key']: item for item in spec.get('object_catalog', [])}
    variants = {}
    for entity_type in sorted(types):
        declared = {item['key']: item for item in definitions.get(entity_type, {}).get('fields', [])}
        for key, prop in spec['entity_schemas'][entity_type].get('properties', {}).items():
            kind = _kind(prop)
            if kind not in {'string', 'boolean', 'number', 'integer'}:
                continue
            field = declared.get(key, {})
            variants.setdefault(key, []).append({'key': key, 'label': field.get('label') or prop.get('title') or key,
                'type': kind, 'unit': field.get('unit', prop.get('unit')), 'entity_type': entity_type})
    fields = []; conflicts = []
    for key, items in sorted(variants.items()):
        if len({(item['type'], item['unit']) for item in items}) != 1:
            conflicts.append({'key': key, 'reason': '所选对象类型对同名字段的类型或单位声明不一致'})
        fields.append({name: items[0][name] for name in ['key', 'label', 'type', 'unit']} |
            {'entity_types': [item['entity_type'] for item in items]})
    metadata = relation_metadata(package, entities)
    relations = [{key: item[key] for key in ['id', 'title', 'description', 'source_types', 'target_types']}
        for item in metadata['relation_types']
        if set(item['source_types']) & types and set(item['target_types']) & types]
    tools = []
    for cap in catalog(db, p, package):
        method = cap.data['specification']['method']
        execution = cap.data['execution']
        if method not in METHODS or execution.get('adapter') != 'builtin' or execution.get('side_effect') is not False:
            _fail('查询目录只能使用固定的本机只读筛选、全文和计算能力', 409)
        properties = cap.data['output_schema']['properties']['records']['items']['properties']['values'].get('properties', {})
        output = [{'key': key, 'type': _kind(prop), 'unit': '条证据记录' if key == 'sample_count' else None}
            for key, prop in properties.items() if _kind(prop) in {'string', 'boolean', 'number', 'integer'}]
        tools.append({'id': cap.id, 'name': cap.data['description'], 'method': method,
            'content_hash': cap.content_hash, 'version': cap.release_version,
            'parameters_schema': arguments_schema(cap), 'output_fields': output})
    return {'schema': CATALOG_SCHEMA,
        'objects': [{'id': item['id'], 'label': item['title'], 'type': item['type'], 'type_label': item['type_title']}
            for item in metadata['entities']],
        'fields': fields, 'relation_types': relations, 'query_tools': tools, 'field_conflicts': conflicts}


def result_schema(catalog):
    schema = Proposal.model_json_schema()
    schema['$defs'].pop('DataToolStep', None)
    schema['properties']['fields']['items'] = {'enum': [item['key'] for item in catalog['fields']]}
    relation = schema['$defs']['ProposedRelationQuery']['properties']
    relation['root_ids']['items'] = {'enum': [item['id'] for item in catalog['objects']]}
    relation['relation_types']['items'] = {'enum': [item['id'] for item in catalog['relation_types']]}
    branches = []
    for tool in catalog['query_tools']:
        if tool['method'] not in METHODS:
            continue
        branches.append({'type': 'object', 'additionalProperties': False,
            'required': ['id', 'capability_id', 'input_from', 'parameters'], 'properties': {
                'id': {'type': 'string', 'pattern': '^[a-z][a-z0-9_]{0,39}$', 'not': {'const': 'evidence'}},
                'capability_id': {'const': tool['id']}, 'input_from': {'type': 'string', 'maxLength': 40},
                'parameters': copy.deepcopy(tool['parameters_schema'])}})
    schema['properties']['tool_steps']['items'] = {'oneOf': branches} if branches else False
    # Mirror the existing local dependency gate in the model-facing contract.
    # Later edges still need deterministic validation against actual earlier ids.
    schema['properties']['tool_steps']['prefixItems'] = [{'allOf': [
        copy.deepcopy(schema['properties']['tool_steps']['items']),
        {'properties': {'input_from': {'const': 'evidence'}}}]}]
    return schema


def _expected_matches(value, prop, operator):
    kind = prop['type']
    if kind in {'number', 'integer'}:
        if type(value) not in {int, float}:
            return False
        try:
            return math.isfinite(value) and (kind != 'integer' or float(value).is_integer())
        except OverflowError:
            return False
    return operator == 'eq' and ((kind == 'boolean' and type(value) is bool) or (kind == 'string' and type(value) is str))


def _numbers(text):
    """Conservative literal support, not a general natural-language interpreter."""
    values = []
    for token in re.findall(r'(?<![0-9A-Za-z_.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?![0-9A-Za-z_.])', text):
        values.append(float(token))
    digits = {char: value for value, char in enumerate('零一二三四五六七八九')}
    digits.update({'〇': 0, '两': 2})
    for token in re.findall(r'[负零〇一二两三四五六七八九十百千万点]+', text):
        if token in {'点', '负'}:
            continue
        sign = -1 if token.startswith('负') else 1
        token = token.lstrip('负'); parts = token.split('点')
        if len(parts) > 2 or not parts[0]:
            continue
        total = 0; digit = 0; valid = True
        for char in parts[0]:
            if char in digits:
                digit = digits[char]
            elif char in {'十', '百', '千'}:
                total += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]; digit = 0
            elif char == '万':
                total = (total + digit) * 10000; digit = 0
            else:
                valid = False
        if valid:
            number = total + digit
            if len(parts) == 2:
                if not parts[1] or any(char not in digits for char in parts[1]):
                    continue
                number += float('0.' + ''.join(str(digits[char]) for char in parts[1]))
            values.append(sign * number)
    return values


def validate_candidate(value, question, catalog, evidence):
    """Return a bounded proposal and trace spans; semantic equivalence is unproven."""
    try:
        requirement = EvidenceRequirement.model_validate(evidence).model_dump()
        errors = list(Draft202012Validator(result_schema(catalog)).iter_errors(value))
        if errors:
            _fail('模型候选不符合固定查询结构或所选目录')
        proposal = Proposal.model_validate(value).model_dump()
    except ValidationError:
        _fail('模型候选或明确资料要求不符合固定契约')
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        _fail('原查询文字无效')
    if len(proposal['fields']) != len(set(proposal['fields'])):
        _fail('候选字段不能重复')
    fields = {item['key']: item for item in catalog['fields']}
    objects = {item['id']: item for item in catalog['objects']}
    relations = {item['id']: item for item in catalog['relation_types']}
    tools = {item['id']: item for item in catalog['query_tools']}
    questions = list(proposal['questions'])
    def ask(text):
        if text not in questions:
            questions.append(text)
    selected = {key: fields[key] for key in proposal['fields']}
    for item in catalog.get('field_conflicts', []):
        if item['key'] in selected:
            ask('请先明确字段“' + fields[item['key']]['label'] + '”在所选对象类型中的一致类型和单位。')
    required = {('field', key) for key in selected}
    relation = proposal['relation_query']
    if relation:
        if (len(relation['root_ids']) != len(set(relation['root_ids']))
            or len(relation['relation_types']) != len(set(relation['relation_types']))):
            _fail('关系根对象和类型不能重复')
        required.update(('root', key) for key in relation['root_ids'])
        required.update(('relation', key) for key in relation['relation_types'])
        for root in relation['root_ids']:
            label = objects[root]['label'].strip().casefold()
            if sum(item['label'].strip().casefold() == label for item in objects.values()) > 1:
                ask('所选对象中存在同名起查对象“' + objects[root]['label'] + '”，请明确具体对象，不能由模型猜定。')
            endpoint = 'source_types' if relation['direction'] == 'outgoing' else 'target_types'
            if not any(objects[root]['type'] in relations[key][endpoint] for key in relation['relation_types']):
                ask('起查对象类型与所选关系方向不一致，请核对关系根和方向。')
        if relation['at']:
            from .temporal import instant
            from .models import now
            if instant(relation['at']) > now():
                _fail('不能把未来关系时点当作已有关系事实')
    frames = {'evidence': selected}; ranked = {'evidence': False}; methods = []
    for step in proposal['tool_steps']:
        if step['id'] in frames or step['input_from'] not in frames:
            _fail('查询步骤只能引用原资料或明确前序结果，不能重复、循环或引用未来步骤')
        tool = tools[step['capability_id']]; method = tool['method']; methods.append(method)
        if method not in METHODS:
            _fail('候选不能使用其他工具或外部能力')
        parameters = step['parameters']; field = parameters.get('field')
        prop = frames[step['input_from']].get(field)
        if not prop:
            _fail('查询字段不属于本步骤的输入结果')
        if method in {'keyword', 'fulltext'}:
            if prop['type'] != 'string':
                _fail('关键词筛选和全文检索须使用文字字段')
            required.add(('filter', step['id']))
            frames[step['id']] = frames[step['input_from']]
        else:
            required.add(('operation', step['id']))
            operation = parameters.get('operation')
            if operation != 'count' and prop['type'] not in {'number', 'integer'}:
                _fail('均值、合计和最小最大值须使用数值字段')
            if operation != 'count' and not prop.get('unit'):
                ask('请明确计算字段“' + prop.get('label', field) + '”的单位，未知单位不能由模型补造。')
            frames[step['id']] = {item['key']: dict(item) for item in tool['output_fields']}
            if 'value' in frames[step['id']]:
                frames[step['id']]['value']['unit'] = '条证据记录' if operation == 'count' else prop.get('unit')
        ranked[step['id']] = ranked[step['input_from']] or method == 'fulltext'
    criterion = proposal['result_criterion']
    if criterion:
        required.add(('result', criterion['field']))
        if criterion['kind'] == 'query_value' and not proposal['tool_steps']:
            _fail('派生结果标准需要至少一个查询步骤')
        output = selected if criterion['kind'] == 'field_value' else frames[proposal['tool_steps'][-1]['id']]
        prop = output.get(criterion['field'])
        if not prop or not _expected_matches(criterion['expected'], prop, criterion['operator']):
            _fail('结果标准的字段、比较方式或期望值类型不符合实际输入/末步输出')
        if prop['type'] in {'number', 'integer'} and not prop.get('unit'):
            ask('请明确比较结果的单位；模型不能推定或换算未知单位。')
    spans = []; seen = set(); claims = {}
    for claim in proposal['claims']:
        key = (claim['kind'], claim['key'])
        if key not in required or key in seen:
            _fail('原文依据不能重复或指向候选中不存在的字段、关系、步骤或标准')
        seen.add(key); start = question.find(claim['quote'])
        if start < 0 or question.find(claim['quote'], start + 1) >= 0:
            ask('请为“' + claim['key'] + '”提供在原需求中唯一出现的逐字依据。')
            continue
        spans.append({**claim, 'start': start, 'end': start + len(claim['quote'])})
        claims[key] = claim['quote']
    for kind, key in sorted(required - set(claims)):
        ask('请核对“' + key + '”的原需求依据（' + kind + '）。')
    for step in proposal['tool_steps']:
        if tools[step['capability_id']]['method'] in {'keyword', 'fulltext'}:
            quote = claims.get(('filter', step['id']), '')
            if step['parameters']['text'].casefold() not in quote.casefold():
                ask('筛选或检索文字“' + step['parameters']['text'] + '”没有可核对的原需求逐字依据。')
    if criterion and type(criterion['expected']) in {int, float}:
        if criterion['expected'] not in _numbers(claims.get(('result', criterion['field']), '')):
            ask('请明确比较数值的原需求依据；未能从引用中核对数值，不推定其含义。')
    if len(questions) > 10:
        questions = questions[:9] + ['还有未核对的依据，请完善原需求后重新提出候选。']
    proposal['questions'] = questions
    if questions:
        proposal['status'] = 'needs_clarification'
    last = proposal['tool_steps'][-1]['id'] if proposal['tool_steps'] else 'evidence'
    subset = ranked[last]
    fixed = [MEANING_LIMITATION]
    if subset:
        fixed.append(RANKED_LIMITATION)
    if any(step['parameters'].get('operation') == 'count' for step in proposal['tool_steps']):
        fixed.append(COUNT_LIMITATION)
    return {'candidate': proposal, 'claim_spans': spans,
        'constraints': {'meaning_confirmed': False, 'semantic_equivalence_verified': False,
            'sample_unit': 'evidence_records', 'ranked_subset': subset,
            'requires_ranked_subset_confirmation': subset and any(method == 'aggregate' for method in methods),
            'limitations': fixed, 'evidence_requirement': requirement},
        'question_hash': digest(question)}


def _candidate(value):
    value = value.get('candidate', value)
    try:
        proposal = Proposal.model_validate(value).model_dump()
    except ValidationError:
        _fail('待采用候选格式无法核对')
    if proposal['status'] != 'candidate':
        _fail('候选仍需澄清，不能采用或执行', 409)
    return proposal


def compile_request(task, candidate, evidence, question):
    """Compile after validation/confirmation; bind scope and package server-side."""
    proposal = _candidate(candidate)
    relation = proposal['relation_query']
    if relation:
        relation = {**relation, 'package_id': task.package_id}
    return DataRequest(question=question, entity_ids=copy.deepcopy(task.data['entity_ids']),
        fields=proposal['fields'], mode='structured', evidence_requirement=EvidenceRequirement.model_validate(evidence),
        relation_query=relation, tool_steps=proposal['tool_steps'],
        max_tool_calls=max(1, len(proposal['tool_steps'])), max_tool_seconds=60,
        allow_external_tools=False).model_dump()


def compile_goal_spec(task, candidate, evidence, question):
    """Return an unconfirmed draft only; a formal native goal cannot be rewritten."""
    if task.data.get('native_goal_context') or task.data.get('goal_spec'):
        _fail('原正式目标已固定；此候选只能作为同任务参考查询', 409)
    proposal = _candidate(candidate)
    request = compile_request(task, proposal, evidence, question)
    criteria = [{'id': 'evidence', 'description': '原范围资料达到明确的来源、时间和核查要求', 'kind': 'evidence_sufficient'},
        {'id': 'human', 'description': '人工核对原资料、查询含义及原流程结果', 'kind': 'human_confirmation'}]
    if proposal['result_criterion']:
        criteria.insert(1, {'id': 'result', 'description': '核对已确认的字段或末步计算结果标准', **proposal['result_criterion']})
    return GoalSpec(purpose=question, entity_ids=request['entity_ids'], required_fields=request['fields'],
        evidence=request['evidence_requirement'], relation_query=request['relation_query'],
        data_query={'tool_steps': request['tool_steps'], 'max_tool_seconds': request['max_tool_seconds'],
            'allow_external_tools': False} if request['tool_steps'] else None,
        observation_mode='existing_only', stop_on_conflict=True,
        subgoals=[{'id': 'query_goal', 'description': question, 'criterion_ids': [item['id'] for item in criteria]}],
        criteria=criteria).model_dump()
