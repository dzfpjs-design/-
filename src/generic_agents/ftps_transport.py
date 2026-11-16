"""Read-only FTPS transport. Only exact operator/workspace grants are usable."""
from __future__ import annotations
import ftplib
import hashlib
import ipaddress
import json
import os
import posixpath
import re
import socket
import ssl
import stat
import time
from pathlib import Path
from .config import settings
from .contracts import digest

class FTPSError(ValueError):
    pass

_REF = re.compile(r'^GA_FTPS_(?:USERNAME|PASSWORD|CA)_[A-Z0-9_]{1,80}$')
_TLS_NAME = re.compile(r'^[A-Za-z0-9.-]{1,253}$')
_MODIFIED = re.compile(r'^\d{14}(?:\.\d{1,6})?$')
MAX_ENTRIES = 500
MAX_LIST_BYTES = 512 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
DEADLINE = 35


def _path(value):
    if (not isinstance(value, str) or not value.startswith('/') or len(value) > 1000
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or '\\' in value or any(part in {'.', '..'} for part in value.split('/'))
            or posixpath.normpath(value) != value):
        raise FTPSError('FTPS 根目录授权配置无效')
    return value


def _entry_name(value):
    return (isinstance(value, str) and 0 < len(value) <= 250 and value not in {'.', '..'}
            and '/' not in value and '\\' not in value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _endpoint(value):
    if not isinstance(value,dict) or set(value)!={'host','port','tls_name','data_port_min','data_port_max'}:
        raise FTPSError('FTPS 运行环境允许列表无效')
    try:
        ip=ipaddress.ip_address(value['host']); port=value['port']; low=value['data_port_min']; high=value['data_port_max']; name=value['tls_name']
        if (ip.version!=4 or str(ip)!=value['host'] or ip.is_multicast or ip.is_unspecified or ip.is_link_local
                or type(port) is not int or not 1<=port<=65535 or type(low) is not int or type(high) is not int
                or not 1024<=low<=high<=65535 or high-low>10000 or not isinstance(name,str)
                or not _TLS_NAME.fullmatch(name) or '..' in name or name.startswith('.') or name.endswith('.')):raise ValueError()
    except (ValueError,TypeError,KeyError):raise FTPSError('FTPS 运行环境允许列表无效') from None
    return dict(value)


def legacy_grants(workspace):
    try:
        allowed=json.loads(settings().ftps_allowed_endpoints); per_workspace=json.loads(settings().ftps_access_grants)
        if not isinstance(allowed,list) or not isinstance(per_workspace,dict):raise ValueError()
        global_endpoints=[_endpoint(x) for x in allowed]
        rows=per_workspace.get(workspace,[])
        if not isinstance(rows,list) or len(rows)>100:raise ValueError()
        out=[];seen=set()
        for row in rows:
            required={'name','endpoint','root','username_ref','password_ref','ca_ref','server_chroot_confirmed'}
            if not isinstance(row,dict) or set(row)!=required:raise ValueError()
            ep=_endpoint(row['endpoint']);root=_path(row['root'])
            if ep not in global_endpoints or row['server_chroot_confirmed'] is not True:raise ValueError()
            if not isinstance(row['name'],str) or not row['name'].strip() or len(row['name'])>100 or any(ord(c)<32 for c in row['name']):raise ValueError()
            for name,prefix in [('username_ref','GA_FTPS_USERNAME_'),('password_ref','GA_FTPS_PASSWORD_')]:
                ref=row[name]
                if not isinstance(ref,str) or not ref.startswith(prefix) or not _REF.fullmatch(ref):raise ValueError()
            ca=row['ca_ref']
            if not isinstance(ca,str) or ca and (not ca.startswith('GA_FTPS_CA_') or not _REF.fullmatch(ca)):raise ValueError()
            item={**row,'endpoint':ep,'root':root};identity=digest({'workspace':workspace,'grant':item});
            if identity in seen:raise ValueError()
            seen.add(identity);out.append({'id':identity,**item})
        return out
    except (ValueError,TypeError,KeyError,json.JSONDecodeError,FTPSError):
        raise FTPSError('FTPS 服务授权配置无效，请联系管理员核对') from None


def grants(workspace,db=None):
    from .managed_ftps_grants import merge
    return merge(workspace,legacy_grants(workspace),db=db)


def find_grant(workspace,identity,db=None):
    item=next((x for x in grants(workspace,db=db) if x['id']==identity),None)
    if not item:raise FTPSError('此工作区的 FTPS 授权已变更或撤销，请联系管理员')
    return item


def public_grant(row):
    ep=row['endpoint'];return {'id':row['id'],'name':row['name'],'host':ep['host'],'port':ep['port'],'root':row['root']}


def _ca_file(ref):
    if not ref:return None
    value=os.getenv(ref)
    if not value:raise FTPSError('FTPS 可信证书尚未配置')
    p=Path(value)
    try:
        if not p.is_absolute() or '..' in p.parts or p.resolve(strict=True)!=p:raise ValueError()
        st=p.stat(follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in {0,os.getuid()} or st.st_mode&0o022 or st.st_size>1024*1024:raise ValueError()
        for parent in p.parents:
            st=parent.stat(follow_symlinks=False)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid not in {0,os.getuid()} or (st.st_mode&0o022 and not (st.st_uid==0 and st.st_mode&stat.S_ISVTX)):raise ValueError()
    except (OSError,ValueError):raise FTPSError('FTPS 可信证书配置无效') from None
    return str(p)


class _PinnedFTP(ftplib.FTP_TLS):
    def __init__(self,grant,context):
        super().__init__(context=context,timeout=5,encoding='utf-8');self.grant=grant
    def makepasv(self):
        host,port=super().makepasv();ep=self.grant['endpoint']
        if host!=ep['host'] or not ep['data_port_min']<=port<=ep['data_port_max']:
            raise FTPSError('FTPS 数据通道超出批准的地址或端口范围')
        return host,port


def _open(grant):
    if grant.get('_managed'):
        try:
            from .managed_ftps_grants import credentials
            user,password,ca=credentials(grant)
        except (ValueError, KeyError):raise FTPSError('FTPS 授权或安全存储不可用') from None
    else:
        user=os.getenv(grant['username_ref']);password=os.getenv(grant['password_ref']);ca=None
    if not user or not password or any(ord(c)<32 or ord(c)==127 for c in user+password):
        raise FTPSError('FTPS 凭据未配置或格式无效')
    ftp=None
    try:
        ctx=ssl.create_default_context(cadata=ca or None) if grant.get('_managed') else ssl.create_default_context(cafile=_ca_file(grant['ca_ref']))
        ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ftp=_PinnedFTP(grant,ctx);ep=grant['endpoint'];ftp.connect(ep['host'],ep['port'],timeout=5)
        # The socket is pinned to the approved IP; SNI and certificate matching use the explicitly approved name.
        ftp.host=ep['tls_name'];ftp.auth();ftp.login(user,password,secure=False);ftp.prot_p();ftp.cwd(grant['root'])
        if ftp.pwd()!=grant['root']:raise FTPSError('FTPS 服务端工作目录与批准根目录不一致')
        return ftp
    except FTPSError:
        if ftp is not None:ftp.close()
        raise
    except (OSError,ssl.SSLError,ftplib.Error,ValueError):
        if ftp is not None:ftp.close()
        raise FTPSError('无法建立经过证书验证的 FTPS 连接，请核对网络、凭据和服务端设置') from None


def _facts(ftp,deadline):
    count=0;total=0;rows=[];seen=set()
    def receive(line):
        nonlocal count,total
        if time.monotonic()>deadline:raise FTPSError('FTPS 目录读取超时')
        count+=1;total+=len(line.encode('utf-8'))
        if count>MAX_ENTRIES or total>MAX_LIST_BYTES:raise FTPSError('FTPS 目录超过条数或大小限制')
        try:
            facts,name=line.split(' ',1);parts=facts.rstrip(';').split(';');pairs=[x.split('=',1) for x in parts];d={k.lower():v for k,v in pairs}
            if len(d)!=len(pairs) or not _entry_name(name):return
            if name in seen:raise FTPSError('FTPS 目录含有同名记录，无法确定来源')
            seen.add(name)
            # Unverifiable or non-regular entries (including symbolic links) are never offered.
            if d.get('type','').lower()!='file' or not re.fullmatch(r'\d{1,12}',d.get('size','')) or not _MODIFIED.fullmatch(d.get('modify','')) or not d.get('unique') or len(d['unique'])>250 or any(ord(c)<32 or ord(c)==127 for c in d['unique']):return
            size=int(d['size'])
            if not 0<size<=MAX_FILE_BYTES:return
            rows.append({'name':name,'size':size,'modified':d['modify'],'unique':d['unique']})
        except (ValueError,UnicodeError):raise FTPSError('FTPS 目录格式无法核对') from None
    ftp.retrlines('MLSD',receive)
    if time.monotonic()>deadline:raise FTPSError('FTPS 目录读取超时')
    return rows


def list_files(grant):
    deadline=time.monotonic()+DEADLINE;ftp=_open(grant)
    try:return _facts(ftp,deadline)
    except FTPSError:raise
    except (OSError,ssl.SSLError,ftplib.Error,UnicodeError):raise FTPSError('FTPS 目录读取失败，未保存不完整目录') from None
    finally:ftp.close()


def read_file(grant,expected):
    if not isinstance(expected,dict) or set(expected)!={'name','size','modified','unique'} or not _entry_name(expected['name']):raise FTPSError('原目录快照无法核对')
    deadline=time.monotonic()+DEADLINE;ftp=_open(grant);body=bytearray()
    try:
        before=next((x for x in _facts(ftp,deadline) if x['name']==expected['name']),None)
        if before!=expected:raise FTPSError('远端文件信息已变化，请重新读取目录')
        def receive(chunk):
            if time.monotonic()>deadline or len(body)+len(chunk)>min(expected['size'],MAX_FILE_BYTES):raise FTPSError('FTPS 文件超过大小或时间限制')
            body.extend(chunk)
        ftp.retrbinary('RETR '+expected['name'],receive,blocksize=65536)
        after=next((x for x in _facts(ftp,deadline) if x['name']==expected['name']),None)
        if len(body)!=expected['size'] or after!=expected:raise FTPSError('远端文件读取前后不一致，未导入')
        return bytes(body),hashlib.sha256(body).hexdigest()
    except FTPSError:raise
    except (OSError,ssl.SSLError,ftplib.Error,UnicodeError):raise FTPSError('FTPS 文件读取失败，原资料未导入') from None
    finally:ftp.close()
