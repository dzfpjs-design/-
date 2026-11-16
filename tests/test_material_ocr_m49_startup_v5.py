"""Actual startup attacks in temporary hardlink copies; no real package mutation.

The declared M49 files retain their hashes. New entries are harmless owned probes.
No OCR/model/cloud work is required to verify this pre-inference integrity boundary.
"""
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr_runtime as runtime

MANIFEST_HASH = "55c902bbe51f2cdc9841e8571e9729f4c1a24c628601361a77019fbc7b1040e2"
MARKER = "M49_UNPINNED_STARTUP_EXECUTED_V5_PROBE"


@pytest.fixture
def copied_runtime(monkeypatch, tmp_path):
    root = tmp_path / "owned-installation"
    venv = root / ".runtime/venvs/ocr-m49"
    package = root / ".runtime/ocr-m49"
    for original, target in [(runtime._BASE.resolve(), package),
                             (runtime._PYTHON.parent.parent.resolve(), venv)]:
        for path in original.rglob("*"):
            copied = target / path.relative_to(original)
            assert not path.is_symlink()
            if path.is_dir():
                copied.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                copied.parent.mkdir(parents=True, exist_ok=True)
                os.link(path, copied)
            else:
                pytest.fail("actual package must contain only regular files/directories")
    assert hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest() == MANIFEST_HASH
    monkeypatch.setattr(runtime, "_ROOT", root)
    monkeypatch.setattr(runtime, "_BASE", package)
    monkeypatch.setattr(runtime, "_PYTHON", venv / "bin/python")
    monkeypatch.setattr(runtime, "_SITE", venv / "lib/python3.12/site-packages")
    monkeypatch.setattr(runtime, "_BASE_RUNTIME", venv / "base-python")
    monkeypatch.setattr(runtime, "_PROFILE", "(version 1)(allow default)(deny network*)" + "".join(
        "(deny file-write* (subpath " + json.dumps(str(path), ensure_ascii=False) + "))"
        for path in [package, venv]))
    return root


def declared_files_still_match():
    manifest = json.loads((runtime._BASE / "manifest.json").read_bytes())
    for base, entries in [(runtime._SITE, manifest["private_site_files"]),
                          (runtime._BASE_RUNTIME, manifest["base_runtime_files"])]:
        for entry in entries:
            assert hashlib.sha256((base / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]


def observe_python_launches(monkeypatch):
    actual_popen = subprocess.Popen
    launches = []
    def observed(command, *args, **kwargs):
        launches.append(command)
        return actual_popen(command, *args, **kwargs)
    monkeypatch.setattr(runtime.subprocess, "Popen", observed)
    return launches


def test_actual_directory_import_attack_is_blocked_before_any_launch(copied_runtime, monkeypatch):
    external = copied_runtime / "outside-pinned-roots"
    external.mkdir()
    (external / "__init__.py").write_text("import builtins\nbuiltins.m49_unpinned_v5=True\n")
    (runtime._SITE / "sitecustomize").symlink_to(external, target_is_directory=True)
    declared_files_still_match()
    # Prove this harmless attack fixture actually imports under -I -B. The
    # product must reject it before making any such Python startup call.
    proof = subprocess.run([str(runtime._SANDBOX), "-p", runtime._PROFILE,
        str(runtime._PYTHON), "-I", "-B", "-c",
        "import builtins;print(getattr(builtins,'m49_unpinned_v5',False))"],
        cwd=copied_runtime, env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=8, check=False)
    assert proof.returncode == 0 and proof.stdout.strip() == "True"
    launches = observe_python_launches(monkeypatch)
    with pytest.raises(HTTPException):
        runtime._installation(full=True)
    assert launches == []


def test_partial_status_probe_does_not_import_unchecked_regular_sitecustomize(copied_runtime, monkeypatch):
    (runtime._SITE / "sitecustomize.py").write_text("print(" + repr(MARKER) + ")\n")
    actual_run = subprocess.run
    captures = []
    def capture(command, *args, **kwargs):
        kwargs["stdout"] = subprocess.PIPE
        result = actual_run(command, *args, **kwargs)
        captures.append((command, result.stdout))
        return result
    monkeypatch.setattr(runtime.subprocess, "run", capture)
    status = runtime.engine_status()
    assert status["ready"] is True, "A safe stdlib-only probe remains available"
    assert captures and all("-S" in command for command, _ in captures)
    assert all(MARKER.encode() not in output for _, output in captures)
    with pytest.raises(HTTPException):
        runtime._installation(full=True)


@pytest.mark.parametrize("root_name", ["_BASE", "_BASE_RUNTIME", "_SITE"])
@pytest.mark.parametrize("entry_kind", ["directory_symlink", "file_symlink", "dangling_symlink", "fifo", "socket"])
@pytest.mark.parametrize("full", [False, True])
def test_unlisted_links_and_special_entries_block_before_probe(copied_runtime, monkeypatch, root_name, entry_kind, full):
    directory = getattr(runtime, root_name)
    extra = directory / "unlisted-startup-probe"
    bound = None
    if entry_kind == "directory_symlink":
        extra.symlink_to(copied_runtime, target_is_directory=True)
    elif entry_kind == "file_symlink":
        extra.symlink_to(runtime._BASE / "runner.py")
    elif entry_kind == "dangling_symlink":
        extra.symlink_to(copied_runtime / "absent-target")
    elif entry_kind == "fifo":
        os.mkfifo(extra)
    else:
        bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # macOS has a short Unix-socket pathname limit. A relative bind avoids
        # changing the semantic fixture because the entry still lives here.
        previous = Path.cwd()
        try:
            os.chdir(directory)
            bound.bind(extra.name)
        finally:
            os.chdir(previous)
    launches = observe_python_launches(monkeypatch)
    try:
        with pytest.raises(HTTPException):
            runtime._installation(full=full)
        assert launches == []
    finally:
        if bound is not None:
            bound.close()


def test_legitimate_complete_installation_still_passes_and_empty_directory_is_allowed(copied_runtime):
    # The contract pins the complete FILE set. An empty ordinary directory does
    # not contain executable bytes and is allowed; never call this an entry set.
    (runtime._SITE / "empty-real-directory").mkdir()
    manifest, hashed = runtime._installation(full=True)
    assert hashed == MANIFEST_HASH and manifest["id"] == runtime._ENGINE_ID
    assert runtime.engine_status()["ready"] is True
