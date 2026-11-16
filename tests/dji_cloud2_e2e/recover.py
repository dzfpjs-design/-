"""Restarted test supervisor: clean only resources identified by a durable journal.

It intentionally leaves cleanup_unverified because SIGKILL cannot supply the
guard's FENCED receipt. This is test recovery, not a production cleanup daemon.
"""
import json
import re
import sys

from sqlalchemy.orm import Session

from generic_agents import dji_cloud2_sandbox_journal as journal
from generic_agents.db import engine
from generic_agents.dji_cloud2_sandbox import DockerSandbox, SandboxSpec
from tests.dji_cloud2_e2e.test_sandbox_sdk import remove_owned_container, remove_owned_network


def main():
    data = json.loads(sys.argv[1]); workspace = data['workspace_id']; journal_id = data['journal_id']
    with Session(engine()) as db:
        rows = [row for row in journal.pending(db, workspace) if row['id'] == journal_id]
    if len(rows) != 1:
        raise RuntimeError('journal missing')
    row = rows[0]
    if row['state'] not in ('active', 'cleanup_unverified') or not row.get('network_id'):
        # A timed-out create can succeed later: no listing is proof of absence.
        raise RuntimeError('creation outcome remains unknown')
    suffix = row['guard_name'].removeprefix('ga-dji2-guard-')
    if (not re.fullmatch(r'[0-9a-f]{32}', suffix)
            or row['sdk_name'] != 'ga-dji2-sdk-'+suffix or row['network_name'] != 'ga-dji2-net-'+suffix
            or not re.fullmatch(r'[0-9a-f]{64}', row['network_id'])):
        raise RuntimeError('unsafe resource identity')
    spec = SandboxSpec(workspace_id=workspace, connection_id=row['connection_id'], generation=row['generation'],
        host=data['host'], port=18443, policy_digest='a'*64, guard_image=data['guard_image'],
        sdk_image=data['sdk_image'], allowed_networks=(data['subnet'],))
    # Never issue Docker mutations until the actual local daemon / PID
    # namespace have been verified by the same fail-closed production gate.
    DockerSandbox(spec, lambda _spec: True)._verify_daemon_identity()
    remove_owned_container(row['sdk_name'], 'dji-cloud2-sdk', suffix)
    remove_owned_container(row['guard_name'], 'dji-cloud2-egress', suffix)
    remove_owned_network(row['network_id'], row['network_name'], suffix)
    with Session(engine()) as db:
        state = journal.mark_unverified(db, workspace, journal_id); db.commit()
    if state != 'cleanup_unverified': raise RuntimeError('missing fence acknowledgement')
    print(json.dumps({'state': state}), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception:
        print(json.dumps({'state': 'recovery_failed'}), flush=True)
        sys.exit(1)
