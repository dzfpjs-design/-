"""Authenticated API for the isolated UAV simulator; no physical control."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .db import session
from .device_gateway_store_v268 import member
from .device_simulator_v175 import DeviceLedger
from .security import Principal, authenticated
from .uav_simulated_missions import SimulatedMissionService

router = APIRouter(prefix='/api/uav-simulation', tags=['模拟无人机任务'])
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def service(p: P):
    # Workspace ID comes from authentication, not caller input. Separate
    # simulator per workspace prevents competing for another tenant's aircraft.
    try:
        identifier = str(UUID(p.workspace_id))
        if identifier != p.workspace_id:
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(403, '工作区标识无效') from None
    root = settings().storage_dir / 'uav-simulation' / identifier
    return SimulatedMissionService(root / 'missions.sqlite', DeviceLedger(root / 'device.sqlite'))


S = Annotated[SimulatedMissionService, Depends(service)]


class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: str = Field(min_length=36, max_length=36)
    event_id: str = Field(min_length=1, max_length=128)
    start: list[float] = Field(min_length=2, max_length=2)
    destination: list[float] = Field(min_length=2, max_length=2)
    speed_m_s: float = Field(default=10, ge=1, le=15)
    observation_seconds: int = Field(default=60, ge=1, le=600)


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirm: bool


@router.get('/missions')
def missions(p: P, db: DB, s: S):
    member(db, p)
    return {'simulated': True, 'missions': s.list(p.workspace_id)}


@router.post('/missions')
def prepare(body: Plan, p: P, db: DB, s: S):
    member(db, p, admin=True)
    return s.prepare(p.workspace_id, body.event_id, body.start, body.destination,
                     speed_m_s=body.speed_m_s, observation_seconds=body.observation_seconds,
                     request_id=body.request_id)


@router.get('/missions/{mission_id}')
def mission(mission_id: str, p: P, db: DB, s: S):
    member(db, p)
    return s.get(p.workspace_id, mission_id)


@router.post('/missions/{mission_id}/refresh')
def refresh(mission_id: str, p: P, db: DB, s: S):
    member(db, p)
    return s.refresh(p.workspace_id, mission_id)


@router.post('/missions/{mission_id}/start')
def start(mission_id: str, body: Approval, p: P, db: DB, s: S):
    member(db, p, admin=True)
    if body.confirm is not True:
        raise HTTPException(422, '请明确确认后派发模拟任务')
    return s.approve_and_start(p.workspace_id, mission_id, manager=p.user_id, plan_hash=body.plan_hash)


@router.post('/missions/{mission_id}/return')
def return_to_dock(mission_id: str, body: Confirm, p: P, db: DB, s: S):
    member(db, p, admin=True)
    if body.confirm is not True:
        raise HTTPException(422, '请明确确认后发起模拟返航')
    return s.confirm_return(p.workspace_id, mission_id, manager=p.user_id)
