"""Owned supervisor P1 regressions: fake processes and private temp files only.

Run with --noconftest. No PostgreSQL, profile, subprocess or socket is executed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / ".runtime/m61-pg-supervisor.py"
SPEC = importlib.util.spec_from_file_location("m61_independent_pg_supervisor", SCRIPT)
s = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = s
SPEC.loader.exec_module(s)
RUN = "d" * 32
PROBE_CHECKS = (
    "cluster_write_denied", "control_admin_credential_read_denied", "control_write_denied",
    "extra_program_exec_denied", "ipv6_denied", "normal_backups_read_denied",
    "normal_env_read_denied", "normal_postgres_port_denied", "other_loopback_port_denied",
    "outside_append_denied", "outside_chmod_denied", "outside_rename_denied",
    "outside_truncate_denied", "outside_unlink_denied", "owned_loopback_port_connect",
    "private_storage_write", "storage_dotdot_escape_denied", "storage_hardlink_creation_denied",
    "storage_symlink_escape_denied", "udp_denied", "unix_socket_denied",
    "unrelated_signal_denied", "worker_listen_denied")
WORKER_PROFILE = "fixture worker profile never executed"


@pytest.fixture(autouse=True)
def no_real_external_operations(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("uncontrolled database, subprocess, socket or process signal")
    monkeypatch.setattr(s, "create_engine", denied)
    monkeypatch.setattr(s.m, "create_engine", denied)
    monkeypatch.setattr(s.m.check, "create_engine", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(os, "killpg", denied)
    monkeypatch.setattr(os, "kill", denied)
    monkeypatch.setattr(s, "stop_requested", False)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class Process:
    pid = 43210

    def __init__(self, *, exited=False):
        self.returncode = 0 if exited else None
        self.group_alive = True
        self.group_signals, self.leader_signals = [], []
        self.waits = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        assert not self.group_alive
        self.waits += 1
        return self.returncode

    def send_signal(self, signum):
        self.leader_signals.append(signum)
        self.returncode = -signum
        self.group_alive = False

    def killpg(self, pid, signum):
        assert pid == self.pid, "signalled a process group not owned by this fake"
        if not self.group_alive:
            raise ProcessLookupError
        if signum:
            self.group_signals.append(signum)
            self.returncode = self.returncode if self.returncode is not None else -signum
            self.group_alive = False


def fake_runtime(monkeypatch, process):
    clock = Clock()
    monkeypatch.setattr(s, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setattr(s.os, "killpg", process.killpg)
    return clock


def private(path, contents):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
    path.chmod(0o600)
    return path


@pytest.fixture
def owned(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "project"
    root.mkdir(mode=0o700)
    run = root / ".runtime" / ("m61-run-" + RUN)
    run.mkdir(mode=0o700, parents=True)
    for name in ("control", "cluster", "pg-tmp", "pg-logs"):
        (run / name).mkdir(mode=0o700)
    work = root / ".runtime/m61-worker-isolation-work"
    work.mkdir(mode=0o700)
    helper = private(root / "scripts/m61_worker_isolation.py", b"fixture provenance only")
    private(work / "latest-owned-run.json", json.dumps({
        "run_id": RUN, "run_root": str(run), "port": 59431}))
    private(work / "normal-database-locale-metadata.json", json.dumps({
        "encoding": "UTF8", "datlocprovider": "c", "datcollate": "C", "datctype": "C"}))
    for attempt, source_sha in ((5, "0" * 64), (6, hashlib.sha256(helper.read_bytes()).hexdigest())):
        private(run / "control" / ("probe-attempt-" + str(attempt) + "-result.json"), json.dumps({
            "format": "m61-os-probe-attempt-v1", "attempt": attempt, "run_id": RUN,
            "passed": True, "contract_source_sha256": source_sha,
            "profile_sha256": hashlib.sha256(WORKER_PROFILE.encode()).hexdigest(),
            "checks": {name: True for name in PROBE_CHECKS},
            "normal_config_unchanged": True, "owned_outside_canary_unchanged": True,
            "real_database_or_services_used": False, "returncode": 0}))
    private(run / "control/admin-password", "fixture-private-password-" + "x" * 32)
    private(run / "cluster/PG_VERSION", "17\n")
    monkeypatch.setattr(s, "ROOT", root)
    monkeypatch.setattr(s.m, "ROOT", root)
    monkeypatch.setattr(s, "WORK", work)
    monkeypatch.setattr(s, "PG", root / "fake-pg17/bin")
    monkeypatch.setattr(s.m, "postgres_profile", lambda *args: "fixture profile never executed")
    monkeypatch.setattr(s.m, "worker_profile", lambda *args: WORKER_PROFILE)
    return run


def test_approval_selects_current_proof_instead_of_fixed_historical_attempt(owned):
    selected = s.approved_probe(owned, 59431)
    assert selected["attempt"] == 6
    assert selected["run_id"] == RUN
    assert len(selected["checks"]) == 23
    assert (owned / "control/probe-attempt-5-result.json").is_file()


def test_approval_uses_latest_matching_attempt_and_ignores_newer_wrong_profile(owned):
    value = json.loads((owned / "control/probe-attempt-6-result.json").read_text())
    private(owned / "control/probe-attempt-8-result.json", json.dumps({**value, "attempt": 8}))
    private(owned / "control/probe-attempt-9-result.json", json.dumps({
        **value, "attempt": 9, "profile_sha256": "0" * 64}))
    assert s.approved_probe(owned, 59431)["attempt"] == 8


@pytest.mark.parametrize("change", ["run", "source", "profile", "failed", "missing_check", "false_check", "truthy_check"])
def test_invalid_current_proof_cannot_fall_back_to_stale_source_or_spawn(owned, change):
    path = owned / "control/probe-attempt-6-result.json"
    value = json.loads(path.read_text())
    if change == "run":
        value["run_id"] = "e" * 32
    elif change in {"source", "profile"}:
        value[{"source": "contract_source_sha256", "profile": "profile_sha256"}[change]] = "0" * 64
    elif change == "failed":
        value["passed"] = False
    elif change == "missing_check":
        value["checks"].pop("worker_listen_denied")
    else:
        value["checks"]["worker_listen_denied"] = False if change == "false_check" else 1
    private(path, json.dumps(value))
    with pytest.raises(AssertionError, match="No current complete OS isolation proof"):
        s.run(RUN, existing=True)
    assert not (owned / "control/postgres-restart-state.json").exists()
    assert not list((owned / "pg-logs").iterdir())


def test_initdb_timeout_terminates_and_reaps_owned_group(monkeypatch):
    process = Process()
    fake_runtime(monkeypatch, process)
    with pytest.raises(TimeoutError, match="timed out"):
        s.wait_tool(process, timeout=.1)
    assert process.group_signals == [signal.SIGTERM]
    assert not process.group_alive and process.waits == 1


def test_exited_leader_does_not_hide_owned_descendants(monkeypatch):
    process = Process(exited=True)
    fake_runtime(monkeypatch, process)
    s.stop_group(process, signal.SIGINT)
    assert process.group_signals == [signal.SIGINT]
    assert not process.leader_signals
    assert not process.group_alive and process.waits == 1


def test_run_initdb_timeout_uses_new_group_and_private_log_then_preserves_failure(owned, monkeypatch):
    (owned / "cluster/PG_VERSION").unlink()
    private(owned / "cluster/probe-cluster-canary", b"m61-cluster")
    process, spawns = Process(), []
    fake_runtime(monkeypatch, process)
    def spawn(command, **kwargs):
        spawns.append((command, kwargs))
        return process
    monkeypatch.setattr(s.subprocess, "Popen", spawn)
    with pytest.raises(TimeoutError, match="timed out"):
        s.run(RUN, existing=False)
    assert len(spawns) == 1
    command, kwargs = spawns[0]
    assert Path(command[0]).name == "initdb"
    assert command[command.index("-D") + 1] == str(owned / "cluster")
    assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert (owned / "pg-logs/initial-initdb.log").is_file()
    assert (owned / "pg-logs/initial-initdb.log").stat().st_mode & 0o777 == 0o600
    assert process.group_signals == [signal.SIGTERM]
    assert not process.group_alive
    state = json.loads((owned / "control/postgres-initial-state.json").read_text())
    assert state["phase"] == "failed_preserved"
    assert state["failed_phase"] == "preparing_owned_cluster"
    assert state["error_type"] == "TimeoutError"
    assert state["cleanup_error_types"] == []


def test_state_and_failure_log_errors_cannot_skip_owned_pg_cleanup(owned, monkeypatch):
    process, spawns = Process(), []
    fake_runtime(monkeypatch, process)
    def spawn(command, **kwargs):
        spawns.append((command, kwargs))
        return process
    monkeypatch.setattr(s.subprocess, "Popen", spawn)
    original_replace, original_open = os.replace, os.open
    def failing_replace(source, destination, *args, **kwargs):
        if json.loads(Path(source).read_text())["phase"] == "starting":
            raise OSError("fixture state write failure after owned spawn")
        return original_replace(source, destination, *args, **kwargs)
    def failing_open(path, *args, **kwargs):
        if str(path).endswith("restart-failure.json"):
            raise OSError("fixture failure log unavailable")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(s.os, "replace", failing_replace)
    monkeypatch.setattr(s.os, "open", failing_open)
    with pytest.raises(OSError, match="state write failure"):
        s.run(RUN, existing=True)
    assert len(spawns) == 1
    assert spawns[0][1]["start_new_session"] is True
    assert spawns[0][1]["close_fds"] is True
    assert process.leader_signals == [signal.SIGINT]
    assert not process.group_alive and process.waits == 1


@pytest.mark.parametrize("change", ["symlink", "hardlink", "public", "oversize"])
def test_password_rejected_before_any_spawn(owned, change):
    password = owned / "control/admin-password"
    if change == "symlink":
        copy = password.with_name("password-copy")
        password.rename(copy)
        password.symlink_to(copy)
    elif change == "hardlink":
        os.link(password, password.with_name("password-copy"))
    elif change == "public":
        password.chmod(0o644)
    else:
        private(password, "x" * 4097)
    with pytest.raises(s.m.IsolationError):
        s.run(RUN, existing=True)
    assert not (owned / "control/postgres-restart-state.json").exists()
    assert not list((owned / "pg-logs").iterdir())
