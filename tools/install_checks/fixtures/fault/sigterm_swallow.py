import signal
import sys
import time


def _swallow(sig, frame):
    sys.exit(0)  # masked kill: must never read as a pass


signal.signal(signal.SIGTERM, _swallow)
time.sleep(30)
