"""Human-confirmed native AudioRun registrations, separate from processing v1."""
import copy
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select

from .contracts import Contract, digest
from .db import session
from .models import Capability, DomainInstance, SpecialistInstallation, uid
from .graph_event_models import GraphEventProducer
from .graph_event_admission import check_producer
from .graph_event_source_families_v136 import NATIVE_AUDIO, NATIVE_RECORD, native_command_hash, record_family
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, scoped
from . import graph_event_native_producer_requests_v136 as requests

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/instances/{identity}/graph-event-native-producers')


class Register(Contract):
    schema: Literal['graph-event-native-audio-producer-command-v1']
    request_id: UUID
    label: str = Field(strict=True, min_length=1, max_length=200)
    adapter_id: Literal['native-audio-transcription-v1']
    instance_id: UUID
    source_id: UUID
    source_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    installation_id: UUID
    installation_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')

    @field_validator('label')
    @classmethod
    def meaningful_label(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请填写来源名称')
        value.encode('utf-8'); return value


def wire(value): return requests.wire(value)


def _instance(db, p, identity): return scoped(db, DomainInstance, str(identity), p)


def public(db, p, instance_id, row):
    contract = check_producer(row)
    if record_family(row) != NATIVE_AUDIO or contract['instance_id'] != instance_id:
        raise HTTPException(409, '此原请求属于另一实例或另一来源协议，请从原入口核对')
    from .graph_event_source_families_v136 import verify_native_registration
    try: verify_native_registration(db, p, row)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise HTTPException(409, '原生来源登记与实际确认记录不一致；原记录保留') from None
    return {'id': row.id, 'label': row.data['label'], 'status': row.status, 'version': row.version,
        'contract_hash': row.contract_hash, 'contract': contract,
        'registration': copy.deepcopy(row.data['registration']),
        'execution_granted': False, 'physical_state_verified': False}


def proposed(db, p, identity, body):
    instance_id = str(identity)
    if str(body.instance_id) != instance_id: raise HTTPException(409, '登记正文与当前实例不一致')
    from .graph_event_native_audio_sources_v136 import build_contract
    try:
        contract = build_contract(db, p, instance_id, str(body.source_id), str(body.installation_id), lock=True)
    except ValueError:
        raise HTTPException(409, '本地语音来源或原模型安装版本无法核对；原稿保留') from None
    if contract['source_hash'] != body.source_hash or contract['installation_hash'] != body.installation_hash:
        raise HTTPException(409, '来源或模型安装版本已有变化，请重新读取后核对')
    return contract


def _source_display_name(instance):
    name = instance.data.get('name')
    name = name.strip() if type(name) is str else ''
    internal_names = (instance.id, instance.id.replace('-', ''), instance.namespace)
    if not name or any(type(value) is str and value and value.casefold() in name.casefold()
        for value in internal_names):
        name = '当前实例'
    return name[:80] + '的本地语音结果'


@router.get('/options')
def options(identity: UUID, p: P, db: DB, after: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    instance = _instance(db, p, identity)
    source_name = _source_display_name(instance)
    from .local_audio_install import PACK, installed, trace
    query = select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.kind == 'source',
        Capability.namespace == 'platform_native_receipts', Capability.status == 'published',
        Capability.data['specification']['managed_instance'].as_string() == str(identity))
    if after is not None: query = query.where(Capability.id > str(after))
    rows = list(db.scalars(query.order_by(Capability.id).limit(limit + 1)))
    sources = []
    for row in rows[:limit]:
        if (row.content_hash == row.validated_hash == digest(row.data)
            and row.data.get('execution', {}).get('adapter') == 'upload'
            and row.data.get('data_policy', {}).get('cloud_allowed') is False
            and row.data.get('data_policy', {}).get('external_allowed') is False):
            sources.append({'id': row.id, 'name': source_name, 'content_hash': row.content_hash, 'release_version': row.release_version})
    installations = []
    current = db.scalar(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id == p.workspace_id,
        SpecialistInstallation.pack_id == PACK))
    if current is not None:
        try:
            value = trace(installed(db, p, current.id))
            installations.append({'id': current.id, 'name': '本地整片语音转写', 'installation_hash': digest(value), 'trace': value})
        except (HTTPException, ValueError, KeyError, TypeError, OSError): pass
    active = instance.status == 'partially_active' and instance.active_release_id is not None
    reason = ('请先启用当前实例；原来源与处理记录仍保留。' if not active
        else '请先完成实际本地语音处理、关联对象并明确归集，随后登记已存在的处理来源。' if not sources
        else '请先安装并核对本地整片语音模型；不会自动下载或转为云调用。' if not installations
        else '当前账号可查看已有来源；登记须由管理员明确确认。' if p.role != 'admin' else None)
    return wire({'schema': 'graph-event-native-audio-producer-options-v1', 'instance_id': str(identity),
        'sources': sources, 'installations': installations,
        'next_after': rows[limit - 1].id if len(rows) > limit else None,
        'can_register': p.role == 'admin' and active, 'reason': reason})


@router.post('/preview')
def preview(identity: UUID, body: Register, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p); _instance(db, p, identity)
    value = body.model_dump(mode='json'); contract = proposed(db, p, identity, body)
    result = {'schema': 'graph-event-native-audio-producer-preview-v1', 'instance_id': str(identity),
        'command': value, 'contract': contract, 'contract_hash': digest(contract),
        'command_hash': native_command_hash(p, value), 'execution_granted': False,
        'meaning': '仅供应已保存的机器转写候选；不证明文字正确、录制时刻、现场覆盖或对象状态。业务等待仍须单独确认。'}
    db.commit(); return wire(result)


@router.post('', status_code=201)
def register(identity: UUID, body: Register, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p); _instance(db, p, identity)
    instance_id = str(identity); raw = body.model_dump(mode='json')
    if raw['instance_id'] != instance_id: raise HTTPException(409, '原登记正文与当前实例不一致')
    signature = native_command_hash(p, raw)
    requests.lock_original(db, p, 'register', raw['request_id'])
    requests.guard_not_retired(db, p, instance_id, 'register', raw)
    from .graph_event_request_recovery_v123 import guard_not_retired as old_retired
    old_retired(db, p, 'register', raw)
    prior = db.scalar(select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.request_id == raw['request_id']).execution_options(populate_existing=True))
    if prior is not None:
        if prior.registered_by != p.user_id or prior.command_hash != signature:
            raise HTTPException(409, '该编号已有另一份原登记命令；不得改换来源协议或正文')
        result = public(db, p, instance_id, prior); db.commit(); return wire({'producer': result, 'created': False})
    contract = proposed(db, p, identity, body)
    row = GraphEventProducer(id=uid(), workspace_id=p.workspace_id, registered_by=p.user_id,
        request_id=raw['request_id'], command_hash=signature, contract_hash=digest(contract), status='active',
        data={'schema': NATIVE_RECORD, 'label': raw['label'], 'contract': contract, 'contract_hash': digest(contract),
            'registration': {'request_id': raw['request_id'], 'actor_id': p.user_id, 'command_hash': signature, 'command': raw}})
    db.add(row); db.flush()
    audit(db, p, 'graph_event.native_producer_registered_v136', row.id,
        {'instance_id': instance_id, 'request_id': row.request_id, 'command_hash': row.command_hash, 'contract_hash': row.contract_hash})
    db.flush(); result = public(db, p, instance_id, row)
    db.commit(); return wire({'producer': result, 'created': True})


@router.get('/requests/{request_id}')
def request(identity: UUID, request_id: UUID, p: P, db: DB):
    _instance(db, p, identity)
    row = db.scalar(select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.request_id == str(request_id), GraphEventProducer.registered_by == p.user_id))
    return wire({'found': row is not None, 'producer': public(db, p, str(identity), row) if row else None})


def item(db, p, instance_id, row):
    value = public(db, p, instance_id, row)
    from .graph_event_catalog_v122 import current_availability
    scoped(db, Capability, value['contract']['source_id'], p)
    return {**value, 'source_name': _source_display_name(_instance(db, p, instance_id)),
        'installation_name': '本地整片语音转写',
        'current_availability': current_availability(db, p, row, value['contract']), 'receipt_verified': False}


@router.get('')
def listing(identity: UUID, p: P, db: DB, after: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    _instance(db, p, identity)
    query = select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.data['contract']['instance_id'].as_string() == str(identity),
        GraphEventProducer.data['schema'].as_string() == NATIVE_RECORD)
    if after is not None: query = query.where(GraphEventProducer.id > str(after))
    rows = list(db.scalars(query.order_by(GraphEventProducer.id).limit(limit + 1)))
    return wire({'schema': 'graph-event-native-audio-producer-page-v1', 'instance_id': str(identity),
        'items': [item(db, p, str(identity), row) for row in rows[:limit]],
        'next_after': rows[limit - 1].id if len(rows) > limit else None, 'can_register': p.role == 'admin'})


@router.get('/registrations/{producer_id}')
def detail(identity: UUID, producer_id: UUID, p: P, db: DB):
    _instance(db, p, identity)
    return wire(item(db, p, str(identity), scoped(db, GraphEventProducer, str(producer_id), p)))


@router.get('/observations/{observation_id}/stages')
def receipt_stages(identity: UUID, observation_id: UUID, p: P, db: DB):
    from .models import Observation
    from .graph_event_native_capture_v136 import stages
    _instance(db, p, identity)
    try: return wire(stages(db, p, str(identity), scoped(db, Observation, str(observation_id), p)))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError, OSError):
        raise HTTPException(409, '原生结果的归集或送达依据无法核对；原处理与原件保留') from None


router.include_router(requests.router)
