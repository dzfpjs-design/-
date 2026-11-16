"""Approved graph role snapshots survive blocked setup checks, without IO.

The public synthetic V195 parser/compiler and final runtime plan verifier stay
real. This is not a PostgreSQL, source authority, registration or enabled Task
proof. Root separately exercises the normal missing-role HTTP regression.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import instance_compiler as compiler
from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import business_flow_nested_adoption_v195 as nested
from tests.test_business_flow_nested_adoption_v195 import fixture, identity


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('graph role tests forbid database, network, subprocess and model execution')
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


def scene():
    binding=fixture()
    roles={role:actor['user_id'] for role,actor in binding['role_bindings'].items()}
    members={actor['user_id']:NS(username='当前账号名称可能已更新')
        for actor in binding['role_bindings'].values()}
    return binding,roles,members


def verify_frozen_runtime(binding,roles,issues):
    # The same strict role comparison which previously raised the 500 remains
    # enabled. A blocked check must be representable without forging a new user.
    name=binding['business_name']
    origin=nested.origin(identity('role-check-origin'),binding,'d'*64)
    business={'id':identity('role-check-business'),'name':name,'flow':copy.deepcopy(binding['flow']),
        'issues':copy.deepcopy(issues),'available':not issues,
        'execution_mode':'blocked' if issues else 'confirmed_graph_workflow',
        runtime.BUSINESS_MARKER:origin}
    plan=runtime.plan_value([business],[{'origin':origin,'binding':binding}],None,roles)
    snapshot={'businesses':[business],'roles':roles,'semantic_runtime_plan':plan}
    assert runtime.verify_plan(snapshot)==plan
    return business


@pytest.mark.parametrize('fault',['missing','replacement','revoked','missing_and_revoked'])
def test_invalid_requested_roles_keep_approved_snapshot_and_real_runtime_marks_business_blocked(fault):
    binding,requested,members=scene();role=next(iter(requested));actor=binding['role_bindings'][role]
    if fault in {'missing','missing_and_revoked'}:requested.pop(role)
    if fault=='replacement':
        replacement=identity('unapproved-replacement')
        requested[role]=replacement;members[replacement]=NS(username='仍不得替换原批准人')
    if fault in {'revoked','missing_and_revoked'}:members.pop(actor['user_id'])
    before=copy.deepcopy(binding);supplied=copy.deepcopy(requested)
    snapshots,issues=compiler._graph_role_snapshots({binding['business_name']:binding},requested,members)
    assert snapshots[role]=={key:actor[key] for key in ('user_id','username')}
    assert issues[binding['business_name']] and all(role in issue for issue in issues[binding['business_name']])
    if fault in {'revoked','missing_and_revoked'}:
        assert any('失去' in issue for issue in issues[binding['business_name']])
    if fault in {'missing','replacement','missing_and_revoked'}:
        assert any('未替换' in issue for issue in issues[binding['business_name']])
    business=verify_frozen_runtime(binding,snapshots,issues[binding['business_name']])
    assert business['available'] is False and business['execution_mode']=='blocked'
    assert requested==supplied and binding==before


def test_correct_current_member_keeps_original_username_and_runtime_has_no_role_blocker():
    binding,requested,members=scene();before=copy.deepcopy(binding)
    snapshots,issues=compiler._graph_role_snapshots({binding['business_name']:binding},requested,members)
    assert issues=={binding['business_name']:[]}
    assert snapshots=={role:{key:actor[key] for key in ('user_id','username')}
        for role,actor in binding['role_bindings'].items()}
    assert verify_frozen_runtime(binding,snapshots,[])['available'] is True
    snapshots[next(iter(snapshots))]['username']='本地调用方更改'
    assert binding==before, 'returned snapshots must not mutate the issued adoption'


@pytest.mark.parametrize('field',['user_id','username'])
def test_incompatible_approved_same_role_snapshots_are_explicit_409_not_synthetic_unification(field):
    binding,requested,members=scene();other=copy.deepcopy(binding)
    role=next(iter(requested));other['business_name']='另一份已批准业务'
    other['role_bindings'][role][field]=identity('conflicting-user') if field=='user_id' else '另一原批准名称'
    before=copy.deepcopy((binding,other,requested))
    with pytest.raises(HTTPException) as error:
        compiler._graph_role_snapshots({binding['business_name']:binding,other['business_name']:other},requested,members)
    assert error.value.status_code==409
    assert role in error.value.detail and '同名岗位' in error.value.detail
    assert (binding,other,requested)==before


def test_compatible_shared_graph_role_is_not_duplicated_or_assigned_from_request():
    binding,requested,members=scene();other=copy.deepcopy(binding)
    other['business_name']='同一真实负责人负责另一业务'
    snapshots,issues=compiler._graph_role_snapshots(
        {binding['business_name']:binding,other['business_name']:other},requested,members)
    assert len(snapshots)==len(binding['role_bindings'])
    assert all(not value for value in issues.values())


def test_later_legacy_role_cannot_replace_graph_snapshot_but_unrelated_linear_roles_stay_unchanged():
    binding,requested,members=scene()
    snapshots,_issues=compiler._graph_role_snapshots({binding['business_name']:binding},requested,members)
    role=next(iter(snapshots));linear={'普通线性岗位':{'user_id':identity('linear-user'),'username':'普通负责人'}}
    before=copy.deepcopy(linear)
    compiler._retain_graph_role_snapshots(linear,{})
    assert linear==before
    compiler._retain_graph_role_snapshots(linear,snapshots)
    assert linear['普通线性岗位']==before['普通线性岗位']
    linear[role]={'user_id':identity('legacy-overwrite'),'username':'请求替换人'}
    rejected=copy.deepcopy(linear)
    with pytest.raises(HTTPException) as error:compiler._retain_graph_role_snapshots(linear,snapshots)
    assert error.value.status_code==409 and '同名岗位' in error.value.detail
    assert linear==rejected, 'a conflict must not silently grant the original or replacement actor'
