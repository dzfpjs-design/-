"""Explicit v4 transport, typed hashes and actual synthetic PG source history.

Pure adapters retain negative zero. PG assertions concern the actual saved
contract (1.0 and a small exponent); they make no negative-zero storage claim.
"""
import copy
import json
import math
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import report_full_semantic_graph_wire as wire
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability
from tests.test_report_full_semantic_graph_editor_v4 import choices, FLOAT_SCHEMA
from tests.test_report_full_semantic_graph_v4 import root4, successor4
from tests.test_report_full_semantic_graph_revision import root_contract
from tests.test_graph_event_observation_sources import contract_fixture
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene, reply,
)
from tests.test_vertical_slice import manifest, publish


def test_v4_editor_wire_retains_complete_python_float_schema_and_v1_context_is_unchanged(choices):
    s = choices
    new = wire.v4_editor_context(UUID(s.identity), s.p, s.db)
    assert new['schema'] == wire.SCHEMA and new['value']['schema'] == wire.editor_v4.SCHEMA
    assert new['canonical'] == json.dumps(new['value'], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    decoded = json.loads(new['canonical'])
    schema = decoded['event_wait']['sources'][0]['contract']['result_schema']['properties']['reading']
    assert type(schema['maximum']) is float and schema['maximum'] == 1.0
    assert type(schema['minimum']) is float and math.copysign(1, schema['minimum']) == -1
    assert '"minimum":-0.0' in new['canonical'] and '"maximum":1.0' in new['canonical']
    assert '"multipleOf":1e-07' in new['canonical']
    old = wire.editor_context(UUID(s.identity), s.p, s.db)
    assert old['value'] == s.original and old['value']['schema'] == wire.editor.SCHEMA
    assert set(old['value']['event_wait']) == {'available', 'reason'}


@pytest.mark.parametrize('wrapper,target,has_body', [
    ('v4_preview', 'preview', True), ('v4_adopt', 'adopt', True),
    ('v4_recover', 'recover', False), ('v4_revision_preview', 'revision_preview', True),
    ('v4_revision_adopt', 'revision_adopt', True), ('v4_revision_recover', 'recover_revision', False),
])
def test_v4_wrappers_delegate_exact_scope_and_command_to_real_v4_family_only(monkeypatch, wrapper, target, has_body):
    identity, request_id = uuid4(), uuid4()
    p, db, body = object(), object(), object(); calls = []
    public = {'schema': 'synthetic-response', 'contract': contract_fixture(result_schema=FLOAT_SCHEMA)}
    def actual(*args): calls.append(args); return public
    monkeypatch.setattr(wire.v4, target, actual)
    result = getattr(wire, wrapper)(identity, body if has_body else request_id, p, db)
    assert calls == [(identity, body if has_body else request_id, p, db)]
    assert result['value'] is public
    assert '"minimum":-0.0' in result['canonical'] and '"multipleOf":1e-07' in result['canonical']


@pytest.mark.parametrize('kind', ['root', 'revision'])
def test_v4_command_hash_uses_actual_typed_family_and_full_float_contract_without_live_authority(monkeypatch, kind):
    p, root, history = root4()
    row = successor4(p, root, history[4], 4)[0] if kind == 'revision' else root
    command = copy.deepcopy(row.data['command'])
    # This is a typed hash fixture, not an executable preview/consent fixture.
    command['event_source_bindings'] = {'synthetic_hash_fixture': {'contract': contract_fixture(result_schema=FLOAT_SCHEMA)}}
    calls = []
    monkeypatch.setattr(wire, '_shared_handoff', lambda db, actor, identity: calls.append((db, actor, identity)))
    def forbidden(*args, **kwargs): raise AssertionError('historical hashing entered live/adoption authority')
    monkeypatch.setattr(wire.v4, 'capabilities', forbidden)
    monkeypatch.setattr(wire.v4, 'preview', forbidden)
    monkeypatch.setattr(wire.v4, 'adopt', forbidden)
    typed = (wire.v4.RevisionAdopt if kind == 'revision' else wire.v4.Adopt).model_validate(command)
    identity = UUID(row.handoff_id)
    result = wire.v4_command_hash(identity, wire.CommandHash(kind=kind, command=command), p, 'read-only-double')
    value = result['value']
    expected = (wire.revisions.command_hash(p, identity, typed, family=wire.v4)
        if kind == 'revision' else wire.base.command_hash(p, identity, typed))
    assert value['schema'] == 'report-full-semantic-graph-command-hash-v1'
    assert value['kind'] == kind and value['command'] == typed.model_dump(mode='json')
    assert value['command_hash'] == expected and len(calls) == 1
    assert '"minimum":-0.0' in result['canonical'] and '"maximum":1.0' in result['canonical']
    with pytest.raises(HTTPException) as old_route:
        wire.command_hash(identity, wire.CommandHash(kind=kind, command=command), p, 'read-only-double')
    assert old_route.value.status_code == 422


def test_v4_hash_route_rejects_v3_instead_of_silently_upgrading_it(monkeypatch):
    p, row, _ = root_contract()
    monkeypatch.setattr(wire, '_shared_handoff', lambda *args: None)
    with pytest.raises(HTTPException) as caught:
        wire.v4_command_hash(UUID(row.handoff_id), wire.CommandHash(kind='root', command=row.data['command']), p, None)
    assert caught.value.status_code == 422


def test_v4_configuration_alias_uses_the_existing_read_only_configuration_projection(monkeypatch):
    identity, p, db = uuid4(), object(), object()
    expected = wire.canonical_wire({'revision': 4, 'configuration': {'number': 1.0, 'zero': -0.0}})
    calls = []
    monkeypatch.setattr(wire, 'configuration', lambda *args: calls.append(args) or expected)
    assert wire.v4_configuration(identity, p, db) is expected
    assert calls == [(identity, p, db)]


def test_v4_routes_have_explicit_family_and_keep_original_route_contracts():
    routes = {(route.path, next(iter(route.methods))): route for route in wire.router.routes}
    prefix = wire.PREFIX
    for suffix, method in [('/editor-context', 'GET'), ('/preview', 'POST'), ('/adopt', 'POST'),
            ('/requests/{request_id}', 'GET'), ('/revisions/preview', 'POST'), ('/revisions/adopt', 'POST'),
            ('/revisions/requests/{request_id}', 'GET'), ('/configuration', 'GET'), ('/command-hash', 'POST')]:
        assert (prefix + suffix, method) in routes
        assert (prefix + '/v4' + suffix, method) in routes
    assert wire.preview.__annotations__['body'] is wire.base.Preview
    assert wire.v4_preview.__annotations__['body'] is wire.v4.Preview
    assert wire.v4_adopt.__annotations__['p'] == wire.base.review.pipeline.P
    assert wire.v4_recover.__annotations__['p'] == wire.base.review.pipeline.ReadP


def test_actual_v4_context_keeps_registered_float_schema_and_cold_history_after_source_revocation(waiting_release):
    value = waiting_release; client = value['client']; identity = value['adopt_path'].rsplit('/', 1)[-1]
    path = '/api/setup/full-semantic-graph-wire/' + identity
    current = reply(client.get(path + '/v4/editor-context'))
    assert current['value']['schema'] == wire.editor_v4.SCHEMA
    assert current['value']['event_wait']['available'] is True
    # A complete new published result schema is genuinely registered; no fake
    # producer, source authority, adoption, ledger or route is injected here.
    # Optional bounds keep the numeric-rule handler's existing result envelope
    # valid; this registration test does not invent an output it cannot emit.
    schema = {'type': 'object', 'properties': {'reading': copy.deepcopy(FLOAT_SCHEMA['properties']['reading'])}}
    schema['properties']['reading']['minimum'] = 0.0
    extractor = publish(client, manifest('tool', name='v120_float_result', allowed_agent_roles=['perception'],
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'}, output_schema=schema,
        specification={'rules': []}, data_policy={'cloud_allowed': False}))
    with Session(engine()) as db:
        source_hash = db.get(Capability, value['source']['id']).content_hash
        extractor_hash = db.get(Capability, extractor['id']).content_hash
    registered = reply(client.post('/api/setup/graph-event-producers', json={
        'schema': 'graph-event-producer-command-v1', 'request_id': str(uuid4()), 'label': '小数结果已登记来源',
        'adapter_id': 'processed-observation-v1', 'source_id': value['source']['id'], 'source_hash': source_hash,
        'extractor_id': extractor['id'], 'extractor_hash': extractor_hash}), status=201)['producer']
    context = reply(client.get(path + '/v4/editor-context'))
    choice = next(row for row in context['value']['event_wait']['sources'] if row['producer_id'] == registered['id'])
    assert choice['available'] is True and choice['contract'] == registered['contract']
    assert choice['contract_hash'] == digest(registered['contract'])
    assert '"maximum":1.0' in context['canonical'] and '"multipleOf":1e-07' in context['canonical']
    old = reply(client.get(path + '/editor-context'))['value']
    assert old['schema'] == wire.editor.SCHEMA
    assert old['adoptions'] == context['value']['adoptions']
    assert all(row['binding']['schema'] == 'report-full-semantic-graph-binding-v4' for row in old['adoptions'])
    original = next(row for row in context['value']['event_wait']['sources'] if row['producer_id'] == value['producer']['id'])
    reply(client.post('/api/setup/graph-event-producers/' + original['producer_id'] + '/revoke', json={
        'version': value['producer']['version'], 'reason': '暂停新执行，旧设计和原nonce保留'}))
    history = reply(client.get(path + '/v4/editor-context'))['value']
    revoked = next(row for row in history['event_wait']['sources'] if row['producer_id'] == original['producer_id'])
    assert revoked['available'] is False and revoked['status'] == 'revoked'
    assert revoked['contract'] == original['contract'] and history['adoptions'] == old['adoptions']
    request_id = value['adopt_body']['request_id']
    recovered = reply(client.get(path + '/v4/requests/' + request_id))['value']
    assert recovered['found'] and recovered['receipt']['request_id'] == request_id
    hashed = reply(client.post(path + '/v4/command-hash', json={'kind': 'root', 'command': value['adopt_body']}))['value']
    assert hashed['command_hash'] == recovered['receipt']['command_hash']
