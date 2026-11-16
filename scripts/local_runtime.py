"""Manage only this project's API/worker processes. Data and credentials are untouched."""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from runtime_source import service_environment

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
RUNTIME = ROOT / ".runtime"
RUNTIME.mkdir(exist_ok=True, mode=0o700)


def services(mode):
    # Only explicitly approved MQTT sources are read; no device commands.
    if mode == "setup":
        return [("api", "serve"), ("setup-worker", "setup-worker"), ("media-worker", "media-worker"), ("mqtt-worker", "mqtt-worker")]
    if mode == "all":
        return [("api", "serve"), ("worker", "worker"), ("setup-worker", "setup-worker"), ("media-worker", "media-worker"), ("mqtt-worker", "mqtt-worker")]
    return [("api", "serve"), ("worker", "worker")]


def owned(pid, command):
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    text = result.stdout
    return str(ROOT) in text and ("generic_agents.cli " + command in text or ".venv/bin/ga " + command in text)


def stop(name, command):
    path = RUNTIME / (name + ".pid")
    if not path.exists():
        return
    pid = int(path.read_text())
    if not owned(pid, command):
        print(name, "PID记录失效，未操作其他进程"); path.unlink(); return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not owned(pid, command):
            break
        time.sleep(.1)
    else:
        if owned(pid, command):
            os.kill(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)


def start(name, command):
    path = RUNTIME / (name + ".pid")
    if path.exists() and owned(int(path.read_text()), command):
        if name == "api":
            from generic_agents.runtime_readiness import wait_for_api
            wait_for_api(lambda: owned(int(path.read_text()), command))
        print(name, "已运行，接口与数据库已就绪" if name == "api" else "已运行"); return
    log_path = RUNTIME / (name + ".log")
    service_env = service_environment(ROOT, os.environ)
    with log_path.open("ab", buffering=0) as log:
        log_path.chmod(0o600)
        process = subprocess.Popen([sys.executable, "-m", "generic_agents.cli", command],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd=ROOT,
            env=service_env)
    path.write_text(str(process.pid))
    if name == "api":
        from generic_agents.runtime_readiness import wait_for_api
        wait_for_api(lambda: process.poll() is None)
    print(name, "已启动，接口与数据库已就绪" if name == "api" else "已启动")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--mode", choices=["setup", "legacy", "all"], default="setup",
                        help="setup启动配置、本地分析、持续录制与已授权传感数据接收，不启用原设备策略；legacy显式启用原业务worker；all同时管理所有服务")
    args = parser.parse_args()
    for name, command in services(args.mode):
        if args.action in {"stop", "restart"}:
            stop(name, command)
        if args.action in {"start", "restart"}:
            start(name, command)
        if args.action == "status":
            path = RUNTIME / (name + ".pid")
            print(name, "运行中" if path.exists() and owned(int(path.read_text()), command) else "未运行")
