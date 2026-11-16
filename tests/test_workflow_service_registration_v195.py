"""Normal authenticated registration on Root's disposable PostgreSQL fixture.

Registry validation, publication, gateway and service code are never mocked.
This proves registration/catalog eligibility and rejection of an unapproved
direct invocation, not adoption, a real Task, notification delivery or sensing.
Root must run this file with the ordinary disposable conftest, or explicitly
name its owned DB and set its exact ownership comment before collection.
"""
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import workflow_service_v195 as service
from generic_agents import business_flow_execution_api as catalog
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.models import Acquisition, Capability, Invocation, Membership, Task, User
from generic_agents.security import Principal


OWNERSHIP_COMMENT = 'owned-disposable-workflow-service-registration-v195'


@pytest.fixture(autouse=True)
def owned_postgres_only():
    bound = engine()
    assert bound.dialect.name == 'postgresql'
    with bound.connect() as connection:
        name, comment = connection.execute(text("SELECT current_database(), "
            "shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()" )).one()
    assert name == bound.url.database
    assert name.startswith('ga_test_') or (
        os.environ.get('GA_WORKFLOW_SERVICE_REGISTRATION_DB') == name and comment == OWNERSHIP_COMMENT), \
        'Only the disposable conftest DB or an explicitly owned, commented test DB may run this file.'


def manifest(actor='task_center'):
    operation = service.adoption.compiler.SERVICE_OPERATIONS[actor]
    return Manifest(kind='tool', namespace='workflow_services', name=operation + '_' + uuid4().hex[:8],
        version='1.0.0', owner='独立工程联调账号', description='仅验证真实服务登记及禁止无批准调用，不认定业务质量',
        allowed_agent_roles=['business'], input_schema=service.input_schema(operation),
        output_schema=service.output_schema(operation),
        execution={'adapter': 'workflow_service', 'service_actor': actor, 'operation': operation, 'side_effect': True},
        budget={'max_attempts': 1}).model_dump(mode='json')


def good(response, status=200):
    assert response.status_code == status, response.text[:900]
    return response.json()


def publish(client, value):
    draft = good(client.post('/api/capabilities', json=value), 201)
    validated = good(client.post('/api/capabilities/' + draft['id'] + '/validate',
        json={'expected_version': draft['version'], 'reason': '核对实际服务处理器的严格输入输出合同'}))
    published = good(client.post('/api/capabilities/' + draft['id'] + '/publish',
        json={'expected_version': validated['version'], 'reason': '允许后续明确采用的流程核对此登记能力'}))
    return published


def authority(db, account):
    user = db.scalar(select(User).where(User.username == account[0]))
    member = db.scalar(select(Membership).where(Membership.user_id == user.id,
        Membership.workspace_id == account[2]))
    assert user.active and member.role == 'admin'
    return Principal(user.id, account[2], member.role, 'owned-registration-asgi-test')


def no_effects(workspace_id):
    with Session(engine()) as db:
        assert not list(db.scalars(select(Invocation).where(Invocation.workspace_id == workspace_id)))
        assert not list(db.scalars(select(Acquisition).where(Acquisition.workspace_id == workspace_id)))
        tasks = list(db.scalars(select(Task).where(Task.workspace_id == workspace_id)))
        assert all(not row.data.get(service.NOTICES) for row in tasks)


@pytest.mark.parametrize('actor', ['task_center', 'execution_service'])
def test_normal_register_validate_publish_persists_current_service_pins_and_catalog_eligibility(client, account, actor):
    value = manifest(actor); row = publish(client, value)
    assert row['status'] == 'published'
    listed = good(client.get('/api/capabilities'))
    assert any(item['id'] == row['id'] and item['status'] == 'published' for item in listed)
    with Session(engine()) as db:
        actual = db.get(Capability, row['id'])
        assert actual.workspace_id == account[2] and actual.kind == 'tool' and actual.status == 'published'
        assert actual.content_hash == actual.validated_hash == digest(actual.data)
        assert actual.data['execution'] == value['execution']
        assert actual.data['input_schema'] == service.input_schema(value['execution']['operation'])
        assert actual.data['output_schema'] == service.output_schema(value['execution']['operation'])
        principal = authority(db, account)
        # This is the actual workflow catalog builder over the same current PG
        # rows. No fake source/qualification is created to open its context API.
        members, choices = catalog._nested_catalog(db, principal)
        choice = next(item for item in choices if item['id'] == actual.id)
        assert any(item['user_id'] == principal.user_id for item in members)
        assert choice['eligible_agent_roles'] == ['business']
        assert choice['service_actor'] == actor and choice['operation'] == value['execution']['operation']
        assert choice['hash'] == actual.content_hash and choice['requires_approved_plan'] is True
        assert 'execution' not in choice and 'endpoint' not in choice and 'secret_ref' not in choice
    no_effects(account[2])


@pytest.mark.parametrize('fault', ['output_missing_receipt', 'input_extra', 'input_wide', 'numeric_side_effect',
    'wrong_operation', 'wrong_actor'])
def test_normal_registry_rejects_uncallable_or_unapproved_service_contracts(client, account, fault):
    value = manifest()
    if fault == 'output_missing_receipt': value['output_schema']['required'].remove('service_receipt')
    elif fault == 'input_extra': value['input_schema']['properties']['execute_now'] = {'type': 'boolean'}
    elif fault == 'input_wide': value['input_schema']['additionalProperties'] = True
    elif fault == 'numeric_side_effect': value['execution']['side_effect'] = 1
    elif fault == 'wrong_operation': value['execution']['operation'] = 'resense_with_receipt'
    else: value['execution']['service_actor'] = 'business_agent'
    created = good(client.post('/api/capabilities', json=value), 201)
    rejected = client.post('/api/capabilities/' + created['id'] + '/validate',
        json={'expected_version': created['version'], 'reason': '负控必须拒绝不匹配的真实处理器合同'})
    assert rejected.status_code == 422, rejected.text[:900]
    with Session(engine()) as db:
        row = db.get(Capability, created['id'])
        assert row.status != 'published' and row.validated_hash != row.content_hash
    no_effects(account[2])


@pytest.mark.parametrize('actor', ['task_center', 'execution_service'])
def test_direct_invocation_without_real_graph_approval_has_zero_calls_acquisitions_and_notices(client, account, actor):
    value = manifest(actor); cap = publish(client, value)
    with Session(engine()) as db:
        principal = authority(db, account)
        inputs = ({'recipient_ids': [principal.user_id], 'message': '未取得图执行批准，不能投递这条通知'}
            if actor == 'task_center' else {'policy_id': str(uuid4()), 'gap_id': str(uuid4())})
        # There is no public invoke endpoint. This tests the actual internal
        # gateway with current SQL rows and no fabricated graph/approval, not an
        # HTTP 404/DTO rejection or a mocked authority decision.
        with pytest.raises(HTTPException) as error:
            invoke(db, principal, cap['id'], 'business', inputs, 'unapproved-service-' + str(uuid4()))
        assert error.value.status_code == 409
    no_effects(account[2])
