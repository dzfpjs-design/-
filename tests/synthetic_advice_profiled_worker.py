"""Test-only wrapper around the isolated synthetic worker.

Saves aggregate counters regularly so that a worker killed on timeout still
leaves numeric diagnostics. The real worker and its guards are unmodified.
"""
import json
import os
import runpy
import threading
import time

from pytest import MonkeyPatch
from tests.synthetic_advice_performance_probe import install


def main():
    path = os.environ.get('GA_TEST_WORKER_METRICS_LOG')
    if not path:
        raise RuntimeError('isolated worker metrics path required')
    patch = MonkeyPatch()
    probe = install(patch)
    stop = threading.Event()

    def record():
        # The selected path is under the private, synthetic test run directory.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (json.dumps({'elapsed_ns': time.monotonic_ns(),
                'inclusive_synthetic_metrics': probe.snapshot()}) + '\n').encode('utf-8'))
        finally:
            os.close(fd)

    def recorder():
        while not stop.wait(5):
            record()

    thread = threading.Thread(target=recorder, daemon=True)
    thread.start()
    try:
        runpy.run_module('generic_agents.same_goal_advice_user_send_worker_candidate_v2', run_name='__main__')
    finally:
        stop.set()
        thread.join()
        record()
        patch.undo()


if __name__ == '__main__':
    main()
