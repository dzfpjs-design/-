"""Versioned, independently pausable destinations for one immutable MQTT original."""
from copy import deepcopy
from datetime import timezone
from hashlib import sha256
from fastapi import HTTPException
from sqlalchemy import select, text
from .contracts import digest
from .models import (MqttGateway, MqttBinding, MqttBindingRoute, MqttBindingRevision,
                     MqttReceipt, MqttReceiptDelivery, Capability, Entity, DomainInstance, Audit, now)
from .services import scoped, require, check_version, audit


def find_binding(db, p, identity):
    row = db.scalar(select(MqttBindingRevision).where(MqttBindingRevision.workspace_id == p.workspace_id,
                                                    MqttBindingRevision.id == identity))
    return row or scoped(db, MqttBinding, identity, p)


def source_binding(db, p, source_id):
    row = db.scalar(select(MqttBindingRevision).where(MqttBindingRevision.workspace_id == p.workspace_id,
                                                    MqttBindingRevision.source_id == source_id))
    return row or db.scalar(select(MqttBinding).where(MqttBinding.workspace_id == p.workspace_id,
                                                     MqttBinding.source_id == source_id))


def binding_route(db, p, binding, *, lock=False):
    query = select(MqttBindingRoute).where(MqttBindingRoute.workspace_id == p.workspace_id)
    if isinstance(binding, MqttBindingRevision): query = query.where(MqttBindingRoute.id == binding.route_id)
    else: query = query.where(MqttBindingRoute.legacy_binding_id == binding.id)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def checked_revision(db, p, row):
    from .mqtt_observations import binding_hash, binding_manifest, business_attributes
    if isinstance(row, MqttBindingRevision) and row.content_hash != binding_hash(row):
        raise HTTPException(409, '保存的关联版本摘要不一致，请核对原确认记录')
    gateway = scoped(db, MqttGateway, row.gateway_id, p)
    entity = scoped(db, Entity, row.entity_id, p)
    package = scoped(db, Capability, row.data['package_id'], p)
    rule = scoped(db, Capability, row.extractor_id, p)
    source = scoped(db, Capability, row.source_id, p)
    if (digest(row.data['config_snapshot']) != row.config_hash or row.topic not in row.data['config_snapshot']['topics']
            or row.data['device_external_id'] != row.data['config_snapshot']['topics'][row.topic]
            or entity.schema_capability_id != package.id or digest(package.data) != row.data['package_hash']
            or digest(rule.data) != row.data['extractor_hash'] or source.data != binding_manifest(row).model_dump()):
        raise HTTPException(409, '原关联的来源、对象或规则依据不一致')
    fields = {entry['attribute'] for entry in row.data['config_snapshot']['mapping'].values()}
    links = row.data['field_links']; props = package.data['specification']['entity_schemas'][entity.entity_type]['properties']
    units = {links[entry['attribute']]: entry['unit'] for entry in row.data['config_snapshot']['mapping'].values()}
    if (set(links) != fields or len(set(links.values())) != len(links) or units != row.data['field_units']
            or any(field not in props or props[field].get('type') not in {'number', 'integer'}
                   or (props[field].get('unit') is not None and props[field]['unit'] != units[field]) for field in links.values())):
        raise HTTPException(409, '原关联的测量字段或单位不一致')
    return gateway


def assert_current(db, p, binding):
    from .mqtt_observations import binding_hash
    route = binding_route(db, p, binding, lock=True)
    if route: _assert_head(db, p, route)
    if isinstance(binding, MqttBindingRevision):
        if (not route or route.current_revision != binding.revision
                or route.data.get('current_revision_id') != binding.id or route.status != 'active'):
            raise HTTPException(409, '此关联已暂停或已改版，原任务保留，不自动换用新规则')
        if binding.content_hash != binding_hash(binding):
            raise HTTPException(409, '原关联版本摘要不一致')
    elif route:
        if route.status != 'active' or route.current_revision != 1 or route.data.get('current_revision_id') != binding.id:
            raise HTTPException(409, '此旧关联已暂停或已改版，原任务保留待核对')
    return route


def _current(db, p, route):
    _assert_head(db, p, route)
    row = scoped(db, MqttBindingRevision, route.data.get('current_revision_id'), p)
    if row.route_id != route.id or row.revision != route.current_revision:
        raise HTTPException(409, '关联头与冻结版本不一致')
    checked_revision(db, p, row)
    return row


def _route_and_gateway(db, p, identity, *, lock=False, adopt=False):
    route = db.scalar(select(MqttBindingRoute).where(MqttBindingRoute.workspace_id == p.workspace_id,
                                                    MqttBindingRoute.id == identity))
    legacy = None if route else scoped(db, MqttBinding, identity, p)
    gateway = scoped(db, MqttGateway, route.gateway_id if route else legacy.gateway_id, p, lock=lock)
    if route:
        route = scoped(db, MqttBindingRoute, route.id, p, lock=lock)
    elif adopt:
        checked_revision(db, p, legacy)
        route = MqttBindingRoute(id=legacy.id, workspace_id=p.workspace_id, gateway_id=gateway.id,
            legacy_binding_id=legacy.id, current_revision=1, status='active',
            data={'schema': 'mqtt-route-v1', 'current_revision_id': legacy.id, 'requests': {},
                  'processing_after': legacy.created_at.isoformat(), 'include_existing': True})
        db.add(route); db.flush()
        revision = MqttBindingRevision(id=legacy.id, workspace_id=p.workspace_id, route_id=route.id, revision=1,
            gateway_id=legacy.gateway_id, topic=legacy.topic, config_hash=legacy.config_hash,
            entity_id=legacy.entity_id, source_id=legacy.source_id, extractor_id=legacy.extractor_id,
            data=deepcopy(legacy.data), created_at=legacy.created_at)
        from .mqtt_observations import binding_hash
        revision.content_hash = binding_hash(revision); db.add(revision); db.flush()
        _seal_head(db, p, route)
    return route, gateway, legacy


def _labels(db, p, row):
    saved=row.data.get('display')
    names=('entity_label','entity_type_label','instance_name','rule_name')
    if isinstance(saved,dict) and set(saved)==set(names) and all(isinstance(saved[key],str) for key in names):
        return deepcopy(saved)
    entity = scoped(db, Entity, row.entity_id, p); rule = scoped(db, Capability, row.extractor_id, p)
    package = scoped(db, Capability, row.data['package_id'], p)
    schema = package.data['specification']['entity_schemas'][entity.entity_type]
    instance = scoped(db, DomainInstance, row.data['instance']['id'], p) if row.data.get('instance') else None
    from .instance_domain import object_display
    display=object_display(entity,package)
    return {'entity_label': display['label'],
            'entity_type_label': display['type'],
            'instance_name': (instance.data.get('name') or '业务实例') if instance else '工作区业务',
            'rule_name': rule.data.get('description') or rule.name}


def revision_view(db, p, row):
    checked_revision(db, p, row)
    return {'binding_id': row.id, 'source_id': row.source_id, 'revision': getattr(row, 'revision', 1),
            'config_hash': row.config_hash, 'topic': row.topic, 'entity_id': row.entity_id,
            'extractor_id': row.extractor_id, 'field_links': deepcopy(row.data['field_links']),
            'field_units': deepcopy(row.data['field_units']), 'instance': deepcopy(row.data.get('instance')),
            'created_at': row.created_at.astimezone(timezone.utc).isoformat(), **_labels(db, p, row)}


def route_view(db, p, route=None, legacy=None):
    row = _current(db, p, route) if route else legacy
    gateway = checked_revision(db, p, row)
    changed = digest(gateway.data) != row.config_hash
    return {**revision_view(db, p, row), 'id': route.id if route else row.id,
            'route_id': route.id if route else row.id, 'gateway_id': gateway.id,
            'version': route.version if route else 1, 'status': route.status if route else 'active',
            'legacy': bool(not route or route.legacy_binding_id), 'needs_review': changed,
            'issue': '接入配置已变化，请保存新的关联版本' if changed else None}


def list_routes(db, p, gateway_id):
    scoped(db, MqttGateway, gateway_id, p)
    heads = db.scalars(select(MqttBindingRoute).where(MqttBindingRoute.workspace_id == p.workspace_id,
                                                     MqttBindingRoute.gateway_id == gateway_id)).all()
    imported = {row.legacy_binding_id for row in heads if row.legacy_binding_id}
    old = db.scalars(select(MqttBinding).where(MqttBinding.workspace_id == p.workspace_id,
                                              MqttBinding.gateway_id == gateway_id)).all()
    return sorted([route_view(db, p, route=row) for row in heads]
                  + [route_view(db, p, legacy=row) for row in old if row.id not in imported], key=lambda row: row['route_id'])


def association_snapshots(db, p, gateway_id):
    rows = list_routes(db, p, gateway_id)
    fields = ('route_id', 'version', 'status', 'binding_id', 'revision', 'source_id', 'config_hash',
              'topic', 'entity_id', 'extractor_id', 'field_links', 'field_units', 'instance', 'needs_review')
    from .mqtt_observations import binding_hash
    output = []
    for row in rows:
        binding = find_binding(db, p, row['binding_id'])
        route = binding_route(db, p, binding)
        scope = (_assert_head(db, p, route) if route else {'include_existing': True,
            'processing_after': binding.created_at.isoformat(), 'basis': 'legacy_explicit_single_binding'})
        output.append({**{key: row[key] for key in fields}, 'hash': binding_hash(binding), 'processing_scope': scope})
    return output


def revisions(db, p, identity):
    route, gateway, legacy = _route_and_gateway(db, p, identity)
    if not route: return [revision_view(db, p, legacy)]
    rows = db.scalars(select(MqttBindingRevision).where(MqttBindingRevision.workspace_id == p.workspace_id,
        MqttBindingRevision.route_id == route.id).order_by(MqttBindingRevision.revision.desc())).all()
    return [revision_view(db, p, row) for row in rows]


def _fingerprint(operation, body):
    if body.get('instance_release_id') is None:
        body={key:value for key,value in body.items() if key!='instance_release_id'}
    return digest({'operation': operation, 'body': body})


def _check_cas(row, expected):
    if row.version!=expected:
        raise HTTPException(409,'关联版本已更新，本次请求未提交。请对照当前版本重新确认。')


def _replay(db, p, route, request_id, fingerprint):
    _assert_head(db,p,route)
    old = route.data.get('requests', {}).get(request_id)
    if not old: return False
    if old.get('actor_id') != p.user_id or old.get('hash') != fingerprint:
        raise HTTPException(409, '此保存请求已用于其他内容，请读取保存结果后核对')
    return True


def _head_snapshot(route):
    return {'route_id': route.id, 'version': route.version, 'status': route.status,
            'current_revision': route.current_revision, 'current_revision_id': route.data.get('current_revision_id'),
            'processing_after': route.data.get('processing_after'),
            'include_existing': route.data.get('include_existing'),
            'requests_hash': digest(route.data.get('requests', {}))}


def _seal_head(db, p, route):
    """The existing append-only audit ledger freezes each confirmed authorization."""
    snapshot = {**_head_snapshot(route), 'actor_id':p.user_id}
    proof = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action='mqtt.route.authorization_confirmed',
        object_id=route.id, data={'schema':'mqtt-route-authorization-v1','snapshot':snapshot,
                                'snapshot_hash':digest(snapshot)})
    db.add(proof);db.flush()
    route.data = {**route.data, 'authorization_id': proof.id, 'authorization_hash': digest(snapshot)}


def _assert_head(db, p, route):
    proof = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.id == route.data.get('authorization_id')))
    snapshot = {**_head_snapshot(route), 'actor_id':proof.actor_id if proof else None}
    if (not proof or proof.action != 'mqtt.route.authorization_confirmed' or proof.object_id != route.id
            or proof.data.get('schema') != 'mqtt-route-authorization-v1' or proof.data.get('snapshot') != snapshot
            or proof.data.get('snapshot_hash') != digest(snapshot)
            or route.data.get('authorization_hash') != digest(snapshot)
            or type(snapshot['include_existing']) is not bool or snapshot['status'] not in {'active','paused'}):
        raise HTTPException(409, '关联的执行范围与原确认记录不一致，请保留原件并核对')
    from .services import timestamp
    if not timestamp(snapshot['processing_after']):raise HTTPException(409, '关联的生效时间缺少原确认依据')
    return deepcopy(snapshot)


def checked_delivery(db, p, delivery, binding, receipt, *, execution):
    from .mqtt_observations import binding_hash
    target = {'receipt_id': receipt.id, 'binding_id': binding.id, 'route_id': getattr(binding, 'route_id', binding.id),
              'revision': getattr(binding, 'revision', 1), 'binding_hash': binding_hash(binding),
              'input_sha256': receipt.input_sha256}
    if (delivery.receipt_id != target['receipt_id'] or delivery.binding_id != target['binding_id']
            or delivery.route_id != target['route_id'] or delivery.revision != target['revision']
            or delivery.data.get('original_target') != target or delivery.data.get('target_hash') != digest(target)
            or delivery.data.get('binding_hash') != target['binding_hash']
            or delivery.data.get('input_sha256') != target['input_sha256']):
        raise HTTPException(409, '原件分发与原冻结对象、关联或摘要不一致')
    proof = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.id == delivery.data.get('authorization_id')))
    snapshot = proof.data.get('snapshot', {}) if proof else {}
    if (not proof or proof.action != 'mqtt.route.authorization_confirmed' or proof.object_id != delivery.route_id
            or proof.data.get('schema') != 'mqtt-route-authorization-v1'
            or digest(snapshot) != proof.data.get('snapshot_hash')
            or digest(snapshot) != delivery.data.get('authorization_hash')
            or snapshot.get('route_id') != delivery.route_id or snapshot.get('current_revision_id') != binding.id
            or snapshot.get('current_revision') != delivery.revision or snapshot.get('status') != 'active'
            or snapshot.get('actor_id') != proof.actor_id
            or snapshot.get('version') != delivery.data.get('route_version')):
        raise HTTPException(409, '原件分发缺少原确认的执行授权依据')
    if execution:
        route = assert_current(db, p, binding)
        if (not route or route.version != delivery.data['route_version']
                or route.data['authorization_id'] != proof.id or not allows_receipt(route, receipt)):
            raise HTTPException(409, '关联的执行授权或生效范围已变化，旧分发保留待核对')
    return target


def _remember(db, route, p, request_id, fingerprint):
    requests = deepcopy(route.data.get('requests', {}))
    if len(requests) >= 4096: raise HTTPException(409, '关联确认记录已达审计上限，请联系管理员归档')
    requests[request_id] = {'hash': fingerprint, 'actor_id': p.user_id, 'version': route.version,
                            'revision': route.current_revision}
    route.data = {**route.data, 'requests': requests}
    _seal_head(db, p, route)


def _revision(db, p, route, gateway, body, number):
    from .mqtt_observations import prepare_binding_data, binding_manifest, binding_hash
    obj, extractor, data = prepare_binding_data(db, p, gateway, body['topic'], body['entity_id'],
                                               body['extractor_id'], body.get('field_links'),
                                               instance_release_id=body.get('instance_release_id'))
    row = MqttBindingRevision(workspace_id=p.workspace_id, route_id=route.id, revision=number,
        request_id=body['request_id'], request_hash=_fingerprint('create' if number == 1 else 'update', body),
        gateway_id=gateway.id, topic=body['topic'], config_hash=digest(gateway.data),
        entity_id=obj.id, extractor_id=extractor.id, data=data)
    db.add(row); db.flush()
    manifest = binding_manifest(row).model_dump(); fingerprint = digest(manifest)
    source = Capability(workspace_id=p.workspace_id, namespace=manifest['namespace'], name=manifest['name'],
        release_version=manifest['version'], kind='source', status='published', content_hash=fingerprint,
        validated_hash=fingerprint, data=manifest)
    db.add(source); db.flush(); row.source_id = source.id; row.content_hash = binding_hash(row)
    route.current_revision = number
    route.data = {**route.data, 'current_revision_id': row.id, 'processing_after': now().isoformat(),
                  'include_existing': body['include_existing']}
    return row


def create_route(db, p, gateway_id, body):
    require(p, 'admin')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    gateway = scoped(db, MqttGateway, gateway_id, p, lock=True)
    lock = int.from_bytes(sha256((p.workspace_id + ':mqtt-route:' + body['request_id']).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    route = db.scalar(select(MqttBindingRoute).where(MqttBindingRoute.workspace_id == p.workspace_id,
        MqttBindingRoute.creation_request_id == body['request_id']))
    fingerprint = digest({'gateway_id': gateway_id, 'request': _fingerprint('create', body)})
    if route:
        if route.gateway_id != gateway_id or not _replay(db, p, route, body['request_id'], fingerprint):
            raise HTTPException(409, '此保存请求已用于其他关联')
        return route
    _check_cas(gateway, body['expected_version'])
    existing_revision=db.scalar(select(MqttBindingRevision).where(MqttBindingRevision.workspace_id==p.workspace_id,
        MqttBindingRevision.request_id==body['request_id']))
    if existing_revision:raise HTTPException(409,'此保存请求已用于其他关联版本')
    route = MqttBindingRoute(workspace_id=p.workspace_id, gateway_id=gateway.id,
        creation_request_id=body['request_id'], data={'schema': 'mqtt-route-v1', 'requests': {}}, status='active')
    db.add(route); db.flush(); _revision(db, p, route, gateway, body, 1)
    _remember(db, route, p, body['request_id'], fingerprint)
    audit(db, p, 'mqtt.route.confirmed', route.id, {'revision': 1, 'include_existing': body['include_existing']})
    from .mqtt_observations import schedule_receipts
    schedule_receipts(db, p, gateway)
    return route


def update_route(db, p, identity, body):
    require(p, 'admin')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    route, gateway, legacy = _route_and_gateway(db, p, identity, lock=True, adopt=True)
    fingerprint = _fingerprint('update', body)
    if _replay(db, p, route, body['request_id'], fingerprint): return route
    _check_cas(route, body['expected_version']); _current(db, p, route)
    existing = db.scalar(select(MqttBindingRevision).where(MqttBindingRevision.workspace_id == p.workspace_id,
        MqttBindingRevision.request_id == body['request_id']))
    if existing: raise HTTPException(409, '此保存请求已用于其他关联版本')
    _revision(db, p, route, gateway, body, route.current_revision + 1); route.version += 1
    _remember(db, route, p, body['request_id'], fingerprint)
    audit(db, p, 'mqtt.route.revised', route.id, {'revision': route.current_revision, 'include_existing': body['include_existing']})
    from .mqtt_observations import schedule_receipts
    schedule_receipts(db, p, gateway)
    return route


def set_route_status(db, p, identity, body):
    require(p, 'admin')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    if body['include_existing']:
        raise HTTPException(422, '暂停或恢复不重放既有原件；如需处理尚未完成原件，请保存新关联版本并明确选择处理既有原件')
    route, gateway, legacy = _route_and_gateway(db, p, identity, lock=True, adopt=True)
    fingerprint = _fingerprint('status', body)
    if _replay(db, p, route, body['request_id'], fingerprint): return route
    _check_cas(route, body['expected_version']); row = _current(db, p, route)
    if body['status'] == 'active':
        from .mqtt_inbox import validate_configuration
        validate_configuration(gateway.data,workspace_id=p.workspace_id,db=db)
        if digest(gateway.data) != row.config_hash: raise HTTPException(409, '接入配置已变化，请先保存新的关联版本')
    route.status = body['status']; route.version += 1
    route.data = {**route.data, 'processing_after': now().isoformat(), 'include_existing': body['include_existing']}
    _remember(db, route, p, body['request_id'], fingerprint)
    audit(db, p, 'mqtt.route.' + route.status, route.id, {'revision': route.current_revision, 'include_existing': body['include_existing']})
    if route.status == 'active':
        from .mqtt_observations import schedule_receipts
        schedule_receipts(db, p, gateway)
    return route


def active_bindings(db, p, gateway):
    heads = db.scalars(select(MqttBindingRoute).where(MqttBindingRoute.workspace_id == p.workspace_id,
        MqttBindingRoute.gateway_id == gateway.id).order_by(MqttBindingRoute.id)).all()
    imported = {row.legacy_binding_id for row in heads if row.legacy_binding_id}
    result = []
    for head in heads:
        if head.status != 'active': continue
        try: row = _current(db, p, head)
        except (HTTPException, KeyError, TypeError, ValueError): continue
        if row.config_hash == digest(gateway.data): result.append((row, head))
    old = db.scalars(select(MqttBinding).where(MqttBinding.workspace_id == p.workspace_id,
        MqttBinding.gateway_id == gateway.id, MqttBinding.config_hash == digest(gateway.data))).all()
    result.extend((row, None) for row in old if row.id not in imported)
    return result


def allows_receipt(route, receipt):
    if not route or route.data.get('include_existing') is True: return True
    from .services import timestamp
    threshold = timestamp(route.data.get('processing_after'))
    return bool(threshold and receipt.created_at >= threshold)


def delivery_view(db, p, row, job=None, observation=None):
    binding = find_binding(db, p, row.binding_id)
    receipt = scoped(db, MqttReceipt, row.receipt_id, p)
    route = binding_route(db, p, binding)
    valid = True
    try:
        checked_revision(db, p, binding);checked_delivery(db,p,row,binding,receipt,execution=False)
        if route:_assert_head(db,p,route)
    except (HTTPException, KeyError, TypeError, ValueError):valid=False
    current = bool(valid and route and route.status=='active' and route.current_revision==row.revision
        and route.data.get('current_revision_id')==binding.id
        and route.version==row.data.get('route_version') and allows_receipt(route,receipt))
    gateway = scoped(db,MqttGateway,receipt.gateway_id,p)
    context_ready = True
    if binding.data.get('instance'):
        instance = scoped(db,DomainInstance,binding.data['instance']['id'],p)
        context_ready = (instance.status=='partially_active'
                         and instance.active_release_id==binding.data['instance']['release_id'])
    from .services import published
    try:
        for identity in (binding.data['package_id'],binding.source_id,binding.extractor_id):published(db,p,identity)
        if context_ready and binding.data.get('instance'):
            from .instance_domain import current_context
            entity=scoped(db,Entity,binding.entity_id,p)
            context=current_context(db,p,entity,release_id=binding.data['instance']['release_id'])
            if not context or context[0].id!=binding.data['instance']['id']:context_ready=False
    except HTTPException:context_ready=False
    can_submit = bool(current and gateway.status=='active' and digest(gateway.data)==binding.config_hash
        and not receipt.data.get('issues') and receipt.status!='quarantined'
        and context_ready
        and (not observation or observation.status=='failed'))
    return {'delivery_id': row.id, 'version': row.version, 'route_id': row.route_id,
            'binding_id': row.binding_id, 'revision': row.revision, 'entity_id': binding.entity_id,
            'source_id': binding.source_id, **_labels(db, p, binding), 'status': row.status,
            'observation_id': row.observation_id,
            'current': current, 'needs_review': not valid or not current, 'can_submit': can_submit,
            'submission': {'status': job.status, 'error': job.data.get('error'), 'attempts': job.attempts} if job else None,
            'processing': {'status': observation.status, 'error': observation.data.get('processing_error')} if observation else None}
