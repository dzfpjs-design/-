"""Bounded ONVIF Media 1 discovery and non-overwriting MediaMTX path setup.

All input addresses must already be explicitly authorized for the workspace.
This module does not log or return credentials or camera-provided stream URLs.
"""
import ipaddress
import json
import os
import re
import ssl
import time
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

import httpx
from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

LIMIT = 1024 * 1024
SOAP = 'http://www.w3.org/2003/05/soap-envelope'
DEVICE = 'http://www.onvif.org/ver10/device/wsdl'
MEDIA = 'http://www.onvif.org/ver10/media/wsdl'
SCHEMA = 'http://www.onvif.org/ver10/schema'
NS = {'s': SOAP, 'tds': DEVICE, 'trt': MEDIA, 'tt': SCHEMA}
REF = re.compile(r'^GA_ONVIF_CAMERA_SECRET_[A-Z0-9_]+$')


class EnrollmentError(ValueError):
    """A deliberately non-sensitive diagnostic suitable for display."""


def secret(reference):
    if not isinstance(reference, str) or not REF.fullmatch(reference):
        raise EnrollmentError('摄像头凭据引用无效')
    try:
        if reference.startswith('GA_ONVIF_CAMERA_SECRET_UI_'):
            from .camera_secure_store import read,revoked,audited
            if revoked(reference) or not audited(reference):raise ValueError()
            value=read(reference)
            return value['username'],value['password']
        value = json.loads(os.environ.get(reference, ''))
        if set(value) != {'username', 'password'}:
            raise ValueError()
        user, password = value['username'], value['password']
        if not all(isinstance(s, str) and 1 <= len(s) <= 256 and not any(ord(c) < 32 for c in s) for s in (user, password)):
            raise ValueError()
        return user, password
    except (ValueError, TypeError, KeyError):
        raise EnrollmentError('摄像头凭据未配置或格式无效') from None


def device_url(value, ip, *, stream=False):
    """Accept only literal addresses of the confirmed, granted camera."""
    try:
        addr = ipaddress.ip_address(ip)
        u = urlsplit(value)
        # HTTPS is required for ONVIF authentication. MediaMTX reads RTSP;
        # the camera and local network must support it under the user's grant.
        # Loopback can only be enabled explicitly for a local synthetic fixture;
        # it does not widen access to any other address or workspace.
        fixture = addr.is_loopback and os.environ.get('GA_ONVIF_ALLOW_LOOPBACK_FIXTURE') == '1'
        if addr.version != 4 or not addr.is_private or (addr.is_loopback and not fixture) or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            raise ValueError()
        if u.scheme != ('rtsp' if stream else 'https') or u.hostname != str(addr) or u.username or u.password or u.fragment:
            raise ValueError()
        if u.port is not None and not 1 <= u.port <= 65535:
            raise ValueError()
        if not u.path.startswith('/') or len(value) > 1800 or any(ord(c) < 33 for c in value):
            raise ValueError()
        if not stream and u.query:
            raise ValueError()
        return urlunsplit((u.scheme, u.netloc, u.path, u.query, ''))
    except (ValueError, TypeError):
        raise EnrollmentError('设备返回的地址不在已授权摄像头范围内或协议不支持') from None


def grant(workspace_id, source_ip, service_address, credential_ref, gateway_id, gateway_origin):
    expected = {'source_ip': source_ip, 'service_address': service_address, 'credential_ref': credential_ref,
                'gateway_id': gateway_id, 'gateway_origin': gateway_origin}
    try:
        if credential_ref.startswith('GA_ONVIF_CAMERA_SECRET_UI_'):
            from .camera_secure_store import read,revoked,audited
            if revoked(credential_ref) or not audited(credential_ref):raise ValueError()
            item=read(credential_ref)
            if item.get('state')=='cancelled' or item.get('reference')!=credential_ref or any(item.get(k)!=v for k,v in {'workspace_id':workspace_id,**{k:v for k,v in expected.items() if k!='credential_ref'}}.items()):raise ValueError()
            return expected
        grants = json.loads(os.environ.get('GA_ONVIF_ENROLLMENT_GRANTS', '{}'))
        entries = grants.get(workspace_id, [])
        if not isinstance(entries, list) or expected not in entries or not REF.fullmatch(credential_ref):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise EnrollmentError('该工作区尚未获得此设备、凭据及视频网关的精确授权') from None
    return expected


def _envelope(body):
    return (f'<s:Envelope xmlns:s="{SOAP}" xmlns:tds="{DEVICE}" xmlns:trt="{MEDIA}" xmlns:tt="{SCHEMA}">'
            f'<s:Body>{body}</s:Body></s:Envelope>').encode()


def _xml(client, address, action, body, auth):
    try:
        started=time.monotonic()
        with client.stream('POST', address, content=_envelope(body), auth=auth,
                           headers={'Content-Type':f'application/soap+xml; charset=utf-8; action="{action}"'}) as response:
            if 300 <= response.status_code < 400:
                raise EnrollmentError('摄像头返回了不允许的重定向')
            response.raise_for_status()
            data = bytearray()
            for part in response.iter_bytes():
                data.extend(part)
                if len(data) > LIMIT or time.monotonic()-started>25:
                    raise EnrollmentError('摄像头响应过大')
        root = fromstring(bytes(data), forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != '{%s}Envelope' % SOAP or root.find('./s:Body/s:Fault', NS) is not None:
            raise EnrollmentError('摄像头未接受只读媒体请求')
        return root
    except (httpx.HTTPError, ParseError, DefusedXmlException):
        raise EnrollmentError('无法认证摄像头或读取媒体配置，请核对设备和授权') from None


def stream_uri(service_address, source_ip, credential_ref, *, transport=None):
    """Get a stable H.264 RTSP address using ONVIF Media 1 and HTTP Digest."""
    source = device_url(service_address, source_ip)
    user, password = secret(credential_ref)
    auth = httpx.DigestAuth(user, password)
    if ipaddress.ip_address(source_ip).is_loopback and not (credential_ref.startswith('GA_ONVIF_CAMERA_SECRET_FIXTURE_') or (credential_ref.startswith('GA_ONVIF_CAMERA_SECRET_UI_') and os.environ.get('GA_ONVIF_ALLOW_LOOPBACK_FIXTURE')=='1')):
        raise EnrollmentError('本机模拟设备只能使用专用测试凭据引用')
    ca = os.environ.get('GA_ONVIF_TRUSTED_CA_FILE', '')
    try:
        if credential_ref.startswith('GA_ONVIF_CAMERA_SECRET_UI_'):
            from .camera_secure_store import read
            verify=ssl.create_default_context();verify.load_verify_locations(cadata=read(credential_ref)['certificate'])
        else:verify = ssl.create_default_context(cafile=ca) if ca else True
    except (OSError, ssl.SSLError): raise EnrollmentError('摄像头可信证书配置不可用') from None
    with httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False, transport=transport, verify=verify) as client:
        root = _xml(client, source, DEVICE+'/GetCapabilities', '<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>', auth)
        path = './/tds:GetCapabilitiesResponse/tds:Capabilities/tt:Media/tt:XAddr'
        media = device_url(root.findtext(path, namespaces=NS) or '', source_ip)
        root = _xml(client, media, MEDIA+'/GetProfiles', '<trt:GetProfiles/>', auth)
        profiles = root.findall('.//trt:GetProfilesResponse/trt:Profiles', NS)
        if len(profiles) > 100:
            raise EnrollmentError('摄像头媒体配置过多，未自动选择')
        profiles = [p for p in profiles if (p.findtext('./tt:VideoEncoderConfiguration/tt:Encoding', namespaces=NS) or '').upper() == 'H264']
        if not profiles:
            raise EnrollmentError('未找到可用的 H.264 媒体配置')
        token = profiles[0].get('token') or ''
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', token):
            raise EnrollmentError('摄像头媒体配置标识无效')
        body = ('<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>'
                '<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>'
                f'<trt:ProfileToken>{escape(token)}</trt:ProfileToken></trt:GetStreamUri>')
        root = _xml(client, media, MEDIA+'/GetStreamUri', body, auth)
        uri = root.findtext('.//trt:GetStreamUriResponse/trt:MediaUri/tt:Uri', namespaces=NS) or ''
        valid_until_connect = root.findtext('.//trt:GetStreamUriResponse/trt:MediaUri/tt:InvalidAfterConnect', namespaces=NS)
        valid_until_reboot = root.findtext('.//trt:GetStreamUriResponse/trt:MediaUri/tt:InvalidAfterReboot', namespaces=NS)
        timeout = root.findtext('.//trt:GetStreamUriResponse/trt:MediaUri/tt:Timeout', namespaces=NS)
        if (not all(isinstance(x,str) for x in (valid_until_connect,valid_until_reboot,timeout))
            or valid_until_connect.strip().lower() not in ('false', '0')
            or valid_until_reboot.strip().lower() not in ('false', '0')
            or timeout.strip() not in ('PT0S', 'PT0.0S')):
            raise EnrollmentError('摄像头取流地址有有效期，暂不支持自动配置')
        return device_url(uri, source_ip, stream=True)


def authenticated_uri(uri, credential_ref):
    user, password = secret(credential_ref)
    parsed = urlsplit(uri)
    return urlunsplit((parsed.scheme, quote(user, safe='') + ':' + quote(password, safe='') + '@' + parsed.netloc,
                       parsed.path, parsed.query, ''))


def _gateway_request(api_origin,path,uri,credential_ref,api_auth,*,transport=None,create=False):
    if not re.fullmatch(r'onvif_[0-9a-f]{32}', path):
        raise EnrollmentError('视频网关通道标识无效')
    parsed = urlsplit(api_origin)
    if parsed.scheme != 'https' and parsed.hostname != '127.0.0.1':
        raise EnrollmentError('配置视频网关需使用本机地址或 HTTPS')
    source = authenticated_uri(uri, credential_ref)
    endpoint = api_origin.rstrip('/') + '/v3/config/paths/'
    try:
        with httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False, transport=transport) as client:
            def read():
                with client.stream('GET',endpoint + 'get/' + path,headers=api_auth) as response:
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    raw=bytearray();started=time.monotonic()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>LIMIT or time.monotonic()-started>25:
                            raise EnrollmentError('视频网关配置回执过大或读取超时')
                    value = json.loads(raw)
                if not isinstance(value,dict) or value.get('source') != source or value.get('sourceOnDemand') is not True:
                    return 'conflict'
                return 'matches'
            existing=read()
            if not create:
                return existing or 'missing'
            if existing=='conflict':
                raise EnrollmentError('视频网关已有同名但不同配置；不会覆盖')
            if existing=='matches':
                return 'existing'
            response = client.post(endpoint + 'add/' + path, headers=api_auth, json={'source':source, 'sourceOnDemand':True})
            response.raise_for_status()
            if read() != 'matches':
                raise EnrollmentError('网关保存结果尚未确认，请按原操作核对')
            return 'created'
    except (httpx.HTTPError, json.JSONDecodeError):
        raise EnrollmentError('视频网关操作结果尚未确认；请按原操作核对，不会覆盖已有通道') from None


def gateway_state(api_origin,path,uri,credential_ref,api_auth,*,transport=None):
    """Read a gateway path without returning the secret-bearing configuration."""
    return _gateway_request(api_origin,path,uri,credential_ref,api_auth,transport=transport)


def configure_gateway(api_origin,path,uri,credential_ref,api_auth,*,transport=None):
    """Create/verify a deterministic on-demand path; never overwrite it."""
    return _gateway_request(api_origin,path,uri,credential_ref,api_auth,transport=transport,create=True)
