"""Fixture: parent ignores SIGTERM while a forked child holds the
inherited stdout pipe and sleeps. A harness that signals only the direct
process sees communicate() stall on the child's pipe long past the
timeout; a group kill drops both."""
import os
import signal
import time

child = os.fork()
if child == 0:
    pidfile = os.environ.get("FAULT_CHILD_PIDFILE")
    if pidfile:
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))
    time.sleep(8)
    os._exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(8)
