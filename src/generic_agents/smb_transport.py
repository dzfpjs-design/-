"""Read-only, pinned SMB share root. No DFS resolution, recursive paths or writes."""
import hashlib,ipaddress,json,os,re,time,uuid
from datetime import timezone
from .contracts import digest
from .ftp_transport import _entry_name,MAX_ENTRIES,MAX_FILE_BYTES,DEADLINE

class SMBError(ValueError):pass

def _endpoint(value):
    try:
        if not isinstance(value,dict) or set(value)!={'host','port','share'}:raise ValueError()
        ip=ipaddress.ip_address(value['host']); private=any(ip in ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
        fixture=ip.is_loopback and os.getenv('GA_SMB_ALLOW_LOOPBACK_FIXTURE')=='1'
        if ip.version!=4 or str(ip)!=value['host'] or not (private or fixture) or type(value['port']) is not int or not 1<=value['port']<=65535:raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_\-$]{1,80}',value['share']):raise ValueError()
        return dict(value)
    except (ValueError,TypeError,KeyError):raise SMBError('SMB 地址或共享目录授权配置无效') from None

def legacy_grants(workspace):
    try:
        allowed=json.loads(os.getenv('GA_SMB_ALLOWED_ENDPOINTS','[]'));workspaces=json.loads(os.getenv('GA_SMB_ACCESS_GRANTS','{}'))
        if not isinstance(allowed,list) or not isinstance(workspaces,dict):raise ValueError()
        allowed=[_endpoint(x) for x in allowed];rows=workspaces.get(workspace,[])
        if not isinstance(rows,list) or len(rows)>100:raise ValueError()
        out=[];seen=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'name','endpoint','username_ref','password_ref','share_root_confirmed'}:raise ValueError()
            ep=_endpoint(row['endpoint'])
            if ep not in allowed or row['share_root_confirmed'] is not True:raise ValueError()
            if not isinstance(row['name'],str) or not row['name'].strip() or len(row['name'])>100 or any(ord(c)<32 for c in row['name']):raise ValueError()
            for key,prefix in (('username_ref','GA_SMB_USERNAME_'),('password_ref','GA_SMB_PASSWORD_')):
                if not isinstance(row[key],str) or not re.fullmatch(prefix+r'[A-Z0-9_]{1,80}',row[key]):raise ValueError()
            item={**row,'endpoint':ep};identity=digest({'workspace':workspace,'grant':item})
            if identity in seen:raise ValueError()
            seen.add(identity);out.append({'id':identity,**item})
        return out
    except (ValueError,TypeError,KeyError,SMBError):raise SMBError('SMB 服务授权配置无效，请联系管理员核对') from None

def grants(workspace,db=None):
    from .managed_file_grants import merge_grants
    return merge_grants(workspace,'smb',legacy_grants(workspace),db=db)

def find_grant(workspace,identity,db=None):
    result=next((g for g in grants(workspace,db=db) if g['id']==identity),None)
    if not result:raise SMBError('此工作区的 SMB 授权已变更或撤销，请联系管理员')
    return result

def public_grant(grant):
    ep=grant['endpoint'];return {'id':grant['id'],'name':grant['name'],'host':ep['host'],'port':ep['port'],'root':ep['share']}

def _open(grant):
    try:
        from smbprotocol.connection import Connection
        from smbprotocol.session import Session
        from smbprotocol.tree import TreeConnect
        ep=grant['endpoint']
        if grant.get('_managed') is True:
            from .managed_file_grants import credentials
            try:user,password=credentials(grant)
            except Exception:raise SMBError('SMB 授权凭据不可用或密钥丢失，请联系管理员核对') from None
        else:user=os.getenv(grant['username_ref']);password=os.getenv(grant['password_ref'])
        if not user or not password or any(ord(c)<32 or ord(c)==127 for c in user+password):raise SMBError('SMB 凭据未配置或格式无效')
        fixture=ipaddress.ip_address(ep['host']).is_loopback and os.getenv('GA_SMB_ALLOW_UNENCRYPTED_FIXTURE')=='1'
        class PinnedConnection(Connection):
            # smbprotocol otherwise follows server-supplied symlinks and waits indefinitely.
            def receive(self,request,wait=True,timeout=None,resolve_symlinks=True):
                return super().receive(request,wait=wait,timeout=min(timeout,5) if timeout is not None else 5,resolve_symlinks=False)
        conn=PinnedConnection(uuid.uuid4(),ep['host'],port=ep['port'],require_signing=True);conn.connect(timeout=5)
        try:
            session=Session(conn,user,password,require_encryption=not fixture,auth_protocol='ntlm');session.connect()
            if not session.encrypt_data and not fixture:raise SMBError('SMB 服务未启用加密，无法接入')
            if fixture and not session.signing_required:raise SMBError('SMB 测试服务未启用签名')
            tree=TreeConnect(session,'\\\\'+ep['host']+'\\'+ep['share']);tree.connect()
            return conn,tree
        except Exception:conn.disconnect(close=False);raise
    except SMBError:raise
    except Exception:raise SMBError('无法建立加密 SMB 连接，请核对网络、凭据和服务端设置') from None

def _handle(tree,name,directory):
    from smbprotocol.open import Open,ImpersonationLevel,FilePipePrinterAccessMask as Access,ShareAccess,CreateDisposition,CreateOptions
    result=Open(tree,name);options=CreateOptions.FILE_OPEN_REPARSE_POINT | (CreateOptions.FILE_DIRECTORY_FILE if directory else CreateOptions.FILE_NON_DIRECTORY_FILE)
    result.create(ImpersonationLevel.Impersonation,Access.FILE_READ_DATA|Access.FILE_READ_ATTRIBUTES,0,ShareAccess.FILE_SHARE_READ,CreateDisposition.FILE_OPEN,options)
    from smbprotocol.file_info import FileAttributes
    if result.file_attributes & FileAttributes.FILE_ATTRIBUTE_REPARSE_POINT:
        result.close();raise SMBError('SMB 重解析点不允许导入')
    return result

def _facts(tree,deadline,max_file_bytes=MAX_FILE_BYTES):
    from smbprotocol.file_info import FileInformationClass,FileAttributes
    from smbprotocol.exceptions import NoMoreFiles
    directory=_handle(tree,'',True);rows=[];seen=set()
    try:
        while True:
            if time.monotonic()>deadline:raise SMBError('SMB 目录读取超时')
            try:batch=directory.query_directory('*',FileInformationClass.FILE_ID_BOTH_DIRECTORY_INFORMATION,max_output=65536)
            except NoMoreFiles:break
            if not batch:break
            for f in batch:
                name=f['file_name'].get_value().decode('utf-16-le');attrs=f['file_attributes'].get_value();size=f['end_of_file'].get_value()
                if name in ('.','..'):continue
                if not _entry_name(name) or name.casefold() in seen:raise SMBError('SMB 目录含无法区分的名称')
                seen.add(name.casefold())
                if len(seen)>MAX_ENTRIES:raise SMBError('SMB 目录超过条数限制')
                if attrs & (FileAttributes.FILE_ATTRIBUTE_DIRECTORY|FileAttributes.FILE_ATTRIBUTE_REPARSE_POINT) or not 0<size<=max_file_bytes:continue
                modified=f['last_write_time'].get_value();created=f['creation_time'].get_value();changed=f['change_time'].get_value();fid=f['file_id'].get_value()
                if not modified or not fid:continue
                token=digest({'file_id':fid,'modified':str(modified),'created':str(created),'changed':str(changed),'size':size})
                rows.append({'name':name,'size':size,'modified':modified.astimezone(timezone.utc).strftime('%Y%m%d%H%M%S'),'unique':token})
        return rows
    finally:directory.close()

def list_files(grant,*,max_file_bytes=MAX_FILE_BYTES):
    conn,tree=_open(grant)
    try:return _facts(tree,time.monotonic()+DEADLINE,max_file_bytes)
    except SMBError:raise
    except Exception:raise SMBError('SMB 目录读取失败，未保存不完整目录') from None
    finally:conn.disconnect(close=False)

def read_file(grant,expected):
    if not isinstance(expected,dict) or set(expected)!={'name','size','modified','unique'} or not _entry_name(expected['name']):raise SMBError('原目录快照无法核对')
    conn,tree=_open(grant);deadline=time.monotonic()+DEADLINE;body=bytearray()
    try:
        if next((f for f in _facts(tree,deadline) if f['name']==expected['name']),None)!=expected:raise SMBError('远端文件信息已变化，请重新读取目录')
        file=_handle(tree,expected['name'],False)
        try:
            if file.end_of_file!=expected['size']:raise SMBError('远端文件大小已变化')
            while len(body)<expected['size']:
                if time.monotonic()>deadline:raise SMBError('SMB 文件读取超时')
                part=file.read(len(body),min(65536,expected['size']-len(body)))
                if not part:raise SMBError('SMB 文件不完整')
                body.extend(part)
        finally:file.close()
        if next((f for f in _facts(tree,deadline) if f['name']==expected['name']),None)!=expected:raise SMBError('远端文件读取前后不一致，未导入')
        return bytes(body),hashlib.sha256(body).hexdigest()
    except SMBError:raise
    except Exception:raise SMBError('SMB 文件读取失败，原资料未导入') from None
    finally:conn.disconnect(close=False)


def stream_file(grant,expected,destination,*,max_file_bytes,deadline_seconds=900):
    """Incremental read; no DFS or server-directed path resolution."""
    if (not isinstance(expected,dict) or set(expected)!={'name','size','modified','unique'} or not _entry_name(expected['name'])
        or type(expected['size']) is not int or not 0<expected['size']<=max_file_bytes):raise SMBError('原影像目录快照无法核对')
    conn,tree=_open(grant);deadline=time.monotonic()+deadline_seconds;checksum=hashlib.sha256();size=0
    try:
        if next((f for f in _facts(tree,deadline,max_file_bytes) if f['name']==expected['name']),None)!=expected:raise SMBError('远端影像已变化，请重新读取目录')
        file=_handle(tree,expected['name'],False)
        try:
            if file.end_of_file!=expected['size']:raise SMBError('远端影像大小已变化')
            while size<expected['size']:
                if time.monotonic()>deadline:raise SMBError('SMB 影像读取超时')
                part=file.read(size,min(65536,expected['size']-size))
                if not part or size+len(part)>expected['size']:raise SMBError('SMB 影像不完整或超出大小')
                destination.write(part);checksum.update(part);size+=len(part)
        finally:file.close()
        if next((f for f in _facts(tree,deadline,max_file_bytes) if f['name']==expected['name']),None)!=expected:raise SMBError('远端影像读取前后不一致，未导入')
        return size,checksum.hexdigest()
    except SMBError:raise
    except Exception:raise SMBError('SMB 影像读取失败，原影像未导入') from None
    finally:conn.disconnect(close=False)
