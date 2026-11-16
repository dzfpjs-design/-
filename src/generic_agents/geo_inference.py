"""Local full-resolution RGB analysis, checked nodata cut and native geometries."""
import base64,io,tempfile
from pathlib import Path
from PIL import Image,ImageChops
from .models import GeoRun
from .services import scoped,require,audit
from .contracts import digest
from .geo_codec import call,RECIPE_HASH
from .geo_storage import path_for
from .evidence import save_evidence,evidence_bytes
from .local_specialists import ready_install,SAM_RECIPE,execute,weights_dir,node_lock,guard_record,process_record
from .segmentation import save_masks


def clip_invalid(result,valid_content,roi):
    """Retain RGB merge provenance, then remove invalid pixels before native geometry."""
    with Image.open(io.BytesIO(valid_content)) as valid:
        if valid.mode!='L' or valid.size!=(result['width'],result['height']):raise ValueError('有效像素与分析图尺寸不一致')
        counts=valid.histogram()
        if counts[0]+counts[255]!=valid.width*valid.height:raise ValueError('有效像素掩膜不是二值')
        masks=[];removed_candidates=0
        for candidate in result['masks']:
            box=candidate['bbox']
            with Image.open(io.BytesIO(base64.b64decode(candidate['png_base64'],validate=True))) as im:
                alpha=ImageChops.multiply(im.getchannel('A'),valid.crop(box));bounds=alpha.getbbox()
                if bounds is None:removed_candidates+=1;continue
                clipped=im.copy();clipped.putalpha(alpha);clipped=clipped.crop(bounds);buf=io.BytesIO();clipped.save(buf,format='PNG')
                local=[box[0]+bounds[0],box[1]+bounds[1],box[0]+bounds[2],box[1]+bounds[3]];area=sum(alpha.histogram()[1:])
                masks.append({**candidate,'candidate_index':len(masks)+1,'bbox':local,
                    'bbox_original':[local[0]+roi[0],local[1]+roi[1],local[2]+roi[0],local[3]+roi[1]],
                    'area_pixels':area,'nodata_removed_pixels':candidate['area_pixels']-area,
                    'rgb_merge_evidence':candidate['merge_evidence'],'merge_evidence':[],
                    'png_base64':base64.b64encode(buf.getvalue()).decode()})
        return {**result,'masks':masks,'nodata_removed_candidates':removed_candidates,
            'nodata_policy':'invalid pixels filled black for RGB model; removed from merged candidate masks before geometry',
            'merge_policy':'RGB model masks merged before nodata exclusion; original pixel evidence retained separately'}


def _run(db,p,job):
    require(p,'admin','operator');row=scoped(db,GeoRun,job.data['run_id'],p,True);generation=job.data['generation']
    if row.status in {'succeeded','cancelled'} or row.data['generation']!=generation:return
    if row.data['runtime_hash']!=RECIPE_HASH or row.data['sam_recipe_hash']!=digest(SAM_RECIPE):raise ValueError('空间处理或模型配方变化，本次记录保留，请重新核对版本')
    installation=ready_install(db,p,row.installation_id)
    if installation.pack_id!='sam2-1-tiny' or installation.data['generation']!=row.data['installation_generation'] or installation.data['backend']!=row.data['backend']:raise ValueError('分割模型与已确认版本不一致')
    original,path=path_for(db,p,row.data['original_evidence_id'])
    if original.sha256!=row.data['input_sha256']:raise ValueError('GeoTIFF原件与分析请求不一致')
    identity=row.id;row.status='running';row.version+=1;row.data={**row.data,'stage':'prepare_rgb'};db.commit()
    def guard():
        current=guard_record(db,p,GeoRun,identity,generation)
        if current.data.get('workflow_graph_scope'):
            from .workflow_graph_scope import guard_record as guard_graph_record
            return guard_graph_record(db,p,current)
        if current.data.get('workflow_task_id'):
            from .models import Task
            task=scoped(db,Task,current.data['workflow_task_id'],p)
            if task.status!='workflow_running' or task.data['workflow_runtime'].get('automatic',{}).get('job_id')!=current.data['workflow_job_id']:
                current.status='cancelled';current.version+=1;db.commit();raise ValueError('业务任务已取消或执行次序变化，空间检查点保留')
        return current
    checkpoint=row.data.get('rgb_checkpoint')
    if not checkpoint:
        with tempfile.TemporaryDirectory(prefix='ga-geo-analysis-') as tmp:
            rendering=call('render',path,{**row.data['configuration'],'window':row.data['roi'],'analysis':True},tmp,guard)
            if rendering['preview'] or rendering['width']!=rendering['source_width'] or rendering['height']!=rendering['source_height']:raise ValueError('分析输入被缩放，结果未接受')
            rgb=save_evidence(db,p,(Path(tmp)/'image.png').read_bytes(),'image/png');valid=save_evidence(db,p,(Path(tmp)/'valid.png').read_bytes(),'image/png')
            for ev,kind in [(rgb,'rgb'),(valid,'valid')]:
                ev.data={**ev.data,'purpose':'geo_analysis_'+kind,'run_id':identity,'source_sha256':original.sha256,'roi':row.data['roi']};evidence_bytes(db,p,ev.id)
            checkpoint={**rendering,'rgb_evidence_id':rgb.id,'rgb_sha256':rgb.sha256,'valid_evidence_id':valid.id,'valid_sha256':valid.sha256}
            db.flush()  # A lease guard expires loaded rows; retain newly assigned provenance.
            current=guard();current.data={**current.data,'rgb_checkpoint':checkpoint,'stage':'local_model'};current.version+=1;db.commit()
    rgb,content=evidence_bytes(db,p,checkpoint['rgb_evidence_id']);valid,valid_content=evidence_bytes(db,p,checkpoint['valid_evidence_id'])
    if rgb.sha256!=checkpoint['rgb_sha256'] or valid.sha256!=checkpoint['valid_sha256'] or rgb.data.get('run_id')!=identity or valid.data.get('run_id')!=identity:raise ValueError('已保存RGB分析输入或有效像素记录不一致')
    result=row.data.get('mask_checkpoint')
    if not result:
        with node_lock():
            result=execute(weights_dir(SAM_RECIPE),row.data['backend'],.5,content,guard,recipe=SAM_RECIPE)
        result=clip_invalid(result,valid_content,row.data['roi']);result=save_masks(db,p,result,rgb)
        db.flush()  # Includes the final mask's origin/bbox, not just its previously flushed blob.
        current=guard();current.data={**current.data,'mask_checkpoint':result,'stage':'native_geometry'};current.version+=1;db.commit()
    masks=[];metadata={**row.data['metadata'],'map_image_confirmed':row.data['configuration']['map_image_confirmed']}
    from .segmentation import mask_bytes
    for mask in result['masks']:
        guard();_,raw=mask_bytes(db,p,result,mask['mask_evidence_id'],rgb.id)
        with tempfile.TemporaryDirectory(prefix='ga-geo-mask-') as tmp:
            mp=Path(tmp)/'mask.png';mp.write_bytes(raw)
            try:geometry=call('geometry',mp,{'metadata':metadata,'bbox':mask['bbox_original']},guard=guard)
            except ValueError as error:
                # Cancellation/lease loss must not be converted to a partial geometry result.
                guard();geometry={'geometry_native':None,'area_m2':None,'area_method':None,'position_status':'geometry_failed','geometry_error':str(error)[:500]}
        if geometry.get('pixel_area',mask['area_pixels'])!=mask['area_pixels']:raise ValueError('空间结果与有效掩膜像素面积不一致')
        masks.append({**mask,**geometry})
    path_for(db,p,original.id);current=guard()
    result={**result,'masks':masks,'source_raster':{'sha256':original.sha256,'roi':row.data['roi'],'width':metadata['width'],'height':metadata['height'],
        'crs':metadata['crs'],'transform':metadata['transform'],'georeference_status':metadata['georeference_status'],'ground_accuracy':'not_verified',
        'rgb_bands':row.data['configuration']['rgb_bands'],'ranges':row.data['configuration']['ranges'],'observed_at':row.data['configuration']['observed_at']},
        'runtime_hash':RECIPE_HASH,'sam_recipe_hash':digest(SAM_RECIPE),'candidate_only':True,'entity_state_updated':False,'experience_published':False,
        'geometry_failures':sum(m['position_status']=='geometry_failed' for m in masks)}
    current.status='succeeded';current.version+=1;current.data={**current.data,'result':result,'result_hash':digest(result),'stage':'saved','error':None}
    audit(db,p,'geo.candidates_saved',current.id,{'result_hash':digest(result),'cloud_calls':0,'entity_state_updated':False});db.commit()


def process_run(db,p,job):return process_record(db,p,job,GeoRun,'run_id',_run)
