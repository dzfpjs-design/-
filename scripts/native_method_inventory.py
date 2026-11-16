"""Read-only row/file hashes for update and legitimate login preservation QA."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import inspect, text
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.db import engine


def snapshot():
    tables, hashes = {}, {}
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            for table in sorted(inspect(connection).get_table_names()):
                items = {}
                for row in connection.execute(text('SELECT * FROM "' + table + '"')):
                    value = json.loads(json.dumps(dict(row._mapping), default=str))
                    identity = str(value.get('id') or digest(value))
                    items[identity] = digest(value)
                hashes[table] = items
                tables[table] = {'count': len(items), 'digest': digest(sorted(items.values()))}
    root = settings().storage_dir
    files = {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob('*')) if path.is_file()}
    return {'tables': tables, 'row_hashes': hashes, 'files': files,
            'table_count': len(tables), 'record_count': sum(row['count'] for row in tables.values()),
            'file_count': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    result = snapshot()
    summary = {key: result[key] for key in ('table_count', 'record_count', 'file_count')}
    if args.compare:
        previous = json.loads(args.compare.read_text())
        changed = [table for table, rows in previous['row_hashes'].items()
            if any(result['row_hashes'].get(table, {}).get(identity) != value for identity, value in rows.items())]
        removed_files = [name for name, value in previous['files'].items() if result['files'].get(name) != value]
        result['preservation'] = {'all_old_records_identical': not changed,
            'all_old_files_identical': not removed_files, 'changed_tables': changed,
            'changed_old_file_count': len(removed_files),
            'new_rows_by_table': {table: len(set(rows) - set(previous['row_hashes'].get(table, {})))
                for table, rows in result['row_hashes'].items()
                if set(rows) - set(previous['row_hashes'].get(table, {}))}}
        summary['preservation'] = result['preservation']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(summary))
