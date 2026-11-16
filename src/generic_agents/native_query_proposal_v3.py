"""Quoted query choices with server-owned identities and claim categories.

This is a new wire contract, not a repair path for paid v1/v2 responses. Every
quote is supplied by the model and still checked against the original question
by the unchanged local validator. Compilation does not prove meaning.
"""
import copy
from typing import Annotated, Literal

from jsonschema import Draft202012Validator
from pydantic import Field, StrictBool, StrictInt, StrictStr, TypeAdapter, ValidationError, model_validator

from . import native_query_proposal_contract as v1
from . import native_query_proposal_v2 as v2
from .contracts import digest

CONTRACT_VERSION = 3
Quote = Annotated[StrictStr, Field(min_length=1, max_length=4000)]


class FieldChoice(v1.StrictContract):
    key: StrictStr = Field(min_length=1, max_length=100)
    quote: Quote


class RootChoice(FieldChoice):
    pass


class RelationChoice(FieldChoice):
    pass


class RelationQuery(v1.StrictContract):
    roots: list[RootChoice] = Field(min_length=1, max_length=10)
    types: list[RelationChoice] = Field(min_length=1, max_length=5)
    direction: Literal['outgoing', 'incoming']
    max_hops: StrictInt = Field(ge=1, le=3)
    include_roots: StrictBool
    at: StrictStr | None = Field(max_length=50)


class Operation(v1.StrictContract):
    capability_id: StrictStr
    input_from: StrictInt = Field(ge=0, le=5)
    parameters: dict
    quote: Quote


class Criterion(v1.ResultCriterion):
    quote: Quote


class Candidate(v1.StrictContract):
    status: Literal['candidate']
    summary: StrictStr = Field(min_length=1, max_length=2000)
    fields: list[FieldChoice] = Field(min_length=1, max_length=30)
    relation_query: RelationQuery | None
    operations: list[Operation] = Field(max_length=6)
    result_criterion: Criterion | None
    limitations: list[StrictStr] = Field(max_length=10)

    @model_validator(mode='after')
    def bounded_text_and_prior_frames(self):
        if any(not value.strip() or len(value) > 1000 for value in self.limitations):
            raise ValueError('限制须为有界非空文字')
        for position, operation in enumerate(self.operations):
            if operation.input_from > position:
                raise ValueError('查询操作只能引用原资料或明确前序结果')
        return self


class Clarification(v1.StrictContract):
    status: Literal['needs_clarification']
    summary: StrictStr = Field(min_length=1, max_length=2000)
    questions: list[StrictStr] = Field(min_length=1, max_length=10)
    limitations: list[StrictStr] = Field(max_length=10)

    @model_validator(mode='after')
    def bounded_text(self):
        if any(not value.strip() or len(value) > 1000 for value in self.questions + self.limitations):
            raise ValueError('澄清问题和限制须为有界非空文字')
        return self


Wire = TypeAdapter(Annotated[Candidate | Clarification, Field(discriminator='status')])


def result_schema(catalog):
    """Expose bounded choices and inline quotes, without a free claim namespace."""
    schema = Wire.json_schema()
    definitions = schema['$defs']
    for name, values in [
        ('FieldChoice', [item['key'] for item in catalog['fields']]),
        ('RootChoice', [item['id'] for item in catalog['objects']]),
        ('RelationChoice', [item['id'] for item in catalog['relation_types']]),
    ]:
        definitions[name]['properties']['key'] = {'enum': values} if values else False
    operation = copy.deepcopy(v2.result_schema(catalog)['$defs']['Operation'])
    if isinstance(operation, dict):
        for branch in operation['oneOf']:
            branch['required'].append('quote')
            branch['properties']['quote'] = {'type': 'string', 'minLength': 1, 'maxLength': 4000}
    definitions['Operation'] = operation
    items = {'$ref': '#/$defs/Operation'}
    definitions['Candidate']['properties']['operations']['items'] = items
    definitions['Candidate']['properties']['operations']['prefixItems'] = [
        {'allOf': [copy.deepcopy(items), {'properties': {'input_from': {'enum': list(range(position + 1))}}}]}
        for position in range(6)]
    return schema


def validate_candidate(value, question, catalog, evidence):
    """Compile supplied quotations; never invent or repair model selections."""
    if list(Draft202012Validator(result_schema(catalog)).iter_errors(value)):
        v1._fail('模型候选不符合固定v3查询结构或所选目录')
    try:
        proposal = Wire.validate_python(value, strict=True).model_dump()
    except ValidationError:
        v1._fail('模型候选不符合固定v3查询契约')
    compiled = {'status': proposal['status'], 'summary': proposal['summary'], 'fields': [],
        'relation_query': None, 'tool_steps': [], 'result_criterion': None,
        'claims': [], 'questions': [], 'limitations': proposal['limitations']}
    if proposal['status'] == 'needs_clarification':
        compiled['questions'] = proposal['questions']
    else:
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
                'parameters': operation['parameters']})
            kind = 'filter' if methods[operation['capability_id']] in {'keyword', 'fulltext'} else 'operation'
            compiled['claims'].append({'kind': kind, 'key': identity, 'quote': operation['quote']})
        criterion = proposal['result_criterion']
        if criterion is not None:
            compiled['result_criterion'] = {key: value for key, value in criterion.items() if key != 'quote'}
            compiled['claims'].append({'kind': 'result', 'key': criterion['field'], 'quote': criterion['quote']})
    validated = v1.validate_candidate(compiled, question, catalog, evidence)
    return {**validated, 'wire_contract_version': CONTRACT_VERSION, 'raw_hash': digest(value)}
