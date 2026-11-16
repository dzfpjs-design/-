"""Capture only newly issued native receipt Events in their real transaction."""
from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import Capability, Event, Observation
from .services import scoped
from .graph_event_models import GraphEventProducer


def native_observation(db, p, observation, event):
    """Recognize the actual managed native envelope without granting execution."""
    row = scoped(db, Observation, observation.id, p); notice = scoped(db, Event, event.id, p)
    packet = row.data.get('native_packet')
    if (row.workspace_id != p.workspace_id or notice.workspace_id != p.workspace_id
        or row.status != 'processed' or row.observed_at is not None
        or type(packet) is not dict or packet.get('schema') not in {'native-observation-v1', 'native-observation-v2'}
        or row.data.get('native_packet_hash') != digest(packet)
        or packet.get('entity_id') != row.entity_id or row.data.get('candidate_only') is not True
        or 'extractor_id' in row.data or row.data.get('invocation_id') is not None
        or notice.kind != 'observation.data' or notice.idempotency_key != 'data:' + row.id
        or notice.data.get('native_receipt') != packet or notice.data.get('observed_at', object()) is not None
        or notice.data.get('invocation_id', object()) is not None
        or notice.data.get('observation_id') != row.id or notice.data.get('entity_id') != row.entity_id
        or notice.data.get('source_id') != row.source_id
        or digest(notice.data.get('extracted')) != digest(row.data.get('extracted'))
        or digest(notice.data.get('evidence_ids')) != digest(row.data.get('evidence_ids'))):
        raise HTTPException(409, '原生回执与真实原归集事件不一致，不能改走普通处理来源')
    source = scoped(db, Capability, row.source_id, p)
    if (source.namespace != 'platform_native_receipts'
        or source.data.get('specification', {}).get('managed_instance') != row.data.get('native_instance_id')):
        raise HTTPException(409, '原生来源与实际归集实例不一致')
    return packet


def capture(db, p, event, observation):
    """Called by native_receipts only after a new Observation/Event/Audit flush.

    Repeated sync never calls this entry. An active-contract failure rolls back
    receipt capture; the independently saved AudioRun is neither rerun nor lost.
    """
    db.flush(); packet = native_observation(db, p, observation, event)
    if not packet.get('source_key', '').startswith('AudioRun:'): return []
    from .graph_event_admission import freeze_intent
    from .graph_event_dispatch import queue_intent
    producers = list(db.scalars(select(GraphEventProducer).where(
        GraphEventProducer.workspace_id == p.workspace_id, GraphEventProducer.status == 'active',
        GraphEventProducer.data['contract']['instance_id'].as_string() == observation.data['native_instance_id'],
        GraphEventProducer.data['contract']['source_id'].as_string() == observation.source_id,
        GraphEventProducer.data['contract']['event_kind'].as_string() == event.kind)
        .order_by(GraphEventProducer.id).with_for_update(read=True)
        .execution_options(populate_existing=True)))
    identities = []
    for producer in producers:
        from .graph_event_admission import check_producer
        from .graph_event_source_families_v136 import record_family, NATIVE_AUDIO, NATIVE_LEARNED_GEO
        check_producer(producer)
        family = record_family(producer)
        if family == NATIVE_LEARNED_GEO: continue
        if family != NATIVE_AUDIO:
            raise HTTPException(409, '原生语音来源不能使用其他处理协议')
        intent, _ = freeze_intent(db, p, producer, event, observation)
        queue_intent(db, p, producer, intent); identities.append(intent.id)
    return identities


def stages(db, p, instance_id, observation):
    """Read independent durable stages, without inferring a wait consumption."""
    from .native_audio_provenance_v136 import checked_observation
    from .graph_event_admission import check_producer, check_intent, check_admission
    from .graph_event_source_families_v136 import record_family, NATIVE_AUDIO, verify_history
    from .graph_event_models import GraphEventIntent, GraphEventAdmission
    context = checked_observation(db, p, observation, live=False, lock=False)
    row = context['observation']; event = context['event']
    if context['release'].instance_id != instance_id:
        raise HTTPException(404, '此归集记录不属于当前实例')
    received = False; data_status = 'pending'; data_message = '候选结果已归集，数据管理接收尚未核对。'
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
        if record_family(producer) != NATIVE_AUDIO:
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
    return {'schema': 'native-audio-graph-receipt-stages-v136', 'observation_id': row.id,
        'source_id': row.source_id, 'entity_id': row.entity_id, 'task_id': context['task'].id,
        'collected': True, 'data_received': received, 'data_status': data_status, 'data_message': data_message,
        'deliveries': deliveries, 'waiting_match': 'not_checked',
        'waiting_message': '接纳回执不等于业务等待已匹配；请到相应任务详情核对开窗、关联对象和处理结果。',
        'candidate_only': True, 'source_time_verified': False, 'physical_state_verified': False}
