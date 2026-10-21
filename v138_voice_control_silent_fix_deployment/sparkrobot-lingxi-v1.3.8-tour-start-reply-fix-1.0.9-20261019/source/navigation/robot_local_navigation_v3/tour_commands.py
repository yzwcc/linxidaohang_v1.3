from __future__ import annotations

import re
import unicodedata
from typing import Any


MIN_COMMAND_LENGTH = 2
MAX_COMMAND_LENGTH = 30
MAX_START_PHRASES = 10
MAX_FUZZY_GAP = 4
MAX_FUZZY_TOTAL_GAP = 8
NEGATION_PREFIX_TOKENS = (
    "不要",
    "不需要",
    "不用",
    "不必",
    "别",
    "别再",
    "先不",
    "暂不",
    "无需",
    "没必要",
)

_PHRASE_SPLIT_RE = re.compile(r"[\n,，;；]+")


def normalize_command_text(value: Any) -> str:
    """Return a comparison form without spacing or punctuation."""

    text = str(value or "").strip().casefold()
    return "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))
    )


def collapse_adjacent_duplicate_chars(value: Any) -> str:
    """Return a normalized form with repeated adjacent characters collapsed."""

    text = normalize_command_text(value)
    if not text:
        return ""
    collapsed: list[str] = [text[0]]
    for char in text[1:]:
        if char != collapsed[-1]:
            collapsed.append(char)
    return "".join(collapsed)


def normalize_command_topic(value: Any) -> str:
    topic = " ".join(str(value or "").strip().split())
    normalized = normalize_command_text(topic)
    if not normalized:
        return ""
    if len(normalized) < MIN_COMMAND_LENGTH or len(normalized) > MAX_COMMAND_LENGTH:
        return ""
    return topic


def normalize_start_phrases(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = _PHRASE_SPLIT_RE.split(value)
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    phrases: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        phrase = " ".join(str(item or "").strip().split())
        normalized = normalize_command_text(phrase)
        if (
            len(normalized) < MIN_COMMAND_LENGTH
            or len(normalized) > MAX_COMMAND_LENGTH
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        phrases.append(phrase)
        if len(phrases) >= MAX_START_PHRASES:
            break
    return phrases


def build_task_command_catalog(
    tasks: list[dict[str, Any]],
    *,
    active_task_id: str = "",
) -> dict[str, Any]:
    catalog: list[dict[str, Any]] = []
    phrase_owners: dict[str, list[str]] = {}
    for task in tasks:
        if not isinstance(task, dict) or not task.get("enabled", True):
            continue
        task_id = str(task.get("id", "")).strip()
        if not task_id:
            continue
        topic = normalize_command_topic(task.get("command_topic"))
        phrases = normalize_start_phrases(task.get("start_phrases", task.get("trigger_phrases", [])))
        if topic:
            normalized_topic = normalize_command_text(topic)
            if normalized_topic not in {normalize_command_text(item) for item in phrases}:
                phrases.insert(0, topic)
        phrases = phrases[:MAX_START_PHRASES]
        for phrase in phrases:
            phrase_owners.setdefault(normalize_command_text(phrase), []).append(task_id)
        catalog.append({
            "task_id": task_id,
            "task_name": str(task.get("name", task_id)).strip() or task_id,
            "command_topic": topic,
            "start_phrases": phrases,
            "active": task_id == active_task_id,
        })

    conflicts = [
        {"phrase": phrase, "task_ids": sorted(set(task_ids))}
        for phrase, task_ids in phrase_owners.items()
        if len(set(task_ids)) > 1
    ]
    return {"tasks": catalog, "conflicts": conflicts}


def match_task_command(text: Any, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_text = normalize_command_text(text)
    if not normalized_text:
        return {"matched": False, "status": "empty_text"}

    candidates: list[tuple[tuple[int, int, int], dict[str, Any], str, str]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id", task.get("id", ""))).strip()
        if not task_id:
            continue
        phrases = task.get("start_phrases", [])
        if not isinstance(phrases, list):
            continue
        for raw_phrase in phrases:
            phrase = str(raw_phrase or "").strip()
            normalized_phrase = normalize_command_text(phrase)
            if len(normalized_phrase) < MIN_COMMAND_LENGTH:
                continue
            score, match_type = _phrase_score(normalized_text, normalized_phrase)
            if score is not None:
                candidates.append((score, task, phrase, match_type))

    if not candidates:
        return {"matched": False, "status": "no_match"}
    best_score = max(item[0] for item in candidates)
    best = [item for item in candidates if item[0] == best_score]
    task_ids = {str(item[1].get("task_id", item[1].get("id", ""))) for item in best}
    if len(task_ids) != 1:
        return {
            "matched": False,
            "status": "conflict",
            "message": "命令词同时匹配多个讲解任务，请在网页中调整命令词",
            "task_ids": sorted(task_ids),
        }

    _, task, phrase, match_type = best[0]
    return {
        "matched": True,
        "status": "matched",
        "task_id": str(task.get("task_id", task.get("id", ""))),
        "task_name": str(task.get("task_name", task.get("name", ""))),
        "matched_phrase": phrase,
        "matched_by": match_type,
    }


def _phrase_score(text: str, phrase: str) -> tuple[tuple[int, int, int] | None, str]:
    base_score = _phrase_score_core(text, phrase)
    if base_score[0] is not None:
        if _is_negated_match(text, phrase, base_score[1]):
            return None, ""
        return base_score

    collapsed_text = collapse_adjacent_duplicate_chars(text)
    collapsed_phrase = collapse_adjacent_duplicate_chars(phrase)
    if collapsed_text == text and collapsed_phrase == phrase:
        return None, ""

    repeat_score = _phrase_score_core(collapsed_text, collapsed_phrase)
    if repeat_score[0] is None:
        return None, ""

    score, match_type = repeat_score
    if _is_negated_match(text, collapsed_phrase, match_type):
        return None, ""
    return (0, score[1], score[2]), f"repeat_{match_type}"


def _phrase_score_core(text: str, phrase: str) -> tuple[tuple[int, int, int] | None, str]:
    if text == phrase:
        return (3, len(phrase), 0), "exact"
    if phrase in text:
        return (2, len(phrase), 0), "contains"
    gap_count = _limited_gap_count(text, phrase)
    if gap_count is None:
        return None, ""
    return (1, len(phrase), -gap_count), "limited_gap"


def _is_negated_match(text: str, phrase: str, match_type: str) -> bool:
    if not phrase:
        return False
    if match_type in {"exact", "contains"}:
        anchor = text.find(phrase)
    else:
        anchor = text.find(phrase[0])
    if anchor <= 0:
        return False
    prefix = text[max(0, anchor - 6) : anchor]
    return any(token in prefix for token in NEGATION_PREFIX_TOKENS)


def _limited_gap_count(text: str, phrase: str) -> int | None:
    first = phrase[0]
    start = text.find(first)
    while start >= 0:
        cursor = start
        total_gap = 0
        matched = True
        for char in phrase[1:]:
            next_index = text.find(char, cursor + 1, cursor + MAX_FUZZY_GAP + 2)
            if next_index < 0:
                matched = False
                break
            total_gap += next_index - cursor - 1
            if total_gap > MAX_FUZZY_TOTAL_GAP:
                matched = False
                break
            cursor = next_index
        if matched:
            return total_gap
        start = text.find(first, start + 1)
    return None
