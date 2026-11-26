#!/usr/bin/env python3
"""Own an isolated test session; stop only this session on control or EOF."""
import json
import os
import signal
import subprocess
import sys
import threading
import time


def main():
    if os.getpid()!=os.getsid(0) or os.getpid()!=os.getpgrp():
        raise RuntimeError('synthetic anchor requires its own session and process group')
    status_fd=int(sys.argv[1]);command=sys.argv[2:]
    if not command:raise RuntimeError('synthetic command missing')
    child=subprocess.Popen(command,stdin=subprocess.DEVNULL,close_fds=True)
    # Only the supervisor ignores TERM; the already-spawned test retains its disposition.
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    def report():
        result=child.wait()
        try:os.write(status_fd,(json.dumps({'exit_code':result})+'\n').encode())
        except OSError:pass
        finally:os.close(status_fd)
    threading.Thread(target=report,daemon=True).start()
    # A closed parent pipe has the same shutdown meaning as an explicit request.
    sys.stdin.buffer.read(1)
    # This call originates from the still-running session leader itself, so the
    # group ID cannot have been recycled into an unrelated session.
    os.killpg(os.getpgrp(),signal.SIGTERM)
    time.sleep(0.2)
    os.killpg(os.getpgrp(),signal.SIGKILL)


if __name__=='__main__':main()
