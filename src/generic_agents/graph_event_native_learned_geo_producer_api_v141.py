"""Human-confirmed learned GeoPair registrations, separate from processing v1."""
import copy
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select, func

from .contracts import Contract, VersionAction, digest
from .db import session
from .models import Capability, DomainInstance, Audit, Observation, Event, uid
from .graph_event_models import GraphEventProducer
from .graph_event_admission import check_producer
from .graph_event_source_families_v136 import NATIVE_LEARNED_GEO, LEARNED_RECORD, native_learned_command_hash, record_family
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, scoped, check_version
from . import graph_event_native_learned_geo_producer_requests_v141 as requests

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/instances/{identity}/graph-event-native-learned-geo-producers')


class Register(Contract):
    schema: Literal['graph-event-native-learned-geo-producer-command-v1']
    request_id: UUID
    label: str = Field(strict=True, min_length=1, max_length=200)
    adapter_id: Literal['native-learned-binary-change-v1']
    instance_id: UUID
    source_id: UUID
    source_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    capability_id: UUID
    capability_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')

    @field_validator('request_id', 'instance_id', 'source_id', 'capability_id', mode='before')
    @classmethod
    def exact_identifier(cls, value):
        if type(value) is not str or str(UUID(value)) != value: raise ValueError('请保留原标准编号')
        return value

    @field_validator('label')
    @classmethod
    def meaningful_label(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请填写来源名称')
        value.encode('utf-8'); return value


def wire(value): return requests.wire(value)


def _instance(db, p, identity): return scoped(db, DomainInstance, str(identity), p)


def public(db, p, instance_id, row):
    contract = check_producer(row)
    if record_family(row) != NATIVE_LEARNED_GEO or contract['instance_id'] != instance_id:
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
    from .graph_event_native_learned_geo_sources_v141 import build_contract
    try:
        contract = build_contract(db, p, instance_id, str(body.source_id), str(body.capability_id), lock=True)
    except ValueError:
        raise HTTPException(409, '本地学习型变化检测来源或原模型安装版本无法核对；原稿保留') from None
    if contract['source_hash'] != body.source_hash or contract['capability_hash'] != body.capability_hash:
        raise HTTPException(409, '来源或模型安装版本已有变化，请重新读取后核对')
    return contract


def _source_display_name(instance):
    name = instance.data.get('name')
    name = name.strip() if type(name) is str else ''
    internal_names = (instance.id, instance.id.replace('-', ''), instance.namespace)
    if not name or any(type(value) is str and value and value.casefold() in name.casefold()
        for value in internal_names):
        name = '当前实例'
    return name[:80] + '的本地学习型变化检测结果'


@router.get('/options')
def options(identity: UUID, p: P, db: DB, after: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    instance = _instance(db, p, identity)
    source_name = _source_display_name(instance)
    from .graph_event_native_learned_geo_sources_v141 import build_contract
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
    models = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.kind == 'specialist_model', Capability.status == 'published',
        Capability.data['execution']['adapter'].as_string() == 'local_learned_geo_pair')
        .order_by(Capability.id).limit(201)))
    if len(models) > 200: raise HTTPException(413, '本机变化检测能力超过完整读取上限，请缩小登记范围')
    capabilities = []
    for model in models:
        if not sources or p.role not in {'admin', 'operator'}: continue
        try:
            contract = build_contract(db, p, str(identity), sources[0]['id'], model.id, lock=False)
            capabilities.append({'id': model.id, 'name': '本地学习型变化检测',
                'content_hash': model.content_hash, 'release_version': model.release_version,
                **{key: copy.deepcopy(contract[key]) for key in ('model_trace', 'model_trace_hash',
                    'probability_threshold', 'alignment', 'runtime_hash')}})
        except (HTTPException, ValueError, KeyError, TypeError, OSError): pass
    active = instance.status == 'partially_active' and instance.active_release_id is not None
    reason = ('请先启用当前实例；原来源与处理记录仍保留。' if not active
        else '请先完成真实本地变化检测并归集候选，再登记该实例已存在的结果来源。' if not sources
        else '请先发布当前可用的本地学习型变化检测业务能力；不自动安装、下载或调用云模型。' if not capabilities
        else '当前账号仅可查看；登记须由管理员明确确认。' if p.role != 'admin' else None)
    return wire({'schema': 'graph-event-native-learned-geo-producer-options-v1', 'instance_id': str(identity),
        'sources': sources, 'capabilities': capabilities,
        'next_after': rows[limit - 1].id if len(rows) > limit else None,
        'can_register': p.role == 'admin' and active, 'reason': reason})

@router.post('/preview')
def preview(identity: UUID, body: Register, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p); _instance(db, p, identity)
    value = body.model_dump(mode='json'); contract = proposed(db, p, identity, body)
    result = {'schema': 'graph-event-native-learned-geo-producer-preview-v1', 'instance_id': str(identity),
        'command': value, 'contract': contract, 'contract_hash': digest(contract),
        'command_hash': native_learned_command_hash(p, value), 'execution_granted': False,
        'meaning': '仅供应已保存的变化检测候选；不证明领域准确率、采集时刻、现场覆盖或对象状态。业务等待仍须单独确认。'}
    db.commit(); return wire(result)


@router.post('', status_code=201)
def register(identity: UUID, body: Register, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p); _instance(db, p, identity)
    instance_id = str(identity); raw = body.model_dump(mode='json')
    if raw['instance_id'] != instance_id: raise HTTPException(409, '原登记正文与当前实例不一致')
    signature = native_learned_command_hash(p, raw)
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
        data={'schema': LEARNED_RECORD, 'label': raw['label'], 'contract': contract, 'contract_hash': digest(contract),
            'registration': {'request_id': raw['request_id'], 'actor_id': p.user_id, 'command_hash': signature, 'command': raw}})
    db.add(row); db.flush()
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id,
        action='graph_event.native_learned_geo_producer_registered_v141', object_id=row.id,
        created_at=db.scalar(select(func.clock_timestamp())), data={'instance_id': instance_id,
            'request_id': row.request_id, 'command_hash': row.command_hash, 'contract_hash': row.contract_hash}))
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
        'capability_name': '本地学习型变化检测',
        'current_availability': current_availability(db, p, row, value['contract']), 'receipt_verified': False}


@router.get('')
def listing(identity: UUID, p: P, db: DB, after: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    _instance(db, p, identity)
    query = select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.data['contract']['instance_id'].as_string() == str(identity),
        GraphEventProducer.data['schema'].as_string() == LEARNED_RECORD)
    if after is not None: query = query.where(GraphEventProducer.id > str(after))
    rows = list(db.scalars(query.order_by(GraphEventProducer.id).limit(limit + 1)))
    return wire({'schema': 'graph-event-native-learned-geo-producer-page-v1', 'instance_id': str(identity),
        'items': [item(db, p, str(identity), row) for row in rows[:limit]],
        'next_after': rows[limit - 1].id if len(rows) > limit else None, 'can_register': p.role == 'admin'})


@router.get('/registrations/{producer_id}')
def detail(identity: UUID, producer_id: UUID, p: P, db: DB):
    _instance(db, p, identity)
    return wire(item(db, p, str(identity), scoped(db, GraphEventProducer, str(producer_id), p)))


@router.get('/observations/{observation_id}/stages')
def receipt_stages(identity: UUID, observation_id: UUID, p: P, db: DB):
    from .graph_event_native_learned_geo_capture_v141 import stages
    _instance(db, p, identity)
    try: return wire(stages(db, p, str(identity), scoped(db, Observation, str(observation_id), p)))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError, OSError):
        raise HTTPException(409, '原生结果的归集或送达依据无法核对；原处理与原件保留') from None


@router.post('/observations/{observation_id}/retry-data-delivery')
def retry_data_delivery(identity: UUID, observation_id: UUID, body: VersionAction, p: P, db: DB):
    """Release only the original blocked receive; never rerun the model or sync."""
    require(p, 'admin', 'operator'); lock_current_actor(db, p); _instance(db, p, identity)
    # Match the worker's Event -> Observation lock order before any receive write.
    event = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == 'observation.data', Event.idempotency_key == 'data:' + str(observation_id))
        .with_for_update().execution_options(populate_existing=True))
    if event is None: raise HTTPException(409, '原候选接收消息不存在，不能创建替代消息')
    row = scoped(db, Observation, str(observation_id), p, lock=True)
    from .native_learned_geo_pair_provenance_v138 import checked_observation, RECEPTION_SCHEMA
    from .instance_data_receipts import observation_receipt
    context = checked_observation(db, p, row, event)
    if context['release'].instance_id != str(identity):
        raise HTTPException(404, '此归集记录不属于当前实例')
    if event.status == 'received':
        # A lost reply is recovered from the actual unique Memory and ACK. The
        # caller's old version is harmless once this original receive is saved.
        receipt = observation_receipt(db, p, row).get('data_reception')
        if type(receipt) is not dict or receipt.get('schema') != RECEPTION_SCHEMA:
            raise HTTPException(409, '原数据接收回执无法核对；原结果保留')
        result = receipt_stages(identity, observation_id, p, db)
        db.commit(); return result
    # A proper failed savepoint has neither a receipt nor a Memory. Do not turn
    # a partially saved or damaged phase into an implicit repair operation.
    from .models import Memory
    if ('data_reception' in row.data or 'data_reception' in event.data or event.received_at is not None
        or db.scalar(select(Memory.id).where(Memory.workspace_id == p.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence').limit(1)) is not None):
        raise HTTPException(409, '原接收阶段与实际保存记录不一致，不能自动修补；原结果保留')
    if event.status == 'blocked_delivery':
        check_version(row, body.expected_version)
        event.status = 'pending_delivery'
        row.version += 1
        audit(db, p, 'observation.native_learned_data_delivery_retried_v141', row.id,
            {'event_id': event.id, 'reason': body.reason, 'observation_version': row.version})
    elif event.status != 'pending_delivery':
        raise HTTPException(409, '此原数据接收状态不能重试；原结果保留')
    # Receipt, Memory, ACK, intent and queue are written only by the worker's
    # existing shared savepoint, after current source and actor checks succeed.
    db.commit()
    from .worker import deliver_events
    deliver_events(workspace_id=p.workspace_id, observation_id=str(observation_id), event_kind='observation.data')
    db.expire_all()
    return receipt_stages(identity, observation_id, p, db)


router.include_router(requests.router)

