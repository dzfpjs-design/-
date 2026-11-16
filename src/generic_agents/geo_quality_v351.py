"""Scoped, local quality-derived raster inputs; no claims about physical truth.

Uses existing Job/Audit/Evidence/GeoScene storage. Four user-selected originals
are frozen before execution; the source association remains user-declared.
"""
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt
from sqlalchemy import select, text

from . import models as m
from .config import settings
from .contracts import Contract, Manifest, digest
from .geo_codec import RECIPE_HASH as GEO_HASH
from .geo_storage import path_for
from .services import scoped, require, register, validate_capability, publish, published

KIND='prepare_geo_quality_v351'
KIND_V352='prepare_geo_quality_v352'
REQUEST_ACTION='geo.quality_requested_v351'
RESULT_ACTION='geo.quality_result_v351'
SCHEMA='geo-quality-request-v351'
RUNNER=Path(__file__).with_name('geo_quality_scl_runner_v351.py')
RUNNER_HASH=hashlib.sha256(RUNNER.read_bytes()).hexdigest()
POLICY={'adapter':'sentinel2_scl_v1','allowed':[4,5,6],'buffer_pixels':6,
        'source_association':'user_declared_unverified','resampling':'nearest',
        'source_resolution_m':20,'target_resolution_m':10}


def need(value,message='质量来源、原请求或保存结果无法核对；原件保留',status=409):
    if not value:raise HTTPException(status,message)


def manifest():
    return Manifest(kind='tool',namespace='platform_geo_quality',name='sentinel2_scl_v1',version='1.0.0',
        owner='本地影像质量适配器',description='按用户声明匹配Sentinel-2 SCL生成保守有效区；分类、关联与领域效果未核验。',
        allowed_agent_roles=['perception','data'],input_schema={'type':'object'},output_schema={'type':'object'},
        execution={'adapter':'geo_quality_scl_v351','runner_sha256':RUNNER_HASH,'side_effect':False},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},specification=POLICY)


def adapter(db,p,identity):
    cap=published(db,p,identity,'tool');expected=manifest().model_dump(mode='json')
    need(cap.data==expected and cap.content_hash==digest(expected),'质量适配器版本不可用；请重新核对')
    return cap


def register_adapter(db,p):
    require(p,'admin');value=manifest();db.execute(text('SELECT pg_advisory_xact_lock(:n)'),
        {'n':int.from_bytes(hashlib.sha256((p.workspace_id+':geo-quality-scl-v351').encode()).digest()[:8],'big',signed=True)})
    cap=db.scalar(select(m.Capability).where(m.Capability.workspace_id==p.workspace_id,
        m.Capability.namespace==value.namespace,m.Capability.name==value.name,m.Capability.release_version==value.version))
    if cap is None:
        cap=register(db,p,value);validate_capability(db,p,cap.id,cap.version)
        publish(db,p,cap.id,cap.version,'本地质量候选；SCL分类与用户声明的来源关联仍需核验')
    else:adapter(db,p,cap.id)
    return cap


class Create(Contract):
    schema:Literal['geo-quality-request-v351','geo-quality-request-v352']='geo-quality-request-v352'
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    adapter_id:str=Field(min_length=1,max_length=100)
    before_image_id:str=Field(min_length=1,max_length=100)
    before_image_version:StrictInt=Field(ge=1)
    after_image_id:str=Field(min_length=1,max_length=100)
    after_image_version:StrictInt=Field(ge=1)
    before_quality_id:str=Field(min_length=1,max_length=100)
    before_quality_version:StrictInt=Field(ge=1)
    after_quality_id:str=Field(min_length=1,max_length=100)
    after_quality_version:StrictInt=Field(ge=1)
    association_confirmed:StrictBool


def _sources(db,p,body,*,lock=False):
    fields=('before_image','after_image','before_quality','after_quality')
    identities=[getattr(body,k+'_id') for k in fields]
    need(len(set(identities))==4,'请选择两期不同影像及各自不同的质量层',422)
    if lock:
        # Deterministic order prevents inverse A/B lock ordering.
        rows=list(db.scalars(select(m.GeoScene).where(m.GeoScene.workspace_id==p.workspace_id,
            m.GeoScene.id.in_(identities)).order_by(m.GeoScene.id).with_for_update(read=True)))
        lookup={r.id:r for r in rows}
    else:
        lookup={identity:scoped(db,m.GeoScene,identity,p) for identity in identities}
    result={}
    for key in fields:
        scene=lookup.get(getattr(body,key+'_id'));need(scene is not None,'影像或质量原件不在当前工作区',404)
        need(scene.version==getattr(body,key+'_version'),'所选影像或质量层已更新，请重新核对')
        need(scene.status=='ready' if key.endswith('image') else scene.status in {'parsed','ready'},'请先完成影像或质量层的本地读取')
        meta=scene.data.get('metadata');need(type(meta) is dict and meta.get('georeference_status')=='affine_available','请选择已有坐标参考的原件')
        if key.endswith('image'):
            need(meta.get('bands')==3 and meta.get('dtypes')==['uint8']*3 and scene.data.get('configuration',{}).get('map_image_confirmed') is True,
                '当前质量适配器需要已确认地图参考的三波段8位影像')
        else:need(meta.get('bands')==1 and meta.get('dtypes')==['uint8'],'当前SCL适配器需要单波段8位质量层')
        ev,_=path_for(db,p,scene.evidence_id);need(scene.data.get('input_sha256')==ev.sha256)
        result[key]={'scene_id':scene.id,'scene_version':scene.version,'scene_generation':scene.data['generation'],
            'evidence_id':ev.id,'sha256':ev.sha256,'name':scene.data['name'],'metadata':copy.deepcopy(meta),
            'configuration':copy.deepcopy(scene.data.get('configuration'))}
    return result


def _key(request_id):return 'geo-quality-v351:'+request_id


def create(db,p,body):
    require(p,'admin','operator');need(body.association_confirmed is True,'请明确确认两期影像与所选质量层的对应关系',422)
    value=body.model_dump(mode='json');body_hash=digest(value)
    db.execute(text('SELECT pg_advisory_xact_lock(:n)'),{'n':int.from_bytes(hashlib.sha256((p.workspace_id+':'+body.request_id).encode()).digest()[:8],'big',signed=True)})
    old=db.scalar(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==_key(body.request_id)))
    if old:
        # V351 clients omitted the then-default schema. Preserve exact replay
        # only when the caller still omits it and an original V351 Job exists.
        if old.kind==KIND and 'schema' not in body.model_fields_set:
            value={**value,'schema':'geo-quality-request-v351'};body_hash=digest(value)
        need(old.kind in {KIND,KIND_V352} and old.data.get('actor_id')==p.user_id and old.data.get('body_hash')==body_hash,'原申请不能更换来源、适配器或确认人')
        checked(db,p,old);return old
    need(body.schema=='geo-quality-request-v352','历史协议仅供读取原申请，不允许创建新的历史任务',422)
    cap=adapter(db,p,body.adapter_id);sources=_sources(db,p,body,lock=True)
    job=m.Job(workspace_id=p.workspace_id,kind=KIND_V352,idempotency_key=_key(body.request_id),data={
        'schema':SCHEMA,'request':value,'body_hash':body_hash,'actor_id':p.user_id,
        'adapter_hash':cap.content_hash,'runner_hash':RUNNER_HASH,'sources':sources,'sources_hash':digest(sources),
        'result':None,'result_hash':None})
    db.add(job);db.flush()
    db.add(m.Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=REQUEST_ACTION,object_id=job.id,
        data={'body_hash':body_hash,'sources_hash':digest(sources),'adapter_hash':cap.content_hash,'runner_hash':RUNNER_HASH}))
    db.flush();return job


def _audit(db,p,action,identity):
    rows=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==action,m.Audit.object_id==identity).limit(2)))
    need(len(rows)==1);return rows[0]


def _checked(db,p,job,*,current_sources=False):
    need(job.workspace_id==p.workspace_id and job.kind in {KIND,KIND_V352} and type(job.data) is dict)
    data=job.data
    try:body=Create.model_validate(data['request'],strict=True)
    except (ValueError,KeyError,TypeError):raise HTTPException(409,'原质量处理申请格式无法核对；原件保留') from None
    need((job.kind==KIND and body.schema=='geo-quality-request-v351' or job.kind==KIND_V352 and body.schema=='geo-quality-request-v352')
        and data['schema']==SCHEMA and data['body_hash']==digest(body.model_dump(mode='json'))
        and job.idempotency_key==_key(body.request_id) and type(data['sources']) is dict
        and data['sources_hash']==digest(data['sources']) and body.association_confirmed is True)
    frozen=_audit(db,p,REQUEST_ACTION,job.id)
    need(frozen.actor_id==data['actor_id'] and frozen.data=={'body_hash':data['body_hash'],
        'sources_hash':data['sources_hash'],'adapter_hash':data['adapter_hash'],'runner_hash':data['runner_hash']})
    if current_sources:
        cap=adapter(db,p,body.adapter_id)
        need(cap.content_hash==data['adapter_hash'] and RUNNER_HASH==data['runner_hash'])
        need(_sources(db,p,body,lock=True)==data['sources'],'原影像或质量层已变化，请重新核对')
    else:
        for source in data['sources'].values():
            ev,_=path_for(db,p,source['evidence_id']);need(ev.sha256==source['sha256'])
    if job.status=='succeeded':need(data.get('result') is not None)
    if data.get('result') is not None:
        result=data['result'];need(data.get('result_hash')==digest(result))
        saved=_audit(db,p,RESULT_ACTION,job.id)
        need(saved.actor_id==data['actor_id'] and saved.data=={'result_hash':data['result_hash'],
            'body_hash':data['body_hash'],'sources_hash':data['sources_hash'],'runner_hash':data['runner_hash']})
        need(type(result.get('artifacts')) is list and len(result['artifacts'])==5
            and {x.get('filename') for x in result['artifacts']}=={'before.tif','after.tif','before-valid.png','after-valid.png','common-valid.png'}
            and type(result.get('native')) is dict and result['native'].get('schema')=='sentinel-scl-quality-result-v351'
            and result['sources_hash']==data['sources_hash'] and result['adapter_hash']==data['adapter_hash']
            and result['runner_hash']==data['runner_hash'] and result['source_association']=='user_declared_unverified')
        for entry in result['artifacts']:
            ev,_=path_for(db,p,entry['evidence_id'])
            need(ev.sha256==entry['sha256'] and ev.size_bytes==entry['size_bytes'])
        scenes=result.get('derived_scenes');need(type(scenes) is list)
        need(len(scenes)==(2 if result['native']['common_valid_pixels']>0 else 0))
        if scenes:
            need({x.get('side') for x in scenes}=={'before','after'})
            for item in scenes:
                scene=scoped(db,m.GeoScene,item['scene_id'],p)
                expected=next(x for x in result['artifacts'] if x['filename']==item['side']+'.tif')
                need(scene.evidence_id==expected['evidence_id'] and scene.data.get('input_sha256')==expected['sha256']
                    and scene.data.get('quality_origin_v351',{}).get('quality_job_id')==job.id
                    and scene.data['quality_origin_v351'].get('side')==item['side'])
    return job


def checked(db,p,job,*,current_sources=False):
    try:return _checked(db,p,job,current_sources=current_sources)
    except HTTPException:raise
    except (ValueError,KeyError,TypeError,AttributeError,OSError,StopIteration):
        raise HTTPException(409,'质量报告原始证据无法核对；原件保留') from None


def view(db,p,job):
    checked(db,p,job)
    from .geo_quality_reception_v352 import status as reception_status
    reception=reception_status(db,p,job)
    result=copy.deepcopy(job.data.get('result')) if job.status=='succeeded' else None
    return {'schema':'geo-quality-assessment-v351','id':job.id,'status':job.status,
        'request_id':job.data['request']['request_id'],'request':copy.deepcopy(job.data['request']),
        'sources':copy.deepcopy(job.data['sources']),'result':result,
        'error':job.data.get('error') if job.status=='failed' else None,'data_reception':reception,
        'source_association':'user_declared_unverified','cloud_calls':0,'object_state_updated':False}


def _invoke(paths,out,guard):
    python=settings().geo_python.absolute();need(python.is_file(),'本地地理处理环境尚未安装')
    need(hashlib.sha256(RUNNER.read_bytes()).hexdigest()==RUNNER_HASH,'质量适配器代码已变化')
    command=[str(python),str(RUNNER),'--before',str(paths['before_image']),'--after',str(paths['after_image']),
        '--before-quality',str(paths['before_quality']),'--after-quality',str(paths['after_quality']),'--out',str(out)]
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PROJ_NETWORK='OFF',GDAL_PAM_ENABLED='NO',GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR')
    with tempfile.TemporaryFile(mode='w+') as stdout,tempfile.TemporaryFile(mode='w+') as stderr:
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
        try:
            started=time.monotonic()
            while child.poll() is None:
                guard();need(time.monotonic()-started<120,'质量处理超时，原件保留');time.sleep(.2)
            guard()
            if child.returncode:
                stderr.seek(0);lines=stderr.read()[-1500:].splitlines();last=lines[-1] if lines else ''
                raise ValueError(last[12:] if last.startswith('ValueError: ') else '质量影像处理失败，原件保留')
            stdout.seek(0);raw=stdout.read(65537);need(len(raw)<=65536,'质量结果超过本地输出上限')
            result=json.loads(raw)
            need(type(result) is dict and result.get('schema')=='sentinel-scl-quality-result-v351'
                and result.get('cloud_calls')==0 and result.get('object_state_updated') is False
                and result.get('source_association')=='user_declared_unverified'
                and result.get('classification_accuracy')=='not_verified'
                and result.get('buffer_effectiveness')=='not_verified'
                and result.get('buffer_pixels')==POLICY['buffer_pixels']
                and result.get('files')==['before.tif','after.tif','before-valid.png','after-valid.png','common-valid.png'],
                '质量处理结果格式无法核对；原件保留')
            total=result.get('total_pixels');valid=result.get('common_valid_pixels');unknown=result.get('unknown_or_excluded_pixels')
            need(type(total) is int and type(valid) is int and type(unknown) is int
                and 0<total<=16000000 and 0<=valid<=total and unknown==total-valid,
                '质量处理统计无法核对；原件保留')
            return result
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)


def process(db,p,job):
    from .leases import ownership,checkpoint
    require(p,'admin','operator');checked(db,p,job,current_sources=True)
    lease=ownership(db);need(job.status=='running' and job.data['actor_id']==p.user_id and lease.get('job_id')==job.id
        and lease.get('lease_token')==job.lease_token and bool(job.lease_token))
    if job.data.get('result') is not None:return
    sources=copy.deepcopy(job.data['sources']);paths={k:path_for(db,p,v['evidence_id'])[1] for k,v in sources.items()}
    checkpoint(db)
    def guard():
        checkpoint(db)
    with tempfile.TemporaryDirectory(prefix='ga-scl-quality-v351-') as temp:
        output=Path(temp);native=_invoke(paths,output,guard)
        job=scoped(db,m.Job,job.id,p,True);checked(db,p,job,current_sources=True)
        need(job.data.get('result') is None and not list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==RESULT_ACTION,m.Audit.object_id==job.id).limit(1))))
        from .evidence import save_immutable,evidence_bytes
        artifacts=[];scene_ids=[]
        for filename in native['files']:
            path=output/filename;need(path.is_file() and not path.is_symlink() and path.stat().st_size<=64*1024*1024)
            raw=path.read_bytes();media='image/tiff' if filename.endswith('.tif') else 'image/png'
            evidence=save_immutable(db,p,raw,media,{'purpose':'geo_quality_derived_v351','quality_job_id':job.id,'filename':filename,'source_association':'user_declared_unverified'})
            evidence_bytes(db,p,evidence.id)
            artifacts.append({'filename':filename,'evidence_id':evidence.id,'sha256':evidence.sha256,'size_bytes':evidence.size_bytes,'media_type':media})
            if filename.endswith('.tif') and native['common_valid_pixels']>0:
                side=filename[:-4];original=sources[side+'_image'];cfg=copy.deepcopy(original['configuration'])
                scene=m.GeoScene(workspace_id=p.workspace_id,evidence_id=evidence.id,status='queued',data={
                    'name':original['name']+' · SCL有效区（来源关联由用户确认）','input_sha256':evidence.sha256,'size_bytes':evidence.size_bytes,
                    'runtime_hash':GEO_HASH,'generation':1,'configuration':cfg,'metadata':None,'preview':None,'error':None,'history':[],
                    'quality_origin_v351':{'quality_job_id':job.id,'side':side,'sources_hash':job.data['sources_hash'],
                        'source_association':'user_declared_unverified','classification_accuracy':'not_verified','buffer_effectiveness':'not_verified'}})
                db.add(scene);db.flush();scene_ids.append({'side':side,'scene_id':scene.id})
        result={'schema':'geo-quality-result-v351','native':native,'artifacts':artifacts,'derived_scenes':scene_ids,
            'sources_hash':job.data['sources_hash'],'adapter_hash':job.data['adapter_hash'],'runner_hash':job.data['runner_hash'],
            'source_association':'user_declared_unverified','classification_accuracy':'not_verified','buffer_effectiveness':'not_verified',
            'cloud_calls':0,'object_state_updated':False}
        for item in scene_ids:
            scene=scoped(db,m.GeoScene,item['scene_id'],p)
            scene.data={**scene.data,'quality_origin_v351':{**scene.data['quality_origin_v351'],
                'result_hash':digest(result)}}
        job.data={**job.data,'result':result,'result_hash':digest(result)}
        db.add(m.Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=RESULT_ACTION,object_id=job.id,data={
            'result_hash':digest(result),'body_hash':job.data['body_hash'],'sources_hash':job.data['sources_hash'],'runner_hash':job.data['runner_hash']}))
        db.flush()


def finalize(db,p,job):
    """Queue preview jobs only in the parent's successful final transaction."""
    checked(db,p,job);need(job.data.get('result') is not None)
    from .geo_api import queue
    for item in job.data['result']['derived_scenes']:
        scene=scoped(db,m.GeoScene,item['scene_id'],p,True)
        need(scene.data['quality_origin_v351']['quality_job_id']==job.id)
        if not scene.data.get('job_id'):
            queue(db,p,scene)
    if job.kind==KIND_V352:
        from .geo_quality_reception_v352 import emit
        emit(db,p,job)
    db.flush()


def checked_scene(db,p,scene):
    """Rebuild derived-image custody without upgrading ordinary old scenes."""
    marker=scene.data.get('quality_origin_v351')
    matches=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.kind.in_([KIND,KIND_V352]),
        m.Job.data['result']['derived_scenes'].contains([{'scene_id':scene.id}])).limit(2)))
    if marker is None and not matches:return None
    need(type(marker) is dict and len(matches)==1 and marker.get('quality_job_id')==matches[0].id)
    job=matches[0];need(job.status=='succeeded');checked(db,p,job)
    received=None
    if job.kind==KIND_V352:
        from .geo_quality_reception_v352 import status as reception_status
        received=reception_status(db,p,job)
        need(received['state']=='received','质量报告尚未由数据管理接收，请等待或恢复接收')
    result=job.data['result'];side=marker.get('side')
    need(side in {'before','after'} and marker.get('result_hash')==job.data['result_hash']
        and marker.get('sources_hash')==job.data['sources_hash']
        and marker.get('source_association')=='user_declared_unverified'
        and marker.get('classification_accuracy')=='not_verified'
        and marker.get('buffer_effectiveness')=='not_verified'
        and sum(x['scene_id']==scene.id and x['side']==side for x in result['derived_scenes'])==1)
    value=copy.deepcopy(marker)
    if received:value['data_reception_hash']=received['receipt_hash']
    return value


def validate_pair(before,after):
    a,b=before.get('quality_origin_v351'),after.get('quality_origin_v351')
    if a is None and b is None:return
    need(type(a) is dict and type(b) is dict and a.get('quality_job_id')==b.get('quality_job_id')
        and a.get('result_hash')==b.get('result_hash') and a.get('data_reception_hash')==b.get('data_reception_hash')
        and a.get('side')=='before' and b.get('side')=='after',
        '请选择同一质量报告生成的前后两期影像，不能混用不同来源')
