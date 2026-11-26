"""No external network: exercise target restrictions and one-attempt fence."""
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import ssl
import subprocess
from unittest.mock import patch

import httpx

from scripts.synthetic_once_proxy import provider_address, tunnel


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-once-proxy-')
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)

    def tearDown(self):
        self.temp.cleanup()

    def _exchange(self, request, expected_auth=None):
        client, proxy = socket.socketpair()
        upstream, service = socket.socketpair()
        errors = []
        def run():
            try:
                tunnel(proxy, self.root, connect_provider=lambda: upstream, expected_auth=expected_auth)
            except Exception as exc:
                errors.append(exc)
            finally:
                upstream.close()
                proxy.close()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        client.sendall(request)
        client.settimeout(2)
        return client, service, thread, errors

    def test_tunnel_has_fixed_target_and_exactly_one_attempt(self):
        c, service, worker, errors = self._exchange(b'CONNECT api.deepseek.com:443 HTTP/1.1\r\nHost: api.deepseek.com\r\n\r\n')
        self.assertIn(b'200 Connection Established', c.recv(1024))
        c.sendall(b'synthetic-encrypted-request')
        self.assertEqual(service.recv(1024), b'synthetic-encrypted-request')
        service.sendall(b'synthetic-encrypted-response')
        self.assertEqual(c.recv(1024), b'synthetic-encrypted-response')
        c.close(); service.close(); worker.join(3)
        self.assertFalse(errors)
        self.assertEqual((self.root/'provider-connect-attempt').stat().st_mode & 0o777, 0o600)
        c2, service2, worker2, errors2 = self._exchange(b'CONNECT api.deepseek.com:443 HTTP/1.1\r\n\r\n')
        self.assertEqual(c2.recv(1), b'')
        worker2.join(3); c2.close(); service2.close()
        self.assertEqual(len(errors2), 1)
        self.assertIsInstance(errors2[0], FileExistsError)

    def test_other_destinations_and_nonconnect_leave_no_attempt(self):
        for request in (b'CONNECT other.example:443 HTTP/1.1\r\n\r\n',
                        b'CONNECT api.deepseek.com:80 HTTP/1.1\r\n\r\n',
                        b'GET https://api.deepseek.com/ HTTP/1.1\r\n\r\n'):
            c, service, worker, errors = self._exchange(request)
            self.assertEqual(c.recv(1), b'')
            worker.join(3); c.close(); service.close()
            self.assertEqual(len(errors), 1)
        self.assertFalse((self.root/'provider-connect-attempt').exists())

    def test_invalid_local_proxy_auth_does_not_consume_attempt(self):
        c, service, worker, errors = self._exchange(
            b'CONNECT api.deepseek.com:443 HTTP/1.1\r\nProxy-Authorization: Basic bad\r\n\r\n',
            expected_auth=b'Basic correct')
        self.assertEqual(c.recv(1), b'')
        worker.join(3); c.close(); service.close()
        self.assertEqual(len(errors), 1)
        self.assertFalse((self.root/'provider-connect-attempt').exists())

    def test_only_public_or_specific_virtual_dns_addresses_are_accepted(self):
        def answer(*values):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (value, 443)) for value in values]
        with patch('socket.getaddrinfo', return_value=answer('198.18.1.3')) as query:
            self.assertEqual(provider_address(), ('198.18.1.3', 443))
            query.assert_called_once_with('api.deepseek.com', 443, socket.AF_INET, socket.SOCK_STREAM)
        for addresses in ((), ('127.0.0.1',), ('10.2.3.4',), ('192.168.1.1',),
                          ('169.254.2.3',), ('224.0.0.1',), ('198.18.1.3','127.0.0.1')):
            with self.subTest(addresses=addresses), patch('socket.getaddrinfo', return_value=answer(*addresses)):
                with self.assertRaises(ValueError): provider_address()

    def test_sender_sandbox_cannot_reach_unapproved_local_port(self):
        allowed, denied = socket.socket(), socket.socket()
        try:
            allowed.bind(('127.0.0.1', 0)); allowed.listen(1)
            denied.bind(('127.0.0.1', 0)); denied.listen(1)
            permitted, other = allowed.getsockname()[1], denied.getsockname()[1]
            profile = (f'(version 1) (allow default) (deny network*) '
                       f'(allow network-outbound (remote ip "localhost:{permitted}"))')
            program = ('import socket,sys; '
                       'a=socket.socket(); a.settimeout(3); good=a.connect_ex(("127.0.0.1",int(sys.argv[1]))); '
                       'b=socket.socket(); b.settimeout(3); bad=b.connect_ex(("127.0.0.1",int(sys.argv[2]))); '
                       'raise SystemExit(0 if good==0 and bad==1 else 2)')
            result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/usr/bin/python3',
                                     '-c', program, str(permitted), str(other)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            self.assertEqual(result.returncode, 0)
        finally:
            allowed.close(); denied.close()

    def test_httpx_proxy_preserves_tls_hostname_and_blocks_repeat(self):
        cert, key = self.root/'cert.pem', self.root/'key.pem'
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=api.deepseek.com',
            '-addext', 'subjectAltName=DNS:api.deepseek.com'], check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        service = socket.socket(); service.bind(('127.0.0.1', 0)); service.listen(1)
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(2)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cert, key)
        received = []
        def serve():
            incoming, _ = service.accept()
            with tls.wrap_socket(incoming, server_side=True) as conn:
                received.append(conn.recv(16384))
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
        def proxy():
            client, _ = listener.accept()
            with client:
                tunnel(client, self.root, expected_auth=b'Basic dGVzdDp0b2tlbg==',
                       connect_provider=lambda: socket.create_connection(service.getsockname(), timeout=3))
        workers = [threading.Thread(target=serve, daemon=True), threading.Thread(target=proxy, daemon=True)]
        for worker in workers: worker.start()
        trust = ssl.create_default_context(cafile=str(cert))
        with httpx.Client(proxy='http://test:token@127.0.0.1:'+str(listener.getsockname()[1]), verify=trust,
                          trust_env=False, timeout=5) as client:
            result = client.post('https://api.deepseek.com/chat/completions', json={'synthetic': True},
                                 headers={'Authorization': 'Bearer synthetic-only'})
        for worker in workers: worker.join(3)
        listener.close(); service.close()
        self.assertEqual(result.status_code, 200)
        self.assertTrue(received and b'Bearer synthetic-only' in received[0])
        self.assertTrue((self.root/'provider-connect-attempt').is_file())


if __name__ == '__main__':
    unittest.main()
