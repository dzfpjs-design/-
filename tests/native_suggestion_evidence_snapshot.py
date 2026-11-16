"""Read-only evidence from the live disposable CUA fixture, never application data."""
import json
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from generic_agents.contracts import digest
from generic_agents.models import CloudCall, Job, Memory, Task

ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / '.runtime/native-suggestion-browser-access.json'
OUT = ROOT / 'docs/evidence/native-suggestion-v11/browser'


def snapshot(label):
    assert label.isascii() and label.replace('-', '').isalnum()
    access = json.loads(ACCESS.read_text())
    base = make_url(dotenv_values(ROOT / '.env')['GA_DATABASE_URL'])
    options = dict(poolclass=NullPool,
        connect_args={'options': '-c default_transaction_read_only=on -c statement_timeout=5000'})
    admin = create_engine(base.set(database='postgres'), **options)
    with admin.connect() as db:
        names = list(db.scalars(text("SELECT datname FROM pg_database WHERE datname LIKE 'ga_test_%'")))
    admin.dispose()
    matches = []
    task_ids = [item['id'] for item in access['tasks'].values()]
    for name in names:
        assert name.startswith('ga_test_')
        candidate = create_engine(base.set(database=name), **options)
        try:
            with candidate.connect() as db:
                exists = db.scalar(text("SELECT to_regclass('public.tasks')"))
                if not exists or not db.scalar(select(Task.id).where(Task.id == task_ids[0])):
                    continue
                records = {}
                for model, predicate in [
                    (Task, Task.id.in_(task_ids)),
                    (Memory, Memory.workspace_id == access['workspace_id']),
                    (Job, Job.workspace_id == access['workspace_id']),
                    (CloudCall, CloudCall.workspace_id == access['workspace_id']),
                ]:
                    records[model.__tablename__] = [dict(row._mapping)
                        for row in db.execute(select(model.__table__).where(predicate))]
                assert len(records['tasks']) == 3
                assert all(row['workspace_id'] == access['workspace_id'] for row in records['tasks'])
                matches.append(records)
        finally:
            candidate.dispose()
    assert len(matches) == 1, 'Require exactly the live disposable task fixture'
    value = json.loads(json.dumps(matches[0], default=str))
    result = {'read_only': True, 'disposable_database_only': True,
              'classification': 'persisted_records_not_product_acceptance',
              'records': value, 'records_hash': digest(value)}
    path = OUT / ('persisted-' + label + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'path': str(path), 'counts': {key: len(rows) for key, rows in value.items()}}))


if __name__ == '__main__':
    import sys
    snapshot(sys.argv[1])
