"""Disposable supervisor for the SIGKILL recovery experiment; synthetic only."""
import json
import os
import select
import sys
import time
from uuid import uuid4

from sqlalchemy.orm import Session

from generic_agents import dji_cloud2_sandbox_journal as journal
from generic_agents.db import engine
from generic_agents.dji_cloud2_connections import is_current
from generic_agents.dji_cloud2_process import parse_result
from generic_agents.dji_cloud2_sandbox import SandboxSpec
from tests.dji_cloud2_e2e.test_sandbox_sdk import TestOnlySubnetSandbox


def send(**message):
    print(json.dumps(message, separators=(',', ':')), flush=True)


def read_frame(proc):
    deadline = time.monotonic() + 25
    data = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], deadline - time.monotonic())
        if not ready: break
        value = os.read(proc.stdout.fileno(), 1)
        if value == b'\n': return bytes(data)
        if not value or len(data) >= 262144: break
        data += value
    raise RuntimeError('invalid SDK response')


def main():
    item = json.loads(sys.argv[1]); workspace = item['workspace_id']
    spec = SandboxSpec(workspace_id=workspace, connection_id=item['connection_id'],
        generation=item['generation'], host=item['host'], port=18443, policy_digest='a'*64,
        guard_image=item['guard_image'], sdk_image=item['sdk_image'], allowed_networks=(item['subnet'],))
    def authorize(_spec):
        with Session(engine()) as db: return is_current(db, workspace, spec.connection_id, spec.generation)
    sandbox = TestOnlySubnetSandbox(spec, authorize, docker='/usr/bin/docker', subnet=item['subnet'], allow_test_fixtures=True)
    with Session(engine()) as db:
        journal_id = journal.begin(db, sandbox); db.commit()
    send(event='intent', journal_id=journal_id, guard=sandbox.guard_name, sdk=sandbox.sdk_name, network=sandbox.network_name)
    sandbox.prepare_network()
    with Session(engine()) as db:
        journal.record_network(db, workspace, journal_id, sandbox); db.commit()
    send(event='network', network_id=sandbox.network_id)
    if sys.stdin.readline().strip() != 'GO':
        return 2
    child = sandbox.start()
    sandbox.preflight_sdk()
    journal.send_init(workspace, journal_id, sandbox)
    sandbox.read_sdk_ready()
    with Session(engine()) as db:
        journal.mark_active(db, workspace, journal_id, sandbox); db.commit()
    rid = str(uuid4()); child.stdin.write(('LIST\t'+rid+'\n').encode()); child.stdin.flush()
    parse_result(read_frame(child), rid)
    send(event='active')
    # The test intentionally SIGKILLs this process. Never print secrets.
    while True: time.sleep(1)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        send(event='error')
        sys.exit(1)
