"""Bounded fault locations for the runtime's private stderr, never API payloads.

Do not stringify exceptions or include locals/source lines: either can contain
point clouds, credentials or user data. Diagnostic failure must not mask cleanup.
"""
import json
import math
import re
import sys
import time


_DIAGNOSTIC_LIMITS = {
    'input_age_ms_at_exchange_start': (0, 300000),
    'received_age_ms_at_exchange_start': (0, 300000),
    'age_deadline_remaining_ms_at_exchange_start': (-300000, 300000),
    'processing_deadline_remaining_ms_at_exchange_start': (-10000, 10000),
    'age_deadline_remaining_ms_after_exchange': (-300000, 300000),
    'processing_deadline_remaining_ms_after_exchange': (-10000, 10000),
    'exchange_elapsed_ms': (0, 30000),
    'window_span_ms': (0, 30000),
    'scan_count': (0, 128),
    'ray_count': (0, 131072),
    'request_bytes': (0, 40000000),
}


def record_fault(phase, error=None, *, started_at=None, code=None, diagnostics=None):
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
        if type(diagnostics) is dict:
            safe = {}
            for key, number in diagnostics.items():
                bounds = _DIAGNOSTIC_LIMITS.get(key)
                if (bounds is None or type(number) not in (int, float)
                        or type(number) is float and not math.isfinite(number)
                        or not bounds[0] <= number <= bounds[1]):
                    continue
                safe[key] = int(number) if type(number) is int else round(number, 3)
            if safe:
                value['diagnostics'] = safe
        sys.stderr.write(json.dumps(value, ensure_ascii=True, separators=(',', ':')) + '\n')
        sys.stderr.flush()
    except BaseException:
        pass
