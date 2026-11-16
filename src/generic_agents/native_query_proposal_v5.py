"""The unchanged v4 wire with a server-derived, non-executing plan explanation.

This describes registered producer frames, never the meaning of a question or
the records a query would actually find. Historic validators remain immutable.
"""
import copy

from . import native_query_proposal_v4 as v4

CONTRACT_VERSION = 5
EFFECT_SCHEMA = 'native-query-effect-v1'
LIMITATIONS = [
    '这是已登记工具的计划效果，未执行查询，未解析实际关系节点或推测结果数量。',
    '字段、出处、输入输出和单位的结构说明不证明候选符合原需求，仍须人工核对含义。',
    '计数不是实物数量；注册的通用计数单位须结合实际输入分母阅读，前序聚合的一行不是原始样本总数。',
    '出处来自服务器绑定的证据元数据；业务名称字段不是出处引用的替代。',
]


def result_schema(catalog):
    """v5 deliberately asks the provider for exactly the same wire as v4."""
    return v4.result_schema(catalog)


def _field(value):
    return {key: value.get(key, value.get('key') if key == 'label' else None)
        for key in ('key', 'label', 'type', 'unit')}


def _frame(identity, fields, *, shape='records', record_unit='evidence_records',
        cardinality='zero_or_more', ranked_subset=False, subset_limits=None):
    return {'id': identity, 'shape': shape, 'record_unit': record_unit,
        'cardinality': cardinality, 'fields': copy.deepcopy(fields),
        'ranked_subset': ranked_subset, 'subset_limits': copy.deepcopy(subset_limits or [])}


def derive_query_effect(candidate, catalog):
    """Explain an already validated plan using its exact input_from edges.

    The catalog is server-built from hash-pinned native manifests. Unknown
    producers/shapes stay unknown here; this function never repairs a plan.
    No business question, source values, graph lookup or oracle is consulted.
    """
    relation = candidate.get('relation_query')
    effect = {'schema': EFFECT_SCHEMA, 'status': 'derived',
        'semantic_equivalence_verified': False, 'selected_fields': [],
        'source_scope': {'kind': 'relation_result_set' if relation else 'selected_task_entities',
            'relation_query': copy.deepcopy(relation), 'resolved_entities': False},
        'provenance': {'evidence_metadata': ['entity_id', 'version', 'observation_id', 'source'],
            'tool_record_metadata': ['entity_ids', 'evidence_refs'],
            'requires_business_name_field': False},
        'steps': [], 'final': None, 'unknown_reasons': [], 'limitations': list(LIMITATIONS)}
    if candidate.get('status') != 'candidate':
        effect['status'] = 'not_applicable'
        return effect

    def unknown(reason):
        effect['status'] = 'unknown'
        if reason not in effect['unknown_reasons']:
            effect['unknown_reasons'].append(reason)

    declared = {item['key']: item for item in catalog.get('fields', [])}
    for key in candidate.get('fields', []):
        if key not in declared:
            unknown('selected_field_not_in_catalog:' + key)
            effect['selected_fields'].append({'key': key, 'label': key, 'type': None, 'unit': None})
        else:
            effect['selected_fields'].append(_field(declared[key]))
    source = _frame('evidence', effect['selected_fields'])
    if effect['status'] == 'unknown':
        source.update(shape='unknown', record_unit='unknown', cardinality='unknown', ranked_subset=None)
    frames = {'evidence': source}
    tools = {item['id']: item for item in catalog.get('query_tools', [])}
    for step in candidate.get('tool_steps', []):
        identity = step['id']
        tool = tools.get(step['capability_id'], {})
        method = tool.get('method')
        parameters = step['parameters']
        operation = parameters.get('operation') if method == 'aggregate' else None
        field = parameters.get('field')
        input_frame = frames.get(step['input_from'])
        if input_frame is None:
            unknown('input_frame_not_found:' + identity)
            input_frame = _frame(step['input_from'], [], shape='unknown', record_unit='unknown',
                cardinality='unknown', ranked_subset=None)
        prop = next((item for item in input_frame['fields'] if item['key'] == field), None)
        output = copy.deepcopy(input_frame)
        output['id'] = identity
        denominator = None
        if prop is None or input_frame['shape'] == 'unknown':
            unknown('input_field_or_shape_unknown:' + identity)
            output = _frame(identity, [], shape='unknown', record_unit='unknown',
                cardinality='unknown', ranked_subset=None)
        elif method in {'keyword', 'fulltext'}:
            # builtin keyword/fulltext return the input records unchanged,
            # including their values and source references, selecting a subset.
            output['cardinality'] = 'zero_or_more'
            if method == 'fulltext':
                output['ranked_subset'] = True
                output['subset_limits'].append({'step_id': identity, 'method': method,
                    'parameters': {key: parameters[key] for key in ('field', 'top_k', 'min_score', 'match_mode')}})
        elif method == 'aggregate' and operation in {'count', 'sum', 'mean', 'min', 'max'}:
            registered = tool.get('output_fields', [])
            types = {item['key']: item.get('type') for item in registered}
            if types != {'value': 'number', 'sample_count': 'integer', 'field': 'string', 'operation': 'string'}:
                unknown('aggregate_output_shape_unknown:' + identity)
                output = _frame(identity, [], shape='unknown', record_unit='unknown',
                    cardinality='unknown', ranked_subset=None)
            else:
                fields = [_field(item) for item in registered]
                for item in fields:
                    if item['key'] == 'value':
                        # Match the existing v4 validator/registered convention;
                        # denominator below distinguishes prior aggregate rows.
                        item['unit'] = '条证据记录' if operation == 'count' else prop['unit']
                output = _frame(identity, fields, shape='aggregate_record', record_unit='aggregate_rows',
                    cardinality='one_on_success', ranked_subset=input_frame['ranked_subset'],
                    subset_limits=input_frame['subset_limits'])
                denominator = {'frame_id': input_frame['id'], 'record_unit': input_frame['record_unit'],
                    'ranked_subset': input_frame['ranked_subset'],
                    'subset_limits': copy.deepcopy(input_frame['subset_limits']),
                    'on_success': 'all_input_rows', 'empty_input': 'error_no_statistic',
                    'value_requirement': 'present_non_null_finite' if operation == 'count' else 'finite_number_not_boolean',
                    'missing_values': 'error_no_silent_skip'}
        else:
            unknown('producer_method_unknown:' + identity)
            output = _frame(identity, [], shape='unknown', record_unit='unknown',
                cardinality='unknown', ranked_subset=None)
        effect['steps'].append({'id': identity, 'input_from': step['input_from'],
            'capability_id': step['capability_id'], 'method': method, 'operation': operation,
            'field': field, 'input': copy.deepcopy(input_frame), 'output': copy.deepcopy(output),
            'denominator': denominator})
        frames[identity] = output
    effect['final'] = copy.deepcopy(frames[effect['steps'][-1]['id']] if effect['steps'] else source)
    return effect


def validate_candidate(value, question, catalog, evidence):
    validated = v4.validate_candidate(value, question, catalog, evidence)
    return {**validated, 'wire_contract_version': CONTRACT_VERSION,
        'query_effect': derive_query_effect(validated['candidate'], catalog)}
