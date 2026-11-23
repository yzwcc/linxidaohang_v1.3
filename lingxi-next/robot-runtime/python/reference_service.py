import argparse
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid


REFERENCE_REVISION = "90bbc8096c23a5660fc6c5e26dd99ec1ed79da11"
SESSION = r"[0-9a-f]{32}"
MAP = rf"fastlio-{SESSION}"
LOGGER = logging.getLogger("lingxi.reference")
EXPORT_WAIT_S = 600
STATIONARY_CAPTURE_S = 3


def reference_capture_settings(settings):
    # Live grid precomputation waits for the upstream visualization worker, which
    # is disabled in this product. Export directly from the validated recording instead.
    return {**settings, "mapping": {**settings["mapping"], "grid_cache_enabled": False}}


def capture_parameters(reader, target):
    # Only the mapping manager's per-session copy; not the factory configuration
    # or navigation LIO. Keep the registered scan, body scan, PCD and recorder.
    def load(path):
        params = reader(path)
        if path != target:
            return params
        return {**params, 'publish': {**params.get('publish', {}), 'map_en': False,
                                       'path_en': False, 'effect_map_en': False}}
    return load


def reference_capture_phase(current, now):
    if current["state"] == "recording":
        began = current.get("recording_started_at")
        if type(began) in (float, int) and math.isfinite(began) and began > 0 and now - began >= STATIONARY_CAPTURE_S:
            return "running"
        return "requested"
    return {"starting": "requested", "stopping": "saving"}.get(current["state"], "failed")


def reference_capture_progress(current):
    if current is None or current["state"] not in {"recording", "stopping"}:
        return None
    stats = current.get("stats", {})
    paired, recorded = stats.get("paired_frames"), stats.get("recording_bytes")
    if not all(type(value) is int and 0 <= value <= (1 << 53) - 1 for value in (paired, recorded)):
        return None
    return {"pairedFrames": paired, "recordingBytes": recorded}


def private_directory(path):
    if not path.is_absolute() or path != path.resolve(strict=True) or not path.is_dir():
        raise ValueError("canonical_private_directory_required")
    details = path.stat()
    if details.st_uid != os.getuid() or details.st_mode & 0o077:
        raise ValueError("owned_private_directory_required")
    return path


def resource(root, path):
    path = Path(path)
    result = path.resolve(strict=True)
    if result != path or not result.is_relative_to(root) or not result.is_file():
        raise ValueError("reference_asset_outside_data_directory")
    return result


def reference_capture_failure(root, record):
    if not record or record.get("state") != "failed":
        return None
    process = record.get("processes", {}).get("fastlio", {})
    # The manifest comes from the Linux reference process; use its SIGABRT
    # exit code even when diagnostics/tests run on Windows.
    if process.get("returncode") != -6 or not record.get("output_dir"):
        return None
    directory = Path(record["output_dir"])
    if not directory.name.endswith("__" + record["id"]):
        return None
    try:
        log = resource(root, directory / "logs" / "fastlio.log")
        with log.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - 8192))
            lines = stream.read(8192).decode("utf-8", errors="replace").splitlines()
    except (ValueError, OSError):
        return None
    if any(line.strip() == "what():  FAST-LIO 扫描处理延迟超过允许范围" for line in lines):
        return "fastlio_scan_backlog"
    return None


def file_sha256(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def checked_grid(root, bundle, expected_sha256=None):
    import yaml
    from io import BytesIO
    from PIL import Image

    metadata_path = resource(root, bundle["yaml_path"])
    with metadata_path.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("reference_grid_metadata_too_large")
    try:
        metadata = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise ValueError("invalid_reference_grid_metadata") from error
    if not isinstance(metadata, dict) or metadata.get("image") != "map.pgm" or metadata.get("mode", "trinary") != "trinary":
        raise ValueError("unsupported_grid_format")
    try:
        resolution = float(metadata["resolution"])
        origin = [float(value) for value in metadata["origin"]]
        occupied, free = float(metadata["occupied_thresh"]), float(metadata["free_thresh"])
        negate = metadata["negate"]
    except (TypeError, OverflowError) as error:
        raise ValueError("invalid_grid_geometry") from error
    if (len(origin) != 3 or not all(map(math.isfinite, origin)) or not 0.02 <= resolution <= 0.2
            or not 0 <= free < occupied <= 1 or negate not in (0, 1)):
        raise ValueError("invalid_grid_geometry")
    pgm = resource(root, metadata_path.parent / "map.pgm")
    with pgm.open("rb") as stream:
        pixels = stream.read(4_001_025)
    if (len(pixels) > 4_001_024 or not pixels.startswith(b"P5\n")
            or (expected_sha256 is not None and hashlib.sha256(pixels).hexdigest() != expected_sha256)):
        raise ValueError("reference_grid_digest_or_format_mismatch")
    if expected_sha256 is not None and (occupied, free, negate) != (0.65, 0.196, 0):
        raise ValueError("reference_grid_policy_mismatch")
    with Image.open(BytesIO(pixels), formats=("PPM",)) as image:
        width, height = image.size
        if (image.mode != "L" or min(width, height) < 1 or width * height > 4_000_000
                or [width, height] != [bundle["width"], bundle["height"]]
                or resolution != bundle["resolution"] or origin != bundle["origin"]):
            raise ValueError("grid_metadata_mismatch")
        values = [value / 255.0 if negate else 1.0 - value / 255.0 for value in range(256)]
        lookup = [100 if value > occupied else 0 if value < free else -1 for value in values]
        pixels = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
        cells = [lookup[value] for value in pixels]
    return {"width": width, "height": height, "resolution": resolution, "originX": origin[0],
            "originY": origin[1], "originYaw": origin[2], "stamp": metadata_path.stat().st_mtime_ns / 1e6,
            "cells": cells}


def checked_map(root, bundle, *, overview=False):
    """Reopen a pinned export and verify the assets before save or load acknowledgement."""
    import numpy as np

    def read_metadata(path):
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("reference_map_metadata_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid_reference_map_metadata")
        return value

    yaml_file = resource(root, bundle["yaml_path"])
    directory = yaml_file.parent
    if (yaml_file.name != "map.yaml" or directory.name != bundle["id"]
            or resource(root, bundle["localization_map"]) != directory / "localization.ply"):
        raise ValueError("reference_map_asset_path_mismatch")
    metadata = read_metadata(resource(root, directory / "metadata.json"))
    sidecar = read_metadata(resource(root, directory / "localization.json"))
    fields = ("schema_version", "id", "name", "revision", "frame_id", "resolution", "origin", "width", "height",
              "map_points", "source_session_id", "navigation_from_source", "base_from_imu", "imu_from_lidar",
              "free_space")
    count = metadata.get("map_points")
    free_space = metadata.get("free_space")
    if (metadata.get("schema_version") != 1 or metadata.get("frame_id") != "map" or not isinstance(free_space, dict)
            or metadata.get("source_session_id") != bundle["id"].removeprefix("fastlio-")
            or not all(k in metadata and k in bundle and metadata[k] == bundle[k] for k in fields)
            or type(count) is not int or not 200 <= count <= 2_000_000
            or sidecar.get("map_points") != count or sidecar.get("normal_orientation") != "towards_sensor"
            or sidecar.get("viewpoint_frame") != "map" or sidecar.get("trajectory_frame") != "map"
            or sidecar.get("imu_from_lidar") != metadata["imu_from_lidar"]):
        raise ValueError("reference_map_metadata_mismatch")

    # The pinned exporter writes binary little-endian Open3D vertices with six
    # doubles and no faces. Check the layout and byte count before interpreting data.
    cloud = resource(root, directory / "localization.ply")
    sha = hashlib.sha256()
    sampled = []
    with cloud.open("rb") as stream:
        header = stream.peek(1024)[:1024]
        end = header.find(b"end_header\n")
        if end < 0:
            raise ValueError("invalid_reference_ply_header")
        raw_header = stream.read(end + len(b"end_header\n"))
        layout = [line for line in raw_header.splitlines() if not line.startswith(b"comment ")]
        if layout != [b"ply", b"format binary_little_endian 1.0", f"element vertex {count}".encode(),
                      *(b"property double " + field for field in (b"x", b"y", b"z", b"nx", b"ny", b"nz")),
                      b"end_header"] or os.fstat(stream.fileno()).st_size != len(raw_header) + 48 * count:
            raise ValueError("invalid_reference_ply_layout")
        sha.update(raw_header)
        for offset in range(0, count, 16384):
            size = 48 * min(count - offset, 16384)
            chunk = stream.read(size)
            if len(chunk) != size:
                raise ValueError("truncated_reference_ply")
            sha.update(chunk)
            vertices = np.frombuffer(chunk, dtype="<f8").reshape(-1, 6)
            if overview:
                stride = math.ceil(count / 50000)
                sampled.extend(vertices[(-offset) % stride::stride, :3].round(3).reshape(-1).tolist())
            if not np.isfinite(vertices).all():
                raise ValueError("invalid_reference_ply_geometry")
            squared_norms = np.einsum("ij,ij->i", vertices[:, 3:], vertices[:, 3:])
            if np.any((squared_norms < .81) | (squared_norms > 1.21)):
                raise ValueError("invalid_reference_ply_normals")
        if stream.read(1) or sha.hexdigest() != sidecar.get("cache_sha256"):
            raise ValueError("reference_ply_digest_mismatch")

    raster_hash = free_space.get("output_sha256")
    if not isinstance(raster_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", raster_hash):
        raise ValueError("reference_grid_digest_missing")
    grid = checked_grid(root, bundle, raster_hash)
    if overview:
        from reference_cloud import saved_trail
        if sidecar.get('trajectory_child_frame') != 'lidar_imu_chest_front':
            raise ValueError('unknown_trajectory_frame')
        trail = saved_trail(resource(root, directory / 'trajectory.csv'), metadata.get('frame_count'))
        return dict(id=bundle['id'], frame='map', stampMs=0, xyz=sampled, trail=trail, pose=None)
    return grid


def wait_for(read, complete, deadline):
    while time.monotonic() < deadline:
        value = read()
        if complete(value):
            return value
        time.sleep(0.15)
    raise TimeoutError("reference_operation_unconfirmed")


def make_app(upstream, root, nav2_process=None, motion_review=None):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
    from typing import Literal
    from reference_calibration import check_target_mounting
    from reference_motion import MOTION_RECORD, MotionTask, motion_record_present
    from reference_target import MAP_FILES
    from reference_cloud import SurveyCloud

    motion_maps = None if motion_review is None else motion_review.maps
    invalidated = threading.Event()
    preview_stop = threading.Event()
    preview_cloud = None

    Mode = Literal["point", "nav2"]

    class Session(BaseModel):
        model_config = ConfigDict(extra="forbid")
        session: str = Field(pattern=f"^{SESSION}$")

    class Save(Session):
        name: str = Field(min_length=1, max_length=64)

    class Pose(BaseModel):
        model_config = ConfigDict(extra="forbid")
        x: FiniteFloat = Field(ge=-1000, le=1000)
        y: FiniteFloat = Field(ge=-1000, le=1000)
        yaw: FiniteFloat = Field(ge=-2 * math.pi, le=2 * math.pi)

    class Place(BaseModel):
        model_config = ConfigDict(extra="forbid")
        pose: Pose | None = None

    class Destination(BaseModel):
        model_config = ConfigDict(extra="forbid")
        session: str = Field(pattern=f"^{SESSION}$")
        mapId: str = Field(pattern=f"^{MAP}$")
        taskId: str = Field(min_length=1, max_length=100)
        pointId: str = Field(min_length=1, max_length=100)
        mode: Mode
        pose: Pose

    class Control(BaseModel):
        model_config = ConfigDict(extra="forbid")
        session: str = Field(pattern=f"^{SESSION}$")
        taskId: str = Field(min_length=1, max_length=100)
        action: Literal["pause", "resume", "stop"]

    @asynccontextmanager
    async def lifespan(_app):
        async with upstream.lifespan(upstream.app):
            if upstream.app.state.ros.motion_enabled != (motion_maps is not None):
                raise RuntimeError("reference_motion_configuration_mismatch")
            if motion_maps is None:
                try:
                    yield
                finally:
                    preview_stop.set()
                return
            done = threading.Event()

            def watch_interlocks():
                next_target_check = 0.0
                while not done.wait(.10):
                    reason = None
                    changed = False
                    if not nav_process_alive():
                        reason = "Nav2 进程已退出"
                    now = time.monotonic()
                    if not invalidated.is_set() and now >= next_target_check:
                        next_target_check = now + 1.0
                        changed = not motion_review.matches_current_target()
                    if reason is None and not changed and not invalidated.is_set():
                        continue
                    ros = state.ros
                    with ros.lock:
                        if changed:
                            invalidated.set()
                        if invalidated.is_set():
                            reason = "本机版本、标定或运动审查已变化，需要重新核对"
                        task = ros.motion_task
                        if task and task.phase in {"requested", "running"}:
                            ros.revoke(task, reason)

            watch = threading.Thread(target=watch_interlocks, name="reference-motion-watchdog", daemon=True)
            watch.start()
            try:
                yield
            finally:
                done.set()
                preview_stop.set()
                watch.join(timeout=1)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    state = upstream.app.state
    transition = threading.Lock()
    finalizing = None
    last_failed = None
    last_started = None
    export_request = None
    active_review = None

    def reviewed(bundle):
        if motion_maps is None or bundle["id"] not in motion_maps:
            return None
        yaml_file = resource(root, bundle["yaml_path"])
        files = {name: resource(root, yaml_file.parent / name) for name in MAP_FILES}
        if resource(root, bundle["localization_map"]) != files["localization.ply"]:
            raise RuntimeError("reference_localization_resource_mismatch")
        calculated = {name: file_sha256(file) for name, file in files.items()}
        if calculated != motion_maps[bundle["id"]]:
            return None
        return (bundle["id"], bundle["revision"])

    def nav_process_alive():
        return nav2_process is None or nav2_process.poll() is None

    def calibration_status():
        return check_target_mounting(state.settings["mapping"]["fastlio_config"], state.ros._navigation_transform)

    def require_calibration():
        status = calibration_status()
        if status != "matched":
            raise HTTPException(status_code=409, detail=f"reference_calibration_{status}")

    def motion_ready():
        return bool(active_review and not invalidated.is_set() and active_review[0] == state.active_map_id and nav_process_alive()
                    and not state.mapping.is_active() and state.ros.output_idle() and state.ros.ready())

    def submit_reviewed(task):
        ros = state.ros
        current = motion_review.matches_current_target()
        with ros.lock:
            if not current:
                invalidated.set()
            if invalidated.is_set() or not nav_process_alive():
                raise RuntimeError("reference_motion_review_changed")
            ros.submit(task)

    def navigation_idle():
        if recovery_required():
            raise HTTPException(409, detail="reference_stop_recovery_required")
        if motion_maps is not None and not state.ros.output_idle():
            raise RuntimeError("reference_motion_not_confirmed_idle")

    def recovery_required():
        return motion_maps is None and motion_record_present(root / MOTION_RECORD)

    def navigation_payload(calibration=None):
        if calibration is None:
            calibration = calibration_status()
        ros = state.ros
        with ros.lock:
            task = ros.motion_task
            current = task.phase if task else "idle"
            still_stopping = current == "stopping" and task.retirement_running
            return {"session": ros.motion_session, "ready": calibration == "matched" and motion_ready(),
                    "physicalMotionSent": ros.physical_motion_sent,
                    "status": "running" if still_stopping else "failed" if current == "stopping" else current,
                    "requiresStop": current == "stopping" and not still_stopping, "taskId": task.task_id if task else None,
                    "pointId": task.point_id if task else None, "detail": task.detail if task else
                    "本机运动审查已变化，需要重新核对" if invalidated.is_set() else "等待运动准入"}

    @app.exception_handler(RuntimeError)
    @app.exception_handler(ValueError)
    @app.exception_handler(KeyError)
    @app.exception_handler(OSError)
    async def operation_failure(_request, error):
        LOGGER.warning("Reference operation failed: %s", type(error).__name__)
        LOGGER.debug("Reference exception detail", exc_info=error)
        code = 504 if isinstance(error, TimeoutError) else 404 if isinstance(error, (KeyError, FileNotFoundError)) else 409
        return JSONResponse(status_code=code, content={"error": "reference_operation_unconfirmed"})

    def guarded(operation):
        if not transition.acquire(blocking=False):
            raise RuntimeError("reference_transition_in_progress")
        try:
            return operation()
        finally:
            transition.release()

    def aimdk_ready():
        return bool(state.ros.source_set.service_is_ready() and state.ros.source_get.service_is_ready())

    def session(identity):
        for item in state.mapping.snapshot()["items"]:
            if item["id"] == identity:
                return item
        raise KeyError("unknown_reference_session")

    def export_pending():
        return bool(export_request and state.jobs.get(export_request[2])["state"] in {"queued", "running"})

    def managed_map(identity):
        if not re.fullmatch(MAP, identity):
            raise KeyError("unknown_reference_map")
        bundle = state.store.map(identity)
        resource(root, bundle["localization_map"])
        return bundle

    def map_payload(bundle):
        return {"id": bundle["id"], "name": bundle["name"], "resolution": bundle["resolution"],
                "origin": bundle["origin"], "active": state.active_map_id == bundle["id"]}

    @app.get("/lingxi/status")
    def status():
        snapshot = state.mapping.snapshot()
        current = snapshot["active"]
        ros = state.ros.snapshot()
        sensors = ros["sensor_fresh"]
        aimdk = aimdk_ready()
        sensors_ready = bool(sensors["lidar"] and sensors["imu"] and not ros.get("fatal_error"))
        calibration = calibration_status()
        recovery = recovery_required()
        saving = finalizing or (export_request[0] if export_pending() else None)
        failed = last_failed or next((item["id"] for item in snapshot["items"] if item["id"] == last_started
                                      and item["state"] in {"failed", "interrupted"}), None)
        lifecycle = ros.get("lifecycle", {})
        nav2_available = nav_process_alive() and all(lifecycle.get(name, {}).get("service_ready") is True
                                                     for name in ("map_server", "planner_server", "controller_server",
                                                                  "velocity_smoother", "bt_navigator"))
        result = {"phase": "saving" if saving else reference_capture_phase(current, time.time()) if current
                            else "failed" if failed else "idle",
                  "session": saving or (current["id"] if current else failed),
                  "sensorsReady": sensors_ready, "aimdkReady": aimdk, "calibration": calibration,
                  "nav2Available": nav2_available,
                  "mappingReady": bool(snapshot["enabled"] and current is None and sensors_ready
                                       and aimdk and calibration == "matched" and not recovery and not saving and not transition.locked()),
                  "planningReady": bool(calibration == "matched" and not recovery and nav_process_alive()
                                        and ros["localization_fresh"] and ros["actions"]["compute_path"]
                                        and ros["nav2"]["loaded_map"] and state.active_map_id)}
        if current and current["id"] == result["session"]:
            progress = reference_capture_progress(current)
            if progress is not None:
                result["referenceCaptureProgress"] = progress
        if result["phase"] == "failed" and failed:
            record = next((item for item in snapshot["items"] if item["id"] == failed), None)
            failure = reference_capture_failure(root, record)
            if failure:
                result["mappingFailure"] = failure
        if recovery:
            result["motionRecoveryRequired"] = True
        if not recovery and calibration == "matched" and state.active_map_id and ros["localization_fresh"] and ros.get("pose") and ros.get("stamp"):
            result.update(pose=ros["pose"], poseAtMs=ros["stamp"] * 1000)
        if motion_maps is not None:
            result["navigation"] = navigation_payload(calibration)
        return result

    @app.get("/lingxi/lidar")
    def lidar():
        import numpy as np

        active = state.mapping.snapshot()["active"]
        if not active or active["state"] != "recording":
            raise HTTPException(404)
        with state.ros.lock:
            frame = state.ros.frame
            received = state.ros.frame_received
        now = time.time()
        if (frame is None or frame["stamp"] < active["started_at"] or not -0.1 <= now - frame["stamp"] <= 1.5
                or time.monotonic() - received > 1.5):
            raise HTTPException(404)
        points = frame["body_points"]
        sample = points[::max(1, math.ceil(len(points) / 8000)), :3]
        transform = frame["odom_imu"]
        values = sample @ transform[:3, :3].T + transform[:3, 3]
        values = values[np.isfinite(values).all(axis=1)]
        if len(values) == 0:
            raise HTTPException(404)
        stamp_ms = frame["stamp_ns"] // 1_000_000
        return {"sequence": stamp_ms, "epoch": active["id"], "source": "point-cloud", "frameId": "fastlio_odom",
                "sensorFrame": "lidar_chest_front", "fixedFrame": True, "stampMs": stamp_ms,
                "receivedAtMs": (now - (time.monotonic() - received)) * 1000,
                "points": values.reshape(-1).tolist()}

    @app.get("/lingxi/maps")
    def maps():
        return [map_payload(bundle) for bundle in state.store.maps() if re.fullmatch(MAP, bundle["id"])]

    @app.get("/lingxi/maps/{identity}/grid")
    def grid(identity: str):
        return JSONResponse(checked_map(root, managed_map(identity)))

    @app.get('/lingxi/maps/{identity}/overview')
    def saved_overview(identity: str):
        return JSONResponse(checked_map(root, managed_map(identity), overview=True))

    @app.get('/lingxi/surveys/{identity}/overview')
    def live_overview(identity: str):
        cloud = preview_cloud
        if cloud is None or cloud.identity != identity:
            raise HTTPException(404)
        return JSONResponse(cloud.snapshot())

    def collect_preview(cloud, began, stop):
        while not stop.wait(.32) and state.mapping.is_active():
            try:
                with state.ros.lock:
                    frame = state.ros.frame
                if frame is None or frame['stamp'] < began or frame['stamp_ns'] <= cloud.last:
                    continue
                body = state.ros._navigation_transform('base_link', 'lidar_imu_chest_front', frame['stamp_ns'])
                cloud.add(frame, body)
            except (ValueError, ArithmeticError, RuntimeError) as error:
                LOGGER.debug('Preview frame unavailable: %s', type(error).__name__)

    @app.post("/lingxi/mapping/start")
    def start():
        def operation():
            nonlocal last_failed, last_started, preview_cloud, preview_stop
            navigation_idle()
            if not aimdk_ready() or export_pending():
                raise RuntimeError("reference_mapping_start_unavailable")
            require_calibration()
            if state.navigation_lio.is_active():
                upstream.stop_navigation_runtime()
                wait_for(state.navigation_lio.is_active, lambda active: not active, time.monotonic() + 55)
            state.active_map_id = None
            request_id = uuid.uuid4().hex
            result = upstream.start_mapping(upstream.MappingStartRequest(
                name=f"扫描-{request_id}", request_id=request_id))
            last_failed = None
            last_started = result["id"]
            preview_stop.set()
            preview_cloud, preview_stop = SurveyCloud(last_started), threading.Event()
            threading.Thread(target=collect_preview, args=(preview_cloud, result['started_at'], preview_stop),
                             daemon=True, name='survey-overview').start()
            return result["id"]
        return guarded(operation)

    def stop_capture(identity):
        current = state.mapping.snapshot()["active"]
        if current and current["id"] != identity:
            raise RuntimeError("reference_session_changed")
        upstream.stop_mapping(identity)
        return wait_for(lambda: session(identity), lambda value: value["state"] not in {"starting", "recording", "stopping"}
                        and not state.mapping.is_active(), time.monotonic() + 470)

    @app.post("/lingxi/mapping/discard")
    def discard(body: Session):
        def operation():
            nonlocal finalizing, last_failed, last_started
            finalizing = body.session
            try:
                if any(bundle["id"] == f"fastlio-{body.session}" for bundle in state.store.maps()):
                    raise RuntimeError("exported_capture_cannot_be_discarded")
                current = state.mapping.snapshot()["active"]
                if current and current["id"] != body.session:
                    raise RuntimeError("reference_session_changed")
                if any(item["id"] == body.session for item in state.mapping.snapshot()["items"]):
                    stop_capture(body.session)
                result = upstream.delete_mapping(body.session, confirmed=True)
                if result.get("deleted") is not True or result.get("id") != body.session:
                    raise RuntimeError("reference_discard_unconfirmed")
                last_failed = None
                if last_started == body.session:
                    last_started = None
                return True
            finally:
                finalizing = None
        return guarded(operation)

    @app.post("/lingxi/mapping/save")
    def save(body: Save):
        def operation():
            nonlocal finalizing, last_failed, export_request
            identity = f"fastlio-{body.session}"
            current = state.mapping.snapshot()["active"]
            if current and current["id"] == body.session and reference_capture_phase(current, time.time()) == "requested":
                raise HTTPException(409, detail="reference_capture_initializing")
            finalizing = body.session
            try:
                selected = stop_capture(body.session)
                if selected["state"] != "saved":
                    raise RuntimeError("reference_capture_not_saved")
                try:
                    bundle = managed_map(identity)
                except KeyError:
                    if export_pending():
                        if export_request[:2] != (body.session, body.name):
                            raise RuntimeError("reference_export_already_running")
                        job_id = export_request[2]
                    else:
                        job = upstream.export_map_request(upstream.ExportRequest(
                            recording_dir=selected["recording_dir"], map_id=identity, name=body.name))
                        job_id = job["id"]
                        export_request = (body.session, body.name, job_id)
                    result = wait_for(lambda: state.jobs.get(job_id),
                                      lambda value: value["state"] in {"succeeded", "failed"}, time.monotonic() + EXPORT_WAIT_S)
                    if result["state"] != "succeeded":
                        raise RuntimeError("reference_grid_export_failed")
                    bundle = managed_map(identity)
                if bundle["name"] != body.name:
                    raise RuntimeError("reference_map_name_changed")
                checked_map(root, bundle)
                last_failed = None
                return map_payload(bundle)
            except Exception:
                last_failed = body.session
                raise
            finally:
                finalizing = None
        return guarded(operation)

    @app.post("/lingxi/maps/{identity}/activate")
    def activate(identity: str, body: Place):
        def operation():
            nonlocal active_review
            navigation_idle()
            require_calibration()
            active_review = None
            if export_pending():
                raise RuntimeError("reference_export_already_running")
            bundle = managed_map(identity)
            checked_map(root, bundle)
            upstream.activate_map(identity)
            if body.pose is None:
                return True
            try:
                runtime = wait_for(state.navigation_lio.snapshot, lambda value: value["state"] in {"ready", "failed"},
                                   time.monotonic() + 95)
                if runtime["state"] != "ready":
                    raise RuntimeError("reference_localization_input_failed")
                upstream.localize(upstream.LocalizationRequest(map_id=identity, **body.pose.model_dump(), timeout_s=30))
                result = wait_for(state.localizer.snapshot,
                                  lambda value: value["state"] in {"confirmed", "failed", "cancelled"},
                                  time.monotonic() + 31)
                if result["state"] != "confirmed":
                    raise RuntimeError("reference_localization_unconfirmed")
                state.localizer.wait_for_confirmation(identity, timeout_s=2)
                state.ros.activate_map(bundle["yaml_path"])
                active_review = reviewed(bundle)
            except Exception:
                upstream.cancel_localization()
                state.active_map_id = None
                raise
            return True
        return guarded(operation)

    @app.post("/lingxi/maps/{identity}/plan")
    def plan(identity: str, body: Place):
        def operation():
            navigation_idle()
            require_calibration()
            bundle = managed_map(identity)
            upstream.require_mapping_idle()
            if body.pose is None or state.active_map_id != identity:
                raise RuntimeError("reference_map_inactive_or_goal_missing")
            state.tasks.require_localization(identity)
            checked_map(root, bundle)
            state.ros.activate_map(bundle["yaml_path"])
            return state.ros.compute_path(body.pose.model_dump(), timeout_s=15)
        return guarded(operation)

    if motion_maps is not None:
        @app.post("/lingxi/navigation/start")
        def navigate(body: Destination):
            def operation():
                ros = state.ros
                if body.session != ros.motion_session or body.mapId != state.active_map_id:
                    raise RuntimeError("reference_motion_not_ready")
                require_calibration()
                if not motion_ready():
                    raise RuntimeError("reference_motion_not_ready")
                prior = ros.motion_task
                if prior and prior.task_id == body.taskId:
                    raise RuntimeError("reference_task_id_reused")
                if reviewed(managed_map(body.mapId)) != active_review:
                    raise RuntimeError("reference_map_changed_after_activation")
                state.tasks.require_localization(body.mapId)
                submit_reviewed(MotionTask(body.taskId, body.pointId, body.mapId, body.pose.model_dump(), body.mode))
                return navigation_payload()
            return guarded(operation)

        @app.post("/lingxi/navigation/control")
        def control(body: Control):
            def operation():
                ros = state.ros
                task = ros.motion_task
                if body.session != ros.motion_session or not task or task.task_id != body.taskId:
                    raise RuntimeError("reference_task_changed")
                if body.action == "resume":
                    if task.phase != "paused" or task.map_id != state.active_map_id or not nav_process_alive():
                        raise RuntimeError("reference_task_not_resumable")
                    if invalidated.is_set():
                        raise RuntimeError("reference_motion_review_changed")
                    if reviewed(managed_map(task.map_id)) != active_review or active_review is None:
                        raise RuntimeError("reference_map_changed_after_activation")
                    require_calibration()
                    state.tasks.require_localization(task.map_id)
                    submit_reviewed(MotionTask(task.task_id, task.point_id, task.map_id, dict(task.goal), task.mode))
                    return navigation_payload()
                with ros.lock:
                    if task.phase == "paused" and body.action == "stop":
                        task.phase, task.detail = "idle", "任务已停止"
                    elif task.phase in {"requested", "running", "stopping"}:
                        if body.action == "stop" and task.phase == "stopping":
                            task.result = "idle"
                        ros.revoke(task, "正在确认停稳", "paused" if body.action == "pause" else "idle")
                wait_for(lambda: task, lambda item: item.phase != "stopping" and not item.retirement_running,
                         time.monotonic() + 18)
                return navigation_payload()
            return guarded(operation)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ros2", type=Path, required=True)
    parser.add_argument("--require-motion-disabled", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if not sys.platform.startswith("linux"):
        raise RuntimeError("reference_runtime_requires_linux")
    source = args.source.resolve(strict=True)
    # systemd runs as lingxi-next while the verified checkout is maintained by root.
    for path in (source, source / ".git"):
        if path.is_symlink() or not path.is_dir() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise RuntimeError("reference_checkout_must_be_root_owned")
    git = ["git", "--no-optional-locks", "-c", f"safe.directory={source}", "-c", "core.fsmonitor=false",
           "-C", str(source)]
    revision = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output([*git, "status", "--porcelain", "--untracked-files=all"], text=True)
    if revision != REFERENCE_REVISION or dirty:
        raise RuntimeError("reference_checkout_does_not_match_pinned_revision")
    private_directory(args.socket.parent)
    if not args.socket.is_absolute() or len(os.fsencode(args.socket)) >= 100 or os.path.lexists(args.socket):
        raise ValueError("new_private_reference_socket_required")
    os.environ["ROBOT_NAV_CONFIG"] = str(args.config.resolve(strict=True))
    sys.path.insert(0, str(source))
    import uvicorn
    from robot_nav_tasks.settings import load_settings

    original_settings = load_settings()
    settings = reference_capture_settings(original_settings)
    if args.require_motion_disabled and settings["motion_enabled"] is not False:
        raise RuntimeError("reference_commissioning_requires_motion_disabled")
    root = private_directory(Path(settings["data_dir"]))
    if settings.get("nav2_enabled") is not True or settings.get("visualization", {}).get("enabled", False):
        raise RuntimeError("reference_profile_requires_nav2_and_disables_visualization_bridge")
    if settings.get("color_mapping", {}).get("enabled", False):
        raise RuntimeError("reference_profile_does_not_support_color_mapping")
    if settings.get("mapping", {}).get("loop_closure_enabled", False):
        raise RuntimeError("reference_profile_does_not_support_loop_closure")
    from robot_nav_tasks import server as upstream
    from robot_nav_tasks import mapping_manager
    mapping_manager.yaml_parameters = capture_parameters(mapping_manager.yaml_parameters,
                                                         Path(settings['mapping']['fastlio_config']))
    from reference_ground import read_saved_export_reference
    if original_settings["mapping"].get("grid_cache_enabled"):
        LOGGER.warning("Skipping live grid cache; the saved recording will be used for grid export")
    upstream.load_settings = lambda: settings
    upstream.saved_export_reference = lambda session: read_saved_export_reference(source, session)
    motion_review = None
    if settings["motion_enabled"]:
        from reference_motion import gateway_type
        from reference_target import load_motion_review
        motion_review = load_motion_review(settings["lingxi_motion_review"], args.config, settings["mapping"]["fastlio_config"])
        idle_sources = settings["lingxi_motion_idle_sources"]
        if not isinstance(idle_sources, list) or not idle_sources or not all(isinstance(name, str) for name in idle_sources):
            raise ValueError("motion_idle_sources_required")
        if not 0 < settings["max_forward_velocity"] <= .25 or not 0 < settings["max_angular_velocity"] <= .8 or settings["max_lateral_velocity"] != 0:
            raise ValueError("reference_motion_limits_out_of_range")
        source_timeout = settings.get("source_timeout_ms")
        if type(source_timeout) is not int or not 250 <= source_timeout <= 1000:
            raise ValueError("reference_aimdk_source_timeout_out_of_range")
        upstream.RosGateway = gateway_type(upstream.RosGateway)

    # Uvicorn changes UDS mode itself when binding. Supply an already bound private socket.
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
        endpoint.bind(str(args.socket))
        identity = args.socket.stat().st_ino
        os.chmod(args.socket, 0o600)
        nav2 = None
        try:
            os.environ.setdefault("ROS_LOG_DIR", str(root / "logs"))
            nav2 = subprocess.Popen([sys.executable, str(args.ros2.resolve(strict=True)), "launch",
                                     str(source / "deploy/nav2_services.launch.py")], cwd=source,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            config = uvicorn.Config(make_app(upstream, root, nav2, motion_review), access_log=False, lifespan="on")
            runner = uvicorn.Server(config)
            runner.run(sockets=[endpoint])
            if not runner.started:
                raise RuntimeError("reference_service_start_failed")
        finally:
            if nav2 is not None and nav2.poll() is None:
                try:
                    os.killpg(nav2.pid, signal.SIGINT)
                    nav2.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(nav2.pid, signal.SIGKILL)
                    nav2.wait(timeout=5)
                except ProcessLookupError:
                    nav2.wait(timeout=5)
            if os.path.lexists(args.socket):
                details = args.socket.lstat()
                if stat.S_ISSOCK(details.st_mode) and details.st_ino == identity:
                    args.socket.unlink()


if __name__ == "__main__":
    main()
