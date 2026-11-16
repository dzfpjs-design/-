"""One completed real target, bounded proof reuse and live negative gates.

Root alone runs this on disposable ga_test PostgreSQL. All successful source,
case, reference, parse/ACK, Task, packet and human records use normal APIs and
the actual worker. Only a negative savepoint damages Use.status; a real source
file is temporarily moved, and a real current role is disabled/restored via
its normal API. No clock, successful Job, provider or proof is fabricated.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker, workflow_graph
from generic_agents import layered_approval_store_v175 as approval
from generic_agents import layered_case_use_origin_v210 as origin
from generic_agents import layered_case_uses_v210 as uses
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import scene
from tests.test_layered_cases_pg_v209 import accepted_case_source
from tests.test_layered_case_uses_pg_v210 import (
    case_use_target, create_use, ready_use, stage_reference, source_inventory)
from tests.test_authored_workflow_pg_v208 import good
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import command, task_jobs


def complete_target_before_independent_acceptance(f, source):
    current = good(f.author_client.get(f.target_approval_path))
    value = copy.deepcopy(current['value']); value['graph_inputs'][source['input_key']] = source['ref']
    command(f.author_client, f.target_approval_path, 'revise', value=value)
    command(f.author_client, f.target_approval_path, 'submit')
    submission = good(f.author_client.get(f.target_approval_path))['submission_hash']
    for reviewer in (f.first_client, f.second_client):
        command(reviewer, f.target_approval_path, 'decide', submission_hash=submission, decision='approve')
    command(f.author_client, f.target_approval_path, 'start')
    jobs = task_jobs(f.s, f.target_task_id); assert len(jobs) == 1
    assert worker.process_once(jobs[0][0], kinds=[workflow_graph.KIND], deliver=False, workspace_id=f.s.workspace)
    path = '/api/workspace/tasks/' + f.target_task_id
    for _ in range(16):
        current = good(f.author_client.get(path))
        pending = current['graph_pending_humans']
        if not pending: break
        human = pending[0]
        good(f.author_client.post(path + '/graph/complete', json={'request_id': str(uuid4()),
            'activation_id': human['activation_id'], 'version': human['version'],
            'cancel_epoch': current['graph_runtime']['cancel_epoch'],
            'output': {'result': '已对照完整参考及原资料保存实际人工结果，仍待独立验收'}}))
    assert good(f.author_client.get(f.target_approval_path))['status'] == 'executing'


def test_real_completed_outcome_reuses_once_per_call_but_rechecks_live_gates_and_source_file(case_use_target, monkeypatch):
    f = case_use_target
    _, _, created = create_use(f); reference = ready_use(f, created)
    source = stage_reference(f, reference)
    complete_target_before_independent_acceptance(f, source)
    original_source = source_inventory(f)

    calls = []
    actual_approved_case = origin.approved_case

    def observe(*args, **kwargs):
        calls.append(args[2])
        return actual_approved_case(*args, **kwargs)

    monkeypatch.setattr(origin, 'approved_case', observe)  # Observe the real full verifier.
    with Session(engine()) as db:
        task = db.get(m.Task, f.target_task_id)
        managed = approval.managed(db, f.s.p, task)
        assert task.status == 'workflow_completed' and managed.status == 'executing'
        target_before = copy.deepcopy((task.version, task.status, task.data, managed.version, managed.data))

        def outcome():
            current_task = db.get(m.Task, f.target_task_id)
            current_managed = approval.managed(db, f.s.p, current_task)
            return approval.outcome(db, f.s.p, current_task, current_managed, lock_sources=False)

        first = outcome()
        assert calls == [f.source_case['id']], 'One full source proof should serve this single calculation.'
        assert 'layered_case_use_verification_v210' not in db.info
        second = outcome()
        assert calls == [f.source_case['id'], f.source_case['id']], 'Another calculation must verify again.'
        assert first == second
        assert first['schema'] == 'layered-approval-acceptance-basis-v175'
        assert first['task_id'] == f.target_task_id and first['task_version'] == task.version
        assert first['physical_state_verified'] is False
        assert first['graph_state_hash'] == task.data['graph_runtime']['state_hash']
        assert first['workflow_runtime'] == task.data['workflow_runtime']
        assert first['basis_hash'] == approval.acceptance_hash({key: value for key, value in first.items() if key != 'basis_hash'})

        savepoint = db.begin_nested()
        try:
            # Negative-only corruption; it is not a permitted withdrawal of an
            # executing Task. Current Use state must never be a cached grant.
            row = db.get(uses.Use, reference['id']); row.status = 'withdrawn'; db.flush()
            with pytest.raises(HTTPException) as stopped: outcome()
            assert stopped.value.status_code == 409
            assert 'layered_case_use_verification_v210' not in db.info
        finally:
            savepoint.rollback()
        assert outcome() == first

        packet_id = f.source_case['source']['packets'][0]['evidence_id']
        evidence = db.get(m.Evidence, packet_id)
        original_path = settings().storage_dir / evidence.storage_key
        temporary_path = original_path.with_name(original_path.name + '.outcome-outage-' + uuid4().hex)
        original_path.rename(temporary_path)
        try:
            with pytest.raises(HTTPException) as missing: outcome()
            assert missing.value.status_code == 409
            assert 'layered_case_use_verification_v210' not in db.info
        finally:
            temporary_path.rename(original_path)
        assert outcome() == first

        # The original fixture names its executor post explicitly. Disable it
        # through the actual normal API, not a mock of role_allowed or guard.
        grant = db.scalar(select(approval.Role).where(approval.Role.workspace_id == f.s.workspace,
            approval.Role.user_id == f.s.p.user_id, approval.Role.role_key == 'executor',
            approval.Role.enabled.is_(True)))
        assert grant is not None
        fields = {'grant_id': grant.id, 'unit_id': grant.unit_id, 'user_id': grant.user_id, 'role_key': grant.role_key}
        role_path = '/api/workspace/approval-settings-v175/roles'
        good(f.author_client.post(role_path, json={'request_id': str(uuid4()),
            'expected_version': grant.version, **fields, 'enabled': False}))
        try:
            with pytest.raises(HTTPException) as forbidden: outcome()
            assert forbidden.value.status_code in {403, 409}
            assert 'layered_case_use_verification_v210' not in db.info
        finally:
            with Session(engine()) as current:
                current_version = current.get(approval.Role, fields['grant_id']).version
            good(f.author_client.post(role_path, json={'request_id': str(uuid4()),
                'expected_version': current_version, **fields, 'enabled': True}))
        assert outcome() == first
        assert 'layered_case_use_verification_v210' not in db.info
        db.refresh(task); db.refresh(managed)
        assert (task.version, task.status, task.data, managed.version, managed.data) == target_before
    assert source_inventory(f) == original_source
