"""M61 contract tests: only fake connections and temporary regular files.

Run with --noconftest. No profile, binary, socket or PostgreSQL is executed.
These tests do not establish real macOS sandbox enforcement or a CLI journey.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/m61_worker_isolation.py"
SPEC = importlib.util.spec_from_file_location("m61_independent_isolation", SCRIPT)
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
RUN = "c" * 32
SECRET = "fixture-MUST-STAY-PRIVATE-4g7"


def rejected(action, code=None):
    with pytest.raises(m.IsolationError) as caught:
        action()
    assert SECRET not in str(caught.value)
    assert SECRET not in repr(caught.value)
    if code is not None:
        assert caught.value.code == code


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("uncontrolled database, subprocess or socket operation")
    monkeypatch.setattr(m, "create_engine", denied)
    monkeypatch.setattr(m.check, "create_engine", denied)
    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    for key in list(os.environ):
        if (key.upper().startswith("PG") or key.upper().endswith("_PROXY")
                or key.upper() in {"DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"}):
            monkeypatch.delenv(key)


def private_file(path, contents):
    path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
    path.chmod(0o600)
    return path


@pytest.fixture
def owned(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "project"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(m, "ROOT", root)
    run = root / ".runtime" / ("m61-run-" + RUN)
    run.mkdir(mode=0o700, parents=True)
    for name in ("control", "cluster", "storage", "tmp", "logs", "pg-tmp", "pg-logs"):
        (run / name).mkdir(mode=0o700)
    value = m.OwnedCluster(RUN, run, 59431, "7412345678901234567",
        "ga_m61_" + RUN, 12345, "m61-workers-owned:" + RUN,
        "postgresql+psycopg://m61_app_" + RUN[:12] + ":" + SECRET
        + "@127.0.0.1:59431/ga_m61_" + RUN)
    private_file(run / "cluster/PG_VERSION", "17\n")
    private_file(run / "control/cluster-identity.json", json.dumps(m._control_record(value)))
    private_file(run / "storage/original.bin", b"original\x00\xff")
    return value


def test_exact_private_identity_and_repr(owned):
    assert repr(owned) == "OwnedCluster(<private>)"
    assert SECRET not in repr(owned)
    assert m._verify_control(owned) is None


@pytest.mark.parametrize("field,value", [
    ("run_id", "C" * 32), ("run_id", "a" * 31), ("run_id", True),
    ("port", 55432), ("port", True), ("port", 0), ("port", 65536),
    ("system_identifier", "0"), ("system_identifier", "01"),
    ("system_identifier", "18446744073709551616"),
    ("system_identifier", 7412345678901234567),
    ("database_name", "generic_agents"),
    ("database_name", "ga_test_m60_m59_restore_" + RUN),
    ("oid", True), ("oid", 0), ("marker", "m60-m59-release-owned:" + RUN),
])
def test_owned_identity_rejects_shared_or_ambiguous_values(owned, field, value):
    rejected(lambda: replace(owned, **{field: value}))


@pytest.mark.parametrize("suffix", [
    "?host=127.0.0.1", "?hostaddr=127.0.0.1", "?options=-csuperuser=on",
    "?service=normal", "?sslmode=disable",
])
def test_app_url_has_no_query_redirect(owned, suffix):
    rejected(lambda: replace(owned, app_url=owned.app_url + suffix))


@pytest.mark.parametrize("old,new", [
    ("127.0.0.1", "localhost"), ("127.0.0.1", "remote.invalid"),
    ("59431", "55432"), ("m61_app_" + RUN[:12], "postgres"),
    ("ga_m61_" + RUN, "generic_agents"),
    (":" + SECRET + "@", "@"),
])
def test_app_url_is_pinned_to_nonadmin_role_and_owned_endpoint(owned, old, new):
    rejected(lambda: replace(owned, app_url=owned.app_url.replace(old, new)))


@pytest.mark.parametrize("key", [
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE",
    "PGOPTIONS", "HTTPS_PROXY", "http_proxy", "ALL_PROXY", "NO_PROXY",
    "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
])
def test_inherited_redirects_and_provider_credentials_rejected(key):
    rejected(lambda: m.validate_environment({key: SECRET}), m.Code.ENVIRONMENT)
    assert m.validate_environment({key: ""}) is None


def test_run_path_cannot_be_arbitrary_or_symlinked(owned):
    rejected(lambda: replace(owned, run_root=owned.run_root / "storage"))
    rejected(lambda: replace(owned, run_root=Path(str(owned.run_root) + "/../" + owned.run_root.name)))
    rejected(lambda: replace(owned, run_root=str(owned.run_root)))
    moved = owned.run_root.with_name("real-branch")
    owned.run_root.rename(moved)
    owned.run_root.symlink_to(moved, target_is_directory=True)
    rejected(lambda: m._verify_control(owned), m.Code.PATH)


@pytest.mark.parametrize("change", ["state", "marker", "extra", "duplicate", "version", "symlink", "permissions", "hardlink"])
def test_control_and_pg_version_fail_before_connect(owned, change):
    record = owned.run_root / "control/cluster-identity.json"
    value = m._control_record(owned)
    if change in {"state", "marker", "extra"}:
        value[{"state": "state", "marker": "marker", "extra": "extra"}[change]] = SECRET
        private_file(record, json.dumps(value))
    elif change == "duplicate":
        private_file(record, json.dumps(value)[:-1] + ',"state":"ready"}')
    elif change == "version":
        private_file(owned.run_root / "cluster/PG_VERSION", "16\n")
    elif change == "symlink":
        copy = owned.run_root / "control/copy.json"
        record.rename(copy)
        record.symlink_to(copy)
    elif change == "permissions":
        record.chmod(0o644)
    else:
        os.link(record, owned.run_root / "control/linked.json")
    rejected(lambda: m.snapshot_owned(owned, owned.run_root / "storage"))


def identity(owned):
    return {"database_name": owned.database_name, "oid": owned.oid,
        "marker": owned.marker, "transaction_read_only": "on",
        "system_identifier": owned.system_identifier, "server_address": "127.0.0.1",
        "server_port": owned.port, "role_name": "m61_app_" + RUN[:12],
        **{key: False for key in ("superuser", "createdb", "createrole", "replication",
            "bypassrls", "read_server_files", "write_server_files", "execute_server_program",
            "has_role_memberships")}}


class FakeConnection:
    def __init__(self, observed):
        self.observed, self.commands = observed, []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement):
        self.commands.append(str(statement))
        return self

    def mappings(self):
        return self

    def one(self):
        return self.observed


class FakeEngine:
    def __init__(self, observed):
        self.connection, self.disposed = FakeConnection(observed), False

    @contextmanager
    def connect(self):
        yield self.connection

    def dispose(self):
        self.disposed = True


def inject_database(monkeypatch, owned, observed=None):
    engine = FakeEngine(identity(owned) if observed is None else observed)
    calls = []
    def create(url, **kwargs):
        calls.append((url, kwargs))
        return engine
    monkeypatch.setattr(m, "create_engine", create)
    return engine, calls


def test_snapshot_checks_readonly_identity_before_inventory(owned, monkeypatch):
    engine, calls = inject_database(monkeypatch, owned)
    def inventory(connection, files):
        assert connection.commands[0] == "SET TRANSACTION READ ONLY"
        assert "pg_catalog.pg_control_system()" in connection.commands[1]
        assert "pg_catalog.host(pg_catalog.inet_server_addr()) AS server_address" in connection.commands[1]
        assert "pg_catalog.inet_server_addr()::text" not in connection.commands[1]
        assert len(connection.commands) == 2
        assert set(files) == {"original.bin"}
        return {"private": "inventory"}
    monkeypatch.setattr(m.check, "_collect_inventory", inventory)
    assert m.snapshot_owned(owned, owned.run_root / "storage") == {"private": "inventory"}
    assert engine.disposed
    assert len(calls) == 1
    assert calls[0][1] == {"isolation_level": "REPEATABLE READ", "connect_args": {
        "hostaddr": "127.0.0.1", "port": owned.port,
        "options": "-c default_transaction_read_only=on", "connect_timeout": 5}}


def test_snapshot_accepts_explicit_originals_root_without_fixture_state(owned, monkeypatch):
    originals = owned.run_root / "storage/originals"
    originals.mkdir(mode=0o700)
    (owned.run_root / "storage/original.bin").rename(originals / "original.bin")
    fixture = owned.run_root / "storage/fixture"
    fixture.mkdir(mode=0o700)
    private_file(fixture / "state.json", '{"synthetic":true}')
    engine, calls = inject_database(monkeypatch, owned)
    def inventory(connection, files):
        assert set(files) == {"original.bin"}
        return {"originals": dict(files)}
    monkeypatch.setattr(m.check, "_collect_inventory", inventory)
    result = m.snapshot_owned(owned, originals)
    assert set(result["originals"]) == {"original.bin"}
    assert len(calls) == 1 and engine.disposed


@pytest.mark.parametrize("which", ["fixture", "nested", "tmp", "other-run", "symlink"])
def test_snapshot_rejects_other_subtrees_cross_run_and_originals_symlink(owned, which):
    originals = owned.run_root / "storage/originals"
    if which == "symlink":
        target = owned.run_root / "storage/fixture"
        target.mkdir(mode=0o700)
        originals.symlink_to(target, target_is_directory=True)
        storage = originals
    else:
        paths = {"fixture": owned.run_root / "storage/fixture",
            "nested": originals / "nested", "tmp": owned.run_root / "tmp",
            "other-run": m.ROOT / ".runtime" / ("m61-run-" + "e" * 32) / "storage"}
        storage = paths[which]
        storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    rejected(lambda: m.snapshot_owned(owned, storage), m.Code.PATH)


@pytest.mark.parametrize("field,value", [
    ("database_name", "generic_agents"), ("oid", 1), ("marker", "other"),
    ("transaction_read_only", "off"), ("system_identifier", "1"),
    ("server_address", "::1"), ("server_address", "127.0.0.2"),
    ("server_address", "127.0.0.1/32"), ("server_port", 55432), ("role_name", "postgres"),
    ("superuser", True), ("createdb", True), ("createrole", True),
    ("replication", True), ("bypassrls", True), ("read_server_files", True),
    ("write_server_files", True), ("execute_server_program", True),
    ("has_role_memberships", True), ("superuser", 0), ("unexpected", False),
])
def test_wrong_live_identity_or_role_never_reaches_tables(owned, monkeypatch, field, value):
    observed = {**identity(owned), field: value}
    engine, _ = inject_database(monkeypatch, owned, observed)
    monkeypatch.setattr(m.check, "_collect_inventory", lambda *a: pytest.fail("tables accessed before identity"))
    rejected(lambda: m.snapshot_owned(owned, owned.run_root / "storage"), m.Code.IDENTITY)
    assert engine.disposed


@pytest.mark.parametrize("target", ["files", "control"])
def test_snapshot_detects_durable_inputs_changing_during_inventory(owned, monkeypatch, target):
    engine, _ = inject_database(monkeypatch, owned)
    def inventory(*args):
        if target == "files":
            private_file(owned.run_root / "storage/original.bin", b"changed")
        else:
            private_file(owned.run_root / "control/cluster-identity.json", "{}")
        return {}
    monkeypatch.setattr(m.check, "_collect_inventory", inventory)
    rejected(lambda: m.snapshot_owned(owned, owned.run_root / "storage"))
    assert engine.disposed


def test_database_exception_is_private(owned, monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError(SECRET + owned.app_url)
    monkeypatch.setattr(m, "create_engine", failure)
    rejected(lambda: m.snapshot_owned(owned, owned.run_root / "storage"), m.Code.DATABASE)


def test_private_storage_cannot_share_an_inode_with_historical_original(owned):
    os.link(owned.run_root / "storage/original.bin", m.ROOT / "historical-original.bin")
    rejected(lambda: m.snapshot_owned(owned, owned.run_root / "storage"), m.Code.FILES)


def profile_inputs(owned):
    read = m.ROOT / "frozen-src"
    read.mkdir(mode=0o700)
    binary = private_file(read / "fixture-binary", b"fixture never executed")
    binary.chmod(0o700)
    return read, binary


def test_worker_profile_has_only_owned_writes_and_outbound_endpoint(owned):
    read, binary = profile_inputs(owned)
    profile = m.worker_profile(owned.run_root, owned.port, binary, [read])
    assert "(deny default)" in profile and "(allow default)" not in profile
    writes = next(line for line in profile.splitlines() if line.startswith("(allow file-write*"))
    for name in ("storage", "tmp", "logs"):
        assert '(subpath "' + str(owned.run_root / name) + '")' in writes
    for name in ("control", "cluster", "pg-tmp", "pg-logs"):
        assert str(owned.run_root / name) not in profile
    assert '(remote tcp "localhost:59431")' in profile
    for forbidden in ("network-bind", "network-inbound", "process-fork", "signal", "mach-lookup", "55432"):
        assert forbidden not in profile
    assert str(m.ROOT / ".runtime/evidence") not in profile


def test_postgres_profile_has_only_owned_listener_data_and_pg_ipc(owned):
    read, binary = profile_inputs(owned)
    profile = m.postgres_profile(owned.run_root, owned.port, binary, [read])
    assert "(allow process-fork)" in profile
    assert '(local tcp "localhost:59431")' in profile
    assert "network-outbound" not in profile and "mach-lookup" not in profile
    writes = next(line for line in profile.splitlines() if line.startswith("(allow file-write*"))
    for name in ("cluster", "pg-tmp", "pg-logs"):
        assert str(owned.run_root / name) in writes
    for name in ("control", "storage", "tmp", "logs"):
        assert '(subpath "' + str(owned.run_root / name) + '")' not in writes
    assert "ipc-posix-name-regex" in profile and "PostgreSQL" in profile


def test_sysv_shm_and_sem_permissions_are_only_for_postgres_not_worker(owned):
    read, binary = profile_inputs(owned)
    worker = m.worker_profile(owned.run_root, owned.port, binary, [read])
    postgres = m.postgres_profile(owned.run_root, owned.port, binary, [read])
    assert "ipc-sysv" not in worker
    assert "(allow ipc-sysv-shm)" in postgres.splitlines()
    assert "(allow ipc-sysv-sem)" in postgres.splitlines()
    assert postgres.count("ipc-sysv") == 2
    assert "(allow ipc*)" not in postgres
    assert "(allow default)" not in postgres
    assert "network-outbound" not in postgres


def test_postgres_posix_shm_lifecycle_is_scoped_to_named_numeric_segments(owned):
    read, binary = profile_inputs(owned)
    worker = m.worker_profile(owned.run_root, owned.port, binary, [read])
    postgres = m.postgres_profile(owned.run_root, owned.port, binary, [read])
    assert "ipc-posix" not in worker and "ipc-sysv" not in worker
    posix_rules = [line for line in postgres.splitlines() if "ipc-posix" in line]
    assert posix_rules == [
        '(allow ipc-posix-shm* (ipc-posix-name-regex #"^/?PostgreSQL[.][0-9]+$"))']
    assert "(allow ipc-posix-shm*)" not in postgres


@pytest.mark.parametrize("builder", [m.worker_profile, m.postgres_profile])
def test_dyld_startup_permissions_are_named_sysctls_and_root_literal_only(owned, builder):
    read, binary = profile_inputs(owned)
    profile = builder(owned.run_root, owned.port, binary, [read])
    assert '(allow file-read-data (literal "/"))' in profile
    assert '(subpath "/")' not in profile
    sysctl = [line for line in profile.splitlines() if line.startswith("(allow sysctl-read")]
    assert len(sysctl) == 1
    assert '(sysctl-name "security.mac.lockdown_mode_state")' in sysctl[0]
    assert '(sysctl-name "kern.bootargs")' in sysctl[0]
    assert '(allow sysctl-read)' not in profile
    assert '(sysctl-name-regex' not in profile
    assert '(allow file-read*)' not in profile
    assert '(allow default)' not in profile


@pytest.mark.parametrize("which", ["project", "runtime", "run", "home", "slash", "control", "normal", "symlink"])
def test_profiles_reject_overbroad_or_symlink_read_roots(owned, which):
    read, binary = profile_inputs(owned)
    normal = m.ROOT / ".runtime/evidence"
    normal.mkdir(mode=0o700)
    alias = m.ROOT / "alias"
    alias.symlink_to(read, target_is_directory=True)
    roots = {"project": m.ROOT, "runtime": m.ROOT / ".runtime", "run": owned.run_root,
        "home": Path.home(), "slash": Path("/"), "control": owned.run_root / "control",
        "normal": normal, "symlink": alias}
    rejected(lambda: m.worker_profile(owned.run_root, owned.port, binary, [roots[which]]))


def test_profiles_encode_path_literals_without_sbpl_injection(owned):
    read, binary = profile_inputs(owned)
    quoted = m.ROOT / 'frozen-"(allow default)'
    quoted.mkdir(mode=0o700)
    profile = m.worker_profile(owned.run_root, owned.port, binary, [read, quoted])
    assert 'frozen-\\"(allow default)' in profile
    assert "\n(allow default)" not in profile


def test_worker_cannot_replace_allowed_binary_or_map_mutable_storage(owned):
    read, binary = profile_inputs(owned)
    profile = m.worker_profile(owned.run_root, owned.port, binary,
        [read, owned.run_root / "storage"])
    executable = next(line for line in profile.splitlines()
        if line.startswith("(allow file-map-executable"))
    assert str(owned.run_root / "storage") not in executable
    mutable_binary = private_file(owned.run_root / "tmp/python", b"never executed")
    mutable_binary.chmod(0o700)
    rejected(lambda: m.worker_profile(owned.run_root, owned.port, mutable_binary, [read]))


def test_m60_identity_remains_strictly_shared_cluster_restore_only(owned):
    with pytest.raises(m.check.CheckError):
        m.check.OwnedRestore(owned.app_url, owned.run_id, owned.oid, owned.marker)


def test_sqlalchemy_platform_uname_gets_only_three_named_system_queries(owned):
    read, binary = profile_inputs(owned)
    profile = m.worker_profile(owned.run_root, owned.port, binary, [read])
    for name in ("kern.hostname", "kern.version", "hw.machine"):
        assert profile.count('(sysctl-name "' + name + '")') == 1
    assert "sysctl-name-regex" not in profile
    assert "(allow sysctl-read)" not in profile
