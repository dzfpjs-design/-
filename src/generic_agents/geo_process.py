"""Durable local scene parsing and preview, bound to immutable original/version."""
import tempfile
from pathlib import Path
from .models import GeoScene
from .services import scoped,require,audit
from .geo_storage import path_for
from .geo_codec import call,RECIPE_HASH
from .evidence import save_evidence,evidence_bytes
from .local_specialists import guard_record,process_record


def checked_meta(value):
    if value.get('schema')!='local-raster-v1' or value.get('cloud_calls')!=0 or value.get('observed_at') is not None:
        raise ValueError('本地空间元数据格式或来源声明无效')
    if any(not isinstance(value.get(k),int) or isinstance(value[k],bool) or value[k]<=0 for k in ['width','height','bands']):raise ValueError('影像尺寸或波段无效')
    if len(value.get('transform',[]))!=6 or len(value.get('dtypes',[]))!=value['bands']:raise ValueError('影像空间/波段字段不完整')
    return value


def _scene(db,p,job):
    require(p,'admin','operator');row=scoped(db,GeoScene,job.data['scene_id'],p,True);generation=job.data['generation']
    if row.status in {'parsed','ready','cancelled'} or row.data['generation']!=generation:return
    if row.data['runtime_hash']!=RECIPE_HASH:raise ValueError('地理处理配方已变更，需明确重试')
    ev,path=path_for(db,p,row.evidence_id);identity=row.id;row.status='running';row.version+=1;db.commit()
    guard=lambda:guard_record(db,p,GeoScene,identity,generation)
    meta=checked_meta(call('inspect',path,guard=guard));preview=None
    if row.data.get('wms_origin'):
        from .wms import source_provenance
        source_provenance(db,p,row)
        meta={**meta,'source_kind':'wms_rendered_map','raw_satellite_bands':False,'source_capture_time_known':False}
    current=guard();configuration=current.data.get('configuration')
    if configuration:
        request={**configuration,'preview':True}
        with tempfile.TemporaryDirectory(prefix='ga-geo-preview-') as out:
            result=call('render',path,request,out,guard)
            image=save_evidence(db,p,(Path(out)/'image.png').read_bytes(),'image/png')
            image.data={**image.data,'purpose':'geo_preview','scene_id':identity,'scene_sha256':ev.sha256,'generation':generation}
            evidence_bytes(db,p,image.id)
            preview={**result,'evidence_id':image.id,'sha256':image.sha256}
    path_for(db,p,row.evidence_id);current=guard();current.status='ready' if preview else 'parsed';current.version+=1
    current.data={**current.data,'metadata':meta,'preview':preview,'error':None}
    audit(db,p,'geo.scene_checked',current.id,{'generation':generation,'cloud_calls':0,'preview':bool(preview)});db.commit()


def process_scene(db,p,job):
    return process_record(db,p,job,GeoScene,'scene_id',_scene)
