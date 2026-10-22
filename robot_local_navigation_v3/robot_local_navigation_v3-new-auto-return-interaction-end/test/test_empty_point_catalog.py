import unittest
from types import SimpleNamespace

from robot_local_navigation_v3.point_catalog import PointCatalog, PointNotFoundError


class EmptyPointCatalogTest(unittest.TestCase):
    def test_empty_catalog_supports_first_run_configuration(self):
        catalog = PointCatalog([])

        self.assertEqual(catalog.list_points(), [])
        self.assertEqual(catalog.list_voice_names(), [])
        self.assertEqual(catalog.list_ambiguous_voice_names(), [])
        self.assertIsNone(catalog.get_start_point())
        with self.assertRaisesRegex(PointNotFoundError, "Point not found"):
            catalog.resolve("未配置的点位")

    def test_ambiguous_voice_names_are_active_map_scoped_and_voice_enabled(self):
        def point(point_id, name, aliases, map_name="F1", enabled=True):
            return SimpleNamespace(
                id=point_id,
                name=name,
                map_name=map_name,
                aliases=aliases,
                voice=SimpleNamespace(enabled=enabled, display_name=""),
            )

        catalog = PointCatalog([
            point("a", "入口A", ["前台", "Reception Desk"]),
            point("b", "入口B", [" 前台 ", "receptiondesk"]),
            point("c", "入口C", ["前台"], map_name="F2"),
            point("d", "入口D", ["前台"], enabled=False),
            point("greek-a", "Greek A", ["ΟΣ"]),
            point("greek-b", "Greek B", ["οσ"]),
        ])

        self.assertEqual(
            catalog.list_ambiguous_voice_names(map_name="F1"),
            ["Reception Desk", "前台"],
        )
        self.assertEqual(
            catalog.list_ambiguous_voice_names(map_name="F2"),
            [],
        )

    def test_same_point_does_not_make_its_own_name_ambiguous(self):
        point = SimpleNamespace(
            id="a",
            name="服务台",
            map_name="F1",
            aliases=["服务台", " 服务台 "],
            voice=SimpleNamespace(enabled=True, display_name="服务台"),
        )
        catalog = PointCatalog([point])

        self.assertEqual(catalog.list_ambiguous_voice_names(map_name="F1"), [])
