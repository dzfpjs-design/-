"""Pure typed projection only; not producer, authority, semantic or Task proof."""
import copy
import math

import pytest
from jsonschema import Draft202012Validator

from generic_agents import guarded_port_projection_v198 as projection
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import no_external


def ident(number): return '19800000-0000-4000-8000-' + str(number).zfill(12)


def obj(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def reference(expression, *, kind='object', nullable=False, unit=None, origin='/packet'):
    return {'schema': projection.SCHEMA, 'source': projection.SOURCE, 'kind': kind,
        'nullable': nullable, 'unit': unit, 'consumer_node_id': 'resense',
        'origin': {'node_id': 'read_context', 'port_ordinal': 0, 'output_pointer': origin},
        'projection': expression}


def mapping(*, require_non_null=True, minimum=1, maximum=16):
    return {'op': 'map', 'pointer': '/candidates', 'min_items': minimum, 'max_items': maximum,
        'require_non_null': require_non_null, 'item': {'op': 'object', 'fields': {
            'candidate_ref': {'op': 'ordinal_label', 'prefix': 'candidate_'},
            'summary': {'op': 'literal', 'value': '待核观察方案'},
            'context': {'op': 'copy', 'pointer': ''}}}}


def scene():
    context = obj(gap_id={'type': 'string', 'pattern': r'^[0-9a-f-]{36}$'}, reading={'type': 'number'})
    packet = obj(candidates={'type': 'array', 'minItems': 0, 'maxItems': 1000, 'items': context},
        evidence_ids={'type': 'array', 'items': {'type': 'string', 'pattern': r'^[0-9a-f-]{36}$'}},
        nothing={**obj(), 'type': ['object', 'null']}, temperature={'type': 'number', 'x-unit': '°C'})
    schema = obj(packet=packet, outside=obj(evidence_ids={'type': 'array', 'items': {'type': 'string'}}))
    value = {'packet': {'candidates': [{'gap_id': ident(1), 'reading': 1.0},
            {'gap_id': ident(2), 'reading': -0.0}], 'evidence_ids': [ident(3)], 'nothing': None,
            'temperature': -0.0}, 'outside': {'evidence_ids': [ident(99)]}}
    ref = reference({'op': 'object', 'fields': {'evidence_ids': {'op': 'copy', 'pointer': '/evidence_ids'},
        'candidates': mapping()}})
    return schema, value, ref


def test_compound_port_wraps_every_original_context_in_order_without_modifying_values():
    schema, value, ref = scene(); before = canonical_json(({'schema': schema, 'value': value, 'reference': ref}))
    derived = projection.derive_schema(schema, ref)
    result = projection.project(value, ref)
    Draft202012Validator(derived).validate(result)
    assert kernel._field(obj(payload=derived), 'payload') == ('object', False)
    assert result['evidence_ids'] == value['packet']['evidence_ids']
    assert [row['candidate_ref'] for row in result['candidates']] == ['candidate_1', 'candidate_2']
    assert all(row['summary'] == '待核观察方案' for row in result['candidates'])
    assert canonical_json([row['context'] for row in result['candidates']]) == canonical_json(value['packet']['candidates'])
    assert type(result['candidates'][0]['context']['reading']) is float
    assert math.copysign(1, result['candidates'][1]['context']['reading']) == -1
    result['candidates'][0]['context']['gap_id'] = ident(100)
    derived['properties'].clear()
    assert canonical_json({'schema': schema, 'value': value, 'reference': ref}) == before


def conditional_object():
    value = obj(route={'enum': ['acquire', 'continue']},
        context={**obj(value={'type': 'number'}), 'type': ['object', 'null']})
    value['allOf'] = [{'if': {'properties': {'route': {'const': 'acquire'}}},
        'then': {'properties': {'context': {'type': 'object'}}},
        'else': {'properties': {'context': {'type': 'null'}}}}]
    return value


def test_explicit_object_conjunction_preserves_original_output_and_selected_child_constraints():
    schema = conditional_object(); before = copy.deepcopy(schema)
    ref = reference({'op': 'select', 'pointer': '/context', 'require_non_null': True}, origin='')
    derived = projection.derive_schema(schema, ref)
    assert derived == obj(value={'type': 'number'})
    valid = {'route': 'acquire', 'context': {'value': 1}}
    Draft202012Validator(schema).validate(valid)
    assert projection.project(valid, ref) == {'value': 1}
    assert not Draft202012Validator(schema).is_valid({'route': 'continue', 'context': {'value': 1}})
    with pytest.raises(projection.ProjectionUnavailable):
        projection.project({'route': 'continue', 'context': None}, ref)
    assert schema == before


@pytest.mark.parametrize('damage', ['inferred_type', 'nullable_parent', 'anyOf', 'oneOf', 'whole_output', 'child_union'])
def test_parent_conjunction_does_not_infer_types_flatten_unions_or_relax_whole_output(damage):
    schema = conditional_object()
    ref = reference({'op': 'select', 'pointer': '/context', 'require_non_null': True}, origin='')
    if damage == 'inferred_type': schema.pop('type')
    elif damage == 'nullable_parent': schema['type'] = ['object', 'null']
    elif damage in {'anyOf', 'oneOf'}: schema[damage] = [{'type': 'object'}]
    elif damage == 'whole_output': ref['projection'] = {'op': 'copy', 'pointer': ''}
    else: schema['properties']['context']['anyOf'] = [{'type': 'object'}, {'type': 'null'}]
    with pytest.raises(projection.ProjectionContractError):
        projection.derive_schema(schema, ref)


def test_dependency_metadata_retains_original_port_and_scoped_map_reads_without_authority():
    _, _, ref = scene()
    dependency = projection.source_dependencies(ref)
    assert dependency['origin'] == ref['origin'] and dependency['whole_output_required'] is False
    assert dependency['permissions_verified'] is False and dependency['can_dispatch'] is False
    assert {'expression_path': '/projection/fields/candidates/item/fields/context',
        'pointer': '', 'map_ancestry': ['/candidates']} in dependency['reads']
    whole = copy.deepcopy(ref); whole['origin']['output_pointer'] = ''
    assert projection.source_dependencies(whole)['whole_output_required'] is True
    # This states a dependency, not that a whole published output was declared
    # by the original source port. Only the caller/compiler can establish that.
    dependency['origin']['port_ordinal'] = 9
    assert ref['origin']['port_ordinal'] == 0


def test_explicit_nonnull_guard_preserves_nullable_schema_until_selected_value_is_available():
    schema, value, _ = scene()
    ordinary = reference({'op': 'copy', 'pointer': '/nothing'}, nullable=True)
    assert kernel._field(obj(payload=projection.derive_schema(schema, ordinary)), 'payload') == ('object', True)
    assert projection.project(value, ordinary) is None
    lie = copy.deepcopy(ordinary); lie['nullable'] = False
    with pytest.raises(projection.ProjectionContractError): projection.derive_schema(schema, lie)
    guarded = reference({'op': 'select', 'pointer': '/nothing', 'require_non_null': True})
    assert kernel._field(obj(payload=projection.derive_schema(schema, guarded)), 'payload') == ('object', False)
    with pytest.raises(projection.ProjectionUnavailable) as waiting: projection.project(value, guarded)
    assert waiting.value.kind == 'null' and waiting.value.reason
    value['packet']['nothing'] = {}
    assert projection.project(value, guarded) == {}  # This is the actual {}, not an invented fallback.


def test_nullable_parent_and_arrays_require_explicit_guards_without_default_or_first_selection():
    schema = obj(packet={**obj(child={'type': 'string'}), 'type': ['object', 'null']})
    ref = reference({'op': 'copy', 'pointer': '/child'}, kind='string')
    with pytest.raises(projection.ProjectionContractError): projection.derive_schema(schema, ref)
    ref['projection'] = {'op': 'select', 'pointer': '/child', 'require_non_null': True}
    assert projection.derive_schema(schema, ref)['type'] == 'string'
    with pytest.raises(projection.ProjectionUnavailable) as waiting: projection.project({'packet': None}, ref)
    assert waiting.value.kind == 'null'
    source, value, _ = scene()
    ref = reference({'op': 'copy', 'pointer': '/candidates/0'})
    with pytest.raises(projection.ProjectionContractError): projection.derive_schema(source, ref)
    with pytest.raises(projection.ProjectionUnavailable) as indexed: projection.project(value, ref)
    assert indexed.value.kind == 'type'
    for operation in ({'op': 'first', 'pointer': '/candidates'}, {'op': 'default', 'value': {}},
            {'op': 'filter', 'pointer': '/candidates'}, {'op': 'lookup', 'id': ident(1)}):
        with pytest.raises(projection.ProjectionContractError): projection.validate_reference_shape(reference(operation))


def test_projection_cannot_escape_its_declared_origin_or_guess_missing_pointer_segments():
    schema, value, _ = scene()
    for pointer in ('/outside/evidence_ids', '/../outside/evidence_ids', '/evidence_id'):
        ref = reference({'op': 'copy', 'pointer': pointer}, kind='array')
        with pytest.raises(projection.ProjectionContractError): projection.derive_schema(schema, ref)
        with pytest.raises(projection.ProjectionUnavailable) as missing: projection.project(value, ref)
        assert missing.value.kind == 'missing'
    schema = obj(packet=obj(**{'a/b': obj(**{'~leaf': {'type': 'number'}})}))
    ref = reference({'op': 'copy', 'pointer': '/a~1b/~0leaf'}, kind='number')
    assert projection.derive_schema(schema, ref)['type'] == 'number'
    assert canonical_json(projection.project({'packet': {'a/b': {'~leaf': -0.0}}}, ref)) == '-0.0'
    for invalid in ('a/b', '/bad~2escape', '/bad~'):
        with pytest.raises(projection.ProjectionContractError):
            projection.validate_reference_shape(reference({'op': 'copy', 'pointer': invalid}))


@pytest.mark.parametrize('size', [0, 17])
def test_map_capacity_stops_the_entire_result_never_truncates_or_selects_a_candidate(size):
    schema, value, _ = scene()
    value['packet']['candidates'] = [copy.deepcopy(value['packet']['candidates'][0]) for _ in range(size)]
    before = canonical_json(value)
    ref = reference(mapping(), kind='array')
    assert projection.derive_schema(schema, ref)['maxItems'] == 16
    with pytest.raises(projection.ProjectionUnavailable) as blocked: projection.project(value, ref)
    assert blocked.value.kind == 'capacity' and canonical_json(value) == before


def test_map_nullable_array_keeps_null_unless_a_real_nonnull_guard_is_declared():
    schema, value, _ = scene(); schema['properties']['packet']['properties']['candidates']['type'] = ['array', 'null']
    value['packet']['candidates'] = None
    ref = reference(mapping(require_non_null=False), kind='array', nullable=True)
    assert projection.derive_schema(schema, ref)['type'] == ['array', 'null']
    assert projection.project(value, ref) is None
    ref['projection']['require_non_null'] = True; ref['nullable'] = False
    assert projection.derive_schema(schema, ref)['type'] == 'array'
    with pytest.raises(projection.ProjectionUnavailable) as waiting: projection.project(value, ref)
    assert waiting.value.kind == 'null'


@pytest.mark.parametrize('actual,kind', [(True, 'boolean'), (1, 'integer'), (1.0, 'number'), (-0.0, 'number')])
def test_json_numeric_types_and_negative_zero_are_never_coerced(actual, kind):
    schema = obj(packet=obj(value={'type': kind}))
    ref = reference({'op': 'copy', 'pointer': '/value'}, kind=kind)
    assert projection.derive_schema(schema, ref)['type'] == kind
    result = projection.project({'packet': {'value': actual}}, ref)
    assert type(result) is type(actual) and canonical_json(result) == canonical_json(actual)
    other = False if kind != 'boolean' else 0
    with pytest.raises(projection.ProjectionUnavailable) as invalid:
        projection.project({'packet': {'value': other}}, ref)
    assert invalid.value.kind == 'type'


def test_units_local_schema_refs_and_schema_constraints_are_not_normalized_or_dropped():
    schema, value, _ = scene()
    ref = reference({'op': 'copy', 'pointer': '/temperature'}, kind='number', unit='°C')
    assert projection.derive_schema(schema, ref) == {'type': 'number', 'x-unit': '°C'}
    assert canonical_json(projection.project(value, ref)) == '-0.0'
    ref['unit'] = 'K'
    with pytest.raises(projection.ProjectionContractError): projection.derive_schema(schema, ref)
    schema = obj(packet={'$ref': '#/$defs/packet'}); schema['$defs'] = {'packet': obj(name={'type': 'string', 'minLength': 2, 'maxLength': 20})}
    ref = reference({'op': 'copy', 'pointer': '/name'}, kind='string')
    assert projection.derive_schema(schema, ref) == {'type': 'string', 'minLength': 2, 'maxLength': 20}
    schema['properties']['packet'] = {'$ref': 'https://untrusted.invalid/schema'}
    with pytest.raises(projection.ProjectionContractError): projection.derive_schema(schema, ref)


def test_literal_only_supplies_display_labels_and_cannot_invent_evidence_uuid_or_candidate_selection():
    for value in (ident(1), True, 1, 1.0, None, {'evidence_id': ident(1)}):
        with pytest.raises(projection.ProjectionContractError):
            projection.validate_reference_shape(reference({'op': 'literal', 'value': value}, kind='string'))
    with pytest.raises(projection.ProjectionContractError):
        projection.validate_reference_shape(reference({'op': 'ordinal_label', 'prefix': 'candidate_'}, kind='string'))
    bad = mapping(); bad['item']['fields']['evidence_id'] = bad['item']['fields'].pop('candidate_ref')
    with pytest.raises(projection.ProjectionContractError): projection.validate_reference_shape(reference(bad, kind='array'))
    _, value, ref = scene(); result = projection.project(value, ref)
    assert result['evidence_ids'] == [ident(3)] and ident(99) not in canonical_json(result)
    assert all(row['candidate_ref'] != ident(3) for row in result['candidates'])
    # Copying an existing identifier does not classify it as Evidence. Actual
    # database identity, consent and source-port semantic contract remain caller
    # checks; the pure module reports no identity or authority verification.
    assert projection.source_dependencies(ref)['permissions_verified'] is False


@pytest.mark.parametrize('fault', ['unknown_key', 'unknown_origin', 'bad_ordinal', 'self_source', 'bad_nullable', 'missing_guard'])
def test_reference_and_operator_shapes_are_closed(fault):
    _, _, ref = scene()
    if fault == 'unknown_key': ref['verified'] = True
    elif fault == 'unknown_origin': ref['origin']['workspace_id'] = ident(1)
    elif fault == 'bad_ordinal': ref['origin']['port_ordinal'] = True
    elif fault == 'self_source': ref['origin']['node_id'] = ref['consumer_node_id']
    elif fault == 'bad_nullable': ref['nullable'] = 0
    else: del ref['projection']['fields']['candidates']['require_non_null']
    with pytest.raises(projection.ProjectionContractError): projection.validate_reference_shape(ref)


def test_depth_bytes_and_whole_array_bounds_fail_without_partial_results():
    expr = {'op': 'copy', 'pointer': ''}
    for _ in range(projection.MAX_EXPRESSION_DEPTH + 1): expr = {'op': 'object', 'fields': {'wrapped': expr}}
    with pytest.raises(projection.ProjectionContractError): projection.validate_reference_shape(reference(expr))
    oversized = {'op': 'object', 'fields': {str(index): {'op': 'literal', 'value': 'x' * 4000} for index in range(40)}}
    with pytest.raises(projection.ProjectionContractError): projection.validate_reference_shape(reference(oversized))
    ref = reference({'op': 'copy', 'pointer': ''}, kind='array')
    with pytest.raises(projection.ProjectionUnavailable) as blocked: projection.project({'packet': list(range(projection.MAX_ITEMS + 1))}, ref)
    assert blocked.value.kind == 'capacity'
    with pytest.raises(projection.ProjectionUnavailable): projection.project({'packet': [float('nan')]}, ref)
    ambiguous = projection.ProjectionUnavailable('ambiguous', '原生产者存在多个当前产物，未选择第一项。')
    assert ambiguous.kind == 'ambiguous'  # The actual product-count decision is the native caller's job.
