"""Normal registration/catalog checks on Root's disposable PostgreSQL only.

No model HTTP is requested. Real authenticated registry endpoints, current SQL
rows, eligibility and gateway rejection are used without replacing validators.
This file does not prove a Task, source context, model quality or device action.
"""
import copy
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_context_required_v197 as required
from generic_agents import workflow_service_context_v196 as context_service
from generic_agents import workflow_service_v195 as legacy
from generic_agents import business_reasoning_v197 as reasoning
from generic_agents import business_flow_execution_api as catalog
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.models import Acquisition, Capability, CloudCall, Invocation, Task
from tests.test_workflow_service_registration_v195 import (
    good, publish, authority, manifest as legacy_manifest)
from tests.test_business_reasoning_v197 import local_context


OWNERSHIP_COMMENT = 'owned-disposable-workflow-context-registration-v197'


@pytest.fixture(autouse=True)
def owned_postgres_only():
    bound = engine()
    assert bound.dialect.name == 'postgresql'
    with bound.connect() as connection:
        name, comment = connection.execute(text("SELECT current_database(), "
            "shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")).one()
    assert name == bound.url.database
    assert name.startswith('ga_test_') or (
        os.environ.get('GA_WORKFLOW_CONTEXT_REGISTRATION_DB') == name and comment == OWNERSHIP_COMMENT), \
        'Only a disposable conftest DB or the explicitly owned, commented registration DB is allowed.'


def tool(module, *, version='1.0.0', operation=None):
    execution = ({'adapter': 'workflow_service', 'service_actor': 'execution_service',
        'operation': operation, 'side_effect': True} if operation else
        {'adapter': 'builtin', 'handler': module.HANDLER, 'side_effect': False})
    return Manifest(kind='tool', namespace='workflow_context_registration',
        name=('context_service' if operation else module.HANDLER) + '_' + uuid4().hex[:8],
        version=version, owner='独立登记验收账号',
        description='验证真实登记和类型合同；不认定来源真实性、业务质量或执行批准',
        allowed_agent_roles=['business' if operation else 'data'],
        execution=execution, input_schema=module.input_schema(), output_schema=module.output_schema(),
        budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json')


def model_manifest(*, consent=True):
    return Manifest(kind='foundation_model', namespace='workflow_context_registration',
        name='registered_test_model_' + uuid4().hex[:8], version='1.0.0',
        owner='独立登记验收账号', description='qwen/test-model仅登记，不请求模型HTTP或密钥值',
        allowed_agent_roles=['business'],
        execution={'provider': 'qwen', 'model': 'test-model', 'secret_ref': 'DASHSCOPE_API_KEY',
            'max_tokens': 1024, 'supports_images': True},
        input_schema={'type': 'object'}, output_schema={'type': 'object'},
        data_policy={'cloud_allowed': consent},
        budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json')


def reasoner(model_id, model_hash):
    return Manifest(kind='tool', namespace='workflow_context_registration',
        name='registered_business_reasoning_' + uuid4().hex[:8], version='1.0.0',
        owner='独立登记验收账号', description='仅登记分析合同；不进行模型调用或自动批准',
        allowed_agent_roles=['business'], input_schema=reasoning.input_schema(),
        output_schema=reasoning.output_schema(),
        execution={'adapter': reasoning.ADAPTER, 'model_profile_id': model_id,
            'model_profile_hash': model_hash, 'side_effect': False}, dependencies=[model_id],
        specification={'schema': reasoning.CONTRACT,
            'instruction': '对照本次逐项授权的证据与候选，保留资料不足和冲突，不代替人工批准。'},
        data_policy={'cloud_allowed': True, 'external_allowed': True},
        budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json')


def current_hash(capability_id):
    with Session(engine()) as db:
        row = db.get(Capability, capability_id)
        assert row.status == 'published' and row.content_hash == row.validated_hash == digest(row.data)
        return row.content_hash


def no_dispatch(workspace_id):
    with Session(engine()) as db:
        for model in (Invocation, Acquisition, CloudCall):
            assert not list(db.scalars(select(model).where(model.workspace_id == workspace_id))), model.__name__
        assert all(not task.data.get(legacy.NOTICES)
            for task in db.scalars(select(Task).where(Task.workspace_id == workspace_id)))


def rejected_validation(client, value):
    draft = good(client.post('/api/capabilities', json=value), 201)
    response = client.post('/api/capabilities/' + draft['id'] + '/validate',
        json={'expected_version': draft['version'], 'reason': '拒绝不匹配合同，不能发布或调度'})
    assert response.status_code in {409, 422}, response.text[:900]
    with Session(engine()) as db:
        actual = db.get(Capability, draft['id'])
        assert actual.status == 'draft' and actual.validated_hash != actual.content_hash
        assert actual.version == draft['version']
    return draft


def test_context_tools_model_and_reasoning_are_really_published_and_listed_with_current_pins(client, account):
    values = [tool(reader), tool(required), tool(context_service,
        version=context_service.RELEASE_VERSION, operation=context_service.OP)]
    rows = [publish(client, value) for value in values]
    model = publish(client, model_manifest())
    reasoning_value = reasoner(model['id'], current_hash(model['id']))
    values.append(reasoning_value); rows.append(publish(client, reasoning_value))
    # Old services keep their exact canonical contracts, even with the new
    # installed catalog and context operation present in the same workspace.
    old_values = [legacy_manifest(actor) for actor in ('task_center', 'execution_service')]
    old_rows = [publish(client, value) for value in old_values]
    listed = good(client.get('/api/capabilities'))
    assert {row['id'] for row in [*rows, model, *old_rows]} <= {row['id'] for row in listed}
    with Session(engine()) as db:
        principal = authority(db, account)
        members, options = catalog._nested_catalog(db, principal)
        assert any(member['user_id'] == principal.user_id for member in members)
        options = {item['id']: item for item in options}
        assert model['id'] not in options  # Models are explicit tool dependencies.
        for value, public in zip(values, rows):
            row = db.get(Capability, public['id']); option = options[row.id]
            assert row.workspace_id == account[2] and row.status == 'published'
            assert row.content_hash == row.validated_hash == digest(row.data)
            assert row.data == value and option['hash'] == row.content_hash
            assert option['eligible_agent_roles'] == value['allowed_agent_roles']
            assert option['input_schema'] == value['input_schema'] and option['output_schema'] == value['output_schema']
            assert option['release_version'] == value['version'] and option['max_attempts'] == 1
            assert not {'execution', 'endpoint', 'secret_ref', 'model_profile_id', 'model_profile_hash'} & set(option)
            if value['execution']['adapter'] == 'workflow_service':
                assert option['release_version'] == '2.0.0'
                assert option['service_actor'] == 'execution_service' and option['operation'] == context_service.OP
                assert option['requires_approved_plan'] is True and option['external'] is False
            elif value['execution']['adapter'] == reasoning.ADAPTER:
                assert option['external'] is True and option['cloud_allowed'] is True
                assert option['external_allowed'] is True and option['requires_approved_plan'] is False
                assert row.data['execution']['model_profile_hash'] == current_hash(model['id'])
            else:
                assert option['external'] is False and option['requires_approved_plan'] is False
        for value, public in zip(old_values, old_rows):
            actual = db.get(Capability, public['id'])
            operation = value['execution']['operation']
            assert actual.release_version == '1.0.0' and actual.data == value
            assert actual.data['input_schema'] == legacy.input_schema(operation)
            assert actual.data['output_schema'] == legacy.output_schema(operation)
            assert actual.content_hash == actual.validated_hash == digest(value)
    no_dispatch(account[2])


def test_direct_context_service_without_real_task_approval_cannot_create_calls_or_collection(client, account):
    value = tool(context_service, version='2.0.0', operation=context_service.OP)
    registered = publish(client, value)
    # Valid structure deliberately carries no trusted live Task. The actual
    # gateway must refuse the missing graph/175 approval before any Invocation.
    inputs = {'context': local_context()}
    with Session(engine()) as db:
        principal = authority(db, account)
        with pytest.raises(HTTPException) as rejected:
            invoke(db, principal, registered['id'], 'business', inputs,
                'unapproved-context-service-' + str(uuid4()))
        assert rejected.value.status_code == 409
        assert '批准' in str(rejected.value.detail)
    no_dispatch(account[2])


@pytest.mark.parametrize('fault', ['reader_schema', 'required_schema', 'service_operation',
    'reasoning_schema', 'model_hash', 'attempts_two', 'tool_cloud_consent'])
def test_registry_rejects_inexact_context_or_reasoning_contracts_without_effects(client, account, fault):
    if fault == 'reader_schema':
        value = tool(reader); value['output_schema']['additionalProperties'] = True
    elif fault == 'required_schema':
        value = tool(required)
        value['output_schema']['properties']['context']['type'] = ['object', 'null']
    elif fault == 'service_operation':
        value = tool(context_service, version='2.0.0', operation=context_service.OP)
        value['execution']['operation'] = 'resense_with_receipt'
    else:
        model = publish(client, model_manifest())
        value = reasoner(model['id'], current_hash(model['id']))
        if fault == 'reasoning_schema': value['output_schema']['additionalProperties'] = True
        elif fault == 'model_hash': value['execution']['model_profile_hash'] = '0' * 64
        elif fault == 'attempts_two': value['budget']['max_attempts'] = 2
        else: value['data_policy']['cloud_allowed'] = False
    rejected_validation(client, value)
    no_dispatch(account[2])


def test_unconsented_model_stays_draft_and_cannot_authorize_its_dependent_reasoner(client, account):
    model_value = model_manifest(consent=False)
    model = rejected_validation(client, model_value)
    # The valid-looking hash is only a pin, not publication or consent. Both
    # real registry gates must reject; no direct SQL publication is fabricated.
    value = reasoner(model['id'], digest(model_value))
    rejected_validation(client, value)
    no_dispatch(account[2])
