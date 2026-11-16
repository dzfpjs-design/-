"""Private, read-only release inventories; no API, model, worker or cloud imports."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
OCR_TABLES = {"material_ocr_runs", "material_ocr_units", "material_ocr_versions",
              "material_ocr_confirmations", "material_ocr_requests"}
RESTORE_PATTERN = r"ga_test_m48_v5_restore_[0-9a-f]{32}"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    hashed = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hashed.update(block)
    return hashed.hexdigest()


def real_tree(path):
    """Never read/follow links, sockets, FIFOs or device nodes."""
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeError("tree root must be a real directory")
    for entry in path.rglob("*"):
        mode = entry.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise RuntimeError("tree contains a link or special entry")


def file_inventory(path):
    real_tree(path)
    return {str(entry.relative_to(path)): sha(entry)
            for entry in sorted(path.rglob("*")) if stat.S_ISREG(entry.lstat().st_mode)}


def private_json(path, body, *, replace=False):
    path = path.absolute()
    if ".." in path.parts or not path.is_relative_to(ROOT / ".runtime/backups"):
        raise RuntimeError("private output outside project backups")
    current = ROOT
    for part in path.relative_to(ROOT).parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError("private output contains a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(body) + b"\n")


def snapshot(database_url, storage):
    url = make_url(database_url)
    if (url.host, url.port) != ("127.0.0.1", 55432) or not (
        url.database == "generic_agents" or re.fullmatch(RESTORE_PATTERN, url.database or "")):
        raise RuntimeError("inventory database outside the fixed local release scope")
    database = create_engine(url)
    result = {"format": "m48-v5-release-inventory-v1", "tables": {}, "row_hashes": {}, "schema": {}}
    try:
        with database.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                inspector = inspect(connection)
                other = [schema for schema in inspector.get_schema_names()
                         if schema not in {"public", "information_schema"} and not schema.startswith("pg_")]
                if any(inspector.get_table_names(schema=schema) for schema in other):
                    raise RuntimeError("unexpected non-public application tables; extend inventory before releasing")
                for table in sorted(inspector.get_table_names()):
                    quoted = connection.dialect.identifier_preparer.quote(table)
                    # Preserve duplicates and every column; never collapse by an optional id.
                    rows = sorted(digest(dict(row._mapping)) for row in connection.execute(text("SELECT * FROM " + quoted)))
                    result["row_hashes"][table] = rows
                    result["tables"][table] = {"count": len(rows), "digest": digest(rows)}
                    result["schema"][table] = {
                        "columns": [{"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"],
                                     "default": c.get("default"), "computed": c.get("computed"), "identity": c.get("identity")}
                                    for c in inspector.get_columns(table)],
                        "primary_key": inspector.get_pk_constraint(table),
                        "foreign_keys": sorted(inspector.get_foreign_keys(table), key=canonical),
                        "unique_constraints": sorted(inspector.get_unique_constraints(table), key=canonical),
                        "check_constraints": sorted(inspector.get_check_constraints(table), key=canonical),
                        "indexes": sorted(inspector.get_indexes(table), key=canonical),
                    }
    finally:
        database.dispose()
    result["files"] = file_inventory(storage)
    result["table_count"] = len(result["tables"])
    result["record_count"] = sum(row["count"] for row in result["tables"].values())
    result["file_count"] = len(result["files"])
    # JSON normalization makes reflected SQLAlchemy metadata comparable after reload.
    return json.loads(canonical(result))


def compare(before, after, *, migration=False):
    original = set(before["tables"])
    expected = original | OCR_TABLES if migration else original
    if migration and original & OCR_TABLES:
        raise RuntimeError("baseline already has candidate OCR tables")
    if set(after["tables"]) != expected:
        raise RuntimeError("unexpected missing or additional tables")
    for table in original:
        if any(before[key][table] != after[key][table] for key in ("tables", "row_hashes", "schema")):
            raise RuntimeError("old rows or schema changed")
    if before["files"] != after["files"]:
        raise RuntimeError("storage file set or bytes changed")
    if migration and any(after["tables"][name]["count"] or after["row_hashes"][name] for name in OCR_TABLES):
        raise RuntimeError("new OCR tables must be empty")
    return {"old_rows_schema_files_exact": True, "old_table_count": len(original),
            "old_record_count": before["record_count"], "file_count": before["file_count"],
            "new_empty_ocr_tables": sorted(OCR_TABLES) if migration else []}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("snapshot")
    take.add_argument("output", type=Path)
    take.add_argument("--storage", type=Path, required=True)
    check = sub.add_parser("compare")
    check.add_argument("before", type=Path)
    check.add_argument("after", type=Path)
    check.add_argument("--migration", action="store_true")
    args = parser.parse_args()
    if args.command == "snapshot":
        if not args.output.absolute().is_relative_to(ROOT / ".runtime/backups"):
            raise RuntimeError("inventories remain in private project backups")
        config = dotenv_values(ROOT / ".env")
        value = snapshot(os.environ.get("GA_DATABASE_URL") or config["GA_DATABASE_URL"], args.storage)
        private_json(args.output, value)
        print(json.dumps({key: value[key] for key in ("table_count", "record_count", "file_count")}))
    else:
        value = compare(json.loads(args.before.read_bytes()), json.loads(args.after.read_bytes()), migration=args.migration)
        print(json.dumps(value))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("release check stopped: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
