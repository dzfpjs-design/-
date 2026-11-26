"""Only synthetic loopback HTTP; persistent records contain no body or headers."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import httpx
import pytest
from tests.synthetic_deepseek_loopback_v2 import provider

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),reason='isolated loopback only')


def request(port):
    with httpx.Client(trust_env=False,timeout=5) as client:
        return client.post(f'http://127.0.0.1:{port}/chat/completions',content=b'private-user-content',headers={'Authorization':'Bearer private-api-key'})


def entries(path):
    content=path.read_bytes()
    assert b'private-user-content' not in content and b'private-api-key' not in content
    rows=[json.loads(line) for line in content.splitlines()]
    assert all(set(row)=={'count','received_at','method','content_length'} for row in rows)
    assert all(row['method']=='POST' and row['content_length']==len(b'private-user-content') for row in rows)
    return rows


def test_durable_after_normal_and_assertion_failure(tmp_path):
    port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);path=tmp_path/'requests.jsonl'
    with pytest.raises(AssertionError,match='synthetic assertion'):
        with provider(port,request_log=path) as server:
            assert request(port).status_code==200
            assert request(port).status_code==200
            assert len(server.requests)==2
            raise AssertionError('synthetic assertion')
    assert [row['count'] for row in entries(path)]==[1,2]
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    # Explicit owner cleanup; provider preserves evidence on context exit.
    path.unlink();assert not path.exists()


def test_crash_after_receipt_leaves_durable_record(tmp_path):
    port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);path=tmp_path/'crash-requests.jsonl'
    program='''
import os,sys
from tests.synthetic_deepseek_loopback_v2 import provider
with provider(int(sys.argv[1]),request_log=sys.argv[2],on_received=lambda:os._exit(23)):
 print('READY',flush=True)
 import time;time.sleep(20)
'''
    child=subprocess.Popen([sys.executable,'-u','-c',program,str(port),str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        with pytest.raises(httpx.TransportError):request(port)
        assert child.wait(timeout=5)==23
        assert [row['count'] for row in entries(path)]==[1]
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.stdout.close();child.stderr.close()
    path.unlink();assert not path.exists()


def test_existing_file_is_not_overwritten(tmp_path):
    port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);path=tmp_path/'requests.jsonl'
    path.write_text('keep')
    with pytest.raises(FileExistsError):
        with provider(port,request_log=path):pass
    assert path.read_text()=='keep'
