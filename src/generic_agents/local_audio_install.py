"""Pinned public assets and actual CPU probe under the existing installation lease."""
import hashlib,io,math,os,time,wave,struct
from pathlib import Path
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from .local_audio_paths import audio_paths
from .contracts import digest
from .local_audio import RECIPE,RECIPE_HASH,call,ready
from .local_specialists import guard_record,node_lock
from .models import SpecialistInstallation
from .services import scoped,audit,require

PACK=RECIPE['pack_id']
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def safe_path(path):
    path=path.absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('本地语音缓存不能使用符号链接')
    return path

def download_file(client,item,root,progress,guard):
    if Path(item['name']).name!=item['name']:raise ValueError('模型文件名不属于固定本地包')
    root=safe_path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=safe_path(root/item['name']);part=safe_path(root/(item['name']+'.part'));guard()
    if target.is_file() and target.stat().st_size==item['bytes'] and sha(target)==item['sha256']:
        progress('hash_check',file=item['name'],downloaded=item['bytes'],total=item['bytes'],cache_verified=True);return
    offset=part.stat().st_size if part.is_file() else 0
    if offset==item['bytes'] and sha(part)==item['sha256']:
        guard();os.replace(part,target);progress('hash_check',file=item['name'],downloaded=offset,total=offset,cache_verified=True);return
    if offset>=item['bytes']:offset=0
    url=item.get('url') or f"https://modelscope.cn/models/{RECIPE['repository']}/resolve/{item['revision']}/{item['name']}"
    from urllib.parse import urlsplit
    endpoint=urlsplit(url)
    if endpoint.scheme!='https' or endpoint.hostname not in {'modelscope.cn','files.pythonhosted.org'} or endpoint.username or endpoint.password:raise ValueError('公开模型下载来源不在固定官方范围')
    progress('downloading',file=item['name'],downloaded=offset,total=item['bytes'],cache_verified=False)
    with client.stream('GET',url,headers={'Range':f'bytes={offset}-'} if offset else {}) as r:
        r.raise_for_status()
        if r.url.scheme!='https':raise ValueError('官方模型下载必须使用HTTPS')
        if offset and r.status_code==206:
            if r.headers.get('content-range')!=f"bytes {offset}-{item['bytes']-1}/{item['bytes']}":raise ValueError('官方模型断点范围与固定大小不一致')
        else:offset=0
        flags=os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)|(os.O_APPEND if offset else os.O_TRUNC)
        with os.fdopen(os.open(part,flags,0o600),'wb') as f:
            size=offset;reported=time.monotonic()
            for block in r.iter_bytes(1024*1024):
                guard();size+=len(block)
                if size>item['bytes']:raise ValueError('官方模型文件超过固定包大小，停止加载')
                f.write(block)
                if time.monotonic()-reported>2:
                    f.flush();progress('downloading',file=item['name'],downloaded=size,total=item['bytes'],cache_verified=False);reported=time.monotonic()
            f.flush();os.fsync(f.fileno())
    guard()
    if part.stat().st_size!=item['bytes'] or sha(part)!=item['sha256']:raise ValueError('官方语音文件大小或摘要不一致，保留下载文件，不加载')
    os.replace(part,target);progress('hash_check',file=item['name'],downloaded=item['bytes'],total=item['bytes'],cache_verified=False)

def probe():
    # Reproducible synthetic non-silent waveform; this is not speech-quality evidence.
    b=io.BytesIO()
    with wave.open(b,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(b''.join(struct.pack('<h',int(900*math.sin(2*math.pi*440*i/16000))) for i in range(8000)))
    return b.getvalue()

def process(db,p,job):
    require(p,'admin');row=scoped(db,SpecialistInstallation,job.data['installation_id'],p,True);generation=job.data['generation']
    if row.status in {'ready','cancelled'} or row.data.get('generation')!=generation:return
    if row.pack_id!=PACK or row.data['recipe_hash']!=RECIPE_HASH or row.data['source']!='official' or row.data['backend']!='cpu':raise ValueError('语音安装不属于固定官方CPU包')
    identity=row.id
    paths=audio_paths()
    def guard():return guard_record(db,p,SpecialistInstallation,identity,generation)
    def progress(stage,**value):
        current=guard();current.status='installing';current.version+=1;current.data={**current.data,'stage':stage,'progress':value};db.commit()
    with node_lock():
        if not paths.python.is_file():raise ValueError('语音隔离运行环境尚未部署，请由运维准备固定依赖；不会转云')
        progress('license_check',production_license_verified=False)
        def https_only(request):
            if request.url.scheme!='https':raise ValueError('官方模型下载不能降级为HTTP')
        with httpx.Client(trust_env=False,follow_redirects=True,timeout=httpx.Timeout(30,connect=8),event_hooks={'request':[https_only]}) as client:
            for item in RECIPE['files']:download_file(client,item,paths.weights,progress,guard)
        progress('loading');content=probe();result=call(content,{'language':'zh','channel':'mono'},guard=guard,on_started=lambda pid:progress('model_check',pid=pid),_paths=paths)
        if not result['transcription']['inference_performed']:raise ValueError('本机语音推理检查未实际执行模型')
        current=guard();current.status='ready';current.version+=1
        current.data={**current.data,'stage':'smoke_check','error':None,'domain_evaluation':'pending','artifact_hashes':{f['name']:f['sha256'] for f in RECIPE['files']},
            'smoke':{'backend':'cpu','deployment':'local','inference_performed':True,'cloud_calls':0,'probe_sha256':hashlib.sha256(content).hexdigest(),'duration_seconds':result['audio']['duration_seconds'],'elapsed_seconds':result['elapsed_seconds'],'probe_kind':'synthetic_non_speech_engineering_only','runner_sha256':RECIPE['runner_sha256'],'production_license_verified':False}}
        audit(db,p,'audio.install.ready',current.id,{'recipe_hash':RECIPE_HASH,'generation':generation,'engineering_probe_only':True});db.commit()

def installed(db,p,identity=None):
    row=scoped(db,SpecialistInstallation,identity,p) if identity else db.scalar(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id==p.workspace_id,SpecialistInstallation.pack_id==PACK))
    if not row or row.pack_id!=PACK or row.status!='ready' or row.data.get('recipe_hash')!=RECIPE_HASH or not row.data.get('smoke',{}).get('inference_performed'):raise HTTPException(409,'请先在资源与能力安装并检查本地语音包')
    try:ready()
    except (ValueError,OSError) as e:raise HTTPException(409,str(e))
    return row

def trace(row):return {'installation_id':row.id,'pack_id':PACK,'generation':row.data['generation'],'recipe_hash':RECIPE_HASH,'backend':'cpu'}
