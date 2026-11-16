"""Read-only normal runtime inventory; artifacts contain hashes, never row data."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import inspect, text
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine


def inventory():
    database = engine()
    tables = {}
    with database.connect() as connection:
        for name in sorted(inspect(database).get_table_names()):
            rows = [digest(json.loads(json.dumps(dict(row._mapping), default=str)))
                    for row in connection.execute(text('SELECT * FROM "'+name+'"'))]
            tables[name] = {'count': len(rows), 'digest': digest(sorted(rows))}
    root = settings().storage_dir
    files = {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob('*')) if path.is_file()}
    return {'tables': tables, 'files': files, 'table_count': len(tables),
            'record_count': sum(row['count'] for row in tables.values()), 'file_count': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('output', type=Path)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    result = inventory(); output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({key: result[key] for key in ('table_count', 'record_count', 'file_count')}))
