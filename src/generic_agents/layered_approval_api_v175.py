"""Authenticated durable approval commands. No route performs adapter I/O."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from .setup_api import P, DB
from .models import Task
from .services import scoped
from .layered_approval_contract_v175 import (original_json, UnitCommand,
    RoleCommand, PolicyCommand, TaskCommand, ReviseCommand, DecisionCommand,
    AcceptanceCommand)
from . import layered_approval_store_v175 as store
from .signal_dual_read_session import read_session

router = APIRouter()
TaskReadDB = Annotated[object, Depends(read_session)]


async def command(request, model):
    try:
        return original_json(await request.body(), model)
    except (ValueError, TypeError):
        raise HTTPException(422, '审批请求字段、类型或原正文不符合要求，请保留原输入核对') from None


@router.get('/approval-settings-v175')
def settings(p: P, db: DB):
    return store.settings(db, p)


@router.get('/approval-settings-v175/requests/{request_id}')
def original(request_id: str, p: P, db: DB,
        expected_sha256: str = Query(pattern=r'^[0-9a-f]{64}$')):
    return store.request_result(db, p, request_id, expected_sha256)


async def setting(request, model, kind, p, db):
    body, raw, sha = await command(request, model)
    def apply():
        value = store.configure(db, p, kind, body, raw, sha)
        db.commit()
        return value
    return await run_in_threadpool(apply)


@router.post('/approval-settings-v175/units')
async def unit(request: Request, p: P, db: DB):
    return await setting(request, UnitCommand, 'unit', p, db)


@router.post('/approval-settings-v175/roles')
async def role(request: Request, p: P, db: DB):
    return await setting(request, RoleCommand, 'role', p, db)


@router.post('/approval-settings-v175/policies')
async def policy(request: Request, p: P, db: DB):
    return await setting(request, PolicyCommand, 'policy', p, db)


@router.get('/tasks/{identity}/approval-v175')
def detail(identity: str, p: P, db: TaskReadDB):
    from .report_full_source_read_scope import task_full_source_read_scope
    with task_full_source_read_scope(db, p, identity):
        return store.detail(db, p, scoped(db, Task, identity, p))


async def task_command(identity, request, model, operation, p, db):
    body, raw, sha = await command(request, model)
    def apply():
        try:
            result = store.perform(db, p, identity, operation, body, raw, sha)
        except ValueError:
            raise HTTPException(409, '当前审批状态或提交内容不允许本次操作，原记录与输入保留') from None
        db.commit()
        return result
    return await run_in_threadpool(apply)


@router.post('/tasks/{identity}/approval-v175/revise')
async def revise(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, ReviseCommand, 'revise', p, db)


@router.post('/tasks/{identity}/approval-v175/submit')
async def submit(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, TaskCommand, 'submit', p, db)


@router.post('/tasks/{identity}/approval-v175/decide')
async def decide(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, DecisionCommand, 'decide', p, db)


@router.post('/tasks/{identity}/approval-v175/withdraw')
async def withdraw(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, TaskCommand, 'withdraw', p, db)


@router.post('/tasks/{identity}/approval-v175/start')
async def start(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, TaskCommand, 'start', p, db)


@router.post('/tasks/{identity}/approval-v175/resubmit-acceptance')
async def resubmit_acceptance(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, TaskCommand, 'resubmit_acceptance', p, db)


@router.post('/tasks/{identity}/approval-v175/accept')
async def accept(identity: str, request: Request, p: P, db: DB):
    return await task_command(identity, request, AcceptanceCommand, 'accept', p, db)
