"""Confirmed sensor bindings enter the existing observation/outbox pipeline."""
import math
from fastapi import HTTPException
from sqlalchemy import select, exists, or_
from .contracts import Manifest, ObservationCreate, digest
from .models import (MqttGateway, MqttBinding, MqttReceipt, Capability, Entity, InstanceRelease, Job, Observation,
                     MqttBindingRevision, MqttReceiptDelivery, DomainInstance)
from .services import scoped, published, require, audit, check_version, validate, enqueue_event
from .mqtt_inbox import authorized_gateway
from .mqtt_messages import SensorFields, decode_sensor_message
from .evidence import evidence_bytes


def binding_hash(row):
    return digest({'gateway_id':row.gateway_id,'topic':row.topic,'config_hash':row.config_hash,
                   'entity_id':row.entity_id,'source_id':row.source_id,'extractor_id':row.extractor_id,'data':row.data})


def binding_manifest(binding):
    return Manifest(kind='source', namespace='platform_mqtt', name='binding_' + binding.id.replace('-', ''),
        version='1.0.0', owner='平台传感接入', description='已确认主题和对象的MQTT原报文来源（设备声明，未独立校准）',
        allowed_agent_roles=['perception', 'data'], execution={'adapter': 'mqtt_source', 'binding_id':binding.id},
        specification={'mqtt_binding_id': binding.id, 'gateway_id': binding.gateway_id, 'topic': binding.topic,
                       'timezone': 'Asia/Shanghai', 'date_basis': 'reported_capture_time',
                       **({'managed_instance': binding.data['instance']['id']} if binding.data.get('instance') else {})},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})


def validate_source_manifest(db,p,manifest):
    from .mqtt_binding_routes import find_binding
    binding=find_binding(db,p,manifest.execution.get('binding_id'))
    if manifest.model_dump()!=binding_manifest(binding).model_dump():
        raise HTTPException(422,'MQTT来源须采用已确认对象绑定的固定只读契约')


def checked_extractor(db, p, identity, fields, *, package_id=None, entity_type=None, field_units=None):
    cap = published(db, p, identity)
    if (cap.kind not in {'tool','specialist_model'}
            or cap.data['execution'] != {'adapter': 'builtin', 'handler': 'numeric_rules'}
            or 'perception' not in cap.data['allowed_agent_roles']
            or cap.data['data_policy'].get('cloud_allowed') or cap.data['data_policy'].get('external_allowed')):
        raise HTTPException(422, '此入口只允许已发布的本地数值规则，不授予外发或设备操作权限')
    rules = cap.data['specification'].get('rules', [])
    managed = cap.data['specification'].get('managed_numeric_rule')
    if managed is not None or cap.namespace == 'platform_numeric_rules':
        from .local_rules_api import require_current_check
        require_current_check(db, p, cap)
        if (managed.get('package_id') != package_id or managed.get('entity_type') != entity_type
                or field_units is None or any(rule.get('unit') != (field_units.get(rule.get('field')) or None) for rule in rules)):
            raise HTTPException(422, '本地规则的业务对象类型或单位与来源不符，请核对规则和测量约定')
    if not isinstance(rules, list) or len(rules) > 64:
        raise HTTPException(422, '规则超限')
    for rule in rules:
        if (not isinstance(rule, dict) or rule.get('field') not in fields or rule.get('operator') not in {'gt', 'lt'}
                or type(rule.get('threshold')) not in {int, float} or abs(rule['threshold']) > 1e15
                or not math.isfinite(rule['threshold']) or not isinstance(rule.get('id'), str) or not rule['id']):
            raise HTTPException(422, '规则字段和有限数值条件须与已确认映射一致')
    return cap


def prepare_binding_data(db, p, gateway, topic, entity_id, extractor_id, field_links=None, *, instance_release_id=None):
    if topic not in gateway.data['topics']:
        raise HTTPException(422, '主题尚未确认')
    from .mqtt_inbox import validate_configuration
    validate_configuration(gateway.data,workspace_id=p.workspace_id,db=db)
    obj = scoped(db, Entity, entity_id, p)
    package = published(db, p, obj.schema_capability_id, 'domain_package')
    properties = package.data['specification']['entity_schemas'][obj.entity_type].get('properties', {})
    measurement_fields={spec['attribute'] for spec in gateway.data['mapping'].values()}
    links={field:field for field in measurement_fields} if field_links is None else field_links
    if (not isinstance(links,dict) or set(links)!=measurement_fields
        or any(not isinstance(value,str) or not value or len(value)>100 for value in links.values())
        or len(set(links.values()))!=len(links)):
        raise HTTPException(422,'须逐个对应已确认测量字段，不能遗漏、覆盖或自行换算')
    fields=set(links.values())
    if any(field not in properties or properties[field].get('type') not in {'number', 'integer'} for field in fields):
        raise HTTPException(422, '传感字段须属于此业务对象已确认的数值字段')
    units={links[spec['attribute']]:spec['unit'] for spec in gateway.data['mapping'].values()}
    if any(properties[field].get('unit') is not None and properties[field]['unit']!=units[field] for field in fields):
        raise HTTPException(422,'业务字段单位与来源声明不符，不自动换算')
    extractor = checked_extractor(db, p, extractor_id, fields, package_id=package.id,
        entity_type=obj.entity_type, field_units=units)
    instance_context = None
    from .instance_domain import current_context, require_destination_version
    context=current_context(db,p,obj,release_id=instance_release_id)
    if context:
        instance,current,_=context
        db.scalar(select(DomainInstance).where(DomainInstance.workspace_id==p.workspace_id,
            DomainInstance.id==instance.id).with_for_update(read=True).execution_options(populate_existing=True))
        instance,current,_=current_context(db,p,obj,release_id=instance_release_id if instance_release_id is not None else current.id)
        require_destination_version(current,[obj],instance_release_id)
        instance_context={'id':instance.id,'release_id':current.id}
    from .instance_domain import object_display
    display=object_display(obj,package)
    return obj, extractor, {'schema': 'mqtt-binding-v1', 'package_id': package.id, 'package_hash': digest(package.data),
              'extractor_hash': digest(extractor.data), 'config_snapshot': gateway.data,
              'field_links':dict(links),'field_units':units,
              'device_external_id': gateway.data['topics'][topic], 'instance': instance_context,
              'display':{'entity_label':display['label'],'entity_type_label':display['type'],
                         'instance_name':instance.data.get('name') or '业务实例' if instance_context else '工作区业务',
                         'rule_name':extractor.data.get('description') or extractor.name}}


def bind(db, p, gateway_id, version, topic, entity_id, extractor_id,*,field_links=None,instance_release_id=None):
    require(p, 'admin')
    gateway = scoped(db, MqttGateway, gateway_id, p, lock=True); check_version(gateway, version)
    obj, extractor, data = prepare_binding_data(db, p, gateway, topic, entity_id, extractor_id, field_links,
                                               instance_release_id=instance_release_id)
    config_hash = digest(gateway.data)
    existing = db.scalar(select(MqttBinding).where(MqttBinding.gateway_id == gateway.id,
        MqttBinding.topic == topic, MqttBinding.config_hash == config_hash))
    if existing:
        if (existing.entity_id != obj.id or existing.extractor_id != extractor.id
                or existing.data.get('field_links')!=data['field_links']
                or existing.data.get('instance')!=data['instance']):
            raise HTTPException(409, '此接入配置已有旧单对象关联，请在业务对象关联中新增或修改')
        return existing
    row = MqttBinding(workspace_id=p.workspace_id, gateway_id=gateway.id, topic=topic,
        config_hash=config_hash, entity_id=obj.id, extractor_id=extractor.id,
        data=data)
    db.add(row); db.flush()
    manifest = binding_manifest(row).model_dump(); fingerprint = digest(manifest)
    source = Capability(workspace_id=p.workspace_id, namespace=manifest['namespace'], name=manifest['name'],
        release_version=manifest['version'], kind='source', status='published', content_hash=fingerprint,
        validated_hash=fingerprint, data=manifest)
    db.add(source); db.flush(); row.source_id = source.id
    audit(db, p, 'mqtt.binding.confirmed', row.id, {'entity_id': obj.id, 'source_id': source.id})
    schedule_receipts(db,p,gateway)
    return row


def checked_packet(db, p, receipt, binding, *, execution):
    gateway = scoped(db, MqttGateway, receipt.gateway_id, p, lock=execution)
    if execution:
        authorized_gateway(db, p, gateway.id, gateway.version)
        if digest(gateway.data) != binding.config_hash:
            raise HTTPException(409, '接入配置已变化，原数据保留待核对')
        from .mqtt_binding_routes import assert_current, allows_receipt, checked_delivery
        route=assert_current(db, p, binding)
        if route and not allows_receipt(route,receipt):
            raise HTTPException(409,'此原件不在当前关联确认的处理范围内')
        delivery=db.scalar(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
            MqttReceiptDelivery.receipt_id==receipt.id,MqttReceiptDelivery.binding_id==binding.id))
        if delivery:checked_delivery(db,p,delivery,binding,receipt,execution=True)
    if isinstance(binding, MqttBindingRevision) and binding.content_hash != binding_hash(binding):
        raise HTTPException(409, '原关联版本摘要不一致')
    snapshot = receipt.data['config_snapshot']
    if (receipt.gateway_id != binding.gateway_id or receipt.data['topic'] != binding.topic
            or digest(snapshot) != receipt.data['config_hash'] or receipt.data['config_hash'] != binding.config_hash
            or digest(binding.data['config_snapshot']) != binding.config_hash
            or receipt.data['qos'] != 1 or receipt.data['retain'] or receipt.data['issues']
            or receipt.status == 'quarantined'):
        raise HTTPException(409, '原收件、绑定或协议依据不一致，不能进入对象状态')
    evidence, raw = evidence_bytes(db, p, receipt.evidence_id)
    decoded = decode_sensor_message(raw, snapshot['mapping'], SensorFields(**snapshot['fields']))
    if (decoded != receipt.data['decoded'] or not decoded['usable'] or decoded['input_sha256'] != receipt.input_sha256
            or evidence.sha256 != receipt.input_sha256 or decoded['source_record_id'] != receipt.source_record_id
            or decoded['device_external_id'] != binding.data['device_external_id']
            or evidence.data.get('modality') != 'sensor_packet'
            or evidence.data.get('gateway_id') != receipt.gateway_id or evidence.data.get('topic') != binding.topic):
        raise HTTPException(409, '原报文与解释结果不一致，不能使用修改后的字段')
    obj = scoped(db, Entity, binding.entity_id, p)
    package = scoped(db, Capability, binding.data['package_id'], p)
    extractor = scoped(db, Capability, binding.extractor_id, p)
    source = scoped(db, Capability, binding.source_id, p)
    if (obj.schema_capability_id != package.id or digest(package.data) != binding.data['package_hash']
            or digest(extractor.data) != binding.data['extractor_hash']
            or source.data != binding_manifest(binding).model_dump()):
        raise HTTPException(409, '原业务字段、来源或规则版本不一致')
    properties = package.data['specification']['entity_schemas'][obj.entity_type]['properties']
    mapped=business_attributes(binding,decoded)
    for field, value in mapped.items(): validate(properties[field], value)
    if execution:
        published(db, p, package.id, 'domain_package')
        checked_extractor(db, p, extractor.id, set(mapped), package_id=package.id,
            entity_type=obj.entity_type, field_units=binding.data['field_units'])
        published(db, p, source.id, 'source')
        if binding.data.get('instance'):
            db.scalar(select(DomainInstance).where(DomainInstance.workspace_id == p.workspace_id,
                DomainInstance.id == binding.data['instance']['id']).with_for_update(read=True)
                .execution_options(populate_existing=True))
            from .instance_api import active
            instance, release = active(db, p, binding.data['instance']['id'])
            if release.id != binding.data['instance']['release_id'] or obj.namespace != instance.namespace:
                raise HTTPException(409, '业务实例版本已变化')
            from .entity_release_compat import require_entity
            require_entity(db,p,instance,release,obj)
    return decoded


def business_attributes(binding,decoded):
    links=binding.data['field_links']
    if set(links)!=set(decoded['attributes']) or len(set(links.values()))!=len(links):
        raise HTTPException(409,'原测量字段与业务对应关系不完整')
    return {links[field]:value for field,value in decoded['attributes'].items()}


def request_for(binding, receipt, decoded):
    return ObservationCreate(source_id=binding.source_id, source_record_id=receipt.source_record_id,
        entity_id=binding.entity_id, observed_at=decoded['observed_at'], business_day=decoded['business_day'],
        attributes=business_attributes(binding,decoded), evidence_ids=[receipt.evidence_id], extractor_id=binding.extractor_id,
        startup_mode='automatic')


def _ingest_packet(db, p, receipt, binding, decoded):
    """Capture unit provenance only for an observation created in this ingest.

    Both callers hold the source gateway/receipt lock. The public observation
    API cannot submit an MQTT receipt directly, so the original-key check and
    insert serialize with the other MQTT ingestion path for this binding.
    """
    body=request_for(binding,receipt,decoded)
    prior=db.scalar(select(Observation.id).where(Observation.workspace_id==p.workspace_id,
        Observation.source_id==body.source_id,Observation.source_record_id==body.source_record_id,
        Observation.processing_version==body.processing_version))
    from .api import ingest_observation_record
    value=ingest_observation_record(body,p,db,mqtt_receipt_id=receipt.id,commit=False)
    row=scoped(db,Observation,value['id'],p)
    if prior is None:
        from .mqtt_unit_evidence_v342 import capture
        capture(db,p,row,binding,receipt,decoded)
    elif prior!=row.id:
        raise HTTPException(409,'原观测来源发生竞争，请保留原报文核对')
    return row


def verify_request(db, p, receipt_id, body):
    receipt = scoped(db, MqttReceipt, receipt_id, p)
    from .mqtt_binding_routes import source_binding
    binding = source_binding(db, p, body.source_id)
    if not binding: raise HTTPException(409, '缺少已确认的设备对象绑定')
    decoded = checked_packet(db, p, receipt, binding, execution=True)
    if body.model_dump() != request_for(binding, receipt, decoded).model_dump():
        raise HTTPException(409, '观测必须由原报文生成，不接受另填字段或补采声明')
    return receipt, binding


def promote(db, p, receipt_id, expected_version):
    require(p, 'admin', 'operator')
    receipt = scoped(db, MqttReceipt, receipt_id, p)
    # Gateway first, matching capture lock order; serialize conflict arrival and intake.
    gateway = scoped(db, MqttGateway, receipt.gateway_id, p, lock=True)
    receipt = scoped(db, MqttReceipt, receipt_id, p, lock=True)
    binding = db.scalar(select(MqttBinding).where(MqttBinding.gateway_id == gateway.id,
        MqttBinding.topic == receipt.data['topic'], MqttBinding.config_hash == receipt.data['config_hash']))
    if not binding: raise HTTPException(409, '请先确认此主题对应的业务对象和提取规则')
    decoded = checked_packet(db, p, receipt, binding, execution=True)
    if receipt.data.get('observation_id'):
        row = scoped(db, Observation, receipt.data['observation_id'], p)
        guard(db, p, row, execution=True)
        return row
    check_version(receipt, expected_version)
    row = _ingest_packet(db,p,receipt,binding,decoded)
    receipt.status = 'submitted'; receipt.version += 1
    receipt.data = {**receipt.data, 'observation_id': row.id, 'binding_id': binding.id}
    audit(db, p, 'mqtt.packet.submitted', receipt.id, {'observation_id': row.id})
    db.flush()
    from .mqtt_daily import index_legacy
    index_legacy(db,p,receipt,binding)
    return row


def guard(db, p, observation, *, execution, result=None):
    receipt = scoped(db, MqttReceipt, observation.data['mqtt_receipt_id'], p)
    from .mqtt_binding_routes import find_binding
    binding = find_binding(db, p, observation.data['mqtt_binding_id'])
    if observation.data.get('mqtt_binding_hash') != binding_hash(binding):
        raise HTTPException(409, '原对象绑定与保存摘要不一致')
    decoded = checked_packet(db, p, receipt, binding, execution=execution)
    body = request_for(binding, receipt, decoded).model_dump()
    delivery = db.scalar(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id == p.workspace_id,
        MqttReceiptDelivery.receipt_id == receipt.id, MqttReceiptDelivery.binding_id == binding.id))
    if delivery:
        from .mqtt_binding_routes import checked_delivery
        target=checked_delivery(db,p,delivery,binding,receipt,execution=execution)
        if (observation.data.get('mqtt_delivery_id')!=delivery.id
                or observation.data.get('mqtt_delivery_target_hash')!=digest(target)):
            raise HTTPException(409,'观测缺少原分发的固定反向关联')
    linked = (delivery.observation_id == observation.id if delivery else receipt.data.get('observation_id') == observation.id)
    if (observation.data.get('original_request') != body or observation.data.get('input_hash') != digest(body)
            or observation.data.get('attributes') != body['attributes']
            or observation.data.get('evidence_ids') != body['evidence_ids']
            or not linked):
        raise HTTPException(409, '原收件与观测关联或字段摘要不一致')
    for field in ('source_id', 'source_record_id', 'entity_id', 'business_day', 'processing_version'):
        if getattr(observation, field) != body[field]: raise HTTPException(409, '原观测归属不一致')
    from .services import timestamp
    if observation.observed_at != timestamp(body['observed_at']) or observation.data.get('extractor_id') != body['extractor_id']:
        raise HTTPException(409, '原采集时间或提取器不一致')
    if result is not None and result.get('attributes') != body['attributes']:
        raise HTTPException(409, '提取结果与原传感报文不同')
    from .mqtt_unit_evidence_v342 import verify as verify_units
    verify_units(db,p,observation,binding,receipt,decoded)
    return receipt


def reconcile(db, p, source_id, day, observation_id=None):
    from .mqtt_daily import index_observation,publish_ledger,UNKNOWN_DAY
    if observation_id:
        row=scoped(db,Observation,observation_id,p)
        if row.source_id!=source_id or row.business_day!=day:raise HTTPException(409,'单项整理归属与原观测不一致')
        ledger=index_observation(db,p,row)
    else:
        # Compatibility entry point publishes an already maintained revision; never re-scans a whole day.
        from .mqtt_binding_routes import source_binding
        from .models import MqttDailyLedger
        binding=source_binding(db,p,source_id)
        if not binding:return
        ledger=db.scalar(select(MqttDailyLedger).where(MqttDailyLedger.workspace_id==p.workspace_id,
            MqttDailyLedger.gateway_id==binding.gateway_id,MqttDailyLedger.day_key==(day or UNKNOWN_DAY)))
    if ledger:publish_ledger(db,ledger)


def schedule_receipts(db,p,gateway):
    if gateway.status!='active':return
    config_hash=digest(gateway.data)
    from .mqtt_binding_routes import active_bindings, allows_receipt
    for binding,route in active_bindings(db,p,gateway):
        if route:
            query=select(MqttReceipt).where(MqttReceipt.gateway_id==gateway.id,
                MqttReceipt.workspace_id==p.workspace_id,MqttReceipt.status.in_(['awaiting_object_binding','submitted']),
                MqttReceipt.data['topic'].as_string()==binding.topic,
                MqttReceipt.data['config_hash'].as_string()==config_hash,
                ~exists(select(MqttReceiptDelivery.id).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
                    MqttReceiptDelivery.receipt_id==MqttReceipt.id,MqttReceiptDelivery.binding_id==binding.id)),
                ~exists(select(MqttReceiptDelivery.id).join(Observation,
                    Observation.id==MqttReceiptDelivery.observation_id).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
                    MqttReceiptDelivery.receipt_id==MqttReceipt.id,MqttReceiptDelivery.route_id==route.id,
                    Observation.status=='processed')))
            if route.legacy_binding_id:
                query=query.where(~exists(select(Observation.id).where(Observation.workspace_id==p.workspace_id,
                    Observation.id==MqttReceipt.data['observation_id'].as_string(),Observation.status=='processed',
                    MqttReceipt.data['binding_id'].as_string()==route.legacy_binding_id)))
            if route.data.get('include_existing') is not True:
                from .services import timestamp
                after=timestamp(route.data.get('processing_after'))
                if not after:continue
                query=query.where(MqttReceipt.created_at>=after)
            rows=db.scalars(query.order_by(MqttReceipt.created_at,MqttReceipt.id).limit(200)).all()
            for receipt in rows:
                if allows_receipt(route,receipt):enqueue_delivery(db,p,gateway,receipt,binding,route)
            continue
        rows=db.scalars(select(MqttReceipt).where(MqttReceipt.gateway_id==gateway.id,
            MqttReceipt.workspace_id==p.workspace_id,MqttReceipt.status=='awaiting_object_binding',
            MqttReceipt.data['topic'].as_string()==binding.topic,
            MqttReceipt.data['config_hash'].as_string()==config_hash,
            ~exists(select(Job.id).where(Job.workspace_id==p.workspace_id,Job.kind=='submit_mqtt_receipt',
                Job.data['receipt_id'].as_string()==MqttReceipt.id)))
            .order_by(MqttReceipt.created_at,MqttReceipt.id).limit(200)).all()
        for receipt in rows: enqueue_receipt(db,p,gateway,receipt,binding)


def schedule_pending(workspace_id=None):
    from sqlalchemy.orm import Session
    from .db import engine
    from .worker import actor
    with Session(engine()) as listing:
        query=select(MqttGateway.id).where(MqttGateway.status=='active')
        if workspace_id is not None:query=query.where(MqttGateway.workspace_id==workspace_id)
        ids=listing.scalars(query.order_by(MqttGateway.id)).yield_per(100)
        for identity in ids:
            with Session(engine()) as db:
                try:
                    if workspace_id is None:gateway=db.get(MqttGateway,identity)
                    else:
                        gateway=db.scalar(select(MqttGateway).where(MqttGateway.id==identity,
                            MqttGateway.workspace_id==workspace_id))
                        if gateway is None:continue
                    p=actor(db,gateway.workspace_id,gateway.owner_id)
                    gateway=authorized_gateway(db,p,gateway.id,gateway.version)
                    schedule_receipts(db,p,gateway);db.commit()
                except Exception:db.rollback()


def enqueue_delivery(db,p,gateway,receipt,binding,route):
    from .mqtt_binding_routes import allows_receipt
    if not allows_receipt(route,receipt):return
    existing=db.scalar(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
        MqttReceiptDelivery.receipt_id==receipt.id,MqttReceiptDelivery.binding_id==binding.id))
    if existing:return existing
    completed=db.scalar(select(MqttReceiptDelivery.id).join(Observation,
        Observation.id==MqttReceiptDelivery.observation_id).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
        MqttReceiptDelivery.receipt_id==receipt.id,MqttReceiptDelivery.route_id==route.id,Observation.status=='processed'))
    if completed:return
    if route.legacy_binding_id and receipt.data.get('binding_id')==route.legacy_binding_id and receipt.data.get('observation_id'):
        original=scoped(db,Observation,receipt.data['observation_id'],p)
        if original.status=='processed':return
    target={'receipt_id':receipt.id,'binding_id':binding.id,'route_id':route.id,'revision':binding.revision,
            'binding_hash':binding_hash(binding),'input_sha256':receipt.input_sha256}
    row=MqttReceiptDelivery(workspace_id=p.workspace_id,receipt_id=receipt.id,binding_id=binding.id,
        route_id=route.id,revision=binding.revision,status='pending',
        data={'schema':'mqtt-delivery-v1','binding_hash':binding_hash(binding),
              'input_sha256':receipt.input_sha256,'route_version':route.version,
              'original_target':target,'target_hash':digest(target),
              'authorization_id':route.data['authorization_id'],'authorization_hash':route.data['authorization_hash']})
    if receipt.data.get('binding_id')==binding.id and receipt.data.get('observation_id'):
        row.observation_id=receipt.data['observation_id'];row.status='submitted'
    db.add(row);db.flush()
    if row.observation_id:return row
    key='mqtt-submit:'+receipt.id+':'+binding.id
    db.add(Job(workspace_id=p.workspace_id,kind='submit_mqtt_receipt',idempotency_key=key,
        data={'actor_id':gateway.owner_id,'receipt_id':receipt.id,'binding_id':binding.id,
              'delivery_id':row.id,'binding_hash':binding_hash(binding),'input_sha256':receipt.input_sha256,
              'route_id':route.id,'route_version':route.version,'authorization_hash':route.data['authorization_hash'],
              'target_hash':digest(target)}))
    db.flush()
    from .mqtt_daily import index_delivery
    index_delivery(db,p,row)
    return row


def enqueue_receipt(db,p,gateway,receipt,binding=None):
    if receipt.status!='awaiting_object_binding' or gateway.status!='active':return
    if binding is None:
        from .mqtt_binding_routes import active_bindings
        for current,route in active_bindings(db,p,gateway):
            if current.topic!=receipt.data['topic'] or current.config_hash!=receipt.data['config_hash']:continue
            if route:enqueue_delivery(db,p,gateway,receipt,current,route)
            else:enqueue_receipt(db,p,gateway,receipt,current)
        return
    key='mqtt-submit:'+receipt.id
    if db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key)):return
    db.add(Job(workspace_id=p.workspace_id,kind='submit_mqtt_receipt',idempotency_key=key,
        data={'actor_id':gateway.owner_id,'receipt_id':receipt.id,'binding_id':binding.id,
              'binding_hash':binding_hash(binding),'input_sha256':receipt.input_sha256}))
    db.flush()
    from .mqtt_daily import index_legacy
    index_legacy(db,p,receipt,binding)


def process_submission(db,p,job):
    receipt=scoped(db,MqttReceipt,job.data['receipt_id'],p)
    from .mqtt_binding_routes import find_binding
    binding=find_binding(db,p,job.data['binding_id'])
    if binding_hash(binding)!=job.data['binding_hash'] or receipt.input_sha256!=job.data['input_sha256']:
        raise HTTPException(409,'自动提交范围或原件摘要不一致')
    if job.data.get('delivery_id'):
        delivery=scoped(db,MqttReceiptDelivery,job.data['delivery_id'],p)
        if (delivery.receipt_id!=receipt.id or delivery.binding_id!=binding.id
                or delivery.route_id!=job.data.get('route_id') or delivery.data.get('target_hash')!=job.data.get('target_hash')
                or delivery.data.get('authorization_hash')!=job.data.get('authorization_hash')
                or delivery.data.get('route_version')!=job.data.get('route_version')):
            raise HTTPException(409,'原后台任务与固定的原件分发范围不一致')
        return promote_delivery(db,p,job.data['delivery_id'],None)
    # Old jobs remain fixed to their original legacy binding, even after adoption.
    checked_packet(db,p,receipt,binding,execution=True)
    return promote(db,p,receipt.id,receipt.version)


def promote_delivery(db,p,delivery_id,expected_version):
    require(p,'admin','operator')
    delivery=scoped(db,MqttReceiptDelivery,delivery_id,p)
    receipt=scoped(db,MqttReceipt,delivery.receipt_id,p)
    scoped(db,MqttGateway,receipt.gateway_id,p,lock=True)
    receipt=scoped(db,MqttReceipt,receipt.id,p,lock=True)
    from .mqtt_binding_routes import find_binding, binding_route
    binding=find_binding(db,p,delivery.binding_id)
    route=binding_route(db,p,binding,lock=True)
    delivery=scoped(db,MqttReceiptDelivery,delivery.id,p,lock=True)
    if (not route or delivery.route_id!=route.id or delivery.revision!=getattr(binding,'revision',1)
            or delivery.data.get('binding_hash')!=binding_hash(binding)
            or delivery.data.get('input_sha256')!=receipt.input_sha256):
        raise HTTPException(409,'原件分发依据与原关联版本不一致')
    decoded=checked_packet(db,p,receipt,binding,execution=True)
    if delivery.observation_id:
        observation=scoped(db,Observation,delivery.observation_id,p)
        guard(db,p,observation,execution=True)
        if expected_version is not None and observation.status=='failed':
            check_version(delivery,expected_version)
            extraction=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key=='extract:'+observation.id).with_for_update())
            if not extraction or extraction.status!='failed' or extraction.attempts>=3:
                raise HTTPException(409,'原处理尚未满足重试条件或已达到三次上限')
            observation.status='received';observation.version+=1
            observation.data={**observation.data,'previous_failure':observation.data.get('processing_error'),
                              'processing_error':None,'failure_type':None}
            extraction.status='queued'
            from .models import now
            extraction.available_at=now();extraction.lease_token=None;extraction.lease_until=None
            extraction.data={**extraction.data,'actor_id':p.user_id,'retry_reason':'用户确认恢复此原件的固定关联处理'}
            delivery.version+=1
            audit(db,p,'mqtt.delivery.retry',delivery.id,{'observation_id':observation.id,'attempt':extraction.attempts+1})
            from .state_refresh import source_changed
            source_changed(db,p,observation,'已保存原件处理重试，等待处理')
            db.flush()
            from .mqtt_daily import index_delivery
            index_delivery(db,p,delivery)
        return observation
    if expected_version is not None:check_version(delivery,expected_version)
    observation=_ingest_packet(db,p,receipt,binding,decoded)
    observation.data={**observation.data,'mqtt_delivery_id':delivery.id,
                      'mqtt_delivery_target_hash':delivery.data['target_hash']}
    delivery.observation_id=observation.id;delivery.status='submitted';delivery.version+=1
    receipt.status='submitted';receipt.version+=1
    audit(db,p,'mqtt.delivery.submitted',delivery.id,{'observation_id':observation.id,'revision':delivery.revision})
    db.flush()
    from .mqtt_daily import index_delivery
    index_delivery(db,p,delivery)
    return observation


def invalidate_conflict(db,p,receipt):
    from .mqtt_daily import index_original
    index_original(db,p,receipt)
    identities={receipt.data.get('observation_id')} - {None}
    deliveries=db.scalars(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
        MqttReceiptDelivery.receipt_id==receipt.id)).all()
    for delivery in deliveries:
        delivery.status='invalidated';delivery.version+=1
        delivery.data={**delivery.data,'error':'同一设备数据编号出现不同原文，原依据已失效'}
        if delivery.observation_id:identities.add(delivery.observation_id)
        db.flush()
        from .mqtt_daily import index_delivery
        index_delivery(db,p,delivery)
    for identity in sorted(identities):_invalidate_observation(db,p,receipt,identity)


def _invalidate_observation(db,p,receipt,identity):
    row=scoped(db,Observation,identity,p)
    if row.data.get('mqtt_receipt_id')!=receipt.id:raise HTTPException(409,'原收件关联异常')
    row.status='failed';row.version+=1
    row.data={**row.data,'processing_error':'同一设备数据编号出现不同原文，原依据已失效，请核查',
              'failure_type':'mqtt_record_content_conflict'}
    enqueue_event(db,p,'observation.failed','mqtt-conflict:'+row.id,
        {'observation_id':row.id,'entity_id':row.entity_id,'source_id':row.source_id,
         'status':'mqtt_record_content_conflict','observed_at':row.observed_at.isoformat(),
         'evidence_ids':row.data.get('evidence_ids',[]),'error':row.data['processing_error']})
    from .state_refresh import source_changed
    source_changed(db,p,row,row.data['processing_error'])
    reconcile(db,p,row.source_id,row.business_day,observation_id=row.id)
