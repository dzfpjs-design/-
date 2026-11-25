#!/usr/bin/env python3
"""Run a single fake-provider mature advice chain in a retained private PG.

This command has no real-provider mode. It creates a new local PG cluster and
private data directories, uses a sandbox denying external network, and retains
all files for inspection. It must not be pointed at an application cluster.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import uuid


def call(args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)


def metadata(root, value):
    temporary = root / 'status.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', closefd=False) as out:
            json.dump(value, out, sort_keys=True); out.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, root / 'status.json')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True, help='private generated synthetic checkpoint')
    parser.add_argument('--pg-bin', required=True, help='directory with initdb, pg_ctl and psql')
    args = parser.parse_args()
    pg = Path(args.pg_bin).resolve(strict=True)
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_absolute() or not checkpoint.is_dir() or checkpoint.is_symlink():
        raise RuntimeError('invalid synthetic checkpoint')
    if not Path('/usr/bin/sandbox-exec').is_file():
        raise RuntimeError('this offline harness requires macOS sandbox-exec')
    repo = Path(__file__).resolve().parents[1]
    root = Path(tempfile.mkdtemp(prefix='advice-race-pg-'))
    evidence = Path(tempfile.mkdtemp(prefix='ga-test-evidence-'))
    spool = Path(tempfile.mkdtemp(prefix='advice-v2-response-spool-'))
    for path in (root, evidence, spool): os.chmod(path, 0o700)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    # Neither the generated request ID nor the cluster is reused on reruns.
    run_id = str(uuid.uuid4()); database = 'ga_test_' + uuid.uuid4().hex[:12]
    status = {'schema':'isolated-synthetic-advice-mock-v1','state':'initializing',
        'run_id':run_id,'database':database,'port':port,'storage':str(evidence),'spool':str(spool)}
    metadata(root, status)
    print('Private synthetic run created; status directory:', root, flush=True)
    data = root / 'data'; running = False
    try:
        call([pg/'initdb', '-D', data, '-U', 'advice_test', '-A', 'trust', '--no-locale', '-E', 'UTF8'],
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        def start():
            call([pg/'pg_ctl','-D',data,'-l',root/'postgres.log','-o',f'-h 127.0.0.1 -p {port} -k {root}','-w','start'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        def stop():
            call([pg/'pg_ctl','-D',data,'-m','fast','-w','stop'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        start(); running = True
        psql = [pg/'psql','-h','127.0.0.1','-p',str(port),'-U','advice_test','-d','postgres']
        sid = subprocess.check_output([str(a) for a in psql]+['-Atc','select system_identifier from pg_control_system()'], text=True).strip()
        status['system_identifier'] = sid
        metadata(root, status)
        call(psql + ['-v','ON_ERROR_STOP=1','-c',f'CREATE DATABASE "{database}"'], stdout=subprocess.DEVNULL)
        environment = os.environ.copy()
        environment.update(GA_TEST_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{port}/postgres',
            GA_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{port}/{database}',
            GA_TEST_PG_DATA_DIR=str(data), GA_TEST_PG_SYSTEM_IDENTIFIER=sid, GA_STORAGE_DIR=str(evidence),
            GA_SYNTHETIC_FRESH_CHECKPOINT=str(checkpoint), GA_SYNTHETIC_RUN_ROOT=str(root),
            GA_SYNTHETIC_RUN_ID=run_id, GA_SYNTHETIC_SPOOL=str(spool), GA_V2_RESPONSE_SPOOL_ROOT=str(spool),
            GA_V2_RESPONSE_SPOOL_ENABLED='true', GA_COOKIE_SECURE='false', GA_CLOUD_ALLOWED_PROVIDERS='',
            GA_REDIS_URL='redis://127.0.0.1:1/0', DEEPSEEK_API_KEY='', DASHSCOPE_API_KEY='',
            PATH=str(pg)+':'+os.environ.get('PATH',''), PYTHONPATH=str(repo/'src')+':'+str(repo))
        profile = f'(version 1) (allow default) (deny network*) (allow network-outbound (remote ip "localhost:{port}"))'
        def child(module, logfile):
            with (root/logfile).open('w') as log:
                os.chmod(root/logfile, 0o600)
                return subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-m',module],
                    cwd=repo, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=2400).returncode
        status['state']='running_fake_provider'; metadata(root,status)
        if child('tests.synthetic_advice_mock_bootstrap','flow.log') != 0:
            status['state']='failed_preserved_no_retry'; metadata(root,status); return 1
        # Stop and restart the dedicated PostgreSQL process, not just a Session.
        stop(); running = False
        start(); running = True
        status['state']='checking_after_database_restart'; metadata(root,status)
        if child('tests.synthetic_advice_mock_verify','verify.log') != 0:
            status['state']='recovery_unverified_no_retry'; metadata(root,status); return 1
        status['state']='mock_passed'; metadata(root,status)
        print('Synthetic mock run passed; private data retained.', flush=True)
        return 0
    except BaseException:
        status['state']='interrupted_preserved_no_retry'; metadata(root,status)
        raise
    finally:
        if running:
            try: stop()
            except Exception: pass


if __name__ == '__main__':
    sys.exit(main())
