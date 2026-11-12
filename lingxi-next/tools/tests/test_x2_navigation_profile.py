"""Vendor envelope math and actual physical Nav2 configuration integration."""
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "robot-navigation")]
import prepare_x2_navigation_profile as model
from lingxi_navigation.nav2_producer import load_configuration
from lingxi_navigation.physical_bridge import MapEvidence, Ticket


class X2ProfileTests(unittest.TestCase):
    def test_binary_and_ascii_mesh_bounds_include_all_triangle_vertices(self):
        vertices = (-.2, -.3, .1, .4, .5, -.6, .1, -.1, .7)
        binary = bytes(80) + struct.pack("<I", 1) + struct.pack("<12fH", 0., 0., 1., *vertices, 0)
        ascii_stl = "\n".join("vertex %s %s %s" % vertices[i:i+3] for i in (0, 3, 6)).encode()
        for data in (binary, ascii_stl):
            for actual, expected in zip(model.mesh_bounds(data), ((-.2, .4), (-.3, .5), (-.6, .7))):
                for a, b in zip(actual, expected):
                    self.assertAlmostEqual(a, b, places=6)

    def test_joint_origin_rotates_and_translates_the_child_envelope(self):
        transform = model.multiply(model.pose((1., 2., 3.), (0., 0., math.pi / 2)), model.pose((.5, 0., 0.)))
        for actual, expected in zip((row[3] for row in transform[:3]), (1., 2.5, 3.)):
            self.assertAlmostEqual(expected, actual)

    def test_another_model_cannot_generate_the_pinned_profile(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(model.MODEL, '<robot name="another"/>')
        with zipfile.ZipFile(data) as archive, self.assertRaisesRegex(ValueError, "pinned_x2"):
            model.envelope(archive)

    def test_shipped_profile_loads_with_exact_footprint_and_encloses_nominal_body(self):
        folder = ROOT / "deploy/profiles/x2-ultra-v1.3"
        data = (folder / "footprint.json").read_bytes()
        footprint = json.loads(data)
        identity = MapEvidence("slam-" + "a" * 32, "b" * 64, "c" * 64, hashlib.sha256(data).hexdigest())
        configuration = load_configuration((folder / "nav2-profile.json").resolve(), identity)
        self.assertGreater(configuration.clearance_radius_metres, footprint["horizontalEnvelopeRadiusMetres"])
        self.assertFalse(footprint["dynamicEnvelopeValidated"])
        self.assertFalse(footprint["physicalScaleValidated"])
        self.assertEqual((.1, 0., .2), (configuration.limits.forward, configuration.limits.lateral, configuration.limits.angular))
        profile = configuration.bind(map_frame="lingxi_saved_map", base_frame="base_link", sensor_frame="lidar_chest_front",
            cloud_topic="/lingxi/points", odometry_topic="/lingxi/odometry", terrain_topic="/lingxi/terrain")
        parameters = profile.parameters(Ticket("preparing", "d" * 32), Path("/unused"))
        for name in ("global_costmap", "local_costmap"):
            polygon = json.loads(parameters[name][name]["ros__parameters"]["footprint"])
            # The polygon's sides, including stopping allowance, must enclose a
            # 0.60 m circle. A circumscribed polygon is essential at corners.
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                distance = abs(a[0] * b[1] - a[1] * b[0]) / math.hypot(b[0] - a[0], b[1] - a[1])
                self.assertGreaterEqual(distance + 1e-6, .6)


if __name__ == "__main__":
    unittest.main()
