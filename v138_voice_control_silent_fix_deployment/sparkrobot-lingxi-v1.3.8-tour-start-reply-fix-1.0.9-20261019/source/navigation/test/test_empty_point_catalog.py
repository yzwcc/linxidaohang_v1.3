import unittest

from robot_local_navigation_v3.point_catalog import PointCatalog, PointNotFoundError


class EmptyPointCatalogTest(unittest.TestCase):
    def test_empty_catalog_supports_first_run_configuration(self):
        catalog = PointCatalog([])

        self.assertEqual(catalog.list_points(), [])
        self.assertEqual(catalog.list_voice_names(), [])
        self.assertIsNone(catalog.get_start_point())
        with self.assertRaisesRegex(PointNotFoundError, "Point not found"):
            catalog.resolve("未配置的点位")
