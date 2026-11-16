"""Real Gateway/registration entry guards over explicit published-capability IO.

No actual publication, grant, worker, PG, SDK, provider, data read or execution
is claimed here. Every post-publication SQL/write would explode. A prerequisite
grant alone does not authorize the formally installed execution path.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import business_tools, gateway
from generic_agents import same_goal_continuation_grant_contracts_v313 as c
from generic_agents.contracts import digest
from generic_agents.models import Invocation
from generic_agents.security import Principal


def uid(value): return str(UUID(int=value))


BLOCKED = '办理的实际批准、租约、调用或结果原件不完整，未发送或替换调用'


class NoSDKDatabase:
    """The real early guard must precede ALL of these Session operations."""
    def __init__(self): self.touches = [];self.previous = None
    def _forbid(self, name):
        self.touches.append(name)
        pytest.fail('the continuation prerequisite guard must precede database/SDK work: ' + name)
    def scalar(self, *args, **kwargs): return self._forbid('scalar/previous SDK')
    def scalars(self, *args, **kwargs): return self._forbid('scalars')
    def execute(self, *args, **kwargs): return self._forbid('execute')
    def add(self, *args, **kwargs): return self._forbid('add/SDK')
    def flush(self, *args, **kwargs): return self._forbid('flush')
    def commit(self, *args, **kwargs): return self._forbid('commit')
    def rollback(self, *args, **kwargs): return self._forbid('rollback')
    def __getattr__(self, name): return self._forbid(name)


@pytest.fixture
def scene(monkeypatch):
    p = Principal(uid(1), uid(2), 'operator', 'explicit-published-capability-IO')
    db = NoSDKDatabase()
    v = NS(p=p, db=db, published=[], cap=None)
    def install(manifest):
        v.cap = NS(id=uid(3), workspace_id=p.workspace_id, kind='tool', status='published',
            release_version='1.0.0', data=copy.deepcopy(manifest), content_hash=digest(manifest),
            validated_hash=digest(manifest))
        return v.cap
    def published(actual_db, principal, identity, *args, **kwargs):
        assert actual_db is db and principal is p and identity == v.cap.id
        v.published.append(identity)
        return v.cap  # Explicit registry/current-publication IO, never a grant.
    monkeypatch.setattr(gateway, 'published', published)
    def no_scoped(*args, **kwargs): pytest.fail('ordinary invocation must reject before scoped capability/SDK lookup')
    monkeypatch.setattr(gateway, 'scoped', no_scoped)
    v.install = install
    v.inputs = {'field': 'temperature', 'operation': 'mean', 'records': [
        {'entity_id': uid(4), 'version': 1, 'attributes': {'temperature': 21}}]}
    v.invoke = lambda approval=None, key='goal-continuation-public-attempt', role='data': gateway.invoke(
        db, p, v.cap.id, role, copy.deepcopy(v.inputs), key, approval=approval)
    return v


@pytest.mark.parametrize('binding', c.BINDINGS)
def test_exact_registered_read_manifest_still_requires_typed_leased_execution(scene, binding):
    v = scene;manifest = c.manifest(binding);v.install(manifest)
    assert c.applies(manifest) and c.validate_manifest(manifest) == manifest
    before = copy.deepcopy(v.cap.data)
    with pytest.raises(HTTPException) as caught: v.invoke()
    assert caught.value.status_code == 409 and caught.value.detail == BLOCKED
    assert v.published == [v.cap.id] and v.db.touches == [] and v.cap.data == before


@pytest.mark.parametrize(('key', 'approval'), [
    ('native-goal-query:original', {'native_step_execution': True, 'approved': True}),
    ('workflow-graph:original:node', {'schema': 'workflow-service-approval-v195', 'approved': True}),
    ('registered-tool:original', {'schema': 'workflow-registered-tool-child-approval-v201', 'approved': True}),
    ('native-task-query:original', {'schema': 'goal-evaluation-append-approval-v297', 'approved': True}),
    ('goal-continuation:original', {'schema': 'goal-continuation-approval-v313',
        'grant_id': uid(5), 'grant_version': 1, 'grant_hash': 'a' * 64, 'execution_approved': True}),
])
def test_approval_or_graph_service_query_markers_cannot_substitute_for_a_real_installed_worker(scene, key, approval):
    v = scene;v.install(c.manifest())
    with pytest.raises(HTTPException) as caught: v.invoke(approval, key)
    assert caught.value.status_code == 409 and caught.value.detail == BLOCKED
    assert v.db.touches == [] and v.published == [v.cap.id]


def test_a_prior_succeeded_sdk_cannot_escape_the_prerequisite_gate_or_return_its_saved_output(scene):
    v = scene;v.install(c.manifest());key = 'same-original-key'
    v.db.previous = Invocation(id=uid(6), workspace_id=v.p.workspace_id, capability_id=v.cap.id,
        idempotency_key=key, status='succeeded', data={'input_hash': digest(v.inputs),
            'output': {'value': 21, 'execution_approved': True}, 'role': 'data'})
    prior = copy.deepcopy(v.db.previous.data)
    with pytest.raises(HTTPException) as caught: v.invoke(key=key)
    assert caught.value.status_code == 409 and caught.value.detail == BLOCKED
    assert v.db.touches == [] and v.db.previous.status == 'succeeded' and v.db.previous.data == prior


@pytest.mark.parametrize('remaining', ['scope', 'specification', 'namespace', *sorted(c.NAMES)])
def test_stripping_other_markers_does_not_downgrade_a_reserved_facet_into_an_ordinary_tool(scene, remaining):
    manifest = c.manifest();manifest['namespace'] = 'ordinary_fixture';manifest['name'] = 'ordinary_fixture'
    manifest['execution'].pop('dispatch_scope');manifest['specification'] = {}
    if remaining == 'scope': manifest['execution']['dispatch_scope'] = c.SCOPE
    elif remaining == 'specification': manifest['specification']['schema'] = c.MANIFEST_SCHEMA
    elif remaining == 'namespace': manifest['namespace'] = c.NAMESPACE
    else: manifest['name'] = remaining
    assert c.applies(manifest);v = scene;v.install(manifest)
    with pytest.raises(HTTPException) as caught: v.invoke({'approved': True})
    assert caught.value.status_code in {409, 422} and v.db.touches == []


@pytest.mark.parametrize(('field', 'value'), [
    ('adapter', 'http'), ('adapter', 'mcp'), ('adapter', 'workflow_service'),
    ('adapter', 'same_goal_advice_v302'), ('handler', 'workflow_task_quality_archive_v220'),
    ('query_tool', True), ('dispatch_scope', 'registered_tool_plan_v201'),
])
def test_retyping_the_adapter_or_legacy_dispatch_keeps_the_reserved_namespace_and_specification_guard(scene, field, value):
    manifest = c.manifest();manifest['execution'][field] = value;assert c.applies(manifest)
    v = scene;v.install(manifest)
    with pytest.raises(HTTPException) as caught: v.invoke({'native_step_execution': True})
    assert caught.value.status_code in {409, 422} and v.db.touches == [] and v.published == [v.cap.id]


@pytest.mark.parametrize('bindings', [None, [], ['records'], 'observation_records'])
def test_malformed_reserved_binding_is_a_controlled_rejection_before_any_sdk(scene, bindings):
    manifest = c.manifest();manifest['execution']['input_bindings'] = bindings
    v = scene;v.install(manifest)
    with pytest.raises(HTTPException) as caught: v.invoke()
    assert caught.value.status_code in {409, 422} and v.db.touches == []


def test_facade_checks_the_reserved_facet_before_the_old_query_tool_branch(monkeypatch):
    from generic_agents import data_tools
    manifest = c.manifest();manifest['execution']['query_tool'] = True;old = []
    def old_validator(*args, **kwargs): old.append(True);pytest.fail('legacy query validator must not precede the reserved facet')
    monkeypatch.setattr(data_tools, 'validate_manifest', old_validator)
    with pytest.raises(HTTPException) as caught: business_tools.validate_bindings(manifest)
    assert caught.value.status_code in {409, 422} and old == []


def test_pure_facet_validator_never_recurses_through_the_generic_binding_facade(monkeypatch):
    calls = []
    def facade(*args, **kwargs): calls.append(True);pytest.fail('pure reserved-facet validation must not call the facade')
    monkeypatch.setattr(business_tools, 'validate_bindings', facade)
    for binding in c.BINDINGS:
        manifest = c.manifest(binding)
        assert c.validate_manifest(manifest) == manifest
    assert calls == []


def test_plain_unmarked_generic_tool_keeps_its_existing_binding_validation(monkeypatch):
    manifest = c.manifest();manifest['namespace'] = 'ordinary_local_fixture';manifest['name'] = 'ordinary_numeric_reader'
    manifest['execution'].pop('dispatch_scope');manifest['specification'] = {}
    assert not c.applies(manifest)
    def reserved(*args, **kwargs): pytest.fail('an ordinary unmarked tool must not enter the reserved validator')
    monkeypatch.setattr(c, 'validate_manifest', reserved)
    assert business_tools.validate_bindings(manifest) is None
