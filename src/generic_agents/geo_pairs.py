"""Saved explicit RGB comparison; freezes sources, never changes object facts or learning."""
import hashlib,json,tempfile
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from pydantic import Field,StrictInt
from sqlalchemy import select,text,or_
from .models import GeoScene,GeoPair,Job,Membership,Audit
from .setup_api import P,DB
from .contracts import Contract,digest
from .services import scoped,require,check_version,audit
from .security import Principal
from .geo_storage import path_for
from .geo_pair_codec import call,RECIPE,RECIPE_HASH,recipe_for_hash
from .geo_codec import call as geo_call,RECIPE_HASH as GEO_HASH
from .evidence import save_evidence,evidence_bytes
from .local_specialists import process_record,guard_record
router=APIRouter(prefix='/api/geo/pairs')
KINDS=('before','after','valid','difference','candidate')
LEARNED_KINDS=('before','after','valid','probability','candidate','probability_data','probability_raster','change_mask')


def kinds(row):
    return LEARNED_KINDS if row.data.get('request_kind')=='learned_binary_change' else KINDS


def view(row):
    return {'id':row.id,'version':row.version,'status':row.status,'before_scene_id':row.before_scene_id,'after_scene_id':row.after_scene_id,'created_at':row.created_at.isoformat(),**row.data}


class Create(Contract):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    before_scene_id:str=Field(min_length=1,max_length=100)
    after_scene_id:str=Field(min_length=1,max_length=100)
    before_version:int=Field(ge=1)
    after_version:int=Field(ge=1)
    roi:tuple[StrictInt,StrictInt,StrictInt,StrictInt]
    alignment:str=Field(pattern=r'^(same_grid|reproject_after_to_before)$')
    threshold:StrictInt=Field(ge=1,le=255)
    rgb_comparability_confirmed:bool


class Version(Contract):version:int=Field(ge=1)


class Review(Version):
    note:str=Field(min_length=1,max_length=4000)
    decision:str=Field(pattern=r'^(keep|exclude|uncertain)$')


def queue(db,p,row):
    job=Job(workspace_id=p.workspace_id,kind='compare_geo_pair',idempotency_key=f'geo-pair:{row.id}:{row.data["generation"]}',data={'pair_id':row.id,'generation':row.data['generation'],'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id}


def snapshot(db,p,identity,version):
    scene=scoped(db,GeoScene,identity,p,True);check_version(scene,version)
    if scene.status!='ready' or scene.data['runtime_hash']!=GEO_HASH:raise HTTPException(409,'请先完成两幅影像的RGB设置与本地预览')
    config=scene.data['configuration'];meta=scene.data['metadata']
    if not config['map_image_confirmed'] or meta['georeference_status']!='affine_available':raise HTTPException(422,'两幅影像均需确认已有地图参考；不会猜测坐标或像素配准')
    ev,_=path_for(db,p,scene.evidence_id)
    if ev.sha256!=scene.data['input_sha256']:raise HTTPException(409,'影像版本与原件摘要不一致')
    from .geo_quality_v351 import checked_scene
    quality=checked_scene(db,p,scene)
    return {'scene_id':scene.id,'scene_version':scene.version,'scene_generation':scene.data['generation'],'name':scene.data['name'],'evidence_id':ev.id,'sha256':ev.sha256,'configuration':config,'metadata':meta,'runtime_hash':GEO_HASH,
        **({'quality_origin_v351':quality} if quality else {})}


def inputs(db,p,row):
    paths=[]
    for snap in row.data['sources']:
        ev,path=path_for(db,p,snap['evidence_id'])
        if ev.sha256!=snap['sha256']:raise HTTPException(409,'双影像冻结原件摘要不一致；旧记录保留')
        if snap.get('quality_origin_v351'):
            from .geo_quality_v351 import checked_scene
            scene=scoped(db,GeoScene,snap['scene_id'],p)
            if checked_scene(db,p,scene)!=snap['quality_origin_v351']:
                raise HTTPException(409,'质量掩膜与固定来源不一致；原结果保留')
        paths.append(path)
    if digest(row.data['sources'])!=row.data['sources_hash']:raise HTTPException(409,'双影像来源快照损坏')
    return paths


@router.get('')
def listing(p:P,db:DB,include_learned:bool=False):
    query=select(GeoPair).where(GeoPair.workspace_id==p.workspace_id)
    if not include_learned:query=query.where(or_(GeoPair.data['request_kind'].as_string().is_(None),GeoPair.data['request_kind'].as_string()!='learned_binary_change'))
    return [view(r) for r in db.scalars(query.order_by(GeoPair.created_at.desc()).limit(100))]


@router.get('/catalog')
def catalog(p:P):
    from .config import settings
    return {'recipe_hash':RECIPE_HASH,'runtime_installed':settings().geo_python.is_file(),'local':True,'learned_model':False,'algorithm':RECIPE['algorithm'],'changer_status':'unavailable_checkpoint_safety_and_domain_validation_pending','max_pixels':RECIPE['max_comparison_pixels']}


@router.post('',status_code=201)
def create(body:Create,p:P,db:DB):
    require(p,'admin','operator');value=body.model_dump(mode='json');body_hash=digest(value)
    lock=int.from_bytes(hashlib.sha256((p.workspace_id+body.request_id).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
    old=db.scalar(select(GeoPair).where(GeoPair.workspace_id==p.workspace_id,GeoPair.data['request_id'].as_string()==body.request_id))
    if old:
        if old.data['request_hash']!=body_hash:raise HTTPException(409,'同一比较请求不能更改原件版本、范围或算法；请核对原请求')
        inputs(db,p,old);return view(old)
    if not body.rgb_comparability_confirmed:raise HTTPException(422,'请核对两幅影像的RGB含义与显示范围，并确认可以比较')
    a=snapshot(db,p,body.before_scene_id,body.before_version);b=snapshot(db,p,body.after_scene_id,body.after_version)
    from .geo_quality_v351 import validate_pair
    validate_pair(a,b)
    if a['scene_id']==b['scene_id'] or a['sha256']==b['sha256']:raise HTTPException(422,'两幅原件相同；请选择需要对照的不同影像')
    meta=a['metadata'];x0,y0,x1,y1=body.roi
    if not 0<=x0<x1<=meta['width'] or not 0<=y0<y1<=meta['height']:raise HTTPException(422,'比较范围超出A影像边界')
    if (x1-x0)*(y1-y0)>RECIPE['max_comparison_pixels']:raise HTTPException(422,'比较范围超过1600万像素；不会自动降采样')
    at=a['configuration'].get('observed_at');bt=b['configuration'].get('observed_at')
    if at and bt and datetime.fromisoformat(bt)<=datetime.fromisoformat(at):raise HTTPException(422,'已填写时间时，B采集时间应晚于A；请核对影像顺序')
    if body.alignment=='same_grid' and any(a['metadata'][k]!=b['metadata'][k] for k in ('width','height','transform','crs_wkt')):
        raise HTTPException(422,'两幅影像网格不同；请明确选择将B重投影到A，不能静默对齐')
    sources=[a,b];row=GeoPair(workspace_id=p.workspace_id,before_scene_id=a['scene_id'],after_scene_id=b['scene_id'],data={
        'request_id':body.request_id,'request_hash':body_hash,'request':value,'sources':sources,'sources_hash':digest(sources),'runtime_hash':RECIPE_HASH,
        'generation':1,'chronology':'user_declared_ordered_times' if at and bt else 'unknown_dates_A_B_comparison','result':None,'result_hash':None,'error':None,'review':None,'review_history':[]})
    db.add(row);db.flush();queue(db,p,row);audit(db,p,'geo.pair_queued',row.id,{'learned_model':False,'cloud_calls':0});db.commit();return view(row)


@router.get('/requests/{request_id}')
def recover(request_id:str,p:P,db:DB,expected_hash:str|None=None):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request_id):raise HTTPException(422,'比较请求格式不正确')
    row=db.scalar(select(GeoPair).where(GeoPair.workspace_id==p.workspace_id,GeoPair.data['request_id'].as_string()==request_id))
    if not row:raise HTTPException(404,'此比较请求尚未保存；请保留原请求重试')
    if expected_hash and expected_hash!=row.data['request_hash']:raise HTTPException(409,'保存的比较内容与原请求不一致')
    inputs(db,p,row);return view(row)


@router.get('/{identity}')
def detail(identity:str,p:P,db:DB):return view(scoped(db,GeoPair,identity,p))


def checked(db,p,row):
    inputs(db,p,row)
    if row.status!='succeeded' or not row.data.get('result'):raise HTTPException(409,'比较证据尚未完整保存；原件与记录保留')
    if digest(row.data['result'])!=row.data['result_hash']:raise HTTPException(409,'比较结果摘要不一致')
    if row.data.get('request_kind')=='learned_binary_change':
        from .learned_geo_pairs import checked_result
        checked_result(row)
    return row


def artifact_bytes(db,p,row,kind):
    saved=row.data['result']['artifacts'][kind];ev,raw=evidence_bytes(db,p,saved['evidence_id'])
    if row.data.get('request_kind')=='learned_binary_change' and ev.media_type!=saved.get('media_type'):
        raise HTTPException(409,'变化证据格式与保存记录不一致')
    if ev.sha256!=saved['sha256'] or ev.data.get('pair_id')!=row.id or ev.data.get('sources_hash')!=row.data['sources_hash'] or ev.data.get('generation')!=row.data['generation'] or ev.data.get('kind')!=kind:
        raise HTTPException(409,'比较证据与固定来源版本不一致；未显示为核查依据')
    return ev,raw


def workspace_principal(db,p,workspace_id):
    if not workspace_id or workspace_id==p.workspace_id:return p
    m=db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==p.user_id))
    if not m:raise HTTPException(403,'没有此工作区的访问权限')
    return Principal(p.user_id,m.workspace_id,m.role,p.session_id)


@router.get('/{identity}/artifacts/{kind}')
def artifact(identity:str,kind:str,p:P,db:DB,workspace_id:str|None=None):
    if kind not in (*KINDS,*LEARNED_KINDS):raise HTTPException(404,'没有此类比较证据')
    p=workspace_principal(db,p,workspace_id);row=checked(db,p,scoped(db,GeoPair,identity,p))
    if kind not in kinds(row):raise HTTPException(404,'此处理没有该类证据')
    ev,raw=artifact_bytes(db,p,row,kind)
    headers={'Cache-Control':'private, no-store','X-Content-SHA256':ev.sha256,'X-Content-Type-Options':'nosniff'}
    if ev.media_type!='image/png':headers['Content-Disposition']='attachment; filename="learned-change-'+kind+('.tif' if ev.media_type=='image/tiff' else '.npy')+'"'
    return Response(raw,media_type=ev.media_type,headers=headers)


@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB,workspace_id:str|None=None):
    p=workspace_principal(db,p,workspace_id);row=checked(db,p,scoped(db,GeoPair,identity,p))
    for kind in kinds(row):artifact_bytes(db,p,row,kind)
    value={'sources':row.data['sources'],'chronology':row.data['chronology'],'result':row.data['result'],'result_hash':row.data['result_hash'],'review':row.data['review'],'runtime_hash':row.data['runtime_hash']}
    filename='local-learned-change.json' if row.data.get('request_kind')=='learned_binary_change' else 'local-rgb-comparison.json'
    return Response(json.dumps(value,ensure_ascii=False,allow_nan=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="'+filename+'"','Cache-Control':'private, no-store'})


@router.put('/{identity}/review')
def review(identity:str,body:Review,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,GeoPair,identity,p,True);check_version(row,body.version);checked(db,p,row)
    if not body.note.strip():raise HTTPException(422,'请填写核查说明')
    for kind in kinds(row):artifact_bytes(db,p,row,kind)
    history=row.data['review_history']+([row.data['review']] if row.data.get('review') else [])
    row.data={**row.data,'review':{'note':body.note.strip(),'decision':body.decision,'result_hash':row.data['result_hash'],'reviewer_id':p.user_id,'saved_at':datetime.now().astimezone().isoformat()},'review_history':history[-30:]};row.version+=1
    audit(db,p,'geo.pair_review_saved',row.id,{'candidate_only':True,'entity_state_updated':False,'learned_model':row.data.get('request_kind')=='learned_binary_change'});db.commit();return view(row)


@router.post('/{identity}/register-business')
def register_business(identity:str,body:Version,p:P,db:DB):
    require(p,'admin')
    from .workflow_geo_pairs import register_from_pair
    row=scoped(db,GeoPair,identity,p,True);check_version(row,body.version)
    if row.data.get('request_kind')=='learned_binary_change':
        from .workflow_learned_geo_pairs import register_from_pair as register_learned
        return register_learned(db,p,row)
    return register_from_pair(db,p,row)


@router.post('/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,GeoPair,identity,p,True);check_version(row,body.version)
    if row.data.get('workflow_task_id'):raise HTTPException(409,'这是业务节点的比较，请在业务任务中取消，证据保留')
    if row.status not in {'queued','running'}:raise HTTPException(409,'比较已结束；证据和记录保留')
    row.status='cancelled';row.version+=1;db.commit();return view(row)


@router.post('/{identity}/retry')
def retry(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,GeoPair,identity,p,True);check_version(row,body.version)
    if row.data.get('workflow_task_id'):raise HTTPException(409,'这是业务节点的比较，请在业务任务中按原输入重试')
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只有失败或取消的比较可以重试')
    if row.data.get('request_kind')=='learned_binary_change':
        from .learned_geo_pairs import model_check,RECIPE_HASH as learned_hash
        if row.data['runtime_hash']!=learned_hash:raise HTTPException(409,'旧学习型配方已变化；原请求保留')
        model_check(db,p,row,verify=True)
    elif row.data['runtime_hash']!=RECIPE_HASH:raise HTTPException(409,'旧比较配方改变；不能按新算法重新解释原请求')
    inputs(db,p,row);row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'error':None}
    queue(db,p,row);db.commit();return view(row)


def _process(db,p,job):
    require(p,'admin','operator');row=scoped(db,GeoPair,job.data['pair_id'],p,True);generation=job.data['generation']
    if row.status in {'succeeded','cancelled'} or row.data['generation']!=generation:return
    if row.data.get('request_kind')=='learned_binary_change':
        from .learned_geo_pairs import process as process_learned
        return process_learned(db,p,job)
    recipe=recipe_for_hash(row.data['runtime_hash']);runtime_hash=row.data['runtime_hash']
    before,after=inputs(db,p,row);identity=row.id;value=row.data['request'];sources=row.data['sources'];sources_hash=row.data['sources_hash']
    row.status='running';row.version+=1;db.commit()
    def guard():
        current=guard_record(db,p,GeoPair,identity,generation)
        if current.data.get('workflow_graph_scope'):
            from .workflow_graph_scope import guard_record as guard_graph_record
            return guard_graph_record(db,p,current)
        if current.data.get('workflow_task_id'):
            from .models import Task
            task=scoped(db,Task,current.data['workflow_task_id'],p)
            if task.status!='workflow_running' or task.data['workflow_runtime']['automatic'].get('job_id')!=current.data['workflow_job_id']:
                raise ValueError('业务比较已取消或被替代；结果不进入业务回执')
        return current
    request={'roi':value['roi'],'alignment':value['alignment'],'threshold':value['threshold'],'before_configuration':sources[0]['configuration'],'after_configuration':sources[1]['configuration']}
    if recipe['id']=='geo-pair-v2':request['after_allowed_window']=row.data['after_allowed_window']
    with tempfile.TemporaryDirectory(prefix='ga-geo-pair-result-') as out:
        result=call(before,after,request,out,guard,recipe_id=recipe['id']);artifacts={}
        for kind in KINDS:
            ev=save_evidence(db,p,(Path(out)/(kind+'.png')).read_bytes(),'image/png')
            ev.data={**ev.data,'purpose':'geo_pair_comparison','pair_id':identity,'sources_hash':sources_hash,'generation':generation,'kind':kind};db.flush();evidence_bytes(db,p,ev.id)
            artifacts[kind]={'evidence_id':ev.id,'sha256':ev.sha256}
        geometry=None;geometry_issue=None
        try:geometry=geo_call('geometry',Path(out)/'candidate.png',{'metadata':{**sources[0]['metadata'],'map_image_confirmed':True},'bbox':value['roi']},guard=guard)
        except ValueError as e:
            guard();geometry_issue=str(e)[:1000]
        result={**result,'artifacts':artifacts,'geometry':geometry,'geometry_issue':geometry_issue,'sources_hash':sources_hash,'runtime_hash':runtime_hash}
    current=guard();inputs(db,p,current);current.status='succeeded';current.version+=1;current.data={**current.data,'result':result,'result_hash':digest(result),'error':None}
    completed=Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action='geo.pair_saved',object_id=identity,
        data={'cloud_calls':0,'learned_model':False,'candidate_pixels':result['candidate_pixels'],'unknown_pixels':result['unknown_pixels']})
    db.add(completed);db.flush()
    from .native_geo_pair_provenance_v137 import record_result
    record_result(db,p,current,completed)
    db.commit()


def process(db,p,job):return process_record(db,p,job,GeoPair,'pair_id',_process)
