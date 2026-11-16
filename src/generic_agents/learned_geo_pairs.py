"""Persisted learned change candidates, with immutable sources and model trace."""
import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictFloat
from sqlalchemy import select, text
from .contracts import Contract, digest
from .models import GeoPair, SpecialistInstallation, Audit
from .services import require, scoped, audit
from .setup_api import P, DB
from .geo_pairs import snapshot, inputs, view, queue
from .local_specialists import guard_record
from .local_changer_install import installed, trace, model_path, PACK, RECIPE_HASH as MODEL_HASH
from .changer_pair_codec import call, RECIPE, RECIPE_HASH, ROOT
from .evidence import save_evidence, save_immutable, evidence_bytes

router = APIRouter(prefix='/api/geo/learned-pairs')
KIND = 'learned_binary_change'
ARTIFACTS = {'before': ('before.png', 'image/png'), 'after': ('after.png', 'image/png'),
             'valid': ('valid.png', 'image/png'), 'probability': ('probability.png', 'image/png'),
             'candidate': ('candidate.png', 'image/png'),
             'probability_data': ('probability.npy', 'application/octet-stream'),
             'probability_raster': ('probability.tif', 'image/tiff'),
             'change_mask': ('change-mask.tif', 'image/tiff')}


class Create(Contract):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    model_installation_id: str = Field(min_length=1, max_length=100)
    model_generation: StrictInt = Field(ge=1)
    model_recipe_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    before_scene_id: str = Field(min_length=1, max_length=100)
    after_scene_id: str = Field(min_length=1, max_length=100)
    before_version: StrictInt = Field(ge=1)
    after_version: StrictInt = Field(ge=1)
    roi: tuple[StrictInt, StrictInt, StrictInt, StrictInt]
    alignment: str = Field(pattern=r'^(same_grid|reproject_after_to_before)$')
    probability_threshold: StrictFloat = Field(gt=0, lt=1, allow_inf_nan=False)
    rgb_comparability_confirmed: bool


@router.get('')
def listing(p:P,db:DB):
    rows=db.scalars(select(GeoPair).where(GeoPair.workspace_id==p.workspace_id,
        GeoPair.data['request_kind'].as_string()==KIND).order_by(GeoPair.created_at.desc()).limit(100))
    return [view(row) for row in rows]


@router.get('/catalog')
def catalog(p:P,db:DB):
    from .config import settings
    rows=db.scalars(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id==p.workspace_id,
        SpecialistInstallation.pack_id==PACK).order_by(SpecialistInstallation.created_at.desc()))
    return {'pack_id':PACK,'model_recipe_hash':MODEL_HASH,'pipeline_hash':RECIPE_HASH,
        'title':'本地影像变化候选识别','backend':'cpu','default_deployment':'local','cloud_fallback':False,
        'domain_quality_verified':False,'business_registration_enabled':True,
        'prepared_available':(ROOT/'.runtime/changer-candidate/changer-r18.safetensors').is_file(),
        'runtime_available':settings().specialist_python.is_file() and settings().geo_python.is_file(),
        'installations':[{'id':row.id,'version':row.version,'status':row.status,**row.data} for row in rows]}


def model_check(db, p, row, verify=False):
    saved = row.data.get('model_trace')
    if not isinstance(saved, dict) or not isinstance(saved.get('installation_id'),str) or digest(saved) != row.data.get('model_trace_hash'):
        raise HTTPException(409, '已保存模型版本记录损坏；原件保留')
    model = installed(db, p, saved['installation_id']) if verify else scoped(db, SpecialistInstallation, saved['installation_id'], p)
    if model.pack_id != PACK or model.status != 'ready' or model.data.get('recipe_hash') != MODEL_HASH or trace(model) != saved:
        raise HTTPException(409, '原模型安装版本已变化或不可用；不能按新版本解释原请求')
    return model


@router.post('', status_code=201)
def create(body: Create, p: P, db: DB):
    require(p, 'admin', 'operator'); value = body.model_dump(mode='json'); fingerprint = digest(value)
    lock = int.from_bytes(hashlib.sha256((p.workspace_id + body.request_id).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    old = db.scalar(select(GeoPair).where(GeoPair.workspace_id == p.workspace_id, GeoPair.data['request_id'].as_string() == body.request_id))
    if old:
        if old.data.get('request_kind') != KIND or old.data['request_hash'] != fingerprint:
            raise HTTPException(409, '同一比较请求不能修改原件、范围、模型或阈值；请核对原请求')
        inputs(db, p, old); return view(old)
    if not body.rgb_comparability_confirmed:
        raise HTTPException(422, '请核对两幅影像的RGB含义与数值范围，确认可以比较')
    model = installed(db, p, body.model_installation_id); model_trace = trace(model)
    if model_trace['generation'] != body.model_generation or model_trace['recipe_hash'] != body.model_recipe_hash:
        raise HTTPException(409, '选定模型版本已变化，请重新读取安装记录后确认')
    a = snapshot(db, p, body.before_scene_id, body.before_version)
    b = snapshot(db, p, body.after_scene_id, body.after_version)
    from .geo_quality_v351 import validate_pair
    validate_pair(a,b)
    if a['sha256'] == b['sha256']:
        raise HTTPException(422, '两幅原件相同，请选择不同影像')
    x0, y0, x1, y1 = body.roi; meta = a['metadata']
    allowed = a['configuration'].get('window') or [0, 0, meta['width'], meta['height']]
    if not allowed[0] <= x0 < x1 <= allowed[2] or not allowed[1] <= y0 < y1 <= allowed[3]:
        raise HTTPException(422, '比较范围超出A影像已确认的读取范围')
    if min(x1 - x0, y1 - y0) < 32 or (x1 - x0) * (y1 - y0) > RECIPE['max_comparison_pixels']:
        raise HTTPException(422, '学习型变化分析每边至少32像素，总范围不超过1600万像素；不自动缩放')
    if body.alignment == 'same_grid' and any(a['metadata'][key] != b['metadata'][key] for key in ('width', 'height', 'transform', 'crs_wkt')):
        raise HTTPException(422, '两幅网格不同，请明确选择将B重投影到A')
    at, bt = [s['configuration'].get('observed_at') for s in (a, b)]
    if at and bt and datetime.fromisoformat(bt) <= datetime.fromisoformat(at):
        raise HTTPException(422, 'B采集时间应晚于A；请核对顺序')
    sources = [a, b]
    row = GeoPair(workspace_id=p.workspace_id, before_scene_id=a['scene_id'], after_scene_id=b['scene_id'], data={
        'request_kind': KIND, 'request_id': body.request_id, 'request_hash': fingerprint, 'request': value,
        'sources': sources, 'sources_hash': digest(sources), 'runtime_hash': RECIPE_HASH,
        'model_trace': model_trace, 'model_trace_hash': digest(model_trace),
        'after_allowed_window': b['configuration'].get('window') or [0, 0, b['metadata']['width'], b['metadata']['height']],
        'generation': 1, 'chronology': 'user_declared_ordered_times' if at and bt else 'unknown_dates_A_B_comparison',
        'result': None, 'result_hash': None, 'error': None, 'review': None, 'review_history': []})
    db.add(row); db.flush(); queue(db, p, row)
    audit(db, p, 'geo.learned_pair_queued', row.id, {'model_trace_hash': row.data['model_trace_hash'], 'cloud_calls': 0})
    db.commit(); return view(row)


def process(db, p, job):
    require(p, 'admin', 'operator'); row = scoped(db, GeoPair, job.data['pair_id'], p, True)
    generation = job.data['generation']
    if row.status in {'succeeded', 'cancelled'} or row.data['generation'] != generation:
        return
    if row.data.get('request_kind') != KIND or row.data.get('runtime_hash') != RECIPE_HASH:
        raise ValueError('已保存学习型变化配方改变；原请求保留')
    from .workflow_learned_geo_pairs import guard_workflow
    guard_workflow(db,p,row)
    model_check(db, p, row, verify=True)
    before, after = inputs(db, p, row); identity = row.id; data = row.data
    row.status = 'running'; row.version += 1; db.commit()
    def guard():
        current = guard_record(db, p, GeoPair, identity, generation)
        model_check(db, p, current)
        from .workflow_learned_geo_pairs import guard_workflow
        guard_workflow(db,p,current)
        return current
    guard()
    request = {key: data['request'][key] for key in ('roi', 'alignment', 'probability_threshold')}
    request.update(before_configuration=data['sources'][0]['configuration'], after_configuration=data['sources'][1]['configuration'],
                   after_allowed_window=data['after_allowed_window'])
    with tempfile.TemporaryDirectory(prefix='ga-learned-result-') as temporary:
        result = call(before, after, request, temporary, guard, weights=model_path())
        if result['runtime_hash'] != RECIPE_HASH or result['source_sha256'] != [s['sha256'] for s in data['sources']]:
            raise ValueError('模型结果与冻结来源不一致')
        guard(); current = scoped(db, GeoPair, identity, p, True)
        if current.status == 'cancelled' or current.data['generation'] != generation:
            raise ValueError('分析已取消或被替代；结果未接受')
        model_check(db, p, current, verify=True); inputs(db, p, current)
        from .workflow_learned_geo_pairs import guard_workflow
        guard_workflow(db,p,current)
        artifacts = {}
        with db.begin_nested():
            for kind, (filename, media) in ARTIFACTS.items():
                path = Path(temporary) / filename; raw = path.read_bytes()
                if len(raw) > RECIPE['max_comparison_pixels'] * 4 + 1024 * 1024:
                    raise ValueError('学习型变化证据超过固定本地额度')
                if media == 'image/png':
                    ev = save_evidence(db, p, raw, media)
                else:
                    ev = save_immutable(db, p, raw, media, {'modality': 'learned_change_derivative', 'validated_by': RECIPE_HASH})
                ev.data = {**ev.data, 'purpose': 'geo_pair_comparison', 'pair_id': identity,
                           'sources_hash': data['sources_hash'], 'generation': generation, 'kind': kind}
                db.flush(); evidence_bytes(db, p, ev.id)
                artifacts[kind] = {'evidence_id': ev.id, 'sha256': ev.sha256, 'media_type': media}
            result = {**result, 'artifacts': artifacts, 'sources_hash': data['sources_hash'],
                      'model_trace': data['model_trace'], 'model_trace_hash': data['model_trace_hash']}
            current.status = 'succeeded'; current.version += 1
            current.data = {**current.data, 'result': result, 'result_hash': digest(result), 'error': None}
            completed = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action='geo.pair_saved', object_id=identity,
                data={'learned_model': True, 'cloud_calls': 0, 'candidate_pixels': result['candidate_pixels']})
            db.add(completed); db.flush()
            from .native_learned_geo_pair_provenance_v138 import record_result
            record_result(db, p, current, completed)
        db.commit()


def checked_result(row):
    result = row.data['result']
    if (not isinstance(row.data.get('request'),dict) or digest(row.data['request'])!=row.data.get('request_hash')
            or not isinstance(result, dict) or result.get('schema') != 'local-changer-comparison-v1'
            or result.get('learned_model') is not True or result.get('cloud_calls') != 0
            or result.get('model_trace_hash') != row.data.get('model_trace_hash')
            or digest(result.get('model_trace')) != row.data.get('model_trace_hash')
            or result.get('sources_hash') != row.data.get('sources_hash')
            or result.get('runtime_hash') != row.data.get('runtime_hash')
            or result.get('probability_threshold') != row.data['request'].get('probability_threshold')
            or not isinstance(result.get('artifacts'),dict)
            or set(result.get('artifacts', {})) != set(ARTIFACTS)):
        raise HTTPException(409, '学习型变化结果与固定模型或证据不一致；原记录保留')
    for kind, saved in result['artifacts'].items():
        if not isinstance(saved,dict) or not isinstance(saved.get('evidence_id'),str) or saved.get('media_type')!=ARTIFACTS[kind][1]:
            raise HTTPException(409,'变化证据保存位置或格式损坏；原记录保留')
