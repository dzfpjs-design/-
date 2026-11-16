"""Human-operated evaluation and rollout endpoints; no executable configuration uploads."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from .db import session
from .security import Principal, authenticated, require
from .contracts import Contract, VersionAction
from .models import (LearningCandidate, EvaluationSet, EvaluationRun, LearningDeployment, LearningChannel,
                     LearningTrial, Memory, uid)
from .services import scoped, as_dict, audit, check_version
from . import learning

router = APIRouter(prefix="/api/learning")
DB = Annotated[object, Depends(session)]
P = Annotated[Principal, Depends(authenticated)]


class CandidateCreate(Contract):
    package_id: str
    title: str = Field(min_length=1,max_length=200)
    memory_ids: list[str] = Field(min_length=1,max_length=50)
    config: learning.SelectionConfig | None = None


class SetCreate(Contract):
    package_id: str
    title: str = Field(min_length=1,max_length=200)


class TestCase(Contract):
    name: str = Field(min_length=1,max_length=200)
    category: Literal["normal","no_answer","conflict","stale","ambiguous"]
    entity_ids: list[str] = Field(max_length=10)
    fields: list[str] = Field(max_length=30)
    expected_status: Literal["sufficient","insufficient","conflict"]
    expected_memory_ids: list[str] = Field(default_factory=list,max_length=5)
    label_reason: str = Field(min_length=1,max_length=2000)


class CaseSave(VersionAction):
    case: TestCase


class EvaluationStart(VersionAction):
    set_id: str


class ShadowStart(VersionAction):
    run_id: str


class CanaryStart(VersionAction):
    percent: int = Field(default=10,ge=1,le=25)
    max_requests: int = Field(default=30,ge=3,le=100)


class TrialReview(VersionAction):
    expected_status: Literal["sufficient","insufficient","conflict"]
    expected_memory_ids: list[str] = Field(default_factory=list,max_length=5)


@router.get("/overview")
def overview(p:P, db:DB):
    def rows(model):
        return [as_dict(r) for r in db.scalars(select(model).where(model.workspace_id==p.workspace_id)
                                             .order_by(model.created_at.desc()).limit(100))]
    return {"candidates":rows(LearningCandidate),"sets":rows(EvaluationSet),"runs":rows(EvaluationRun),
            "deployments":rows(LearningDeployment),"channels":rows(LearningChannel),"trials":rows(LearningTrial)}


@router.post("/candidates",status_code=201)
def candidate(body:CandidateCreate,p:P,db:DB):
    row=learning.propose_candidate(db,p,body.package_id,body.memory_ids,body.title,
                                  body.config.model_dump() if body.config else None)
    db.commit();return as_dict(row)


@router.post("/sets",status_code=201)
def test_set(body:SetCreate,p:P,db:DB):
    row=learning.create_set(db,p,body.package_id,body.title);db.commit();return as_dict(row)


@router.post("/sets/{set_id}/cases")
def case_save(set_id:str,body:CaseSave,p:P,db:DB):
    row=learning.add_case(db,p,set_id,body.expected_version,{**body.case.model_dump(),"case_id":uid()})
    db.commit();return as_dict(row)


@router.post("/sets/{set_id}/cases/{case_id}/remove")
def case_remove(set_id:str,case_id:str,body:VersionAction,p:P,db:DB):
    require(p,"admin","operator");row=scoped(db,EvaluationSet,set_id,p,True);check_version(row,body.expected_version)
    if row.status!="draft":raise HTTPException(409,"冻结后不能移除用例")
    case=next((case for case in row.data["cases"] if case["case_id"]==case_id),None)
    if not case:raise HTTPException(404,"用例不存在")
    row.data={**row.data,"cases":[c for c in row.data["cases"] if c["case_id"]!=case_id],
              "removed_cases":[*row.data.get("removed_cases",[]),case]};row.version+=1
    audit(db,p,"learning.case_remove",row.id,{"case_id":case_id,"reason":body.reason});db.commit();return as_dict(row)


@router.post("/sets/{set_id}/restore")
def case_restore(set_id:str,body:VersionAction,p:P,db:DB):
    require(p,"admin","operator");row=scoped(db,EvaluationSet,set_id,p,True);check_version(row,body.expected_version)
    removed=row.data.get("removed_cases",[])
    if row.status!="draft" or not removed:raise HTTPException(409,"没有可恢复的草稿用例")
    case=removed[-1]
    if any(c["name"]==case["name"] for c in row.data["cases"]):raise HTTPException(409,"同名用例已存在，请先核对")
    row.data={**row.data,"cases":[*row.data["cases"],case],"removed_cases":removed[:-1]};row.version+=1
    audit(db,p,"learning.case_restore",row.id,{"reason":body.reason});db.commit();return as_dict(row)


@router.post("/sets/{set_id}/freeze")
def freeze(set_id:str,body:VersionAction,p:P,db:DB):
    row=learning.freeze_set(db,p,set_id,body.expected_version,body.reason);db.commit();return as_dict(row)


@router.post("/candidates/{candidate_id}/evaluate",status_code=202)
def evaluate(candidate_id:str,body:EvaluationStart,p:P,db:DB):
    row=learning.queue_evaluation(db,p,candidate_id,body.set_id,body.expected_version);db.commit();return as_dict(row)


@router.post("/candidates/{candidate_id}/shadow")
def shadow(candidate_id:str,body:ShadowStart,p:P,db:DB):
    row=learning.start_shadow(db,p,candidate_id,body.run_id,body.expected_version,body.reason);db.commit();return as_dict(row)


@router.post("/deployments/{deployment_id}/canary")
def canary(deployment_id:str,body:CanaryStart,p:P,db:DB):
    row=learning.advance(db,p,deployment_id,body.expected_version,body.reason,"canary",body.percent,body.max_requests)
    db.commit();return as_dict(row)


@router.post("/deployments/{deployment_id}/publish")
def publish(deployment_id:str,body:VersionAction,p:P,db:DB):
    row=learning.advance(db,p,deployment_id,body.expected_version,body.reason,"published");db.commit();return as_dict(row)


@router.post("/deployments/{deployment_id}/rollback")
def rollback(deployment_id:str,body:VersionAction,p:P,db:DB):
    row=scoped(db,LearningDeployment,deployment_id,p,True);check_version(row,body.expected_version)
    learning.rollback(db,p,row,body.reason);db.commit();return as_dict(row)


@router.post("/trials/{trial_id}/review")
def review(trial_id:str,body:TrialReview,p:P,db:DB):
    row=learning.review_trial(db,p,trial_id,body.expected_version,body.expected_status,body.expected_memory_ids,body.reason)
    db.commit();return as_dict(row)


@router.post("/memories/{memory_id}/revoke")
def revoke(memory_id:str,body:VersionAction,p:P,db:DB):
    require(p,"admin","operator");row=scoped(db,Memory,memory_id,p,True);check_version(row,body.expected_version)
    if row.status!="trusted":raise HTTPException(409,"此案例尚未生效或已经撤回")
    row.status="revoked";row.version+=1;row.data={**row.data,"revoked_by":p.user_id,"revoke_reason":body.reason}
    audit(db,p,"memory.revoke",row.id,{"reason":body.reason});db.commit();return as_dict(row)
