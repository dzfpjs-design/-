"""The actual, context-scoped values behind original human input labels."""
import copy
from fastapi import HTTPException
from . import full_semantic_graph_v1 as graph


def field_schema(root, field):
    """Keep native local-reference context when showing an extracted field."""
    graph._field(root, field)
    item, pointer, seen = root, '#', set()
    while '$ref' in item:
        pointer = item['$ref']
        if pointer in seen: raise ValueError('scalar_reference')
        seen.add(pointer); item = root
        for part in pointer[2:].split('/'):
            item = item[part.replace('~1', '/').replace('~0', '~')]
    selected = item['properties'][field]
    def has_ref(value):
        return (isinstance(value, dict) and ('$ref' in value or any(has_ref(v) for v in value.values()))
            or isinstance(value, list) and any(has_ref(v) for v in value))
    if pointer == '#' and not has_ref(selected): return copy.deepcopy(selected)
    def rebase(value):
        if isinstance(value, dict):
            return {k: '#/$defs/source' + v[1:] if k == '$ref' else rebase(v) for k, v in value.items()}
        if isinstance(value, list): return [rebase(v) for v in value]
        return copy.deepcopy(value)
    escaped = field.replace('~', '~0').replace('/', '~1')
    return {'$ref': '#/$defs/source' + pointer[1:] + '/properties/' + escaped,
        '$defs': {'source': rebase(root)}}


def describe(binding, state, activation_id):
    if binding.get('schema') == 'business-flow-nested-execution-binding-v195':
        return _nested_describe(binding, state, activation_id)
    if binding.get('schema') != 'business-flow-execution-binding-v1': return []
    actual = graph.check_run(binding['graph'], state)
    activation = actual['activations'][activation_id]
    node = next(n for n in binding['graph']['nodes'] if n['id'] == activation['node_id'])
    if node['kind'] != 'human': return []
    supplied = binding['execution_bindings']['nodes'][node['source_step_id']]['input_ports']
    values, producers = graph._resolution(binding['graph'], actual, graph._metadata(binding['graph']), activation['context'])
    result = []
    for label, key in supplied.items():
        ref = binding['graph']['references'][key]
        if ref['source'] == 'input': root_schema = binding['graph']['input_schema']
        else:
            source = next(n for n in binding['graph']['nodes'] if n['id'] == ref['node_id'])
            root_schema = source['execution']['output_schema'] if source['kind'] == 'ability' else source['output_schema']
        result.append({'label': label, 'available': key in values,
            'value': copy.deepcopy(values[key]) if key in values else None,
            'source': ref['source'], 'producer_activation_id': producers.get(key),
            'schema': field_schema(root_schema, ref['field'])})
    return result


def _nested_describe(binding, state, activation_id):
    """Actual recursive ports, including a unique current-branch result."""
    try:
        actual = graph.check_run(binding['graph'], state)
        activation = actual['activations'][activation_id]
        nodes = {node['id']: node for node in binding['graph']['nodes']}
        node = nodes[activation['node_id']]
        if node['kind'] != 'human': return []
        actions = binding['translation']['source_mapping']['actions']
        mapping = [item for item in actions if item['graph_node_id'] == node['id']]
        if len(mapping) != 1: raise ValueError('missing_original_action')
        mapping = mapping[0]; labels = mapping['source_action']['inputs']; ports = mapping['input_ports']
        if (type(labels) is not list or type(ports) is not list or len(labels) != len(ports)
                or any(type(port) is not dict or set(port) != {'ordinal', 'reference'}
                    or type(port['ordinal']) is not int or port['ordinal'] != index
                    for index, port in enumerate(ports))):
            raise ValueError('original_input_ports')
        meta = graph._metadata(binding['graph'])
        values, producers = graph._resolution(binding['graph'], actual, meta,
            activation['context'], consumer_node_id=node['id'])
        titles = {item['graph_node_id']: item['source_action']['name'] for item in actions}
        result = []
        for index, port in enumerate(ports):
            key = port['reference']; ref = binding['graph']['references'][key]
            source, field, availability = ref['source'], ref.get('field'), 'ready' if key in values else 'missing'
            source_title = '流程启动时提供的资料'; derived_schema = None
            if source == 'input': root_schema = binding['graph']['input_schema']
            else:
                source_id = ref.get('node_id')
                if source == 'port_projection':
                    source_id = ref['origin']['node_id']
                    resolution = graph._projection_resolution(ref,actual,meta,activation['context'],node['id'])
                    availability = 'ready' if resolution['status'] == 'unique' else resolution['status']
                    source_title = titles.get(source_id,'已完成步骤') + '中明确选定的资料'
                    source_node = nodes[source_id]
                    root = source_node['execution']['output_schema'] if source_node['kind'] == 'ability' else source_node['output_schema']
                    from .guarded_port_projection_v198 import derive_schema
                    derived_schema = derive_schema(root,ref)
                elif source == 'exclusive_output':
                    resolution = graph._exclusive_resolution(ref, actual, meta, activation['context'], node['id'])
                    availability = 'ready' if resolution['status'] == 'unique' else resolution['status']
                    # Missing/ambiguous values have no current producer. The
                    # checked candidates share kind/nullability/unit; selecting
                    # their schema here does not select an output or a value.
                    candidate = resolution if resolution['status'] == 'unique' else ref['candidates'][0]
                    source_id, field = candidate['node_id'], candidate['field']
                    source_title = (titles.get(source_id, '已完成步骤') if availability == 'ready'
                        else '等待唯一的分支结果' if availability == 'missing' else '分支返回了多个结果，需要核查')
                else: source_title = titles.get(source_id, '已完成步骤')
                source_node = nodes[source_id]
                root_schema = source_node['execution']['output_schema'] if source_node['kind'] == 'ability' else source_node['output_schema']
            result.append({'label': labels[index], 'ordinal': index, 'available': key in values,
                'value': copy.deepcopy(values[key]) if key in values else None,
                'source': source, 'source_name': source_title, 'availability_code': availability,
                'producer_activation_id': producers.get(key),
                'schema': derived_schema if derived_schema is not None else field_schema(root_schema, field)})
        return result
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, OverflowError):
        raise HTTPException(409, '原流程输入、当前分支结果或字段合同无法核对；已保存草稿保留') from None


def require_inputs(binding, state, activation_id):
    values = describe(binding, state, activation_id)
    if any(not value['available'] for value in values):
        raise HTTPException(409, '此待办的已绑定输入尚未产生；草稿仍可保存，不能用未提供的输入完成处理')
    return values
