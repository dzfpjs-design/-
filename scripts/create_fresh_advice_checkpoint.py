#!/usr/bin/env python3
"""Create one new synthetic maturity checkpoint in an isolated local PG.

No real account, external service, provider key or existing workspace is read.
Both the generated checkpoint and a stopped diagnostic cluster are retained.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time


def write_status(root, value):
    path = root / 'status.json'
    temp = root / 'status.tmp'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def free_port(excluded):
    for _ in range(100):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port not in excluded and port not in {8766, 55486}:
            return port
    raise RuntimeError('no isolated port')


def stop_test_group(process):
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: return True
    except OSError: return False
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: pass
    except OSError: return False
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: return True
    except OSError: return False
    try:
        if process.poll() is None: process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired): return False
    deadline = time.monotonic() + 5
    while True:
        try: os.killpg(process.pid, 0)
        except ProcessLookupError: return True
        except OSError: return False
        if time.monotonic() >= deadline: return False
        time.sleep(0.05)


def run(pg, pytest_bin, timeout, preflight=False, preflight_fail=False, preflight_bad_system=False):
    pg, pytest_bin = Path(pg).resolve(), Path(pytest_bin).resolve()
    repo = Path(__file__).resolve().parents[1]
    if ((repo / '.env').exists() or not Path('/usr/bin/sandbox-exec').is_file()
            or not all((pg / name).is_file() for name in ('initdb', 'pg_ctl', 'psql'))
            or not pytest_bin.is_file()):
        raise RuntimeError('isolated runtime requirements not met')
    root = Path(tempfile.mkdtemp(prefix='advice-race-pg-')).resolve()
    checkpoint = Path(tempfile.mkdtemp(prefix='advice-fresh-mature-checkpoint-')).resolve()
    os.chmod(root, 0o700); os.chmod(checkpoint, 0o700)
    marker = root / 'retain-synthetic-run.json'
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'schema': 'isolated-advice-retention-v1'}, out)
        out.flush(); os.fsync(out.fileno())
    data = root / 'data'
    pg_port = free_port(set())
    gateway_port = free_port({pg_port})
    env = {k: os.environ[k] for k in ('HOME', 'TMPDIR', 'LANG', 'LC_ALL') if k in os.environ}
    env.update(PATH=str(pg) + ':/usr/bin:/bin:/usr/sbin:/sbin', DEEPSEEK_API_KEY='', DASHSCOPE_API_KEY='')
    started = False
    status = {'state': 'initializing', 'checkpoint': str(checkpoint), 'cluster_stopped': False,
        'test_processes_stopped': True}
    write_status(root, status)
    print('Synthetic seed run directory:', root, flush=True)
    try:
        subprocess.run([str(pg / 'initdb'), '-D', str(data), '-U', 'advice_test', '-A', 'trust',
            '--no-locale', '-E', 'UTF8'], env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True, timeout=60)
        subprocess.run([str(pg / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'), '-o',
            f'-h 127.0.0.1 -p {pg_port} -k {root}', '-w', 'start'], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=60)
        started = True
        sid = subprocess.check_output([str(pg / 'psql'), '-h', '127.0.0.1', '-p', str(pg_port),
            '-U', 'advice_test', '-d', 'postgres', '-Atc', 'select system_identifier from pg_control_system()'],
            env=env, text=True, timeout=15).strip()
        env.update(GA_TEST_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{pg_port}/postgres',
            GA_TEST_PG_DATA_DIR=str(data), GA_TEST_PG_SYSTEM_IDENTIFIER=sid,
            GA_TEST_RETAIN_SYNTHETIC_CLUSTER='1',
            GA_TEST_SYNTHETIC_GATEWAY_PORT=str(gateway_port), GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT=str(checkpoint),
            GA_COOKIE_SECURE='false', GA_CLOUD_ALLOWED_PROVIDERS='', GA_REDIS_URL='redis://127.0.0.1:1/0',
            PYTHONPATH=str(repo / 'src') + os.pathsep + str(repo))
        profile = f'''(version 1) (allow default) (deny network*)
          (allow network-inbound (local ip "localhost:{gateway_port}"))
          (allow network-outbound (remote ip "localhost:{pg_port}"))
          (allow network-outbound (remote ip "localhost:{gateway_port}"))'''
        if preflight_fail:
            env['GA_TEST_SYNTHETIC_PREFLIGHT_FAIL'] = '1'
        if preflight_bad_system:
            env['GA_TEST_PG_SYSTEM_IDENTIFIER'] = '0'
        status['state'] = 'running'; write_status(root, status)
        began = time.monotonic()
        fd = os.open(root / 'test.log', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as out:
            proc = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', profile, str(pytest_bin), '-q', '-s',
                'tests/test_synthetic_checkpoint_seed_preflight_pg.py' if preflight else
                'tests/test_same_goal_advice_offline_fresh_checkpoint_pg.py'], cwd=repo, env=env,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            status['test_processes_stopped'] = False
            try:
                code = proc.wait(timeout=timeout)
                status.update(state='passed' if code == 0 else 'failed', exit_code=code)
            except subprocess.TimeoutExpired:
                status['state'] = 'timeout'
            finally:
                status['test_processes_stopped'] = stop_test_group(proc)
                if not status['test_processes_stopped'] and status['state'] == 'passed':
                    status['state'] = 'process_cleanup_unconfirmed'
        status['elapsed_seconds'] = round(time.monotonic() - began)
    finally:
        if started:
            result = subprocess.run([str(pg / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            status['cluster_stopped'] = result.returncode == 0
        else:
            status['cluster_stopped'] = True
        write_status(root, status)
    return 0 if status['state'] == 'passed' and status['cluster_stopped'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pg-bin', required=True)
    parser.add_argument('--pytest', required=True)
    parser.add_argument('--timeout', type=int, default=2400)
    parser.add_argument('--preflight', action='store_true', help='check save guard with only synthetic records')
    parser.add_argument('--preflight-fail', action='store_true', help='after saving, deliberately fail to test retention')
    parser.add_argument('--preflight-bad-system', action='store_true', help='deliberately test PG identity rejection')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 7200 or ((args.preflight_fail or args.preflight_bad_system) and not args.preflight):
        parser.error('invalid timeout')
    return run(args.pg_bin, args.pytest, args.timeout, args.preflight, args.preflight_fail, args.preflight_bad_system)


if __name__ == '__main__':
    raise SystemExit(main())
