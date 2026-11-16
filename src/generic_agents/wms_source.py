"""Published WMS scope catalog. Reads saved rasters, never performs network capture."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Manifest,digest
from .models import WMSConnection,WMSRead,GeoScene,Capability
from .services import scoped,published
from .wms import authorize,check_catalog,source_provenance
from .wms_protocol import within
from .wms_raster import RECIPE_HASH


def checked_scope(db,p,connection_id,scope_id):
    connection=scoped(db,WMSConnection,connection_id,p)
    authorize(connection.data['configuration'],p.workspace_id,db)
    from .config import settings
    if connection.data['configuration']['endpoint'] not in {v.strip() for v in settings().wms_allowed_endpoints.split(',') if v.strip()}:
        raise HTTPException(409,'地图服务的运行环境授权已撤销；已保存影像保留')
    if connection.status!='active':raise HTTPException(409,'地图服务已暂停或未确认读取范围；已保存影像保留')
    scope=connection.data['scopes'].get(scope_id)
    if not scope:raise HTTPException(409,'原地图范围已失效，请回资源页核对')
    content=check_catalog(db,p,connection.data)
    layer=next((r for r in content['layers'] if r['name']==scope['layer']),None)
    value={k:v for k,v in scope.items() if k!='id'}
    if (digest({'catalog_hash':connection.data['catalog_hash'],'scope':value})[:24]!=scope_id
        or not layer or scope['title']!=layer['title'] or scope['crs'] not in layer['bounds']
        or not within(scope['bbox'],layer['bounds'][scope['crs']]) or scope['style'] and scope['style'] not in layer['styles']):
        raise HTTPException(409,'地图来源范围与实际目录或固定摘要不一致')
    return connection,scope


def manifest(connection,scope,generation):
    release=int(digest({'catalog':connection.data['catalog_hash'],'scope':scope,'recipe':RECIPE_HASH})[:12],16)
    return Manifest(kind='source',namespace='wms',name='scope_'+connection.id.replace('-','')+'_'+scope['id'],
        version=f'{generation}.{connection.data["generation"]}.{release}',owner='已确认WMS地图范围',
        description=scope['title']+'；查询此范围已保存的地图与来源，不重新访问远端，不提供原始卫星波段或已确认业务状态。',
        allowed_agent_roles=['perception','data'],input_schema={'type':'object','additionalProperties':False},
        output_schema={'type':'object','required':['maps','has_more','not_authoritative','network_reads'],
            'additionalProperties':False,'properties':{'maps':{'type':'array','maxItems':10},'has_more':{'type':'boolean'},
                'not_authoritative':{'const':True},'network_reads':{'const':0}}},
        execution={'adapter':'wms_source','connection_id':connection.id,'scope_id':scope['id'],
            'publication_generation':generation,'connection_generation':connection.data['generation'],
            'catalog_sha256':connection.data['catalog_sha256'],'scope_hash':digest(scope),'runtime_hash':RECIPE_HASH,'side_effect':False},
        data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'},
        specification={'timezone':'Asia/Shanghai','raw_satellite_bands':False,'automatic_domain_use':False,'source_capture_time_verified':False})


def saved_maps(db,p,connection,scope,limit=10):
    rows=db.scalars(select(WMSRead).join(GeoScene,GeoScene.id==WMSRead.data['scene_id'].as_string()).where(
        WMSRead.workspace_id==p.workspace_id,WMSRead.connection_id==connection.id,WMSRead.status=='succeeded',
        WMSRead.data['request']['scope_id'].as_string()==scope['id'],GeoScene.workspace_id==p.workspace_id,GeoScene.status=='ready')
        .order_by(WMSRead.created_at.desc(),WMSRead.id).limit(limit+1)).all()
    result=[]
    for read in rows[:limit]:
        scene=scoped(db,GeoScene,read.data['scene_id'],p)
        provenance=source_provenance(db,p,scene)
        if read.data['connection_snapshot']['scope']!=scope:raise HTTPException(409,'已保存地图与登记范围不一致')
        result.append({'scene_id':scene.id,'scene_version':scene.version,'read_id':read.id,'name':scene.data['name'],
            'original_evidence_id':scene.evidence_id,'input_sha256':scene.data['input_sha256'],
            'configuration':copy.deepcopy(scene.data['configuration']),'metadata':copy.deepcopy(scene.data['metadata']),
            'provenance':provenance,'not_authoritative':True})
    return result,len(rows)>limit


def validate_manifest(db,p,value):
    execution=value.execution
    try:
        connection,scope=checked_scope(db,p,execution['connection_id'],execution['scope_id'])
        publication=connection.data.get('publications',{}).get(scope['id'],{})
        if publication.get('generation')!=execution['publication_generation'] or value.model_dump()!=manifest(connection,scope,publication['generation']).model_dump():
            raise HTTPException(422,'地图来源只能使用原登记范围的固定只读契约')
        maps,_=saved_maps(db,p,connection,scope,1)
        if not maps:raise HTTPException(409,'请先实际读取此范围并确认RGB，不能只凭目录登记可用来源')
    except (KeyError,TypeError,ValueError):raise HTTPException(409,'保存的地图来源登记结构不完整，请核对原记录') from None


def current_source(db,p,capability_id):
    cap=published(db,p,capability_id,'source')
    value=Manifest.model_validate(cap.data)
    if value.execution.get('adapter')!='wms_source':raise HTTPException(409,'此能力不是已登记地图来源')
    validate_manifest(db,p,value)
    connection,scope=checked_scope(db,p,value.execution['connection_id'],value.execution['scope_id'])
    publication=connection.data['publications'][scope['id']]
    if publication.get('capability_id')!=cap.id or publication.get('manifest_hash')!=cap.content_hash or digest(cap.data)!=cap.content_hash:
        raise HTTPException(409,'地图来源登记与发布版本不一致')
    return cap,connection,scope


def registered_read(db,p,cap):
    cap,connection,scope=current_source(db,p,cap.id)
    rows,more=saved_maps(db,p,connection,scope)
    return {'maps':rows,'has_more':more,'not_authoritative':True,'network_reads':0}


def resource_proof(db,p,resource):
    details=resource.get('details',{});cap,connection,scope=current_source(db,p,details.get('capability_id'))
    if (details.get('wms_connection_id')!=connection.id or details.get('scope_id')!=scope['id']
        or details.get('manifest_hash')!=cap.content_hash):raise HTTPException(409,'业务地图资源与服务器登记的服务/范围/版本不一致')
    return {'capability_id':cap.id,'manifest_hash':cap.content_hash,'scope':copy.deepcopy(scope),
        'connection_id':connection.id,'catalog_sha256':connection.data['catalog_sha256'],'source_kind':'wms_rendered_map',
        'raw_satellite_bands':False,'domain_quality_verified':False}
