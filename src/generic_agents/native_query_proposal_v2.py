"""Versioned model wire contract with deterministic local step identities.

Frame 0 is source evidence; frame n is the output of operation n (one based).
An operation may select source or any earlier frame. The wire answer is checked
before compilation; invalid edges, identities or claims are never repaired.
The existing v1 contract remains the authority for typed frames and traceability.
"""
import copy
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import Field, StrictInt, StrictStr, ValidationError, model_validator

from . import native_query_proposal_contract as v1
from .contracts import digest

CONTRACT_VERSION = 2


class Operation(v1.StrictContract):
    capability_id: StrictStr
    input_from: StrictInt = Field(ge=0, le=5)
    parameters: dict


class MetadataClaim(v1.StrictContract):
    kind: Literal['field', 'relation', 'root', 'result']
    key: StrictStr = Field(min_length=1, max_length=100)
    quote: StrictStr = Field(min_length=1, max_length=4000)


class OperationClaim(v1.StrictContract):
    kind: Literal['filter', 'operation']
    key: StrictInt = Field(ge=1, le=6)
    quote: StrictStr = Field(min_length=1, max_length=4000)


class ProposalV2(v1.StrictContract):
    status: Literal['candidate', 'needs_clarification']
    summary: StrictStr = Field(min_length=1, max_length=2000)
    fields: list[StrictStr] = Field(max_length=30)
    relation_query: v1.ProposedRelationQuery | None
    operations: list[Operation] = Field(max_length=6)
    result_criterion: v1.ResultCriterion | None
    claims: list[MetadataClaim | OperationClaim] = Field(max_length=60)
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
        for position, operation in enumerate(self.operations):
            if operation.input_from > position:
                raise ValueError('查询操作只能引用原资料或明确前序结果')
        return self


def result_schema(catalog):
    """Expose every allowed input frame position, not arbitrary model ids."""
    schema = ProposalV2.model_json_schema()
    old = v1.result_schema(catalog)
    schema['properties']['fields'] = copy.deepcopy(old['properties']['fields'])
    schema['$defs']['ProposedRelationQuery'] = copy.deepcopy(old['$defs']['ProposedRelationQuery'])
    branches = []
    old_items = old['properties']['tool_steps']['items']
    for branch in old_items.get('oneOf', []) if isinstance(old_items, dict) else []:
        operation = copy.deepcopy(branch)
        operation['required'].remove('id')
        operation['properties'].pop('id')
        operation['properties']['input_from'] = {'type': 'integer', 'minimum': 0, 'maximum': 5}
        branches.append(operation)
    schema['$defs']['Operation'] = {'oneOf': branches} if branches else False
    items = {'$ref': '#/$defs/Operation'}
    schema['properties']['operations']['items'] = items
    schema['properties']['operations']['prefixItems'] = [
        {'allOf': [copy.deepcopy(items), {'properties': {'input_from': {'enum': list(range(position + 1))}}}]}
        for position in range(6)]
    # The kind determines the key type. A numeric-looking metadata key stays a
    # string; operation references cannot be strings or booleans.
    schema['properties']['claims']['items'] = {'oneOf': [
        {'$ref': '#/$defs/MetadataClaim'}, {'$ref': '#/$defs/OperationClaim'}]}
    return schema


def validate_candidate(value, question, catalog, evidence):
    """Strictly validate v2, compile fixed ids, then apply unchanged v1 gates.

    ``raw_hash`` hashes the parsed wire JSON; it does not replace the service's
    separately retained exact raw-response and paid-receipt hash.
    """
    if list(Draft202012Validator(result_schema(catalog)).iter_errors(value)):
        v1._fail('模型候选不符合固定v2查询结构或所选目录')
    try:
        proposal = ProposalV2.model_validate(value).model_dump()
    except ValidationError:
        v1._fail('模型候选不符合固定v2查询契约')
    compiled = copy.deepcopy(proposal)
    operations = compiled.pop('operations')
    compiled['tool_steps'] = [
        {'id': 'operation_' + str(position + 1), 'capability_id': operation['capability_id'],
            'input_from': 'evidence' if operation['input_from'] == 0 else 'operation_' + str(operation['input_from']),
            'parameters': operation['parameters']}
        for position, operation in enumerate(operations)]
    for claim in compiled['claims']:
        if claim['kind'] in {'filter', 'operation'}:
            claim['key'] = 'operation_' + str(claim['key'])
    validated = v1.validate_candidate(compiled, question, catalog, evidence)
    return {**validated, 'wire_contract_version': CONTRACT_VERSION, 'raw_hash': digest(value)}
