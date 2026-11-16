"""Pure failure-path review of the one-shot M44 maintenance caller.

No real processes, signals, network, services or database connections are used.
Passing these tests is not evidence of an actual maintenance recovery.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import signal
import sys
from types import ModuleType, SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / ".runtime/m60-run-rehearsal-once.py"
SPEC = importlib.util.spec_from_file_location("m60_maintenance_review", SCRIPT)
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
# The established runtime module has import-time chdir/mkdir. Never load it here.
previous_runtime = sys.modules.get("local_runtime")
sys.modules["local_runtime"] = ModuleType("fixture_only_local_runtime")
try:
    SPEC.loader.exec_module(m)
finally:
    if previous_runtime is None:
        sys.modules.pop("local_runtime", None)
    else:
        sys.modules["local_runtime"] = previous_runtime

RUN = "e" * 32
SERVICES = (("api", "serve"), ("worker", "worker"), ("setup-worker", "setup-worker"),
            ("media-worker", "media-worker"), ("mqtt-worker", "mqtt-worker"))
ORIGINAL = {name: {"pid": index + 100, "command": command}
            for index, (name, command) in enumerate(SERVICES) if name != "worker"}


@pytest.fixture(autouse=True)
def no_real_runtime(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("uncontrolled process, network, signal or database operation")
    for module in (m, m.entry, m.check):
        monkeypatch.setattr(module, "create_engine", denied)
    monkeypatch.setattr(m.subprocess, "Popen", denied)
    monkeypatch.setattr(m.subprocess, "run", denied)
    monkeypatch.setattr(m.os, "killpg", denied)
    monkeypatch.setattr(m.httpx, "Client", denied)
    monkeypatch.setattr(m.signal, "signal", denied)
    monkeypatch.setattr(m, "state", {})
    monkeypatch.setattr(m, "recovery_errors", [])
    monkeypatch.setattr(m, "defer_signals", False)
    original_umask = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(original_umask)


def job(kind="task", status="succeeded", *, leased=0, tokens=0):
    return {"kind": kind, "status": status, "count": 1, "leased": leased, "tokens": tokens}


def test_only_source_proven_terminal_jobs_and_explicit_housekeeping_are_safe():
    rows = [job(status=status, tokens=1) for status in ("succeeded", "failed", "cancelled", "superseded")]
    rows += [job("cloud_budget", "internal"), job("login_limit", "internal")]
    assert m.require_safe_activity(rows, [{"status": "succeeded"}, {"status": "failed"}]) is None


@pytest.mark.parametrize("row", [
    job(status="queued"), job(status="running"), job(status="waiting"),
    job(status="completed"), job(status="done"), job(status="unknown"),
    job("unknown_kind", "internal"), job("cloud_budget", "queued"),
    job("login_limit", "running"), job(status="succeeded", leased=1),
    job("cloud_budget", "internal", leased=1), job("login_limit", "internal", tokens=1),
])
def test_unknown_queued_running_or_inconsistent_lease_jobs_refuse(row):
    with pytest.raises(AssertionError):
        m.require_safe_activity([row], [])


@pytest.mark.parametrize("status", ["reserved", "dispatched", "unknown", "pending", "cancelled", "charged"])
def test_every_unsettled_or_unrecognized_cloud_status_refuses(status):
    with pytest.raises(AssertionError):
        m.require_safe_activity([], [{"status": status, "count": 1}])


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_signals_unwind_active_work_and_are_deferred_during_recovery(signum, monkeypatch):
    with pytest.raises(m.MaintenanceInterrupted):
        m.controlled_signal(signum, None)
    assert m.state["received_signals"] == [signum]
    monkeypatch.setattr(m, "defer_signals", True)
    m.controlled_signal(signum, None)
    assert m.state["received_signals"] == [signum, signum]


@pytest.fixture
def caller_fixture(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    target = root / ".runtime" / ("m60-maintenance-" + RUN)
    target.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(m, "JOB", target)
    monkeypatch.setattr(m, "RUN", RUN)
    monkeypatch.setattr(m.signal, "signal", lambda *args: None)
    events = []
    live = copy.deepcopy(ORIGINAL)
    profile = SimpleNamespace(database_url="fixture-only")
    context = SimpleNamespace(profile=profile, contract={"baseline": {}, "candidate": {}})
    baseline = m.check.make_inventory({"old": [{"id": 1}, {"id": 1}]}, {"old": {"columns": []}}, {})

    def stop(name, command):
        events.append(("stop", name))
        live.pop(name, None)

    def start(name, command):
        assert name in ORIGINAL and name != "worker"
        events.append(("start", name))
        live[name] = {"pid": ORIGINAL[name]["pid"] + 100, "command": command}

    def activity(*args):
        events.append(("activity", args[1]))
        m.require_safe_activity([job("cloud_budget", "internal")], [{"status": "succeeded"}])
        return 19

    def writer(*, result=True):
        events.append(("writer",))
        backup = root / ".runtime/backups" / ("m60-m59-rehearsal-fixture-" + RUN)
        backup.mkdir(parents=True, mode=0o700)
        m.entry.private_json(backup, "normal-baseline.json", baseline)
        if result:
            m.entry.private_json(target, "writer-result.json", {"verified": True})
        return 0

    monkeypatch.setattr(m.runtime, "services", lambda mode: SERVICES, raising=False)
    monkeypatch.setattr(m.runtime, "start", start, raising=False)
    monkeypatch.setattr(m.runtime, "stop", stop, raising=False)
    monkeypatch.setattr(m, "observe_services", lambda: copy.deepcopy(live))
    monkeypatch.setattr(m, "require_original_bytes", lambda: context)
    monkeypatch.setattr(m, "require_recovery_baseline", lambda value: profile)
    monkeypatch.setattr(m, "activity", activity)
    monkeypatch.setattr(m, "require_quiescent", lambda *args: events.append(("quiescent",)))
    monkeypatch.setattr(m, "health", lambda: 200)
    monkeypatch.setattr(m, "group_exists", lambda pgid: False)
    monkeypatch.setattr(m.check, "snapshot_normal", lambda *args, **kwargs: copy.deepcopy(baseline))
    monkeypatch.setattr(m, "run_writer", writer)
    return SimpleNamespace(root=root, target=target, events=events, context=context,
                           writer=writer, baseline=baseline)


def test_missing_writer_result_cannot_turn_successful_recovery_green(caller_fixture, monkeypatch, capsys):
    f = caller_fixture
    monkeypatch.setattr(m, "run_writer", lambda: f.writer(result=False))
    assert m.main(RUN) == 1
    assert m.state["original_services_restored"] is True
    assert m.state["old_normal_data_exact_after_api_reload"] is True
    assert m.state["actual_normal_backup_restore_m59_migration"] is False
    assert m.state["verified"] is False
    assert [event[1] for event in f.events if event[0] == "start"] == list(ORIGINAL)
    assert [event[1] for event in f.events if event[0] == "activity"] == [
        "before-stop-activity.json", "after-stop-activity.json"]
    assert json.loads(capsys.readouterr().out)["verified"] is False


def test_failed_status_logs_and_error_diagnostics_cannot_block_four_service_recovery(
        caller_fixture, monkeypatch, capsys):
    f = caller_fixture
    actual_save, actual_open = m.save, os.open

    def failed_writer():
        raise RuntimeError("fixture writer failure")

    def save(phase, **kwargs):
        if phase in {"caller_failed", "restoring_original_m44", "failed_preserved"}:
            raise OSError("fixture status disk failure")
        return actual_save(phase, **kwargs)

    def open_without_recovery_logs(path, *args, **kwargs):
        if str(path).startswith(str(f.target / "recover-")):
            raise PermissionError("fixture recovery log unavailable")
        return actual_open(path, *args, **kwargs)

    def failed_diagnostic(*args, **kwargs):
        raise OSError("fixture private diagnostic unavailable")

    monkeypatch.setattr(m, "run_writer", failed_writer)
    monkeypatch.setattr(m, "save", save)
    monkeypatch.setattr(m, "private_error", failed_diagnostic)
    monkeypatch.setattr(m.os, "open", open_without_recovery_logs)
    assert m.main(RUN) == 1
    assert [event[1] for event in f.events if event[0] == "start"] == list(ORIGINAL)
    assert m.state["original_services_restored"] is True
    assert any(error.startswith("recovery_log:") for error in m.recovery_errors)
    assert m.state["verified"] is False
    capsys.readouterr()


def test_candidate_drift_does_not_enter_original_recovery_contract(caller_fixture, monkeypatch):
    f = caller_fixture
    raw = b'{"source_dir":".runtime/releases/m44/project/src"}'
    metadata = {"sha256": "a" * 64, "bytes": 8}
    baseline = {
        "pointer": {"path": ".runtime/active-backend-source.json", "literal_utf8": raw.decode()},
        "config": metadata, "source": {"old": True}, "frozen_source_manifest": {"old": True},
        "static": {"old": True},
    }
    class RejectedCandidate(dict):
        def __getitem__(self, key):
            pytest.fail("candidate read while recovering original M44")
    context = SimpleNamespace(profile=f.context.profile,
        contract={"baseline": baseline, "candidate": RejectedCandidate(),
                  "provenance": {"runtime_interpreters": [{"role": "main", "path": ".venv/bin/python"}]}})
    checks = []
    monkeypatch.setattr(m.check, "load_normal_profile", lambda *args: context.profile)
    monkeypatch.setattr(m.check, "_read_at", lambda *args, **kwargs: raw)
    monkeypatch.setattr(m.entry, "NORMAL_POINTER_SHA", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(m.entry, "_file_record", lambda *args: metadata)
    monkeypatch.setattr(m.entry, "_manifest", lambda root, value: {"generic_agents/__init__.py": metadata})
    monkeypatch.setattr(m.entry, "_frozen_manifest", lambda root, value: {"generic_agents/__init__.py": metadata})
    monkeypatch.setattr(m.entry, "_verify_tree", lambda root, value: checks.append(value))
    monkeypatch.setattr(m.entry, "_interpreter_record", lambda root, value: checks.append(value))
    monkeypatch.setattr(m.runtime, "service_environment", lambda *args: {
        "PYTHONPATH": str(f.root / ".runtime/releases/m44/project/src")}, raising=False)
    monkeypatch.setattr(m, "require_original_bytes", lambda: pytest.fail("full candidate preflight in recovery"))
    # Restore the real baseline-only helper hidden by the ordinary caller fixture.
    helper = original_recovery_baseline
    assert helper(context) is context.profile
    assert checks == [baseline["static"], context.contract["provenance"]["runtime_interpreters"][0]]


original_recovery_baseline = m.require_recovery_baseline
original_run_writer = m.run_writer


def test_exited_writer_leader_still_requires_whole_owned_group_cleanup(monkeypatch):
    events, alive = [], [True]
    process = SimpleNamespace(pid=31337, poll=lambda: events.append("poll") or 0,
                              wait=lambda **kwargs: events.append("wait") or 0)
    clock = itertools.count()
    monkeypatch.setattr(m, "group_exists", lambda pgid: alive[0])
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(m.time, "sleep", lambda *args: None)
    def killpg(pgid, sig):
        assert pgid == process.pid
        events.append(sig)
        if sig == signal.SIGKILL:
            alive[0] = False
    monkeypatch.setattr(m.os, "killpg", killpg)
    m.stop_owned_group(process)
    assert signal.SIGTERM in events and signal.SIGKILL in events
    assert events[-1] == "wait" and alive[0] is False and m.defer_signals is False


def test_writer_pid_diagnostic_failure_still_cleans_spawned_group(caller_fixture, monkeypatch):
    f = caller_fixture
    events = []
    process = SimpleNamespace(pid=31337, wait=lambda **kwargs: pytest.fail("writer wait after failed save"))
    def spawn(command, **kwargs):
        assert command[:3] == ["/usr/bin/sandbox-exec", "-p", m.SANDBOX]
        assert kwargs["start_new_session"] is True
        assert kwargs["env"].get("DASHSCOPE_API_KEY") is None
        assert kwargs["env"].get("DEEPSEEK_API_KEY") is None
        events.append("spawn")
        return process
    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "save", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture pid-save failed")))
    monkeypatch.setattr(m, "stop_owned_group", lambda value: events.append(("cleanup", value.pid)))
    with pytest.raises(OSError, match="pid-save failed"):
        original_run_writer()
    assert events == ["spawn", ("cleanup", process.pid)]


def test_signal_during_spawn_binding_is_deferred_then_cleans_group(caller_fixture, monkeypatch):
    events = []
    process = SimpleNamespace(pid=31337, wait=lambda **kwargs: pytest.fail("wait after spawn signal"))
    def spawn(*args, **kwargs):
        assert m.defer_signals is True
        m.controlled_signal(signal.SIGTERM, None)
        events.append("spawned")
        return process
    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "stop_owned_group", lambda value: events.append(("cleanup", value.pid)))
    with pytest.raises(m.MaintenanceInterrupted):
        original_run_writer()
    assert events == ["spawned", ("cleanup", process.pid)]
    assert m.state["received_signals"] == [signal.SIGTERM]
    assert m.defer_signals is False


def test_spawn_signal_and_failed_group_cleanup_cannot_resume_normal_writers(caller_fixture, monkeypatch, capsys):
    f = caller_fixture
    process = SimpleNamespace(pid=31337, wait=lambda **kwargs: pytest.fail("wait after spawn signal"))
    def spawn(*args, **kwargs):
        m.controlled_signal(signal.SIGTERM, None)
        return process
    def cleanup(value):
        assert value is process
        raise RuntimeError("fixture owned process group remains alive")
    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "run_writer", original_run_writer)
    monkeypatch.setattr(m, "stop_owned_group", cleanup)
    monkeypatch.setattr(m, "group_exists", lambda pgid: pgid == process.pid)
    assert m.main(RUN) == 1
    assert m.state.get("writer_pid") == process.pid
    assert not any(event[0] == "start" for event in f.events)
    assert m.state["original_services_restored"] is False
    assert "owned_rehearsal_group_present" in m.recovery_errors
    assert json.loads(capsys.readouterr().out)["verified"] is False
