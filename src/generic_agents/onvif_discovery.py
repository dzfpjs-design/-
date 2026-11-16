"""Explicit, bounded ONVIF WS-Discovery. Discovery results are untrusted leads.

This never contacts advertised URLs, authenticates a camera, configures a
stream, or treats a response as device identity proof.
"""
import ipaddress
import re
import select
import socket
import time
import uuid
from hashlib import sha256
from urllib.parse import unquote, urlsplit
from defusedxml import ElementTree

DESTINATION = ('239.255.255.250', 3702)
MAX_PACKET = 32768
MAX_DEVICES = 100
NS = {'s': 'http://www.w3.org/2003/05/soap-envelope',
      'a': 'http://schemas.xmlsoap.org/ws/2004/08/addressing',
      'd': 'http://schemas.xmlsoap.org/ws/2005/04/discovery'}


def probe(message_id, device_type):
    if device_type not in ('dn:NetworkVideoTransmitter', 'tds:Device'):
        raise ValueError('Unsupported discovery type')
    return (f'''<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="{NS['s']}" xmlns:a="{NS['a']}" xmlns:d="{NS['d']}" xmlns:dn="http://www.onvif.org/ver10/network/wsdl" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Header><a:MessageID>{message_id}</a:MessageID><a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To><a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action></s:Header><s:Body><d:Probe><d:Types>{device_type}</d:Types></d:Probe></s:Body></s:Envelope>''').encode('utf-8')


def parse_response(raw, source, message_id):
    """Return display-only entries when a response matches this probe and peer.

    No advertised address is ever fetched; names and identifiers are untrusted.
    """
    if len(raw) > MAX_PACKET:
        return []
    try:
        addr = ipaddress.ip_address(source)
        if addr.version != 4 or not addr.is_private or addr.is_loopback or addr.is_unspecified or addr.is_multicast:
            return []
        root = ElementTree.fromstring(raw)
        if root.tag != '{%s}Envelope' % NS['s']:
            return []
        if root.findtext('./s:Header/a:RelatesTo', namespaces=NS) not in ({message_id} if isinstance(message_id, str) else message_id):
            return []
        action = root.findtext('./s:Header/a:Action', namespaces=NS)
        if action != NS['d'] + '/ProbeMatches':
            return []
        matches = root.findall('./s:Body/d:ProbeMatches/d:ProbeMatch', NS)
        if len(matches) > MAX_DEVICES:
            return []
        result = []
        for match in matches:
            epr = (match.findtext('./a:EndpointReference/a:Address', namespaces=NS) or '').strip()
            if len(epr) > 250 or not epr.startswith('urn:uuid:'):
                continue
            try:
                device_uuid = str(uuid.UUID(epr[9:]))
            except ValueError:
                continue
            # Only use paths advertised under the packet's source IP. Do not
            # keep or display tokens, credentials, query strings or fragments.
            endpoints = []
            for raw_url in (match.findtext('./d:XAddrs', namespaces=NS) or '').split()[:8]:
                try:
                    u = urlsplit(raw_url)
                    if u.scheme not in ('http', 'https') or u.username or u.password or u.query or u.fragment or u.hostname != str(addr) or (u.port is not None and not 1 <= u.port <= 65535):
                        continue
                    if len(u.path) > 300 or not re.fullmatch(r'/[A-Za-z0-9_./~-]*', u.path):
                        continue
                    endpoints.append(raw_url)
                except ValueError:
                    continue
            if not endpoints:
                continue
            name = ''
            for scope in (match.findtext('./d:Scopes', namespaces=NS) or '').split()[:30]:
                prefix = 'onvif://www.onvif.org/name/'
                if scope.startswith(prefix):
                    name = unquote(scope[len(prefix):])[:100]
                    name = ''.join(ch for ch in name if ch.isprintable())
                    break
            result.append({'device_id':device_uuid, 'source_ip':str(addr), 'name':name or '未命名设备', 'service_address':endpoints[0]})
        return result
    except (ValueError, TypeError, ElementTree.ParseError):
        return []
    except Exception as exc:
        # DefusedXML rejects dangerous declarations through its own exception
        # classes; these packets are untrusted and must not abort discovery.
        from defusedxml.common import DefusedXmlException
        if isinstance(exc, DefusedXmlException):
            return []
        raise


def discover(timeout=3):
    probes = [('urn:uuid:' + str(uuid.uuid4()), kind) for kind in ('dn:NetworkVideoTransmitter', 'tds:Device')]
    message_ids = {message_id for message_id, _ in probes}
    seen = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.bind(('', 0))
        for message_id, kind in probes:
            sock.sendto(probe(message_id, kind), DESTINATION)
        deadline = time.monotonic() + timeout
        packets = 0
        while time.monotonic() < deadline and packets < 500 and len(seen) < MAX_DEVICES:
            ready, _, _ = select.select([sock], [], [], max(0, deadline-time.monotonic()))
            if not ready:
                break
            raw, peer = sock.recvfrom(MAX_PACKET+1)
            packets += 1
            for item in parse_response(raw, peer[0], message_ids):
                # A duplicated device identity with a different IP is kept as
                # a distinct unverified candidate; no silent merge of devices.
                key = sha256((item['device_id']+'\0'+item['source_ip']).encode()).hexdigest()[:32]
                seen[key] = {'candidate_id': key, **item}
    return {'devices':list(seen.values()), 'packets_limited':packets >= 500 or len(seen) >= MAX_DEVICES}
