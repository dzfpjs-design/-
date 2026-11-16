"""Pure CLI/dispatch tests for the private M61 candidate.

Run with --noconftest and M61 project/src first in PYTHONPATH. No processes,
database, models, services or network are used; this is not takeover evidence.
"""
from __future__ import annotations

import ast
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from generic_agents import cli, db as database, worker


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / ".runtime/m61-worker-isolation-work/project/src/generic_agents"
FROZEN = ROOT / ".runtime/m60-release-contract-tools/run-6efb88376dcb4696a30320e3da3156b8/candidate/project/src/generic_agents"
IDENTITY = "e2d911e1-241c-47a7-b28a-31ac8e1f7648"


@pytest.fixture(autouse=True)
def forbid_real_runtime(monkeypatch):
    assert Path(worker.__file__).resolve().parent == CANDIDATE
    assert Path(cli.__file__).resolve().parent == CANDIDATE

    def denied(*args, **kwargs):
        pytest.fail("Real database, network or subprocess action is forbidden")

    monkeypatch.setattr(database, "create_engine", denied)
    for module in (worker, cli):
        monkeypatch.setattr(module, "engine", denied)
        monkeypatch.setattr(module, "Session", denied)
    monkeypatch.setattr(cli, "migrate", denied)
    monkeypatch.setattr(worker, "deliver_events", denied)
    monkeypatch.setattr(httpx, "Client", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)


def argv(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["ga", *arguments])


def persisted_status(monkeypatch, status):
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, model, identity):
            assert model is worker.Job and identity == IDENTITY
            return SimpleNamespace(status=status) if status is not None else None

    monkeypatch.setattr(worker, "engine", lambda: "fake-engine")
    monkeypatch.setattr(worker, "Session", lambda bind: FakeSession())


def test_frozen_setup_kind_order_and_default_body_are_unchanged():
    old = ast.parse((FROZEN / "worker.py").read_text())
    new = ast.parse((CANDIDATE / "worker.py").read_text())
    old_setup = next(node for node in old.body if isinstance(node, ast.FunctionDef) and node.name == "run_setup")
    new_setup = next(node for node in new.body if isinstance(node, ast.FunctionDef) and node.name == "run_setup")
    call = next(node for node in ast.walk(old_setup) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "process_once")
    kinds = next(item for item in call.keywords if item.arg == "kinds")
    assert tuple(ast.literal_eval(kinds.value)) == worker.SETUP_KINDS
    kinds.value = ast.Name(id="SETUP_KINDS", ctx=ast.Load())
    assert ast.dump(old_setup, include_attributes=False) == ast.dump(new_setup, include_attributes=False)


def test_default_cli_still_calls_normal_setup_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "run_setup", lambda: calls.append("default"))
    monkeypatch.setattr(worker, "run_setup_job", lambda value: pytest.fail("bounded branch selected without --job-id"))
    argv(monkeypatch, "setup-worker")
    assert cli.main() is None
    assert calls == ["default"]


@pytest.mark.parametrize("exit_code", [0, 1, 3])
def test_scoped_cli_uses_exact_identity_and_exit_code(monkeypatch, exit_code):
    calls = []
    monkeypatch.setattr(worker, "run_setup", lambda: pytest.fail("default scheduler branch called"))
    monkeypatch.setattr(worker, "run_setup_job", lambda value: calls.append(value) or exit_code)
    argv(monkeypatch, "setup-worker", "--job-id", IDENTITY)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == exit_code
    assert calls == [IDENTITY]


@pytest.mark.parametrize("identity", ["", "missing", "null", "../old-job", IDENTITY.upper(), IDENTITY.replace("-", ""), "{" + IDENTITY + "}"])
def test_invalid_identity_is_rejected_before_worker_or_database(monkeypatch, identity):
    monkeypatch.setattr(worker, "run_setup_job", lambda value: pytest.fail("invalid identity reached worker"))
    argv(monkeypatch, "setup-worker", "--job-id", identity)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


@pytest.mark.parametrize("command", ["migrate", "bootstrap", "serve", "worker", "media-worker", "mqtt-worker"])
def test_job_id_rejected_on_other_commands_before_actions(monkeypatch, command):
    argv(monkeypatch, command, "--job-id", IDENTITY)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


def test_missing_job_id_value_is_rejected(monkeypatch):
    argv(monkeypatch, "setup-worker", "--job-id")
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


@pytest.mark.parametrize("identity", [None, "", "invalid", IDENTITY.upper(), 4])
def test_direct_bounded_entry_cannot_fall_back_to_global_claim(monkeypatch, identity):
    monkeypatch.setattr(worker, "process_once", lambda **kwargs: pytest.fail("invalid direct entry reached claim"))
    with pytest.raises(ValueError, match="UUID"):
        worker.run_setup_job(identity)


def test_scoped_worker_passes_exact_claim_limits_once(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(worker, "process_once", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(worker, "run_setup", lambda: pytest.fail("global scheduler called"))
    persisted_status(monkeypatch, "succeeded")
    assert worker.run_setup_job(IDENTITY) == 0
    assert calls == [{"job_id": IDENTITY, "kinds": worker.SETUP_KINDS, "deliver": False}]
    assert "持久状态为 succeeded" in capsys.readouterr().out


def test_real_process_once_no_claim_never_delivers_or_expands_scope(monkeypatch, capsys):
    claims = []
    monkeypatch.setattr(worker, "claim", lambda job_id, kinds: claims.append((job_id, kinds)))
    # Retain shipping process_once. Its normal second claim must retain the same
    # exact identity and kinds, while deliver=False suppresses generic outbox.
    assert worker.run_setup_job(IDENTITY) == 3
    assert claims == [(IDENTITY, worker.SETUP_KINDS), (IDENTITY, worker.SETUP_KINDS)]
    assert "未领取指定任务" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["failed", "cancelled", "running", "queued", "unknown", None])
def test_processed_true_cannot_misreport_non_success_status(monkeypatch, capsys, status):
    monkeypatch.setattr(worker, "process_once", lambda **kwargs: True)
    persisted_status(monkeypatch, status)
    assert worker.run_setup_job(IDENTITY) == 1
    text = capsys.readouterr().out
    assert "未确认成功" in text and "持久状态为 succeeded" not in text


@pytest.mark.parametrize("stage", ["process", "readback"])
def test_exceptions_fail_without_retry_or_connection_detail(monkeypatch, capsys, stage):
    calls = []

    def attempt(**kwargs):
        calls.append(kwargs)
        if stage == "process":
            raise RuntimeError("private-connection-detail")
        return True

    monkeypatch.setattr(worker, "process_once", attempt)
    if stage == "readback":
        monkeypatch.setattr(worker, "engine", lambda: (_ for _ in ()).throw(RuntimeError("private-connection-detail")))
    assert worker.run_setup_job(IDENTITY) == 1
    assert len(calls) == 1
    text = capsys.readouterr().out
    assert "无法确认成功" in text and "未自动重试" in text
    assert "private-connection-detail" not in text


def test_database_error_is_failure_and_does_not_retry(monkeypatch, capsys):
    calls = []

    def attempt(**kwargs):
        calls.append(kwargs)
        raise worker.DBAPIError("private-dsn", None, RuntimeError("private-driver-error"))

    monkeypatch.setattr(worker, "process_once", attempt)
    assert worker.run_setup_job(IDENTITY) == 1
    assert len(calls) == 1
    text = capsys.readouterr().out
    assert "数据库错误" in text and "未自动重试" in text
    assert "private-dsn" not in text and "private-driver-error" not in text


def test_bounded_entry_does_not_call_any_scheduler(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("global scheduler or receiver called")

    for module, method in (("mqtt_data", "deliver_pending"), ("perception_signals", "deliver_pending"),
                           ("candidate_watches", "deliver_pending"), ("supplement_watch", "schedule_once"),
                           ("media_monitor", "schedule_once"), ("supplement", "schedule_once"),
                           ("video_daily", "schedule_once"), ("audio_daily", "schedule_once"),
                           ("geo_daily", "schedule_once"), ("local_observations", "schedule"),
                           ("native_receipts", "schedule"), ("mqtt_observations", "schedule_pending"),
                           ("mqtt_daily", "schedule_once")):
        stub = ModuleType("generic_agents." + module)
        setattr(stub, method, forbidden)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    monkeypatch.setattr(worker, "process_once", lambda **kwargs: False)
    assert worker.run_setup_job(IDENTITY) == 3
