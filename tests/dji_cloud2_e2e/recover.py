"""Invoke fail-closed recovery in a fresh test supervisor process."""
import json
import sys
from generic_agents import dji_cloud2_sandbox_journal as journal


def main():
    data = json.loads(sys.argv[1])
    result = journal.recover_owned(data['workspace_id'], data['journal_id'])
    if result != {'state': 'cleanup_unverified', 'outcome': 'resources_absent_unverified'}:
        raise RuntimeError('recovery remains unresolved')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'state': 'recovery_failed'}), flush=True)
        sys.exit(1)
