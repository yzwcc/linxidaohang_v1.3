"""Reproduce a nominal X2 Ultra envelope; no vendor mesh is redistributed."""
import argparse
import hashlib
import io
import json
import math
import posixpath
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MODEL = "X2_URDF-v1.3.0/x2_ultra.urdf"
URDF_SHA256 = "f628a969aaf3d5785b8c8901611a85f358d7eb62a8b06b1216dc42291a72944e"
SOURCE_COMMIT = "575cc6b988f976c23550e0db85aa1e5475d3652d"


def pose(xyz=(0., 0., 0.), rpy=(0., 0., 0.)):
    x, y, z = xyz
    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, x],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, y],
            [-sp, cp * sr, cp * cr, z], [0., 0., 0., 1.]]


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def vector(text, default=(0., 0., 0.)):
    return tuple(map(float, text.split())) if text else default


def origin(element):
    return pose(vector(element.get("xyz")), vector(element.get("rpy"))) if element is not None else pose()


def corners(bounds):
    return [(x, y, z) for x in bounds[0] for y in bounds[1] for z in bounds[2]]


def mesh_bounds(payload):
    if len(payload) >= 84:
        count = struct.unpack_from("<I", payload, 80)[0]
        if count and len(payload) == 84 + 50 * count:
            low, high = [float("inf")] * 3, [float("-inf")] * 3
            for index in range(count):
                values = struct.unpack_from("<9f", payload, 96 + index * 50)
                for offset in (0, 3, 6):
                    for axis in range(3):
                        value = values[offset + axis]
                        if not math.isfinite(value):
                            raise ValueError("finite_vendor_stl_required")
                        low[axis] = min(low[axis], value)
                        high[axis] = max(high[axis], value)
            return tuple(zip(low, high))
    text = payload.decode("ascii", errors="strict")
    points = [tuple(map(float, match)) for match in re.findall(
        r"\bvertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)", text)]
    if not points or not all(math.isfinite(v) for p in points for v in p):
        raise ValueError("finite_vendor_stl_required")
    return tuple((min(p[i] for p in points), max(p[i] for p in points)) for i in range(3))


def envelope(archive):
    payload = archive.read(MODEL)
    if hashlib.sha256(payload).hexdigest() != URDF_SHA256:
        raise ValueError("pinned_x2_ultra_v13_urdf_required")
    robot = ET.fromstring(payload)
    children = {}
    for joint in robot.findall("joint"):
        parent, child = joint.find("parent").get("link"), joint.find("child").get("link")
        children.setdefault(parent, []).append((child, origin(joint.find("origin"))))
    links = {link.get("name") for link in robot.findall("link")}
    descendants = {j.find("child").get("link") for j in robot.findall("joint")}
    if links - descendants != {"base_link"}:
        raise ValueError("base_link_root_required")
    transforms, pending = {"base_link": pose()}, ["base_link"]
    while pending:
        parent = pending.pop()
        for child, transform in children.get(parent, []):
            if child in transforms:
                raise ValueError("tree_urdf_required")
            transforms[child] = multiply(transforms[parent], transform)
            pending.append(child)
    points, meshes = [], {}
    for link in robot.findall("link"):
        for collision in link.findall("collision"):
            geometry = collision.find("geometry")
            mesh = geometry.find("mesh")
            if mesh is None:
                raise ValueError("pinned_model_collision_mesh_required")
            member = posixpath.normpath(posixpath.join(posixpath.dirname(MODEL), mesh.get("filename").replace("\\", "/")))
            if not member.startswith(posixpath.dirname(MODEL) + "/"):
                raise ValueError("model_local_mesh_required")
            data = archive.read(member)
            meshes[member] = hashlib.sha256(data).hexdigest()
            scale = vector(mesh.get("scale"), (1., 1., 1.))
            bounds = tuple((min(a * s, b * s), max(a * s, b * s))
                           for (a, b), s in zip(mesh_bounds(data), scale))
            transform = multiply(transforms[link.get("name")], origin(collision.find("origin")))
            points.extend(tuple(sum(transform[i][j] * p[j] for j in range(3)) + transform[i][3]
                                for i in range(3)) for p in corners(bounds))
    low = [min(p[i] for p in points) for i in range(3)]
    high = [max(p[i] for p in points) for i in range(3)]
    return {
        "schema": 1, "model": "X2 Ultra v1.3 (nominal zero joint angles)", "baseFrame": "base_link",
        "source": {"repository": "https://github.com/AgibotTech/agibot_x2_urdf",
                   "commit": SOURCE_COMMIT, "urdf": MODEL, "urdfSha256": URDF_SHA256,
                   "collisionMeshesSha256": dict(sorted(meshes.items()))},
        "boundsMetres": {axis: [low[i], high[i]] for i, axis in enumerate("xyz")},
        "extentsMetres": {axis: high[i] - low[i] for i, axis in enumerate("xyz")},
        "nominalAabbCenterInBaseMetres": [(a + b) / 2 for a, b in zip(low, high)],
        "horizontalEnvelopeRadiusMetres": max(math.hypot(p[0], p[1]) for p in points),
        "clearanceRadiusMetres": .45,
        "status": "provisional_commissioning_only",
        "dynamicEnvelopeValidated": False,
        "sensorExtrinsicsValidated": False,
        "physicalScaleValidated": False,
    }


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def prepare(sdk, output):
    # Read ZIP entries in memory, including the vendor's relative symlink.
    # Never extract vendor archives into the project or install on the robot.
    with zipfile.ZipFile(sdk) as archive:
        links = [n for n in archive.namelist() if n.endswith("/X2_URDF.zip") and "/zh-cn/" in n]
        if len(links) != 1:
            raise ValueError("one_vendor_urdf_archive_required")
        data = archive.read(links[0])
        if not data.startswith(b"PK"):
            member = posixpath.normpath(posixpath.join(posixpath.dirname(links[0]), data.decode("utf-8")))
            data = archive.read(member)
        with zipfile.ZipFile(io.BytesIO(data)) as models:
            footprint = envelope(models)
    if footprint["horizontalEnvelopeRadiusMetres"] >= footprint["clearanceRadiusMetres"]:
        raise ValueError("clearance_must_enclose_nominal_model")
    data = encoded(footprint)
    profile = {
        "schema": 1, "footprintSha256": hashlib.sha256(data).hexdigest(),
        "clearanceRadiusMetres": .45, "stoppingMarginMetres": .15,
        "forward": .10, "lateral": 0., "angular": .20,
        "acceleration": .10, "deceleration": .20, "angularAcceleration": .40,
        "commandAge": .15, "firstCommandTimeout": .75,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "footprint.json").write_bytes(data)
    (output / "nav2-profile.json").write_bytes(encoded(profile))
    return profile


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory; existing files are preserved")
    args = parser.parse_args()
    print(json.dumps(prepare(args.sdk_zip, args.output), indent=2))
