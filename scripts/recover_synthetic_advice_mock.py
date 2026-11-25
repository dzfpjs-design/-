#!/usr/bin/env python3
"""No-send recovery of a previously stopped synthetic mock run only."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile


def private_dir(path, prefix):
    if not path.is_absolute() or path.is_symlink() or path.parent.resolve() != Path(tempfile.gettempdir()).resolve():
        raise RuntimeError('invalid synthetic directory')
    info = path.lstat()
    if not path.name.startswith(prefix) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('invalid private synthetic directory')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-root', required=True)
    parser.add_argument('--pg-bin', required=True)
    args=parser.parse_args()
    root=Path(args.run_root); private_dir(root,'advice-race-pg-')
    status_file=root/'status.json'; info=status_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:
        raise RuntimeError('invalid synthetic state file')
    state=json.loads(status_file.read_text())
    if state.get('schema')!='isolated-synthetic-advice-mock-v1' or not re.fullmatch(r'ga_test_[0-9a-f]{12}',state.get('database','')):
        raise RuntimeError('invalid synthetic database')
    evidence=Path(state['storage']); spool=Path(state['spool'])
    private_dir(evidence,'ga-test-evidence-'); private_dir(spool,'advice-v2-response-spool-')
    # Incomplete responses cannot be recovered; never call a sender here.
    if not (root/'receipt_ref.json').is_file() or not (root/'fake_request.json').is_file():
        raise RuntimeError('original fake response references unavailable; leave for review')
    pg=Path(args.pg_bin).resolve(strict=True); data=root/'data'
    if data.is_symlink() or not data.is_dir(): raise RuntimeError('invalid synthetic PostgreSQL data')
    env={key:os.environ[key] for key in ('TMPDIR','LANG','LC_ALL') if key in os.environ}
    env['PATH']=str(pg)+':/usr/bin:/bin:/usr/sbin:/sbin'; env['LC_ALL']='C'
    if subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env).returncode==0:
        raise RuntimeError('synthetic PostgreSQL already running; do not attach')
    repo=Path(__file__).resolve().parents[1]
    if os.path.lexists(repo/'.env'):
        raise RuntimeError('run only in a clean isolated worktree without .env')
    control=subprocess.check_output([str(pg/'pg_controldata'),str(data)],text=True,env=env,timeout=30)
    match=re.search(r'^Database system identifier:\s*(\d+)\s*$',control,re.MULTILINE)
    if not match or (state.get('system_identifier') and state['system_identifier']!=match.group(1)):
        raise RuntimeError('synthetic database identity mismatch')
    sid=match.group(1); sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),'-o',f'-h 127.0.0.1 -p {port} -k {root}','-w','start'],
        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,env=env)
    try:
        env.update(GA_TEST_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{port}/postgres',
            GA_DATABASE_URL=f"postgresql+psycopg://advice_test@127.0.0.1:{port}/{state['database']}",
            GA_TEST_PG_DATA_DIR=str(data),GA_TEST_PG_SYSTEM_IDENTIFIER=sid,GA_STORAGE_DIR=str(evidence),
            GA_SYNTHETIC_RUN_ROOT=str(root),GA_SYNTHETIC_RUN_ID=state['run_id'],GA_V2_RESPONSE_SPOOL_ROOT=str(spool),
            GA_V2_RESPONSE_SPOOL_ENABLED='true',GA_CLOUD_ALLOWED_PROVIDERS='',DEEPSEEK_API_KEY='',DASHSCOPE_API_KEY='',
            GA_REDIS_URL='redis://127.0.0.1:1/0',PYTHONPATH=str(repo/'src')+':'+str(repo))
        profile=f'(version 1) (allow default) (deny network*) (allow network-outbound (remote ip "localhost:{port}"))'
        log_fd=os.open(root/'manual-recovery.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        log_info=os.fstat(log_fd)
        if not stat.S_ISREG(log_info.st_mode) or log_info.st_uid!=os.getuid() or log_info.st_nlink!=1 or log_info.st_mode&0o077:
            os.close(log_fd); raise RuntimeError('invalid synthetic recovery log')
        with os.fdopen(log_fd,'a') as log:
            result=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-m','tests.synthetic_advice_mock_verify'],
                cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
        if result.returncode: raise RuntimeError('synthetic recovery not verified; originals retained')
        print('Synthetic response recovered and verified; no network send occurred.')
        return 0
    finally:
        subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,env=env)


if __name__=='__main__':sys.exit(main())
