"""Fast checks for the offline runner's durable, one-use marker."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch
from tests.synthetic_advice_mock_state import record_once, save_state, read_state, read_request_marker


class StateChecks(unittest.TestCase):
    def test_marker_and_receipt_survive_process_and_cannot_repeat(self):
        with tempfile.TemporaryDirectory(prefix='advice-race-pg-') as name:
            root=Path(name); os.chmod(root,0o700)
            run_id=str(uuid4())
            values={key:str(uuid4()) for key in ('workspace_id','call_id','intent_id','permit_id')}
            values['run_id']=run_id
            with patch.dict(os.environ,{'GA_SYNTHETIC_RUN_ROOT':name,'GA_SYNTHETIC_RUN_ID':run_id}):
                save_state(values); record_once()
                self.assertEqual(read_state(),values)
                self.assertEqual(read_request_marker()['fake_requests'],1)
                with self.assertRaises(FileExistsError): record_once()
                child=subprocess.run([sys.executable,'-c','from tests.synthetic_advice_mock_state import read_state,read_request_marker; assert read_state() and read_request_marker()["fake_requests"] == 1'],
                    env=os.environ.copy(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.assertEqual(child.returncode,0)
                self.assertEqual((root/'fake_request.json').stat().st_mode & 0o077,0)

    def test_nonprivate_root_rejected(self):
        with tempfile.TemporaryDirectory(prefix='advice-race-pg-') as name:
            os.chmod(name,0o755)
            with patch.dict(os.environ,{'GA_SYNTHETIC_RUN_ROOT':name,'GA_SYNTHETIC_RUN_ID':str(uuid4())}):
                with self.assertRaises(RuntimeError): record_once()


if __name__=='__main__': unittest.main()
