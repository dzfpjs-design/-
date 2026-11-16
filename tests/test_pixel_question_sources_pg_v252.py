"""Native V250 sources for V252 capture/history. Root alone runs this suite.

The query-row helper below is an explicit source-storage fixture, not a fake
cloud/SDK invocation or completed V252 product query. Producers, artifacts,
independent ACK and human work use the normal native/API fixture paths.
"""
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import pixel_question_sources_v252 as subject
from generic_agents import pixel_question_source_contract_v252 as contract
from generic_agents import native_pixel_provenance_v250 as origin
from generic_agents.pixel_question_service_v252 import source_audit_expected
from generic_agents.worker import process_once
from tests.test_workflow_pixel_native_pg_v250 import (flow_scene, native_scene, pytestmark, ack, good, build_flow_scene)
from tests.test_instance_workspace import complete, object_row
from tests.test_pixel_change_pg_v249 import pause
from tests.test_workflow_abilities import run as run_ability


def capture(s, **scope):
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        value = subject.capture(db, s.p, s.instance_id, **scope)
        assert not db.new and not db.dirty and not db.deleted
        return value


def source_fixture(s, value):
    """Explicit persisted source fixture; does not claim planner authorization."""
    with Session(engine()) as db:
        context = {'instance_id': s.instance_id, 'authorization_release_id': s.release['id'],
            'authorization_configuration_hash': s.release['configuration_hash'],
            'tool_id': s.registered['capability']['id'], 'tool_hash': s.registered['capability']['hash']}
        row = m.DataRun(workspace_id=s.workspace_id, owner_id=s.p.user_id,
            question='source fixture only, not a paid/model query', status='running', data={})
        db.add(row); db.flush()
        state = {'context': context, 'context_hash': digest(context), 'creation_hash': digest(['fixture', row.id]),
            'plan_hash': digest(['fixture-plan', row.id]), 'source_job_id': s.job_id,
            'source_snapshot': copy.deepcopy(value), 'source_audit_id': None}
        proof = m.Audit(workspace_id=s.workspace_id, actor_id=s.p.user_id, action=subject.SOURCE_ACTION,
            object_id=row.id, data=source_audit_expected(row, state))
        db.add(proof); db.flush()
        state['source_audit_id'] = proof.id
        row.data = {'family': 'pixel_question_v252', 'pixel_question_v252': state}
        identity = row.id; db.commit()
        return identity


def verify(s, query_id, value):
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        row = db.get(m.DataRun, query_id)
        subject.verify_saved_snapshot(db, s.p, row, value)
        assert not db.new and not db.dirty and not db.deleted


def quantities(s):
    with Session(engine()) as db:
        return {cls.__name__: sorted((r.id, digest(r.data)) for r in db.scalars(
            select(cls).where(cls.workspace_id == s.workspace_id)))
            for cls in (m.WorkflowPixelRun, m.Invocation, m.Observation, m.Event, m.Memory, m.Audit)}


def check_409(call):
    with pytest.raises(HTTPException) as failure: call()
    assert failure.value.status_code == 409


def test_real_pending_then_ack_then_human_progress_preserves_original_snapshot(flow_scene):
    s = flow_scene
    pending = capture(s, task_id=s.task_id, entity_id=s.entity['id'])
    row = pending['records'][0]
    assert row['reception']['state'] == 'waiting_data' and row['metrics'] is None and row['artifacts'] is None
    query_id = source_fixture(s, pending)
    before = quantities(s)
    verify(s, query_id, pending)
    assert quantities(s) == before
    ack(s)
    verify(s, query_id, pending)  # newly present real Memory is a legal later ACK
    received = capture(s, task_id=s.task_id, entity_id=s.entity['id'])
    assert received['records'][0]['reception']['state'] == 'received'
    assert received['records'][0]['metrics'] is not None
    received_id = source_fixture(s, received)
    task = good(s.client.get('/api/workspace/tasks/' + s.task_id))
    good(complete(s.client, task, note='后补人工核查；原问题中的未完成状态应保留'))
    verify(s, query_id, pending)
    verify(s, received_id, received)
    current = capture(s, task_id=s.task_id, entity_id=s.entity['id'])
    human = current['records'][0]['human_receipts'][0]
    assert 'note' not in human and 'output' not in human and human['note_sha256']
    assert pending['records'][0]['human_receipts'] == received['records'][0]['human_receipts'] == []
    assert pending['records'][0]['metrics'] is None
    before = quantities(s)
    ack(s); verify(s, query_id, pending); verify(s, received_id, received)
    assert quantities(s) == before


def test_later_real_run_does_not_invalidate_an_asof_answer_or_restart_old_inference(flow_scene):
    s = flow_scene; ack(s)
    old = capture(s)
    query_id = source_fixture(s, old)
    task = good(s.client.post(s.base + '/tasks', json={**s.body, 'request_id': str(uuid4()),
        'title': '来源快照之后新增的正常真实任务'}), 201)
    job = task['workflow_runtime']['automatic']['job_id']
    assert process_once(job_id=job, deliver=False, workspace_id=s.workspace_id)
    fresh = capture(s)
    assert len(fresh['records']) == 2 and len(old['records']) == 1
    # Two actual model producers exercise the SQL limit+1 boundary without
    # inventing a thousand successful Runs or accepting a one-row subtotal.
    check_409(lambda: capture(s, limit=1))
    before = quantities(s)
    verify(s, query_id, old)
    assert quantities(s) == before
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        check_409(lambda: subject.verify_current_snapshot(db, s.p, old))
    assert quantities(s) == before  # explicit diagnostic did not refresh old query


@pytest.mark.parametrize('fault', ['memory_deleted', 'memory_data', 'event_clock', 'issued_marker',
    'input_audit', 'input_duplicate', 'source_audit', 'result_audit', 'run_scope', 'packet'])
def test_received_source_faults_cannot_be_rehashed_into_a_historical_answer(flow_scene, fault):
    s = flow_scene; observation_id = ack(s)
    value = capture(s); query_id = source_fixture(s, value)
    with Session(engine()) as db:
        observation = db.get(m.Observation, observation_id)
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + observation.id))
        run = db.get(m.WorkflowPixelRun, s.run_id)
        if fault.startswith('memory'):
            memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
                m.Memory.data['event_id'].as_string() == event.id, m.Memory.data['type'].as_string() == 'observation_evidence'))
            if fault == 'memory_deleted': db.delete(memory)
            else: memory.data = {**memory.data, 'candidate_only': False}
        elif fault == 'event_clock': event.received_at = None
        elif fault == 'issued_marker':
            body = dict(observation.data); body.pop(origin.ISSUED); observation.data = body
        elif fault == 'input_audit': db.delete(db.get(m.Audit, run.data['input_audit_id']))
        elif fault == 'input_duplicate':
            old = db.get(m.Audit, run.data['input_audit_id'])
            db.add(m.Audit(workspace_id=old.workspace_id, actor_id=old.actor_id, action=old.action,
                object_id=old.object_id, data=copy.deepcopy(old.data)))
        elif fault == 'result_audit': db.delete(db.get(m.Audit, run.data['result_audit_id']))
        elif fault == 'source_audit':
            db.delete(db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                m.Audit.action == origin.SOURCE_ACTION, m.Audit.object_id == observation.source_id)))
        elif fault == 'run_scope': run.data = {**run.data, 'instance_id': str(uuid4())}
        elif fault == 'packet':
            packet = {**observation.data[origin.PACKET], 'entity_id': str(uuid4())}
            observation.data = {**observation.data, origin.PACKET: packet, 'native_pixel_packet_hash_v250': digest(packet)}
        db.commit()
    before = quantities(s)
    check_409(lambda: verify(s, query_id, value))
    check_409(lambda: capture(s))
    assert quantities(s) == before


def test_bad_original_probability_blocks_history_without_running_model_or_repairing(flow_scene):
    s = flow_scene; ack(s)
    value = capture(s); query_id = source_fixture(s, value)
    with Session(engine()) as db:
        run = db.get(m.WorkflowPixelRun, s.run_id)
        ref = next(r for r in run.data['result']['artifacts'] if r['kind'] == 'probability_data')
        evidence = db.get(m.Evidence, ref['evidence_id'])
        path = settings().storage_dir / evidence.storage_key
    original = path.read_bytes()
    try:
        path.write_bytes(b'declared post-success physical-integrity fault')
        before = quantities(s)
        check_409(lambda: verify(s, query_id, value))
        assert quantities(s) == before
    finally:
        path.write_bytes(original)
    verify(s, query_id, value)


def test_frozen_human_summary_has_real_dual_audits_and_rejects_original_deletion(flow_scene):
    s = flow_scene; ack(s)
    task = good(s.client.get('/api/workspace/tasks/' + s.task_id))
    good(complete(s.client, task, note='这份原人工记录必须留存，问答仅存摘要'))
    value = capture(s); query_id = source_fixture(s, value)
    human = value['records'][0]['human_receipts'][0]
    verify(s, query_id, value)
    with Session(engine()) as db:
        db.delete(db.get(m.Audit, human['completion_audit']['id'])); db.commit()
    check_409(lambda: verify(s, query_id, value))


def test_independent_source_audit_marker_and_source_scope_are_not_caller_signed_authority(flow_scene):
    s = flow_scene; ack(s)
    value = capture(s); query_id = source_fixture(s, value)
    with Session(engine()) as db:
        row = db.get(m.DataRun, query_id); state = row.data['pixel_question_v252']
        proof_id = state['source_audit_id']
        db.delete(db.get(m.Audit, proof_id)); db.commit()
    check_409(lambda: verify(s, query_id, value))
    # Merely constructing an ORM-looking namespace is not a persisted query.
    with Session(engine()) as db:
        fake = SimpleNamespace(id=query_id, owner_id=s.p.user_id, workspace_id=s.workspace_id)
        check_409(lambda: subject.verify_saved_snapshot(db, s.p, fake, value))


def test_source_audit_numeric_one_cannot_replace_true_even_in_standalone_helper(flow_scene):
    s = flow_scene; ack(s)
    value = capture(s); query_id = source_fixture(s, value)
    with Session(engine()) as db:
        row = db.get(m.DataRun, query_id)
        proof = db.get(m.Audit, row.data['pixel_question_v252']['source_audit_id'])
        proof.data = {**proof.data, 'complete': 1}; db.commit()
    check_409(lambda: verify(s, query_id, value))


def test_scope_intersection_rr_requirement_and_paused_historical_sources(flow_scene):
    s = flow_scene; ack(s)
    value = capture(s, task_id=s.task_id, entity_id=s.entity['id'])
    query_id = source_fixture(s, value)
    with Session(engine()) as db:
        check_409(lambda: subject.capture(db, s.p, s.instance_id))  # ordinary read-committed is not the capture contract
    other = object_row(s.client, s.release)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        check_409(lambda: subject.capture(db, s.p, s.instance_id, task_id=s.task_id, entity_id=other['id']))
    pause(s)
    verify(s, query_id, value)
    assert capture(s, task_id=s.task_id, entity_id=s.entity['id'])['records'][0]['metrics'] == value['records'][0]['metrics']


def test_reverse_input_inventory_cannot_hide_deleted_marker_by_moving_current_task_scope(flow_scene):
    s = flow_scene; ack(s)
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id)
        # No FK relaxation: corrupt only mutable JSON; independent original
        # input proof still pins this real instance and must make capture 409.
        data = dict(task.data); data['instance_id'] = str(uuid4()); data.pop('pixel_pair_snapshot')
        task.data = data; db.commit()
    before = quantities(s)
    check_409(lambda: capture(s))
    assert quantities(s) == before


def test_original_failed_attempt_then_true_new_invocation_ack_does_not_duplicate_or_destroy_history(client, account, native_scene, monkeypatch):
    from generic_agents import workflow_pixel_runs_v250 as runtime
    def stop_before_step_receipt(*args, **kwargs):
        raise HTTPException(409, '明确夹具故障：模型结果保存后、步骤完成前停止')
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'local_result', stop_before_step_receipt)
        s = build_flow_scene(client, account, native_scene, expected_status=None)
    failed = capture(s)
    assert len(failed['records']) == 1 and failed['records'][0]['reception']['state'] == 'producer_failed'
    assert failed['records'][0]['metrics'] is None and failed['records'][0]['timing']['registration_day'] is None
    query_id = source_fixture(s, failed)
    task = good(s.client.get('/api/workspace/tasks/' + s.task_id))
    retried = good(run_ability(s.client, task, retry=True))
    job_id = retried['workflow_runtime']['automatic']['job_id']
    assert job_id != s.job_id and process_once(job_id=job_id, deliver=False, workspace_id=s.workspace_id)
    entries = good(s.client.get('/api/workspace/tasks/' + s.task_id + '/pixel-results'))['records']
    fresh_run = next(e['run'] for e in entries if e['run']['job_id'] == job_id)
    ack(SimpleNamespace(**{**vars(s), 'run_id': fresh_run['id']}))
    fresh = capture(s)
    assert len(fresh['records']) == 2
    assert sum(r['reception']['state'] == 'received' for r in fresh['records']) == 1
    assert sum(r['reception']['state'] == 'producer_failed' for r in fresh['records']) == 1
    verify(s, query_id, failed)
    current_task = good(s.client.get('/api/workspace/tasks/' + s.task_id))
    good(complete(s.client, current_task, note='仅核对正常重试成果；旧失败不伪装收件'))
    verify(s, query_id, failed)
