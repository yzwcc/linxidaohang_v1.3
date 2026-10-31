"""Small health-only IPC; no poses, sensor payloads, credentials or commands."""
import json
import math
import re
import threading
import time


VERSION = 1
FIELDS = frozenset(("version", "session", "sequence", "workerState", "completedMessages",
                    "processedScans", "estimatedPoses", "failure", "emittedAtSeconds"))
STATES = frozenset(("new", "starting", "running", "stopping", "stopped", "failed"))
MAXIMUM_COUNTER = 2**63 - 1
MAPPER_FIELDS = frozenset(("engine", "acceptedFrames", "completedFrames", "frameCapacity",
                           "acceptedImu", "completedImu", "imuCapacity", "observedAtSeconds",
                           "maximumWaitMs", "maximumTaskMs"))


def mapper_health(value):
    if type(value) is not dict or set(value) != MAPPER_FIELDS or value["engine"] != "glim-full-async-v1":
        raise ValueError("exact_full_mapper_health_required")
    for kind, capacity in (("Frames", 128), ("Imu", 8192)):
        accepted, completed = (value[key + kind] for key in ("accepted", "completed"))
        if (type(accepted) is not int or type(completed) is not int
                or not 0 <= completed <= accepted <= 2**53 - 1 or accepted - completed > capacity
                or type(value["frameCapacity" if kind == "Frames" else "imuCapacity"]) is not int
                or value["frameCapacity" if kind == "Frames" else "imuCapacity"] != capacity):
            raise ValueError("bounded_full_mapper_health_counters_required")
    for key in ("observedAtSeconds", "maximumWaitMs", "maximumTaskMs"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0:
            raise ValueError("finite_full_mapper_health_timing_required")
    return value.copy()


def session_id(value):
    if type(value) is not str or re.fullmatch("[a-f0-9]{32}", value) is None:
        raise ValueError("explicit_process_session_id_required")
    return value


def validate(event):
    if (type(event) is not dict or set(event) not in (FIELDS, FIELDS | {"mapper"})
            or type(event["version"]) is not int or event["version"] != VERSION
            or type(event["workerState"]) is not str or event["workerState"] not in STATES):
        raise ValueError("exact_versioned_worker_health_event_required")
    session_id(event["session"])
    if (type(event["emittedAtSeconds"]) not in (int, float)
            or not math.isfinite(event["emittedAtSeconds"]) or event["emittedAtSeconds"] < 0):
        raise ValueError("finite_local_monotonic_event_time_required")
    for name in ("sequence", "completedMessages", "processedScans", "estimatedPoses"):
        if type(event[name]) is not int or not 0 <= event[name] <= MAXIMUM_COUNTER:
            raise ValueError("bounded_integer_worker_counters_required")
    if not event["estimatedPoses"] <= event["processedScans"] <= event["completedMessages"]:
        raise ValueError("consistent_worker_health_counters_required")
    failure = event["failure"]
    if event["workerState"] == "failed":
        if type(failure) is not str or re.fullmatch("[a-z_]{1,80}", failure) is None:
            raise ValueError("fixed_worker_failure_code_required")
    elif failure is not None:
        raise ValueError("failure_requires_failed_worker_state")
    if "mapper" in event:
        mapper = mapper_health(event["mapper"])
        if mapper["observedAtSeconds"] > event["emittedAtSeconds"] + .05:
            raise ValueError("mapper_observation_cannot_come_from_future")
    return event


def decode(payload, maximum_bytes):
    if type(payload) is not bytes or not payload.endswith(b"\n") or len(payload) > maximum_bytes:
        raise ValueError("bounded_complete_worker_health_line_required")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_health_field")
            result[key] = value
        return result

    try:
        event = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("worker_health_json_required") from error
    return validate(event)


class HealthEmitter:
    """One explicit sink, sequential records; a heartbeat alone never means progress."""

    def __init__(self, sink, session, *, clock=time.monotonic):
        self.sink, self.session = sink, session_id(session)
        if not callable(clock):
            raise ValueError("explicit_emitter_clock_required")
        self.clock = clock
        self.sequence, self.lock = 0, threading.Lock()

    def emit(self, snapshot, *, mapper=None):
        with self.lock:
            stream = snapshot["stream"]
            event = validate({
                "version": VERSION, "session": self.session, "sequence": self.sequence,
                "workerState": snapshot["state"],
                "completedMessages": snapshot["counts"]["completedMessages"],
                "processedScans": 0 if stream is None else stream["counts"]["processedScans"],
                "estimatedPoses": 0 if stream is None else stream["counts"]["estimatedPoses"],
                "failure": snapshot["failure"],
                "emittedAtSeconds": self.clock(),
                **({"mapper": mapper_health(mapper)} if mapper is not None else {}),
            })
            payload = (json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n").encode()
            written = self.sink.write(payload)
            if written != len(payload):
                raise OSError("complete_worker_health_write_required")
            self.sink.flush()
            self.sequence += 1
            return event
