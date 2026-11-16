"""Node-local encrypted camera credentials, bound to one exact workspace grant.

The key is deliberately outside the evidence directory. Operators must back up
both the key and evidence store; loss of the key cannot be silently repaired.
"""
import base64, fcntl, json, os, re, ssl, stat, tempfile, uuid
from contextlib import contextmanager
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from .config import settings

PREFIX='GA_ONVIF_CAMERA_SECRET_UI_'
PATTERN=re.compile(r'^GA_ONVIF_CAMERA_SECRET_UI_([A-F0-9]{32})_([A-F0-9]{32})$')

class StoreError(ValueError): pass

def reference(workspace_id, request_id):
    try:return PREFIX+uuid.UUID(workspace_id).hex.upper()+'_'+uuid.UUID(request_id).hex.upper()
    except (ValueError,TypeError,AttributeError):raise StoreError('工作区或请求标识无效') from None

def _paths(ref):
    match=PATTERN.fullmatch(ref or '')
    if not match:raise StoreError('凭据引用无效')
    root=Path(settings().storage_dir).resolve()
    return root/'camera-secure-store'/match.group(1)/(match.group(2)+'.sealed'), root.parent/'camera-secure-store.key'

def _key(path,create=False):
    try:
        if create:
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if not path.exists() and any((Path(settings().storage_dir).resolve()/'camera-secure-store').glob('*/*.sealed')):
                raise StoreError('摄像头安全存储密钥丢失；现有授权保留，请从备份恢复密钥')
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key());f.flush();os.fsync(f.fileno())
            except FileExistsError:pass
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:raise StoreError('摄像头密钥文件权限不安全')
            value=f.read(256)
        return Fernet(value)
    except (OSError,ValueError):raise StoreError('摄像头安全存储密钥不可用，请联系管理员恢复') from None

def read(ref):
    path,key=_paths(ref)
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:raise StoreError('摄像头安全记录权限不安全')
            cipher=f.read(262145)
        if len(cipher)>262144:raise ValueError()
        value=json.loads(_key(key).decrypt(cipher))
        if not isinstance(value,dict) or value.get('reference')!=ref:raise ValueError()
        return value
    except FileNotFoundError:raise StoreError('未找到此摄像头授权') from None
    except (OSError,ValueError,InvalidToken,TypeError):raise StoreError('摄像头授权记录无法解密或已损坏') from None

@contextmanager
def locked(ref):
    path,_=_paths(ref);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(str(path)+'.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX);yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def _write(ref,payload):
    path,key=_paths(ref)
    payload={**payload,'reference':ref}
    if path.exists():
        old=read(ref)
        if old.get('state')=='cancelled':return 'cancelled'
        if old!=payload:raise StoreError('原请求已保存其他内容，请核对后发起新请求')
        return 'saved'
    cipher=_key(key,create=True).encrypt(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())
    fd,tmp=tempfile.mkstemp(prefix='.camera-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as f:f.write(cipher);f.flush();os.fsync(f.fileno())
        try:os.link(tmp,path,follow_symlinks=False)
        except FileExistsError:
            if read(ref)!=payload:raise StoreError('原请求已保存其他内容，请核对后发起新请求') from None
        d=os.open(path.parent,os.O_RDONLY);os.fsync(d);os.close(d)
        return 'saved'
    finally:
        try:os.unlink(tmp)
        except FileNotFoundError:pass

def save(ref, payload):
    for name in ('username','password'):
        value=payload.get(name)
        if not isinstance(value,str) or not 1<=len(value)<=256 or any(ord(c)<32 for c in value):raise StoreError('账号或密码格式无效')
    cert=payload.get('certificate')
    if not isinstance(cert,str) or not 1<=len(cert)<=65536 or 'PRIVATE KEY' in cert:raise StoreError('请填写不含私钥的可信 PEM 证书')
    try:
        ctx=ssl.create_default_context();ctx.load_verify_locations(cadata=cert)
    except (ssl.SSLError,ValueError):raise StoreError('可信 PEM 证书无效') from None
    with locked(ref):return _write(ref,payload)

def cancel(ref, workspace_id, actor_id, gateway_id, candidate_id):
    with locked(ref):
        try:item=read(ref)
        except StoreError as exc:
            if str(exc)!='未找到此摄像头授权':raise
            _write(ref,{'workspace_id':workspace_id,'actor_id':actor_id,'gateway_id':gateway_id,'candidate_id':candidate_id,'state':'cancelled'})
            return 'cancelled'
        if any(item.get(k)!=v for k,v in {'workspace_id':workspace_id,'actor_id':actor_id,'gateway_id':gateway_id,'candidate_id':candidate_id}.items()):raise StoreError('原请求绑定不一致')
        return 'cancelled' if item.get('state')=='cancelled' else 'saved'

def matching(workspace_id,device,gateway,include_revoked=False,include_unaudited=False):
    try: workspace=uuid.UUID(workspace_id).hex.upper()
    except (ValueError,TypeError):return []
    root=Path(settings().storage_dir).resolve()/'camera-secure-store'/workspace
    if not root.is_dir():return []
    refs=[]
    paths=[p for p in sorted(root.iterdir()) if p.name.endswith('.sealed') and re.fullmatch(r'[A-F0-9]{32}\.sealed',p.name)]
    if len(paths)>1000:raise StoreError('摄像头授权超过单次读取上限，请联系管理员分页处理')
    for path in paths:
        ref=PREFIX+workspace+'_'+path.stem
        if not PATTERN.fullmatch(ref):continue
        item=read(ref)
        if item.get('state')!='cancelled' and (include_revoked or not revoked(ref)) and item.get('workspace_id')==workspace_id and item.get('source_ip')==device.get('source_ip') and item.get('service_address')==device.get('service_address') and item.get('gateway_id')==gateway.id and item.get('gateway_origin')==gateway.data.get('api_origin'):
            if not audited(ref) and not include_unaudited:continue
            refs.append(ref)
    return refs

def revoked(ref):
    path,_=_paths(ref)
    return Path(str(path)+'.revoked').exists()

def audited(ref):
    path,_=_paths(ref)
    return Path(str(path)+'.audited').exists()

def mark_audited(ref):
    path,_=_paths(ref)
    try:
        fd=os.open(str(path)+'.audited',os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w') as f:f.write('audited\n');f.flush();os.fsync(f.fileno())
        d=os.open(path.parent,os.O_RDONLY);os.fsync(d);os.close(d)
    except FileExistsError:pass

def revocation_actor(ref):
    path,_=_paths(ref)
    try:
        fd=os.open(str(path)+'.revoked',os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'r') as f:
            value=json.load(f)
        actor=value.get('actor_id')
        if not isinstance(actor,str) or not actor:raise ValueError()
        return actor
    except (OSError,ValueError,TypeError):raise StoreError('凭据停用回执损坏，无法核对审计') from None

def revoke(ref, workspace_id, actor_id):
    with locked(ref):
        item=read(ref)
        if item.get('workspace_id')!=workspace_id or item.get('state')=='cancelled':raise StoreError('未找到此摄像头授权')
        path,_=_paths(ref)
        try:
            fd=os.open(str(path)+'.revoked',os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'w') as f:json.dump({'actor_id':actor_id},f);f.flush();os.fsync(f.fileno())
            d=os.open(path.parent,os.O_RDONLY);os.fsync(d);os.close(d)
        except FileExistsError:pass
