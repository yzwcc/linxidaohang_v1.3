from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from robot_local_navigation_v3.models import NavPoint


class PointCatalogError(RuntimeError):
    pass


class PointNotFoundError(PointCatalogError):
    pass


class AmbiguousPointError(PointCatalogError):
    pass


@dataclass(frozen=True)
class PointMatch:
    query: str
    point: NavPoint
    matched_by: str


class PointCatalog:
    """Name and alias resolver for configured navigation points."""

    def __init__(self, points: list[NavPoint]):
        self.points = list(points)
        self._by_id: dict[str, NavPoint] = {}
        self._by_map_and_name: dict[tuple[str, str], NavPoint] = {}
        self._name_index: dict[str, list[NavPoint]] = defaultdict(list)
        self._rebuild()

    def list_points(self, map_name: str | None = None) -> list[NavPoint]:
        if map_name is None:
            return list(self.points)
        return [point for point in self.points if point.map_name == map_name]

    def list_voice_names(self, map_name: str | None = None) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for point in self.list_points(map_name):
            if not point.voice.enabled:
                continue
            candidates = [point.voice.display_name or point.name, point.name, *point.aliases]
            for candidate in candidates:
                normalized = candidate.strip()
                if not normalized or normalized in seen:
                    continue
                names.append(normalized)
                seen.add(normalized)
        return names

    def list_ambiguous_voice_names(self, map_name: str | None = None) -> list[str]:
        owners: dict[str, set[str]] = defaultdict(set)
        display_names: dict[str, str] = {}
        for point in self.list_points(map_name):
            if not point.voice.enabled:
                continue
            per_point: set[str] = set()
            candidates = [point.voice.display_name or point.name, point.name, *point.aliases]
            for candidate in candidates:
                name = str(candidate or "").strip()
                key = self._normalize_voice_name(name)
                if not key or key in per_point:
                    continue
                per_point.add(key)
                owners[key].add(point.id)
                display_names.setdefault(key, name)
        return [
            display_names[key]
            for key in sorted(owners)
            if len(owners[key]) > 1
        ]

    def get_start_point(self, map_name: str | None = None) -> NavPoint | None:
        for point in self.list_points(map_name):
            if point.is_start:
                return point
        return None

    def get_by_id(self, point_id: str) -> NavPoint:
        point = self._by_id.get(point_id)
        if point is None:
            raise PointNotFoundError(f"Point id not found: {point_id}")
        return point

    def resolve(self, query: str, map_name: str | None = None) -> PointMatch:
        normalized = str(query or "").strip()
        if not normalized:
            raise PointNotFoundError("Point query is empty")

        if normalized in self._by_id:
            point = self._by_id[normalized]
            if map_name is not None and point.map_name != map_name:
                raise PointNotFoundError(f"Point id not in map {map_name}: {normalized}")
            return PointMatch(query=normalized, point=point, matched_by="id")

        if map_name is not None:
            point = self._by_map_and_name.get((map_name, normalized))
            if point is not None:
                return PointMatch(query=normalized, point=point, matched_by="map_name")

        candidates = [
            point
            for point in self._name_index.get(normalized, [])
            if map_name is None or point.map_name == map_name
        ]
        if not candidates:
            raise PointNotFoundError(f"Point not found: {normalized}")
        if len(candidates) > 1:
            maps = ", ".join(sorted({point.map_name for point in candidates}))
            raise AmbiguousPointError(
                f"Point name is ambiguous across maps: {normalized}; maps={maps}"
            )
        return PointMatch(query=normalized, point=candidates[0], matched_by="name_or_alias")

    def _rebuild(self) -> None:
        for point in self.points:
            if point.id in self._by_id:
                raise AmbiguousPointError(f"Duplicate point id: {point.id}")
            self._by_id[point.id] = point
            self._by_map_and_name[(point.map_name, point.name)] = point
            for name in self._index_names(point):
                self._name_index[name].append(point)

    def _index_names(self, point: NavPoint) -> list[str]:
        names = [point.name, point.voice.display_name, *point.aliases]
        result: list[str] = []
        seen: set[str] = set()
        for name in names:
            normalized = str(name or "").strip()
            if not normalized or normalized in seen:
                continue
            result.append(normalized)
            seen.add(normalized)
        return result

    @staticmethod
    def _normalize_voice_name(name: str) -> str:
        return "".join(str(name or "").lower().split())
