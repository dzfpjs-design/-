"""SQL compilation/default/result contracts only: no engine, sessions or DB.

These tests execute SQLAlchemy's actual default processor in memory.  Root's
separate PostgreSQL tests must establish binding order, constraints and rollback
against the issued schema; compiling a statement is not a persistence proof.
"""
import json
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, bindparam, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column
from sqlalchemy.orm.attributes import NO_VALUE, set_committed_value
from sqlalchemy.schema import AddConstraint, CreateTable

from generic_agents.typed_payload_codec_v131 import (
    ERROR, LEGACY_MARKER, TypedPayloadError, decode_payload, encode_payload, new_marker,
)
from generic_agents.typed_payload_orm_v131 import (
    CANONICAL, FORMAT, CanonicalJSONB, _DecodedPayloadText,
    _canonical_default, _prepare_update, constraint_name, instrument_model,
)


class LocalBase(DeclarativeBase):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        instrument_model(cls)


class ScopedRow(LocalBase):
    __tablename__ = 'typed_payload_scope_test'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default='draft')
    version: Mapped[int] = mapped_column(Integer, default=1)


class UnscopedRow(LocalBase):
    __tablename__ = 'typed_payload_unscoped_test'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))


def defaults(statement, many=None):
    compiled = statement.compile(dialect=postgresql.dialect())
    context = DefaultExecutionContext.__new__(DefaultExecutionContext)
    context.compiled = compiled
    context.dialect = postgresql.dialect()
    context.compiled_parameters = ([compiled.construct_params(params=value) for value in many]
                                   if many is not None else [compiled.construct_params()])
    # Use the installed library's processor, not a copied default loop.
    context._process_execute_defaults()
    return compiled, context.compiled_parameters


def group(parameters, key, index):
    return parameters[key + '_m' + str(index)] if key + '_m' + str(index) in parameters else parameters[key]


def owner(table, identity='owner-a', workspace='workspace-a'):
    return {'table': str(table.name), 'field': 'data', 'owner_id': identity,
            'workspace_id': workspace if 'workspace_id' in table.c else None}


def test_base_hook_instruments_once_in_declared_default_order():
    table = ScopedRow.__table__
    assert isinstance(table.c.data.type, CanonicalJSONB)
    assert type(table.c.data.type.owner_table) is str
    columns = list(table.c.keys())
    assert columns.index('id') < columns.index(FORMAT) < columns.index(CANONICAL)
    assert columns.index('data') < columns.index(CANONICAL)
    assert table.c[FORMAT].nullable is False and table.c[CANONICAL].nullable is True
    assert table.c[FORMAT].info['typed_payload_internal'] is True
    assert table.c[CANONICAL].info['typed_payload_internal'] is True
    before = list(table.c)
    instrument_model(ScopedRow)
    assert list(table.c) == before


def test_non_jsonb_and_no_payload_are_not_instrumented():
    for column in [Column('data', JSON), Column('ordinary', String)]:
        table = Table('plain_' + column.name, MetaData(), Column('id', String, primary_key=True), column)
        model = type('Plain', (), {'__table__': table})
        instrument_model(model)
        assert FORMAT not in table.c and CANONICAL not in table.c


@pytest.mark.parametrize('statement', [
    lambda: select(ScopedRow.data),
    lambda: select(ScopedRow),
    lambda: select(ScopedRow.data.label('saved_payload')),
    lambda: select(aliased(ScopedRow, name='actual_alias').data),
    lambda: insert(ScopedRow).values(workspace_id='workspace-a', data={'n': 1.0}).returning(ScopedRow.data),
])
def test_scalar_orm_alias_label_and_returning_include_actual_owner_envelope(statement):
    compiled = statement().compile(dialect=postgresql.dialect())
    rendered = str(compiled)
    assert 'json_build_object' in rendered and FORMAT in rendered and CANONICAL in rendered
    strings = {value for value in compiled.params.values() if type(value) is str}
    assert {'projection', 'owner_id', 'workspace_id'} <= strings
    assert 'typed_payload_scope_test' in rendered
    if 'actual_alias' in rendered:
        assert 'actual_alias.id' in rendered
        assert 'actual_alias.' + FORMAT in rendered


@pytest.mark.parametrize('predicate', [
    lambda: ScopedRow.data['nested']['flag'].as_string() == 'value',
    lambda: ScopedRow.data[('nested', 'flag')].as_string() == 'value',
    lambda: ScopedRow.data[['nested', 'flag']].as_string() == 'value',
    lambda: ScopedRow.data.has_key('flag'),
    lambda: ScopedRow.data.contains({'flag': [1, 2]}),
])
def test_jsonb_index_paths_filters_keep_native_query_projection(predicate):
    # Binding JSONB comparison literals with literal_binds is not supported by
    # SQLAlchemy itself, so inspect the parameterized compiled WHERE here.
    rendered = str(select(ScopedRow.id).where(predicate()).compile(dialect=postgresql.dialect()))
    assert 'WHERE' in rendered and 'json_build_object' not in rendered
    assert FORMAT not in rendered and CANONICAL not in rendered


def test_index_select_is_projection_but_partial_full_payload_subquery_fails_closed():
    assert 'json_build_object' not in sql(select(ScopedRow.data['nested']))
    partial = select(ScopedRow.data).subquery('missing_owner')
    with pytest.raises(TypedPayloadError, match=ERROR):
        sql(select(partial.c.data))


def test_constraint_is_closed_bound_to_actual_owner_projection_and_null_safe():
    constraint = next(item for item in ScopedRow.__table__.constraints if item.name == constraint_name(ScopedRow.__table__))
    rendered = sql(AddConstraint(constraint))
    for required in [FORMAT, CANONICAL, 'IS true', 'legacy-jsonb-v1', '?&', 'jsonb_build_object', 'payload_hash', 'canonical', 'owner']:
        assert required.lower() in rendered.lower()
    assert 'typed_payload_scope_test.id' not in rendered, 'constraint uses local unqualified columns'
    assert 'typed-json-v131:unpaired' in sql(CreateTable(ScopedRow.__table__))


def test_constraint_long_names_are_stable_bounded_and_distinct():
    one = SimpleNamespace(name='typed_' + 'x' * 80)
    two = SimpleNamespace(name=one.name + 'y')
    assert len(constraint_name(one)) <= 63
    assert constraint_name(one) == constraint_name(one)
    assert constraint_name(one) != constraint_name(two)


@pytest.mark.parametrize('value', [{'n': -0.0, 'large': 1e21, 'one': 1.0}, {'n': 0, 'list': [1.0, -0.0]}])
def test_core_insert_defaults_capture_original_python_before_jsonb_bind(value):
    compiled, rows = defaults(insert(ScopedRow).values(workspace_id='workspace-a', data=value))
    parameters = rows[0]
    prefetch = [column.key for column in compiled.insert_prefetch]
    assert prefetch.index('id') < prefetch.index(FORMAT) < prefetch.index(CANONICAL)
    decoded = decode_payload(parameters[CANONICAL], parameters[FORMAT],
        owner(ScopedRow.__table__, parameters['id']), json.dumps(value))
    assert type(decoded.get('one', 1.0)) is float
    if value['n'] == 0 and type(value['n']) is float:
        assert math.copysign(1, decoded['n']) == -1


@pytest.mark.parametrize('explicit_ids', [False, True])
def test_multivalues_actual_defaults_use_each_row_generated_owner_and_marker(explicit_ids):
    values = [{'workspace_id': 'workspace-a', 'data': {'n': -0.0}},
              {'workspace_id': 'workspace-b', 'data': {'n': 1e21}}]
    if explicit_ids:
        for index, value in enumerate(values):
            value['id'] = 'owner-' + str(index)
    _, rows = defaults(insert(ScopedRow).values(values))
    parameters = rows[0]
    identities, markers = [], []
    for index, original in enumerate(values):
        identity, marker = group(parameters, 'id', index), group(parameters, FORMAT, index)
        result = decode_payload(group(parameters, CANONICAL, index), marker,
            owner(ScopedRow.__table__, identity, original['workspace_id']), json.dumps(original['data']))
        assert result == original['data']
        assert type(result['n']) is float
        identities.append(identity); markers.append(marker)
    assert len(set(identities)) == len(set(markers)) == 2


def test_executemany_defaults_do_not_share_owner_marker_or_canonical_text():
    statement = insert(ScopedRow).values(workspace_id=bindparam('workspace_id'), data=bindparam('data'))
    _, rows = defaults(statement, [{'workspace_id': 'a', 'data': {'n': -0.0}},
                                 {'workspace_id': 'b', 'data': {'n': 1.0}}])
    assert rows[0]['id'] != rows[1]['id'] and rows[0][FORMAT] != rows[1][FORMAT]
    for row in rows:
        assert decode_payload(row[CANONICAL], row[FORMAT], owner(ScopedRow.__table__, row['id'], row['workspace_id']),
                              json.dumps(row['data'])) == row['data']


def test_on_conflict_do_nothing_keeps_same_insert_defaults_and_cold_returning():
    statement = insert(ScopedRow).values(id='known-owner', workspace_id='workspace-a', data={'n': -0.0})
    statement = statement.on_conflict_do_nothing(index_elements=['id']).returning(ScopedRow.data)
    compiled, rows = defaults(statement)
    assert 'ON CONFLICT' in str(compiled) and 'json_build_object' in str(compiled)
    assert rows[0][CANONICAL]
    assert decode_payload(rows[0][CANONICAL], rows[0][FORMAT], owner(ScopedRow.__table__, 'known-owner'), '{"n":0.0}')['n'] == 0


def test_unscoped_default_and_read_envelope_use_explicit_null_workspace():
    _, rows = defaults(insert(UnscopedRow).values(data={'n': -0.0}))
    row = rows[0]
    assert json.loads(row[CANONICAL])['owner']['workspace_id'] is None
    assert 'workspace_id' in sql(select(UnscopedRow.data))


@pytest.mark.parametrize('missing', ['id', 'data', FORMAT, 'workspace_id'])
def test_default_never_invents_missing_owner_payload_or_marker(missing):
    parameters = {'id': 'owner-a', 'workspace_id': 'workspace-a', 'data': {'n': -0.0}, FORMAT: new_marker()}
    parameters.pop(missing)
    context = SimpleNamespace(current_column=ScopedRow.__table__.c[CANONICAL],
        compiled=SimpleNamespace(compile_state=SimpleNamespace(_has_multi_parameters=False)),
        get_current_parameters=lambda **kwargs: parameters)
    with pytest.raises(TypedPayloadError) as error:
        _canonical_default(ScopedRow.__table__)(context)
    assert str(error.value) == ERROR


def test_result_decode_preserves_signed_types_not_query_jsonb_spelling():
    table = ScopedRow.__table__; marker = new_marker(); original = {'n': -0.0, 'large': 1e21, 'one': 1.0}
    wrapper = {'marker': marker, 'canonical': encode_payload(original, owner(table), marker),
               'projection': '{"n":0.0,"large":1000000000000000000000,"one":1.0}', 'owner': owner(table)}
    value = _DecodedPayloadText().process_result_value(json.dumps(wrapper), None)
    assert math.copysign(1, value['n']) == -1 and type(value['large']) is float and type(value['one']) is float


@pytest.mark.parametrize('change', ['owner', 'marker', 'projection', 'missing', 'extra', 'corrupt'])
def test_result_wrapper_corruption_is_safe_409_without_repair(change):
    table = ScopedRow.__table__; marker = new_marker()
    wrapper = {'marker': marker, 'canonical': encode_payload({'n': -0.0}, owner(table), marker),
               'projection': '{"n":0.0}', 'owner': owner(table)}
    if change == 'owner': wrapper['owner'] = owner(table, 'foreign-owner')
    if change == 'marker': wrapper['marker'] = new_marker()
    if change == 'projection': wrapper['projection'] = '{"n":1}'
    if change == 'missing': wrapper['canonical'] = None
    if change == 'extra': wrapper['private'] = 'must not echo'
    if change == 'corrupt': wrapper['canonical'] = '{private bad'
    with pytest.raises(HTTPException) as error:
        _DecodedPayloadText().process_result_value(json.dumps(wrapper), None)
    assert error.value.status_code == 409 and error.value.detail == ERROR


def test_legacy_read_never_guesses_lost_sign_or_synthesizes_text():
    wrapper = {'marker': LEGACY_MARKER, 'canonical': None, 'projection': '{"n":0}', 'owner': owner(ScopedRow.__table__)}
    assert _DecodedPayloadText().process_result_value(json.dumps(wrapper), None) == {'n': 0}
    assert wrapper['canonical'] is None


@pytest.mark.parametrize('left,right', [(1, 1.0), (0.0, -0.0), (True, 1), ({'v': 1}, {'v': 1.0})])
def test_dirty_comparison_retains_types_sign_and_boolean_boundary(left, right):
    assert ScopedRow.__table__.c.data.type.compare_values(left, right) is False


def test_no_value_comparison_is_safe_and_order_only_dict_change_is_equal():
    kind = ScopedRow.__table__.c.data.type
    assert kind.compare_values(NO_VALUE, NO_VALUE) is True
    assert kind.compare_values(NO_VALUE, {}) is False
    assert kind.compare_values({'a': 1, 'b': 2.0}, {'b': 2.0, 'a': 1}) is True


def committed(value):
    row = ScopedRow(id='owner-a', workspace_id='workspace-a', data=value, status='draft', version=1)
    set_committed_value(row, 'data', value)
    set_committed_value(row, FORMAT, LEGACY_MARKER)
    set_committed_value(row, CANONICAL, None)
    return row


def test_status_lease_like_update_does_not_sign_legacy_or_repair_tampered_text():
    row = committed({'n': 0})
    row.status = 'published'; row.version = 2
    _prepare_update(inspect(ScopedRow), None, row)
    assert getattr(row, FORMAT) == LEGACY_MARKER and getattr(row, CANONICAL) is None
    set_committed_value(row, CANONICAL, 'damaged text')
    _prepare_update(inspect(ScopedRow), None, row)
    assert getattr(row, CANONICAL) == 'damaged text'


@pytest.mark.parametrize('before,after', [({'n': 1}, {'n': 1.0}), ({'n': 0.0}, {'n': -0.0})])
def test_actual_data_history_change_creates_new_bound_text_and_marker(before, after):
    row = committed(before); row.data = after
    assert inspect(row).attrs.data.history.has_changes()
    _prepare_update(inspect(ScopedRow), None, row)
    marker = getattr(row, FORMAT)
    assert marker != LEGACY_MARKER
    value = decode_payload(getattr(row, CANONICAL), marker, owner(ScopedRow.__table__), json.dumps(after))
    assert type(value['n']) is float
    if math.copysign(1, after['n']) == -1:
        assert math.copysign(1, value['n']) == -1


@pytest.mark.parametrize('value', [float('nan'), float('inf'), '\ud800', {'private': 'bad\0value'}])
def test_unsafe_payload_bind_is_safe_codec_error_before_serializer(value):
    with pytest.raises(TypedPayloadError) as error:
        ScopedRow.__table__.c.data.type.process_bind_param(value, postgresql.dialect())
    assert str(error.value) == ERROR
