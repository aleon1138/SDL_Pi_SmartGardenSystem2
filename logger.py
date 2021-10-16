"""
A trivial logger implementation.
"""
import time
import sys

out = sys.stderr


def log(msg):
    """
    Log a message to the default logger.
    """
    stime = time.strftime("%Y-%m-%d %H:%M:%S")
    out.write(f"[{stime}] {msg}\n")
    out.flush()
