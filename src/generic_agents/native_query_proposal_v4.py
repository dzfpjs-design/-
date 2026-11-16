"""Method-aware field choices for new requests; historic wire parsers stay fixed.

The schema bounds possible fields, not the meaning of the user's request or the
actual producer of a prior frame. The unchanged v3/v1 validators still decide
those dependencies and all source, unit, quotation and review requirements.
"""
import copy

from jsonschema import Draft202012Validator

from . import native_query_proposal_contract as v1
from . import native_query_proposal_v3 as v3

CONTRACT_VERSION = 4
SCALAR_TYPES = {'string', 'boolean', 'integer', 'number'}
NUMERIC_TYPES = {'integer', 'number'}
ARITHMETIC = ['mean', 'sum', 'min', 'max']


def result_schema(catalog):
    """Keep the quoted wire, bounding parameters by method and input family.

    A prior frame can retain source fields after filtering or expose registered
    output fields after computation. Its possible-field union deliberately does
    not substitute for the exact producer-frame check in the existing validator.
    """
    schema = copy.deepcopy(v3.result_schema(catalog))
    definitions = schema['$defs']
    source = catalog['fields']
    prior = source + [field for tool in catalog['query_tools'] for field in tool['output_fields']]
    for family, fields in [('Source', source), ('Prior', prior)]:
        for name, types in [('Text', {'string'}), ('Numeric', NUMERIC_TYPES), ('Scalar', SCALAR_TYPES)]:
            keys = sorted({field['key'] for field in fields if field['type'] in types})
            definitions['Query' + family + name + 'Fields'] = {
                'type': 'string', 'enum': keys,
                'description': '选择字段key，不能填写显示名称label；前序输入还须通过实际生产者帧核验。',
            } if keys else False
    tools = {tool['id']: tool for tool in catalog['query_tools']}
    original = definitions['Operation']
    branches = []
    for operation in original.get('oneOf', []) if isinstance(original, dict) else []:
        method = tools[operation['properties']['capability_id']['const']]['method']
        variants = [('Text', None)] if method in {'keyword', 'fulltext'} else [
            ('Scalar', ['count']), ('Numeric', ARITHMETIC)]
        for family in ['Source', 'Prior']:
            for kind, calculations in variants:
                branch = copy.deepcopy(operation)
                properties = branch['properties']
                properties['input_from'] = ({'const': 0} if family == 'Source' else
                    {'type': 'integer', 'minimum': 1, 'maximum': 5})
                bound = {'type': 'object', 'required': ['field'], 'properties': {
                    'field': {'$ref': '#/$defs/Query' + family + kind + 'Fields'}}}
                if calculations is not None:
                    bound['required'].append('operation')
                    bound['properties']['operation'] = {'enum': calculations}
                # Retain every registered argument constraint and parameter;
                # the extra schema only intersects the field/method choices.
                properties['parameters'] = {'allOf': [properties['parameters'], bound]}
                branches.append(branch)
    definitions['Operation'] = {'oneOf': branches} if branches else False
    return schema


def validate_candidate(value, question, catalog, evidence):
    if next(Draft202012Validator(result_schema(catalog)).iter_errors(value), None) is not None:
        v1._fail('模型候选不符合固定v4查询结构、字段类型或所选目录')
    validated = v3.validate_candidate(value, question, catalog, evidence)
    return {**validated, 'wire_contract_version': CONTRACT_VERSION}
