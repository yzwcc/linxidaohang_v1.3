from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_PROVIDER = "doubao_multimodal"
DEFAULT_MODEL = "doubao-seed-2-0-pro-260215"
DEFAULT_TASK_TYPE = 2
DEFAULT_TARGET_POSE_RADIUS = 0.15
DEFAULT_YAW_TOLERANCE_RAD = math.radians(10.0)
DEFAULT_MAX_FORWARD_SPEED = 0.3
DEFAULT_PNC_MODE = 0


@dataclass
class Position:
    x: float
    y: float
    z: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Position":
        return cls(
            x=float(payload.get("x", 0.0)),
            y=float(payload.get("y", 0.0)),
            z=float(payload.get("z", 0.0)),
        )


@dataclass
class Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Quaternion":
        return cls(
            x=float(payload.get("x", payload.get("qx", payload.get("ox", 0.0)))),
            y=float(payload.get("y", payload.get("qy", payload.get("oy", 0.0)))),
            z=float(payload.get("z", payload.get("qz", payload.get("oz", 0.0)))),
            w=float(payload.get("w", payload.get("qw", payload.get("ow", 1.0)))),
        )

    @classmethod
    def from_yaw(cls, yaw: float) -> "Quaternion":
        return cls(z=math.sin(yaw / 2.0), w=math.cos(yaw / 2.0))

    @property
    def yaw(self) -> float:
        siny = 2.0 * (self.w * self.z + self.x * self.y)
        cosy = 1.0 - 2.0 * (self.y * self.y + self.z * self.z)
        return math.atan2(siny, cosy)


@dataclass
class PoseConfig:
    position: Position
    orientation: Quaternion = field(default_factory=Quaternion)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PoseConfig":
        if "position" in payload or "orientation" in payload:
            return cls(
                position=Position.from_dict(payload.get("position", {})),
                orientation=Quaternion.from_dict(payload.get("orientation", {})),
            )

        orientation_keys = {"qx", "qy", "qz", "qw", "ox", "oy", "oz", "ow"}
        if not any(key in payload for key in orientation_keys) and "yaw" in payload:
            orientation = Quaternion.from_yaw(float(payload.get("yaw", 0.0)))
        else:
            orientation = Quaternion.from_dict(payload)
        return cls(position=Position.from_dict(payload), orientation=orientation)


@dataclass
class AppSettings:
    provider: str = DEFAULT_PROVIDER
    doubao_api_key: str = ""
    doubao_model: str = DEFAULT_MODEL
    nav_mode: str = "dry-run"

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AppSettings":
        payload = payload or {}
        provider = str(payload.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER
        if provider in {"asr_llm", "frontend_gemini", "gemini"}:
            provider = DEFAULT_PROVIDER
        if provider != DEFAULT_PROVIDER:
            raise ValueError(f"Unsupported provider: {provider}")
        nav_mode = str(payload.get("nav_mode", "dry-run")).strip() or "dry-run"
        if nav_mode not in {"execute", "dry-run"}:
            raise ValueError("nav_mode must be 'execute' or 'dry-run'")
        return cls(
            provider=provider,
            doubao_api_key=str(
                payload.get("doubao_api_key", payload.get("gemini_api_key", ""))
            ).strip(),
            doubao_model=str(
                payload.get("doubao_model", payload.get("gemini_model", DEFAULT_MODEL))
            ).strip()
            or DEFAULT_MODEL,
            nav_mode=nav_mode,
        )


@dataclass
class ArrivalPolicyConfig:
    position_tolerance_m: float = DEFAULT_TARGET_POSE_RADIUS
    yaw_tolerance_rad: float | None = DEFAULT_YAW_TOLERANCE_RAD
    stable_duration_sec: float = 0.8
    require_low_speed: bool = False
    low_speed_threshold_mps: float = 0.03

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ArrivalPolicyConfig":
        payload = payload or {}
        yaw_tolerance = payload.get("yaw_tolerance_rad", DEFAULT_YAW_TOLERANCE_RAD)
        return cls(
            position_tolerance_m=float(
                payload.get("position_tolerance_m", DEFAULT_TARGET_POSE_RADIUS)
            ),
            yaw_tolerance_rad=None if yaw_tolerance is None else float(yaw_tolerance),
            stable_duration_sec=float(payload.get("stable_duration_sec", 0.8)),
            require_low_speed=bool(payload.get("require_low_speed", False)),
            low_speed_threshold_mps=float(payload.get("low_speed_threshold_mps", 0.03)),
        )


@dataclass
class StopPolicyConfig:
    mode: str = "on_completed"
    hold_sec: float = 1.5

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "StopPolicyConfig":
        payload = payload or {}
        mode = str(payload.get("mode", "on_completed")).strip() or "on_completed"
        if mode not in {"never", "on_arrived_region", "on_completed"}:
            raise ValueError("stop_policy.mode must be never, on_arrived_region, or on_completed")
        return cls(mode=mode, hold_sec=max(0.0, float(payload.get("hold_sec", 1.5))))


@dataclass
class PathSafetyConfig:
    robot_radius_m: float | None = None
    extra_clearance_m: float = 0.10
    occupied_threshold: int = 205
    max_odom_age_sec: float = 1.5
    max_start_drift_m: float = 0.10
    max_reference_points: int = 256

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PathSafetyConfig":
        payload = payload or {}
        raw_radius = payload.get("robot_radius_m")
        radius = None if raw_radius in (None, "") else float(raw_radius)
        result = cls(
            robot_radius_m=radius,
            extra_clearance_m=float(payload.get("extra_clearance_m", 0.10)),
            occupied_threshold=int(payload.get("occupied_threshold", 205)),
            max_odom_age_sec=float(payload.get("max_odom_age_sec", 1.5)),
            max_start_drift_m=float(payload.get("max_start_drift_m", 0.10)),
            max_reference_points=int(payload.get("max_reference_points", 256)),
        )
        if radius is not None and (not math.isfinite(radius) or radius <= 0 or radius > 2.0):
            raise ValueError("path_safety.robot_radius_m must be measured and in (0, 2.0]")
        if not math.isfinite(result.extra_clearance_m) or not 0 <= result.extra_clearance_m <= 2.0:
            raise ValueError("path_safety.extra_clearance_m must be in [0, 2.0]")
        if not 1 <= result.occupied_threshold <= 254:
            raise ValueError("path_safety.occupied_threshold must be in [1, 254]")
        if not math.isfinite(result.max_odom_age_sec) or not 0.1 <= result.max_odom_age_sec <= 10:
            raise ValueError("path_safety.max_odom_age_sec must be in [0.1, 10]")
        if not math.isfinite(result.max_start_drift_m) or not 0 <= result.max_start_drift_m <= 2.0:
            raise ValueError("path_safety.max_start_drift_m must be in [0, 2.0]")
        if not 2 <= result.max_reference_points <= 4096:
            raise ValueError("path_safety.max_reference_points must be in [2, 4096]")
        return result


@dataclass
class NavigationDefaults:
    default_task_type: int = DEFAULT_TASK_TYPE
    default_target_pose_radius: float = DEFAULT_TARGET_POSE_RADIUS
    default_max_forward_speed: float = DEFAULT_MAX_FORWARD_SPEED
    default_pnc_mode: int = DEFAULT_PNC_MODE
    arrival_policy: ArrivalPolicyConfig = field(default_factory=ArrivalPolicyConfig)
    stop_policy: StopPolicyConfig = field(default_factory=StopPolicyConfig)
    path_safety: PathSafetyConfig = field(default_factory=PathSafetyConfig)
    timeout_sec: float = 120.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "NavigationDefaults":
        payload = payload or {}
        return cls(
            default_task_type=int(payload.get("default_task_type", DEFAULT_TASK_TYPE)),
            default_target_pose_radius=float(
                payload.get("default_target_pose_radius", DEFAULT_TARGET_POSE_RADIUS)
            ),
            default_max_forward_speed=float(
                payload.get("default_max_forward_speed", DEFAULT_MAX_FORWARD_SPEED)
            ),
            default_pnc_mode=int(payload.get("default_pnc_mode", DEFAULT_PNC_MODE)),
            arrival_policy=ArrivalPolicyConfig.from_dict(payload.get("arrival_policy")),
            stop_policy=StopPolicyConfig.from_dict(payload.get("stop_policy")),
            path_safety=PathSafetyConfig.from_dict(payload.get("path_safety")),
            timeout_sec=max(1.0, float(payload.get("timeout_sec", 120.0))),
        )


@dataclass
class MapConfig:
    map_name: str
    map_id: int
    map_path: str = ""
    resolution: float | None = None
    origin_x: float | None = None
    origin_y: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MapConfig":
        return cls(
            map_name=str(payload.get("map_name", payload.get("name", ""))).strip(),
            map_id=int(payload.get("map_id", 0)),
            map_path=str(payload.get("map_path", "")).strip(),
            resolution=_optional_float(payload.get("resolution")),
            origin_x=_optional_float(payload.get("origin_x")),
            origin_y=_optional_float(payload.get("origin_y")),
        )


@dataclass
class PointNavigationConfig:
    target_pose_radius: float = DEFAULT_TARGET_POSE_RADIUS
    pnc_mode: int = DEFAULT_PNC_MODE
    max_forward_speed: float = DEFAULT_MAX_FORWARD_SPEED
    task_type: int = DEFAULT_TASK_TYPE

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any] | None,
        defaults: NavigationDefaults | None = None,
    ) -> "PointNavigationConfig":
        payload = payload or {}
        defaults = defaults or NavigationDefaults()
        return cls(
            target_pose_radius=float(
                payload.get("target_pose_radius", payload.get("radius", defaults.default_target_pose_radius))
            ),
            pnc_mode=int(payload.get("pnc_mode", defaults.default_pnc_mode)),
            max_forward_speed=float(
                payload.get("max_forward_speed", defaults.default_max_forward_speed)
            ),
            task_type=int(payload.get("task_type", defaults.default_task_type)),
        )


@dataclass
class PointUiConfig:
    pixel_u: float | None = None
    pixel_v: float | None = None
    heading_pixel_u: float | None = None
    heading_pixel_v: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PointUiConfig":
        payload = payload or {}
        return cls(
            pixel_u=_optional_float(payload.get("pixel_u")),
            pixel_v=_optional_float(payload.get("pixel_v")),
            heading_pixel_u=_optional_float(payload.get("heading_pixel_u")),
            heading_pixel_v=_optional_float(payload.get("heading_pixel_v")),
        )


@dataclass
class PointVoiceConfig:
    enabled: bool = True
    display_name: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PointVoiceConfig":
        payload = payload or {}
        return cls(
            enabled=bool(payload.get("enabled", True)),
            display_name=str(payload.get("display_name", "")).strip(),
        )


@dataclass
class BusinessAction:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BusinessAction":
        action_type = str(payload.get("type", "")).strip()
        if not action_type:
            raise ValueError("business action type is required")
        rest = dict(payload)
        rest.pop("type", None)
        return cls(type=action_type, payload=rest)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


@dataclass
class PointBusinessConfig:
    on_arrival: list[BusinessAction] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PointBusinessConfig":
        payload = payload or {}
        return cls(
            on_arrival=[
                BusinessAction.from_dict(item)
                for item in payload.get("on_arrival", [])
                if isinstance(item, dict)
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {"on_arrival": [action.to_dict() for action in self.on_arrival]}


@dataclass
class PointTourConfig:
    enabled: bool = False
    order: int | None = None
    summary: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PointTourConfig":
        payload = payload or {}
        order = payload.get("order")
        return cls(
            enabled=bool(payload.get("enabled", False)),
            order=None if order is None else int(order),
            summary=str(payload.get("summary", payload.get("knowledge", ""))).strip(),
        )


@dataclass
class NavPoint:
    id: str
    name: str
    map_name: str
    map_id: int
    pose: PoseConfig
    is_start: bool = False
    aliases: list[str] = field(default_factory=list)
    navigation: PointNavigationConfig = field(default_factory=PointNavigationConfig)
    ui: PointUiConfig = field(default_factory=PointUiConfig)
    voice: PointVoiceConfig = field(default_factory=PointVoiceConfig)
    business: PointBusinessConfig = field(default_factory=PointBusinessConfig)
    tour: PointTourConfig = field(default_factory=PointTourConfig)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        defaults: NavigationDefaults | None = None,
    ) -> "NavPoint":
        defaults = defaults or NavigationDefaults()
        map_name = str(payload.get("map_name", payload.get("map_id", ""))).strip()
        map_id = int(payload.get("map_id", 0))
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("point name is required")

        pose_payload = payload.get("pose") if isinstance(payload.get("pose"), dict) else payload
        point_id = str(payload.get("id", f"{map_name}:{name}")).strip()
        return cls(
            id=point_id,
            name=name,
            is_start=bool(payload.get("is_start", payload.get("is_home", False))),
            aliases=[str(item).strip() for item in payload.get("aliases", []) if str(item).strip()],
            map_name=map_name,
            map_id=map_id,
            pose=PoseConfig.from_dict(pose_payload),
            navigation=PointNavigationConfig.from_dict(
                payload.get("navigation") if isinstance(payload.get("navigation"), dict) else payload,
                defaults=defaults,
            ),
            ui=PointUiConfig.from_dict(
                payload.get("ui") if isinstance(payload.get("ui"), dict) else payload
            ),
            voice=PointVoiceConfig.from_dict(payload.get("voice")),
            business=PointBusinessConfig.from_dict(payload.get("business")),
            tour=PointTourConfig.from_dict(payload.get("tour")),
        )

    @property
    def yaw(self) -> float:
        return self.pose.orientation.yaw

    def to_legacy_voice_dict(self, waypoint_index: int | None = None) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "map_name": self.map_name,
            "map_id": self.map_id,
            "is_start": self.is_start,
            "x": self.pose.position.x,
            "y": self.pose.position.y,
            "z": self.pose.position.z,
            "qx": self.pose.orientation.x,
            "qy": self.pose.orientation.y,
            "qz": self.pose.orientation.z,
            "qw": self.pose.orientation.w,
            "target_pose_radius": self.navigation.target_pose_radius,
            "pnc_mode": self.navigation.pnc_mode,
            "max_forward_speed": self.navigation.max_forward_speed,
            "task_type": self.navigation.task_type,
            "pixel_u": self.ui.pixel_u,
            "pixel_v": self.ui.pixel_v,
            "heading_pixel_u": self.ui.heading_pixel_u,
            "heading_pixel_v": self.ui.heading_pixel_v,
            "yaw": self.yaw,
            "ox": self.pose.orientation.x,
            "oy": self.pose.orientation.y,
            "oz": self.pose.orientation.z,
            "ow": self.pose.orientation.w,
        }
        if waypoint_index is not None:
            payload["waypoint_index"] = waypoint_index
        return payload

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["business"] = self.business.to_dict()
        return payload


@dataclass
class UnifiedNavConfig:
    schema_version: int = 2
    kind: str = "robot_local_nav.unified_config"
    app: AppSettings = field(default_factory=AppSettings)
    navigation: NavigationDefaults = field(default_factory=NavigationDefaults)
    maps: list[MapConfig] = field(default_factory=list)
    points: list[NavPoint] = field(default_factory=list)
    exports: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UnifiedNavConfig":
        navigation = NavigationDefaults.from_dict(payload.get("navigation"))
        return cls(
            schema_version=int(payload.get("schema_version", 2)),
            kind=str(payload.get("kind", "robot_local_nav.unified_config")),
            app=AppSettings.from_dict(payload.get("app", payload)),
            navigation=navigation,
            maps=[
                MapConfig.from_dict(item)
                for item in payload.get("maps", [])
                if isinstance(item, dict)
            ],
            points=[
                NavPoint.from_dict(item, defaults=navigation)
                for item in payload.get("points", [])
                if isinstance(item, dict)
            ],
            exports=dict(payload.get("exports", {})),
            created_at=float(payload.get("created_at", time.time())),
            updated_at=float(payload.get("updated_at", time.time())),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["points"] = [self._point_to_dict(point) for point in self.points]
        return payload

    def _point_to_dict(self, point: NavPoint) -> dict[str, Any]:
        return point.to_dict()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
