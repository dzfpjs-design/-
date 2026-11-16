"""Real registered SDK/PG/workflow receipt status, not acoustic quality proof.

sound_scene runs actual pinned CPU classification and independent SDK reception
over synthetic silence. Only the execute observer below wraps and calls the real
reader; it never fabricates a successful output. The four-step flow is an
explicit engineering configuration, not a replacement for a machine-authored
business. Numeric rules describe administrative receipt gaps only.
"""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.instance_compiler import signature_value
from generic_agents.security import Principal, password_hash
from generic_agents.services import register, validate_capability, publish as publish_capability
from generic_agents import business_tools, workflow_abilities as abilities
from generic_agents import workflow_sound_receipt_status_v261 as status_tool
from generic_agents.worker import process_once, deliver_events
from tests.test_vertical_slice import publish
from tests.test_workflow_sound_native_pg import sound_scene, original_scene, ack, good
from tests.test_instance_workspace import configuration, prepared, object_row
from tests.test_workflow_abilities import enable


def principal(s):
    me = good(s.client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'receipt-sdk-test')


def numeric_manifest():
    status = status_tool.output_schema()
    return Manifest(kind='tool', namespace='sound_receipt_local_review', name='receipt_administration',
        version='1.0.0', owner='工程核对岗位', description='仅核查已登记声音Run的行政收件缺口，不判断声学异常或现场事实',
        allowed_agent_roles=['business'], execution={'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        input_schema={'type': 'object', 'required': ['attributes'], 'additionalProperties': False,
            'properties': {'attributes': copy.deepcopy(status)}},
        output_schema={'type': 'object', 'required': ['attributes', 'anomalies', 'method'], 'additionalProperties': False,
            'properties': {'attributes': copy.deepcopy(status), 'anomalies': {'type': 'array', 'items': {'type': 'object',
                'required': ['field', 'value', 'rule_id', 'severity'], 'additionalProperties': False,
                'properties': {'field': {'type': 'string'}, 'value': {'type': 'number'},
                    'rule_id': {'type': 'string'}, 'severity': {'type': 'string'}}}},
                'method': {'const': 'registered_numeric_rules'}}},
        specification={'rules': [
            {'id': 'no_registered_run', 'field': 'registered_count', 'operator': 'lt', 'threshold': 1, 'severity': 'review'},
            {'id': 'waiting_receipt', 'field': 'waiting_count', 'operator': 'gt', 'threshold': 0, 'severity': 'review'},
            {'id': 'blocked_receipt', 'field': 'blocked_count', 'operator': 'gt', 'threshold': 0, 'severity': 'review'}]},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})


def sdk(p, capability, inputs, key, *, db=None):
    if db is not None:
        call = invoke(db, p, capability['id'], 'data', inputs, key)
        return call.id, call.status, copy.deepcopy(call.data)
    with Session(engine()) as writer:
        return sdk(p, capability, inputs, key, db=writer)


def test_real_gateway_uses_distinct_physically_readonly_rr_session_and_freezes_sdk_output(sound_scene, monkeypatch):
    s = sound_scene; p = principal(s)
    capability = publish(s.client, status_tool.manifest().model_dump(mode='json'))
    original = status_tool.execute; readers = []; writer_session = None

    def observed(db, actor, task_id):
        assert db is not writer_session
        assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        assert db.bind is not writer_session.bind
        # Exercise the underlying PostgreSQL restriction, not just SHOW text.
        with pytest.raises(DBAPIError) as failure:
            with db.begin_nested():
                db.execute(text('UPDATE tasks SET version=version WHERE id=:id'), {'id': task_id})
        assert getattr(failure.value.orig, 'sqlstate', getattr(failure.value.orig, 'pgcode', None)) == '25006'
        result = original(db, actor, task_id)
        readers.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr(status_tool, 'execute', observed)
    key = 'receipt-sdk:' + str(uuid4())
    with Session(engine()) as writer:
        writer_session = writer
        identity, state, stored = sdk(p, capability, {'task_id': s.task['id']}, key, db=writer)
        assert state == 'succeeded' and stored['output']['waiting_count'] == 1
        assert writer.scalar(text('SHOW transaction_read_only')) == 'off'
    frozen = copy.deepcopy(stored['output'])
    assert len(readers) == 1 and frozen == readers[0]
    assert frozen['snapshot_hash'] == digest({key: item for key, item in frozen.items() if key != 'snapshot_hash'})
    ack(s)  # The real independent receiver changes only subsequent reads.
    with Session(engine()) as writer:
        writer_session = writer
        replay = sdk(p, capability, {'task_id': s.task['id']}, key, db=writer)
        assert replay[0] == identity and replay[2]['output'] == frozen and len(readers) == 1
        fresh = sdk(p, capability, {'task_id': s.task['id']}, 'receipt-sdk:' + str(uuid4()), db=writer)
        assert fresh[1] == 'succeeded' and fresh[2]['output']['received_count'] == 1
        assert fresh[2]['output']['all_registered_received'] is True
    with Session(engine()) as db:
        assert db.get(m.Invocation, identity).data['output'] == frozen
    assert frozen['received_count'] == 0 and len(readers) == 2


def configured_flow(s):
    receipt_cap = publish(s.client, status_tool.manifest().model_dump(mode='json'))
    rules_cap = publish(s.client, numeric_manifest().model_dump(mode='json'))
    c = configuration(); flow = c['flows']['资料核验']
    first = copy.deepcopy(flow['steps'][0]); human = copy.deepcopy(flow['steps'][-1])
    classification_receipt = next(value for value in s.task['workflow_runtime']['receipts']
        if value.get('output', {}).get('sound_run_id') == s.run['id'])
    first.update(name='合成原音候选分类', agent='多模感知智能体', role='', nextId='sound_receipts',
        execution={'capability_id': classification_receipt['capability_id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    data = copy.deepcopy(first)
    data.update(id='sound_receipts', name='已登记声音Run收件核查', agent='数据管理智能体', nextId='receipt_business',
        execution={'capability_id': receipt_cap['id'], 'automation_action': 'data', 'arguments': {}, 'bindings': {}})
    business = copy.deepcopy(first)
    business.update(id='receipt_business', name='声音成果行政收件待核查判断', agent='业务智能体', nextId=human['id'],
        execution={'capability_id': rules_cap['id'], 'automation_action': 'state', 'arguments': {},
            'bindings': {'attributes': {'source': 'previous_output_object', 'step_id': data['id']}}})
    flow['steps'] = [first, data, business, human]
    for action in ('observe', 'data', 'state'):
        c['automation']['policies']['资料核验']['rules'][action] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, check = prepared(s.client, c, instance_id=s.instance['id'])
    assert release['available_business_ids'], check
    enable(s.client, release)
    return SimpleNamespace(release=release, data=data, business=business, human=human)


def before_business(s, meta, *, received=True):
    release = meta.release; entity = object_row(s.client, release)
    task = good(s.client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'sound_ids': [s.attached['id']],
        'title': '真实只读SDK与完整结果绑定工程验收', 'request_id': str(uuid4())}), 201)
    assert process_once(task['workflow_runtime']['automatic']['job_id'], deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    entry = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/sounds'))['records'][0]
    assert entry['reception']['state'] == 'waiting_data'
    if received:
        deliver_events(workspace_id=entry['run']['sound_snapshot']['workspace_id'],
            observation_id=entry['reception']['observation_id'], event_kind='observation.data')
    assert process_once(task['workflow_runtime']['automatic']['job_id'], deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    assert task['status'] == 'workflow_queued', task['workflow_runtime'].get('automatic')
    data_receipt = next(value for value in task['workflow_runtime']['receipts'] if value['step_id'] == meta.data['id'])
    output = copy.deepcopy(data_receipt['output'])
    return SimpleNamespace(task=task, data_receipt=data_receipt, output=output, entity=entity)


def test_real_task_classification_ack_data_status_whole_output_and_local_business_rule_workflow(sound_scene):
    s = sound_scene; p = principal(s); meta = configured_flow(s); scene = before_business(s, meta)
    task = scene.task; data_receipt = scene.data_receipt; output = scene.output; entity = scene.entity
    assert output['task_id'] == task['id'] and output['registered_count'] == output['received_count'] == 1
    assert output['all_registered_received'] is True and output['expected_sources_verified'] is False
    assert process_once(task['workflow_runtime']['automatic']['job_id'], deliver=False)
    task = good(s.client.get('/api/workspace/tasks/' + task['id']))
    result = next(value for value in task['workflow_runtime']['receipts'] if value['step_id'] == meta.business['id'])
    assert result['input_snapshot']['attributes'] == output
    assert result['output'] == {'attributes': output, 'anomalies': [], 'method': 'registered_numeric_rules'}
    assert task['status'] == 'workflow_waiting' and task['workflow_runtime']['current_step_id'] == meta.human['id']
    with Session(engine()) as db:
        assert db.get(m.Invocation, data_receipt['invocation_id']).data['output'] == output
        assert db.get(m.Invocation, result['invocation_id']).data['role'] == 'business'
        assert list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == p.workspace_id))) == []
        actual = db.get(m.Entity, entity['id'])
        assert actual.version == entity['version'] and actual.data == entity['attributes']
        actual_task = db.get(m.Task, task['id'])
        detached = abilities.bind(actual_task, meta.business, db, p, None)
        assert detached['attributes'] == output
        detached['attributes']['runs'][0]['memory']['id'] = str(uuid4())
        assert next(value for value in actual_task.data['workflow_runtime']['receipts']
            if value['step_id'] == meta.data['id'])['output'] == output
        assert db.get(m.Invocation, data_receipt['invocation_id']).data['output'] == output


@pytest.mark.parametrize('damage', ['output_only', 'foreign_task_receipt', 'missing_sdk', 'missing_receipt_field',
    'missing_completed_audit', 'input_snapshot', 'job_scope', 'unsupported_execution',
    'fake_running', 'foreign_lease'])
def test_whole_result_requires_same_task_real_job_and_successful_sdk_not_only_task_json(sound_scene, damage):
    s = sound_scene; p = principal(s); meta = configured_flow(s)
    scene = before_business(s, meta, received=False)
    assert scene.output['waiting_count'] == 1 and scene.output['received_count'] == 0
    other = before_business(s, meta, received=False) if damage == 'foreign_task_receipt' else None
    with Session(engine()) as db:
        task = db.get(m.Task, scene.task['id']); data = copy.deepcopy(task.data)
        receipt = next(value for value in data['workflow_runtime']['receipts'] if value['step_id'] == meta.data['id'])
        if damage == 'output_only':
            receipt['output']['received_count'] = 1; receipt['output']['waiting_count'] = 0
            receipt['output']['all_registered_received'] = True
            receipt['output']['snapshot_hash'] = digest({key: value for key, value in receipt['output'].items()
                if key != 'snapshot_hash'})  # Even self-consistent JSON is not the actual SDK result.
        elif damage == 'foreign_task_receipt':
            position = next(i for i, value in enumerate(data['workflow_runtime']['receipts']) if value['step_id'] == meta.data['id'])
            data['workflow_runtime']['receipts'][position] = copy.deepcopy(other.data_receipt)
        elif damage == 'missing_sdk':
            db.delete(db.get(m.Invocation, receipt['invocation_id']))
        elif damage == 'missing_receipt_field':
            del receipt['invocation_id']
        elif damage == 'missing_completed_audit':
            records = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.object_id == task.id, m.Audit.action == 'workflow.ability_completed',
                m.Audit.data['invocation'].as_string() == receipt['invocation_id'])))
            assert len(records) == 1; db.delete(records[0])
        elif damage == 'input_snapshot':
            receipt['input_snapshot']['task_id'] = str(uuid4())
        elif damage == 'job_scope':
            job = db.get(m.Job, receipt['request_id']); changed = copy.deepcopy(job.data)
            changed['scope']['step_id'] = 'not_the_original_step'; job.data = changed
        elif damage == 'unsupported_execution':
            receipt['execution_mode'] = 'manual'
        else:
            job = db.get(m.Job, receipt['request_id'])
            job.status = 'running'; job.lease_token = str(uuid4()); job.lease_until = m.now() + timedelta(minutes=5)
        task.data = data; db.commit()  # Explicit disposable integrity counterexample, never accepted evidence.
    with Session(engine()) as db:
        if damage == 'foreign_lease':
            db.info['worker_lease'] = {'job_id': s.job_id, 'lease_token': str(uuid4())}
        task = db.get(m.Task, scene.task['id'])
        with pytest.raises(HTTPException) as failure:
            abilities.bind(task, meta.business, db, p, None)
        assert failure.value.status_code == 409
    business_job = scene.task['workflow_runtime']['automatic']['job_id']
    assert process_once(business_job, deliver=False)
    with Session(engine()) as db:
        job = db.get(m.Job, business_job)
        assert job.status != 'succeeded'
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == p.workspace_id,
            m.Invocation.idempotency_key == job.data['invocation_key'])) is None
        if damage != 'missing_sdk':
            assert db.get(m.Invocation, scene.data_receipt['invocation_id']).data['output'] == scene.output


@pytest.mark.parametrize('damage', ['role', 'handler', 'binding', 'external'])
def test_registration_validation_cannot_broaden_the_fixed_receipt_manifest(sound_scene, damage):
    s = sound_scene; p = principal(s); value = status_tool.manifest().model_dump(mode='json')
    if damage == 'role': value['allowed_agent_roles'] = ['business']
    elif damage == 'handler': value['execution']['handler'] = 'numeric_rules'
    elif damage == 'binding': value['execution']['input_bindings']['task_id'] = 'object_ids'
    else: value['data_policy']['external_allowed'] = True
    with Session(engine()) as db:
        row = register(db, p, Manifest.model_validate(value))
        with pytest.raises(HTTPException) as failure:
            validate_capability(db, p, row.id, row.version)
        assert failure.value.status_code == 422


def test_task_identifier_alias_remains_server_owned_and_pin_rejects_override(sound_scene):
    s = sound_scene; p = principal(s)
    capability = publish(s.client, status_tool.manifest().model_dump(mode='json'))
    step = {'id': 'receipt_test', 'agent': '数据管理智能体', 'condition': '',
        'execution': {'capability_id': capability['id'], 'automation_action': 'data', 'arguments': {}, 'bindings': {}}}
    policy = {'rules': {'data': {'mode': '自动运行'}}}
    with Session(engine()) as db:
        pinned = abilities.pin(db, p, copy.deepcopy(step), policy)
        assert pinned['bindings'] == {'task_id': {'source': 'task_identifier'}}
        for override in ('arguments', 'bindings'):
            bad = copy.deepcopy(step)
            bad['execution'][override]['task_id'] = str(uuid4()) if override == 'arguments' else {'source': 'object_ids'}
            with pytest.raises(HTTPException) as failure:
                abilities.pin(db, p, bad, policy)
            assert failure.value.status_code == 422
    # This is a binding unit check only; the real workflow test above proves
    # persistence and actual task ownership through the registered SDK.
    cap = SimpleNamespace(data=status_tool.manifest().model_dump(mode='json'))
    task = SimpleNamespace(id=s.task['id'], data={})
    assert business_tools.bind_inputs(cap, {}, task, {'evidence': []}, 'actual-key') == {'task_id': task.id}
    with pytest.raises(HTTPException):
        business_tools.bind_inputs(cap, {'task_id': str(uuid4())}, task, {'evidence': []}, 'actual-key')


def test_gateway_missing_task_fails_instead_of_fabricating_empty_success_and_wrong_role_is_forbidden(sound_scene):
    s = sound_scene; p = principal(s)
    capability = publish(s.client, status_tool.manifest().model_dump(mode='json'))
    _, state, data = sdk(p, capability, {'task_id': str(uuid4())}, 'missing-receipt-task:' + str(uuid4()))
    assert state != 'succeeded' and 'output' not in data
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as failure:
            invoke(db, p, capability['id'], 'business', {'task_id': s.task['id']}, 'wrong-role:' + str(uuid4()))
        assert failure.value.status_code == 403


def test_gateway_cannot_read_an_actual_task_from_another_workspace(sound_scene):
    s = sound_scene
    with Session(engine()) as db:
        user = m.User(username='receipt_scope_' + uuid4().hex[:16], password_hash=password_hash(uuid4().hex))
        workspace = m.Workspace(name='声音收件核查独立隔离工作区')
        db.add_all([user, workspace]); db.flush()
        db.add(m.Membership(user_id=user.id, workspace_id=workspace.id, role='admin')); db.flush()
        p = Principal(user.id, workspace.id, 'admin', 'actual-receipt-scope-user')
        row = register(db, p, status_tool.manifest())
        validate_capability(db, p, row.id, row.version)
        publish_capability(db, p, row.id, row.version, '独立工作区实际只读工具契约')
        capability = {'id': row.id}
        original = db.get(m.Task, s.task['id']); original_pin = (original.version, digest(original.data))
        db.commit()
    _, state, data = sdk(p, capability, {'task_id': s.task['id']}, 'foreign-receipt-task:' + str(uuid4()))
    assert state != 'succeeded' and 'output' not in data
    with Session(engine()) as db:
        original = db.get(m.Task, s.task['id'])
        assert (original.version, digest(original.data)) == original_pin


@pytest.mark.parametrize('binding', [
    {'source': 'previous_output_object'},
    {'source': 'previous_output_object', 'step_id': True},
    {'source': 'previous_output_object', 'step_id': ''},
    {'source': 'previous_output_object', 'step_id': 'source', 'field': 'attributes'},
    {'source': 'previous_output_object', 'step_id': 'source', 'value': {'registered_count': 99}}])
def test_whole_object_binding_is_closed_not_a_field_or_client_value(binding):
    # Explicit synthetic binding unit input, never a forged accepted receipt in PG.
    task = SimpleNamespace(data={'object_snapshots': [], 'workflow_runtime': {
        'receipts': [{'step_id': 'source', 'output': {'registered_count': 1}}]}})
    step = {'execution': {'arguments': {}, 'bindings': {'attributes': binding}}}
    with pytest.raises(HTTPException) as failure:
        abilities.bind(task, step, None, None, None)
    assert failure.value.status_code == 422


def test_synthetic_object_without_sdk_is_not_provenance_and_legacy_previous_output_keeps_field_semantics():
    original = {'count': 1, 'nested': {'references': ['original']}}
    task = SimpleNamespace(data={'object_snapshots': [], 'workflow_runtime': {'receipts': [
        {'step_id': 'source', 'output': original}]}})
    step = {'execution': {'arguments': {}, 'bindings': {'attributes': {
        'source': 'previous_output_object', 'step_id': 'source'}}}}
    with pytest.raises(HTTPException) as failure:
        abilities.bind(task, step, None, None, None)
    assert failure.value.status_code == 409
    step['execution']['bindings']['attributes'] = {'source': 'previous_output', 'step_id': 'source', 'field': 'nested'}
    assert abilities.bind(task, step, None, None, None)['attributes'] == original['nested']
    step['execution']['bindings']['attributes']['field'] = '$output'
    with pytest.raises(HTTPException) as failure:
        abilities.bind(task, step, None, None, None)
    assert failure.value.status_code == 409


@pytest.mark.parametrize('receipts', [[], [{'step_id': 'other', 'output': {}}],
    [{'step_id': 'source', 'output': []}], [{'step_id': 'source', 'output': 1}],
    [{'step_id': 'source'}]])
def test_absent_or_nonobject_prior_result_is_not_fabricated(receipts):
    task = SimpleNamespace(data={'object_snapshots': [], 'workflow_runtime': {'receipts': receipts}})
    step = {'execution': {'arguments': {}, 'bindings': {'attributes': {
        'source': 'previous_output_object', 'step_id': 'source'}}}}
    with pytest.raises(HTTPException) as failure:
        abilities.bind(task, step, None, None, None)
    assert failure.value.status_code == 409
