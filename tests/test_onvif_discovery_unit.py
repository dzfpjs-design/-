import unittest
from unittest.mock import MagicMock, patch
from generic_agents.onvif_discovery import DESTINATION, MAX_PACKET, discover, parse_response

MSG='urn:uuid:11111111-2222-3333-4444-555555555555'
DEVICE='11111111-1111-1111-1111-111111111111'

def reply(*, relates=MSG, xaddr='http://192.168.1.41:80/onvif/device_service', action='ProbeMatches', name='North%20Camera'):
    return f'''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><s:Header><a:RelatesTo>{relates}</a:RelatesTo><a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/{action}</a:Action></s:Header><s:Body><d:ProbeMatches><d:ProbeMatch><a:EndpointReference><a:Address>urn:uuid:{DEVICE}</a:Address></a:EndpointReference><d:Scopes>onvif://www.onvif.org/name/{name}</d:Scopes><d:XAddrs>{xaddr}</d:XAddrs></d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>'''.encode()

class OnvifDiscoveryTests(unittest.TestCase):
    def test_matching_response_is_display_only(self):
        got=parse_response(reply(),'192.168.1.41',MSG)
        self.assertEqual(len(got),1);self.assertEqual(got[0]['name'],'North Camera');self.assertEqual(got[0]['source_ip'],'192.168.1.41')
        self.assertNotIn('authenticated',got[0]);self.assertNotIn('rtsp',got[0])
    def test_rejects_unrelated_or_unsafe_addresses(self):
        self.assertEqual(parse_response(reply(relates='urn:uuid:wrong'),'192.168.1.41',MSG),[])
        self.assertEqual(parse_response(reply(action='Hello'),'192.168.1.41',MSG),[])
        for url in ('http://127.0.0.1:80/onvif','http://192.168.1.42/onvif','http://user:pass@192.168.1.41/onvif','http://192.168.1.41/onvif?secret=x','http://evil.example/onvif'):
            self.assertEqual(parse_response(reply(xaddr=url),'192.168.1.41',MSG),[])
        self.assertEqual(parse_response(reply(),'8.8.8.8',MSG),[])
    def test_untrusted_xml_and_large_datagrams_are_ignored(self):
        self.assertEqual(parse_response(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>','192.168.1.41',MSG),[])
        self.assertEqual(parse_response(b'x'*(MAX_PACKET+1),'192.168.1.41',MSG),[])
    def test_both_probe_types_are_correlated_and_same_device_is_deduplicated(self):
        import re
        mock=MagicMock();sock=mock.__enter__.return_value
        replies=[]
        def response(_n):
            request=sock.sendto.call_args_list[len(replies)].args[0].decode()
            current=re.search(r'<a:MessageID>(.*?)</a:MessageID>',request).group(1)
            replies.append(current)
            return reply(relates=current),('192.168.1.41',3702)
        sock.recvfrom.side_effect=response
        with patch('generic_agents.onvif_discovery.socket.socket',return_value=mock), patch('generic_agents.onvif_discovery.select.select',side_effect=[([sock],[],[]),([sock],[],[]),([],[],[])]):
            result=discover()
        self.assertNotEqual(replies[0],replies[1])
        self.assertEqual(len(result['devices']),1)

    def test_scan_sends_both_onvif_probes_only_to_local_multicast(self):
        mock=MagicMock();sock=mock.__enter__.return_value
        def response(_n):
            sent=sock.sendto.call_args_list[0].args[0].decode(); import re
            current=re.search(r'<a:MessageID>(.*?)</a:MessageID>',sent).group(1)
            return reply(relates=current),('192.168.1.41',3702)
        sock.recvfrom.side_effect=response
        with patch('generic_agents.onvif_discovery.socket.socket',return_value=mock),patch('generic_agents.onvif_discovery.select.select',side_effect=[([sock],[],[]),([],[],[])]):
            result=discover()
        self.assertEqual(sock.sendto.call_count,2)
        calls=sock.sendto.call_args_list
        self.assertTrue(all(call.args[1] == DESTINATION for call in calls))
        self.assertIn(b'dn:NetworkVideoTransmitter',calls[0].args[0]);self.assertIn(b'tds:Device',calls[1].args[0])
        self.assertEqual(len(result['devices']),1);self.assertFalse(result['packets_limited'])
        self.assertEqual(len(result['devices'][0]['candidate_id']),32)
