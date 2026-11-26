#!/usr/bin/env python3
"""Run one isolated synthetic checkpoint test and retain its stopped PG.

The runner never connects to an existing cluster or external network. It never
replays an application. Retained directories require explicit --cleanup.
"""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time

SCHEMA = 'isolated-advice-retention-v1'
PROTECTED_SERVICE_PORTS = frozenset({55486, 8766})


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


def _isolated_ports():
    """Select distinct loopback ports without choosing known service ports."""
    selected=[]
    for _ in range(60):
        candidate=_port()
        if (type(candidate) is int and 1 <= candidate <= 65535
            and candidate not in PROTECTED_SERVICE_PORTS and candidate not in selected):
            selected.append(candidate)
            if len(selected)==3:return tuple(selected)
    raise RuntimeError('could not select three distinct isolated ports')


def _stop_test_process_group(process):
    """Ask our live anchor over its private pipe; never signal a numeric PGID."""
    if process.returncode is not None:return False
    try:
        process.stdin.write(b'S');process.stdin.flush();process.stdin.close()
        process.wait(timeout=10)
    except (OSError,ValueError,subprocess.TimeoutExpired):return False
    deadline=time.monotonic()+5
    while True:
        try:
            output=subprocess.check_output(['/bin/ps','-ax','-o','pgid='],text=True,timeout=2)
            groups=output.split()
            if not groups or any(not item.isdecimal() for item in groups):return False
            if str(process.pid) not in groups:return True
        except (OSError,subprocess.SubprocessError):return False
        if time.monotonic()>=deadline:return False
        time.sleep(0.05)


def _await_test_result(fd,timeout):
    ready,_,_=select.select([fd],[],[],timeout)
    if not ready:return None
    value=os.read(fd,256)
    try:result=json.loads(value)
    except (ValueError,UnicodeError):raise RuntimeError('synthetic test result unavailable')
    if not isinstance(result,dict) or type(result.get('exit_code')) is not int:
        raise RuntimeError('synthetic test result unavailable')
    return result['exit_code']


def _network_profile(port, provider_port, http_port, context_only):
    if context_only:
        return f'''(version 1) (allow default) (deny network*)
          (allow network-outbound (remote ip "localhost:{port}"))'''
    return f'''(version 1) (allow default) (deny network*)
          (allow network-inbound (local ip "localhost:{provider_port}"))
          (allow network-inbound (local ip "localhost:{http_port}"))
          (allow network-outbound (remote ip "localhost:{port}"))
          (allow network-outbound (remote ip "localhost:{provider_port}"))
          (allow network-outbound (remote ip "localhost:{http_port}"))'''


def run(pg,pytest_bin,checkpoint,timeout,retention_probe=False,context_only=False):
    if retention_probe and context_only:raise ValueError('probe modes must be distinct')
    pg=Path(pg).resolve();pytest_bin=Path(pytest_bin).resolve();checkpoint=Path(checkpoint).resolve()
    repo=Path(__file__).resolve().parents[1]
    if (not (pg/'initdb').is_file() or not (pg/'pg_ctl').is_file() or not (pg/'psql').is_file()
        or not pytest_bin.is_file() or not checkpoint.is_dir() or (repo/'.env').exists()
        or not Path('/usr/bin/sandbox-exec').is_file()):
        raise RuntimeError('isolated runtime requirements not met')
    port,provider_port,http_port=_isolated_ports()
    root=private_root();data=root/'data'
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
        if context_only:env['GA_TEST_CONTEXT_ONLY_PROFILE']='1'
        profile=_network_profile(port,provider_port,http_port,context_only)
        _write(root/'status.json',{'state':'running','cluster_stopped':False})
        fd=os.open(root/'test.log',os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out:
            result_read,result_write=os.pipe()
            try:
                process=subprocess.Popen(['/usr/bin/sandbox-exec','-p',profile,sys.executable,
                    str(repo/'scripts'/'synthetic_process_anchor.py'),str(result_write),str(pytest_bin),'-q',
                    'tests/test_advice_context_profile_only_pg.py' if context_only else
                    'tests/test_synthetic_retention_probe_pg.py' if retention_probe else
                    'tests/test_advice_user_real_http_checkpoint_pg.py'],cwd=repo,env=env,
                    stdin=subprocess.PIPE,stdout=out,stderr=subprocess.STDOUT,start_new_session=True,
                    pass_fds=(result_write,))
            except BaseException:
                os.close(result_read);os.close(result_write);raise
            os.close(result_write)
            test_processes_stopped=False
            try:
                exit_code=_await_test_result(result_read,timeout)
                outcome='finished' if exit_code is not None else 'timeout'
            except (OSError,RuntimeError):
                outcome='process_identity_unconfirmed'
            finally:
                os.close(result_read)
                # The anchor's private pipe remains bound to this exact process.
                test_processes_stopped=_stop_test_process_group(process)
                if outcome=='finished':
                    outcome=('passed' if exit_code==0 else 'failed') if test_processes_stopped else 'process_cleanup_unconfirmed'
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
    parser.add_argument('--context-only',action='store_true',help='profile only context GET in an isolated PG with outbound limited to PG')
    args=parser.parse_args()
    if args.cleanup:cleanup(args.cleanup);return 0
    if args.retention_probe and args.context_only:parser.error('choose only one probe mode')
    if not args.pg_bin or not args.pytest or not args.checkpoint or not 1<=args.timeout<=7200:
        parser.error('supply --pg-bin, --pytest and --checkpoint')
    return run(args.pg_bin,args.pytest,args.checkpoint,args.timeout,args.retention_probe,args.context_only)


if __name__=='__main__':sys.exit(main())
