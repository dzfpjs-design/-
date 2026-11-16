"""Local, fixed OCR installation. No cloud fallback or automatic download.

The adapter selects a source-owned, fixed runtime epoch and accepts no executable,
model path or installation digest from the caller. Changed files block execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable

from fastapi import HTTPException
from .runtime_paths import local_runtime_root

# Literal epochs select package bytes; server configuration selects only the root.
_RUNTIME_PROFILES = {
    "m45": {"manifest_hash":"d242884f8972275fac53bc1e4dc1a8907e7bf252dea96081e7fa13dd2776942d"},
    "m49": {"manifest_hash":"55c902bbe51f2cdc9841e8571e9729f4c1a24c628601361a77019fbc7b1040e2"},
    "m49-low-memory-v1": {"manifest_hash":"3a11f677a07ca36d3a767379b00546aa41c1c47f2dc659b80344872e7c10d13f"},
    "m49-low-memory-v2": {"manifest_hash":"54e499dbd267250c20a8b6a0ccc1174fed792030283a3459b3d2c15084459eb8"},
}
_RUNTIME_EPOCH = "m49"
_MANIFEST_HASH = _RUNTIME_PROFILES[_RUNTIME_EPOCH]["manifest_hash"]
_ENGINE_ID = "rapidocr-3.10.0-ppocrv6-small-cpu-" + _RUNTIME_EPOCH
_NAME = "RapidOCR PP-OCRv6 small（本地CPU）"
_MAX_BYTES = 50 * 1024 * 1024
_SANDBOX = Path("/usr/bin/sandbox-exec")
_WALL_TIMEOUT_SECONDS = 90
_MAX_RESIDENT_BYTES = 1024 * 1024 * 1024
LOW_MEMORY_ENGINE_ID = "rapidocr-3.10.0-ppocrv6-small-cpu-m49-low-memory-v1"
LOW_MEMORY_MANIFEST_HASH = _RUNTIME_PROFILES["m49-low-memory-v1"]["manifest_hash"]
LOW_MEMORY_V2_ENGINE_ID = "rapidocr-3.10.0-ppocrv6-small-cpu-m49-low-memory-v2"
LOW_MEMORY_V2_MANIFEST_HASH = _RUNTIME_PROFILES["m49-low-memory-v2"]["manifest_hash"]


@dataclass(frozen=True)
class _RuntimeProfile:
    manifest_hash: str
    engine_id: str
    name: str
    base_name: str
    venv_name: str
    private_runtime: bool


_LOW_MEMORY_PROFILE = _RuntimeProfile(
    LOW_MEMORY_MANIFEST_HASH, LOW_MEMORY_ENGINE_ID,
    "RapidOCR PP-OCRv6 small（本地CPU，低内存逐条识别）",
    "ocr-m49-low-memory-v1", "venvs/ocr-m49", True)

_LOW_MEMORY_V2_PROFILE = _RuntimeProfile(
    LOW_MEMORY_V2_MANIFEST_HASH, LOW_MEMORY_V2_ENGINE_ID,
    "RapidOCR 本地识别（减少检测内存）",
    "ocr-m49-low-memory-v2", "venvs/ocr-m49", True)


def recognition_profile_pairs() -> tuple[tuple[str, str], ...]:
    """Registered historical choices, not a live default or caller registration."""
    return ((LOW_MEMORY_ENGINE_ID, LOW_MEMORY_MANIFEST_HASH),
            (LOW_MEMORY_V2_ENGINE_ID, LOW_MEMORY_V2_MANIFEST_HASH))


def _selected_profile(engine_hash: str | None = None) -> _RuntimeProfile:
    # Legacy globals stay the default, including explicit old test fixtures.
    # Every operation captures an immutable descriptor; no request changes them.
    if engine_hash is None or engine_hash == _MANIFEST_HASH:
        return _RuntimeProfile(_MANIFEST_HASH, _ENGINE_ID, _NAME,
                               "ocr-" + _RUNTIME_EPOCH, "venvs/ocr-" + _RUNTIME_EPOCH,
                               _RUNTIME_EPOCH == "m49")
    if type(engine_hash) is str and engine_hash == LOW_MEMORY_MANIFEST_HASH:
        return _LOW_MEMORY_PROFILE
    if type(engine_hash) is str and engine_hash == LOW_MEMORY_V2_MANIFEST_HASH:
        return _LOW_MEMORY_V2_PROFILE
    _blocked("任务冻结的引擎版本未登记，不能替换原识别授权")



@dataclass(frozen=True)
class _RuntimePaths:
    root: Path
    base: Path
    python: Path
    site: Path
    base_runtime: Path
    profile: str
    engine_hash: str | None = None


def _sandbox_policy(base: Path, venv: Path, private: bool) -> str:
    profile = "(version 1)(allow default)(deny network*)"
    if private:
        for readonly_root in (base, venv):
            profile += "(deny file-write* (subpath " + json.dumps(str(readonly_root), ensure_ascii=False) + "))"
    return profile


def _operation_paths(*, engine_hash: str | None = None) -> _RuntimePaths:
    # Resolve once per operation. The same immutable snapshot is checked/probed/run.
    selected = _selected_profile(engine_hash)
    root = local_runtime_root()
    base = root / selected.base_name
    venv = root / selected.venv_name
    profile = _sandbox_policy(base, venv, selected.private_runtime)
    # Six-position legacy construction retains its original equality/default.
    binding = selected.manifest_hash if selected in (_LOW_MEMORY_PROFILE, _LOW_MEMORY_V2_PROFILE) else None
    return _RuntimePaths(root, base, venv / "bin/python",
                         venv / "lib/python3.12/site-packages", venv / "base-python", profile, binding)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _blocked(message: str):
    raise HTTPException(409, "本地文字识别暂不可用：" + message)


def _checked_file(base: Path, entry: dict):
    relative = entry.get("path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        _blocked("安装清单路径异常")
    path = base / relative
    # A same-root symlink can still redirect a pinned import when another file
    # changes. Reject the base itself and every path component, not only the leaf.
    components = [base, *(base.joinpath(*Path(relative).parts[:index])
                          for index in range(1, len(Path(relative).parts) + 1))]
    if (any(component.is_symlink() for component in components) or not path.is_file()
        or not path.resolve().is_relative_to(base.resolve())):
        _blocked("缺少固定安装文件")
    if _sha(path.read_bytes()) != entry.get("sha256"):
        _blocked("固定安装文件校验失败，请管理员修复安装")


def _checked_tree(base: Path):
    # File-set equality alone ignores symlink directories, dangling links and
    # special entries. Inspect lstat without opening a FIFO/socket/device.
    if not stat.S_ISDIR(base.lstat().st_mode):
        _blocked("私有运行包根目录异常，已停止处理")
    for path in base.rglob("*"):
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            _blocked("私有运行包存在符号链接或特殊文件，已停止处理")


def _installation(*, full: bool = True, _paths: _RuntimePaths | None = None,
                  engine_hash: str | None = None) -> tuple[dict, str]:
    try:
        paths = (_operation_paths() if engine_hash is None else _operation_paths(engine_hash=engine_hash)) if _paths is None else _paths
        selected = _selected_profile(paths.engine_hash)
        if engine_hash is not None and _selected_profile(engine_hash) != selected:
            _blocked("固定引擎版本与所选运行包不一致")
        base, python, site, base_runtime = paths.base, paths.python, paths.site, paths.base_runtime
        if paths.profile != _sandbox_policy(base, python.parent.parent, selected.private_runtime):
            _blocked("固定运行包的断网与只读策略无法核对")
        if sys.platform != "darwin" or not _SANDBOX.is_file():
            _blocked("当前系统尚未安装经验证的断网运行环境")
        if not python.is_file() or not (base / "manifest.json").is_file():
            _blocked("未安装本地引擎及权重")
        if selected.private_runtime:
            # Before any file read or Python startup, including partial status.
            _checked_tree(base)
            _checked_tree(python.parent.parent)
        raw = (base / "manifest.json").read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            _blocked("安装清单超过上限")
        manifest = json.loads(raw)
        hashed = _sha(_canonical(manifest))
        if hashed != selected.manifest_hash or manifest.get("id") != selected.engine_id:
            _blocked("安装版本与已确认版本不一致")
        if selected.private_runtime:
            runtime_root=paths.root
            owned_venv=runtime_root/selected.venv_name
            if (base.is_symlink() or python.is_symlink() or site.is_symlink() or base_runtime.is_symlink()
                or python.parent.parent.resolve()!=owned_venv or python.resolve()!=owned_venv/"bin/python"
                or base.resolve()!=runtime_root/selected.base_name or base_runtime.resolve()!=owned_venv/"base-python"
                or site.resolve()!=owned_venv/"lib/python3.12/site-packages"):
                _blocked("私有运行包路径异常，不能使用共享解释器")
        if _sha(python.read_bytes()) != manifest["interpreter"]["sha256"] or _sha((python.parent.parent / "pyvenv.cfg").read_bytes()) != manifest["interpreter"]["venv_config_sha256"]:
            _blocked("固定解释器校验失败")
        if selected.private_runtime:
            if manifest["interpreter"].get("private_base")!="base-python" or manifest["interpreter"].get("private_base_required") is not True:
                _blocked("私有解释器声明不完整")
            _checked_file(python.parent.parent,{"path":"bin/python","sha256":manifest["interpreter"]["sha256"]})
            _checked_file(python.parent.parent,{"path":"pyvenv.cfg","sha256":manifest["interpreter"]["venv_config_sha256"]})
            # Base imports are outside venv site-packages and must remain pinned too.
            for entry in manifest["base_runtime_files"]:
                _checked_file(base_runtime,entry)
            actual_base={str(path.relative_to(base_runtime)) for path in base_runtime.rglob("*") if path.is_file()}
            if actual_base!={entry["path"] for entry in manifest["base_runtime_files"]}:
                _blocked("私有基础解释器存在未确认的新增或缺失文件")
        for entry in [manifest["runner"], manifest["config"], manifest["dictionary"], *manifest["models"]]:
            _checked_file(base, entry)
        if full:
            for entry in manifest["notices"]:
                _checked_file(base, entry)
            if selected.private_runtime:
                for entry in manifest["private_site_files"]:
                    _checked_file(site,entry)
                actual_private={str(path.relative_to(site)) for path in site.rglob("*") if path.is_file()}
                if actual_private!={entry["path"] for entry in manifest["private_site_files"]}:
                    _blocked("私有依赖目录存在未确认的新增或缺失文件")
            for entry in manifest["runtime_files"]:
                _checked_file(site, entry)
            actual = {str(path.relative_to(site)) for path in site.rglob("*")
                      if path.is_file() and "__pycache__" not in path.parts
                      and path.suffix in {".py", ".so", ".dylib", ".yaml", ".json", ".txt", ".pth"}}
            if actual != {entry["path"] for entry in manifest["runtime_files"]}:
                _blocked("运行目录存在未确认的新增或缺失文件")
        _sandbox_probe(_paths=paths)
        return manifest, hashed
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        _blocked("安装清单缺失或损坏")


def _sandbox_probe(*, _paths: _RuntimePaths | None = None):
    # A localhost connect must be denied by policy (EPERM/EACCES), rather than
    # merely failing because no server listens. Never perform a remote probe.
    code = "import socket,sys\ntry:\n s=socket.socket();n=s.connect_ex(('127.0.0.1',1));s.close()\nexcept OSError as e:n=e.errno\nsys.exit(0 if n in (1,13) else 3)"
    try:
        paths = _operation_paths() if _paths is None else _paths
        with tempfile.TemporaryDirectory(prefix="ocr-network-check-") as directory:
            # Partial status has pinned the complete base, but not site files.
            # -S keeps this stdlib-only network probe out of site startup hooks.
            result = subprocess.run([str(_SANDBOX), "-p", paths.profile, str(paths.python), "-I", "-B", "-S", "-c", code],
                                    cwd=directory, env={"PATH": "/usr/bin:/bin", "TMPDIR": directory},
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=5, check=False)
            if result.returncode != 0:
                _blocked("操作系统断网隔离未生效，已停止处理")
    except (OSError, subprocess.TimeoutExpired):
        _blocked("操作系统断网隔离不可用，已停止处理")


def _summary(manifest: dict, hashed: str) -> dict:
    selected = {"rapidocr", "onnxruntime", "pypdfium2", "numpy", "pillow", "opencv-python-headless"}
    return {"contract": manifest["contract"], "id": manifest["id"], "name": manifest["name"],
            "manifest_hash": hashed, "python": manifest["python"],
            "packages": [item for item in manifest["packages"] if item["name"].lower() in selected],
            "models": manifest["models"], "dictionary": manifest["dictionary"],
            "limits": manifest["limits"], "data_policy": manifest["data_policy"],
            "licenses": {"code": "Apache-2.0", "model": "Apache-2.0 (Baidu/PaddleOCR)",
                         "onnxruntime": "MIT", "pdfium": "BSD-3-Clause and retained third-party notices",
                         "notice_count": len(manifest["notices"]), "notice_index_hash": _sha(_canonical(manifest["notices"]))}}


def engine_status(*, engine_hash: str | None = None) -> dict:
    selected = _selected_profile(engine_hash)
    try:
        # Keep the no-argument legacy invocation shape for existing integrations.
        if engine_hash is None:
            manifest, hashed = _installation(full=False)
        else:
            paths = _operation_paths(engine_hash=engine_hash)
            manifest, hashed = _installation(full=False, _paths=paths)
        if hashed != selected.manifest_hash or manifest.get("id") != selected.engine_id:
            _blocked("安装版本与已确认版本不一致")
        summary = _summary(manifest, hashed)
        return {"id": selected.engine_id, "name": selected.name, "manifest_hash": hashed,
                "manifest": summary, "summary_hash": _sha(_canonical(summary)),
                "verification_tier": "core_files_and_os_isolation_probe; full_runtime_rechecked_before_execution",
                "ready": True, "reason": None}
    except HTTPException as error:
        return {"id": selected.engine_id, "name": selected.name, "manifest_hash": selected.manifest_hash,
                "manifest": None, "summary_hash": None, "verification_tier": "blocked",
                "ready": False, "reason": error.detail}


def status(*, engine_hash: str | None = None) -> dict:
    return engine_status() if engine_hash is None else engine_status(engine_hash=engine_hash)


def profiles() -> list[dict]:
    return [engine_status(), engine_status(engine_hash=LOW_MEMORY_MANIFEST_HASH)]


def _resident_bytes(process) -> int:
    """Observe only this owned child; polling is a soft RSS boundary, not a kernel cap."""
    try:
        result=subprocess.run(["/bin/ps","-p",str(process.pid),"-o","rss="],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            timeout=1,check=False,text=True)
        if process.poll() is not None:return 0
        if result.returncode or not result.stdout.strip().isdigit():
            _blocked("无法核对本地处理内存，已停止处理")
        return int(result.stdout.strip())*1024
    except (OSError,subprocess.TimeoutExpired):
        _blocked("本地处理内存监控不可用，已停止处理")


def _execute(content: bytes, action: str, *, engine_hash: str | None = None,
             page: int | None = None, guard: Callable[[], None] | None = None,
             _paths: _RuntimePaths | None = None) -> dict:
    if not isinstance(content, bytes) or not content or len(content) > _MAX_BYTES:
        _blocked("原件为空或超过50MiB处理上限")
    paths = (_operation_paths() if engine_hash is None else _operation_paths(engine_hash=engine_hash)) if _paths is None else _paths
    selected = _selected_profile(paths.engine_hash)
    if engine_hash is not None and _selected_profile(engine_hash) != selected:
        _blocked("固定引擎版本与所选运行包不一致")
    manifest, hashed = _installation(_paths=paths)
    if engine_hash is not None and engine_hash != hashed:
        _blocked("任务冻结的引擎版本与本地安装不一致")
    if guard:
        guard()
    with tempfile.TemporaryDirectory(prefix="material-ocr-") as temporary:
        directory = Path(temporary)
        input_path, png_path = directory / "source.bin", directory / "page.png"
        request_path, result_path = directory / "request.json", directory / "result.json"
        input_path.write_bytes(content)
        request_path.write_bytes(_canonical({"action": action, "input": str(input_path),
                                             "output_png": str(png_path), "page": page}))
        environment = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "TMPDIR": temporary,
                       "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "2"}
        command = [str(_SANDBOX), "-p", paths.profile, str(paths.python), "-I", "-B",
                   str(paths.base / "runner.py"), str(request_path), str(result_path)]
        process = None
        try:
            with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
                process = subprocess.Popen(command, cwd=temporary, env=environment,
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                           start_new_session=True)
                start = time.monotonic()
                while process.poll() is None:
                    if guard:
                        guard()
                    if selected.private_runtime and _resident_bytes(process)>_MAX_RESIDENT_BYTES:
                        _blocked("本地处理内存超过上限，可拆分页面后重试")
                    if time.monotonic() - start > _WALL_TIMEOUT_SECONDS:
                        _blocked("处理超时，可稍后重试或拆分页面")
                    time.sleep(0.1)
            if not result_path.is_file() or result_path.stat().st_size > 2 * 1024 * 1024:
                _blocked("本地隔离运行失败或返回结果超过上限")
            answer = json.loads(result_path.read_bytes())
            if process.returncode != 0 or not answer.get("ok"):
                _blocked(str(answer.get("error", "本地引擎执行失败"))[:500])
            result = answer["result"]
            if selected.private_runtime:
                peak = result.get("peak_rss_bytes") if isinstance(result, dict) else None
                if type(peak) is not int or peak <= 0:
                    _blocked("本地处理峰值内存无法核对，原结果未接收")
                if peak > _MAX_RESIDENT_BYTES:
                    _blocked("本地处理内存超过上限，可拆分页面后重试")
            receipt = result["receipt"]
            if receipt.get("manifest_hash") != hashed or receipt.get("input_sha256") != _sha(content):
                _blocked("引擎回执与本次原件不一致")
            receipt["manifest"] = _summary(manifest, hashed)
            receipt["summary_hash"] = _sha(_canonical(receipt["manifest"]))
            receipt["verification_tier"] = "full_runtime_files_and_os_isolation_probe"
            if action in {"normalize", "render"}:
                png = png_path.read_bytes()
                if len(png) > _MAX_BYTES or _sha(png) != receipt.get("output_sha256"):
                    _blocked("页面图片与引擎回执不一致")
                result["png"] = png
            if action == "recognize" and _sha(_canonical(result["regions"])) != receipt.get("result_sha256"):
                _blocked("文字结果与引擎回执不一致")
            if guard:
                guard()
            return result
        except HTTPException:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            _blocked("本地引擎未完成有效处理")
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


def normalize_image(content: bytes, *, guard: Callable[[], None] | None = None,
                    expected_manifest_hash: str | None = None) -> dict:
    if expected_manifest_hash is None:
        return _execute(content, "normalize", guard=guard)
    return _execute(content, "normalize", engine_hash=expected_manifest_hash, guard=guard)


def pdf_info(content: bytes, *, guard: Callable[[], None] | None = None,
             expected_manifest_hash: str | None = None) -> dict:
    if expected_manifest_hash is None:
        return _execute(content, "pdf_info", guard=guard)
    return _execute(content, "pdf_info", engine_hash=expected_manifest_hash, guard=guard)


def render_pdf(content: bytes, page: int | None = None, *, guard: Callable[[], None] | None = None,
               expected_manifest_hash: str | None = None) -> list[dict]:
    """Production callers pass a one-based page and checkpoint it individually."""
    if page is None:
        paths = _operation_paths() if expected_manifest_hash is None else _operation_paths(engine_hash=expected_manifest_hash)
        count = _execute(content, "pdf_info", guard=guard, _paths=paths)["page_count"]
        if count > 4:
            _blocked("请逐页处理此PDF，避免同时加载全部页面")
        return [_execute(content, "render", page=number, guard=guard, _paths=paths) for number in range(1, count + 1)]
    if type(page) is not int or page < 1:
        _blocked("PDF页码必须为正整数")
    if expected_manifest_hash is None:
        return [_execute(content, "render", page=page, guard=guard)]
    return [_execute(content, "render", page=page, engine_hash=expected_manifest_hash, guard=guard)]


def recognize(content: bytes, engine_hash: str | None = None, *, expected_manifest_hash: str | None = None,
              guard: Callable[[], None] | None = None) -> dict:
    if expected_manifest_hash is not None:
        if engine_hash is not None and engine_hash != expected_manifest_hash:
            _blocked("引擎版本参数不一致")
        engine_hash = expected_manifest_hash
    if not isinstance(engine_hash, str) or len(engine_hash) != 64:
        _blocked("缺少任务确认的固定引擎版本")
    return _execute(content, "recognize", engine_hash=engine_hash, guard=guard)
