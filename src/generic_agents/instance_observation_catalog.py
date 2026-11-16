"""Instance-bound, read-only access to published, already saved observations."""
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from .contracts import digest
from .instance_api import active
from .services import published, scoped
from .setup_api import DB, P
from .models import AuthSession, Membership, StreamSession, User, now

router = APIRouter(prefix='/api/instance-observations')


def ensure_current_principal(db, p):
    """Recheck a live request's authenticated session and workspace role."""
    auth = db.get(AuthSession, p.session_id)
    user = db.get(User, p.user_id)
    member = db.scalar(select(Membership).where(Membership.user_id == p.user_id,
                                                Membership.workspace_id == p.workspace_id))
    if (not auth or auth.revoked or auth.user_id != p.user_id or auth.expires_at <= now()
            or not user or not user.active or not member or member.role != p.role):
        raise HTTPException(403, '账号会话或工作区授权已失效')


def media_resource_proof(db, p, resource):
    from .media import channel_manifest, validated_channel
    details = resource.get('details', {})
    if details.get('live_readonly') is True:
        from .instance_live_video import readonly_proof
        return readonly_proof(db, p, details.get('channel_id'))
    cap = published(db, p, details.get('capability_id'), 'source')
    channel, gateway = validated_channel(db, p, details.get('channel_id'))
    expected = channel_manifest(channel, gateway).model_dump(mode='json')
    if (cap.data != expected or cap.content_hash != digest(cap.data)
            or channel.data.get('capability_id') != cap.id):
        raise HTTPException(409, '媒体来源登记与通道或发布版本不一致')
    return {'source_kind': 'media_recorded_video', 'capability_id': cap.id,
            'channel_id': channel.id, 'manifest_hash': cap.content_hash,
            'streaming_ready': False, 'physical_device_identity': 'unconfirmed'}


def source_kind(resource):
    if resource.get('category') == 'video' and resource.get('protocol') == 'RTSP / H.264':
        return 'video'
    if resource.get('category') == 'maps' and resource.get('protocol') == 'ArcGIS FeatureServer':
        return 'vector'
    if resource.get('category') == 'maps' and resource.get('protocol') in ('WMS', 'WMTS', 'ArcGIS MapServer', 'ArcGIS ImageServer'):
        return 'imagery'
    return None


def resources(db, p, instance_id):
    instance, release = active(db, p, instance_id)
    snapshot = release.data.get('snapshot', {})
    items = snapshot.get('resources', []) if isinstance(snapshot, dict) else []
    if not isinstance(items, list):
        raise HTTPException(409, '已发布资源目录无效')
    businesses=snapshot.get('businesses',[])
    if not isinstance(businesses,list):raise HTTPException(409,'已发布业务目录无效')
    enabled=set(instance.data.get('enabled_business_ids',[]))
    enabled_names={b.get('name') for b in businesses if isinstance(b,dict) and b.get('available') is True and b.get('id') in enabled and isinstance(b.get('name'),str)}
    # New fixed-map sources require an enabled associated business.  Do not
    # expose an unpublished/disabled business's saved image from a partial release.
    from .map_access import can_read
    return [r for r in items if isinstance(r, dict) and source_kind(r) and
            (source_kind(r) not in ('imagery','vector','video') or can_read(db,p,[n for n in (r.get('businesses',[]) if isinstance(r.get('businesses'),list) else []) if isinstance(n,str) and n in enabled_names])) and
            (r.get('protocol') not in ('WMTS','ArcGIS MapServer','ArcGIS ImageServer','ArcGIS FeatureServer') or
             (isinstance(r.get('businesses'),list) and any(n in enabled_names for n in r['businesses'])))]


def verified(db, p, resource):
    if resource.get('status') in ('disabled', 'inactive', 'deleted', 'paused'):
        raise HTTPException(409, '来源已停用')
    saved = resource.get('backend_source_proof')
    if not isinstance(saved, dict):
        raise HTTPException(409, '缺少发布时来源版本证明，请重新核对并发布实例')
    if source_kind(resource)=='vector':
        from .feature_gateway_api import resource_proof,current_source
        actual=resource_proof(db,p,resource)
        if saved!=actual:raise HTTPException(409,'发布的要素来源版本已变化')
        _,row,_=current_source(db,p,actual['capability_id'])
        return [{'scene_id':row.id,'name':'已保存矢量要素'}]
    if source_kind(resource) == 'video':
        actual = media_resource_proof(db, p, resource)
        if saved != actual:
            raise HTTPException(409, '发布的来源版本已变化，请重新核对并发布实例')
        if actual['source_kind'] == 'media_live_readonly_v1':
            return []
        from .media import registered_read
        cap = published(db, p, actual['capability_id'], 'source')
        return registered_read(db, p, cap.data['execution'])['sessions']
    if resource.get('protocol') in ('WMTS','ArcGIS MapServer','ArcGIS ImageServer'):
        from .map_saved_source import resource_proof, current_source, registered_read
    else:
        from .wms_source import resource_proof, current_source, registered_read
    actual = resource_proof(db, p, resource)
    if saved != actual:
        raise HTTPException(409, '发布的来源版本已变化，请重新核对并发布实例')
    cap, _, _ = current_source(db, p, actual['capability_id'])
    return registered_read(db, p, cap)['maps']


def one_resource(db, p, instance_id, resource_id):
    found = [r for r in resources(db, p, instance_id) if r.get('id') == resource_id]
    if len(found) != 1:
        raise HTTPException(404, '实例未配置此来源或来源标识不唯一')
    return found[0]


@router.get('/{instance_id}')
def catalog(instance_id: str, p: P, db: DB):
    all_resources = resources(db, p, instance_id)
    counts = {}
    for r in all_resources:
        identity = r.get('id')
        if isinstance(identity, str):
            counts[identity] = counts.get(identity, 0) + 1
    sources = []
    for r in all_resources:
        identity = r.get('id')
        if not isinstance(identity, str) or not identity or '/' in identity or len(identity) > 200:
            continue
        kind = source_kind(r)
        item = {'id': identity, 'name': str(r.get('name') or '未命名来源')[:200],
                'kind': kind, 'protocol': str(r.get('protocol') or '')[:40], 'status': 'unavailable', 'message': '',
                'streaming_ready': False, 'live_map_ready': False, 'observations': []}
        try:
            if counts[identity] != 1:
                raise HTTPException(409, '来源标识重复，请重新核对配置')
            rows = verified(db, p, r)
            for row in rows:
                observation_id = row['session_id'] if kind == 'video' else row['scene_id']
                saved_at = None
                if kind == 'video':
                    session = scoped(db, StreamSession, observation_id, p)
                    saved_at = session.created_at.isoformat()
                elif r.get('protocol') in ('WMTS','ArcGIS MapServer','ArcGIS ImageServer','ArcGIS FeatureServer'):
                    from .models import Job
                    saved_at = scoped(db,Job,observation_id,p).created_at.isoformat()
                base = '/api/instance-observations/' + quote(instance_id, safe='') + '/sources/' + quote(identity, safe='')
                item['observations'].append({'id': observation_id, 'name': '已保存视频片段' if kind == 'video' else str(row.get('name') or '已保存地图')[:200],
                    'captured_at': None, 'saved_at': saved_at, 'partial': bool(row.get('partial', False)),
                    'historical_configuration': bool(row.get('historical_configuration', False)),
                    'url': base + '/observations/' + quote(observation_id, safe=''),
                    'media_type': 'video/mp4' if kind == 'video' else 'application/geo+json' if kind=='vector' else 'image/png'})
            item['status'] = 'available'
            # The request is confined to the published WMS scope. This flag
            # denotes support, not that the remote service is currently up.
            if kind == 'imagery' and r.get('protocol') == 'WMS':
                item['live_map_ready'] = True
            elif kind == 'video':
                item['live_supported'] = True
                item['live_url'] = ('/api/instance-observations/' + quote(instance_id, safe='')
                                    + '/sources/' + quote(identity, safe='') + '/live')
            item['message'] = ('可发起实时预览，在线状态待检测；另可查看已保存视频片段，拍摄时间未经校准。' if kind == 'video'
                               else '已保存的矢量要素，需结合来源核验。' if kind=='vector' else '已保存的地图渲染图，不代表原始遥感波段或事件证据。')
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError):
            item['message'] = '来源未启用、未通过授权或发布版本已失效，请回配置页核对并重新发布。'
        sources.append(item)
    return {'instance_id': instance_id, 'sources': sources}


@router.get('/{instance_id}/sources/{resource_id}/wms-live')
def live_wms(instance_id: str, resource_id: str, p: P, db: DB):
    """Render only the exact scope frozen in the active release.

    The request never accepts an endpoint, layer, credentials, extent, or size
    from the browser. It remains a WMS render, not proof of capture time.
    """
    import struct
    import httpx
    from .wms import fetch, authorize
    from .models import WMSConnection
    from .config import settings
    from .wms_protocol import map_params, MAP_LIMIT
    from .wms_source import current_source
    from fastapi.responses import Response

    resource = one_resource(db, p, instance_id, resource_id)
    if source_kind(resource) != 'imagery' or resource.get('protocol') != 'WMS':
        raise HTTPException(404, '此来源不是 WMS 地图服务')
    verified(db, p, resource)
    proof = resource['backend_source_proof']
    cap, connection, scope = current_source(db, p, proof['capability_id'])
    connection_id, capability_id = connection.id, cap.id
    connection_version, capability_version = connection.version, cap.version
    connection_hash, capability_hash = digest(connection.data), digest(cap.data)
    params = map_params(connection.data['configuration']['version'], scope, scope['bbox'], 1024, 768)

    def guard():
        # Re-read database state while the remote response is arriving, so a
        # paused instance or revoked source is not masked by the ORM cache.
        db.expire_all()
        ensure_current_principal(db, p)
        current = one_resource(db, p, instance_id, resource_id)
        if current != resource:
            raise HTTPException(409, '业务实例来源已变化，请刷新后重试')
        # The immutable catalog can be megabytes. Parsing it for each response
        # chunk exhausts the network deadline; compare fresh DB state and its
        # digest for each chunk instead, then perform full verification below.
        c = scoped(db, WMSConnection, connection_id, p)
        a = published(db, p, capability_id, 'source')
        if (c.status != 'active' or c.version != connection_version or digest(c.data) != connection_hash
                or a.version != capability_version or a.content_hash != capability_hash
                or digest(a.data) != capability_hash):
            raise HTTPException(409, '地图服务或已发布来源已变化，请刷新后重试')
        authorize(c.data['configuration'], p.workspace_id, db)
        if c.data['configuration']['endpoint'] not in {v.strip() for v in settings().wms_allowed_endpoints.split(',') if v.strip()}:
            raise HTTPException(409, '地图服务的运行环境授权已撤销')

    guard()
    try:
        body, mime = fetch(connection.data['configuration'], params, MAP_LIMIT, guard, db)
        # Check file signature and declared PNG dimensions before forwarding.
        if (mime != 'image/png' or len(body) < 24 or body[:8] != b'\x89PNG\r\n\x1a\n'
                or body[12:16] != b'IHDR' or struct.unpack('>II', body[16:24]) != (1024, 768)):
            raise ValueError('WMS 返回的图像格式或尺寸与请求不一致')
    except (ValueError, OSError, httpx.HTTPError):
        raise HTTPException(502, 'WMS 服务暂时无法读取，请检查服务状态后重试') from None
    guard()
    # Validate saved catalog evidence, source proof, and sample again before
    # any bytes reach the caller, including changes while the response arrived.
    verified(db, p, one_resource(db, p, instance_id, resource_id))
    guard()
    return Response(body, media_type='image/png', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'X-Image-Capture-Time': 'unknown',
    })


@router.get('/{instance_id}/sources/{resource_id}/observations/{observation_id}')
def observation(instance_id: str, resource_id: str, observation_id: str, p: P, db: DB):
    resource = one_resource(db, p, instance_id, resource_id)
    rows = verified(db, p, resource)
    if source_kind(resource)=='vector':
        if not any(row.get('scene_id')==observation_id for row in rows):raise HTTPException(404,'此实例来源没有该要素记录')
        from .feature_gateway_api import read_record
        import json
        row=read_record(db,p,observation_id)
        return Response(json.dumps(row.data['features'],ensure_ascii=False,allow_nan=False),media_type='application/geo+json',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    if source_kind(resource) == 'imagery':
        if not any(row.get('scene_id') == observation_id for row in rows):
            raise HTTPException(404, '此实例来源没有该地图')
        if resource.get('protocol') in ('WMTS','ArcGIS MapServer','ArcGIS ImageServer'):
            from .map_services_api import image_internal
            return image_internal(observation_id,p,db)
        from .geo_api import preview_internal
        return preview_internal(observation_id, p, db)
    row = next((r for r in rows if r.get('session_id') == observation_id), None)
    if not row:
        raise HTTPException(404, '此实例来源没有该视频片段')
    from .models import VideoRun
    run = scoped(db, VideoRun, row['video_run_id'], p)
    if run.evidence_id != row['original_evidence_id'] or run.data.get('input_sha256') != row['sha256']:
        raise HTTPException(409, '保存的视频与本次通道读取不一致')
    from .video_api import original
    response = original(row['video_run_id'], p, db)
    response.headers['Content-Disposition'] = 'inline'
    response.headers['Cache-Control'] = 'private, no-store'
    return response
