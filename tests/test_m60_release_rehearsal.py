"""Independent M60 refusal boundaries; run with --noconftest.

Only temporary fixture files and injected database/process doubles are used.
These tests do not prove a normal-database backup, restore or M44 recovery.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/m60_local_release_rehearsal.py"
SPEC = importlib.util.spec_from_file_location("m60_independent_rehearsal", SCRIPT)
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

RUN = "c" * 32
DATABASE = "ga_test_m60_m59_restore_" + RUN
SECRET = "fixture-secret-MUST-STAY-PRIVATE-9j3"
URL = "postgresql+psycopg://fixture:" + SECRET + "@127.0.0.1:55432/generic_agents"


def rejected(action):
    with pytest.raises(m.RehearsalError) as caught:
        action()
    assert SECRET not in str(caught.value)
    assert SECRET not in repr(caught.value)
    return caught.value


@pytest.fixture(autouse=True)
def no_uncontrolled_process_or_database(monkeypatch):
    """A missed injection must fail rather than touch any real runtime."""
    def denied(*args, **kwargs):
        pytest.fail("uncontrolled process or database operation")
    if hasattr(m, "subprocess"):
        monkeypatch.setattr(m.subprocess, "run", denied)
        monkeypatch.setattr(m.subprocess, "Popen", denied)
    if hasattr(m, "create_engine"):
        monkeypatch.setattr(m, "create_engine", denied)
    if hasattr(m, "check"):
        monkeypatch.setattr(m.check, "create_engine", denied)


def test_only_rehearsal_mode_and_exact_uuid_database_are_constructible():
    assert m.restore_name(RUN) == DATABASE
    assert m.validate_rehearsal_mode("rehearsal") is None


@pytest.mark.parametrize("run_id", [
    "", "a" * 31, "a" * 33, "C" * 32, "../" + SECRET, True, 1, None,
    DATABASE, RUN + "?host=remote.example",
])
def test_restore_identity_cannot_be_an_arbitrary_database_or_path(run_id):
    rejected(lambda: m.restore_name(run_id))


@pytest.mark.parametrize("mode", [
    "release", "restore", "migrate", "normal", "setup", "all", "", None,
    "rehearsal " + SECRET,
])
def test_no_release_normal_restore_or_service_modes(mode):
    rejected(lambda: m.validate_rehearsal_mode(mode))


@pytest.mark.parametrize("binary,database,args", [
    ("pg_restore", "generic_agents", ("--exit-on-error", "/tmp/fixture.dump")),
    ("pg_restore", "ga_test_m60_m59_restore_", ("--exit-on-error", "/tmp/fixture.dump")),
    ("pg_restore", DATABASE + "x", ("--exit-on-error", "/tmp/fixture.dump")),
    ("pg_restore", URL, ("--exit-on-error", "/tmp/fixture.dump")),
    ("pg_dump", DATABASE, ("--format=custom", "--file", "/tmp/fixture.dump")),
    ("psql", "generic_agents", ()),
    ("/tmp/pg_restore", DATABASE, ("--exit-on-error", "/tmp/fixture.dump")),
    ("pg_restore", DATABASE, ("--dbname", "generic_agents", "fixture.dump")),
    ("pg_restore", DATABASE, ("--dbname=" + URL, "fixture.dump")),
    ("pg_restore", DATABASE, ("-d", "generic_agents", "fixture.dump")),
    ("pg_restore", DATABASE, ("-dgeneric_agents", "fixture.dump")),
    ("pg_restore", DATABASE, ("--host", "remote.example", "fixture.dump")),
    ("pg_restore", DATABASE, ("--host=remote.example", "fixture.dump")),
    ("pg_restore", DATABASE, ("-hremote.example", "fixture.dump")),
    ("pg_restore", DATABASE, ("--port=5432", "fixture.dump")),
    ("pg_restore", DATABASE, ("--username=" + SECRET, "fixture.dump")),
    ("pg_restore", DATABASE, ("--create", "fixture.dump")),
    ("pg_restore", DATABASE, ("--clean", "fixture.dump")),
    ("pg_restore", DATABASE, ("host=remote.example dbname=generic_agents",)),
])
def test_pg_command_cannot_redirect_restore_or_supply_unreviewed_flags(binary, database, args):
    rejected(lambda: m.pg_command(binary, database, "fixture", *args))


def test_pg_commands_pin_local_cluster_and_keep_password_out_of_argv():
    dump = m.pg_command("pg_dump", "generic_agents", "fixture",
                        "--format=custom", "--file", "/tmp/fixture.dump")
    restore = m.pg_command("pg_restore", DATABASE, "fixture", "--exit-on-error", "/tmp/fixture.dump")
    for command in (dump, restore):
        assert Path(command[0]).is_absolute()
        assert Path(command[0]).name in {"pg_dump", "pg_restore"}
        assert "127.0.0.1" in command and "55432" in command
        assert SECRET not in repr(command) and URL not in repr(command)
    assert DATABASE in restore and "generic_agents" not in restore


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(path):
    raw = path.read_bytes()
    return {"sha256": digest(raw), "bytes": len(raw)}


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def tree(root, relative, files):
    for name, data in files.items():
        write(root, relative + "/" + name, data)
    return {"directory": relative,
            "files": {name: record(root / relative / name) for name in files}}


def source(root, relative, value):
    path = write(root, relative + "/generic_agents/__init__.py", value)
    manifest_relative = relative + "/source-manifest.json"
    manifest = write(root, manifest_relative, json.dumps({"files": [
        {"path": "generic_agents/__init__.py", **record(path)}]}))
    return {"directory": relative, "manifest": manifest_relative,
            "sha256": digest(manifest.read_bytes())}


NEW_SCHEMA = {
    "columns": [{"name": "id", "type": "INTEGER", "nullable": False,
                 "default": None, "computed": None, "identity": None}],
    "primary_key": {"constrained_columns": ["id"], "name": "new_records_pkey"},
    "foreign_keys": [], "unique_constraints": [], "check_constraints": [], "indexes": [],
}


@pytest.fixture
def release_fixture(tmp_path, monkeypatch):
    """Small independent manifest; no real release or private config is read."""
    root = tmp_path.resolve()
    write(root, ".env", "GA_DATABASE_URL=" + URL + "\nGA_STORAGE_DIR=.runtime/evidence\n")
    write(root, ".runtime/evidence/original.bin", b"original\x00\xff")
    old = source(root, ".runtime/releases/m44/project/src", b"old = True\n")
    candidate = source(root, ".runtime/candidate/project/src", b"candidate = True\n")
    frozen = write(root, ".runtime/frozen-source-manifest.json", json.dumps({"files": [{
        "path": "project/src/generic_agents/__init__.py",
        **record(root / candidate["directory"] / "generic_agents/__init__.py")}]}))
    pointer = {"format": 1, "source_dir": old["directory"], "manifest_sha256": old["sha256"]}
    pointer_file = write(root, ".runtime/active-backend-source.json", json.dumps(pointer))
    static = tree(root, "normal-static", {"index.html": b"old static"})
    trees = [tree(root, ".runtime/candidate/" + name, {"item": name})
             for name in ("frontend-source", "frontend-dist", "model-packs")]
    archive = write(root, ".runtime/frozen.zip", b"synthetic archive: read only")
    dependency = write(root, ".runtime/installed-tool", b"synthetic installed tool")
    interpreter = write(root, ".venv/bin/python", b"#!/fixture-only\n")
    interpreter.chmod(0o700)
    binaries = root / "pg17"
    for name in ("pg_dump", "pg_restore"):
        write(root, "pg17/" + name, b"#!/fixture-only\n").chmod(0o700)
    monkeypatch.setattr(m, "PG", binaries)
    monkeypatch.setattr(m, "M59_MANIFEST_SHA", digest(frozen.read_bytes()))
    monkeypatch.setattr(m, "M59_ZIP_SHA", digest(archive.read_bytes()))
    monkeypatch.setattr(m, "NORMAL_POINTER_SHA", digest(pointer_file.read_bytes()))
    for name in ("m60_local_release_rehearsal.py", "m60_local_release_check.py"):
        write(root, "scripts/" + name, b"# private script snapshot fixture\n")
    value = {
        "format": "m60-release-contract-v1",
        "baseline": {
            "pointer": {"path": ".runtime/active-backend-source.json", **record(pointer_file),
                        "content": pointer, "literal_utf8": pointer_file.read_text()},
            "source": old, "static": static,
            "frozen_source_manifest": {"path": old["manifest"], "sha256": old["sha256"]},
            "config": {"path": ".env", **record(root / ".env")},
        },
        "candidate": {"source": candidate,
                      "frozen_source_manifest": {"path": ".runtime/frozen-source-manifest.json",
                                                 "sha256": digest(frozen.read_bytes())},
                      "archive": {"path": ".runtime/frozen.zip", **record(archive)},
                      "trees": trees},
        "expected_new_empty_schemas": {"new_records": copy.deepcopy(NEW_SCHEMA)},
        "provenance": {
            "runtime_interpreters": [{"role": "main", "path": ".venv/bin/python",
                "available": True, "links": [], "final": {"path": str(interpreter), **record(interpreter)}}],
            "pg_tools": [{"name": name, "path": str(binaries / name),
                "version": name + " (PostgreSQL) 17.0 fixture", **record(binaries / name)}
                for name in ("pg_dump", "pg_restore")],
        },
        "dependencies": [{"kind": "file", "path": ".runtime/installed-tool",
                          "available": True, **record(dependency)}],
    }
    contract = write(root, m.CONTRACT_PATH, json.dumps(value, sort_keys=True))
    return SimpleNamespace(root=root, contract=contract, value=value,
                           sha=digest(contract.read_bytes()))


def run_fixture(fixture, **kwargs):
    return m.run_rehearsal(fixture.root, m.CONTRACT_PATH, fixture.sha,
                           inherited=kwargs.pop("inherited", {}), run_id=RUN, **kwargs)


def fixture_state(root):
    return {str(p.relative_to(root)): ("directory" if p.is_dir() else digest(p.read_bytes()))
            for p in root.rglob("*")}


def test_valid_preflight_has_no_runtime_or_write_side_effect(release_fixture):
    f = release_fixture
    before = fixture_state(f.root)
    context = m.preflight(f.root, m.CONTRACT_PATH, f.sha, {})
    assert context.profile.database_url == URL
    assert context.contract["expected_new_empty_schemas"] == {"new_records": NEW_SCHEMA}
    assert fixture_state(f.root) == before
    assert SECRET not in repr(context)


@pytest.mark.parametrize("mutation", [
    "contract", "old_source", "candidate_source", "normal_static", "candidate_static",
    "archive", "installed_dependency", "pointer", "expected_schema", "config",
])
def test_changed_release_or_expected_schema_is_rejected_before_admin_or_write(
        release_fixture, monkeypatch, mutation):
    f = release_fixture
    paths = {
        "contract": m.CONTRACT_PATH,
        "old_source": f.value["baseline"]["source"]["directory"] + "/generic_agents/__init__.py",
        "candidate_source": f.value["candidate"]["source"]["directory"] + "/generic_agents/__init__.py",
        "normal_static": "normal-static/index.html",
        "candidate_static": ".runtime/candidate/frontend-dist/item",
        "archive": ".runtime/frozen.zip", "installed_dependency": ".runtime/installed-tool",
        "pointer": ".runtime/active-backend-source.json",
    }
    if mutation == "expected_schema":
        f.value["expected_new_empty_schemas"]["new_records"]["columns"][0]["nullable"] = True
        f.contract.write_text(json.dumps(f.value, sort_keys=True))
    elif mutation == "config":
        # Still a canonical local URL: the pinned configuration bytes must matter.
        config = f.root / ".env"
        config.write_text(config.read_text() + "GA_CLOUD_DAILY_CALL_LIMIT=999\n")
    else:
        write(f.root, paths[mutation], SECRET)
    before = fixture_state(f.root)
    calls = []
    monkeypatch.setattr(m, "_admin_engine", lambda *a: calls.append("admin"))
    with pytest.raises((m.RehearsalError, m.check.CheckError, ValueError, KeyError)):
        run_fixture(f)
    assert calls == []
    assert fixture_state(f.root) == before
    assert not (f.root / ".runtime/backups").exists()


@pytest.mark.parametrize("inherited", [
    {"GA_DATABASE_URL": URL.replace("generic_agents", "wrong_normal")},
    {"PGHOSTADDR": "remote.example"},
])
def test_inherited_target_override_fails_before_admin_or_backup(release_fixture, monkeypatch, inherited):
    f = release_fixture
    calls = []
    monkeypatch.setattr(m, "_admin_engine", lambda *a: calls.append("admin"))
    before = fixture_state(f.root)
    with pytest.raises((m.RehearsalError, m.check.CheckError)):
        run_fixture(f, inherited=inherited)
    assert calls == []
    assert fixture_state(f.root) == before


def test_pg_and_migrate_environments_cannot_inherit_proxies_keys_or_python_injection(release_fixture):
    f = release_fixture
    inherited = {"HTTP_PROXY": "http://" + SECRET, "https_proxy": "http://" + SECRET,
                 "ALL_PROXY": "http://" + SECRET, "NO_PROXY": SECRET,
                 "PYTHONPATH": SECRET, "PYTHONSTARTUP": SECRET,
                 "DYLD_INSERT_LIBRARIES": SECRET, "LD_PRELOAD": SECRET,
                 "DASHSCOPE_API_KEY": SECRET, "DEEPSEEK_API_KEY": SECRET,
                 "UNRELATED_PRIVATE_KEY": SECRET}
    context = m.preflight(f.root, m.CONTRACT_PATH, f.sha, inherited)
    owned = m.check.OwnedRestore(URL.replace("generic_agents", DATABASE), RUN, 22,
                                 "m60-m59-release-owned:" + RUN)
    env = m.pg_environment(context.profile, inherited)
    assert env["PGHOSTADDR"] == "127.0.0.1"
    assert env["PGPORT"] == "55432" and env["PGDATABASE"] == "generic_agents"
    assert env["PGPASSWORD"] == SECRET
    assert "PGSERVICE" not in env and env["PGPASSFILE"] == "/dev/null"
    assert all(key not in env for key in inherited)
    migrated = m.migrate_environment(context, owned, f.root / ".runtime/backups/owned")
    assert migrated["PGDATABASE"] == DATABASE and migrated["GA_DATABASE_URL"] == owned.database_url
    assert migrated["GA_STORAGE_DIR"].endswith("/backups/owned/storage")
    assert migrated["DASHSCOPE_API_KEY"] == migrated["DEEPSEEK_API_KEY"] == ""
    assert migrated["GA_CLOUD_DAILY_CALL_LIMIT"] == migrated["GA_CLOUD_DAILY_TOKEN_LIMIT"] == "0"
    for key in ("HTTP_PROXY", "https_proxy", "ALL_PROXY", "PYTHONSTARTUP", "LD_PRELOAD",
                "DYLD_INSERT_LIBRARIES", "UNRELATED_PRIVATE_KEY"):
        assert key not in migrated
    assert migrated["PYTHONPATH"] == str(f.root / f.value["candidate"]["source"]["directory"])


class FakeAdmin:
    """Small DB protocol double; unexpected SQL is a failure, not a mock success."""
    def __init__(self, *, sessions=0, wrong_identity_at=None, wrong_identity_field="oid"):
        self.sessions = sessions
        self.wrong_identity_at = wrong_identity_at
        self.wrong_identity_field = wrong_identity_field
        self.events = []
        self.identity_checks = 0

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def scalar(self, query, params=None):
        sql = str(query)
        self.events.append(sql)
        if "pg_stat_activity" in sql:
            return self.sessions
        if "SELECT count(*)" in sql and "pg_database" in sql:
            assert params == {"name": DATABASE}
            return 0
        if "SELECT oid" in sql and "pg_database" in sql:
            if "datname='generic_agents'" in sql:
                return 11
            assert params == {"name": DATABASE}
            return 22
        pytest.fail("unexpected scalar SQL")

    def execute(self, query, params=None):
        sql = str(query)
        self.events.append(sql)
        assert "DROP" not in sql.upper()
        if sql.startswith("SELECT oid,") and "pg_database" in sql:
            assert params == {"name": DATABASE}
            self.identity_checks += 1
            row = {"oid": 22, "marker": "m60-m59-release-owned:" + RUN}
            if self.identity_checks == self.wrong_identity_at:
                row[self.wrong_identity_field] = (23 if self.wrong_identity_field == "oid"
                                                  else "m60-m59-release-owned:" + "d" * 32)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: row,
                                                                    one_or_none=lambda: row))
        assert sql.startswith(("CREATE DATABASE ", "COMMENT ON DATABASE ", "REVOKE "))
        assert '"' + DATABASE + '"' in sql and '"generic_agents"' not in sql
        return None

    def dispose(self):
        self.events.append("dispose")


@pytest.mark.parametrize("pid_name", ["api", "worker", "setup-worker", "media-worker", "mqtt-worker"])
def test_every_normal_pid_record_blocks_before_creating_backup(release_fixture, monkeypatch, pid_name):
    f = release_fixture
    write(f.root, ".runtime/" + pid_name + ".pid", "synthetic stale-or-live record")
    admin = FakeAdmin()
    monkeypatch.setattr(m, "_admin_engine", lambda *args: admin)
    error = rejected(lambda: run_fixture(f))
    assert error.code == m.Code.PID
    assert not (f.root / ".runtime/backups").exists()
    assert admin.events[-1] == "dispose"
    assert not any(sql.startswith("CREATE") for sql in admin.events)


def test_even_one_unrecognized_normal_session_blocks_without_termination(release_fixture, monkeypatch):
    f = release_fixture
    admin = FakeAdmin(sessions=1)
    monkeypatch.setattr(m, "_admin_engine", lambda *args: admin)
    error = rejected(lambda: run_fixture(f))
    assert error.code == m.Code.SESSIONS
    assert not (f.root / ".runtime/backups").exists()
    assert not any("terminate" in sql.lower() or sql.startswith("CREATE") for sql in admin.events)


def test_new_backup_is_private_and_existing_directory_is_never_reused(release_fixture, monkeypatch):
    f = release_fixture
    instant = m.datetime.now(m.timezone.utc)
    monkeypatch.setattr(m, "datetime", SimpleNamespace(now=lambda tz: instant))
    backup = m._new_backup(f.root, RUN)
    assert backup.stat().st_mode & 0o777 == 0o700
    sentinel = write(backup, "keep", SECRET)
    with pytest.raises((m.RehearsalError, OSError)):
        m._new_backup(f.root, RUN)
    assert sentinel.read_text() == SECRET


@pytest.mark.parametrize("location", ["backups_parent", "private_parent", "private_file"])
def test_symlink_output_cannot_touch_outside_files(release_fixture, tmp_path, location):
    f = release_fixture
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    sentinel = write(outside, "sentinel", SECRET)
    if location == "backups_parent":
        (f.root / ".runtime/backups").symlink_to(outside, target_is_directory=True)
        action = lambda: m._new_backup(f.root, RUN)
    else:
        backup = m._new_backup(f.root, RUN)
        if location == "private_parent":
            (backup / "private").symlink_to(outside, target_is_directory=True)
            action = lambda: m.private_json(backup, "private/sentinel", {"overwrite": True})
        else:
            (backup / "sentinel").symlink_to(sentinel)
            action = lambda: m.private_json(backup, "sentinel", {"overwrite": True})
    with pytest.raises((m.RehearsalError, m.check.CheckError, OSError)):
        action()
    assert sentinel.read_text() == SECRET
    assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]


def simulated_runtime(f, monkeypatch, *, mutation=None, failure=None, wrong_identity_at=None,
                      wrong_identity_field="oid"):
    """Run actual orchestration, filesystem copies and comparisons; fake only I/O."""
    admin = FakeAdmin(wrong_identity_at=wrong_identity_at, wrong_identity_field=wrong_identity_field)
    monkeypatch.setattr(m, "_admin_engine", lambda *args: admin)
    calls = []
    old_schema = copy.deepcopy(NEW_SCHEMA)
    old = m.check.make_inventory({"old_records": [{"id": 1}, {"id": 1}]},
        {"old_records": old_schema}, m.check.file_inventory(f.root / ".runtime/evidence"))

    def normal_snapshot(profile, *, expected_oid):
        assert expected_oid == 11 and profile.database_url == URL
        current = copy.deepcopy(old)
        current["files"] = m.check.file_inventory(profile.storage)
        return current

    snapshots = []
    def restored_snapshot(owned, storage):
        assert owned.database_url == URL.replace("generic_agents", DATABASE)
        current = copy.deepcopy(old)
        current["files"] = m.check.file_inventory(storage)
        if snapshots:
            current["tables"]["new_records"] = {"schema": copy.deepcopy(NEW_SCHEMA), "row_hashes": []}
            if mutation == "lost_duplicate":
                current["tables"]["old_records"]["row_hashes"].pop()
            elif mutation == "new_rows":
                current["tables"]["new_records"]["row_hashes"] = m.check.row_multiset([{"id": 1}])
            elif mutation == "new_schema":
                current["tables"]["new_records"]["schema"]["columns"][0]["type"] = "TEXT"
            elif mutation == "extra_table":
                current["tables"]["uncontracted"] = {"schema": copy.deepcopy(NEW_SCHEMA), "row_hashes": []}
        snapshots.append(current)
        return current

    def run(command, **kwargs):
        executable = Path(command[0]).name
        phase = "migrate" if executable == "python" else executable
        calls.append(phase)
        assert kwargs["stdout"] is not None and kwargs["stderr"] == m.subprocess.STDOUT
        kwargs["stdout"].write(("private subprocess output " + SECRET + "\n").encode())
        if phase == "pg_dump":
            dump = Path(command[command.index("--file") + 1])
            assert dump.stat().st_mode & 0o777 == 0o600
            assert dump.stat().st_size == 0
            fd = os.open(dump, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            os.write(fd, b"synthetic private dump")
            os.close(fd)
        else:
            assert kwargs["env"]["PGDATABASE"] == DATABASE
        if phase == "migrate":
            assert command == [str(f.root / ".venv/bin/python"), "-B", "-m", "generic_agents.cli", "migrate"]
            assert kwargs["env"]["GA_DATABASE_URL"] == URL.replace("generic_agents", DATABASE)
            assert kwargs["env"]["DASHSCOPE_API_KEY"] == kwargs["env"]["DEEPSEEK_API_KEY"] == ""
            if mutation == "restored_file":
                (Path(kwargs["env"]["GA_STORAGE_DIR"]) / "original.bin").write_bytes(b"changed")
            elif mutation in {"normal_source", "normal_static", "normal_pointer", "normal_config", "normal_file"}:
                target = {
                    "normal_source": f.value["baseline"]["source"]["directory"] + "/generic_agents/__init__.py",
                    "normal_static": "normal-static/index.html",
                    "normal_pointer": ".runtime/active-backend-source.json",
                    "normal_config": ".env", "normal_file": ".runtime/evidence/original.bin",
                }[mutation]
                with (f.root / target).open("ab") as stream:
                    stream.write(b"changed during isolated migration")
        if failure == phase + "_error":
            raise RuntimeError("injected private driver failure " + SECRET)
        if failure == phase + "_timeout":
            raise m.subprocess.TimeoutExpired(command, 0.01, output=SECRET)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(m.check, "snapshot_normal", normal_snapshot)
    monkeypatch.setattr(m.check, "snapshot_owned_restore", restored_snapshot)
    monkeypatch.setattr(m.subprocess, "run", run)
    monkeypatch.setattr(m, "_queue_inventory", lambda *args: ({"private_fixture": True}, {"job_count": 0}))
    return admin, calls


def only_backup(f):
    backups = list((f.root / ".runtime/backups").iterdir())
    assert len(backups) == 1
    return backups[0]


def test_success_keeps_owned_restore_and_reports_only_rehearsal_scope(release_fixture, monkeypatch, capsys):
    f = release_fixture
    admin, calls = simulated_runtime(f, monkeypatch)
    before = m.check.file_inventory(f.root / "normal-static")
    report = run_fixture(f)
    assert report["verified"] is True and report["restore_retained"] is True
    assert report["worker_takeover_verified"] is False and report["normal_release_changed"] is False
    assert report["old_record_count"] == 2
    assert calls == ["pg_dump", "pg_restore", "migrate"]
    assert admin.identity_checks == 3
    assert not any("DROP" in event.upper() for event in admin.events)
    assert m.check.file_inventory(f.root / "normal-static") == before
    backup = only_backup(f)
    for path in backup.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    private = json.loads((backup / "verification.json").read_text())
    assert private["normal_services_recovery"] == "caller_finally_required_not_verified_by_this_script"
    assert SECRET not in json.dumps(report)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("wrong_at,wrong_field,expected_calls", [
    (2, "oid", ["pg_dump"]), (3, "oid", ["pg_dump", "pg_restore"]),
    (2, "marker", ["pg_dump"]),
])
def test_ownership_is_rechecked_before_restore_and_before_migrate(
        release_fixture, monkeypatch, wrong_at, wrong_field, expected_calls):
    f = release_fixture
    admin, calls = simulated_runtime(f, monkeypatch, wrong_identity_at=wrong_at,
                                    wrong_identity_field=wrong_field)
    error = rejected(lambda: run_fixture(f))
    assert error.code == m.Code.IDENTITY and calls == expected_calls
    assert not any("DROP" in event.upper() for event in admin.events)
    assert json.loads((only_backup(f) / "state.json").read_text())["phase"] == "failure_retained"


@pytest.mark.parametrize("mutation", [
    "lost_duplicate", "new_rows", "new_schema", "extra_table", "restored_file",
    "normal_source", "normal_static", "normal_pointer", "normal_config", "normal_file",
])
def test_real_orchestration_refuses_data_schema_original_or_normal_drift(
        release_fixture, monkeypatch, mutation):
    f = release_fixture
    admin, calls = simulated_runtime(f, monkeypatch, mutation=mutation)
    with pytest.raises((m.RehearsalError, m.check.CheckError, ValueError)):
        run_fixture(f)
    assert calls == ["pg_dump", "pg_restore", "migrate"]
    backup = only_backup(f)
    assert (backup / "database.dump").exists() and (backup / "failure.json").exists()
    assert not (backup / "verification.json").exists()
    assert not any("DROP" in event.upper() for event in admin.events)


@pytest.mark.parametrize("failure,phase", [
    ("pg_restore_error", "restore_load"), ("migrate_timeout", "restore_migrate"),
])
def test_failure_or_timeout_retains_owned_database_progress_and_full_private_trace(
        release_fixture, monkeypatch, capsys, failure, phase):
    f = release_fixture
    admin, calls = simulated_runtime(f, monkeypatch, failure=failure)
    with pytest.raises((m.RehearsalError, RuntimeError)):
        run_fixture(f)
    backup = only_backup(f)
    state = json.loads((backup / "state.json").read_text())
    error = json.loads((backup / "failure.json").read_text())
    connection = json.loads((backup / "private/restore-connection.json").read_text())
    assert state["phase"] == "failure_retained" and state["failed_phase"] == phase
    assert error["phase"] == phase and error["restore_retained"] is True
    assert "Traceback (most recent call last)" in error["traceback"]
    if failure.endswith("timeout"):
        assert "TimeoutExpired" in error["traceback"]
    else:
        assert SECRET in error["traceback"]
    assert connection["ownership_verified"] is True and connection["retained"] is True
    assert (backup / "database.dump").exists()
    assert (backup / "failure.json").stat().st_mode & 0o777 == 0o600
    assert calls == (["pg_dump", "pg_restore"] if phase == "restore_load" else ["pg_dump", "pg_restore", "migrate"])
    assert not any("DROP" in event.upper() for event in admin.events)
    assert capsys.readouterr() == ("", "")


def test_main_never_prints_exception_values_or_private_connection(release_fixture, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("injected driver exception " + URL)
    monkeypatch.setattr(m, "run_rehearsal", fail)
    monkeypatch.setattr(m, "ROOT", release_fixture.root)
    original_umask = os.umask(0o077)
    try:
        exit_code = m.main(["--contract-sha256", "a" * 64])
    finally:
        os.umask(original_umask)
    captured = capsys.readouterr()
    assert exit_code == 1 and captured.err == ""
    assert SECRET not in captured.out and URL not in captured.out
    report = json.loads(captured.out)
    assert report["verified"] is False and report["code"] == m.Code.FAILED.value


@pytest.mark.parametrize("unreviewed_option", [
    ["--purpose", "normal-restore-" + SECRET],
    ["--backup-dir", "../" + SECRET],
    ["--database-url", URL],
])
def test_cli_has_no_arbitrary_database_or_output_directory_escape(
        release_fixture, monkeypatch, capsys, unreviewed_option):
    monkeypatch.setattr(m, "ROOT", release_fixture.root)
    calls = []
    monkeypatch.setattr(m, "run_rehearsal", lambda *a, **kw: calls.append("run"))
    original_umask = os.umask(0o077)
    try:
        code = m.main(["--contract-sha256", "a" * 64, *unreviewed_option])
    finally:
        os.umask(original_umask)
    captured = capsys.readouterr()
    assert code == 1 and calls == [] and captured.err == ""
    assert SECRET not in captured.out and URL not in captured.out
    assert json.loads(captured.out)["verified"] is False


def test_public_summary_rejects_unreviewed_fields_counts_and_diagnostic_values():
    for status, phase, counts, code in [
        (SECRET, "normal_final", {}, SECRET),
        ("failed", SECRET, {}, SECRET),
        ("failed", "normal_final", {"payload": SECRET}, SECRET),
        ("failed", "normal_final", {"old_record_count": SECRET}, SECRET),
        ("failed", "normal_final", {"old_record_count": True}, SECRET),
    ]:
        report = m.public_summary(status, phase, counts, code)
        assert report["verified"] is False
        assert SECRET not in json.dumps(report)


def test_private_copy_closes_target_descriptor_when_source_open_fails(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    source = root / "source"
    target = root / "target"
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    original = write(source, "original", b"fixture source")
    expected = record(original)
    opened = []
    actual_directory_fd = m.check._directory_fd

    def directory_fd(path):
        descriptor = actual_directory_fd(path)
        assert Path(path) == target
        opened.append(descriptor)
        return descriptor

    def source_open(*args, **kwargs):
        raise PermissionError("injected source-open refusal")

    monkeypatch.setattr(m.check, "_directory_fd", directory_fd)
    monkeypatch.setattr(m.check, "_open_at", source_open)
    with pytest.raises(PermissionError, match="source-open refusal"):
        m.private_copy(source, "original", target, "copied", expected=expected)
    assert len(opened) == 1 and list(target.iterdir()) == []
    with pytest.raises(OSError):
        os.fstat(opened[0])
    assert original.read_bytes() == b"fixture source"


def test_admin_dispose_failure_is_retained_and_never_reclassified_as_preflight(
        release_fixture, monkeypatch, capsys):
    f = release_fixture
    admin, calls = simulated_runtime(f, monkeypatch)
    actual_run = m.run_rehearsal

    def fixed_run(project_root, contract_path, contract_sha256, **kwargs):
        return actual_run(project_root, contract_path, contract_sha256,
                          inherited={}, run_id=RUN, **kwargs)

    def failed_dispose():
        admin.events.append("dispose_failure")
        raise RuntimeError("injected admin cleanup failure " + SECRET)

    def forbid_preflight_diagnostic(*args, **kwargs):
        pytest.fail("post-database cleanup failure was reclassified as preflight")

    monkeypatch.setattr(admin, "dispose", failed_dispose)
    monkeypatch.setattr(m, "run_rehearsal", fixed_run)
    monkeypatch.setattr(m, "ROOT", f.root)
    monkeypatch.setattr(m, "_preserve_early_failure", forbid_preflight_diagnostic)
    original_umask = os.umask(0o077)
    try:
        code = m.main(["--contract-sha256", f.sha])
    finally:
        os.umask(original_umask)
    captured = capsys.readouterr()
    assert code == 1 and captured.err == "" and SECRET not in captured.out
    assert json.loads(captured.out)["verified"] is False
    assert calls == ["pg_dump", "pg_restore", "migrate"]
    backup = only_backup(f)
    state = json.loads((backup / "state.json").read_text())
    cleanup = json.loads((backup / "cleanup-failure.json").read_text())
    assert state["phase"] == "failure_retained" and state["failed_phase"] == "admin_dispose"
    assert cleanup["phase"] == "admin_dispose" and cleanup["restore_retained"] is True
    assert cleanup["prior_phase"] == "verified_restore_retained"
    assert "Traceback (most recent call last)" in cleanup["traceback"]
    assert SECRET in cleanup["traceback"]
    assert (backup / "cleanup-failure.json").stat().st_mode & 0o777 == 0o600
    assert not (backup / "failure.json").exists()
    assert json.loads((backup / "private/restore-connection.json").read_text())["retained"] is True
    assert not any("DROP" in event.upper() for event in admin.events)



