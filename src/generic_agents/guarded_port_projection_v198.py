"""Bounded, deterministic projections within ONE explicitly declared source port.

No producer is selected here. A caller must establish the real unique completed
product, declared port, Task/context/iteration and current authority. Dependency
metadata records reads; neither its marker nor a derived schema grants access.
"""
import copy
import math
import re

from .contracts import check_schema
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'guarded-port-projection-v198'
SOURCE = 'port_projection'
DEPENDENCIES = 'guarded-port-projection-dependencies-v198'
MAX_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_BYTES = 128 * 1024
MAX_DEPTH = 32
MAX_EXPRESSION_DEPTH = 8
MAX_EXPRESSION_NODES = 128
MAX_VALUE_NODES = 100000
MAX_ITEMS = 1000
MAX_FIELDS = 64
MAX_POINTER_BYTES = 2048
MAX_POINTER_PARTS = 32
KINDS = {'boolean', 'integer', 'number', 'string', 'date', 'object', 'array'}
REF_KEYS = {'schema', 'source', 'kind', 'nullable', 'unit', 'consumer_node_id', 'origin', 'projection'}
_UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


class ProjectionContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('端口投影的明确范围、结构或类型无法核对：' + code)


class ProjectionUnavailable(ValueError):
    def __init__(self, kind, reason):
        if kind not in {'null', 'missing', 'ambiguous', 'type', 'capacity'}:
            raise ProjectionContractError('unavailable_kind')
        self.kind = kind
        self.reason = reason
        super().__init__(reason)


def _need(ok, code):
    if not ok: raise ProjectionContractError(code)


def _text(value, limit, *, empty=False):
    _need(type(value) is str and (empty or bool(value)) and '\x00' not in value, 'text')
    try: encoded = value.encode('utf-8', 'strict')
    except UnicodeError: raise ProjectionContractError('unicode') from None
    _need(len(encoded) <= limit, 'text_limit')
    return value


def _bounded(value, maximum=MAX_BYTES):
    count = 0
    def visit(item, depth, ancestors):
        nonlocal count
        count += 1
        _need(depth <= MAX_DEPTH and count <= MAX_VALUE_NODES, 'value_capacity')
        kind = type(item)
        if item is None or kind in {bool, int}: return
        if kind is float:
            _need(math.isfinite(item), 'finite_number'); return
        if kind is str:
            _text(item, maximum, empty=True); return
        _need(kind in {dict, list} and not any(item is parent for parent in ancestors), 'json_type')
        parents = (*ancestors, item)
        if kind is list:
            _need(len(item) <= MAX_ITEMS, 'array_capacity')
            for child in item: visit(child, depth + 1, parents)
        else:
            for key, child in item.items():
                _text(key, maximum, empty=True); visit(child, depth + 1, parents)
    visit(value, 0, ())
    try: encoded = canonical_json(value).encode('utf-8', 'strict')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ProjectionContractError('typed_json') from None
    _need(len(encoded) <= maximum, 'byte_capacity')


def _pointer(value):
    _text(value, MAX_POINTER_BYTES, empty=True)
    if value == '': return []
    _need(value.startswith('/'), 'json_pointer')
    parts = value[1:].split('/')
    _need(len(parts) <= MAX_POINTER_PARTS, 'pointer_capacity')
    _need(all(re.search(r'~(?![01])', part) is None for part in parts), 'json_pointer_escape')
    return [part.replace('~1', '/').replace('~0', '~') for part in parts]


def _expression(value, *, depth=0, count=None, in_map=False, field=None):
    if count is None: count = [0]
    count[0] += 1
    _need(depth <= MAX_EXPRESSION_DEPTH and count[0] <= MAX_EXPRESSION_NODES, 'expression_capacity')
    _need(type(value) is dict and type(value.get('op')) is str, 'expression_shape')
    op = value['op']
    if op in {'select', 'copy'}:
        _need(set(value) == ({'op', 'pointer', 'require_non_null'} if op == 'select' else {'op', 'pointer'}), 'selection_shape')
        _pointer(value['pointer'])
        if op == 'select': _need(type(value['require_non_null']) is bool, 'nonnull_guard')
    elif op == 'map':
        _need(set(value) == {'op', 'pointer', 'item', 'min_items', 'max_items', 'require_non_null'}, 'map_shape')
        _pointer(value['pointer'])
        _need(type(value['min_items']) is int and type(value['max_items']) is int
            and 0 <= value['min_items'] <= value['max_items'] <= MAX_ITEMS
            and type(value['require_non_null']) is bool, 'map_bounds')
        _expression(value['item'], depth=depth + 1, count=count, in_map=True)
    elif op == 'object':
        _need(set(value) == {'op', 'fields'} and type(value['fields']) is dict
            and 1 <= len(value['fields']) <= MAX_FIELDS, 'object_shape')
        for key, child in value['fields'].items():
            _text(key, 200)
            _expression(child, depth=depth + 1, count=count, in_map=in_map, field=key)
    elif op == 'literal':
        _need(set(value) == {'op', 'value'}, 'literal_shape')
        _text(value['value'], 4000)
        _need(bool(value['value'].strip()) and _UUID.fullmatch(value['value']) is None, 'literal_label_only')
    elif op == 'ordinal_label':
        _need(set(value) == {'op', 'prefix'} and in_map and field == 'candidate_ref', 'ordinal_candidate_only')
        _need(type(value['prefix']) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,38}_', value['prefix']) is not None,
            'ordinal_label_prefix')
    else:
        raise ProjectionContractError('unsupported_operation')


def validate_reference_shape(ref):
    _bounded(ref, MAX_REFERENCE_BYTES)
    _need(type(ref) is dict and set(ref) == REF_KEYS and ref['schema'] == SCHEMA
        and ref['source'] == SOURCE and type(ref['kind']) is str and ref['kind'] in KINDS
        and type(ref['nullable']) is bool, 'reference_shape')
    _text(ref['consumer_node_id'], 200)
    _need(bool(ref['consumer_node_id'].strip()), 'consumer_identity')
    _need(ref['unit'] is None or type(ref['unit']) is str and bool(ref['unit'].strip()), 'unit')
    if ref['unit'] is not None: _text(ref['unit'], 200)
    origin = ref['origin']
    _need(type(origin) is dict and set(origin) == {'node_id', 'port_ordinal', 'output_pointer'}, 'origin_shape')
    _text(origin['node_id'], 200)
    _need(bool(origin['node_id'].strip()) and origin['node_id'] != ref['consumer_node_id'] and type(origin['port_ordinal']) is int
        and 0 <= origin['port_ordinal'] < MAX_ITEMS, 'origin_identity')
    _pointer(origin['output_pointer'])
    _expression(ref['projection'])
    return copy.deepcopy(ref)


def source_dependencies(ref):
    checked = validate_reference_shape(ref)
    reads = []
    def walk(expr, path, ancestry):
        if expr['op'] in {'select', 'copy', 'map'}:
            reads.append({'expression_path': path, 'pointer': expr['pointer'], 'map_ancestry': list(ancestry)})
        if expr['op'] == 'object':
            for key, child in expr['fields'].items():
                walk(child, path + '/fields/' + key.replace('~', '~0').replace('/', '~1'), ancestry)
        elif expr['op'] == 'map': walk(expr['item'], path + '/item', (*ancestry, expr['pointer']))
    walk(checked['projection'], '/projection', ())
    return {'schema': DEPENDENCIES, 'origin': checked['origin'],
        'whole_output_required': checked['origin']['output_pointer'] == '', 'reads': reads,
        'permissions_verified': False, 'can_dispatch': False}


def _schema_ref(root, pointer):
    _need(type(pointer) is str and pointer.startswith('#/'), 'schema_reference')
    value = root
    for part in _pointer(pointer[1:]):
        if type(value) is dict:
            _need(part in value, 'schema_reference'); value = value[part]
        elif type(value) is list:
            _need(re.fullmatch(r'0|[1-9][0-9]*', part) is not None and int(part) < len(value), 'schema_reference')
            value = value[int(part)]
        else: raise ProjectionContractError('schema_reference')
    _need(type(value) is dict, 'schema_reference')
    return value


def _expand_schema(value, root, stack=(), counter=None):
    if counter is None: counter = [0]
    counter[0] += 1
    _need(counter[0] <= MAX_VALUE_NODES and len(stack) <= MAX_DEPTH, 'schema_capacity')
    if type(value) is list:
        return [_expand_schema(item, root, stack, counter) for item in value]
    if type(value) is not dict: return copy.deepcopy(value)
    _need(not {'$id', '$dynamicRef', '$recursiveRef'} & set(value), 'schema_reference')
    if '$ref' in value:
        _need(set(value) <= {'$ref', 'title', 'description', 'x-unit'}, 'schema_ref_siblings')
        pointer = value['$ref']; _need(type(pointer) is str and pointer not in stack, 'schema_reference_cycle')
        result = _expand_schema(_schema_ref(root, pointer), root, (*stack, pointer), counter)
        for key in ('title', 'description', 'x-unit'):
            if key in value:
                _need(key != 'x-unit' or key not in result or result[key] == value[key], 'schema_unit_conflict')
                result[key] = copy.deepcopy(value[key])
        return result
    return {key: _expand_schema(child, root, stack, counter) for key, child in value.items()}


def _types(schema):
    _need(type(schema) is dict and not {'anyOf', 'oneOf', 'allOf', 'not', 'if', 'then', 'else'} & set(schema),
        'schema_composition_not_supported')
    types = schema.get('type')
    if types is None:
        values = [schema['const']] if 'const' in schema else schema.get('enum')
        _need(type(values) is list and bool(values), 'schema_type')
        names = {type(None): 'null', bool: 'boolean', str: 'string', int: 'integer', float: 'number', dict: 'object', list: 'array'}
        _need(all(type(item) in names for item in values), 'schema_type')
        kinds = {names[type(item)] for item in values}
        if 'number' in kinds: kinds.discard('integer')
        types = sorted(kinds)
    types = [types] if type(types) is str else types
    _need(type(types) is list and bool(types) and all(type(item) is str for item in types)
        and len(types) == len(set(types)), 'schema_type')
    nonnull = set(types) - {'null'}
    _need(len(nonnull) == 1 and nonnull <= KINDS - {'date'}, 'schema_type')
    kind = next(iter(nonnull))
    if kind == 'string' and schema.get('format') == 'date': kind = 'date'
    return kind, 'null' in types


def _nonnull(schema):
    result = copy.deepcopy(schema)
    kind, nullable = _types(result)
    if not nullable: return result
    result['type'] = 'string' if kind == 'date' else kind
    if 'enum' in result:
        result['enum'] = [item for item in result['enum'] if item is not None]
        _need(bool(result['enum']), 'nonnull_unsatisfiable')
    _need(result.get('const', object()) is not None, 'nonnull_unsatisfiable')
    return result


def _schema_at(schema, pointer, *, guard=False):
    current = schema
    for part in _pointer(pointer):
        parent = current
        if type(parent) is dict and 'allOf' in parent:
            # A conjunction may only further restrict this explicit object.
            # Its base properties still bound every valid producer value.
            # Keep every child constraint; do not flatten unions, infer types
            # from branches, or relax whole-output/selected-value validation.
            _need(parent.get('type') == 'object' and type(parent['allOf']) is list
                and bool(parent['allOf']), 'schema_composition_not_supported')
            parent = {key: value for key, value in parent.items() if key != 'allOf'}
        kind, nullable = _types(parent)
        _need(not nullable or guard, 'nullable_parent_needs_explicit_guard')
        _need(kind == 'object', 'array_index_or_scalar_selection_not_supported')
        properties = current.get('properties')
        _need(type(properties) is dict and part in properties and type(properties[part]) is dict, 'schema_pointer_missing')
        current = properties[part]
    return copy.deepcopy(current)


def _derive(expr, source, *, in_map=False):
    op = expr['op']
    if op in {'copy', 'select'}:
        result = _schema_at(source, expr['pointer'], guard=op == 'select' and expr['require_non_null'])
        return _nonnull(result) if op == 'select' and expr['require_non_null'] else result
    if op == 'object':
        properties = {key: _derive(child, source, in_map=in_map) for key, child in expr['fields'].items()}
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    if op == 'literal': return {'type': 'string', 'const': expr['value']}
    if op == 'ordinal_label':
        _need(in_map, 'ordinal_candidate_only')
        return {'type': 'string', 'pattern': '^' + re.escape(expr['prefix']) + '[1-9][0-9]*$',
            'maxLength': len(expr['prefix']) + len(str(MAX_ITEMS))}
    array = _schema_at(source, expr['pointer'], guard=expr['require_non_null'])
    kind, nullable = _types(array)
    _need(kind == 'array' and 'prefixItems' not in array and type(array.get('items')) is dict, 'map_schema')
    low, high = array.get('minItems', 0), array.get('maxItems', MAX_ITEMS)
    _need(type(low) is int and type(high) is int and 0 <= low <= high, 'map_schema_bounds')
    low, high = max(low, expr['min_items']), min(high, expr['max_items'])
    _need(low <= high, 'map_guard_unsatisfiable')
    return {'type': ['array', 'null'] if nullable and not expr['require_non_null'] else 'array',
        'items': _derive(expr['item'], array['items'], in_map=True), 'minItems': low, 'maxItems': high}


def derive_schema(origin_output_schema, ref):
    checked = validate_reference_shape(ref)
    _bounded(origin_output_schema)
    _need(type(origin_output_schema) is dict, 'origin_schema')
    try: check_schema(origin_output_schema)
    except Exception: raise ProjectionContractError('origin_schema') from None
    expanded = _expand_schema(origin_output_schema, origin_output_schema)
    _bounded(expanded)
    # Origin selection itself cannot silently narrow a nullable published port.
    origin = _schema_at(expanded, checked['origin']['output_pointer'])
    result = _derive(checked['projection'], origin)
    _bounded(result)
    _need(_types(result) == (checked['kind'], checked['nullable'])
        and result.get('x-unit') == checked['unit'], 'declared_projection_contract')
    return copy.deepcopy(result)


def _value_at(value, pointer):
    current = value
    for part in _pointer(pointer):
        if current is None: raise ProjectionUnavailable('null', '原资料的中间层级为空，当前步骤尚无完整输入。')
        if type(current) is list:
            raise ProjectionUnavailable('type', '不能按索引挑取数组记录；须保留原完整顺序。')
        if type(current) is not dict:
            raise ProjectionUnavailable('type', '原资料结构与明确的投影路径不一致，未转换或补值。')
        if part not in current: raise ProjectionUnavailable('missing', '原资料尚未提供这一字段，未填入默认值。')
        current = current[part]
    return current


def _evaluate(expr, source, ordinal=None):
    op = expr['op']
    if op in {'copy', 'select'}:
        result = _value_at(source, expr['pointer'])
        if result is None and op == 'select' and expr['require_non_null']:
            raise ProjectionUnavailable('null', '原结果尚无明确可用方案；未替换为空对象或派发设备。')
        return copy.deepcopy(result)
    if op == 'object': return {key: _evaluate(child, source, ordinal) for key, child in expr['fields'].items()}
    if op == 'literal': return expr['value']
    if op == 'ordinal_label':
        _need(type(ordinal) is int, 'ordinal_candidate_only')
        return expr['prefix'] + str(ordinal)
    array = _value_at(source, expr['pointer'])
    if array is None:
        if not expr['require_non_null']: return None
        raise ProjectionUnavailable('null', '原候选清单为空值，当前步骤不能继续。')
    if type(array) is not list: raise ProjectionUnavailable('type', '原候选清单不是数组，未转换为其他类型。')
    if not expr['min_items'] <= len(array) <= expr['max_items']:
        raise ProjectionUnavailable('capacity', '原候选数量不符合明确范围；保留全部原记录，未截断或挑选。')
    return [_evaluate(expr['item'], item, index + 1) for index, item in enumerate(array)]


def _result_type(value, kind, nullable):
    if value is None:
        if nullable: return
        raise ProjectionUnavailable('null', '原结果为空值，当前步骤尚无明确输入。')
    valid = {'boolean': type(value) is bool, 'integer': type(value) is int,
        'number': type(value) in {int, float}, 'string': type(value) is str,
        'date': type(value) is str, 'object': type(value) is dict, 'array': type(value) is list}
    if not valid[kind]: raise ProjectionUnavailable('type', '投影结果类型与明确合同不符，未强制转换。')


def project(value, ref):
    checked = validate_reference_shape(ref)
    try: _bounded(value)
    except ProjectionContractError as error:
        raise ProjectionUnavailable('capacity' if 'capacity' in error.code else 'type',
            '原资料超过投影容量或不属于安全的完整JSON，未截断或转换。') from None
    origin = _value_at(value, checked['origin']['output_pointer'])
    result = _evaluate(checked['projection'], origin)
    _result_type(result, checked['kind'], checked['nullable'])
    try: _bounded(result)
    except ProjectionContractError:
        raise ProjectionUnavailable('capacity', '完整投影结果超过容量，未返回局部结果。') from None
    return copy.deepcopy(result)
