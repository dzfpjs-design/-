"""Workspace-scoped OPC UA reads with explicit endpoint/node and certificate pins."""
from __future__ import annotations
import asyncio
import hashlib
import ipaddress
import json
import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from .contracts import digest

class OpcuaError(Exception):pass

_NETS=tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))

def _load_files(entry):
    root=os.environ.get('GA_OPCUA_CERT_ROOT','')
    if not root or not Path(root).is_absolute():raise OpcuaError('证书目录未配置')
    root=Path(root).resolve(strict=True)
    result=[]
    for key in ('server_certificate','client_certificate','client_private_key'):
        path=Path(entry[key]);resolved=path.resolve(strict=True)
        if not path.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():raise OpcuaError('证书路径不在允许的证书目录内')
        if key=='client_private_key' and resolved.stat().st_mode & (stat.S_IRWXG|stat.S_IRWXO):raise OpcuaError('客户端私钥文件权限不安全')
        content=resolved.read_bytes()
        if not 0<len(content)<=32768:raise OpcuaError('证书文件大小无效')
        result.append(content)
    return validate_materials(entry,result)

def validate_materials(entry,result):
    if not isinstance(result,list) or len(result)!=3 or any(not isinstance(item,bytes) or not 0<len(item)<=32768 for item in result):
        raise OpcuaError('证书文件大小或格式无效')
    try:
        certs=[(x509.load_pem_x509_certificate(data) if data.startswith(b'-----BEGIN') else x509.load_der_x509_certificate(data)) for data in result[:2]]
        key=serialization.load_pem_private_key(result[2],password=None) if result[2].startswith(b'-----BEGIN') else serialization.load_der_private_key(result[2],password=None)
        if certs[1].public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)!=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):raise ValueError()
        utc=datetime.now(timezone.utc)
        for index,cert in enumerate(certs):
            if not cert.not_valid_before_utc<=utc<=cert.not_valid_after_utc:raise ValueError()
            public_key=cert.public_key()
            if not isinstance(public_key,rsa.RSAPublicKey) or not 2048<=public_key.key_size<=4096:raise ValueError()
            usage=cert.extensions.get_extension_for_class(x509.KeyUsage).value
            extended=cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if not usage.digital_signature or not usage.key_encipherment or (ExtendedKeyUsageOID.SERVER_AUTH if index==0 else ExtendedKeyUsageOID.CLIENT_AUTH) not in extended:raise ValueError()
        server_san=certs[0].extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        client_san=certs[1].extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(entry['host']) not in server_san.get_values_for_type(x509.IPAddress):raise ValueError()
        if entry['server_app_uri'] not in server_san.get_values_for_type(x509.UniformResourceIdentifier):raise ValueError()
        if entry['client_app_uri'] not in client_san.get_values_for_type(x509.UniformResourceIdentifier):raise ValueError()
        return {'server_der':certs[0].public_bytes(serialization.Encoding.DER),'client_der':certs[1].public_bytes(serialization.Encoding.DER),
                'private_der':key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),
                'server_sha256':certs[0].fingerprint(hashes.SHA256()).hex(),'client_sha256':certs[1].fingerprint(hashes.SHA256()).hex()}
    except (ValueError,TypeError,x509.ExtensionNotFound):raise OpcuaError('证书、私钥、有效期或设备身份不匹配') from None

def grants(workspace,db=None):
    try:
        entries=json.loads(os.getenv('GA_OPCUA_READ_GRANTS','[]'))
        if not isinstance(entries,list):raise ValueError()
        result=[]
        for e in entries:
            if not isinstance(e,dict):raise ValueError()
            if e.get('workspace')!=workspace:continue
            expected={'workspace','host','port','path','node_id','server_certificate','client_certificate','client_private_key','server_app_uri','client_app_uri'}
            if set(e)!=expected:raise ValueError()
            ip=ipaddress.IPv4Address(e['host']);port=e['port'];path=e['path'];node=e['node_id']
            if not any(ip in n for n in _NETS) or type(port)!=int or not 1<=port<=65535:raise ValueError()
            if not isinstance(path,str) or not path.startswith('/') or any(x in path for x in ('?','#','@','\\','..')) or len(path)>200 or any(ord(c)<32 for c in path):raise ValueError()
            if not isinstance(node,str) or not 1<=len(node)<=300 or any(ord(c)<32 for c in node):raise ValueError()
            if any(not isinstance(e[k],str) or not 1<=len(e[k])<=300 or any(ord(c)<32 for c in e[k]) for k in ('server_app_uri','client_app_uri')):raise ValueError()
            credentials=_load_files(e)
            public={'workspace':workspace,'host':str(ip),'port':port,'path':path,'node_id':node,'server_app_uri':e['server_app_uri'],
                    'client_app_uri':e['client_app_uri'],'server_sha256':credentials['server_sha256'],'client_sha256':credentials['client_sha256']}
            public['id']=digest(public);result.append({**public,'_credentials':credentials})
        from .managed_opcua_grants import managed_decisions
        decisions=managed_decisions(workspace,db)
        result=[grant for grant in result if grant['id'] not in decisions]
        result.extend(grant for grant in decisions.values() if grant is not None)
        return result
    except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError):raise OpcuaError('OPC UA 授权或证书配置无效') from None

def get_grant(workspace,identity,db=None):
    for grant in grants(workspace,db):
        if grant['id']==identity:return grant
    raise OpcuaError('OPC UA 授权不存在或已撤销')

def public_grant(grant):return {k:v for k,v in grant.items() if k not in ('_credentials','workspace')}

def _timestamp(value):
    if value is None:return None
    if value.tzinfo is None:raise OpcuaError('设备返回的时间没有时区，无法确认')
    return value.astimezone(timezone.utc).isoformat()

async def _read(grant):
    try:
        from asyncua import Client,ua
        from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
        from asyncua.client.ua_client import UASocketProtocol
        from asyncua.common.connection import TransportLimits
        from importlib.metadata import version
        if version('asyncua')!='2.1.0':raise OpcuaError('OPC UA 组件版本不受支持')
    except ImportError:raise OpcuaError('OPC UA 组件未安装，无法连接设备') from None
    credentials=grant['_credentials'];url=f"opc.tcp://{grant['host']}:{grant['port']}{grant['path']}"
    async def operation():
        client=Client(url,timeout=4);client.application_uri=grant['client_app_uri']
        # asyncua 2.1 defaults to unlimited inbound frames. Pin its protocol
        # factory to enforce a bounded response independently of peer claims.
        low=client.uaclient
        def bounded_protocol():
            protocol=UASocketProtocol(low._timeout,security_policy=low.security_policy,limits=TransportLimits(65535,65535,16,1048576))
            low.protocol=protocol;protocol.pre_request_hook=low._pre_request_hook;protocol.on_connection_lost=low._on_transport_lost;protocol.is_session_closing=low._is_session_closing
            return protocol
        low._make_protocol=bounded_protocol
        client.max_messagesize=1048576;client.max_chunkcount=16
        await client.set_security(SecurityPolicyBasic256Sha256,credentials['client_der'],credentials['private_der'],server_certificate=credentials['server_der'],mode=ua.MessageSecurityMode.SignAndEncrypt)
        async def validate(cert,application):
            if cert.fingerprint(hashes.SHA256()).hex()!=grant['server_sha256'] or application.ApplicationUri!=grant['server_app_uri']:
                raise OpcuaError('设备证书或应用身份不匹配')
        client.certificate_validator=validate
        async with client:
            value=await client.get_node(grant['node_id']).read_data_value(raise_on_bad_status=False)
            status=value.StatusCode
            quality='good' if status.is_good() else 'bad' if status.is_bad() else 'uncertain'
            raw=value.Value.Value if value.Value is not None else None
            if type(raw) not in (int,float) or not math.isfinite(raw) or abs(raw)>1e15:
                if quality=='good':raise OpcuaError('设备返回的不是有限数值')
                raw=None
            return {'value':raw,'status_code':int(status.value),'status_name':status.name,'quality':quality,
                    'source_timestamp':_timestamp(value.SourceTimestamp),'server_timestamp':_timestamp(value.ServerTimestamp),
                    'received_at':datetime.now(timezone.utc).isoformat()}
    try:return await asyncio.wait_for(operation(),timeout=10)
    except OpcuaError:raise
    except Exception:raise OpcuaError('OPC UA 连接或读取失败，请检查授权、证书和设备状态') from None

def read_value(grant):return asyncio.run(_read(grant))
