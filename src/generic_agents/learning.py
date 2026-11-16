"""Evaluated, human-reviewed, reversible experience-selection strategies.

This module never changes facts, permissions, approval rules or provider weights.
Frozen tests prove only the nominated retrieval strategy, not general model quality.
"""
from time import perf_counter
from datetime import timezone
from sqlalchemy import select
from fastapi import HTTPException
from pydantic import Field
from .contracts import Contract, digest
from .models import (LearningCandidate, EvaluationSet, EvaluationRun, LearningDeployment,
                     LearningChannel, LearningTrial, Memory, Entity, Evidence, Job, User,
                     Membership, now, uid)
from .security import require
from .services import scoped, published, as_dict, audit, check_version


class SelectionConfig(Contract):
    field_weights: dict[str, int] = Field(default_factory=dict, max_length=30)
    max_cases: int = Field(default=10, ge=1, le=10)


def ensure_config(db, p, package_id, value):
    package = published(db, p, package_id, "domain_package")
    config = SelectionConfig.model_validate(value).model_dump()
    fields = {field for schema in package.data["specification"]["entity_schemas"].values()
              for field in schema.get("properties", {})}
    if not set(config["field_weights"]).issubset(fields) or any(type(v) is not int or not 1 <= v <= 5 for v in config["field_weights"].values()):
        raise HTTPException(422, "权重只能使用业务包登记字段，范围为1至5")
    return config


def eligible(db, p, package_id):
    # Filter validity and package BEFORE limiting, so unrelated cases cannot hide valid evidence.
    rows = db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
        Memory.status == "trusted", Memory.data["package_id"].astext == package_id)
        .order_by(Memory.created_at.desc(), Memory.id).limit(501)).all()
    if len(rows) > 500:
        raise HTTPException(409, "经验规模超过当前评测/选择上限，需要分区检索适配器")
    from .relations import case_valid
    from .goal_queries import case_valid as delegated_valid
    return [m for m in rows if (not m.valid_until or m.valid_until > now())
        and case_valid(db,p,m.data.get('evidence',{})) and delegated_valid(db,p,m.data.get('evidence',{}))]


def memory_view(memory):
    return {"id": memory.id, "version": memory.version, "text": memory.text, "data": memory.data,
            "created_at": memory.created_at.astimezone(timezone.utc).isoformat(),
            "valid_until": memory.valid_until.astimezone(timezone.utc).isoformat() if memory.valid_until else None}


def fields_of(memory):
    return {key for item in memory["data"].get("evidence", {}).get("evidence", [])
            for key in item.get("attributes", {})}


def rank(pool, fields, config):
    # The pool is already ordered by recency. Python's stable sort preserves that tie-break.
    wanted = set(fields)
    scored = sorted(pool, key=lambda m: sum(config["field_weights"].get(f, 0)
                    for f in fields_of(m) & wanted), reverse=True)
    return scored[:config["max_cases"]]


def fingerprints(db, p, evidence):
    result = set()
    for relation in evidence.get('relations',{}).get('relation_evidence',[]):
        result.add('relation:'+relation['relation_id']+':'+str(relation['version']))
    for item in evidence.get("evidence", []):
        result.add("entity:" + item["entity_id"] + ":" + str(item["version"]))
    for item in evidence.get("observation_evidence", []):
        result.add("observation:" + item["observation_id"])
        for evidence_id in item.get("evidence_ids", []):
            row = scoped(db, Evidence, evidence_id, p)
            result.add("file:" + row.sha256)
    return result


def candidate_valid(db, p, candidate):
    if candidate.content_hash != digest(candidate.data):
        raise HTTPException(409, "候选内容发生变化，需重新评测")
    ensure_config(db, p, candidate.package_id, candidate.data["config"])
    for source in candidate.data["sources"]:
        memory = scoped(db, Memory, source["id"], p)
        from .relations import case_valid
        if not case_valid(db,p,memory.data.get('evidence',{})):
            raise HTTPException(409,'候选依据的关系版本或来源已变化，不能继续复用')
        if memory.status != "trusted" or memory.version != source["version"] or digest(memory_view(memory)) != source["hash"] or (memory.valid_until and memory.valid_until <= now()):
            raise HTTPException(409, "候选依据已撤回、纠正或过期")


def propose_candidate(db, p, package_id, memory_ids, title, config=None):
    require(p, "admin", "operator")
    published(db, p, package_id, "domain_package")
    if not memory_ids or len(memory_ids) > 50 or len(set(memory_ids)) != len(memory_ids):
        raise HTTPException(422, "请选择1至50条不重复的已核实案例")
    sources, groups, learned = [], set(), {}
    for memory_id in sorted(memory_ids):
        memory = scoped(db, Memory, memory_id, p)
        from .relations import case_valid
        if not case_valid(db,p,memory.data.get('evidence',{})):
            raise HTTPException(409,'案例的关系依据已撤回、纠正或过期，不能用于进化')
        if memory.status != "trusted" or memory.data.get("package_id") != package_id or (memory.valid_until and memory.valid_until <= now()):
            raise HTTPException(409, "只能从本业务包有效的已核实案例生成候选")
        view = memory_view(memory)
        sources.append({"id": memory.id, "version": memory.version, "hash": digest(view)})
        groups.add("task:" + str(memory.task_id))
        groups |= fingerprints(db, p, memory.data.get("evidence", {}))
        for field in fields_of(view):
            learned[field] = min(5, learned.get(field, 0) + 1)
    config = ensure_config(db, p, package_id, config if config is not None else {"field_weights": learned, "max_cases": 10})
    data = {"kind": "experience_selection", "title": title, "config": config, "sources": sources,
            "training_fingerprints": sorted(groups), "proposed_by": p.user_id}
    previous = db.scalar(select(LearningCandidate).where(LearningCandidate.workspace_id == p.workspace_id,
                                                       LearningCandidate.content_hash == digest(data)))
    if previous:
        return previous
    row = LearningCandidate(workspace_id=p.workspace_id, package_id=package_id, title=title,
                            data=data, content_hash=digest(data))
    db.add(row); db.flush(); audit(db, p, "learning.propose", row.id); return row


def create_set(db, p, package_id, title):
    require(p, "admin", "operator"); published(db, p, package_id, "domain_package")
    row = EvaluationSet(workspace_id=p.workspace_id, package_id=package_id, title=title, data={"cases": []})
    db.add(row); db.flush(); audit(db, p, "learning.set_create", row.id); return row


def add_case(db, p, set_id, expected, value):
    require(p, "admin", "operator")
    row = scoped(db, EvaluationSet, set_id, p, True); check_version(row, expected)
    if row.status != "draft" or len(row.data["cases"]) >= 100:
        raise HTTPException(409, "评测集已冻结或达到100例上限")
    if any(case["name"] == value["name"] for case in row.data["cases"]):
        raise HTTPException(409, "用例名称重复")
    for entity_id in value["entity_ids"]:
        entity = scoped(db, Entity, entity_id, p)
        if entity.schema_capability_id != row.package_id:
            raise HTTPException(422, "评测对象不属于该业务包")
    for memory_id in value["expected_memory_ids"]:
        memory = scoped(db, Memory, memory_id, p)
        if memory.status != "trusted" or memory.data.get("package_id") != row.package_id:
            raise HTTPException(422, "标签案例未核实或不属于该业务包")
    row.data = {**row.data, "cases": [*row.data["cases"], value]}; row.version += 1
    audit(db, p, "learning.case_save", row.id, {"name": value["name"]}); return row


def freeze_set(db, p, set_id, expected, reason):
    require(p, "admin")
    from .services import query_evidence
    row = scoped(db, EvaluationSet, set_id, p, True); check_version(row, expected)
    if row.status != "draft" or not {"normal", "no_answer", "conflict", "stale", "ambiguous"}.issubset({c["category"] for c in row.data["cases"]}):
        raise HTTPException(409, "冻结前需要正常、无答案、冲突、过期及对象未明确的五类用例")
    pool = [memory_view(m) for m in eligible(db, p, row.package_id)]
    snapshots, groups = [], set()
    for case in row.data["cases"]:
        evidence = query_evidence(db, p, case["entity_ids"], case["fields"], learning=False)
        snapshots.append({"case": case, "evidence": evidence})
        groups |= fingerprints(db, p, evidence)
        for memory_id in case["expected_memory_ids"]:
            memory = scoped(db, Memory, memory_id, p)
            groups.add("task:" + str(memory.task_id)); groups |= fingerprints(db, p, memory.data.get("evidence", {}))
    row.data = {**row.data, "snapshots": snapshots, "pool": pool,
                "evaluation_fingerprints": sorted(groups), "frozen_by": p.user_id, "reason": reason,
                "frozen_at": now().isoformat()}
    row.status = "frozen"; row.version += 1; row.content_hash = digest(row.data)
    audit(db, p, "learning.set_freeze", row.id, {"hash": row.content_hash}); return row


def check_set(row):
    if row.status != "frozen" or row.content_hash != digest(row.data):
        raise HTTPException(409, "冻结评测集缺失或内容已改变")


def check_pool(db, p, test_set):
    live = {m.id: memory_view(m) for m in eligible(db, p, test_set.package_id)}
    if any(m["id"] not in live or digest(live[m["id"]]) != digest(m) for m in test_set.data["pool"]):
        raise HTTPException(409, "冻结案例库的依据已纠正、撤回或过期，需重新建立评测集")


def channel(db, p, package_id, create=False):
    row = db.scalar(select(LearningChannel).where(LearningChannel.workspace_id == p.workspace_id,
        LearningChannel.package_id == package_id).with_for_update())
    if not row and create:
        # Serialize first channel creation against the package row.
        from .models import Capability
        scoped(db, Capability, package_id, p, True)
        row = db.scalar(select(LearningChannel).where(LearningChannel.workspace_id == p.workspace_id,
            LearningChannel.package_id == package_id).with_for_update())
        if not row:
            row = LearningChannel(workspace_id=p.workspace_id, package_id=package_id); db.add(row); db.flush()
    return row


def queue_evaluation(db, p, candidate_id, set_id, expected):
    require(p, "admin", "operator")
    candidate = scoped(db, LearningCandidate, candidate_id, p, True); check_version(candidate, expected)
    if candidate.status not in {"draft", "evaluated", "failed"}:
        raise HTTPException(409, "候选正在评测或已进入发布阶段")
    candidate_valid(db, p, candidate)
    test_set = scoped(db, EvaluationSet, set_id, p); check_set(test_set)
    if candidate.package_id != test_set.package_id:
        raise HTTPException(422, "候选和评测集业务包不同")
    if set(candidate.data["training_fingerprints"]) & set(test_set.data["evaluation_fingerprints"]):
        raise HTTPException(409, "训练与评测存在同任务、对象版本或原始文件泄漏")
    current = channel(db, p, candidate.package_id)
    base_id = current.active_deployment_id if current else None
    baseline = {"field_weights": {}, "max_cases": 10}
    if base_id:
        baseline = deployment_config(db, p, scoped(db, LearningDeployment, base_id, p))
    row = EvaluationRun(workspace_id=p.workspace_id, candidate_id=candidate.id, set_id=test_set.id,
        data={"candidate_hash": candidate.content_hash, "set_hash": test_set.content_hash,
              "baseline_deployment_id": base_id, "baseline_config": baseline, "actor_id": p.user_id})
    db.add(row); db.flush(); candidate.status = "evaluating"; candidate.version += 1
    db.add(Job(workspace_id=p.workspace_id, kind="evaluate_learning", idempotency_key="learning:"+row.id,
        data={"run_id": row.id, "actor_id": p.user_id}))
    audit(db, p, "learning.evaluate_queue", row.id); return row


def grade(status, ids, case):
    return status == case["expected_status"] and (not case["expected_memory_ids"] or bool(ids and ids[0] in case["expected_memory_ids"]))


def evaluate(db, p, run_id):
    row = scoped(db, EvaluationRun, run_id, p, True)
    if row.status != "queued":
        return
    candidate = scoped(db, LearningCandidate, row.candidate_id, p, True)
    test_set = scoped(db, EvaluationSet, row.set_id, p)
    candidate_valid(db, p, candidate); check_set(test_set)
    if row.data["candidate_hash"] != candidate.content_hash or row.data["set_hash"] != test_set.content_hash:
        raise HTTPException(409, "评测输入版本改变")
    config = candidate.data["config"]; results = []; pool = test_set.data["pool"]
    # Never reuse withdrawn/expired memory even in a frozen test.
    live = {m.id: memory_view(m) for m in eligible(db, p, candidate.package_id)}
    if any(m["id"] not in live or digest(live[m["id"]]) != digest(m) for m in pool):
        row.status = "invalidated"; candidate.status = "failed"; candidate.version += 1
        row.data = {**row.data, "error": "冻结案例库的依据已纠正、撤回或过期，需重新建立评测集"}; return
    started = perf_counter()
    for sample in test_set.data["snapshots"]:
        case, evidence = sample["case"], sample["evidence"]
        applicable = pool if case["entity_ids"] else []
        base = [m["id"] for m in rank(applicable, case["fields"], row.data["baseline_config"])]
        next_ids = [m["id"] for m in rank(applicable, case["fields"], config)]
        results.append({"name": case["name"], "category": case["category"], "status": evidence["status"],
            "expected_status": case["expected_status"], "expected_memory_ids": case["expected_memory_ids"],
            "baseline_ids": base, "candidate_ids": next_ids,
            "baseline_correct": grade(evidence["status"], base, case),
            "candidate_correct": grade(evidence["status"], next_ids, case)})
    baseline_score = sum(r["baseline_correct"] for r in results)
    candidate_score = sum(r["candidate_correct"] for r in results)
    safety_failed = any(not r["candidate_correct"] and r["category"] != "normal" for r in results)
    elapsed = round((perf_counter() - started)*1000, 3)
    passed = candidate_score > baseline_score and not safety_failed and elapsed < 1000
    row.status = "passed" if passed else "failed"
    row.data = {**row.data, "results": results, "metrics": {"cases": len(results),
        "baseline_correct": baseline_score, "candidate_correct": candidate_score,
        "safety_failed": safety_failed, "retrieval_ms": elapsed, "cloud_tokens": 0,
        "quality_scope": "frozen_experience_selection_only"}, "finished_at": now().isoformat()}
    candidate.status = "evaluated" if passed else "failed"; candidate.version += 1
    audit(db, p, "learning.evaluate_complete", row.id, {"status": row.status})


def deployment_config(db, p, deployment):
    candidate = scoped(db, LearningCandidate, deployment.candidate_id, p)
    candidate_valid(db, p, candidate)
    if deployment.approved_hash != candidate.content_hash:
        raise HTTPException(409, "发布候选与审批版本不一致")
    run = scoped(db, EvaluationRun, deployment.data["evaluation_run_id"], p)
    test_set = scoped(db, EvaluationSet, run.set_id, p)
    check_set(test_set); check_pool(db, p, test_set)
    if (run.status != "passed" or run.candidate_id != candidate.id
            or run.data["candidate_hash"] != candidate.content_hash
            or run.data["set_hash"] != test_set.content_hash):
        raise HTTPException(409, "发布所依据的评测版本已经失效")
    user = db.get(User, deployment.approved_by)
    member = db.scalar(select(Membership).where(Membership.user_id == deployment.approved_by,
        Membership.workspace_id == p.workspace_id))
    if not user or not user.active or not member or member.role != "admin":
        raise HTTPException(409, "发布批准人的权限已撤回")
    return candidate.data["config"]


def start_shadow(db, p, candidate_id, run_id, expected, reason):
    require(p, "admin")
    candidate = scoped(db, LearningCandidate, candidate_id, p, True); check_version(candidate, expected)
    candidate_valid(db, p, candidate)
    run = scoped(db, EvaluationRun, run_id, p); test_set = scoped(db, EvaluationSet, run.set_id, p)
    check_set(test_set); check_pool(db, p, test_set)
    if candidate.status != "evaluated" or run.candidate_id != candidate.id or run.status != "passed" or run.data["candidate_hash"] != candidate.content_hash or run.data["set_hash"] != test_set.content_hash:
        raise HTTPException(409, "需要该候选的冻结评测通过记录")
    current = channel(db, p, candidate.package_id, True)
    if current.shadow_deployment_id or current.active_deployment_id != run.data["baseline_deployment_id"]:
        raise HTTPException(409, "已有影子版本或基线已改变，需要重新评测")
    row = LearningDeployment(workspace_id=p.workspace_id, package_id=candidate.package_id,
        candidate_id=candidate.id, approved_by=p.user_id, approved_hash=candidate.content_hash,
        data={"evaluation_run_id": run.id, "baseline_deployment_id": current.active_deployment_id,
              "shadow_reason": reason, "request_count": 0})
    db.add(row); db.flush(); current.shadow_deployment_id = row.id; current.version += 1
    candidate.status = "shadow"; candidate.version += 1
    audit(db, p, "learning.shadow_start", row.id, {"reason": reason}); return row


def rollback(db, p, deployment, reason, automatic=False):
    if not automatic:
        require(p, "admin")
    current = channel(db, p, deployment.package_id, True)
    if current.active_deployment_id == deployment.id:
        base_id = deployment.data.get("baseline_deployment_id")
        try:
            if base_id:
                base = scoped(db, LearningDeployment, base_id, p); deployment_config(db, p, base)
                if base.status not in {"published"}:
                    base_id = None
        except HTTPException:
            base_id = None
        current.active_deployment_id = base_id
    elif current.shadow_deployment_id != deployment.id:
        raise HTTPException(409, "只能回滚当前生效或影子版本")
    if current.shadow_deployment_id == deployment.id:
        current.shadow_deployment_id = None
    current.version += 1; deployment.status = "rolled_back"; deployment.version += 1
    deployment.data = {**deployment.data, "rollback_reason": reason, "rolled_back_at": now().isoformat(), "automatic": automatic}
    candidate = scoped(db, LearningCandidate, deployment.candidate_id, p, True)
    candidate.status = "rolled_back"; candidate.version += 1
    audit(db, p, "learning.rollback", deployment.id, {"reason": reason, "automatic": automatic})


def trial_gate(db, p, deployment, stage):
    rows = db.scalars(select(LearningTrial).where(LearningTrial.workspace_id == p.workspace_id,
        LearningTrial.deployment_id == deployment.id, LearningTrial.status == "reviewed")).all()
    rows = [r for r in rows if r.data["stage"] == stage]
    if len({r.data["input_hash"] for r in rows}) < 3 or any(not r.data["review"]["candidate_correct"] for r in rows):
        raise HTTPException(409, "需要至少3组不同请求的已审核反馈，且候选不能出现核查失败")
    if stage == "canary" and not {"baseline", "candidate"}.issubset({r.data["served"] for r in rows}):
        raise HTTPException(409, "有限发布需要真实的候选组和对照组反馈")


def advance(db, p, deployment_id, expected, reason, stage, percent=10, max_requests=30):
    require(p, "admin")
    deployment = scoped(db, LearningDeployment, deployment_id, p, True); check_version(deployment, expected)
    deployment_config(db, p, deployment)
    current = channel(db, p, deployment.package_id, True)
    if stage == "canary":
        if deployment.status != "shadow" or current.shadow_deployment_id != deployment.id:
            raise HTTPException(409, "此版本尚未处于影子阶段")
        trial_gate(db, p, deployment, "shadow")
        if not 1 <= percent <= 25 or not 3 <= max_requests <= 100:
            raise HTTPException(422, "有限发布比例最多25%，请求额度为3至100")
        current.shadow_deployment_id = None; current.active_deployment_id = deployment.id
        deployment.data = {**deployment.data, "percent": percent, "max_requests": max_requests,
                           "request_count": 0, "canary_reason": reason}
    else:
        if deployment.status != "canary" or current.active_deployment_id != deployment.id:
            raise HTTPException(409, "需要当前有限发布版本")
        trial_gate(db, p, deployment, "canary")
        deployment.data = {**deployment.data, "publish_reason": reason}
    deployment.status = stage; deployment.version += 1; current.version += 1
    candidate = scoped(db, LearningCandidate, deployment.candidate_id, p, True)
    candidate.status = stage; candidate.version += 1
    audit(db, p, "learning."+stage, deployment.id, {"reason": reason}); return deployment


def select_cases(db, p, packages, fields, context="", route_key=None, result_status="sufficient", entity_ids=None):
    selected, metadata = [], []
    for package_id in sorted(packages):
        pool = [memory_view(m) for m in eligible(db, p, package_id)]
        base_config = {"field_weights": {}, "max_cases": 10}
        current = channel(db, p, package_id)
        deployment = None
        if current:
            deploy_id = current.shadow_deployment_id or current.active_deployment_id
            if deploy_id:
                deployment = scoped(db, LearningDeployment, deploy_id, p, True)
                try:
                    config = deployment_config(db, p, deployment)
                    base_id = deployment.data.get("baseline_deployment_id")
                    if base_id:
                        base_config = deployment_config(db, p, scoped(db, LearningDeployment, base_id, p))
                except HTTPException:
                    rollback(db, p, deployment, "依据或批准权限已失效，停止使用候选", automatic=True)
                    deployment = None
        if not deployment:
            active_id = current.active_deployment_id if current else None
            if active_id:
                active = scoped(db, LearningDeployment, active_id, p)
                base_config = deployment_config(db, p, active)
            selected.extend(rank(pool, fields, base_config)); continue
        key = digest(str(route_key or uid()))
        trial = db.scalar(select(LearningTrial).where(LearningTrial.workspace_id == p.workspace_id,
            LearningTrial.deployment_id == deployment.id, LearningTrial.request_key == key).with_for_update())
        if deployment.status == "canary" and not trial and deployment.data["request_count"] >= deployment.data["max_requests"]:
            rollback(db, p, deployment, "有限发布请求额度已用完，恢复基线", automatic=True)
            selected.extend(rank(pool, fields, base_config)); continue
        baseline = rank(pool, fields, base_config); candidate = rank(pool, fields, config)
        served = "candidate" if deployment.status == "published" else "baseline"
        if deployment.status == "canary":
            served = "candidate" if int(digest([p.workspace_id, deployment.id, key])[:8],16)%100 < deployment.data["percent"] else "baseline"
        request = {"entity_package": package_id, "fields": fields, "context": context,
                   "status": result_status, "entity_ids": entity_ids or []}
        if not trial:
            trial = LearningTrial(workspace_id=p.workspace_id, deployment_id=deployment.id, request_key=key,
                data={"stage": deployment.status, "input_hash": digest(request), "request": request,
                    "baseline_ids": [m["id"] for m in baseline], "candidate_ids": [m["id"] for m in candidate],
                    "served": served, "status_at_query": result_status,
                    "case_snapshots": {m["id"]:m for m in [*baseline,*candidate]}})
            db.add(trial); deployment.data = {**deployment.data,"request_count":deployment.data["request_count"]+1}
        elif trial.data["input_hash"] != digest(request):
            raise HTTPException(409, "进化请求编号已用于不同需求")
        selected.extend(candidate if served == "candidate" else baseline)
        metadata.append({"deployment_id": deployment.id, "stage": deployment.status,
                         "served": served, "request_key": key})
    return [{"id":m["id"],"text":m["text"],"provenance":m["data"]} for m in selected[:10]], metadata


def review_trial(db, p, trial_id, expected, expected_status, memory_ids, reason):
    require(p, "admin", "operator")
    trial = scoped(db, LearningTrial, trial_id, p, True); check_version(trial, expected)
    if trial.status != "pending_review":
        raise HTTPException(409, "此反馈已经保存")
    deployment = scoped(db, LearningDeployment, trial.deployment_id, p, True)
    for memory_id in memory_ids:
        memory = scoped(db, Memory, memory_id, p)
        if memory.status != "trusted" or memory.data.get("package_id") != deployment.package_id:
            raise HTTPException(422, "核查标签必须来自该业务包可信案例")
    case = {"expected_status":expected_status,"expected_memory_ids":memory_ids}
    review = {"expected_status":expected_status,"expected_memory_ids":memory_ids,"reason":reason,"reviewed_by":p.user_id,
              "baseline_correct":grade(trial.data["status_at_query"],trial.data["baseline_ids"],case),
              "candidate_correct":grade(trial.data["status_at_query"],trial.data["candidate_ids"],case)}
    trial.data = {**trial.data,"review":review}; trial.status = "reviewed"; trial.version += 1
    audit(db,p,"learning.trial_review",trial.id)
    if not review["candidate_correct"] and deployment.status in {"canary","published"}:
        rollback(db,p,deployment,"在线核查发现候选不符合已保存标签，自动恢复基线",automatic=True)
    return trial
