#!/usr/bin/env python3
"""Run one isolated synthetic checkpoint test and retain its stopped PG.

The runner never connects to an existing cluster or external network. It never
replays an application. Retained directories require explicit --cleanup.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

SCHEMA = 'isolated-advice-retention-v1'


def _write(path, value):
    tmp=path.with_suffix('.tmp')
    fd=os.open(tmp, os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as out:
        json.dump(value,out);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)


def private_root():
    root=Path(tempfile.mkdtemp(prefix='advice-race-pg-'))
    os.chmod(root,0o700)
    _write(root/'retain-synthetic-run.json',{'schema':SCHEMA})
    return root


def cleanup(root):
    root=Path(root)
    if not root.is_absolute() or root.is_symlink():raise ValueError('invalid retention directory')
    root=root.resolve()
    if root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('advice-race-pg-'):
        raise ValueError('invalid retention directory')
    marker=root/'retain-synthetic-run.json';status=root/'status.json'
    if (not marker.is_file() or marker.is_symlink() or not status.is_file() or status.is_symlink()
        or root.stat().st_uid!=os.getuid() or json.loads(marker.read_text())!={'schema':SCHEMA}):
        raise ValueError('cannot prove the synthetic cluster is stopped')
    saved_status=json.loads(status.read_text())
    processes_stopped=saved_status.get('test_processes_stopped')
    if (saved_status.get('cluster_stopped') is not True
        or (processes_stopped is not None and processes_stopped is not True)
        or (root/'data'/'postmaster.pid').exists()):
        raise ValueError('cannot prove the synthetic cluster is stopped')
    shutil.rmtree(root)


def _port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def _stop_test_process_group(process):
    """Stop our session and confirm its process group has disappeared."""
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:return True
    except OSError:return False
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:pass
    except OSError:return False
    # A child may outlive its pytest parent after any exit, not just timeout.
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:return True
    except OSError:return False
    try:
        if process.poll() is None:process.wait(timeout=10)
    except (OSError,subprocess.TimeoutExpired):return False
    deadline=time.monotonic()+5
    while True:
        try:os.killpg(process.pid,0)
        except ProcessLookupError:return True
        except OSError:return False
        if time.monotonic()>=deadline:return False
        time.sleep(0.05)


def run(pg,pytest_bin,checkpoint,timeout,retention_probe=False):
    pg=Path(pg).resolve();pytest_bin=Path(pytest_bin).resolve();checkpoint=Path(checkpoint).resolve()
    repo=Path(__file__).resolve().parents[1]
    if (not (pg/'initdb').is_file() or not (pg/'pg_ctl').is_file() or not (pg/'psql').is_file()
        or not pytest_bin.is_file() or not checkpoint.is_dir() or (repo/'.env').exists()
        or not Path('/usr/bin/sandbox-exec').is_file()):
        raise RuntimeError('isolated runtime requirements not met')
    root=private_root();data=root/'data';port,provider_port,http_port=_port(),_port(),_port()
    if len({port,provider_port,http_port})!=3:raise RuntimeError('could not reserve distinct ports')
    base={key:os.environ[key] for key in ('PATH','HOME','TMPDIR','LANG','LC_ALL') if key in os.environ}
    base.update(DEEPSEEK_API_KEY='',DASHSCOPE_API_KEY='')
    start_attempted=False;test_processes_stopped=True;outcome='error';exit_code=None;began=time.monotonic()
    _write(root/'status.json',{'state':'initializing','cluster_stopped':False})
    print('Synthetic run directory:',root,flush=True)
    try:
        subprocess.run([str(pg/'initdb'),'-D',str(data),'-U','advice_test','-A','trust','--no-locale','-E','UTF8'],
            env=base,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=60)
        start_attempted=True
        subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),'-o',
            f'-h 127.0.0.1 -p {port} -k {root}','-w','start'],env=base,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=60)
        sid=subprocess.check_output([str(pg/'psql'),'-h','127.0.0.1','-p',str(port),'-U','advice_test',
            '-d','postgres','-Atc','select system_identifier from pg_control_system()'],env=base,text=True,timeout=15).strip()
        env=dict(base)
        env.update(GA_TEST_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{port}/postgres',
            GA_TEST_PG_DATA_DIR=str(data),GA_TEST_PG_SYSTEM_IDENTIFIER=sid,GA_TEST_RETAIN_SYNTHETIC_CLUSTER='1',
            GA_TEST_SYNTHETIC_PROVIDER_PORT=str(provider_port),GA_TEST_SYNTHETIC_HTTP_PORT=str(http_port),
            GA_TEST_SYNTHETIC_REQUEST_STATS=str(root/'provider-stats.json'),GA_TEST_TIMING_LOG=str(root/'timings.jsonl'),
            GA_SYNTHETIC_FRESH_CHECKPOINT=str(checkpoint),GA_COOKIE_SECURE='false',GA_REDIS_URL='redis://127.0.0.1:1/0',
            PYTHONPATH=str(repo/'src')+os.pathsep+str(repo))
        if retention_probe:env['GA_TEST_RETENTION_PROBE']='1'
        profile=f'''(version 1) (allow default) (deny network*)
          (allow network-inbound (local ip "localhost:{provider_port}"))
          (allow network-inbound (local ip "localhost:{http_port}"))
          (allow network-outbound (remote ip "localhost:{port}"))
          (allow network-outbound (remote ip "localhost:{provider_port}"))
          (allow network-outbound (remote ip "localhost:{http_port}"))'''
        _write(root/'status.json',{'state':'running','cluster_stopped':False})
        fd=os.open(root/'test.log',os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out:
            process=subprocess.Popen(['/usr/bin/sandbox-exec','-p',profile,str(pytest_bin),'-q',
                    'tests/test_synthetic_retention_probe_pg.py' if retention_probe else
                    'tests/test_advice_user_real_http_checkpoint_pg.py'],cwd=repo,env=env,
                    stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            test_processes_stopped=False
            try:
                exit_code=process.wait(timeout=timeout);outcome='passed' if exit_code==0 else 'failed'
            except subprocess.TimeoutExpired:
                outcome='timeout'
            finally:
                # A worker's sending state may be unknown; stop it and never restart it.
                test_processes_stopped=_stop_test_process_group(process)
                if not test_processes_stopped and outcome=='passed':outcome='process_cleanup_unconfirmed'
    finally:
        stopped=not start_attempted
        if start_attempted:
            try:
                result=subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],env=base,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
                stopped=result.returncode==0
            except (OSError,subprocess.TimeoutExpired):
                # Startup may have partially succeeded; never report it as stopped.
                stopped=False
        _write(root/'status.json',{'state':outcome,'exit_code':exit_code,
            'elapsed_seconds':round(time.monotonic()-began),'cluster_stopped':stopped,
            'test_processes_stopped':test_processes_stopped})
    return 0 if outcome=='passed' and stopped and test_processes_stopped else 1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cleanup',type=Path)
    parser.add_argument('--pg-bin',type=Path)
    parser.add_argument('--pytest',type=Path)
    parser.add_argument('--checkpoint',type=Path)
    parser.add_argument('--timeout',type=int,default=1200)
    parser.add_argument('--retention-probe',action='store_true',help='run only the deliberate isolated failure probe')
    args=parser.parse_args()
    if args.cleanup:cleanup(args.cleanup);return 0
    if not args.pg_bin or not args.pytest or not args.checkpoint or not 1<=args.timeout<=7200:
        parser.error('supply --pg-bin, --pytest and --checkpoint')
    return run(args.pg_bin,args.pytest,args.checkpoint,args.timeout,args.retention_probe)


if __name__=='__main__':sys.exit(main())
