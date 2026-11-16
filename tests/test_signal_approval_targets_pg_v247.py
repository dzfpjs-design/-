"""Real registered V177 source and normal policy APIs; no fabricated ACK.

The supplier is the existing loopback simulator, explicitly not a field device.
Root alone executes this file on its disposable PostgreSQL environment.
"""
import copy
import json
from contextlib import ExitStack
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import signal_approval_targets_v247 as targets
from generic_agents import signal_graph_http_source_v177 as http_source
from generic_agents import signal_observation_provider_v247 as provider
from generic_agents import worker
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (Capability, DomainInstance, InstanceRelease,
                                  Job, Observation, Task)
from tests.test_acquisition_ack_settlement_pg_v245 import (
    good, mapped_scene, simulated_supplier)
from tests.test_workflow_abilities import start, tool
from tests.test_signal_approval_routing_pg_v247 import (
    scene, build_scene, collect, process_pending, ack, occurrence)
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import command as approval_command


def policy(client, release, *, enabled=True, expected_version=0):
    unit = str(uuid4())
    good(client.post('/api/workspace/approval-settings-v175/units', json={
        'request_id': str(uuid4()), 'unit_id': unit, 'expected_version': 0,
        'name': '数值异常草稿真实组织', 'enabled': True}))
    return good(client.post('/api/workspace/approval-settings-v175/policies', json={
        'request_id': str(uuid4()), 'instance_id': release['instance_id'],
        'business_id': release['available_business_ids'][0],
        'expected_version': expected_version, 'name': '先存草稿后正式分级审批',
        'scope_id': unit, 'stages': [{'name': '人工批准', 'role_key': 'approve', 'scope_id': unit}],
        'executor': {'name': '明确启动', 'role_key': 'execute', 'scope_id': unit},
        'acceptance': {'name': '独立验收', 'role_key': 'accept', 'scope_id': unit},
        'enabled': enabled}))


def target_args(db, s):
    observation = db.get(Observation, s.observation_id)
    release = db.get(InstanceRelease, s.release['id'])
    instance = db.get(DomainInstance, release.instance_id)
    source = db.get(Capability, observation.source_id)
    extractor = db.get(Capability, observation.data['extractor_id'])
    contract = http_source.prepare(db, s.p, instance, release, source, extractor,
                                   [observation.entity_id])
    return (instance, release, release.data['snapshot']['businesses'][0]['id']), {
        'source_contract': contract, 'entity_ids': [observation.entity_id]}


def test_real_source_needs_real_policy_and_snapshot_cannot_be_retyped_to_manual(client, simulated_supplier):
    s = mapped_scene(client, simulated_supplier)
    with Session(engine()) as db:
        args, kwargs = target_args(db, s)
        before = db.scalar(select(func.count()).select_from(Task))
        with pytest.raises(HTTPException) as missing:
            targets.prepare(db, s.p, *args, **kwargs)
        assert missing.value.status_code == 409 and '审批策略' in str(missing.value.detail)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Task)) == before
    configured = policy(client, s.release)
    with Session(engine()) as db:
        args, kwargs = target_args(db, s)
        before = db.scalar(select(func.count()).select_from(Job))
        pinned = targets.prepare(db, s.p, *args, **kwargs)
        assert pinned['business']['execution_mode'] == 'confirmed_human_workflow'
        assert pinned['input_provider'] == 'none' and pinned['required_inputs'] == []
        assert pinned['approval_policy']['id'] == configured['id']
        assert pinned['source_contract_hash'] == digest(kwargs['source_contract'])
        assert pinned['target_hash'] == digest({key: value for key, value in pinned.items() if key != 'target_hash'})
        assert db.scalar(select(func.count()).select_from(Job)) == before
        # A copied flow facade alone is not a publication. Preserve the real
        # package and configuration digest, then damage only the release JSON.
        release = args[1]
        damaged = copy.deepcopy(release.data)
        damaged['snapshot']['businesses'][0]['flow']['steps'][0]['name'] += '被改写'
        release.data = damaged
        with pytest.raises(HTTPException) as changed:
            targets.prepare(db, s.p, *args, **kwargs)
        assert changed.value.status_code == 409
        assert '发布快照' in str(changed.value.detail)
        db.rollback()


def test_disabled_real_policy_does_not_become_an_ordinary_unmanaged_task(client, simulated_supplier):
    s = mapped_scene(client, simulated_supplier)
    policy(client, s.release, enabled=False)
    with Session(engine()) as db:
        args, kwargs = target_args(db, s)
        before = db.scalar(select(func.count()).select_from(Task))
        with pytest.raises(HTTPException) as failure:
            targets.prepare(db, s.p, *args, **kwargs)
        assert failure.value.status_code == 409
        assert db.scalar(select(func.count()).select_from(Task)) == before


def test_actual_task_caller_cannot_create_provider_authority_with_json_marker(client):
    cap = tool(client)
    release, created, _ = start(client, cap)
    with Session(engine()) as db:
        task = db.get(Task, created['id'])
        # This is an explicitly forged marker on a real ordinary Task. No
        # occurrence, grant, reception or source proof is fabricated for it.
        task.data = {**task.data, provider.KEY: {'schema': 'signal-approval-task-origin-v247',
            'observation_id': str(uuid4()), 'origin_hash': digest('untrusted marker')}}
        db.commit()
    response = client.get('/api/workspace/tasks/' + created['id'])
    assert response.status_code == 409, response.text[:500]
    with Session(engine()) as db:
        assert db.get(Task, created['id']).status == 'workflow_queued'
        assert db.get(Task, created['id']).data[provider.KEY]['origin_hash'] == digest('untrusted marker')


@pytest.mark.parametrize('remove', [False, True])
def test_real_task_caller_and_submit_cannot_downgrade_original_occurrence_by_stripping_marker(scene, remove):
    s = collect(scene); process_pending(s); ack(s); process_pending(s)
    saved = occurrence(s); assert saved['status'] == 'approval_draft'
    path = saved['task_path']; assert good(s.client.get(path))['status'] == 'approval_draft'
    with Session(s.bound) as db:
        task = db.get(Task, saved['task_id']); changed = copy.deepcopy(task.data)
        if remove: changed.pop(provider.KEY)
        else: changed[provider.KEY]['receipt_hash'] = digest('different ACK')
        task.data = changed; db.commit()
    result = s.client.get(path)
    assert result.status_code == 409, result.text[:500]
    source = good(s.client.get(path + '/signal-approval-source'))
    assert source['found'] is True and source['source'] is None and source['integrity_issue']
    with Session(s.bound) as db: version = db.get(Task, saved['task_id']).version
    response = s.client.post(path + '/approval-v175/submit', json={
        'request_id': str(uuid4()), 'expected_version': version, 'reason': '原来源损坏不得提交'})
    assert response.status_code == 409, response.text[:500]
    with Session(s.bound) as db:
        task = db.get(Task, saved['task_id'])
        assert task.status == 'approval_draft'
        assert not db.scalar(select(Job.id).where(Job.data['task_id'].as_string() == task.id))


def compact_configuration(client, cfg):
    """Public normal registration/flow example; no historic qualification."""
    from generic_agents.instance_compiler import identifier, signature_value
    cap = tool(client, data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})
    name = '异常数值核验'
    flow = copy.deepcopy(cfg['flows']['资料核验'])
    flow['steps'][0].update(name='核算原收件温度', agent='数据管理智能体', role='', condition='',
        execution={'capability_id': cap['id'], 'automation_action': 'data',
            'arguments': {'field': identifier('field', '温度'), 'operation': 'mean'},
            'bindings': {'records': {'source': 'observation_records'}}})
    cfg['businesses'].append(name); cfg['flows'][name] = flow
    cfg['automation']['policies'][name] = {'rules': {'data': {'mode': '自动运行'}}}
    flow['confirmedSignature'] = json.dumps(signature_value(cfg, flow), ensure_ascii=False)
    return cfg


def test_real_registered_compact_execution_reads_received_value_not_same_named_archive(client, simulated_supplier):
    with ExitStack() as stack:
        s = build_scene(client, simulated_supplier, stack,
            configuration_transform=compact_configuration, route_business_name='异常数值核验')
        original = good(client.get('/api/instances/' + s.release['instance_id'] + '/objects'))
        entity = next(row for row in original if row['id'] == s.entity['id'])
        # A real normal archive edit is deliberately distinct from the later
        # simulator measurement. No receipt, source result or Task is seeded.
        good(client.patch('/api/entities/' + entity['id'], json={
            'expected_version': entity['version'], 'reason': '档案登记值与本次观测明确区分',
            'attributes': {**entity['attributes'], s.temperature: 999}}))
        collect(s); process_pending(s)
        assert occurrence(s)['status'] == 'waiting_data'
        ack(s); process_pending(s)
        saved = occurrence(s); assert saved['status'] == 'approval_draft'
        path = saved['task_path']; approval = path + '/approval-v175'
        task = good(client.get(path))
        assert task['object_snapshots'][0]['attributes'][s.temperature] == 999
        source = good(client.get(path + '/signal-approval-source'))
        assert next(row['value'] for row in source['source']['fields'] if row['key'] == s.temperature) == 25
        approval_command(client, approval, 'submit')
        submission = good(client.get(approval))['submission_hash']
        for _, reviewer in s.reviewers:
            approval_command(reviewer, approval, 'decide', submission_hash=submission, decision='approve')
        with Session(s.bound) as db:
            assert not db.scalar(select(Job.id).where(Job.data['task_id'].as_string() == saved['task_id']))
        approval_command(client, approval, 'start')
        current = good(client.get(path)); job_id = current['workflow_runtime']['automatic']['job_id']
        assert worker.process_once(job_id, kinds=['execute_workflow_step'], workspace_id=s.workspace, deliver=False)
        completed = good(client.get(path)); receipt = completed['workflow_runtime']['receipts'][0]
        assert receipt['output']['value'] == 25 and receipt['output']['sample_count'] == 1
        record = receipt['input_snapshot']['records'][0]
        assert record['schema'] == provider.RECORD and record['attributes'][s.temperature] == 25
        assert record['receipt_hash'] == source['source']['receipt_hash']
        assert record['field_units'][s.temperature] == '°C'
        assert record['version'] == record['processing_version'] == 'http-mapped-v177'
        assert record['physical_state_verified'] is record['source_time_verified'] is False
        objects = good(client.get('/api/instances/' + s.release['instance_id'] + '/objects'))
        assert next(row for row in objects if row['id'] == s.entity['id'])['attributes'][s.temperature] == 999
