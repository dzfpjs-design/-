"""Retained bindings cannot publish or run before explicit review; no database."""
import copy
import socket
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import instance_compiler as compiler
from generic_agents import workflow_abilities as abilities


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('binding review tests forbid SQL, services, network and models')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'execute', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)


def automatic():
    return {'id': 'unchanged-auto', 'agent': '数据管理智能体', 'name': '核对原数量',
            'execution': {'capability_id': 'synthetic-capability', 'automation_action': 'data',
                          'arguments': {'field': '数量', 'operation': 'mean'},
                          'bindings': {'records': {'source': 'object_records'}}}}


def configuration(step, *, alias=False):
    return {'version': 1, 'resources': [], 'connections': [], 'materials': [],
            'instanceProfile': {'name': '合成核对', 'domain': '工程验收'},
            'businesses': ['新名称' if alias else '业务'],
            'businessAliases': {'新名称': '原名称'} if alias else {},
            'flows': {'原名称' if alias else '业务': {'steps': [step]}}}


@pytest.mark.parametrize('binding', ['execution', 'planning_tools'])
@pytest.mark.parametrize('marker', [{'reason': '职责已变化'}, {}, None, False, '损坏标记'])
def test_real_compiler_rejects_any_present_review_marker_without_touching_DB(binding, marker):
    step = automatic() if binding == 'execution' else {
        'id': 'human', 'agent': '人工确认', 'role': '已变更负责人',
        'planning_tools': [{'capability_id': 'synthetic-capability', 'allow_external': False}]}
    step['bindingReview'] = marker
    value = configuration(step, alias=True); before = copy.deepcopy(value)
    with pytest.raises(HTTPException) as error:
        compiler.compile_configuration(None, None, value, [], {})
    assert error.value.status_code == 409 and '能力绑定需要重新核对' in error.value.detail
    assert value == before


def test_actual_pin_blocks_review_before_capability_lookup(monkeypatch):
    step = automatic(); step['bindingReview'] = {'reason': '自动行为已变化'}
    def lookup(*args):
        pytest.fail('unreviewed binding must fail before publication lookup')
    monkeypatch.setattr(abilities, 'published', lookup)
    with pytest.raises(HTTPException) as error:
        abilities.pin(None, None, step, {'rules': {'data': {'mode': '自动运行'}}})
    assert error.value.status_code == 409 and '重新核对' in error.value.detail


def test_explicit_marker_removal_still_runs_real_pin_and_server_binding_validation(monkeypatch):
    step = automatic(); before = copy.deepcopy(step)
    cap = SimpleNamespace(content_hash='f' * 64, release_version='1.0.0', kind='tool', data={
        'description': '仅工程纯测的能力替身；不声称真实注册', 'allowed_agent_roles': ['data'],
        'budget': {'max_attempts': 1}, 'execution': {'adapter': 'builtin',
            'handler': 'aggregate_values', 'side_effect': False,
            'input_bindings': {'records': 'archive_records'}},
        'input_schema': {'type': 'object', 'required': ['records', 'field', 'operation'],
            'properties': {'records': {'type': 'array'}, 'field': {'const': '数量'},
                           'operation': {'const': 'mean'}}}, 'output_schema': {'type': 'object'}})
    lookups = []
    def published(db, principal, identity):
        lookups.append(identity); return cap
    monkeypatch.setattr(abilities, 'published', published)
    policy = {'rules': {'data': {'mode': '自动运行'}}}
    pinned = abilities.pin(None, None, step, policy)
    assert lookups == ['synthetic-capability']
    assert pinned['capability_hash'] == cap.content_hash
    assert pinned['release_version'] == cap.release_version
    assert pinned['bindings']['records'] == {'source': 'object_records'}
    assert step == before
    step['execution']['arguments']['records'] = [{'attributes': {'数量': 999}}]
    with pytest.raises(HTTPException) as error:
        abilities.pin(None, None, step, policy)
    assert error.value.status_code == 422  # Retained review UI never replaces the real pin.


def test_unbound_marker_is_not_execution_authority():
    step = {'id': 'human', 'agent': '人工确认', 'bindingReview': {'reason': '仅保留说明'}}
    abilities.require_binding_reviewed(step)
    with pytest.raises(HTTPException) as error:
        abilities.pin(None, None, step, {})
    assert error.value.status_code == 409 and '尚未绑定能力' in error.value.detail
