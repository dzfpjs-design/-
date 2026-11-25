"""No-cloud tests of the candidate's metadata and local network fence."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4
from scripts import synthetic_advice_real_preflight as subject


def record():
    values={key:str(uuid4()) for key in ('run_id','workspace_id','task_id','plan_id','call_id','request_id')}
    return {'schema':subject.SCHEMA,**values,'source_hash':'1'*64,'model_hash':'2'*64,
        'model':'synthetic-model','provider_host':subject.PROVIDER_HOST,'approved_ips':['1.1.1.1']}


class PreflightChecks(unittest.TestCase):
    def test_original_request_is_one_shot_across_concurrent_callers(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);os.chmod(root,0o700);value=record()
            self.assertFalse(subject.prepare(root,value)['dispatch_authorized'])
            def attempt(_):
                try:return subject.claim_attempt(root,value['request_id'])
                except FileExistsError:return None
            with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(8)))
            self.assertEqual(sum(v is not None for v in results),1)
            self.assertFalse(next(v for v in results if v)['retry_authorized'])
            with self.assertRaises(ValueError):subject.claim_attempt(root,str(uuid4()))
            self.assertEqual(json.loads((root/'one-attempt.json').read_text())['outcome'],'unknown')

    def test_unapproved_or_nonpublic_addresses_fail_closed(self):
        self.assertEqual(subject.verify_dns_answers(['1.1.1.1'],['1.1.1.1']),('1.1.1.1',))
        with self.assertRaises(ValueError):subject.verify_dns_answers(['8.8.8.8'],['1.1.1.1'])
        with self.assertRaises(ValueError):subject.verify_dns_answers(['127.0.0.1'],['127.0.0.1'])
        with self.assertRaises(ValueError):subject.sandbox_profile(1234,['::1'])
        with self.assertRaises(RuntimeError):subject.sandbox_profile(1234,['1.1.1.1'],live=True)

    def test_private_root_and_strict_fields_required(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);os.chmod(root,0o755)
            with self.assertRaises(ValueError):subject.prepare(root,record())
            os.chmod(root,0o700);bad=record();bad['secret']='must-never-save'
            with self.assertRaises(ValueError):subject.prepare(root,bad)
            self.assertEqual(list(root.iterdir()),[])

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').exists(),'macOS sandbox required')
    def test_profile_permits_only_selected_local_fake_service(self):
        allowed=socket.socket();blocked=socket.socket()
        try:
            allowed.bind(('127.0.0.1',0));allowed.listen();blocked.bind(('127.0.0.1',0));blocked.listen()
            port1=allowed.getsockname()[1];port2=blocked.getsockname()[1]
            code=('import socket,sys; a=socket.create_connection(("127.0.0.1",int(sys.argv[1])),timeout=2);a.close(); '
                's=socket.socket();s.settimeout(2);r=s.connect_ex(("127.0.0.1",int(sys.argv[2])));s.close();sys.exit(0 if r else 2)')
            outcome=subprocess.run(['/usr/bin/sandbox-exec','-p',subject.sandbox_profile(port1,['1.1.1.1']),
                sys.executable,'-c',code,str(port1),str(port2)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            self.assertEqual(outcome.returncode,0)
        finally:allowed.close();blocked.close()


if __name__=='__main__':unittest.main()
