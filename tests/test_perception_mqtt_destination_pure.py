"""MQTT destination confirmation through real API/service/proof code, without SQL."""
import copy
import importlib.util
import operator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m, mqtt_api as api, mqtt_observations as mqtt
from generic_agents import mqtt_binding_routes as routes, instance_domain as domain
from generic_agents import mqtt_domain_options as options
from generic_agents.config import settings
from generic_agents.contracts import digest

_spec = importlib.util.spec_from_file_location('_mqtt_definition_fixture', Path(__file__).with_name('test_entity_release_compat_pure.py'))
_fixture = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_fixture)
forbid_runtime = _fixture.forbid_runtime


@pytest.fixture
def world(monkeypatch):
    w = _fixture.World(monkeypatch); w.p.role = 'admin'; w.instance.data = {'name': '合成实例'}
    for release in (w.r1, w.r2):
        release.data['snapshot']['businesses'] = []; w.reseal(release)
    for model in (m.MqttGateway, m.MqttBinding, m.MqttBindingRoute, m.MqttBindingRevision):
        w.db.rows[model] = {}
    key = w.r1.data['snapshot']['object_types'][0]['fields'][1]['key']
    config = {'broker': 'mqtt://127.0.0.1:1883', 'topics': {'sensor/one': 'device-one'},
        'mapping': {'value': {'attribute': key, 'unit': '株'}}, 'fields': {}, 'username_ref': '', 'password_ref': ''}
    w.gateway = NS(id='gateway-one', workspace_id=w.p.workspace_id, version=3, status='paused', data=config)
    w.db.rows[m.MqttGateway][w.gateway.id] = w.gateway
    rule_data = {'execution': {'adapter': 'builtin', 'handler': 'numeric_rules'},
        'allowed_agent_roles': ['perception'], 'data_policy': {'cloud_allowed': False, 'external_allowed': False},
        'specification': {'rules': [{'id': 'high', 'field': key, 'operator': 'gt', 'threshold': 30}]}}
    w.rule = NS(id='original-rule', workspace_id=w.p.workspace_id, namespace='local', name='原数值规则',
        kind='tool', status='published', data=rule_data, content_hash=digest(rule_data), validated_hash=digest(rule_data))
    w.db.rows[m.Capability][w.rule.id] = w.rule
    w.locks = []; w.lock_hook = None; w.commits = 0

    def scalar(query):
        model = query.column_descriptions[0]['entity']
        found = list(w.db.rows[model].values())
        for term in query._where_criteria:
            assert term.operator is operator.eq, 'pure SQL adapter refuses unsupported queries'
            found = [row for row in found if getattr(row, term.left.key, None) == term.right.value]
        if query._for_update_arg is not None:
            w.locks.append(model)
            if model is m.DomainInstance and w.lock_hook:
                hook = w.lock_hook; w.lock_hook = None; hook()
        return found[0] if found else None

    def add(row):
        table = w.db.rows[type(row)]
        if row.id is None: row.id = f'{type(row).__name__.lower()}-{len(table) + 1}'
        if row.created_at is None: row.created_at = datetime.now(timezone.utc)
        if hasattr(row, 'version') and row.version is None: row.version = 1
        if isinstance(row, m.MqttBindingRoute):
            if row.current_revision is None: row.current_revision = 1
        table[row.id] = row

    def execute(statement, params):
        assert 'pg_advisory_xact_lock' in str(statement) and set(params) == {'key'}

    def commit(): w.commits += 1
    w.db.scalar = scalar; w.db.add = add; w.db.flush = lambda: None
    w.db.execute = execute; w.db.commit = commit
    for module in (mqtt, routes, domain, options): monkeypatch.setattr(module, 'scoped', w.db.scoped)
    for module in (mqtt, options): monkeypatch.setattr(module, 'published', w.db.published)
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', config['broker'])
    return w


def body(w, *, release='release-2', request=1, version=None):
    value = {'expected_version': w.gateway.version if version is None else version, 'topic': 'sensor/one',
        'entity_id': w.entity.id, 'extractor_id': w.rule.id,
        'request_id': f'00000000-0000-4000-8000-{request:012d}', 'include_existing': False}
    if release is not None: value['instance_release_id'] = release
    return value


def create(w, raw):
    return api.confirm_route(w.gateway.id, api.RouteCreate(**raw), w.p, w.db)


def binding(w, raw):
    raw = {key: value for key, value in raw.items() if key not in {'request_id', 'include_existing'}}
    return api.confirm_binding(w.gateway.id, api.BindingCreate(**raw), w.p, w.db)


@pytest.mark.parametrize('entry', [create, binding])
@pytest.mark.parametrize('release', [None, 'release-1', 'unknown-release'])
def test_new_cross_package_binding_rejects_unmarked_or_stale_destination(world, entry, release):
    before = copy.deepcopy(vars(world.entity))
    with pytest.raises(HTTPException) as error: entry(world, body(world, release=release))
    assert error.value.status_code == 409
    assert not world.db.rows[m.MqttBindingRevision] and not world.db.rows[m.MqttBinding]
    assert world.commits == 0 and vars(world.entity) == before


@pytest.mark.parametrize('entry', [create, binding])
def test_new_explicit_binding_freezes_target_but_preserves_original_object_and_rule(world, entry):
    original = copy.deepcopy(vars(world.entity)); result = entry(world, body(world))
    model = m.MqttBindingRevision if entry is create else m.MqttBinding
    row = next(iter(world.db.rows[model].values()))
    assert row.entity_id == world.entity.id and row.extractor_id == world.rule.id
    assert row.data['package_id'] == world.r1.package_id
    assert row.data['instance'] == {'id': world.instance.id, 'release_id': world.r2.id}
    assert m.DomainInstance in world.locks and world.commits == 1
    assert vars(world.entity) == original
    assert 'object_definition' not in repr(row.data) and 'object_definition' not in repr(result)


def test_default_none_keeps_exact_old_route_body_and_fingerprint(world):
    raw = body(world, release=None); world.instance.active_release_id = world.r1.id
    old = {**raw, 'field_links': None}
    result = create(world, raw); row = next(iter(world.db.rows[m.MqttBindingRevision].values()))
    assert row.request_hash == digest({'operation': 'create', 'body': old})
    assert routes._fingerprint('create', {**old, 'instance_release_id': None}) == routes._fingerprint('create', old)
    saved = world.db.rows[m.MqttBindingRoute][result['route_id']]
    expected = digest({'gateway_id': world.gateway.id, 'request': digest({'operation': 'create', 'body': old})})
    assert saved.data['requests'][old['request_id']]['hash'] == expected
    assert row.data['instance'] == {'id': world.instance.id, 'release_id': world.r1.id}


@pytest.mark.parametrize('tagged', [False, True])
def test_saved_nonce_returns_original_binding_after_release_changes_without_new_grant(world, tagged):
    world.instance.active_release_id = world.r1.id
    raw = body(world, release=world.r1.id if tagged else None)
    result = create(world, raw)
    frozen = {model: {key: copy.deepcopy(value.data) for key, value in world.db.rows[model].items()}
        for model in (m.MqttBindingRoute, m.MqttBindingRevision, m.Capability, m.Audit)}
    world.instance.active_release_id = world.r2.id
    world.db.rows[m.Capability][world.r1.package_id].status = 'retired'
    assert create(world, raw) == result
    for model, rows in frozen.items():
        assert {key: value.data for key, value in world.db.rows[model].items()} == rows
    with pytest.raises(HTTPException): create(world, {**raw, 'instance_release_id': world.r2.id})


@pytest.mark.parametrize('release', [None, 'release-1'])
def test_existing_route_revision_requires_new_explicit_current_destination(world, release):
    world.instance.active_release_id = world.r1.id
    first = create(world, body(world, release=None)); world.instance.active_release_id = world.r2.id
    next_body = body(world, release=release, request=2, version=first['version'])
    with pytest.raises(HTTPException): api.revise_route(first['route_id'], api.RouteCreate(**next_body), world.p, world.db)
    assert len(world.db.rows[m.MqttBindingRevision]) == 1


def test_explicit_revision_and_its_replay_keep_distinct_original_authorizations(world):
    world.instance.active_release_id = world.r1.id
    first = create(world, body(world, release=None)); world.instance.active_release_id = world.r2.id
    raw = body(world, request=2, version=first['version'])
    second = api.revise_route(first['route_id'], api.RouteCreate(**raw), world.p, world.db)
    revisions = list(world.db.rows[m.MqttBindingRevision].values())
    assert [row.data['instance']['release_id'] for row in revisions] == [world.r1.id, world.r2.id]
    world.instance.status = 'paused'
    assert api.revise_route(first['route_id'], api.RouteCreate(**raw), world.p, world.db) == second
    assert len(world.db.rows[m.MqttBindingRevision]) == 2


@pytest.mark.parametrize('release', [None, 'release-1'])
def test_destination_is_rechecked_after_waiting_for_instance_lock(world, release):
    world.instance.active_release_id = world.r1.id
    raw = body(world, release=release)
    world.lock_hook = lambda: setattr(world.instance, 'active_release_id', world.r2.id)
    with pytest.raises(HTTPException): create(world, raw)
    assert m.DomainInstance in world.locks and not world.db.rows[m.MqttBindingRevision]


@pytest.mark.parametrize('change', ['source_retired', 'source_tampered', 'units', 'rule_foreign_package'])
def test_explicit_target_does_not_broaden_original_source_rule_or_units(world, change, monkeypatch):
    source = world.db.rows[m.Capability][world.r1.package_id]
    if change == 'source_retired': source.status = 'retired'
    elif change == 'source_tampered': source.data['extra'] = 'changed'
    elif change == 'units': world.gateway.data['mapping']['value']['unit'] = '吨'
    else:
        from generic_agents import local_rules_api
        monkeypatch.setattr(local_rules_api, 'require_current_check', lambda *args: None)
        world.rule.data['specification']['managed_numeric_rule'] = {'package_id': world.r2.package_id, 'entity_type': world.entity.entity_type}
        world.rule.content_hash = world.rule.validated_hash = digest(world.rule.data)
    with pytest.raises(HTTPException): create(world, body(world))
    assert not world.db.rows[m.MqttBindingRevision]


def test_object_options_provide_server_current_target_without_private_definition_proof(world):
    result = options.object_option(world.db, world.p, world.entity)
    assert result['package_id'] == world.r1.package_id
    assert result['instance']['instance_release_id'] == world.r2.id and result['instance']['current'] is True
    assert 'definition' not in repr(result)
    world.instance.status = 'paused'
    blocked = options.object_option(world.db, world.p, world.entity)
    assert blocked['instance']['current'] is False and blocked['instance']['reason']


def test_generic_legacy_binding_has_no_instance_destination(world):
    world.db.rows[m.InstanceRelease].clear()
    binding(world, body(world, release=None))
    saved = next(iter(world.db.rows[m.MqttBinding].values()))
    assert saved.data['instance'] is None
    with pytest.raises(HTTPException): binding(world, body(world))


def test_binding_still_requires_actual_admin_permission(world):
    world.p.role = 'operator'
    with pytest.raises(HTTPException) as error: create(world, body(world))
    assert error.value.status_code == 403 and not world.db.rows[m.MqttBindingRevision]


def test_legacy_single_binding_cannot_report_old_destination_as_new_explicit_target(world):
    world.instance.active_release_id = world.r1.id
    original = binding(world, body(world, release=None))
    saved = next(iter(world.db.rows[m.MqttBinding].values())); frozen = copy.deepcopy(saved.data)
    world.instance.active_release_id = world.r2.id
    with pytest.raises(HTTPException) as error: binding(world, body(world))
    assert error.value.status_code == 409 and '业务对象关联' in error.value.detail
    assert saved.data == frozen and saved.id == original['id'] and len(world.db.rows[m.MqttBinding]) == 1


def test_legacy_single_binding_same_destination_still_returns_original(world):
    world.instance.active_release_id = world.r1.id
    original = binding(world, body(world, release=None))
    assert binding(world, body(world, release=world.r1.id)) == original
    assert len(world.db.rows[m.MqttBinding]) == 1
