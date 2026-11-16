"""Workspace-private streamed raster originals; never load a large scene into API RAM."""
import hashlib,os,tempfile
from pathlib import Path
from fastapi import HTTPException
from .config import settings
from .geo_codec import RECIPE,file_digest
from .models import Evidence
from .services import scoped,audit
from .security import require


def path_for(db,p,identity):
    row=scoped(db,Evidence,identity,p)
    expected=p.workspace_id+'/'+row.sha256+'.bin'
    path=settings().storage_dir/expected
    if row.storage_key!=expected or not path.is_file() or path.is_symlink() or path.stat().st_size!=row.size_bytes or file_digest(path)!=row.sha256:
        raise HTTPException(409,'影像原件缺失或摘要不一致，请恢复原件；保存记录保留')
    return row,path


async def upload(db,p,file):
    require(p,'admin','operator')
    filename=(file.filename or '').replace('\\','/').split('/')[-1][:250]
    if Path(filename).suffix.lower() not in {'.tif','.tiff'}:raise HTTPException(415,'请选择本地GeoTIFF影像，当前不接收VRT或远程URL')
    directory=settings().storage_dir/p.workspace_id;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(prefix='raster-upload-',dir=directory);size=0;checksum=hashlib.sha256()
    try:
        with os.fdopen(fd,'wb') as dest:
            while chunk:=await file.read(1024*1024):
                if size==0 and chunk[:4] not in {b'II*\x00',b'MM\x00*',b'II+\x00',b'MM\x00+'}:raise HTTPException(415,'文件头不是TIFF；未打开外部数据源')
                size+=len(chunk)
                if size>RECIPE['max_upload_bytes']:raise HTTPException(413,'影像超过512MB本地上传上限，请分幅上传')
                checksum.update(chunk);dest.write(chunk)
            dest.flush();os.fsync(dest.fileno())
        if not size:raise HTTPException(413,'影像文件为空')
        sha=checksum.hexdigest();target=directory/(sha+'.bin')
        if target.exists():
            if target.is_symlink() or target.stat().st_size!=size or file_digest(target)!=sha:raise HTTPException(409,'相同摘要的已存原件损坏，请先恢复存储')
        else:os.replace(temp,target)
        if file_digest(target)!=sha:raise HTTPException(409,'影像保存读回检查失败，原件仍保留')
        row=Evidence(workspace_id=p.workspace_id,sha256=sha,size_bytes=size,media_type='image/tiff',storage_key=p.workspace_id+'/'+sha+'.bin',
            data={'modality':'raster','purpose':'geo_scene_original','name':filename,'cloud_model_ids':[],'uploaded_by':p.user_id})
        db.add(row);db.flush();audit(db,p,'geo.original_saved',row.id,{'sha256':sha,'size_bytes':size,'cloud_calls':0})
        return row
    finally:Path(temp).unlink(missing_ok=True)
