"""Private encrypted response spool for dedicated disposable tests only.

No report directory or report artifact is read. No spool record grants send or
retry authority. Only a full EOF, fsynced ciphertext and authenticated final
metadata permit recovery. A crash before that point remains unknown. OS account
owners and holders of the local test key remain outside the tamper boundary.
"""
import base64
import copy
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import UUID
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from . import same_goal_advice_first_response_candidate_v2 as responses

MAX_BYTES=16*1024*1024
MAX_SLOTS=4
CHUNK=64*1024
_DIR=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
_READ=os.O_RDONLY|os.O_NOFOLLOW
_HASH=re.compile(r'^[a-f0-9]{64}$')
_FILES={'context.json','headers.json','body.bin','final.json'}


class SpoolError(ValueError):
    def __init__(self,code):super().__init__(code);self.code=code


def _need(value,code='spool_invalid'):
    if not value:raise SpoolError(code)


def _json(value):
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def _check_context(value):
    fields={'schema','workspace_id','call_id','intent_id','permit_id','anchor_hash','provider','model'}
    _need(type(value) is dict and set(value)==fields and value['schema']=='advice-v2-private-spool-test-v1')
    for key in ('workspace_id','call_id','intent_id','permit_id'):
        try:_need(type(value[key]) is str and str(UUID(value[key]))==value[key])
        except (ValueError,TypeError,AttributeError):raise SpoolError('spool_invalid') from None
    _need(type(value['anchor_hash']) is str and _HASH.fullmatch(value['anchor_hash']) is not None
        and value['provider']=='deepseek' and type(value['model']) is str
        and re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}',value['model']) is not None)
    return copy.deepcopy(value)


def _owned(fd,directory=False):
    info=os.fstat(fd)
    _need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==(0o700 if directory else 0o600)
        and (directory or info.st_nlink==1),'spool_storage_unsafe')
    return info


def _root(path):
    _need(isinstance(path,Path) and path.is_absolute() and not path.is_symlink()
        and path.parent.resolve()==Path(tempfile.gettempdir()).resolve()
        and path.name.startswith('advice-v2-response-spool-'),'spool_root_invalid')
    fd=os.open(path,_DIR)
    try:_owned(fd,True);return fd
    except BaseException:os.close(fd);raise


def validate_root(path):
    """Check the private test root before consuming an irreversible intent."""
    try:fd=_root(path)
    except OSError:raise SpoolError('spool_root_invalid') from None
    os.close(fd)


def _write(fd,raw):
    view=memoryview(raw)
    while view:
        n=os.write(fd,view[:CHUNK]);_need(n>0,'spool_write_failed');view=view[n:]


def _new(directory,name,raw):
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    try:_owned(fd);_write(fd,raw);os.fsync(fd)
    finally:os.close(fd)
    os.fsync(directory)


def _read(directory,name,limit):
    fd=os.open(name,_READ,dir_fd=directory)
    try:
        before=_owned(fd);_need(before.st_size<=limit,'spool_storage_unsafe')
        chunks=[];total=0
        while True:
            data=os.read(fd,min(CHUNK,limit+1-total))
            if not data:break
            total+=len(data);_need(total<=limit,'spool_storage_unsafe');chunks.append(data)
        after=_owned(fd)
        ident=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns)
        _need(ident(before)==ident(after) and total==after.st_size,'spool_changed')
        return b''.join(chunks)
    finally:os.close(fd)


def _metadata(directory,name):
    raw=_read(directory,name,65536)
    def pairs(items):
        out={}
        for key,value in items:_need(key not in out);out[key]=value
        return out
    try:
        value=json.loads(raw.decode('ascii'),object_pairs_hook=pairs,
            parse_constant=lambda _x:(_ for _ in ()).throw(SpoolError('spool_invalid')))
        _need(_json(value)==raw);return value
    except (ValueError,TypeError,UnicodeError,RecursionError):raise SpoolError('spool_invalid') from None


def _key(root,create=False):
    if create:
        try:_new(root,'spool.key',os.urandom(32))
        except FileExistsError:pass
    raw=_read(root,'spool.key',32);_need(len(raw)==32,'spool_storage_unsafe');return raw


def _keys(key):
    return (hmac.new(key,b'advice-spool-encryption-v1',hashlib.sha256).digest(),
            hmac.new(key,b'advice-spool-authentication-v1',hashlib.sha256).digest())


def _lock(fd,exclusive=True):
    try:fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
    except OSError:raise SpoolError('spool_busy') from None


def prepare(root_path,context):
    """One exclusive response slot, created before a single test-only POST."""
    context=_check_context(context);root=lockfd=stage=body=None
    try:
        root=_root(root_path)
        lockfd=os.open('spool.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=root)
        _owned(lockfd);_lock(lockfd)
        key=_key(root,True)
        names=set(os.listdir(root));_need(names >= {'spool.key','spool.lock'},'spool_storage_unsafe')
        slots=names-{'spool.key','spool.lock'}
        _need(all(re.fullmatch(r'[a-f0-9-]{36}',name) for name in slots),'spool_storage_unsafe')
        _need(len(slots)<MAX_SLOTS,'spool_capacity_reached')
        try:os.mkdir(context['call_id'],0o700,dir_fd=root)
        except FileExistsError:raise SpoolError('spool_already_claimed') from None
        os.fsync(root)
        stage=os.open(context['call_id'],_DIR,dir_fd=root);_owned(stage,True)
        nonce=os.urandom(12);metadata={'context':context,'nonce':base64.b64encode(nonce).decode('ascii')}
        _new(stage,'context.json',_json(metadata));_new(stage,'body.bin',b'')
        body=os.open('body.bin',os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW,dir_fd=stage);info=_owned(body);_lock(body)
        enc,_mac=_keys(key);writer=Cipher(algorithms.AES(enc),modes.GCM(nonce)).encryptor()
        writer.authenticate_additional_data(_json(context))
        sink=Sink(root_path,os.dup(stage),body,(info.st_dev,info.st_ino),context,key,writer,nonce)
        body=None;return sink
    except OSError:raise SpoolError('spool_storage_unsafe') from None
    finally:
        for fd in (body,stage,lockfd,root):
            if fd is not None:os.close(fd)


class Sink:
    def __init__(self,root,stage,body,identity,context,key,writer,nonce):
        self.root=root;self.stage=stage;self.body=body;self.identity=identity;self.context=context
        self.key=key;self.writer=writer;self.nonce=nonce;self.size=0;self.sha=hashlib.sha256();self.closed=False;self.headers=None;self.poisoned=False

    def _active(self):
        _need(not self.closed and not self.poisoned and self.body is not None,'spool_closed')
        before=_owned(self.body);named=os.stat('body.bin',dir_fd=self.stage,follow_symlinks=False)
        _need((before.st_dev,before.st_ino)==self.identity==(named.st_dev,named.st_ino),'spool_changed')
        _need(set(os.listdir(self.stage)) <= _FILES and 'final.json' not in os.listdir(self.stage),'spool_changed')

    def begin(self,receipt):
        try:
            self._active();_need(self.headers is None)
            responses.seal(cloud_call_id=self.context['call_id'],intent_audit_id=self.context['intent_id'],raw=b'',receipt=receipt)
            self.headers=copy.deepcopy(receipt);_new(self.stage,'headers.json',_json(self.headers))
        except BaseException:
            self.poisoned=True
            raise

    def append(self,raw):
        try:
            self._active();_need(self.headers is not None and type(raw) is bytes)
            _need(self.size+len(raw)<=MAX_BYTES,'spool_capacity_reached')
            for index in range(0,len(raw),CHUNK):
                part=raw[index:index+CHUNK];_write(self.body,self.writer.update(part));self.sha.update(part);self.size+=len(part)
                os.fsync(self.body)
        except BaseException:
            self.poisoned=True
            raise

    def finish(self):
        try:
            self._active();_need(self.headers is not None)
            _write(self.body,self.writer.finalize());os.fsync(self.body);os.fsync(self.stage)
            value={'schema':'advice-v2-private-spool-complete-test-v1','context_hash':hashlib.sha256(_json(self.context)).hexdigest(),
                'raw_size':self.size,'raw_sha256':self.sha.hexdigest(),'nonce':base64.b64encode(self.nonce).decode('ascii'),
                'tag':base64.b64encode(self.writer.tag).decode('ascii'),
                'headers_hash':hashlib.sha256(_json(self.headers)).hexdigest()}
            _enc,mac=_keys(self.key);value['mac']=hmac.new(mac,_json(value),hashlib.sha256).hexdigest()
            _new(self.stage,'final.json',_json(value))
        except BaseException:
            self.poisoned=True
            # A failed fsync may leave a visible final file. Remove it on a
            # best-effort basis; media failure/crash during removal remains an
            # operator-visible ambiguity, never a reason to resend.
            try:
                os.unlink('final.json',dir_fd=self.stage)
                os.fsync(self.stage)
            except OSError:pass
            raise

    def close(self):
        if not self.closed:
            self.closed=True;os.close(self.body);os.close(self.stage)

    def __enter__(self):return self
    def __exit__(self,*_):self.close();return False


def load(root_path,context):
    """Return authenticated complete bytes only; incomplete never authorizes retry."""
    context=_check_context(context);root=stage=body=None
    try:
        root=_root(root_path);key=_key(root)
        stage=os.open(context['call_id'],_DIR,dir_fd=root);_owned(stage,True)
        body=os.open('body.bin',_READ,dir_fd=stage);_owned(body);_lock(body,False)
        _need(set(os.listdir(stage))==_FILES,'spool_incomplete')
        metadata=_metadata(stage,'context.json');headers=_metadata(stage,'headers.json');final=_metadata(stage,'final.json')
        _need(type(metadata) is dict and set(metadata)=={'context','nonce'} and metadata['context']==context)
        fields={'schema','context_hash','raw_size','raw_sha256','nonce','tag','headers_hash','mac'}
        _need(type(final) is dict and set(final)==fields and final['schema']=='advice-v2-private-spool-complete-test-v1')
        enc,mac=_keys(key);mac_value=final['mac']
        _need(type(mac_value) is str and hmac.compare_digest(mac_value,hmac.new(mac,_json({k:v for k,v in final.items() if k!='mac'}),hashlib.sha256).hexdigest()))
        _need(final['context_hash']==hashlib.sha256(_json(context)).hexdigest()
            and final['headers_hash']==hashlib.sha256(_json(headers)).hexdigest()
            and final['nonce']==metadata['nonce'] and type(final['raw_size']) is int and 0<=final['raw_size']<=MAX_BYTES)
        try:nonce=base64.b64decode(final['nonce'],validate=True);tag=base64.b64decode(final['tag'],validate=True)
        except (ValueError,TypeError):raise SpoolError('spool_invalid') from None
        _need(len(nonce)==12 and len(tag)==16)
        encrypted=_read(stage,'body.bin',MAX_BYTES)
        decryptor=Cipher(algorithms.AES(enc),modes.GCM(nonce,tag)).decryptor();decryptor.authenticate_additional_data(_json(context))
        try:raw=decryptor.update(encrypted)+decryptor.finalize()
        except Exception:raise SpoolError('spool_authentication_failed') from None
        _need(len(raw)==final['raw_size'] and hashlib.sha256(raw).hexdigest()==final['raw_sha256'])
        responses.seal(cloud_call_id=context['call_id'],intent_audit_id=context['intent_id'],raw=raw,receipt=headers)
        return raw,headers
    except (OSError,UnicodeError):raise SpoolError('spool_storage_unsafe') from None
    finally:
        for fd in (body,stage,root):
            if fd is not None:os.close(fd)
