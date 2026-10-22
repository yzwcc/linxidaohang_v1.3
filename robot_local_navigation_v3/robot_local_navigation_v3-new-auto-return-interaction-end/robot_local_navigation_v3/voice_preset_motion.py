from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class VoicePresetMotion:
    name: str
    label: str
    default_area: str
    allowed_areas: frozenset[str]


VOICE_PRESET_MOTIONS: dict[str, VoicePresetMotion] = {
    "wave": VoicePresetMotion("wave", "挥手", "right_hand", frozenset({"left_hand", "right_hand"})),
    "handshake": VoicePresetMotion("handshake", "握手", "right_hand", frozenset({"left_hand", "right_hand"})),
    "raise_hand": VoicePresetMotion("raise_hand", "举手", "right_hand", frozenset({"left_hand", "right_hand"})),
    "flying_kiss": VoicePresetMotion("flying_kiss", "飞吻", "right_hand", frozenset({"left_hand", "right_hand"})),
    "sweetheart": VoicePresetMotion("sweetheart", "比心", "both_hands", frozenset({"left_hand", "right_hand", "both_hands"})),
    "high_five": VoicePresetMotion("high_five", "击掌", "right_hand", frozenset({"left_hand", "right_hand"})),
    "salute": VoicePresetMotion("salute", "敬礼", "right_hand", frozenset({"left_hand", "right_hand"})),
    "horizontal_arm": VoicePresetMotion("horizontal_arm", "平举", "both_hands", frozenset({"left_hand", "right_hand", "both_hands"})),
    "hug": VoicePresetMotion("hug", "拥抱", "whole_body", frozenset({"whole_body"})),
    "cheer": VoicePresetMotion("cheer", "加油", "whole_body", frozenset({"whole_body"})),
    "clap": VoicePresetMotion("clap", "鼓掌", "whole_body", frozenset({"whole_body"})),
    "handx": VoicePresetMotion("handx", "双手打叉", "whole_body", frozenset({"whole_body"})),
    "chestwave": VoicePresetMotion("chestwave", "胸前挥手", "right_hand", frozenset({"left_hand", "right_hand"})),
    "bow": VoicePresetMotion("bow", "鞠躬", "whole_body", frozenset({"whole_body"})),
    "bye": VoicePresetMotion("bye", "拜拜", "whole_body", frozenset({"whole_body"})),
    "lightwave": VoicePresetMotion("lightwave", "动感光波", "whole_body", frozenset({"whole_body"})),
    "scratch_head": VoicePresetMotion("scratch_head", "挠头", "whole_body", frozenset({"whole_body"})),
    "scratch_back": VoicePresetMotion("scratch_back", "抓屁股", "whole_body", frozenset({"whole_body"})),
}

_ALIASES = {
    "wave": ("挥手", "挥挥手", "挥一下手", "摆手", "摆摆手", "打招呼", "打个招呼"),
    "handshake": ("握手", "握个手", "握一下手", "握握手"),
    "raise_hand": ("举手", "举一下手", "抬手", "抬一下手"),
    "flying_kiss": ("飞吻", "送飞吻", "送个飞吻", "送一个飞吻", "亲一个"),
    "sweetheart": ("比心", "比个心", "比爱心", "比个爱心", "做心形"),
    "high_five": ("击掌", "击个掌", "来个击掌", "拍手"),
    "salute": ("敬礼", "敬个礼"),
    "horizontal_arm": ("平举", "平举双手", "双手平举", "手臂平举"),
    "hug": ("拥抱", "抱抱", "做个拥抱"),
    "cheer": ("加油动作", "做个加油", "来个加油动作"),
    "clap": ("鼓掌", "鼓掌一下", "拍手鼓掌"),
    "handx": ("双手打叉", "打叉", "双手交叉"),
    "chestwave": ("胸前挥手", "在胸前挥手", "胸前挥挥手"),
    "bow": ("鞠躬", "鞠个躬"),
    "bye": ("拜拜", "挥手告别", "说再见动作"),
    "lightwave": ("动感光波", "发射动感光波"),
    "scratch_head": ("挠头", "挠挠头", "抓抓头"),
    "scratch_back": ("抓屁股", "挠屁股"),
}

_PREFIXES = (
    "我想让你", "麻烦你", "请你", "给大家", "给我们", "我想让", "麻烦",
    "帮我", "给我", "跟我", "和我", "向我", "请", "用",
    "来一个", "来个", "做一个", "做个", "做一下", "做", "摆一个", "摆个", "摆", "让你",
)
_SUFFIXES = ("给我看看", "给大家看看", "一下", "一个", "动作", "吧", "呗", "呀", "啊", "好吗")
_NEGATION_MARKERS = (
    "不要", "不用", "不想", "不需要", "不许", "不能", "禁止", "取消", "停止",
    "他说", "她说", "他们说", "刚才说", "转述", "转告", "听说", "据说", "提到",
    "如果", "假如", "要是", "倘若", "万一", "除非", "否則", "否则",
)
_QUESTION_MARKERS = (
    "吗", "么", "能不能", "可不可以", "可以不可以", "能否", "会不会", "是否",
    "怎么做", "是什么",
)
_QUOTE_MARKERS = ('"', "'", "“", "”", "‘", "’", "「", "」", "『", "』", "《", "》")


def resolve_voice_preset_motion(name: object, area: object = None) -> tuple[str, str] | None:
    normalized_name = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    motion = VOICE_PRESET_MOTIONS.get(normalized_name)
    if motion is None:
        return None
    normalized_area = str(area or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized_area:
        normalized_area = motion.default_area
    if normalized_area not in motion.allowed_areas:
        return None
    return motion.name, normalized_area


def submit_voice_preset_motion(
    text: object,
    name: object,
    area: object,
    navigation_status: object,
    task_active: object,
    motion_controller: object,
) -> dict[str, object]:
    voice_selection = resolve_voice_command(text)
    requested_selection = resolve_voice_preset_motion(name, area)
    if voice_selection is None or requested_selection != voice_selection:
        return {
            "ok": False,
            "accepted": False,
            "status": "command_rejected",
            "message": "动作口令未通过白名单核验",
        }

    block_reason = voice_preset_motion_block_reason(navigation_status, task_active)
    if block_reason:
        message = (
            "机器人导航中，暂不执行动作"
            if block_reason == "navigation_active"
            else "讲解任务进行中或已暂停，暂不执行动作"
        )
        return {
            "ok": False,
            "accepted": False,
            "status": block_reason,
            "message": message,
        }

    if not motion_controller.services_ready().get("motion", False):
        return {
            "ok": False,
            "accepted": False,
            "status": "motion_service_unavailable",
            "message": "动作服务当前不可用",
        }

    future = motion_controller.play_preset_motion(
        requested_selection[0],
        requested_selection[1],
        interrupt=False,
    )
    if future is None:
        return {
            "ok": False,
            "accepted": False,
            "status": "not_submitted",
            "message": "动作请求未能提交，请稍后再试",
        }
    return {
        "ok": True,
        "accepted": True,
        "submitted": True,
        "status": "submitted",
        "motion": requested_selection[0],
        "area": requested_selection[1],
        "message": f"{VOICE_PRESET_MOTIONS[requested_selection[0]].label}动作请求已受理",
    }


def resolve_voice_command(text: object) -> tuple[str, str] | None:
    raw = str(text or "").strip()
    if not raw or has_negation_or_quoted_speech(raw):
        return None
    if any(marker in raw for marker in ("?", "？")):
        return None

    normalized = _normalize_speech(raw)
    if any(marker in normalized for marker in _QUESTION_MARKERS):
        return None
    command = normalized
    for wake_word in ("你好灵犀", "嗨灵犀", "灵犀", "灵心"):
        if command.startswith(wake_word):
            command = command[len(wake_word):]
            break
    for _ in range(4):
        prefix = next((value for value in _PREFIXES if command.startswith(value)), "")
        if not prefix:
            break
        command = command[len(prefix):]
    command_with_area = command
    area = ""
    for marker, value in (("左手", "left_hand"), ("右手", "right_hand"), ("双手", "both_hands"), ("两只手", "both_hands")):
        if marker in command:
            area = value
            command = command.replace(marker, "")
            break
    for suffix in _SUFFIXES:
        if any(command in aliases for aliases in _ALIASES.values()):
            break
        if command.endswith(suffix):
            command = command[:-len(suffix)]

    canonical = next(
        (name for name, aliases in _ALIASES.items() if command in aliases),
        None,
    )
    if canonical is None:
        return None
    selection = resolve_voice_preset_motion(canonical, area)
    if selection is not None:
        return selection
    if area and next(
        (name for name, aliases in _ALIASES.items() if command_with_area in aliases),
        None,
    ) == canonical:
        return resolve_voice_preset_motion(canonical)
    return None


def has_negation_or_quoted_speech(text: object) -> bool:
    raw = str(text or "")
    normalized = _normalize_speech(raw)
    if any(marker in raw for marker in _QUOTE_MARKERS):
        return True
    if any(marker in normalized for marker in _NEGATION_MARKERS):
        return True
    negation = normalized.find("别")
    return negation >= 0 and any(
        normalized.find(alias, negation + 1) >= 0
        for aliases in _ALIASES.values()
        for alias in aliases
    )


def voice_preset_motion_block_reason(navigation_status: object, task_active: object) -> str:
    if str(navigation_status or "unknown").strip().lower() != "idle":
        return "navigation_active"
    if bool(task_active):
        return "tour_active"
    return ""


def _normalize_speech(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )
