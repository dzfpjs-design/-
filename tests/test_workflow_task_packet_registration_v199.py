"""Actual authenticated registry/router and internal gateway fence on owned PG.

No registry, gateway, handler, SQL, source validator or authority is mocked.
This proves registration and rejection without a real graph approval, not a
completed Task, generated packet, cloud consent, provider call or useful model.
Root runs it with the ordinary disposable conftest/owned registration wrapper.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import business_flow_execution_api as catalog
from generic_agents import workflow_task_packet_v199 as packet
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.models import Acquisition, Capability, CloudCall, Evidence, Invocation
from generic_agents.workflow_abilities import eligible
from generic_agents.workflow_task_packet_models_v199 import (
    WorkflowTaskEvidencePacket, WorkflowTaskEvidencePacketCommand)
from tests.test_workflow_persistent_operations_registration_v198 import good, publish, authority
from tests.test_workflow_service_registration_v195 import owned_postgres_only


BASE = '/api/task-evidence-packets'


def manifest():
    return Manifest(kind='tool', namespace='task_packet_registration_v199',
        name='current_task_packet_' + uuid4().hex[:10], version='1.0.0',
        owner='独立工程验收账号', description='登记本任务本地证据资料包，不代表事实确认或云端授权',
        allowed_agent_roles=['data'], input_schema=packet.input_schema(),
        output_schema=packet.output_schema(), execution={'adapter': packet.ADAPTER, 'side_effect': True},
        specification={'schema': 'workflow-task-evidence-packet-contract-v199',
            'operation': packet.OP, 'candidate_only': True}, dependencies=[],
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1, 'max_concurrency': 1}
        ).model_dump(mode='json')


def effect_counts(db, workspace_id):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(
        model.workspace_id == workspace_id)) for model in (Invocation, CloudCall, Acquisition,
            Evidence, WorkflowTaskEvidencePacket, WorkflowTaskEvidencePacketCommand)}


def test_real_packet_registration_pins_and_current_195_catalog_require_explicit_family_eligibility(client, account):
    value = manifest(); result = publish(client, value)
    assert result['status'] == 'published'
    listed = good(client.get('/api/capabilities'))
    assert any(row['id'] == result['id'] and row['status'] == 'published' for row in listed)
    # Independent Session reads what HTTP committed; no in-memory registry row
    # or fake capability is substituted for publication/current-pin checks.
    with Session(engine()) as db:
        cap = db.get(Capability, result['id']); p = authority(db, account)
        assert cap.workspace_id == p.workspace_id and cap.kind == 'tool' and cap.status == 'published'
        assert cap.content_hash == cap.validated_hash == digest(cap.data)
        assert cap.data['execution'] == {'adapter': packet.ADAPTER, 'side_effect': True}
        assert cap.data['allowed_agent_roles'] == ['data'] and cap.data['dependencies'] == []
        assert cap.data['budget']['max_attempts'] == 1 and cap.data['data_policy']['cloud_allowed'] is False
        assert cap.data['input_schema'] == packet.input_schema()
        assert cap.data['output_schema'] == packet.output_schema()
        packet.validate_manifest(cap.data)
        with pytest.raises(HTTPException) as legacy:
            eligible(cap, 'data')
        assert legacy.value.status_code == 409
        with pytest.raises(HTTPException): eligible(cap, 'data', allow_task_archive=True)
        with pytest.raises(HTTPException): eligible(cap, 'business', allow_task_packet=True)
        eligible(cap, 'data', allow_task_packet=True)
        members, choices = catalog._nested_catalog(db, p)
        assert any(row['user_id'] == p.user_id for row in members)
        choice = next(row for row in choices if row['id'] == cap.id)
        assert choice['hash'] == cap.content_hash and choice['release_version'] == cap.release_version
        assert choice['adapter'] == packet.ADAPTER and choice['eligible_agent_roles'] == ['data']
        assert choice['requires_approved_plan'] is True and choice['cloud_allowed'] is False
        assert choice['input_schema'] == cap.data['input_schema']
        assert choice['output_schema'] == cap.data['output_schema'] and choice['max_attempts'] == 1
        assert all(count == 0 for count in effect_counts(db, p.workspace_id).values())


@pytest.mark.parametrize('fault', ['schema_drift', 'side_effect_false', 'side_effect_missing',
    'side_effect_numeric', 'wrong_role', 'cloud_allowed', 'specification_fake'])
def test_real_registry_rejects_packet_contract_drift_before_publication(client, account, fault):
    value = manifest()
    if fault == 'schema_drift':
        value['output_schema']['properties']['packet']['properties']['cloud_authorized']['const'] = True
    elif fault == 'side_effect_false': value['execution']['side_effect'] = False
    elif fault == 'side_effect_missing': value['execution'].pop('side_effect')
    elif fault == 'side_effect_numeric': value['execution']['side_effect'] = 1
    elif fault == 'wrong_role': value['allowed_agent_roles'] = ['business']
    elif fault == 'cloud_allowed': value['data_policy']['cloud_allowed'] = True
    else: value['specification']['candidate_only'] = False
    draft = good(client.post('/api/capabilities', json=value), 201)
    rejected = client.post('/api/capabilities/' + draft['id'] + '/validate',
        json={'expected_version': draft['version'], 'reason': '必须拒绝偏离本地候选资料包合同的能力'})
    assert rejected.status_code in {409, 422}, rejected.text[:900]
    with Session(engine()) as db:
        cap = db.get(Capability, draft['id'])
        assert cap.status != 'published' and cap.validated_hash != cap.content_hash
        assert all(count == 0 for count in effect_counts(db, account[2]).values())


def test_actual_internal_gateway_without_graph_approval_creates_no_call_evidence_or_acquisition(client, account):
    row = publish(client, manifest())
    with Session(engine()) as db:
        p = authority(db, account); before = effect_counts(db, p.workspace_id)
        assert all(count == 0 for count in before.values())
        # No public /invoke endpoint exists. Exercise the real internal fence
        # with a current published cap and actual authenticated DB membership.
        with pytest.raises(HTTPException) as error:
            invoke(db, p, row['id'], 'data', {'policy_pin': None}, 'unapproved-packet-' + uuid4().hex)
        assert error.value.status_code == 409
        assert effect_counts(db, p.workspace_id) == before
    with Session(engine()) as independent:
        assert all(count == 0 for count in effect_counts(independent, account[2]).values())


def test_normal_packet_router_has_exact_absolute_paths_and_strict_query_shapes(client, account):
    paths = client.app.openapi()['paths']
    expected = {BASE, BASE + '/requests/original', BASE + '/{packet_id}'}
    assert expected <= set(paths)
    actual = {path for path in paths if '/task-evidence-packets' in path}
    assert actual == expected, 'the actual app must not double-mount a workspace/api prefix'
    assert all(set(paths[path]) == {'get'} for path in expected)
    unknown = str(uuid4()); sha = 'a' * 64
    invalid = [(BASE, None), (BASE, [('task_id', unknown), ('task_id', unknown)]),
        (BASE, {'task_id': unknown, 'extra': 'not-declared'}),
        (BASE + '/requests/original', {'request_key': 'never-created'}),
        (BASE + '/requests/original', [('request_key', 'never-created'),
            ('request_key', 'other'), ('expected_sha256', sha)]),
        (BASE + '/' + unknown, {'extra': 'not-declared'})]
    for path, params in invalid:
        rejected = client.get(path, params=params)
        assert rejected.status_code == 422, (path, rejected.status_code, rejected.text[:400])
    key = 'never-created-packet-' + uuid4().hex
    original = good(client.get(BASE + '/requests/original',
        params={'request_key': key, 'expected_sha256': sha}))
    assert original == {'schema': packet.ORIGINAL, 'request_key': key,
        'found': False, 'body_sha256': None, 'result': None}
    with Session(engine()) as db:
        assert all(count == 0 for count in effect_counts(db, account[2]).values())
