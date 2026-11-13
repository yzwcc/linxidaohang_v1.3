"""Bounded fault locations for the runtime's private stderr, never API payloads.

Do not stringify exceptions or include locals/source lines: either can contain
point clouds, credentials or user data. Diagnostic failure must not mask cleanup.
"""
import json
import re
import sys
import time


def record_fault(phase, error=None, *, started_at=None, code=None):
    try:
        identifier = re.compile(r'[a-z][a-z0-9_]{0,127}\Z')
        if type(phase) is not str or not identifier.fullmatch(phase):
            return
        value = dict(event='lingxi_private_fault', phase=phase)
        if type(code) is str and identifier.fullmatch(code):
            value['code'] = code
        if started_at is not None:
            elapsed = time.monotonic() - started_at
            if 0 <= elapsed < 86400:
                value['elapsedMs'] = round(elapsed * 1000, 3)
        if error is not None:
            value['exception'] = type(error).__name__[:80]
            frames, trace = [], error.__traceback__
            while trace is not None:
                frame = trace.tb_frame.f_code
                frames.append(dict(file=frame.co_filename.replace('\\', '/').rsplit('/', 1)[-1][:100],
                                   function=frame.co_name[:80], line=trace.tb_lineno))
                frames = frames[-12:]
                trace = trace.tb_next
            value['frames'] = frames
        sys.stderr.write(json.dumps(value, ensure_ascii=True, separators=(',', ':')) + '\n')
        sys.stderr.flush()
    except BaseException:
        pass
