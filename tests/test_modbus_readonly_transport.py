import json
import socket
import struct
import threading

import pytest

from generic_agents.modbus_readonly_transport import ModbusError, read_registers, grants, decode_registers


def device(reply):
    server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)
    port = server.getsockname()[1]
    def run():
        with server:
            conn, _ = server.accept()
            with conn:
                request = conn.recv(12)
                if request:
                    data = reply(request)
                    for byte in data:
                        conn.sendall(bytes([byte]))
    t = threading.Thread(target=run, daemon=True); t.start()
    return port, t


def test_reads_fragmented_real_modbus_response():
    port, t = device(lambda req: req[:2] + b'\0\0\0\x07' + req[6:7] + b'\x03\x04\x01\x02\x03\x04')
    assert read_registers('127.0.0.1', port, 1, 3, 10, 2) == [258, 772]
    t.join(2)


def test_rejects_mismatched_transaction_and_exceptions():
    port, t = device(lambda req: b'\xff\xff\0\0\0\x05' + req[6:7] + b'\x03\x02\0\x01')
    with pytest.raises(ModbusError): read_registers('127.0.0.1', port, 1, 3, 10, 1)
    t.join(2)
    port, t = device(lambda req: req[:2] + b'\0\0\0\x03' + req[6:7] + b'\x83\x02')
    with pytest.raises(ModbusError): read_registers('127.0.0.1', port, 1, 3, 10, 1)
    t.join(2)


def test_numeric_interpretation_and_order():
    assert decode_registers([0xfffe], 'int16', 'big', 0.5, 1) == 0
    assert decode_registers([0, 0x3f80], 'float32', 'little', 1, 0) == 1
    with pytest.raises(ModbusError): decode_registers([0x7fc0, 0], 'float32', 'big', 1, 0)


def test_policy_only_scoped_explicit_private_addresses(monkeypatch):
    entry = dict(workspace='one', host='192.168.1.20', port=502, unit=1, functions=[3,4], start=0, end=100)
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS', json.dumps([entry]))
    assert len(grants('one')) == 1
    assert grants('two') == []
    entry['host']='8.8.8.8'; monkeypatch.setenv('GA_MODBUS_READ_GRANTS', json.dumps([entry]))
    with pytest.raises(ModbusError): grants('one')
    entry['host']='127.0.0.1'; monkeypatch.setenv('GA_MODBUS_READ_GRANTS', json.dumps([entry]))
    with pytest.raises(ModbusError): grants('one')
