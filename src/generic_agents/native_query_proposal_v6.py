"""Typed, source-preserving grounding for new quoted query proposals.

This evaluates the original wire directly. Lexical grounding is not semantic
equivalence; no response repair, query execution or persistence occurs here.
Historic evaluators remain selected by their frozen version.
"""
import copy
from collections import Counter
from dataclasses import asdict, dataclass

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from . import native_query_proposal_contract as v1
from . import native_query_proposal_v3 as v3
from . import native_query_proposal_v4 as v4
from . import native_query_proposal_v5 as v5
from .contracts import EvidenceRequirement, digest
from .retrieval import text_tokens

CONTRACT_VERSION = 6
GROUNDING_SCHEMA = 'native-query-grounding-v1'
TOKENIZER_VERSION = 'zh_character_bigram_en_word_v1'


class CandidateValidationError(HTTPException):
    """A fixed v6 contract rejection, distinct from service/context failures."""
    def __init__(self, message):
        super().__init__(422, message)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    step_id: str | None = None


def result_schema(catalog):
    schema = copy.deepcopy(v4.result_schema(catalog))
    original = schema['$defs']['Criterion']
    source = catalog['fields']
    prior = source + [field for tool in catalog['query_tools'] for field in tool['output_fields']]
    branches = []
    for kind, fields in [('field_value', source), ('query_value', prior)]:
        keys = sorted({field['key'] for field in fields if field['type'] in v4.SCALAR_TYPES})
        if keys:
            branch = copy.deepcopy(original)
            branch['properties']['kind'] = {'const': kind}
            branch['properties']['field'] = {'type': 'string', 'enum': keys}
            branches.append(branch)
    schema['$defs']['Criterion'] = {'oneOf': branches} if branches else False
    return schema


def _compile(proposal, catalog):
    """Compile only supplied choices/quotes, retaining parameters byte-for-value."""
    compiled = {'status': proposal['status'], 'summary': proposal['summary'], 'fields': [],
        'relation_query': None, 'tool_steps': [], 'result_criterion': None,
        'claims': [], 'questions': [], 'limitations': copy.deepcopy(proposal['limitations'])}
    if proposal['status'] == 'needs_clarification':
        compiled['questions'] = list(proposal['questions'])
        return compiled
    compiled['fields'] = [item['key'] for item in proposal['fields']]
    compiled['claims'] = [{'kind': 'field', **item} for item in proposal['fields']]
    relation = proposal['relation_query']
    if relation is not None:
        compiled['relation_query'] = {key: relation[key] for key in
            ['direction', 'max_hops', 'include_roots', 'at']}
        compiled['relation_query'].update(root_ids=[item['key'] for item in relation['roots']],
            relation_types=[item['key'] for item in relation['types']])
        for key, kind in [('roots', 'root'), ('types', 'relation')]:
            compiled['claims'].extend({'kind': kind, **item} for item in relation[key])
    methods = {tool['id']: tool['method'] for tool in catalog['query_tools']}
    for index, operation in enumerate(proposal['operations'], 1):
        identity = 'operation_' + str(index)
        compiled['tool_steps'].append({'id': identity, 'capability_id': operation['capability_id'],
            'input_from': 'evidence' if operation['input_from'] == 0 else 'operation_' + str(operation['input_from']),
            'parameters': copy.deepcopy(operation['parameters'])})
        kind = 'filter' if methods[operation['capability_id']] in {'keyword', 'fulltext'} else 'operation'
        compiled['claims'].append({'kind': kind, 'key': identity, 'quote': operation['quote']})
    criterion = proposal['result_criterion']
    if criterion is not None:
        compiled['result_criterion'] = {key: value for key, value in criterion.items() if key != 'quote'}
        compiled['claims'].append({'kind': 'result', 'key': criterion['field'], 'quote': criterion['quote']})
    return compiled


def validate_candidate(value, question, catalog, evidence):
    """Collect all safely evaluable blockers before bounding display questions."""
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise HTTPException(422, '原查询文字无效')
    try:
        requirement = EvidenceRequirement.model_validate(evidence).model_dump()
    except ValidationError:
        raise HTTPException(422, '原明确资料要求不符合固定契约')
    try:
        if next(Draft202012Validator(result_schema(catalog)).iter_errors(value), None) is not None:
            raise CandidateValidationError('模型候选不符合固定v6查询结构、字段类型或所选目录')
        wire = v3.Wire.validate_python(value, strict=True).model_dump()
    except ValidationError:
        raise CandidateValidationError('模型候选不符合固定v6契约')
    proposal = _compile(wire, catalog)
    diagnostics = []

    def block(code, message, step_id=None):
        item = Diagnostic(code, message, step_id)
        if item not in diagnostics:
            diagnostics.append(item)

    for message in proposal['questions']:
        block('model_clarification', message)
    fields = {item['key']: item for item in catalog['fields']}
    objects = {item['id']: item for item in catalog['objects']}
    relations = {item['id']: item for item in catalog['relation_types']}
    tools = {item['id']: item for item in catalog['query_tools']}
    selected = {key: fields[key] for key in proposal['fields']}
    if len(proposal['fields']) != len(selected):
        block('duplicate_fields', '候选字段不能重复。')
    for item in catalog.get('field_conflicts', []):
        if item['key'] in selected:
            block('field_conflict', '请先明确字段“' + fields[item['key']]['label'] + '”的一致类型和单位。')
    required = {('field', key) for key in selected}
    relation = proposal['relation_query']
    if relation:
        if len(relation['root_ids']) != len(set(relation['root_ids'])) or len(relation['relation_types']) != len(set(relation['relation_types'])):
            block('duplicate_relation_scope', '关系根对象和类型不能重复。')
        required.update(('root', key) for key in relation['root_ids'])
        required.update(('relation', key) for key in relation['relation_types'])
        for root in relation['root_ids']:
            label = objects[root]['label'].strip().casefold()
            if sum(item['label'].strip().casefold() == label for item in objects.values()) > 1:
                block('ambiguous_root', '起查对象“' + objects[root]['label'] + '”存在同名对象，请明确具体对象。')
            endpoint = 'source_types' if relation['direction'] == 'outgoing' else 'target_types'
            if not any(objects[root]['type'] in relations[key][endpoint] for key in relation['relation_types']):
                block('relation_direction', '起查对象类型与所选关系方向不一致。')
        if relation['at']:
            from .temporal import instant
            from .models import now
            try:
                if instant(relation['at']) > now():
                    block('future_relation_time', '不能把未来关系时点当作已有关系事实。')
            except HTTPException:
                block('invalid_relation_time', '关系时点须为可核对的带偏移量时间戳。')
    frames = {'evidence': selected}
    ranked = {'evidence': False}
    methods = []
    for step in proposal['tool_steps']:
        identity = step['id']
        tool = tools[step['capability_id']]
        method = tool['method']
        methods.append(method)
        parameters = step['parameters']
        field = parameters.get('field')
        source = frames.get(step['input_from'], {})
        prop = source.get(field)
        if identity in frames or step['input_from'] not in frames:
            block('invalid_producer', '查询步骤须引用明确的原资料或前序结果。', identity)
        if method not in v1.METHODS:
            block('unsupported_tool', '候选不能使用其他工具或外部能力。', identity)
        if not prop:
            block('producer_field', '查询字段不属于本步骤的实际输入结果。', identity)
        if method in {'keyword', 'fulltext'}:
            required.add(('filter', identity))
            if prop and prop['type'] != 'string':
                block('text_field_type', '关键词筛选和全文检索须使用文字字段。', identity)
            frames[identity] = source if prop and prop['type'] == 'string' else {}
        elif method == 'aggregate':
            required.add(('operation', identity))
            operation = parameters.get('operation')
            if prop and operation != 'count' and prop['type'] not in {'number', 'integer'}:
                block('numeric_field_type', '均值、合计和最小最大值须使用数值字段。', identity)
            if prop and operation != 'count' and not prop.get('unit'):
                block('unknown_calculation_unit', '请明确计算字段“' + prop.get('label', field) + '”的单位。', identity)
            frames[identity] = {item['key']: dict(item) for item in tool['output_fields']} if prop else {}
            if 'value' in frames[identity]:
                frames[identity]['value']['unit'] = '条证据记录' if operation == 'count' else prop.get('unit')
        else:
            frames[identity] = {}
        ranked[identity] = ranked.get(step['input_from'], False) or method == 'fulltext'
    criterion = proposal['result_criterion']
    if criterion:
        required.add(('result', criterion['field']))
        if criterion['kind'] == 'query_value' and not proposal['tool_steps']:
            block('query_criterion_without_steps', '派生结果标准需要至少一个查询步骤。')
        output = selected if criterion['kind'] == 'field_value' else frames.get(
            proposal['tool_steps'][-1]['id'] if proposal['tool_steps'] else '', {})
        prop = output.get(criterion['field'])
        if not prop or not v1._expected_matches(criterion['expected'], prop, criterion['operator']):
            block('criterion_producer', '结果标准的字段、比较方式或期望值类型不符合实际输入/末步输出。')
        elif prop['type'] in {'number', 'integer'} and not prop.get('unit'):
            block('unknown_comparison_unit', '请明确比较结果的单位；不能推定或换算未知单位。')
    spans = []
    claims = {}
    raw_claims = {}
    seen = set()
    for claim in proposal['claims']:
        key = (claim['kind'], claim['key'])
        step_id = claim['key'] if claim['kind'] in {'filter', 'operation'} else None
        raw_claims.setdefault(key, claim['quote'])
        if key not in required or key in seen:
            block('invalid_claim_target', '原文依据不能重复或指向候选中不存在的选择。', step_id)
            continue
        seen.add(key)
        start = question.find(claim['quote'])
        if start < 0 or question.find(claim['quote'], start + 1) >= 0:
            block('quote_not_unique', '请为“' + claim['key'] + '”提供原需求中唯一出现的逐字依据。', step_id)
            continue
        spans.append({**claim, 'start': start, 'end': start + len(claim['quote'])})
        claims[key] = claim['quote']
    for kind, key in sorted(required - set(claims)):
        block('missing_claim', '请核对“' + key + '”的原需求依据（' + kind + '）。',
            key if kind in {'filter', 'operation'} else None)
    grounding_steps = []
    for step in proposal['tool_steps']:
        method = tools[step['capability_id']]['method']
        if method not in {'keyword', 'fulltext'}:
            continue
        identity = step['id']
        text = step['parameters']['text']
        quote = raw_claims.get(('filter', identity), '')
        valid_quote = ('filter', identity) in claims
        if method == 'keyword':
            if not valid_quote or text.casefold() not in quote.casefold():
                block('keyword_not_contiguous', '关键词须在唯一原文引用中连续出现。', identity)
            continue
        query_tokens = text_tokens(text)
        needed = Counter(query_tokens)
        available = Counter(text_tokens(quote)) if valid_quote else Counter()
        if not query_tokens:
            block('fulltext_empty_tokens', '全文检索文字没有可支持的中文或英文词项。', identity)
        if any(count > available[token] for token, count in needed.items()):
            block('fulltext_tokens_not_grounded', '全文检索的每个词项及重复次数须有原文引用依据。', identity)
        codes = [item.code for item in diagnostics if item.step_id == identity]
        start = question.find(quote) if valid_quote else None
        grounding_steps.append({'id': identity, 'method': method, 'text': text, 'quote': quote,
            'start': start, 'end': start + len(quote) if start is not None else None,
            'status': 'blocked' if codes else 'supported', 'query_tokens': query_tokens,
            'tokens': [{'token': token, 'required_count': count, 'source_count': available[token]}
                for token, count in needed.items()], 'diagnostic_codes': codes})
    if criterion and type(criterion['expected']) in {int, float}:
        if criterion['expected'] not in v1._numbers(claims.get(('result', criterion['field']), '')):
            block('criterion_number_not_grounded', '未能从引用中核对比较数值，不推定其含义。')
    # Decisions use the full typed set, never the truncated UI questions.
    questions = list(dict.fromkeys(item.message for item in diagnostics))
    if diagnostics:
        proposal['status'] = 'needs_clarification'
    proposal['questions'] = questions if len(questions) <= 10 else questions[:9] + [
        '还有未核对的依据，请完善原需求后重新提出候选。']
    last = proposal['tool_steps'][-1]['id'] if proposal['tool_steps'] else 'evidence'
    subset = ranked[last]
    fixed = [v1.MEANING_LIMITATION]
    if subset:
        fixed.append(v1.RANKED_LIMITATION)
    if any(step['parameters'].get('operation') == 'count' for step in proposal['tool_steps']):
        fixed.append(v1.COUNT_LIMITATION)
    result = {'candidate': proposal, 'claim_spans': spans,
        'constraints': {'meaning_confirmed': False, 'semantic_equivalence_verified': False,
            'sample_unit': 'evidence_records', 'ranked_subset': subset,
            'requires_ranked_subset_confirmation': subset and 'aggregate' in methods,
            'limitations': fixed, 'evidence_requirement': requirement},
        'wire_contract_version': CONTRACT_VERSION, 'raw_hash': digest(value),
        'question_hash': digest(question),
        'source_grounding': {'schema': GROUNDING_SCHEMA, 'tokenizer_version': TOKENIZER_VERSION,
            'semantic_equivalence_verified': False, 'raw_hash': digest(value),
            'question_hash': digest(question), 'blocker_count': len(diagnostics),
            'diagnostics': [asdict(item) for item in diagnostics], 'steps': grounding_steps}}
    result['query_effect'] = v5.derive_query_effect(proposal, catalog)
    return result
