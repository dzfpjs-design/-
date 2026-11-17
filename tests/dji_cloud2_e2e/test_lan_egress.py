"""Dedicated VM only: guarded SDK traffic to synthetic cross-subnet LAN."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents import dji_cloud2_sandbox_journal as journal
from generic_agents.dji_cloud2_connections import is_current
from generic_agents.dji_cloud2_process import parse_result
from generic_agents.dji_cloud2_sandbox import SandboxSpec
from tests.dji_cloud2_e2e.test_sandbox_sdk import (
    DOCKER, HERE, TestOnlySubnetSandbox, assets, choose_subnet, docker,
    make_connection, read_line, remove_owned_container,
)
from tests.test_managed_mqtt_grants import vault

pytestmark = pytest.mark.skipif(
    os.getenv('GA_DJI2_E2E_ENABLE') != '1' or os.getenv('GA_DJI2_LAN_E2E_ENABLE') != '1',
    reason='explicit dedicated Linux VM LAN E2E opt-in required',
)


def run_ip(*args):
    return subprocess.run(['/usr/bin/sudo', '-n', '/usr/sbin/ip', *args], capture_output=True,
                          timeout=10, check=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})


def test_guarded_sdk_reaches_only_selected_cross_subnet_endpoint(client, account, vault, monkeypatch, assets, tmp_path):
    machine = Path('/etc/machine-id').read_text().strip()
    assert machine and os.getenv('GA_DJI2_LAN_VM_MACHINE_ID') == machine, 'dedicated VM machine ID must be explicitly pinned'
    guard, sdk, _control, fixture, subnet = assets
    host, target = str(subnet.network_address + 10), str(subnet.network_address + 11)
    lease = uuid4().hex
    interface, alias = 'gdj' + lease[:8], 'ga-dji2-lan-test-' + lease
    assert not Path('/sys/class/net', interface).exists()
    fixture_name, component = 'ga-dji2-lan-cert-' + lease, 'dji-cloud2-lan-cert'
    processes, sandbox, journal_id = [], None, None
    workspace, interface_created = account[2], False
    try:
        run_ip('link', 'add', interface, 'type', 'dummy')
        interface_created = True
        run_ip('link', 'set', 'dev', interface, 'alias', alias)
        run_ip('addr', 'add', host + '/24', 'dev', interface)
        run_ip('addr', 'add', target + '/24', 'dev', interface)
        run_ip('link', 'set', 'dev', interface, 'up')
        bridge_subnet = choose_subnet()
        assert not bridge_subnet.overlaps(subnet)
        docker(['create', '--name', fixture_name, '--label', 'org.generic-agents.component='+component,
                '--label', 'org.generic-agents.lease='+lease, fixture])
        for name in ('cert.pem', 'key.pem'):
            docker(['cp', fixture_name+':/opt/fixture/'+name, str(tmp_path / name)])
        service = (HERE / 'fixture_server.py').read_text()
        service = service.replace("'/opt/fixture/cert.pem'", repr(str(tmp_path / 'cert.pem')))
        service = service.replace("'/opt/fixture/key.pem'", repr(str(tmp_path / 'key.pem')))
        service = service.replace("('0.0.0.0', 18443)", "(os.environ['FIXTURE_BIND'], int(os.environ['FIXTURE_PORT']))")
        script = tmp_path / 'fixture.py'
        script.write_text(service)
        for address, port, role in ((host, 18443, 'source'), (target, 18443, 'target'), (host, 18444, 'target')):
            proc = subprocess.Popen([sys.executable, '-u', str(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'FIXTURE_BIND': address, 'FIXTURE_PORT': str(port),
                     'FIXTURE_ROLE': role, 'FIXTURE_MODE': 'normal'}, bufsize=0)
            processes.append(proc)
            assert json.loads(read_line(proc))['kind'] == 'ready'
            with socket.create_connection((address, port), timeout=2):
                pass
        record = make_connection(client, host, subnet, monkeypatch)
        spec = SandboxSpec(workspace_id=workspace, connection_id=record['id'], generation=record['version'],
            host=host, port=18443, policy_digest='a'*64, guard_image=guard, sdk_image=sdk,
            allowed_networks=(str(subnet),), network_mode='lan_test')
        def authorize(_):
            with Session(engine()) as db:
                return is_current(db, workspace, record['id'], record['version'])
        sandbox = TestOnlySubnetSandbox(spec, authorize, docker=DOCKER, subnet=bridge_subnet, experimental_lan=True)
        with Session(engine()) as db:
            journal_id = journal.begin(db, sandbox); db.commit()
        sandbox.prepare_network()
        info = json.loads(docker(['network', 'inspect', sandbox.network_id]).stdout)[0]
        gateway = info['IPAM']['Config'][0]['Gateway']
        proc = subprocess.Popen([sys.executable, '-u', str(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'FIXTURE_BIND': gateway, 'FIXTURE_PORT': '18445',
                 'FIXTURE_ROLE': 'target', 'FIXTURE_MODE': 'normal'}, bufsize=0)
        processes.append(proc)
        assert json.loads(read_line(proc))['kind'] == 'ready'
        with socket.create_connection((gateway, 18445), timeout=2):
            pass
        with Session(engine()) as db:
            journal.record_network(db, workspace, journal_id, sandbox); db.commit()
        sdk_process = sandbox.start()
        sandbox.preflight_sdk()
        journal.send_init(workspace, journal_id, sandbox)
        sandbox.read_sdk_ready()
        with Session(engine()) as db:
            journal.mark_active(db, workspace, journal_id, sandbox); db.commit()
        rid = str(uuid4())
        sdk_process.stdin.write(('LIST\t'+rid+'\n').encode()); sdk_process.stdin.flush()
        assert parse_result(read_line(sdk_process), rid) == []
        # Known listening services make both forbidden-destination checks meaningful.
        probe = """import json,socket,sys
rows=[]
for host,port in ((sys.argv[1],18443),(sys.argv[1],18444),(sys.argv[2],18443),(sys.argv[3],18445),('203.0.113.1',80),('169.254.169.254',80)):
 s=socket.socket();s.settimeout(1)
 try: s.connect((host,port)); rows.append(True)
 except OSError: rows.append(False)
 finally: s.close()
print(json.dumps(rows))
"""
        result = docker(['run', '--rm', '--network', 'container:'+sandbox.guard_name, '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--read-only', '--user', '65534:65534', '--log-driver', 'none',
            '--entrypoint', 'python3', fixture, '-c', probe, host, target, gateway], timeout=15)
        assert json.loads(result.stdout) == [True, False, False, False, False, False]
    finally:
        try:
            if sandbox is not None:
                outcome = sandbox.stop()
                if journal_id:
                    with Session(engine()) as db:
                        journal.mark_stop(db, workspace, journal_id, sandbox, outcome); db.commit()
                assert outcome.network_removed and outcome.containers_stopped, 'sandbox cleanup not verified'
        finally:
            for proc in processes:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
                if proc.stdout: proc.stdout.close()
            try:
                remove_owned_container(fixture_name, component, lease)
            finally:
                if interface_created:
                    saved_alias = Path('/sys/class/net', interface, 'ifalias')
                    if saved_alias.exists() and saved_alias.read_text().strip() == alias:
                        run_ip('link', 'delete', interface)
                    else:
                        raise AssertionError('refusing to remove interface with unexpected identity')
