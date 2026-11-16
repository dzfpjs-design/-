"""New learned receipt capture, atomically after actual Memory and ACK.

The worker owns the outer savepoint. This receiver is never called by sync,
registration, historical reads or an admission retry.
"""
from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import digest
from .security import Principal
from .services import scoped
from .graph_event_models import GraphEventProducer


def handles(db, event, observation):
    from .native_learned_geo_pair_provenance_v138 import has_issued
    return event.kind == 'observation.data' and has_issued(db,
        Principal(observation.data['submitted_by'], event.workspace_id, 'viewer', 'native-learned-receiver'), observation)


def _receipt_actor(db, observation):
    rows = list(db.execute(select(m.User, m.Membership).join(m.Membership,
        m.Membership.user_id == m.User.id).where(m.User.id == observation.data['submitted_by'],
        m.User.active.is_(True), m.Membership.workspace_id == observation.workspace_id,
        m.Membership.role.in_(['admin', 'operator'])).with_for_update(read=True, of=(m.User, m.Membership))))
    if len(rows) != 1: raise HTTPException(403, '原归集账号已无供应消息权限；原模型结果保留')
    user, membership = rows[0]
    return Principal(user.id, membership.workspace_id, membership.role, 'native-learned-graph-receiver')


def receive(db, event, observation, *, derivation_hold=None):
    from .native_learned_geo_pair_provenance_v138 import checked_observation, RECEPTION_SCHEMA
    from .instance_data_receipts import receive_observation, observation_receipt
    from .graph_event_native_learned_geo_eligibility_v141 import inventory, has_eligibility
    from .graph_event_source_families_v136 import record_family, NATIVE_LEARNED_GEO
    from .graph_event_admission import check_producer, freeze_intent
    from .graph_event_dispatch import queue_intent
    if (event.status != 'pending_delivery' or event.kind != 'observation.data'
        or event.workspace_id != observation.workspace_id):
        raise HTTPException(409, '只允许实际新接收的原候选消息供应流程；历史不会补发')
    reader = Principal(observation.data['submitted_by'], event.workspace_id, 'viewer', 'native-learned-receiver')
    context = checked_observation(db, reader, observation, event)
    if db.scalar(select(m.Memory.id).where(m.Memory.workspace_id == event.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id,
        m.Memory.data['type'].as_string() == 'observation_evidence').limit(1)) is not None:
        raise HTTPException(409, '原接收消息已有实际记录，不能重新供应流程')
    reception = receive_observation(db, reader, event, observation)
    if type(reception) is not dict or reception.get('schema') != RECEPTION_SCHEMA:
        raise HTTPException(409, '此原生候选尚未形成现代数据接收，不供应流程消息')
    event.data = {**event.data, 'data_reception': reception}
    if derivation_hold: event.data = {**event.data, 'derivation_receipt': derivation_hold}
    db.add(m.Memory(workspace_id=event.workspace_id, status='candidate',
        text='已接收观测提取结果，等待事实核实', data={**event.data,
            'event_id': event.id, 'type': 'observation_evidence', 'not_authoritative': True}))
    event.status = 'received'; event.received_at = m.now(); event.attempts += 1
    db.flush()
    # The real receiver and exactly one Memory must be visible before source
    # projection can assert data_received. These are still uncommitted writes.
    observation_receipt(db, reader, observation)
    eligible = inventory(db, reader, context['pair'].id)
    if derivation_hold is not None: return
    active = []
    for original in sorted(eligible, key=lambda item: item.data['producer_id']):
        producer = db.scalar(select(GraphEventProducer).where(GraphEventProducer.workspace_id == event.workspace_id,
            GraphEventProducer.id == original.data['producer_id']).with_for_update(read=True)
            .execution_options(populate_existing=True))
        if producer is None or record_family(producer) != NATIVE_LEARNED_GEO:
            raise HTTPException(409, '原候选消息来源登记或原输入资格无法核对')
        contract = check_producer(producer)
        if (contract['instance_id'] != context['release'].instance_id or contract['source_id'] != observation.source_id
            or contract['capability_id'] != context['capability'].id):
            raise HTTPException(409, '原候选与登记的实例、模型或来源不一致')
        if producer.status == 'active': active.append(producer)
    if not active: return  # Old inputs and later registrations are never backfilled.
    actor = _receipt_actor(db, observation)
    for producer in active:
        if not has_eligibility(db, actor, producer, context['pair'].id):
            raise HTTPException(409, '原输入未获当时实际登记资格，不能补发')
        intent, _ = freeze_intent(db, actor, producer, event, observation)
        queue_intent(db, actor, producer, intent)


def stages(db, p, instance_id, observation):
    """Read independent durable stages, without inferring a wait consumption."""
    from .native_learned_geo_pair_provenance_v138 import checked_observation
    from .graph_event_admission import check_producer, check_intent, check_admission
    from .graph_event_source_families_v136 import record_family, NATIVE_LEARNED_GEO, verify_history
    from .graph_event_models import GraphEventIntent, GraphEventAdmission
    context = checked_observation(db, p, observation)
    row = context['observation']; event = context['event']
    if context['release'].instance_id != instance_id:
        raise HTTPException(404, '此归集记录不属于当前实例')
    if type(row.version) is not int or row.version < 1:
        raise HTTPException(409, '原归集记录版本无法核对；原结果保留')
    received = False; data_status = 'unavailable'; data_message = '原数据接收状态无法核对，原结果保留，请核查原处理。'
    if event.status == 'pending_delivery':
        data_status = 'pending'; data_message = '候选结果已归集，正在等待原数据接收。'
    if event.status == 'received':
        try:
            from .instance_data_receipts import observation_receipt
            received = bool(observation_receipt(db, p, row).get('data_reception'))
            data_status = 'received' if received else 'not_declared'
            data_message = '数据管理已保存候选接收回执。' if received else '原版本没有对应的数据接收声明；未冒充已接收。'
        except (HTTPException, ValueError, KeyError, TypeError, OSError):
            data_status = 'unavailable'; data_message = '数据管理原接收依据无法核对，原结果保留，请核查原处理。'
    elif event.status == 'blocked_delivery':
        data_status = 'blocked'; data_message = '数据管理接收未完成，原结果保留，请核查原处理。'
    deliveries = []
    intents = list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.workspace_id == p.workspace_id,
        GraphEventIntent.observation_id == row.id).order_by(GraphEventIntent.id).limit(201)))
    if len(intents) > 200: raise HTTPException(413, '此记录来源消息超过完整读取范围；原记录保留')
    for intent in intents:
        producer = scoped(db, GraphEventProducer, intent.producer_id, p); check_producer(producer)
        if record_family(producer) != NATIVE_LEARNED_GEO:
            raise HTTPException(409, '原生回执存在不相容的消息来源记录')
        frozen = check_intent(intent, producer); verify_history(db, p, producer, frozen)
        actual = list(db.scalars(select(GraphEventAdmission).where(GraphEventAdmission.workspace_id == p.workspace_id,
            GraphEventAdmission.intent_id == intent.id).limit(2)))
        if (len(actual) > 1 or (not actual and intent.receipt_id is not None)
            or (actual and intent.receipt_id != actual[0].id)):
            raise HTTPException(409, '原生来源消息与实际接纳指针不一致')
        admission = None
        if actual:
            value = check_admission(actual[0], intent, producer)
            admission = {key: value[key] for key in ('id', 'sequence', 'admitted_at')}
        deliveries.append({'intent_id': intent.id, 'producer_id': producer.id,
            'source_label': producer.data['label'], 'status': intent.status, 'admission': admission})
    return {'schema': 'native-learned-geo-graph-receipt-stages-v141', 'observation_id': row.id,
        'observation_version': row.version,
        'source_id': row.source_id, 'entity_id': row.entity_id, 'task_id': context['task'].id,
        'collected': True, 'data_received': received, 'data_status': data_status, 'data_message': data_message,
        'deliveries': deliveries, 'waiting_match': 'not_checked',
        'waiting_message': '接纳回执不等于业务等待已匹配；请到相应任务详情核对开窗、关联对象和处理结果。',
        'candidate_only': True, 'source_time_verified': False, 'physical_state_verified': False}

