"""Actual registry contracts only; no gateway transport, DB, model or hardware.

Root executes this source in an isolated harness without repository conftest.
Manifest and the production schema validator are used without replacements.
Endpoint registration/allowlisting and a real approved Plan remain separate gates.
"""
import copy

from fastapi import HTTPException
import pytest

from generic_agents.contracts import Manifest
from generic_agents.device_gateway_catalog_v175 import manifests
from generic_agents.services import validate


BASE = 'http://127.0.0.1:19575'
SECRET_REF = 'GA_CONNECTOR_SECRET_V175_EQUIPMENT'


def catalog():
    rows = [Manifest.model_validate(value) for value in manifests(BASE, SECRET_REF)]
    assert len(rows) == 2
    return {row.name: row for row in rows}


def completed():
    return {'simulation': True, 'origin_kind': 'simulated_equipment',
            'field_verified': False, 'request_id': 'original-device-command',
            'device_id': 'sim-camera-01', 'action': 'set_ptz', 'status': 'succeeded'}


def rejected(schema, value):
    with pytest.raises(HTTPException) as error:
        validate(schema, value)
    assert error.value.status_code == 422


def test_registry_contains_actual_valid_manifests_and_no_credential_literal():
    rows = catalog()
    assert set(rows) == {'read_equipment', 'control_equipment'}
    for row in rows.values():
        assert row.kind == 'tool'
        assert row.execution['adapter'] == 'http'
        assert row.execution['endpoint'] == BASE + '/invoke'
        assert row.execution['secret_ref'] == SECRET_REF
        assert not {'authorization', 'password', 'api_key', 'token'} & row.execution.keys()
        assert row.data_policy['simulation'] is True
        assert row.data_policy['physical_device_connected'] is False
        assert row.data_policy['field_verified'] is False
        assert row.budget == {'max_attempts': 1, 'max_concurrency': 1}
        validate(row.output_schema, completed())


@pytest.mark.parametrize('name', ['read_equipment', 'control_equipment'])
def test_all_four_actual_gateway_inputs_are_required_and_closed(name):
    row = catalog()[name]
    body = {'device_id': 'sim-camera-01',
            'action': 'read_telemetry' if name == 'read_equipment' else 'set_ptz',
            'expected_revision': None if name == 'read_equipment' else 1,
            'arguments': {} if name == 'read_equipment' else {'pan_degrees': -0.0}}
    assert set(row.input_schema['required']) == set(body)
    validate(row.input_schema, body)
    for key in body:
        missing = {k: copy.deepcopy(value) for k, value in body.items() if k != key}
        rejected(row.input_schema, missing)
    rejected(row.input_schema, {**body, 'simulation': True})
    for bad in [True, '1', 0]:
        rejected(row.input_schema, {**body, 'expected_revision': bad})


@pytest.mark.parametrize('status', ['accepted', 'running', 'failed', 'cancelled', 'unknown'])
def test_http_success_with_nonterminal_or_failed_body_cannot_pass_control_output(status):
    row = catalog()['control_equipment']
    rejected(row.output_schema, {**completed(), 'status': status})


@pytest.mark.parametrize('key,bad', [
    ('simulation', False), ('simulation', 1), ('origin_kind', 'field_equipment'),
    ('field_verified', True), ('field_verified', 0),
])
def test_completion_requires_typed_simulation_markers_not_physical_claims(key, bad):
    schema = catalog()['control_equipment'].output_schema
    rejected(schema, {**completed(), key: bad})
    rejected(schema, {k: value for k, value in completed().items() if k != key})


def test_read_and_control_permissions_and_receipt_routes_are_separate():
    rows = catalog()
    read, control = rows['read_equipment'], rows['control_equipment']
    assert read.execution['side_effect'] is False
    assert 'receipt_endpoint' not in read.execution
    assert 'approval_policy' not in read.execution
    assert 'data' in read.allowed_agent_roles
    assert control.execution['side_effect'] is True
    assert control.execution['approval_policy'] == 'admin'
    assert control.execution['receipt_endpoint'] == BASE + '/platform-receipts'
    assert control.execution['result_semantics'] == 'completed_simulated_action_only'
    assert 'data' not in control.allowed_agent_roles
    read_actions = set(read.input_schema['properties']['action']['enum'])
    control_actions = set(control.input_schema['properties']['action']['enum'])
    assert read_actions == {'get_status', 'read_telemetry'}
    assert control_actions == {'start_mission', 'return_to_dock', 'cancel_mission', 'set_ptz'}
    assert read_actions.isdisjoint(control_actions)
    rejected(read.input_schema, {'device_id': 'sim-dock-01', 'action': 'start_mission',
                                'expected_revision': 1, 'arguments': {}})


@pytest.mark.parametrize('base', [
    'https://127.0.0.1:19575', 'http://localhost:19575', 'http://0.0.0.0:19575',
    'http://example.org:19575', 'http://127.0.0.1', 'http://127.0.0.1:0',
    BASE + '/', BASE + '/invoke', BASE + '?token=credential', BASE + '#fragment',
    'http://user:password@127.0.0.1:19575',
])
def test_catalog_rejects_remote_or_embedded_credentials_or_nonbase_endpoint(base):
    with pytest.raises(ValueError):
        manifests(base, SECRET_REF)


@pytest.mark.parametrize('secret', ['', 'literal-bearer-value', 'DASHSCOPE_API_KEY',
                                    'GA_CONNECTOR_SECRET_', 'GA_CONNECTOR_SECRET_X/Y'])
def test_catalog_requires_a_server_reference_not_a_credential_or_other_provider(secret):
    with pytest.raises(ValueError):
        manifests(BASE, secret)
