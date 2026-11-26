"""One CONNECT tunnel for a dedicated synthetic DeepSeek integration test.

The listening socket is local-only. The caller selects no host, port, DNS
server or proxy chain: this process connects only to api.deepseek.com:443.
It carries opaque TLS bytes and has no provider key or access to test records.
This is an application-level restriction, not an OS fixed-IP firewall.
"""
import ipaddress
import os
import select
import socket
import stat
import time
from pathlib import Path

HOST = 'api.deepseek.com'
PORT = 443
MAX_HEADERS = 8192
MAX_BYTES = 64 * 1024 * 1024
TOTAL_SECONDS = 1800


def claim(root):
    if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
        raise ValueError('private run directory required')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('run directory must be private')
        marker = os.open('provider-connect-attempt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
        try:
            os.write(marker, b'one connection attempted; result may be unknown\n')
            os.fsync(marker)
        finally:
            os.close(marker)
        os.fsync(directory)
    finally:
        os.close(directory)


def _request(client, expected_auth=None):
    client.settimeout(10)
    received = bytearray()
    while b'\r\n\r\n' not in received:
        chunk = client.recv(min(1024, MAX_HEADERS + 1 - len(received)))
        if not chunk:
            raise ValueError('incomplete CONNECT')
        received.extend(chunk)
        if len(received) > MAX_HEADERS:
            raise ValueError('CONNECT too large')
    header, trailing = bytes(received).split(b'\r\n\r\n', 1)
    if trailing or header.split(b'\r\n', 1)[0] != b'CONNECT api.deepseek.com:443 HTTP/1.1':
        raise ValueError('only the fixed provider CONNECT is allowed')
    if expected_auth is not None:
        fields = [line.partition(b':') for line in header.split(b'\r\n')[1:]]
        supplied = [value.strip() for name, separator, value in fields
                    if separator and name.strip().lower() == b'proxy-authorization']
        if supplied != [expected_auth]:
            raise ValueError('local proxy authentication failed')


def provider_address():
    # Restrict to the fixed public IPv4 endpoint; TLS certificate/hostname
    # verification remains the responsibility of the client end-to-end.
    values = {entry[4][0] for entry in socket.getaddrinfo(HOST, PORT, socket.AF_INET, socket.SOCK_STREAM)}
    if not values or any(not ipaddress.ip_address(value).is_global for value in values):
        raise ValueError('provider DNS contained a non-public IPv4 address')
    return sorted(values)[0], PORT


def tunnel(client, root, *, connect_provider=None, expected_auth=None):
    """Accept at most one original connection; caller must close listener."""
    _request(client, expected_auth)
    # Record before DNS or socket connection, including any unknown failures.
    claim(root)
    upstream = (connect_provider or (lambda: socket.create_connection(provider_address(), timeout=15)))()
    try:
        client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        client.setblocking(False)
        upstream.setblocking(False)
        deadline = time.monotonic() + TOTAL_SECONDS
        total = 0
        while time.monotonic() < deadline:
            ready, _, _ = select.select([client, upstream], [], [], min(5, max(0, deadline-time.monotonic())))
            for current in ready:
                try:
                    data = current.recv(65536)
                except ConnectionResetError:
                    return
                if not data:
                    return
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError('tunnel byte limit exceeded')
                target = upstream if current is client else client
                target.setblocking(True)
                target.settimeout(30)
                target.sendall(data)
                target.setblocking(False)
        raise TimeoutError('tunnel deadline')
    finally:
        upstream.close()
