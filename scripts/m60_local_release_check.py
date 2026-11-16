"""M60 read-only verification library; no CLI, service or normal-DB entry point.

Callers own the immutable candidate contract and private output storage. Nothing
here creates/drops a database, changes a pointer or starts a worker. Only the
explicit canonical normal-profile loader reads the selected project's .env.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from io import StringIO
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


class Code(str, Enum):
    PATH = "path_rejected"
    MANIFEST = "manifest_rejected"
    SOURCE = "source_changed"
    CONTRACT = "restore_contract_rejected"
    IDENTITY = "restore_identity_mismatch"
    DATABASE = "readonly_database_check_failed"
    UNSUPPORTED = "unsupported_database_objects"
    INVENTORY = "inventory_rejected"
    TABLES = "table_set_changed"
    ROWS = "row_multiset_changed"
    SCHEMA = "schema_changed"
    FILES = "original_files_changed"
    FAILED = "check_failed"


class CheckError(RuntimeError):
    def __init__(self, code: Code):
        self.code = code if isinstance(code, Code) else Code.FAILED
        super().__init__(self.code.value)


def _fail(code):
    raise CheckError(code) from None


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _typed(value):
    """Preserve DB scalar types; default=str would conflate strings and dates."""
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) is int:
        return ["int", str(value)]
    if type(value) is float:
        return ["float", value.hex()]
    if type(value) is str:
        return ["str", value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ["bytes", bytes(value).hex()]
    if isinstance(value, (datetime, date, time)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, UUID):
        return ["uuid", value.hex]
    if isinstance(value, Mapping) and all(type(k) is str for k in value):
        return ["map", [[k, _typed(value[k])] for k in sorted(value)]]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [_typed(v) for v in value]]
    _fail(Code.INVENTORY)


def _digest(value):
    return _sha(json.dumps(_typed(value), ensure_ascii=False, separators=(",", ":")).encode())


def row_multiset(rows: Iterable[Mapping]) -> list[str]:
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            _fail(Code.INVENTORY)
        result.append(_digest(row))
    return sorted(result)


def _parts(value):
    if not isinstance(value, (str, Path)):
        _fail(Code.PATH)
    raw = str(value)
    parsed = PurePosixPath(raw)
    if (not raw or "\\" in raw or "\x00" in raw or parsed.is_absolute()
            or any(p in {"", ".", ".."} for p in raw.split("/"))):
        _fail(Code.PATH)
    return parsed.parts


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _directory_fd(root):
    """Walk every ancestor by descriptor; never resolve away a symlink."""
    path = Path(root).absolute()
    if ".." in path.parts:
        _fail(Code.PATH)
    current = os.open(path.anchor, _DIR)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, _DIR, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def _open_at(root, relative, *, directory=False):
    parts = _parts(relative)
    current = _directory_fd(root)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _DIR, dir_fd=current)
            os.close(current)
            current = next_fd
        flags = _DIR if directory else os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        return os.open(parts[-1], flags, dir_fd=current)
    finally:
        os.close(current)


def _read_fd(fd, *, maximum=None):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or maximum is not None and before.st_size > maximum:
        _fail(Code.PATH)
    chunks = []
    while data := os.read(fd, 1024 * 1024):
        chunks.append(data)
        if maximum is not None and sum(map(len, chunks)) > maximum:
            _fail(Code.PATH)
    after = os.fstat(fd)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, name) != getattr(after, name) for name in fields):
        _fail(Code.SOURCE)
    return b"".join(chunks)


def _read_at(root, relative, *, maximum=None):
    fd = _open_at(root, relative)
    try:
        return _read_fd(fd, maximum=maximum)
    finally:
        os.close(fd)


def _file_metadata(fd):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        _fail(Code.PATH)
    hashed, size = hashlib.sha256(), 0
    while data := os.read(fd, 1024 * 1024):
        hashed.update(data)
        size += len(data)
    after = os.fstat(fd)
    if any(getattr(before, key) != getattr(after, key) for key in
           ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")) or size != before.st_size:
        _fail(Code.SOURCE)
    return {"sha256": hashed.hexdigest(), "bytes": size}


def _walk(fd, prefix=""):
    before = os.fstat(fd)
    with os.scandir(fd) as entries:
        names = sorted(entry.name for entry in entries)
    result = {}
    for name in names:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        key = prefix + name
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _DIR, dir_fd=fd)
            try:
                result.update(_walk(child, key + "/"))
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                result[key] = _file_metadata(child)
            finally:
                os.close(child)
        else:
            _fail(Code.PATH)
    after = os.fstat(fd)
    if (before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
        _fail(Code.SOURCE)
    return result


def file_inventory(root) -> dict:
    try:
        fd = _directory_fd(root)
        try:
            return _walk(fd)
        finally:
            os.close(fd)
    except OSError:
        _fail(Code.PATH)


@dataclass(frozen=True)
class ManifestContract:
    directory: str
    manifest: str
    sha256: str


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail(Code.MANIFEST)
        value[key] = item
    return value


def verify_manifest(project_root, contract: ManifestContract) -> dict:
    """Exact regular-file set, relative paths and bytes against an explicit SHA."""
    try:
        if not isinstance(contract, ManifestContract) or not _is_hash(contract.sha256):
            _fail(Code.MANIFEST)
        _parts(contract.directory)
        _parts(contract.manifest)
        raw = _read_at(project_root, contract.manifest, maximum=4 * 1024 * 1024)
        if _sha(raw) != contract.sha256:
            _fail(Code.MANIFEST)
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or not isinstance(data.get("files"), list) or not data["files"]:
            _fail(Code.MANIFEST)
        expected = {}
        for entry in data["files"]:
            if (not isinstance(entry, dict) or not {"path", "sha256"} <= set(entry)
                    or set(entry) - {"path", "sha256", "bytes"}):
                _fail(Code.MANIFEST)
            name = "/".join(_parts(entry["path"]))
            if name in expected or not _is_hash(entry["sha256"]):
                _fail(Code.MANIFEST)
            if "bytes" in entry and (type(entry["bytes"]) is not int or entry["bytes"] < 0):
                _fail(Code.MANIFEST)
            expected[name] = entry
        fd = _open_at(project_root, contract.directory, directory=True)
        try:
            directory_identity = os.fstat(fd)
            actual = _walk(fd)
        finally:
            os.close(fd)
        manifest_path = PurePosixPath(contract.manifest)
        directory_path = PurePosixPath(contract.directory)
        if manifest_path.is_relative_to(directory_path):
            own_name = str(manifest_path.relative_to(directory_path))
            if own_name in expected:
                _fail(Code.MANIFEST)
            actual.pop(own_name, None)
        if set(actual) != set(expected):
            _fail(Code.SOURCE)
        for name, entry in expected.items():
            if actual[name]["sha256"] != entry["sha256"] or (
                    "bytes" in entry and actual[name]["bytes"] != entry["bytes"]):
                _fail(Code.SOURCE)
        if _read_at(project_root, contract.manifest, maximum=4 * 1024 * 1024) != raw:
            _fail(Code.MANIFEST)
        # A descriptor can still scan an old directory after its pathname has
        # been replaced. Re-reading an identical manifest alone cannot bind the
        # verified bytes to the path that a later process would actually use.
        current_fd = _open_at(project_root, contract.directory, directory=True)
        try:
            current_identity = os.fstat(current_fd)
            if any(getattr(directory_identity, key) != getattr(current_identity, key)
                   for key in ("st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns")):
                _fail(Code.SOURCE)
        finally:
            os.close(current_fd)
        return actual
    except (OSError, ValueError, TypeError, KeyError):
        _fail(Code.MANIFEST)


@dataclass(frozen=True, repr=False)
class OwnedRestore:
    database_url: str = field(repr=False)
    run_id: str = field(repr=False)
    oid: int = field(repr=False)
    marker: str = field(repr=False)

    def __post_init__(self):
        try:
            url = make_url(self.database_url)
            if (not isinstance(self.database_url, str) or not isinstance(self.run_id, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", self.run_id)
                    or type(self.oid) is not int or self.oid <= 0
                    or self.marker != "m60-m59-release-owned:" + self.run_id
                    or url.drivername not in {"postgresql+psycopg", "postgresql+psycopg2", "postgresql"}
                    or (url.host, url.port) != ("127.0.0.1", 55432)
                    or url.database != "ga_test_m60_m59_restore_" + self.run_id
                    or not url.username or not url.password or url.query):
                _fail(Code.CONTRACT)
        except CheckError:
            raise
        except Exception:
            _fail(Code.CONTRACT)

    def __repr__(self):
        return "OwnedRestore(<private>)"


@dataclass(frozen=True, repr=False)
class NormalProfile:
    database_url: str = field(repr=False)
    storage: Path = field(repr=False)
    project_root: Path = field(repr=False)
    config_sha256: str = field(repr=False)

    def __repr__(self):
        return "NormalProfile(<private>)"


def _normal_storage(root, value):
    if not isinstance(value, str) or not value or ".." in Path(value).parts:
        _fail(Code.CONTRACT)
    path = Path(value)
    path = path if path.is_absolute() else root / path
    if not path.is_relative_to(root) or path == root:
        _fail(Code.CONTRACT)
    fd = _directory_fd(path)
    os.close(fd)
    return path


def load_normal_profile(project_root, inherited=None) -> NormalProfile:
    """Canonical local profile only; no arbitrary normal URL or DB connection."""
    try:
        root = Path(project_root).absolute()
        raw = _read_at(root, '.env', maximum=4 * 1024 * 1024)
        config = dotenv_values(stream=StringIO(raw.decode('utf-8')), interpolate=False)
        url = make_url(config['GA_DATABASE_URL'])
        if (url.drivername not in {'postgresql+psycopg', 'postgresql+psycopg2', 'postgresql'}
                or (url.host, url.port, url.database) != ('127.0.0.1', 55432, 'generic_agents')
                or not url.username or not url.password or url.query):
            _fail(Code.CONTRACT)
        storage = _normal_storage(root, config.get('GA_STORAGE_DIR') or '.runtime/evidence')
        env = os.environ if inherited is None else inherited
        if not isinstance(env, Mapping):
            _fail(Code.CONTRACT)
        if any(isinstance(key, str) and key.startswith('PG') and value is not None and value != ''
               for key, value in env.items()):
            _fail(Code.CONTRACT)
        if 'GA_DATABASE_URL' in env and make_url(env['GA_DATABASE_URL']) != url:
            _fail(Code.CONTRACT)
        if 'GA_STORAGE_DIR' in env and _normal_storage(root, env['GA_STORAGE_DIR']) != storage:
            _fail(Code.CONTRACT)
        return NormalProfile(url.render_as_string(hide_password=False), storage, root, _sha(raw))
    except CheckError:
        raise
    except Exception:
        _fail(Code.CONTRACT)


def verify_restore_identity(contract: OwnedRestore, observed: Mapping):
    if not isinstance(contract, OwnedRestore) or not isinstance(observed, Mapping):
        _fail(Code.IDENTITY)
    expected = {"database_name": "ga_test_m60_m59_restore_" + contract.run_id,
                "oid": contract.oid, "marker": contract.marker, "transaction_read_only": "on"}
    if set(observed) != set(expected) or _digest(observed) != _digest(expected):
        _fail(Code.IDENTITY)


def make_inventory(tables: Mapping, schemas: Mapping, files: Mapping) -> dict:
    if (not isinstance(tables, Mapping) or not isinstance(schemas, Mapping)
            or not isinstance(files, Mapping) or set(tables) != set(schemas)):
        _fail(Code.INVENTORY)
    result = {"format": "m60-private-inventory-v1", "tables": {
        name: {"schema": schemas[name], "row_hashes": row_multiset(rows)} for name, rows in tables.items()},
        "files": dict(files)}
    _validate_inventory(result)
    return result


def _validate_inventory(value):
    if (not isinstance(value, Mapping) or set(value) != {"format", "tables", "files"}
            or value["format"] != "m60-private-inventory-v1"
            or not isinstance(value["tables"], Mapping) or not isinstance(value["files"], Mapping)):
        _fail(Code.INVENTORY)
    for name, row in value["tables"].items():
        if (not isinstance(name, str) or not name or not isinstance(row, Mapping)
                or set(row) != {"schema", "row_hashes"} or not isinstance(row["schema"], Mapping)
                or not isinstance(row["row_hashes"], list)
                or any(not _is_hash(x) for x in row["row_hashes"])
                or row["row_hashes"] != sorted(row["row_hashes"])):
            _fail(Code.INVENTORY)
        _typed(row["schema"])
    for name, item in value["files"].items():
        _parts(name)
        if (not isinstance(item, Mapping) or set(item) != {"sha256", "bytes"}
                or not _is_hash(item["sha256"]) or type(item["bytes"]) is not int or item["bytes"] < 0):
            _fail(Code.INVENTORY)


@dataclass(frozen=True)
class CheckSummary:
    old_table_count: int
    old_record_count: int
    file_count: int
    new_empty_table_count: int


def compare_inventories(before, after, *, added_empty_schemas=None) -> CheckSummary:
    _validate_inventory(before)
    _validate_inventory(after)
    added = {} if added_empty_schemas is None else added_empty_schemas
    if not isinstance(added, Mapping) or any(not isinstance(v, Mapping) for v in added.values()):
        _fail(Code.INVENTORY)
    old = before["tables"]
    if set(old) & set(added) or set(after["tables"]) != set(old) | set(added):
        _fail(Code.TABLES)
    for name, item in old.items():
        current = after["tables"][name]
        if _digest(item["schema"]) != _digest(current["schema"]):
            _fail(Code.SCHEMA)
        if item["row_hashes"] != current["row_hashes"]:
            _fail(Code.ROWS)
    for name, schema in added.items():
        if _digest(after["tables"][name]["schema"]) != _digest(schema):
            _fail(Code.SCHEMA)
        if after["tables"][name]["row_hashes"]:
            _fail(Code.ROWS)
    if before["files"] != after["files"]:
        _fail(Code.FILES)
    return CheckSummary(len(old), sum(len(t["row_hashes"]) for t in old.values()), len(before["files"]), len(added))


def public_summary(result=None, *, error=None) -> dict:
    """Never serialize exception text, paths, DB identifiers, URLs or payloads."""
    if error is not None:
        code = error.code.value if isinstance(error, CheckError) and isinstance(error.code, Code) else Code.FAILED.value
        return {"format": "m60-public-check-v1", "verified": False, "code": code}
    keys = ("old_table_count", "old_record_count", "file_count", "new_empty_table_count")
    if type(result) is not CheckSummary:
        return {"format": "m60-public-check-v1", "verified": False, "code": Code.FAILED.value}
    counts = {key: getattr(result, key) for key in keys}
    if any(type(v) is not int or v < 0 for v in counts.values()):
        return {"format": "m60-public-check-v1", "verified": False, "code": Code.FAILED.value}
    return {"format": "m60-public-check-v1", "verified": True, **counts}


def _reject_untracked_catalog_objects(connection):
    """The relational inventory does not describe these objects: fail closed.

    Internal FK triggers and generated array/composite types for ordinary
    tables remain supported. No function bodies, object names or policy text
    leave PostgreSQL; classification yields only one strict boolean.
    """
    unsupported = connection.execute(text("""/* m60_untracked_catalog_guard */
        WITH app_namespaces AS (
            SELECT oid, nspname FROM pg_catalog.pg_namespace
            WHERE nspname <> 'information_schema' AND pg_catalog.left(nspname, 3) <> 'pg_'
        )
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc p
                JOIN app_namespaces n ON n.oid = p.pronamespace
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_class c
                JOIN app_namespaces n ON n.oid = c.relnamespace
                WHERE n.nspname <> 'public' OR c.relkind NOT IN ('r', 'i')
                    OR c.relispartition OR c.relrowsecurity OR c.relforcerowsecurity
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
                JOIN app_namespaces n ON n.oid = c.relnamespace
                WHERE NOT t.tgisinternal
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_policy p
                JOIN pg_catalog.pg_class c ON c.oid = p.polrelid
                JOIN app_namespaces n ON n.oid = c.relnamespace
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_inherits i
                JOIN pg_catalog.pg_class c ON c.oid = i.inhrelid
                JOIN app_namespaces n ON n.oid = c.relnamespace
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_rewrite r
                JOIN pg_catalog.pg_class c ON c.oid = r.ev_class
                JOIN app_namespaces n ON n.oid = c.relnamespace
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_type t
                JOIN app_namespaces n ON n.oid = t.typnamespace
                WHERE t.typtype IN ('d', 'e', 'r', 'm')
                    OR (t.typtype = 'b' AND t.typelem = 0)
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_event_trigger
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_foreign_server
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper
            UNION ALL
            SELECT 1 FROM pg_catalog.pg_default_acl
        ) AS unsupported
    """)).scalar_one()
    if type(unsupported) is not bool:
        _fail(Code.DATABASE)
    if unsupported:
        _fail(Code.UNSUPPORTED)


def _relation_properties(connection, name):
    # Names, grants and collations are semantic, not per-database object OIDs.
    # Preserve owner/privileges on restore and compare them, rather than infer
    # preservation from pg_restore flags or a successful row-count check.
    row = connection.execute(text("""/* m60_relation_properties */
        SELECT pg_catalog.jsonb_build_object(
            'owner', pg_catalog.pg_get_userbyid(c.relowner),
            'persistence', c.relpersistence, 'replica_identity', c.relreplident,
            'tablespace', space.spcname,
            'options', COALESCE((SELECT pg_catalog.jsonb_agg(o.value ORDER BY o.value)
                FROM pg_catalog.unnest(c.reloptions) AS o(value)), '[]'::jsonb),
            'privileges', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'grantor', pg_catalog.pg_get_userbyid(p.grantor),
                'grantee', CASE WHEN p.grantee = 0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(p.grantee) END,
                'privilege', p.privilege_type, 'grantable', p.is_grantable)
                ORDER BY p.grantor, p.grantee, p.privilege_type, p.is_grantable)
                FROM pg_catalog.aclexplode(COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))) p), '[]'::jsonb),
            'columns', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'name', a.attname,
                'collation', CASE WHEN a.attcollation = 0 THEN NULL ELSE pg_catalog.jsonb_build_object(
                    'schema', col_namespace.nspname, 'name', col.collname) END,
                'privileges', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                    'grantor', pg_catalog.pg_get_userbyid(p.grantor),
                    'grantee', CASE WHEN p.grantee = 0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(p.grantee) END,
                    'privilege', p.privilege_type, 'grantable', p.is_grantable)
                    ORDER BY p.grantor, p.grantee, p.privilege_type, p.is_grantable)
                    FROM pg_catalog.aclexplode(a.attacl) p), '[]'::jsonb)) ORDER BY a.attnum)
                FROM pg_catalog.pg_attribute a
                LEFT JOIN pg_catalog.pg_collation col ON col.oid = a.attcollation
                LEFT JOIN pg_catalog.pg_namespace col_namespace ON col_namespace.oid = col.collnamespace
                WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped), '[]'::jsonb)
            ) AS properties
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_database d ON d.datname = pg_catalog.current_database()
        LEFT JOIN pg_catalog.pg_tablespace space ON space.oid = COALESCE(NULLIF(c.reltablespace, 0), d.dattablespace)
        WHERE n.nspname = 'public' AND c.relname = :table_name AND c.relkind = 'r'
    """), {'table_name': name}).mappings().one()
    properties = row['properties']
    if not isinstance(properties, Mapping) or set(properties) != {
            'owner', 'persistence', 'replica_identity', 'tablespace', 'options', 'privileges', 'columns'}:
        _fail(Code.DATABASE)
    _typed(properties)
    return properties


def _collect_inventory(connection, files):
    _reject_untracked_catalog_objects(connection)
    inspector = inspect(connection)
    for schema in inspector.get_schema_names():
        if schema == "information_schema" or schema.startswith("pg_"):
            continue
        if (inspector.get_view_names(schema=schema)
                or inspector.get_materialized_view_names(schema=schema)
                or inspector.get_sequence_names(schema=schema)
                or schema != "public" and inspector.get_table_names(schema=schema)):
            _fail(Code.UNSUPPORTED)
    tables, schemas = {}, {}
    for name in sorted(inspector.get_table_names(schema="public")):
        quoted = connection.dialect.identifier_preparer.quote(name)
        tables[name] = [dict(row) for row in connection.execute(
            text("SELECT * FROM public." + quoted)).mappings()]
        schemas[name] = {"columns": [{k: str(c[k]) if k == "type" else c.get(k)
            for k in ["name", "type", "nullable", "default", "computed", "identity"]}
            for c in inspector.get_columns(name, schema="public")],
            "primary_key": inspector.get_pk_constraint(name, schema="public"),
            "relation_properties": _relation_properties(connection, name),
            **{kind: sorted(getattr(inspector, getter)(name, schema="public"), key=_digest)
               for kind, getter in [("foreign_keys", "get_foreign_keys"),
                   ("unique_constraints", "get_unique_constraints"),
                   ("check_constraints", "get_check_constraints"), ("indexes", "get_indexes")]}}
    return make_inventory(tables, schemas, files)


def snapshot_owned_restore(contract: OwnedRestore, storage) -> dict:
    """Only an explicitly owned local restore. Identity precedes table access."""
    if not isinstance(contract, OwnedRestore):
        _fail(Code.CONTRACT)
    files = file_inventory(storage)
    database = None
    try:
        database = create_engine(contract.database_url, isolation_level="REPEATABLE READ",
            connect_args={"hostaddr": "127.0.0.1", "options": "-c default_transaction_read_only=on", "connect_timeout": 5})
        with database.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                observed = connection.execute(text("SELECT pg_catalog.current_database() AS database_name, oid, "
                    "pg_catalog.shobj_description(oid, 'pg_database') AS marker, "
                    "pg_catalog.current_setting('transaction_read_only') AS transaction_read_only "
                    "FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()" )).mappings().one()
                verify_restore_identity(contract, observed)
                result = _collect_inventory(connection, files)
        if file_inventory(storage) != files:
            _fail(Code.FILES)
        return result
    except CheckError:
        raise
    except Exception:
        _fail(Code.DATABASE)
    finally:
        if database is not None:
            try:
                database.dispose()
            except Exception:
                _fail(Code.DATABASE)


def snapshot_normal(profile: NormalProfile, *, expected_oid: int) -> dict:
    """Read-only canonical app DB at its pre-observed OID; never migrate it."""
    if (not isinstance(profile, NormalProfile) or type(expected_oid) is not int
            or expected_oid <= 0 or load_normal_profile(profile.project_root, os.environ) != profile):
        _fail(Code.CONTRACT)
    files = file_inventory(profile.storage)
    database = None
    try:
        database = create_engine(profile.database_url, isolation_level='REPEATABLE READ',
            connect_args={'hostaddr': '127.0.0.1', 'options': '-c default_transaction_read_only=on',
                          'connect_timeout': 5})
        with database.connect() as connection:
            with connection.begin():
                connection.execute(text('SET TRANSACTION READ ONLY'))
                observed = connection.execute(text("SELECT pg_catalog.current_database() AS database_name, oid, "
                    "pg_catalog.current_setting('transaction_read_only') AS transaction_read_only "
                    "FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()" )).mappings().one()
                expected = {'database_name': 'generic_agents', 'oid': expected_oid,
                            'transaction_read_only': 'on'}
                if _digest(observed) != _digest(expected):
                    _fail(Code.IDENTITY)
                result = _collect_inventory(connection, files)
        if file_inventory(profile.storage) != files:
            _fail(Code.FILES)
        if load_normal_profile(profile.project_root, os.environ) != profile:
            _fail(Code.CONTRACT)
        return result
    except CheckError:
        raise
    except Exception:
        _fail(Code.DATABASE)
    finally:
        if database is not None:
            try:
                database.dispose()
            except Exception:
                _fail(Code.DATABASE)
