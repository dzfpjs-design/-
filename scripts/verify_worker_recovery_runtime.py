"""Opt-in, checkpointed SIGKILL validation using ONLY the existing synthetic workspace.

Stop the owned general worker before UI approval and the crash command. No cloud planning,
credentials, industry records or device actions are sent. The local reference service is used.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task, Plan, Job, Invocation, User, Memory, Capability, now
from generic_agents.security import Principal
from generic_agents.services import scoped, query_evidence, audit, as_dict
from generic_agents.business_tools import prepare_step

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
CHECKPOINT = ROOT / '.runtime/worker-runtime-validation.json'
SNAPSHOT = ROOT / '.runtime/worker-runtime-snapshot.json'
MODE = ROOT / '.runtime/reference-tools-mode.json'
EVIDENCE = ROOT / 'docs/evidence/worker-runtime.json'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)); path.chmod(0o600)


def prepare():
    if CHECKPOINT.exists():
        print('既有后台验收检查点已保存，未重复创建'); return
    example = json.loads((ROOT / '.runtime/business-tool-validation.json').read_text())['cases'][2]
    with Session(engine(), expire_on_commit=False) as db:
        original = db.get(Task, example['task']['id'])
        user = db.get(User, original.owner_id)
        assert user.username == 'ui_validation' and original.title.startswith('合成工具验收')
        p = Principal(user.id, original.workspace_id, 'admin', 'synthetic-runtime-validation')
        cap = scoped(db, Capability, example['tool']['id'], p)
        assert cap.data['execution']['endpoint'] == 'http://127.0.0.1:8877/analyze'
        assert cap.data['execution']['side_effect'] is True
        task = Task(workspace_id=p.workspace_id, package_id=original.package_id, owner_id=p.user_id,
            title='后台恢复验收 · 合成参考任务（无新增云规划）', status='review', data={
                'entity_ids': original.data['entity_ids'], 'fields': ['temperature'], 'observation_mode': 'existing_only',
                'validation_scope': 'explicit synthetic fixture; registered local service; no cloud planning'})
        db.add(task); db.flush()
        evidence = query_evidence(db, p, task.data['entity_ids'], ['temperature'], learning=False)
        tool = prepare_step(db, p, task, {'agent': 'data', 'action': 'invoke_capability',
            'capability_id': cap.id, 'arguments': {'field': 'temperature'}}, evidence, 1)
        plan = Plan(workspace_id=p.workspace_id, task_id=task.id, status='review', data={'status': 'review', 'result': {
            'planning_source': 'explicit_synthetic_recovery_fixture', 'evidence': evidence, 'capability_scoped': False,
            'plan': {'summary': '合成恢复验证：读取档案，调用独立本地回执服务，然后报告；本任务没有新云模型规划。',
                'steps': [{'agent': 'data', 'action': 'query_evidence', 'title': '读取资源档案和观测证据'}, tool,
                          {'agent': 'business', 'action': 'report', 'title': '整理证据报告和待核查限制'}]}}})
        db.add(plan); db.flush(); task.data = {**task.data, 'current_plan_id': plan.id}; task.version += 1
        audit(db, p, 'validation.worker_case_prepared', task.id, {'scope': 'synthetic fixture, no new cloud planning'})
        db.commit()
        save(CHECKPOINT, {'task_id': task.id, 'plan_id': plan.id, 'workspace_id': p.workspace_id,
            'tool_id': cap.id, 'step_index': 1, 'request_key': tool['request_key'], 'input_hash': tool['input_hash'], 'phase': 'review'})
    print('已保存合成计划，等待在浏览器批准；未调用云模型')


def crash():
    state = json.loads(CHECKPOINT.read_text())
    if state.get('phase') != 'review':
        print('崩溃驱动已运行，未重复发送动作'); return
    import local_runtime
    worker_pid = ROOT / '.runtime/worker.pid'
    assert not worker_pid.exists() or not local_runtime.owned(int(worker_pid.read_text()), 'worker'), '先停止自有通用worker'
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.workspace_id == state['workspace_id'],
            Job.data['plan_id'].as_string() == state['plan_id'], Job.status == 'queued'))
        assert job, '需先在浏览器批准该合成计划'
        state['job_id'] = job.id
    # Save before launching: an interrupted driver cannot blindly run this action a second time.
    state['phase'] = 'crash_started'; save(CHECKPOINT, state)
    save(MODE, {'/analyze': 'unknown_after_commit', '/mcp': 'normal'})
    program = '''from generic_agents import leases, gateway
from generic_agents.worker import process_once
import os, signal
leases.LEASE_SECONDS=3
leases.HEARTBEAT_SECONDS=0.3
original=gateway.invoke
def kill_after_saved_unknown(*args, **kwargs):
    result=original(*args, **kwargs)
    assert result.status == 'unknown'
    os.kill(os.getpid(), signal.SIGKILL)
gateway.invoke=kill_after_saved_unknown
process_once(''' + repr(state['job_id']) + ')\n'
    log_path = ROOT / '.runtime/worker-crash-runtime.log'
    try:
        with log_path.open('ab') as log:
            log_path.chmod(0o600)
            process = subprocess.Popen([sys.executable, '-c', program], stdout=log, stderr=log,
                stdin=subprocess.DEVNULL, cwd=ROOT, start_new_session=True)
        state['child_pid'] = process.pid; save(CHECKPOINT, state)
        process.wait(timeout=30)
        assert process.returncode < 0, '子进程未按预期强制退出，请核查私有日志，禁止重复运行动作'
        state['sigkill_verified'] = True; state['phase'] = 'crashed'; save(CHECKPOINT, state)
    finally:
        save(MODE, {'/analyze': 'normal', '/mcp': 'normal'})
    print('自有验收子进程已强制退出；调用记录已保存，等待租约接管')


def capture():
    state = json.loads(CHECKPOINT.read_text())
    with Session(engine()) as db:
        task = db.get(Task, state['task_id'])
        plan = db.get(Plan, state['plan_id'])
        jobs = db.scalars(select(Job).where(Job.workspace_id == state['workspace_id'],
            Job.data['plan_id'].as_string() == state['plan_id']).order_by(Job.created_at)).all()
        calls = db.scalars(select(Invocation).where(Invocation.workspace_id == state['workspace_id'],
            Invocation.idempotency_key == state['request_key'])).all()
        memories = db.scalars(select(Memory).where(Memory.task_id == task.id)).all()
        result = {'task': as_dict(task), 'plan': as_dict(plan), 'jobs': [as_dict(j) for j in jobs],
            'calls': [as_dict(c) for c in calls], 'memories': [as_dict(m) for m in memories]}
    with sqlite3.connect(ROOT / '.runtime/reference-tools.sqlite3') as db:
        result['service_receipts'] = [list(row) for row in db.execute(
            'SELECT request_key,input_hash,output,created_at FROM receipts WHERE request_key=?', (state['request_key'],))]
    return result


def verify():
    state = json.loads(CHECKPOINT.read_text())
    for _ in range(100):
        result = capture()
        if result['task']['status'] == 'waiting_tool': break
        time.sleep(0.1)
    assert result['task']['status'] == 'waiting_tool'
    assert len(result['calls']) == len(result['service_receipts']) == 1
    call = result['calls'][0]
    assert call['status'] == 'unknown'
    assert result['service_receipts'][0][1] == state['input_hash']
    record = result['task']['data']['step_receipts'][state['plan_id'] + ':1']
    assert record['result']['invocation_id'] == call['id']
    assert result['jobs'][0]['status'] == 'failed' and result['jobs'][0]['data']['lease_recoveries'] == 1
    state['phase'] = 'waiting_receipt'; state['invocation_id'] = call['id']; save(CHECKPOINT, state)
    EVIDENCE.write_text(json.dumps({'scope': 'synthetic registered local service, actual subprocess and PostgreSQL',
        'cloud_planning': 'explicit fixture; no new cloud call', 'actual_subprocess_sigkill': state['sigkill_verified'],
        'committed_unknown_call_recovered_into_operator_task': True, 'external_action_records': 1,
        'lease_recoveries': 1, 'human_reconciliation_and_acceptance': 'pending'}, ensure_ascii=False, indent=2))
    print('未知调用已恢复到工作台；独立服务只有1条对应动作，等待浏览器只读核对')


def snapshot():
    result = capture()
    assert result['task']['status'] == 'completed' and len(result['memories']) == 1
    assert len(result['calls']) == len(result['service_receipts']) == 1
    assert result['calls'][0]['status'] == 'succeeded' and len(result['calls'][0]['data']['receipt_checks']) == 1
    assert result['calls'][0]['data']['output']['value'] == 20
    save(SNAPSHOT, result)
    evidence = json.loads(EVIDENCE.read_text())
    evidence.update({'human_reconciliation_and_acceptance': True, 'result_value': 20,
        'external_action_replayed': False, 'persisted_acceptance_feedback': True, 'normal_restart_verified': False})
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print('浏览器核对与验收已保存；动作未重放，已建立重启比较快照')


def compare():
    assert capture() == json.loads(SNAPSHOT.read_text()), '重启后验证记录发生变化'
    evidence = json.loads(EVIDENCE.read_text()); evidence['normal_restart_verified'] = True
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print('API/worker正常重启后，任务、回执、验收反馈与服务动作记录一致')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['prepare', 'crash', 'verify', 'snapshot', 'compare'])
    globals()[parser.parse_args().action]()
