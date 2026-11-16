"""Business coordination and reviewed data governance with durable step receipts."""
from fastapi import HTTPException
from .models import Task, Plan, Acquisition, Capability, Governance, Issue, Entity, Job, Memory, now
from .services import scoped, check_version, audit, published, query_evidence, update_entity, validate
from .security import require
from .contracts import digest
from sqlalchemy import select


def approve_plan(db, p, plan_id, expected, reason):
    require(p, "admin", "operator")
    plan = scoped(db, Plan, plan_id, p, True); check_version(plan, expected)
    if plan.data.get('schema') == 'native-query-proposal-v1':
        raise HTTPException(409, '查询建议请在原任务中核对和采用，不能作为旧执行计划批准')
    task = scoped(db, Task, plan.task_id, p, True)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    if task.status!='review':raise HTTPException(409,'目标或计划当前待重新核对，不能批准旧计划')
    if plan.status != "review" or task.data.get("current_plan_id") != plan.id:
        raise HTTPException(409, "计划已失效或尚不可批准")
    if not plan.data.get("result", {}).get("plan", {}).get("steps"):
        raise HTTPException(409, "没有可执行计划")
    if task.data.get("observation_mode") == "existing_only" and any(s["action"] == "request_observation" for s in plan.data["result"]["plan"]["steps"]):
        raise HTTPException(409, "目标只允许已有证据，不能批准新的观测请求")
    from .goals import check_plan_goal, stop_reason
    check_plan_goal(task,plan)
    reason_stop=stop_reason(db,p,task,plan)
    if reason_stop:raise HTTPException(409,reason_stop)
    from .business_tools import validate_step
    for step in plan.data['result']['plan']['steps']:
        if step['action']=='invoke_capability':
            cap=validate_step(db,p,task,step)
            if cap.data['execution'].get('approval_policy')=='admin':require(p,'admin')
    plan.status = "approved"; plan.approved_by = p.user_id; plan.approved_hash = digest(plan.data)
    plan.version += 1; task.status = "queued"; task.version += 1
    db.add(Job(workspace_id=p.workspace_id, kind="execute_plan", idempotency_key="plan:"+plan.id,
               data={"plan_id": plan.id, "actor_id": p.user_id}))
    audit(db, p, "plan.approve", plan.id, {"hash": plan.approved_hash, "reason": reason})
    return plan



def capability_receipt(plan, step, invocation):
    return {'status': invocation.status, 'invocation_id': invocation.id,
        'output': invocation.data.get('output'), 'reason': invocation.data.get('error'),
        'capability_id': step['capability_id'], 'release_version': step['release_version'],
        'input_hash': step['input_hash'], 'method': 'registered_capability',
        'evidence': plan.data['result']['evidence']['evidence'],
        'observation_evidence': plan.data['result']['evidence'].get('observation_evidence', []),
        'quality_limit': '工具契约执行结果，事实或专业结论仍需核查验收'}

def execute_plan(db, p, plan_id):
    plan = scoped(db, Plan, plan_id, p, True)
    task = scoped(db, Task, plan.task_id, p, True)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task,record_actor=True)
    if task.status == "cancelled":
        plan.status='cancelled';plan.version+=1
        return
    if plan.status not in {"approved", "running"} or plan.approved_hash != digest(plan.data):
        raise ValueError("计划未授权或授权内容发生变化")
    plan.status = "running"; task.status = "running"
    from .goals import check_plan_goal, query_goal, stop_reason, evaluate
    check_plan_goal(task,plan,allow_legacy=True)
    # Checkpoints belong to task runtime, never mutate approved plan content.
    receipts = dict(task.data.get("step_receipts", {}))
    steps = plan.data["result"]["plan"]["steps"]
    if task.data.get("observation_mode") == "existing_only" and any(s["action"] == "request_observation" for s in steps):
        raise ValueError("已有证据目标不能发起新的观测")
    for index, step in enumerate(steps):
        reason_stop=stop_reason(db,p,task,plan)
        if reason_stop:
            task.status='waiting_evidence';task.version+=1
            task.data={**task.data,'execution_error':reason_stop};plan.status='failed';plan.version+=1
            return
        key = plan.id + ":" + str(index)
        if key in receipts:
            if receipts[key]['result'].get('status') not in {'failed','blocked_evidence','blocked_model','unknown','running','not_executed'}:
                if step['action'] == 'invoke_capability':
                    from .business_tools import validate_step
                    try:
                        validate_step(db,p,task,step)
                    except HTTPException:
                        task.status='waiting_evidence';task.version+=1
                        task.data={**task.data,'execution_error':'调用结果已保存，但计划依据或能力已变化；请核对历史结果后重新规划，不会自动重放动作'}
                        plan.status='failed';plan.version+=1
                        return
                continue
            task.status='waiting_tool';plan.status='waiting_tool';return
        if step["action"] == "query_evidence" and step["agent"] == "data":
            receipt = query_goal(db,p,task)
        elif step["action"] == "inspect_observation" and step["agent"] == "perception":
            receipt = inspect_observations(db, p, task.data["entity_ids"], task.data.get("fields", []),task=task)
        elif step["action"] == "request_observation" and step["agent"] == "perception":
            receipt = {"status": "waiting_observation", "requests": []}
            package = published(db, p, task.package_id, "domain_package")
            source_ids = package.data["specification"].get("observation_source_ids", [])
            related=query_goal(db,p,task) if task.data.get('goal_spec',{}).get('relation_query') else None
            if related and related['relations']['relation_gaps']:
                raise HTTPException(409,'关系依据不足，须先核查数据关系再请求对象补采')
            for entity_id in related['selected_entity_ids'] if related else task.data["entity_ids"]:
                for source_id in source_ids:
                    source = published(db, p, source_id, "source")
                    key_acq = "plan:"+plan.id+":"+entity_id+":"+source_id
                    acquisition = db.scalar(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
                                                     Acquisition.idempotency_key == key_acq))
                    if not acquisition:
                        acquisition = Acquisition(workspace_id=p.workspace_id, entity_id=entity_id,
                            capability_id=source.id, idempotency_key=key_acq, status="proposed", data={
                            "task_id": task.id, "fields": task.data.get("fields", []),
                            "reason": "业务计划提出补采，需要已有监测授权或单次批准"})
                        db.add(acquisition); db.flush()
                    receipt["requests"].append(acquisition.id)
            if not source_ids:
                receipt["reason"] = "业务包没有配置可用感知源，需管理员接入设备"
        elif step['action']=='invoke_capability':
            from .business_tools import validate_step
            from .gateway import invoke
            try:
                validate_step(db,p,task,step)
                attempt=task.data.get('tool_attempts',{}).get(key,0)
                call_key=step['request_key']+(':retry:'+str(attempt) if attempt else '')
                invocation=invoke(db,p,step['capability_id'],step['agent'],step['input_snapshot'],call_key,
                    approval={'plan_id':plan.id,'step_index':index})
                receipt=capability_receipt(plan,step,invocation)
            except HTTPException as error:
                receipt={'status':'blocked_evidence','reason':str(error.detail),'method':'registered_capability'}
        elif step["action"] == "report" and step["agent"] == "business":
            evidence = query_goal(db,p,task)
            receipt = {"status": evidence["status"], "evidence": evidence,
                       'tool_results':[r['result'] for k,r in receipts.items() if k.startswith(plan.id+':')
                           and r['step']['action']=='invoke_capability' and r['result']['status']=='succeeded'],
                       "method": "evidence_report", "summary": "证据及工具结果报告；结论依据对象、观测与调用回执，需用户验收"}
        else:
            raise ValueError("计划中的职责与动作组合不受支持")
        # External calls commit and release locks. Re-read the durable task before saving progress.
        task=scoped(db,Task,task.id,p,True)
        receipts[key] = {"step": step, "result": receipt, "finished_at": now().isoformat()}
        task.data = {**task.data, "step_receipts": receipts};task.version+=1; db.commit()
        task = scoped(db, Task, task.id, p, True)
        if task.status == "cancelled":
            plan=scoped(db,Plan,plan.id,p,True);plan.status='cancelled';plan.version+=1
            return
        if receipt.get('status') in {'failed','blocked_evidence','blocked_model','unknown','running'}:
            task.status='waiting_tool';task.version+=1
            plan.status='waiting_tool';plan.version+=1
            audit(db,p,'business.tool_blocked',task.id,{'reason':receipt.get('reason'),'step_index':index})
            return
    latest = query_goal(db,p,task)
    waiting_observation = any(r["result"].get("status") == "waiting_observation"
                              for key, r in receipts.items() if key.startswith(plan.id + ":"))
    incomplete_step = any(r["result"].get("status") in {"insufficient", "conflict", "failed"}
                          for key, r in receipts.items() if key.startswith(plan.id + ":"))
    task.status = "awaiting_acceptance" if latest["status"] == "sufficient" and not waiting_observation and not incomplete_step else "waiting_evidence"
    goal_evaluation=evaluate(task,plan,latest)
    task.data = {**task.data, "final_evidence": latest,"goal_evaluation":goal_evaluation}
    if goal_evaluation.get("ready_for_acceptance") is False:task.status="waiting_evidence"
    task.version += 1
    plan.status = "executed"; plan.version += 1
    audit(db, p, "business.execute", task.id, {"status": task.status})


def inspect_observations(db, p, entity_ids, fields,task=None):
    """Read scoped stored observations; file integrity is not a factual model-quality verdict."""
    from .evidence import evidence_bytes
    from .goals import query_goal
    result = query_goal(db,p,task) if task else query_evidence(db, p, entity_ids, fields)
    observations = result["observation_evidence"]
    integrity = []
    for observation in observations:
        for evidence_id in observation["evidence_ids"]:
            try:
                row, content = evidence_bytes(db, p, evidence_id)
                integrity.append({"evidence_id": row.id, "sha256": row.sha256, "size_bytes": len(content), "status": "verified"})
            except HTTPException:
                integrity.append({"evidence_id": evidence_id, "status": "failed", "reason": "文件无法读取或完整性校验未通过"})
    status = "insufficient" if not observations else "failed" if any(item["status"] == "failed" for item in integrity) else "sufficient"
    return {"status": status, "observation_evidence": observations, "file_integrity": integrity,
            "method": "stored_observation_review", "reason": "校验仅证明已保存文件完整；提取值仍需人工核查，不代表设备真实状态"}


def accept_task(db, p, task_id, expected, feedback):
    require(p, "admin", "operator")
    task = scoped(db, Task, task_id, p, True); check_version(task, expected)
    from .layered_approval_store_v175 import old_acceptance_guard
    old_acceptance_guard(db,p,task)
    if task.status != "awaiting_acceptance":
        raise HTTPException(409, "任务尚未形成可验收结果")
    plan=scoped(db,Plan,task.data['current_plan_id'],p)
    from .goals import check_plan_goal, query_goal, evaluate
    check_plan_goal(task,plan,allow_legacy=True)
    from .business_tools import validate_step
    for index,step in enumerate(plan.data['result']['plan']['steps']):
        if step['action']=='invoke_capability':
            validate_step(db,p,task,step)
            receipt=task.data.get('step_receipts',{}).get(plan.id+':'+str(index),{})
            if receipt.get('result',{}).get('status')!='succeeded':raise HTTPException(409,'工具尚未成功，不可验收')
    latest = query_goal(db,p,task)
    final = task.data["final_evidence"]
    from .business_tools import evidence_hash
    if (latest["status"] != "sufficient" or latest["evidence"] != final["evidence"]
            or latest.get("observation_evidence", []) != final.get("observation_evidence", [])
            or evidence_hash(latest)!=evidence_hash(final)):
        raise HTTPException(409, "证据发生变化，请重新核实后验收")
    if latest.get("observation_evidence") and inspect_observations(db, p, task.data["entity_ids"], task.data.get("fields", []),task=task)["status"] == "failed":
        raise HTTPException(409, "观测文件完整性校验未通过，不能验收")
    assessment=evaluate(task,plan,latest,accepted=True)
    if assessment.get('completed') is False:
        raise HTTPException(409,'目标完成标准尚未满足，不能以报告已生成代替完成')
    task.data={**task.data,'goal_evaluation':assessment}
    task.status = "completed"; task.version += 1
    task.data = {**task.data, "accepted_by": p.user_id, "feedback": feedback, "accepted_at": now().isoformat()}
    memory = Memory(workspace_id=p.workspace_id, task_id=task.id, status="candidate", text=feedback,
                    data={"type": "verified_case", "task_id": task.id, "goal": task.title,
                          'tool_results':[r['result'] for k,r in task.data.get('step_receipts',{}).items()
                            if k.startswith(plan.id+':') and r['step']['action']=='invoke_capability'],
                          "evidence": latest, "package_id": task.package_id, "confirmed_by": p.user_id})
    db.add(memory); db.flush(); audit(db, p, "task.accept", task.id, {"memory_id": memory.id})
    return task


def retry_execution(db,p,task_id,expected,reason):
    require(p,'admin','operator')
    task=scoped(db,Task,task_id,p,True);check_version(task,expected)
    plan=scoped(db,Plan,task.data.get('current_plan_id'),p,True)
    if task.status!='waiting_tool' or plan.approved_hash!=digest(plan.data):raise HTTPException(409,'任务没有可恢复的已批准工具步骤')
    receipts=dict(task.data.get('step_receipts',{}));blocked=[(k,r) for k,r in receipts.items()
        if k.startswith(plan.id+':') and r['result']['status'] in {'failed','blocked_evidence','blocked_model','unknown','running'}]
    if len(blocked)!=1:raise HTTPException(409,'需要核查唯一阻塞工具步骤')
    key,receipt=blocked[0];step=receipt['step']
    if step.get('side_effect') or receipt['result']['status'] in {'unknown','running'}:
        raise HTTPException(409,'外部动作结果未知或可能有副作用，不能重放，请先核对服务回执')
    from .business_tools import validate_step
    cap=validate_step(db,p,task,step)
    attempts=task.data.get('tool_attempts',{});count=attempts.get(key,0)+1
    if count>=min(int(cap.data['budget'].get('max_attempts',2)),3):raise HTTPException(409,'已达到登记工具重试上限，请核查服务或重新规划')
    # Failed read-only calls are retried under a distinct attempt key; the approved arguments remain fixed.
    history=[*task.data.get('tool_failure_history',[]),{**receipt,'retry':{'reason':reason,'requested_at':now().isoformat(),'requested_by':p.user_id}}]
    receipts.pop(key)
    task.data={**task.data,'step_receipts':receipts,'tool_attempts':{**attempts,key:count},'tool_failure_history':history}
    task.status='queued';task.version+=1;plan.status='approved';plan.version+=1
    db.add(Job(workspace_id=p.workspace_id,kind='execute_plan',idempotency_key='plan:'+plan.id+':retry:'+str(count),
        data={'plan_id':plan.id,'actor_id':p.user_id}))
    audit(db,p,'business.tool_retry',task.id,{'reason':reason,'attempt':count});return task


def propose_repair(db, p, issue_id, attributes, expected_entity_version, reason):
    require(p, "admin", "operator")
    issue = scoped(db, Issue, issue_id, p, True)
    entity = scoped(db, Entity, issue.entity_id, p)
    check_version(entity, expected_entity_version)
    if issue.status != "open" or issue.kind != "invalid_schema":
        raise HTTPException(409, "过期或观测不足需要补采，不能用修改属性掩盖")
    package = published(db, p, entity.schema_capability_id, "domain_package")
    validate(package.data["specification"]["entity_schemas"][entity.entity_type], attributes)
    plan = Governance(workspace_id=p.workspace_id, issue_id=issue.id, data={"entity_id": entity.id,
        "expected_entity_version": entity.version, "before": entity.data, "after": attributes,
        "reason": reason, "proposed_by": p.user_id})
    db.add(plan); db.flush(); audit(db, p, "governance.propose", plan.id); return plan


def approve_repair(db, p, plan_id, expected, reason):
    require(p, "admin")
    plan = scoped(db, Governance, plan_id, p, True); check_version(plan, expected)
    if plan.status != "review":
        raise HTTPException(409, "治理方案已处理")
    plan.status = "approved"; plan.approved_by = p.user_id; plan.version += 1
    plan.data = {**plan.data, "approval_reason": reason, "approved_hash": digest(plan.data)}
    db.add(Job(workspace_id=p.workspace_id, kind="apply_governance", idempotency_key="governance:"+plan.id,
               data={"plan_id": plan.id, "actor_id": p.user_id}))
    audit(db, p, "governance.approve", plan.id); return plan


def apply_repair(db, p, plan_id):
    plan = scoped(db, Governance, plan_id, p, True)
    if plan.status == "applied":
        return
    original = {k:v for k,v in plan.data.items() if k not in {"approved_hash", "approval_reason"}}
    if plan.status != "approved" or digest(original) != plan.data["approved_hash"]:
        raise ValueError("治理方案授权无效")
    entity = update_entity(db, p, plan.data["entity_id"], plan.data["after"],
                           plan.data["expected_entity_version"], plan.data["reason"])
    issue = scoped(db, Issue, plan.issue_id, p, True)
    from .quality import verify
    verified=verify(db,p,issue,'执行已批准字段方案后复核当前权威档案')
    issue.data = {**issue.data, "verification": {"entity_version": entity.version,
                  "matches_approved_change": entity.data == plan.data["after"]}, "plan_id": plan.id}
    plan.status = "applied" if verified else 'verification_failed'; plan.version += 1
    audit(db, p, "governance.apply", plan.id, {"entity_version": entity.version})
