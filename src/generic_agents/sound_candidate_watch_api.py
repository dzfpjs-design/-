"""Explicit environmental-sound candidate watches; raw commands have read-only recovery."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Path
from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator
from .contracts import Contract
from .db import session
from .security import authenticated, Principal
from . import sound_candidate_watches as store

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter()
BASE = '/api/workspace/instances/{identity}/sound-candidate-watches'
SHA = r'^[0-9a-f]{64}$'
UUID = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
Resource = Annotated[str, Path(pattern=UUID)]

class TargetPins(Contract):
    instance_version: StrictInt = Field(ge=1)
    release_id: StrictStr = Field(pattern=UUID)
    configuration_hash: StrictStr = Field(pattern=SHA)
    business_id: StrictStr = Field(min_length=1, max_length=200)
    entity_id: StrictStr = Field(pattern=UUID)
    entity_version: StrictInt = Field(ge=1)
    entity_definition_hash: StrictStr = Field(pattern=SHA)
    capability_id: StrictStr = Field(pattern=UUID)
    capability_version: StrictInt = Field(ge=1)
    capability_hash: StrictStr = Field(pattern=SHA)
    labels_sha256: StrictStr = Field(pattern=SHA)

class Create(Contract):
    schema: Literal['sound-candidate-watch-create-v1']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    name: StrictStr = Field(min_length=1, max_length=120)
    target_pins: TargetPins
    class_index: StrictInt = Field(ge=0, le=520)
    min_score_text: StrictStr = Field(min_length=1, max_length=80)
    min_windows: StrictInt = Field(ge=1, le=124)
    confirm_candidate_only: StrictBool

    @field_validator('min_score_text')
    @classmethod
    def score(cls, value):
        store.score(value)
        return value

    @field_validator('name')
    @classmethod
    def name_value(cls, value):
        if not value.strip(): raise ValueError('请填写规则名称')
        return value

    @field_validator('confirm_candidate_only')
    @classmethod
    def confirmation(cls, value):
        if value is not True: raise ValueError('请确认仅通知待核查候选')
        return value

class Status(Contract):
    schema: Literal['sound-candidate-watch-status-v1']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)
    status: Literal['enabled', 'paused']
    target_pins: TargetPins | None

class Check(Contract):
    schema: Literal['sound-candidate-watch-check-v1']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)
    target_pins: TargetPins
    before_run_id: StrictStr | None = Field(default=None, pattern=UUID)
    confirm_existing_received_only: StrictBool

    @field_validator('confirm_existing_received_only')
    @classmethod
    def confirmation(cls, value):
        if value is not True: raise ValueError('请明确确认仅检查已接收的历史成果')
        return value

async def raw_body(request):
    value = await request.body()
    if len(value) > store.MAX_RAW: raise HTTPException(413, '规则申请正文过长')
    return value

@router.get(BASE + '/options')
def options(identity: Resource, p: P, db: DB):
    return store.options(db, p, identity)

@router.get(BASE + '/requests')
def original(identity: Resource, p: P, db: DB, request_id: str = Query(min_length=12, max_length=100),
        raw_sha256: str = Query(pattern=SHA), expected_target: str = Query(max_length=4096)):
    return store.recovery(db, p, identity, request_id, raw_sha256, expected_target=expected_target)

@router.get(BASE)
def listing(identity: Resource, p: P, db: DB, limit: int = Query(20, ge=1, le=50), before_id: str | None = Query(default=None, pattern=UUID)):
    return store.list_rules(db, p, identity, limit=limit, before_id=before_id)

@router.post(BASE)
async def create(identity: Resource, request: Request, p: P, db: DB):
    result = store.create(db, p, identity, await raw_body(request)); db.commit()
    return result

@router.get(BASE + '/{watch_id}')
def detail(identity: Resource, watch_id: Resource, p: P, db: DB):
    return store.view(db, p, store.watch_row(db, p, identity, watch_id))

@router.get(BASE + '/{watch_id}/requests')
def command_original(identity: Resource, watch_id: Resource, p: P, db: DB,
        request_id: str = Query(min_length=12, max_length=100), raw_sha256: str = Query(pattern=SHA),
        operation: Literal['status', 'check_existing'] = 'status', expected_version: int = Query(ge=1),
        expected_target: str | None = Query(default=None, max_length=4096)):
    return store.recovery(db, p, identity, request_id, raw_sha256, watch_id=watch_id,
        operation=operation, expected_version=expected_version, expected_target=expected_target)

@router.post(BASE + '/{watch_id}/status')
async def status(identity: Resource, watch_id: Resource, request: Request, p: P, db: DB):
    result = store.status_command(db, p, identity, watch_id, await raw_body(request)); db.commit()
    return result

@router.post(BASE + '/{watch_id}/check-existing')
async def check(identity: Resource, watch_id: Resource, request: Request, p: P, db: DB):
    result = store.check_existing(db, p, identity, watch_id, await raw_body(request)); db.commit()
    return result

@router.get(BASE + '/{watch_id}/checks')
def checks(identity: Resource, watch_id: Resource, p: P, db: DB):
    return store.checks(db, p, store.watch_row(db, p, identity, watch_id))

@router.get('/api/workspace/instances/{identity}/sound-candidate-notifications/{event_id}')
def evidence(identity: Resource, event_id: Resource, p: P, db: DB):
    return store.notification_evidence(db, p, identity, event_id)
