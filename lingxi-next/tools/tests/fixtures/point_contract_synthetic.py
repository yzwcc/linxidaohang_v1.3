#!/usr/bin/python3
"""Isolated process-lifecycle fixture, not a native point contract executable."""
import sys
import time

if sys.argv[1] == "pending_close":
    time.sleep(30.)
elif sys.argv[1] == "overflow":
    raise SystemExit(5)
elif sys.argv[1] != "copy":
    raise SystemExit(2)
