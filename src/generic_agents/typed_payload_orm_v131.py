"""Private same-row canonical payloads; JSONB remains the query projection.

Called by Base only after a class has been mapped.  No models import belongs
here: the codec and SQL expressions must also work for scalar/Core reads.
New INSERT defaults run before JSONB binding, including Core/multiVALUES.
An ORM update signs only an actual data assignment, never a status/lease edit.
Migration and the no-downgrade database trigger are separate responsibilities.
"""
import json
import hashlib

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, Column, Text, cast, event, func, inspect, literal, text, type_coerce
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, Label
from sqlalchemy.types import TypeDecorator

from .typed_payload_codec_v131 import (
    ERROR, LEGACY_MARKER, SCHEMA, TypedPayloadError, canonical_json,
    decode_payload, encode_payload, new_marker,
)

FORMAT = '_ga_data_format'
CANONICAL = '_ga_data_canonical'
INTERNAL = {'typed_payload_internal': True}
MARKER_PATTERN = r'^typed-json-v131:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'


def _blocked():
    raise HTTPException(409, ERROR) from None


class _DecodedPayloadText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_result_value(self, value, dialect):
        try:
            def object_pairs(pairs):
                result = {}
                for key, item in pairs:
                    if key in result:
                        raise TypedPayloadError(ERROR)
                    result[key] = item
                return result
            wrapper = json.loads(value, object_pairs_hook=object_pairs)
            if type(wrapper) is not dict or set(wrapper) != {'marker', 'canonical', 'projection', 'owner'}:
                raise TypedPayloadError(ERROR)
            return decode_payload(wrapper['canonical'], wrapper['marker'],
                                  wrapper['owner'], wrapper['projection'])
        except (TypedPayloadError, TypeError, ValueError, UnicodeError, RecursionError):
            _blocked()


class CanonicalJSONB(TypeDecorator):
    """Full payload reads decode TEXT; JSONB predicates keep native operators."""
    impl = JSONB
    comparator_factory = JSONB.Comparator
    cache_ok = True

    def __init__(self, owner_table, field='data'):
        super().__init__()
        self.owner_table = str(owner_table)
        self.field = field

    def coerce_compared_value(self, op, value):
        return self.impl.coerce_compared_value(op, value)

    def compare_values(self, left, right):
        if left is NO_VALUE or right is NO_VALUE:
            return left is right
        try:
            return canonical_json(left) == canonical_json(right)
        except TypedPayloadError:
            # The real write/default processor rejects unsafe assigned values.
            return False

    def process_bind_param(self, value, dialect):
        try:
            canonical_json(value)
            return value
        except TypedPayloadError:
            # SQLAlchemy wraps bind/default failures in StatementError.  Keep
            # the safe codec error for db.session's narrow orig-error mapping.
            raise TypedPayloadError(ERROR) from None

    def column_expression(self, column):
        actual = column.element if isinstance(column, Label) else column
        table = getattr(actual, 'table', None)
        # Indexed JSON expressions intentionally return native query projections,
        # not a purported full payload without an owner/format envelope.
        if table is None:
            if isinstance(actual, BinaryExpression) and actual.operator in (
                    operators.json_getitem_op, operators.json_path_getitem_op):
                return column
            raise TypedPayloadError(ERROR)
        if getattr(actual, 'name', None) != self.field:
            return column
        columns = table.c
        if not all(key in columns for key in (self.field, 'id', FORMAT, CANONICAL)):
            raise TypedPayloadError(ERROR)
        owner = func.json_build_object(
            'table', self.owner_table, 'field', self.field,
            'owner_id', cast(columns.id, Text),
            'workspace_id', cast(columns.workspace_id, Text) if 'workspace_id' in columns else cast(literal(None), Text),
        )
        wrapper = func.json_build_object(
            'marker', columns[FORMAT], 'canonical', columns[CANONICAL],
            'projection', cast(actual, Text), 'owner', owner,
        )
        return type_coerce(cast(wrapper, Text), _DecodedPayloadText())


def _owner(table, owner_id, workspace_id):
    return {'table': str(table.name), 'field': 'data', 'owner_id': owner_id,
            'workspace_id': workspace_id}


def _default_parameters(context):
    """Get this INSERT row, including earlier generated id/format defaults.

    SQLAlchemy's isolated multiVALUES dictionary omits other generated defaults.
    Its original/multiparam column carries the exact group index; use the full
    parameter dictionary but never borrow an unsuffixed value from another row.
    """
    parameters = context.get_current_parameters(isolate_multiinsert_groups=False)
    column = context.current_column
    state = context.compiled.compile_state
    if not getattr(state, '_has_multi_parameters', False):
        return parameters
    index = column.index + 1 if getattr(column, '_is_multiparam_column', False) else 0
    result = {}
    for key in ('id', 'workspace_id', 'data', FORMAT):
        grouped = key + '_m' + str(index)
        if grouped in parameters:
            result[key] = parameters[grouped]
        elif index == 0 and key in parameters:
            result[key] = parameters[key]
    return result


def _canonical_default(table):
    def value(context):
        try:
            parameters = _default_parameters(context)
            if not all(key in parameters for key in ('id', 'data', FORMAT)):
                raise TypedPayloadError(ERROR)
            workspace_id = parameters['workspace_id'] if 'workspace_id' in table.c else None
            return encode_payload(parameters['data'], _owner(table, parameters['id'], workspace_id),
                                  parameters[FORMAT])
        except (TypedPayloadError, KeyError, AttributeError, TypeError, ValueError):
            raise TypedPayloadError(ERROR) from None
    return value


def _prepare_update(mapper, connection, target):
    state = inspect(target)
    if not state.attrs.data.history.has_changes():
        return
    try:
        table = mapper.local_table
        marker = new_marker()
        canonical = encode_payload(target.data, _owner(table, target.id,
            getattr(target, 'workspace_id', None)), marker)
        setattr(target, FORMAT, marker)
        setattr(target, CANONICAL, canonical)
    except TypedPayloadError:
        _blocked()


def constraint_name(table):
    name = 'ck_' + table.name + '_typed_payload_v131'
    return name if len(name) <= 63 else name[:46] + '_' + hashlib.sha256(name.encode()).hexdigest()[:16]


def _constraint(table):
    columns = table.c
    envelope = cast(columns[CANONICAL], JSONB)
    names = ['schema', 'owner', 'marker', 'canonical', 'payload_hash']
    expected_owner = func.jsonb_build_object(
        'table', str(table.name), 'field', 'data', 'owner_id', cast(columns.id, Text),
        'workspace_id', cast(columns.workspace_id, Text) if 'workspace_id' in columns else cast(literal(None), Text),
    )
    typed = (
        columns[FORMAT].op('~')(MARKER_PATTERN)
        & (func.jsonb_typeof(envelope) == 'object')
        & envelope.op('?&')(array(names, type_=Text))
        & (envelope.op('-')(cast(array(names, type_=Text), ARRAY(Text))) == cast(literal('{}'), JSONB))
        & (envelope['schema'].astext == SCHEMA)
        & (envelope['marker'].astext == columns[FORMAT])
        & (envelope['owner'] == expected_owner)
        & (func.jsonb_typeof(envelope['canonical']) == 'string')
        & (envelope['payload_hash'].astext.op('~')(r'^[0-9a-f]{64}$'))
        & (cast(envelope['canonical'].astext, JSONB) == columns.data)
    )
    legacy = (columns[FORMAT] == LEGACY_MARKER) & columns[CANONICAL].is_(None)
    return CheckConstraint((legacy | typed).is_(True), name=constraint_name(table))


def instrument_model(cls):
    """Instrument a mapped data/JSONB class exactly once, without importing Base."""
    table = cls.__dict__.get('__table__')
    if table is None or 'data' not in table.c:
        return
    if isinstance(table.c.data.type, CanonicalJSONB):
        return
    if not isinstance(table.c.data.type, JSONB):
        return
    if 'id' not in table.c or FORMAT in table.c or CANONICAL in table.c:
        raise TypedPayloadError(ERROR)
    table.c.data.type = CanonicalJSONB(table.name)
    # Declarative setattr adds both table columns and mapped attributes.  Their
    # order is deliberate: id/data defaults precede format, which precedes TEXT.
    setattr(cls, FORMAT, Column(Text, default=new_marker,
        server_default=text("'typed-json-v131:unpaired'"), nullable=False, info=dict(INTERNAL)))
    setattr(cls, CANONICAL, Column(Text, default=_canonical_default(table),
        nullable=True, info=dict(INTERNAL)))
    implementation = getattr(cls.data, 'impl', None)
    if implementation is not None:
        # Normally Base runs before mapper configuration.  Also keep an already
        # configured mapper's history comparison strict when instrumented later.
        implementation.is_equal = table.c.data.type.compare_values
    table.append_constraint(_constraint(table))
    event.listen(cls, 'before_update', _prepare_update)
