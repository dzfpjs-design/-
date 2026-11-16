"""Read-only FTP transport. Only exact operator/workspace grants are usable."""
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
from .contracts import digest

class FTPError(ValueError):
    pass

_REF = re.compile(r'^GA_FTP_(?:USERNAME|PASSWORD)_[A-Z0-9_]{1,80}$')
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
        raise FTPError('FTP 根目录授权配置无效')
    return value


def _entry_name(value):
    return (isinstance(value, str) and 0 < len(value) <= 250 and value not in {'.', '..'}
            and '/' not in value and '\\' not in value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _endpoint(value):
    required = {'host', 'port', 'data_port_min', 'data_port_max'}
    try:
        if not isinstance(value, dict) or set(value) != required: raise ValueError()
        ip = ipaddress.ip_address(value['host'])
        # Plaintext FTP is allowed only on RFC1918 networks; loopback must be
        # explicitly enabled for isolated tests and is never enabled by default.
        private = any(ip in ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
        loopback = ip.is_loopback and os.getenv('GA_FTP_ALLOW_LOOPBACK_FIXTURE') == '1'
        port, low, high = value['port'], value['data_port_min'], value['data_port_max']
        if ip.version != 4 or str(ip) != value['host'] or not (private or loopback): raise ValueError()
        if type(port) is not int or not 1 <= port <= 65535 or type(low) is not int or type(high) is not int or not 1024 <= low <= high <= 65535 or high-low > 10000: raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise FTPError('FTP 运行环境允许列表无效') from None
    return dict(value)


def legacy_grants(workspace):
    try:
        allowed=json.loads(os.getenv('GA_FTP_ALLOWED_ENDPOINTS', '[]')); per_workspace=json.loads(os.getenv('GA_FTP_ACCESS_GRANTS', '{}'))
        if not isinstance(allowed,list) or not isinstance(per_workspace,dict):raise ValueError()
        global_endpoints=[_endpoint(x) for x in allowed]
        rows=per_workspace.get(workspace,[])
        if not isinstance(rows,list) or len(rows)>100:raise ValueError()
        out=[];seen=set()
        for row in rows:
            required={'name','endpoint','root','username_ref','password_ref','server_chroot_confirmed','plaintext_risk_confirmed'}
            if not isinstance(row,dict) or set(row)!=required:raise ValueError()
            ep=_endpoint(row['endpoint']);root=_path(row['root'])
            if ep not in global_endpoints or row['server_chroot_confirmed'] is not True or row['plaintext_risk_confirmed'] is not True:raise ValueError()
            if not isinstance(row['name'],str) or not row['name'].strip() or len(row['name'])>100 or any(ord(c)<32 for c in row['name']):raise ValueError()
            for name,prefix in [('username_ref','GA_FTP_USERNAME_'),('password_ref','GA_FTP_PASSWORD_')]:
                ref=row[name]
                if not isinstance(ref,str) or not ref.startswith(prefix) or not _REF.fullmatch(ref):raise ValueError()
            item={**row,'endpoint':ep,'root':root};identity=digest({'workspace':workspace,'grant':item});
            if identity in seen:raise ValueError()
            seen.add(identity);out.append({'id':identity,**item})
        return out
    except (ValueError,TypeError,KeyError,json.JSONDecodeError,FTPError):
        raise FTPError('FTP 服务授权配置无效，请联系管理员核对') from None


def grants(workspace,db=None):
    from .managed_file_grants import merge_grants
    return merge_grants(workspace,'ftp',legacy_grants(workspace),db=db)


def find_grant(workspace,identity,db=None):
    item=next((x for x in grants(workspace,db=db) if x['id']==identity),None)
    if not item:raise FTPError('此工作区的 FTP 授权已变更或撤销，请联系管理员')
    return item


def public_grant(row):
    ep=row['endpoint'];return {'id':row['id'],'name':row['name'],'host':ep['host'],'port':ep['port'],'root':row['root']}


class _PinnedFTP(ftplib.FTP):
    def __init__(self, grant):
        super().__init__(timeout=5, encoding='utf-8'); self.grant = grant
    def makepasv(self):
        host, port = super().makepasv(); ep = self.grant['endpoint']
        if host != ep['host'] or not ep['data_port_min'] <= port <= ep['data_port_max']:
            raise FTPError('FTP 数据通道超出批准的地址或端口范围')
        return host, port


def _open(grant):
    if grant.get('_managed') is True:
        from .managed_file_grants import credentials
        try:user,password=credentials(grant)
        except Exception:raise FTPError('FTP 授权凭据不可用或密钥丢失，请联系管理员核对') from None
    else:
        user, password = os.getenv(grant['username_ref']), os.getenv(grant['password_ref'])
    if not user or not password or any(ord(c)<32 or ord(c)==127 for c in user+password):
        raise FTPError('FTP 凭据未配置或格式无效')
    ftp = None
    try:
        ftp = _PinnedFTP(grant); ep = grant['endpoint']
        ftp.connect(ep['host'], ep['port'], timeout=5); ftp.login(user, password); ftp.cwd(grant['root'])
        if ftp.pwd() != grant['root']: raise FTPError('FTP 服务端工作目录与批准根目录不一致')
        return ftp
    except FTPError:
        if ftp is not None: ftp.close()
        raise
    except (OSError, ftplib.Error, ValueError):
        if ftp is not None: ftp.close()
        raise FTPError('无法建立 FTP 连接，请核对网络、凭据和服务端设置') from None


def _facts(ftp,deadline,max_file_bytes=MAX_FILE_BYTES):
    count=0;total=0;rows=[];seen=set()
    def receive(line):
        nonlocal count,total
        if time.monotonic()>deadline:raise FTPError('FTP 目录读取超时')
        count+=1;total+=len(line.encode('utf-8'))
        if count>MAX_ENTRIES or total>MAX_LIST_BYTES:raise FTPError('FTP 目录超过条数或大小限制')
        try:
            facts,name=line.split(' ',1);parts=facts.rstrip(';').split(';');pairs=[x.split('=',1) for x in parts];d={k.lower():v for k,v in pairs}
            if len(d)!=len(pairs) or not _entry_name(name):return
            if name in seen:raise FTPError('FTP 目录含有同名记录，无法确定来源')
            seen.add(name)
            # Unverifiable or non-regular entries (including symbolic links) are never offered.
            if d.get('type','').lower()!='file' or not re.fullmatch(r'\d{1,12}',d.get('size','')) or not _MODIFIED.fullmatch(d.get('modify','')) or not d.get('unique') or len(d['unique'])>250 or any(ord(c)<32 or ord(c)==127 for c in d['unique']):return
            size=int(d['size'])
            if not 0<size<=max_file_bytes:return
            rows.append({'name':name,'size':size,'modified':d['modify'],'unique':d['unique']})
        except (ValueError,UnicodeError):raise FTPError('FTP 目录格式无法核对') from None
    ftp.retrlines('MLSD',receive)
    if time.monotonic()>deadline:raise FTPError('FTP 目录读取超时')
    return rows


def list_files(grant,*,max_file_bytes=MAX_FILE_BYTES):
    deadline=time.monotonic()+DEADLINE;ftp=_open(grant)
    try:return _facts(ftp,deadline,max_file_bytes)
    except FTPError:raise
    except (OSError,ssl.SSLError,ftplib.Error,UnicodeError):raise FTPError('FTP 目录读取失败，未保存不完整目录') from None
    finally:ftp.close()


def read_file(grant,expected):
    if not isinstance(expected,dict) or set(expected)!={'name','size','modified','unique'} or not _entry_name(expected['name']):raise FTPError('原目录快照无法核对')
    deadline=time.monotonic()+DEADLINE;ftp=_open(grant);body=bytearray()
    try:
        before=next((x for x in _facts(ftp,deadline) if x['name']==expected['name']),None)
        if before!=expected:raise FTPError('远端文件信息已变化，请重新读取目录')
        def receive(chunk):
            if time.monotonic()>deadline or len(body)+len(chunk)>min(expected['size'],MAX_FILE_BYTES):raise FTPError('FTP 文件超过大小或时间限制')
            body.extend(chunk)
        ftp.retrbinary('RETR '+expected['name'],receive,blocksize=65536)
        after=next((x for x in _facts(ftp,deadline) if x['name']==expected['name']),None)
        if len(body)!=expected['size'] or after!=expected:raise FTPError('远端文件读取前后不一致，未导入')
        return bytes(body),hashlib.sha256(body).hexdigest()
    except FTPError:raise
    except (OSError,ssl.SSLError,ftplib.Error,UnicodeError):raise FTPError('FTP 文件读取失败，原资料未导入') from None
    finally:ftp.close()


def stream_file(grant,expected,destination,*,max_file_bytes,deadline_seconds=900):
    """Bounded, incremental remote read to a caller-owned private temporary file."""
    if (not isinstance(expected,dict) or set(expected)!={'name','size','modified','unique'} or not _entry_name(expected['name'])
        or type(expected['size']) is not int or not 0<expected['size']<=max_file_bytes):raise FTPError('原影像目录快照无法核对')
    deadline=time.monotonic()+deadline_seconds;ftp=_open(grant);checksum=hashlib.sha256();size=0
    try:
        if next((x for x in _facts(ftp,deadline,max_file_bytes) if x['name']==expected['name']),None)!=expected:raise FTPError('远端影像已变化，请重新读取目录')
        def receive(chunk):
            nonlocal size
            if time.monotonic()>deadline or size+len(chunk)>expected['size']:raise FTPError('FTP 影像超过大小或时间限制')
            destination.write(chunk);checksum.update(chunk);size+=len(chunk)
        ftp.retrbinary('RETR '+expected['name'],receive,blocksize=65536)
        after=next((x for x in _facts(ftp,deadline,max_file_bytes) if x['name']==expected['name']),None)
        if size!=expected['size'] or after!=expected:raise FTPError('远端影像读取前后不一致，未导入')
        return size,checksum.hexdigest()
    except FTPError:raise
    except (OSError,ssl.SSLError,ftplib.Error,UnicodeError):raise FTPError('FTP 影像读取失败，原影像未导入') from None
    finally:ftp.close()
