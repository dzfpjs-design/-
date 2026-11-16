"""Workspace-scoped ONVIF discovery and explicit candidate confirmation."""
import hashlib
import re
from fastapi import APIRouter, HTTPException, Response
from pydantic import Field
from sqlalchemy import select, text
from .contracts import Contract
from .models import Job, now
from .services import audit, require, scoped
from .setup_api import P, DB
from .onvif_discovery import discover

router = APIRouter(prefix='/api/media/onvif')
KIND = 'onvif_discovery_scan'


class Scan(Contract):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    local_network_authorized: bool


class Confirmation(Contract):
    candidate_ids: list[str] = Field(min_length=1, max_length=100)
    candidate_confirmation: bool


def _view(row):
    if row.kind != KIND or row.status != 'internal' or not isinstance(row.data, dict):
        raise HTTPException(409, '扫描记录无法核对')
    return {'id':row.id, 'workspace_id':row.workspace_id, 'created_at':row.created_at.isoformat(), 'version':row.version, **row.data}


def _lock(db, workspace_id):
    key = int.from_bytes(hashlib.sha256(('onvif-discovery:'+workspace_id).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':key})


@router.get('/scans')
def scans(p:P, db:DB):
    require(p, 'admin')
    rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id, Job.kind==KIND, Job.status=='internal').order_by(Job.created_at.desc()).limit(20)).all()
    return [_view(row) for row in rows]


@router.get('/scans/requests/{request_id}')
def lookup(request_id:str, p:P, db:DB):
    require(p, 'admin')
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',request_id):
        raise HTTPException(422,'扫描请求标识无效')
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id, Job.idempotency_key=='onvif-discovery:'+request_id))
    if not row:
        raise HTTPException(404,'尚未找到此扫描请求')
    result=_view(row)
    if result.get('request_id')!=request_id or result.get('actor_id')!=p.user_id:
        raise HTTPException(409,'原扫描请求身份不一致')
    return result


@router.post('/scans')
def scan(body:Scan, response:Response, p:P, db:DB):
    require(p, 'admin')
    if body.local_network_authorized is not True:
        raise HTTPException(422, '请确认有权发现运行节点所在局域网的设备')
    _lock(db, p.workspace_id)
    key='onvif-discovery:'+body.request_id
    old=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
    if old:
        value=_view(old)
        if value.get('request_id') != body.request_id or value.get('actor_id') != p.user_id:
            raise HTTPException(409, '原扫描请求身份不一致')
        response.status_code=200
        return value
    try:
        result=discover()
    except OSError:
        raise HTTPException(503, '本机无法发送或接收局域网发现报文，请检查网卡、组播和防火墙。')
    row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=key,
            data={'request_id':body.request_id,'actor_id':p.user_id,'devices':result['devices'],
                  'packets_limited':result['packets_limited'],'confirmed_ids':[], 'checked_at':now().isoformat()})
    db.add(row);db.flush();audit(db,p,'media.onvif.discovered',row.id,{'candidate_count':len(result['devices'])});db.commit()
    response.status_code=201
    return _view(row)


@router.post('/scans/{scan_id}/confirm')
def confirm(scan_id:str, body:Confirmation, p:P, db:DB):
    require(p,'admin')
    if body.candidate_confirmation is not True:
        raise HTTPException(422,'请确认要记录这些未验证的候选设备')
    if len(set(body.candidate_ids)) != len(body.candidate_ids) or any(not re.fullmatch('[0-9a-f]{32}',i) for i in body.candidate_ids):
        raise HTTPException(422,'设备选择无效，请重新读取扫描结果')
    row=scoped(db,Job,scan_id,p,True); view=_view(row)
    current={x.get('candidate_id') for x in view.get('devices',[]) if isinstance(x,dict)}
    if not set(body.candidate_ids).issubset(current):
        raise HTTPException(409,'所选设备不属于这次扫描，请重新读取')
    previous=set(row.data.get('confirmed_ids',[])); updated=previous | set(body.candidate_ids)
    if updated != previous:
        row.data={**row.data,'confirmed_ids':sorted(updated),'last_confirmed_by':p.user_id,'last_confirmed_at':now().isoformat()}
        row.version+=1;audit(db,p,'media.onvif.candidates_confirmed',row.id,{'candidate_count':len(updated-previous)});db.commit()
    return _view(row)
