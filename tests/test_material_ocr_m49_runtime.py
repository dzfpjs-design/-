"""M49 qualification: real CPU API persistence; separate injected controller boundaries.

Run only after the M49 package is ready, with PYTHONPATH pointing to its frozen source.
Existing conftest owns a disposable ga_test_* PostgreSQL DB/storage, never normal data.
Prepare frozen inputs before running: python .runtime/ocr-m49-qualification.py prepare.
No true OCR method is mocked in the two real API journey cases.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unicodedata
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import material_ocr_runtime as runtime
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (CloudCall, Job, MaterialOcrConfirmation,
                                  MaterialOcrRun, MaterialOcrUnit, MaterialOcrVersion)
from generic_agents.worker import process_once

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/material-ocr-m49"
LEGACY_M45_HASH = "d242884f8972275fac53bc1e4dc1a8907e7bf252dea96081e7fa13dd2776942d"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def record(name, body):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / (name + ".json")).write_bytes(runtime._canonical(body))


@pytest.fixture(scope="module", autouse=True)
def require_m49_frozen_source():
    assert runtime._RUNTIME_EPOCH == "m49", "M49 package/source not ready; do not fall back to M45"
    source = Path(runtime.__file__).resolve()
    source_root = source.parents[1]
    expected = os.getenv("GA_TEST_FROZEN_SOURCE")
    if expected:
        assert source_root == Path(expected).resolve(), "Use the selected frozen source through PYTHONPATH"
    source_manifest = json.loads((source_root / "source-manifest.json").read_bytes())
    relative = str(source.relative_to(source_root))
    pinned = next(entry for entry in source_manifest["files"] if entry["path"] == relative)
    assert sha(source.read_bytes()) == pinned["sha256"], "The active OCR adapter must match its frozen manifest"
    assert runtime._MAX_RESIDENT_BYTES == 1024 ** 3
    assert runtime._WALL_TIMEOUT_SECONDS == 90
    assert set(runtime._RUNTIME_PROFILES) >= {"m45", "m49"}
    assert runtime._BASE.name == "ocr-m49"
    assert runtime._BASE_RUNTIME.name == "base-python"
    poll_calls = [node for node in ast.walk(ast.parse(inspect.getsource(runtime._execute)))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "sleep"]
    assert any(node.args and ast.literal_eval(node.args[0]) == 0.1 for node in poll_calls)


@pytest.fixture(scope="module")
def frozen_inputs():
    saved = json.loads((EVIDENCE / "input-freeze.json").read_bytes())
    gold_raw = (EVIDENCE / "gold-pre-inference.json").read_bytes()
    assert sha(gold_raw) == saved["gold_file_sha256"]
    values = {}
    for item in saved["inputs"]:
        value = (EVIDENCE / item["path"]).read_bytes()
        assert sha(value) == item["sha256"] and len(value) == item["bytes"]
        values[Path(item["path"]).name] = value
    return json.loads(gold_raw), values, saved


def sandbox_python(code, *, timeout=10):
    """Only private Python and owned localhost/process probes; never remote networking."""
    return subprocess.run([str(runtime._SANDBOX), "-p", runtime._PROFILE,
                           str(runtime._PYTHON), "-I", "-B", "-c", code],
                          env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)


def test_real_os_deny_network_probe_and_private_stdlib_native_closure():
    manifest, pinned = runtime._installation(full=True)
    assert pinned == runtime._MANIFEST_HASH != LEGACY_M45_HASH
    assert len(manifest["models"]) == 3 and len(manifest["notices"]) == 91
    assert manifest["base_runtime_files"] and manifest["native_dependency_closure"]
    assert len(manifest["private_site_files"]) > len(manifest["runtime_files"])
    for item in manifest["base_runtime_files"]:
        runtime._checked_file(runtime._BASE_RUNTIME, item)
    runtime._sandbox_probe()  # Real OS denial, not a patched subprocess return code.
    code = '''import ctypes,json,os,ssl,sqlite3,socket,sys,zlib,_ssl,_sqlite3
import cv2,onnxruntime,pypdfium2,rapidocr
try:
 s=socket.socket(); denied=s.connect_ex(('127.0.0.1',1));s.close()
except OSError as e:denied=e.errno
dyld=ctypes.CDLL(None)
dyld._dyld_image_count.restype=ctypes.c_uint32
dyld._dyld_get_image_name.argtypes=[ctypes.c_uint32]
dyld._dyld_get_image_name.restype=ctypes.c_char_p
images=[dyld._dyld_get_image_name(i).decode() for i in range(dyld._dyld_image_count())]
print(json.dumps({'denied_errno':denied,'base_prefix':sys.base_prefix,'prefix':sys.prefix,
 'executable':sys.executable,'ssl':ssl.__file__,'sqlite':sqlite3.__file__,
 'extension_origins':{'_ssl':_ssl.__spec__.origin,'_sqlite3':_sqlite3.__spec__.origin},
 'stdlib_paths':[p for p in sys.path if p],'images':images}))
'''
    result = sandbox_python(code)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[:600]
    actual = json.loads(result.stdout)
    assert actual["denied_errno"] in {1, 13}, "Connection refusal is not OS denial"
    base = runtime._BASE_RUNTIME.resolve()
    venv = runtime._PYTHON.parent.parent.resolve()
    site = runtime._SITE.resolve()
    assert Path(actual["base_prefix"]).resolve() == base
    assert Path(actual["prefix"]).resolve() == venv
    for key in ["ssl", "sqlite"]:
        assert Path(actual[key]).resolve().is_relative_to(base)
    for origin in actual["extension_origins"].values():
        assert origin == "built-in" or Path(origin).resolve().is_relative_to(base), origin
    for path in actual["stdlib_paths"]:
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(base) or resolved.is_relative_to(venv), path
    foreign = [path for path in actual["images"]
               if not (path.startswith("/usr/lib/") or path.startswith("/System/Library/")
                       or Path(path).resolve().is_relative_to(base)
                       or Path(path).resolve().is_relative_to(site)
                       or Path(path).resolve().is_relative_to(venv))]
    assert not foreign, foreign
    record("real-os-and-private-closure", {"verification": "actual_private_process_and_OS_policy",
        "manifest_hash": pinned, "base_files": len(manifest["base_runtime_files"]),
        "audited_native_closure_entries": len(manifest["native_dependency_closure"]),
        "observed_native_images": actual["images"], "denied_errno": actual["denied_errno"],
        "extension_origins": actual["extension_origins"],
        "base_prefix": actual["base_prefix"], "quality_scope": "No OCR quality claim"})


def test_private_base_missing_and_symlink_are_blocked_without_mutating_install(monkeypatch, tmp_path):
    actual = runtime._BASE_RUNTIME
    missing = tmp_path / "absent-base"
    monkeypatch.setattr(runtime, "_BASE_RUNTIME", missing)
    with pytest.raises(HTTPException):
        runtime._installation(full=True)
    linked = tmp_path / "linked-base"
    linked.symlink_to(actual, target_is_directory=True)
    monkeypatch.setattr(runtime, "_BASE_RUNTIME", linked)
    with pytest.raises(HTTPException):
        runtime._installation(full=True)


def test_real_os_private_runtime_write_denial_and_writable_temporary_io(tmp_path):
    roots = [runtime._BASE.resolve(), runtime._PYTHON.parent.parent.resolve()]
    probes = [path / (".m49-private-write-probe-" + uuid4().hex) for path in roots]
    output = tmp_path / "allowed-temporary-output"
    code = "import json,pathlib\nresults=[]\n"
    for probe in probes:
        code += ("try:\n pathlib.Path(" + repr(str(probe)) + ").write_bytes(b'blocked-probe');results.append(0)\n"
                 "except OSError as error:results.append(error.errno)\n")
    code += "pathlib.Path(" + repr(str(output)) + ").write_bytes(b'temporary-ok')\nprint(json.dumps(results))"
    try:
        result = sandbox_python(code)
        assert result.returncode == 0, result.stderr.decode(errors="replace")[:600]
        denied = json.loads(result.stdout)
        assert denied == [1, 1], "Both fixed roots require actual OS EPERM"
        assert output.read_bytes() == b"temporary-ok"
        assert not any(path.exists() for path in probes)
    finally:
        for probe in probes:
            probe.unlink(missing_ok=True)
    record("real-os-private-runtime-readonly", {
        "verification": "actual_OS_file-write_denial_on_both_private_roots_and_allowed_temporary_IO",
        "manifest_hash": runtime._MANIFEST_HASH, "denied_errno": denied,
        "temporary_io_allowed": True, "root_files_added": 0})
def test_changed_manifest_rejected_without_launch(monkeypatch, tmp_path):
    changed = json.loads((runtime._BASE / "manifest.json").read_bytes())
    changed["base_runtime_files"][0]["sha256"] = "0" * 64
    (tmp_path / "manifest.json").write_bytes(runtime._canonical(changed))
    monkeypatch.setattr(runtime, "_BASE", tmp_path)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch changed install"))
    with pytest.raises(HTTPException):
        runtime.recognize(b"untrusted", expected_manifest_hash=runtime._MANIFEST_HASH)


def test_private_file_hash_and_parent_symlink_rejection(tmp_path):
    directory = tmp_path / "base"
    directory.mkdir()
    file = directory / "sealed.py"
    file.write_bytes(b"sealed")
    entry = {"path": "sealed.py", "sha256": sha(b"sealed")}
    runtime._checked_file(directory, entry)
    file.write_bytes(b"tampered")
    with pytest.raises(HTTPException):
        runtime._checked_file(directory, entry)
    file.write_bytes(b"sealed")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(directory, target_is_directory=True)
    with pytest.raises(HTTPException):
        runtime._checked_file(tmp_path, {"path": "linked-parent/sealed.py", "sha256": entry["sha256"]})
    with pytest.raises(HTTPException):
        runtime._checked_file(linked_parent, entry)


def test_full_private_site_rejects_unrecorded_data_format(monkeypatch):
    """Inject only a directory listing; never change the prepared runtime package."""
    native_rglob = Path.rglob
    class ExtraFile:
        def is_file(self): return True
        def relative_to(self, root): return Path("unrecorded-runtime-data.pickle")
    def listing(path, pattern):
        yield from native_rglob(path, pattern)
        if path == runtime._SITE:
            yield ExtraFile()
    monkeypatch.setattr(Path, "rglob", listing)
    with pytest.raises(HTTPException, match="私有依赖目录"):
        runtime._installation(full=True)


def test_environment_cannot_choose_runtime_profile_or_paths(monkeypatch):
    import importlib
    before = tuple(getattr(runtime, name) for name in ["_BASE", "_PYTHON", "_SITE", "_BASE_RUNTIME", "_MANIFEST_HASH"])
    for key in ["GA_OCR_RUNTIME", "GA_OCR_RUNTIME_PROFILE", "GA_OCR_PYTHON", "GA_OCR_MODEL_PATH", "GA_OCR_MANIFEST_HASH", "GA_OCR_RUNTIME_EPOCH"]:
        monkeypatch.setenv(key, "untrusted-client-choice")
    importlib.reload(runtime)
    after = tuple(getattr(runtime, name) for name in ["_BASE", "_PYTHON", "_SITE", "_BASE_RUNTIME", "_MANIFEST_HASH"])
    assert after == before and runtime._RUNTIME_EPOCH == "m49"


@pytest.mark.parametrize("page", [True, 0, -1, "1"])
def test_page_parameter_rejected_before_processing(page):
    with pytest.raises(HTTPException):
        runtime.render_pdf(b"source", page=page)


def test_legacy_hash_does_not_switch_default_profile_and_conflicting_aliases_block(monkeypatch):
    manifest = json.loads((runtime._BASE / "manifest.json").read_bytes())
    monkeypatch.setattr(runtime, "_installation", lambda: (manifest, runtime._MANIFEST_HASH))
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch mismatched hash"))
    with pytest.raises(HTTPException):
        runtime.recognize(b"image", expected_manifest_hash=LEGACY_M45_HASH)
    with pytest.raises(HTTPException):
        runtime.recognize(b"image", "a" * 64, expected_manifest_hash="b" * 64)


def upload(client, value, name):
    response = client.post("/api/setup/materials", files={"file": (name, value)})
    assert response.status_code == 201, response.text[:500]
    created = response.json()
    assert process_once(created["job_id"])
    response = client.get("/api/setup/materials/" + created["id"])
    assert response.status_code == 200, response.text[:500]
    material = response.json()
    assert material["status"] == "needs_input" and material["ocr"]["coverage_status"] == "needs_ocr"
    return material


def test_api_rejects_client_paths_profile_and_hash_override(client, frozen_inputs):
    _, inputs, _ = frozen_inputs
    material = upload(client, inputs["clear-scan.pdf"], "M49-参数拒绝.pdf")
    root = "/api/setup/materials/" + material["id"] + "/ocr"
    status = client.get("/api/setup/ocr/engines").json()["items"][0]
    assert status["ready"], status.get("reason")
    body = {"request_id": str(uuid4()), "expected_material_version": material["version"],
            "engine_id": status["id"], "engine_manifest_hash": status["manifest_hash"]}
    for key in ["python_path", "model_path", "runtime_profile", "runtime_epoch", "manifest_path", "config_path"]:
        response = client.post(root + "/start", json={**body, key: "/tmp/untrusted"})
        assert response.status_code == 422, key
    response = client.post(root + "/start", json={**body, "engine_manifest_hash": LEGACY_M45_HASH})
    assert response.status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MaterialOcrRun)) == 0


def normalized(value):
    return "".join(unicodedata.normalize("NFKC", value).upper().split())


def fresh_backend_readback(token, material_id, run_id, unit_id, raw_id):
    """Fresh interpreter, app and DB connection; rejects a non-test DB before API import."""
    code = '''import json,os,sys
from sqlalchemy.engine import make_url
assert make_url(os.environ['GA_DATABASE_URL']).database.startswith('ga_test_')
from generic_agents.config import settings
assert str(settings().storage_dir)==os.environ['GA_STORAGE_DIR']
from generic_agents import material_ocr_runtime as runtime
assert 'm48-safe-automation-v4' in runtime.__file__ and runtime._RUNTIME_EPOCH=='m49'
from fastapi.testclient import TestClient
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import MaterialOcrVersion
from sqlalchemy.orm import Session
value=json.load(sys.stdin)
prefix='/api/setup/materials/'+value['material_id']+'/ocr'
with TestClient(app) as client:
 client.headers['Authorization']='Bearer '+value['token']
 overview=client.get(prefix);assert overview.status_code==200
 material=client.get('/api/setup/materials/'+value['material_id']);assert material.status_code==200
 document=material.json()
 block=next(b for b in document['blocks'] if b.get('location',{}).get('ocr'))
 locator=block['location']['ocr']
 image=client.get(prefix+'/confirmations/'+locator['confirmation_id']+'/units/'+locator['unit_id']+'/image',params={'image_sha256':locator['image_sha256']})
 assert image.status_code==200
 history=client.get(prefix+'/units/'+value['unit_id']+'/versions',params={'run_id':value['run_id']});assert history.status_code==200
 with Session(engine()) as db:
  raw=db.get(MaterialOcrVersion,value['raw_id']);assert raw is not None
  result={'confirmation':overview.json()['confirmation'],'status':document['status'],
   'coverage_status':document['ocr']['coverage_status'],'block_text':block['text'],
   'image_sha256':__import__('hashlib').sha256(image.content).hexdigest(),
   'raw_revision_hash':raw.content_hash,'raw_receipt':raw.data['receipt'],'history_total':history.json()['total']}
 print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
'''
    environment = dict(os.environ)
    environment.update({"PYTHONPATH": str(Path(runtime.__file__).resolve().parents[1]),
                        "GA_STORAGE_DIR": str(settings().storage_dir),
                        "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), env=environment,
        input=runtime._canonical({"token": token, "material_id": material_id, "run_id": run_id,
                                  "unit_id": unit_id, "raw_id": raw_id}),
        capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-600:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("name,kind,page,body_index", [
    ("clear-scan.pdf", "pdf_page", 1, None),
    ("embedded-image.docx", "docx_image", None, 2),
])
def test_real_cpu_api_worker_save_confirm_reopen_and_durable_receipts(client, frozen_inputs, name, kind, page, body_index):
    gold, inputs, frozen = frozen_inputs
    began = time.monotonic()
    source = inputs[name]
    material = upload(client, source, "M49-" + name)
    prefix = "/api/setup/materials/" + material["id"] + "/ocr"
    installed = client.get("/api/setup/ocr/engines").json()["items"][0]
    assert installed["ready"], installed.get("reason")
    started = client.post(prefix + "/start", json={"request_id": str(uuid4()),
        "expected_material_version": material["version"], "engine_id": installed["id"],
        "engine_manifest_hash": installed["manifest_hash"]})
    assert started.status_code == 200, started.text[:500]
    run_id = started.json()["view"]["run"]["id"]
    assert process_once(started.json()["view"]["run"]["job_id"])
    with Session(engine()) as db:
        jobs = list(db.scalars(select(Job.id).where(Job.kind == "recognize_material_ocr_unit")))
    assert len(jobs) == 1
    assert process_once(jobs[0])
    overview = client.get(prefix).json()
    assert overview["run"]["status"] == "review", overview["run"].get("error")
    assert overview["run"]["source_sha256"] == sha(source)
    units = client.get(prefix + "/units", params={"run_id": run_id}).json()["items"]
    assert len(units) == 1
    unit = units[0]
    assert (unit["kind"], unit["page"], unit["body_index"]) == (kind, page, body_index)
    unit_url = prefix + "/units/" + unit["id"]
    detail = client.get(unit_url, params={"run_id": run_id}).json()
    raw = detail["raw"]
    assert raw and raw["regions"], "Real OCR returned no text"
    actual = normalized(raw["plain_text"])
    for token in gold["required_normalized_tokens"]:
        assert token in actual, {"missing": token, "actual": raw["plain_text"]}
    original = client.get(detail["original"]["url"])
    assert original.status_code == 200 and sha(original.content) == unit["image_sha256"]
    with Session(engine()) as db:
        persisted = db.get(MaterialOcrVersion, raw["id"])
        receipt = persisted.data["receipt"]
        engine_result = persisted.data["engine_result"]
        assert receipt["manifest_hash"] == installed["manifest_hash"]
        assert receipt["input_sha256"] == sha(original.content)
        assert receipt["result_sha256"] == digest(engine_result["regions"])
        assert receipt["device"] == "cpu" and receipt["network"] == "os_denied"
        assert not receipt.get("test_only") and not receipt.get("mock")
        assert receipt["verification_tier"] == "full_runtime_files_and_os_isolation_probe"
        assert 0 < engine_result["peak_rss_bytes"] < runtime._MAX_RESIDENT_BYTES
        source_unit = db.get(MaterialOcrUnit, unit["id"])
        render_receipt = source_unit.data["render"]["receipt"]
        expected_input = sha(source) if kind == "pdf_page" else sha(inputs["clear-synthetic.png"])
        assert render_receipt["input_sha256"] == expected_input
        assert render_receipt["output_sha256"] == unit["image_sha256"]
    save = {"request_id": str(uuid4()), "expected_material_version": detail["material_version"],
            "run_id": run_id, "expected_unit_version": unit["version"], "parent_hash": raw["hash"],
            "reason": "合成工程资料逐行核对；不代表行业质量", "regions": [
                {"id": r["id"], "text": r["text"] + (gold["human_saved_suffix"] if index == 0 else ""),
                 "box": r["box"], "source_region_id": r["id"]}
                for index, r in enumerate(raw["regions"])]}
    saved = client.put(unit_url, json=save)
    assert saved.status_code == 200, saved.text[:500]
    draft = saved.json()["unit"]
    assert draft["raw"]["hash"] == raw["hash"]
    assert gold["human_saved_suffix"] in draft["draft"]["plain_text"]
    probe = client.get(prefix + "/requests/" + save["request_id"]).json()
    assert probe["state"] == "saved" and probe["request"]["result"]["revision_hash"] == draft["draft"]["hash"]
    assert client.put(unit_url, json=save).json()["unit"]["draft"]["hash"] == draft["draft"]["hash"]
    confirmed = client.post(unit_url + "/confirm", json={"request_id": str(uuid4()),
        "expected_material_version": draft["material_version"], "run_id": run_id,
        "expected_unit_version": draft["unit"]["version"], "revision_hash": draft["draft"]["hash"],
        "reason": "原图与合成预冻文本均已核对"})
    assert confirmed.status_code == 200, confirmed.text[:500]
    overview = client.get(prefix).json()
    committed = client.post(prefix + "/confirm", json={"request_id": str(uuid4()),
        "expected_material_version": overview["material"]["version"], "run_id": run_id,
        "expected_run_version": overview["run"]["version"],
        "units_manifest_hash": overview["run"]["units_manifest_hash"], "reason": "合成资料完整保存确认"})
    assert committed.status_code == 200, committed.text[:500]
    token = client.post("/api/tokens").json()["token"]
    engine().dispose()  # Reconnect to committed DB rows; the storage fixture remains durable.
    with TestClient(app) as reopened:
        reopened.headers["Authorization"] = "Bearer " + token
        reloaded = reopened.get(prefix)
        assert reloaded.status_code == 200, reloaded.text[:500]
        restored = reloaded.json()
        assert restored["confirmation"] == committed.json()["view"]["confirmation"]
        final = reopened.get("/api/setup/materials/" + material["id"]).json()
        assert final["status"] == "parsed" and final["ocr"]["coverage_status"] == "complete"
        block = next(b for b in final["blocks"] if b.get("location", {}).get("ocr"))
        assert gold["human_saved_suffix"] in block["text"]
        locator = block["location"]["ocr"]
        retained = reopened.get(prefix + "/confirmations/" + locator["confirmation_id"]
            + "/units/" + locator["unit_id"] + "/image", params={"image_sha256": locator["image_sha256"]})
        assert retained.status_code == 200 and sha(retained.content) == locator["image_sha256"]
        citation = reopened.get(prefix + "/citations/" + block["id"], params={"quote": "M49 VERIFIED"})
        assert citation.status_code == 200 and citation.json()["block"] == block
        history = reopened.get(unit_url + "/versions", params={"run_id": run_id}).json()
        assert history["total"] == 3
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(MaterialOcrConfirmation)) == 1
        stored = db.get(MaterialOcrVersion, raw["id"])
        assert stored.content_hash == raw["hash"] and stored.data["receipt"] == receipt
    fresh = fresh_backend_readback(token, material["id"], run_id, unit["id"], raw["id"])
    assert fresh["confirmation"] == restored["confirmation"]
    assert fresh["status"] == "parsed" and fresh["coverage_status"] == "complete"
    assert fresh["raw_revision_hash"] == raw["hash"] and fresh["raw_receipt"] == receipt
    assert fresh["image_sha256"] == unit["image_sha256"] and fresh["history_total"] == 3
    assert gold["human_saved_suffix"] in fresh["block_text"]
    record("real-cpu-" + Path(name).suffix.lstrip("."), {
        "classification": "synthetic_engineering_fixture_not_industry_quality",
        "verification": "real_CPU_API_worker_save_confirm_DB_reconnect_TestClient_reopen_fresh_backend_process",
        "gold_file_sha256": frozen["gold_file_sha256"], "input_sha256": sha(source),
        "engine_manifest_hash": installed["manifest_hash"], "raw_text": raw["plain_text"],
        "required_tokens": gold["required_normalized_tokens"], "all_tokens_found": True,
        "image_sha256": unit["image_sha256"], "receipt": receipt,
        "raw_revision_hash": raw["hash"], "saved_revision_hash": draft["draft"]["hash"],
        "confirmation": restored["confirmation"], "cloud_calls": 0,
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "fresh_backend_process_readback": True,
        "limits": "Only clear synthetic fixtures; fresh backend interpreter/API proven, no OS restart/browser/industry-quality claim."})


def test_cpu_limit_source_contract_and_safe_real_kernel_signal():
    runner = (runtime._BASE / "runner.py").read_text()
    parsed = ast.parse(runner)
    pairs = [node for node in ast.walk(parsed) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "setrlimit"]
    assert any(len(node.args) == 2 and isinstance(node.args[0], ast.Attribute)
               and node.args[0].attr == "RLIMIT_CPU" and ast.literal_eval(node.args[1]) == (60, 65)
               for node in pairs)
    began = time.monotonic()
    result = sandbox_python("import resource\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\nresource.setrlimit(resource.RLIMIT_CPU,(1,2))\nwhile True: pass", timeout=8)
    assert result.returncode in {-signal.SIGXCPU, -signal.SIGKILL}, result.returncode
    record("safe-cpu-kernel-boundary", {"verification": "real_OS_RLIMIT_CPU_signal",
        "production_source_limit_seconds": [60, 65], "probe_lowered_limit_seconds": [1, 2],
        "returncode": result.returncode, "elapsed_seconds": round(time.monotonic() - began, 3),
        "scope": "Kernel behavior at a safely lowered limit; not a 60-second OCR exhaustion test."})


def still_running(pid):
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=2)
    return result.returncode == 0 and result.stdout.strip() and not result.stdout.strip().startswith("Z")


@pytest.mark.parametrize("boundary", ["timeout", "rss", "cancel"])
def test_injected_controller_boundary_kills_real_owned_process_group(monkeypatch, boundary):
    """Boundary-only injection replaces runner with tiny owned sleepers, never fake OCR."""
    manifest = json.loads((runtime._BASE / "manifest.json").read_bytes())
    monkeypatch.setattr(runtime, "_installation", lambda: (manifest, runtime._MANIFEST_HASH))
    native_popen = subprocess.Popen
    owned = {}
    code = """import os,time
child=os.fork()
if child==0:
 while True:time.sleep(1)
else:
 open('owned-child.pid','w').write(str(child))
 while True:time.sleep(1)
"""
    def launch(command, **kwargs):
        if str(runtime._BASE / "runner.py") not in [str(part) for part in command]:
            return native_popen(command, **kwargs)
        assert kwargs["start_new_session"] is True
        assert "PYTHONPATH" not in kwargs["env"] and "GA_OCR_RUNTIME_PROFILE" not in kwargs["env"]
        process = native_popen([str(runtime._SANDBOX), "-p", runtime._PROFILE,
                                str(runtime._PYTHON), "-I", "-B", "-c", code], **kwargs)
        owned["process"] = process
        marker = Path(kwargs["cwd"]) / "owned-child.pid"
        deadline = time.monotonic() + 3
        while not marker.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.is_file(), "Owned sleeper failed to start"
        owned["child"] = int(marker.read_text())
        assert os.getpgid(owned["child"]) == process.pid
        return process
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    if boundary == "timeout":
        monkeypatch.setattr(runtime, "_WALL_TIMEOUT_SECONDS", 0.01)
    if boundary == "rss":
        monkeypatch.setattr(runtime, "_resident_bytes", lambda process: runtime._MAX_RESIDENT_BYTES + 1)
    def guard():
        if boundary == "cancel" and "process" in owned:
            raise HTTPException(409, "M49 injected owned-group cancellation")
    began = time.monotonic()
    try:
        with pytest.raises(HTTPException) as failure:
            runtime.recognize(b"boundary-only-owned-input", expected_manifest_hash=runtime._MANIFEST_HASH, guard=guard)
        assert failure.value.status_code == 409
        expected_reason = {"timeout": "超时", "rss": "内存", "cancel": "injected owned-group cancellation"}[boundary]
        assert expected_reason in failure.value.detail
        assert owned["process"].poll() is not None
        deadline = time.monotonic() + 3
        while still_running(owned["child"]) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not still_running(owned["child"]), "Owned descendant survived cancellation/limit handling"
    finally:
        if "process" in owned:
            try:
                os.killpg(owned["process"].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            owned["process"].wait(timeout=3)
    record("injected-controller-" + boundary, {
        "verification": "injected_boundary_with_real_OS_owned_process_group_cleanup",
        "boundary": boundary, "controller_poll_seconds": 0.1,
        "wall_cap_seconds": 90, "resident_soft_cap_bytes": 1024 ** 3,
        "scope": "No real OCR, no 90-second stall, no 1GiB allocation; only controller error/cleanup path.",
        "parent_returncode": owned["process"].returncode,
        "owned_child_stopped": True, "elapsed_seconds": round(time.monotonic() - began, 3)})


def test_real_resident_sampler_only_on_owned_popen():
    process = subprocess.Popen([str(runtime._SANDBOX), "-p", runtime._PROFILE,
        str(runtime._PYTHON), "-I", "-B", "-c", "import time;time.sleep(4)"],
        env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        time.sleep(0.1)
        measured = runtime._resident_bytes(process)
        assert type(measured) is int and 0 < measured < runtime._MAX_RESIDENT_BYTES
        record("real-owned-rss-sampler", {"verification": "real_RSS_of_owned_private_Popen",
            "resident_bytes": measured, "cap_bytes": runtime._MAX_RESIDENT_BYTES,
            "scope": "Polling soft boundary; no claim of hard kernel RSS enforcement."})
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
