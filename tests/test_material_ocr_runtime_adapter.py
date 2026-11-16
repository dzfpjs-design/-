"""Adapter integrity tests; real OCR quality evidence is a separate local run."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from generic_agents import material_ocr_runtime as runtime


@pytest.fixture(autouse=True)
def explicit_existing_runtime_without_processes(monkeypatch):
    # Historical manifest reads are explicit; never inherit the instance default
    # or launch OCR/OS probes in this pure adapter suite.
    root = Path(__file__).resolve().parents[1] / '.runtime'
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: root)
    def forbidden(*args, **kwargs):
        pytest.fail('pure adapter tests must not launch any process')
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(runtime.subprocess, 'run', forbidden)


def test_missing_engine_is_blocked_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: tmp_path)
    result = runtime.engine_status()
    assert result["ready"] is False
    assert result["manifest"] is None
    assert "未安装" in result["reason"]


def test_changed_install_manifest_is_not_new_authorization(monkeypatch, tmp_path):
    manifest = json.loads((runtime._operation_paths().base / "manifest.json").read_bytes())
    manifest["models"][0]["sha256"] = "0" * 64
    base = tmp_path / 'ocr-m49'; base.mkdir()
    (base / "manifest.json").write_bytes(runtime._canonical(manifest))
    python = tmp_path / 'venvs/ocr-m49/bin/python'; python.parent.mkdir(parents=True)
    python.write_bytes(b'not-executable-pure-fixture')
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: tmp_path)
    assert runtime.engine_status()["ready"] is False
    with pytest.raises(HTTPException, match="安装版本"):
        runtime.normalize_image(b"not-an-image")


@pytest.mark.parametrize("entry", [{"path": "../source", "sha256": "0" * 64}, {"path": "/tmp/source", "sha256": "0" * 64}])
def test_install_entry_cannot_escape_root(tmp_path, entry):
    with pytest.raises(HTTPException, match="路径异常"):
        runtime._checked_file(tmp_path, entry)


def test_file_hash_and_symlink_are_rejected(tmp_path):
    content = tmp_path / "model"
    content.write_bytes(b"exact-weight")
    entry = {"path": "model", "sha256": hashlib.sha256(b"exact-weight").hexdigest()}
    runtime._checked_file(tmp_path, entry)
    content.write_bytes(b"other-weight")
    with pytest.raises(HTTPException, match="校验失败"):
        runtime._checked_file(tmp_path, entry)
    (tmp_path / "link").symlink_to(content)
    with pytest.raises(HTTPException, match="缺少"):
        runtime._checked_file(tmp_path, {**entry, "path": "link"})


def test_compact_is_independently_hashed_not_claimed_full_hash():
    manifest = json.loads((runtime._operation_paths().base / "manifest.json").read_bytes())
    full_hash = runtime._sha(runtime._canonical(manifest))
    summary = runtime._summary(manifest, full_hash)
    assert full_hash == runtime._MANIFEST_HASH
    assert summary["manifest_hash"] == full_hash
    assert runtime._sha(runtime._canonical(summary)) != full_hash
    assert "runtime_files" not in summary and "artifacts" not in summary
    assert len(runtime._canonical(summary)) < 6000
    assert summary["data_policy"]["cloud_allowed"] is False


@pytest.mark.parametrize("page", [True, 0, -1, "1"])
def test_pdf_page_is_strict_integer(page):
    with pytest.raises(HTTPException, match="页码"):
        runtime.render_pdf(b"pdf", page=page)


def test_conflicting_hash_aliases_cannot_retarget_task():
    with pytest.raises(HTTPException, match="参数不一致"):
        runtime.recognize(b"image", "a" * 64, expected_manifest_hash="b" * 64)


def test_wrong_expected_hash_is_rejected_before_launch(monkeypatch):
    manifest = json.loads((runtime._operation_paths().base / "manifest.json").read_bytes())
    monkeypatch.setattr(runtime, "_installation", lambda **kwargs: (manifest, runtime._MANIFEST_HASH))
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(HTTPException, match="冻结"):
        runtime.recognize(b"image", expected_manifest_hash="0" * 64)


def test_guard_failure_stops_before_launch(monkeypatch):
    manifest = json.loads((runtime._operation_paths().base / "manifest.json").read_bytes())
    monkeypatch.setattr(runtime, "_installation", lambda **kwargs: (manifest, runtime._MANIFEST_HASH))
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not execute"))
    class Cancelled(Exception):
        pass
    def guard():
        raise Cancelled()
    with pytest.raises(Cancelled):
        runtime.recognize(b"image", expected_manifest_hash=runtime._MANIFEST_HASH, guard=guard)


def test_many_pages_require_pagewise_calls(monkeypatch):
    monkeypatch.setattr(runtime, "_execute", lambda *args, **kwargs: {"page_count": 500})
    with pytest.raises(HTTPException, match="逐页"):
        runtime.render_pdf(b"pdf")


def test_os_isolation_failure_is_blocked(monkeypatch):
    class Failed:
        returncode = 3
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: Failed())
    with pytest.raises(HTTPException, match="断网隔离未生效"):
        runtime._sandbox_probe()
