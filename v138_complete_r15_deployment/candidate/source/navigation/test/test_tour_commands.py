from robot_local_navigation_v3.tour_commands import (
    build_task_command_catalog,
    match_task_command,
    normalize_command_text,
    normalize_start_phrases,
)


TASKS = [
    {
        "id": "business",
        "name": "业务办理",
        "enabled": True,
        "command_topic": "办理业务",
        "start_phrases": ["业务办理讲解"],
    },
    {
        "id": "showroom",
        "name": "展厅参观",
        "enabled": True,
        "command_topic": "参观展厅",
        "start_phrases": ["展厅讲解"],
    },
]

REPEATED_TASKS = [
    {
        "id": "business_typo",
        "name": "业务办理",
        "enabled": True,
        "command_topic": "办理业业务",
        "start_phrases": ["办理业业务"],
    },
]


def catalog():
    return build_task_command_catalog(TASKS, active_task_id="business")["tasks"]


def repeated_catalog():
    return build_task_command_catalog(REPEATED_TASKS, active_task_id="business_typo")["tasks"]


def test_command_normalization_and_deduplication():
    assert normalize_command_text(" 我想：办理 业务！") == "我想办理业务"
    assert normalize_start_phrases(["办理业务", "办理 业务", "x", "展厅讲解"]) == [
        "办理业务",
        "展厅讲解",
    ]


def test_exact_and_sentence_containment_match():
    exact = match_task_command("办理业务", catalog())
    assert exact["matched"] and exact["task_id"] == "business"
    assert exact["matched_by"] == "exact"
    sentence = match_task_command("我需要办理业务", catalog())
    assert sentence["matched"] and sentence["task_id"] == "business"
    assert sentence["matched_by"] == "contains"


def test_limited_gap_match_keeps_character_order():
    matched = match_task_command("你带我办理一下业务吧", catalog())
    assert matched["matched"] and matched["task_id"] == "business"
    assert matched["matched_by"] == "limited_gap"
    assert not match_task_command("业务先不办理", catalog())["matched"]


def test_repeated_adjacent_character_fallback_matches_typo_topic():
    exact = match_task_command("我需要办理业务", repeated_catalog())
    assert exact["matched"] and exact["task_id"] == "business_typo"
    assert exact["matched_by"].startswith("repeat_")

    sentence = match_task_command("你带我办理一下业务吧", repeated_catalog())
    assert sentence["matched"] and sentence["task_id"] == "business_typo"
    assert sentence["matched_by"].startswith("repeat_")


def test_negation_sentence_is_not_matched_by_repeated_fallback():
    assert not match_task_command("我不需要办理业务", repeated_catalog())["matched"]


def test_equal_score_across_tasks_is_reported_as_conflict():
    tasks = [
        {"task_id": "one", "task_name": "一", "start_phrases": ["办理业务"]},
        {"task_id": "two", "task_name": "二", "start_phrases": ["办理业务"]},
    ]
    result = match_task_command("办理业务", tasks)
    assert not result["matched"]
    assert result["status"] == "conflict"


def test_equal_score_across_tasks_is_reported_as_conflict_for_repeated_fallback():
    tasks = [
        {"task_id": "one", "task_name": "一", "start_phrases": ["办理业业务"]},
        {"task_id": "two", "task_name": "二", "start_phrases": ["办理业业务"]},
    ]
    result = match_task_command("我需要办理业务", tasks)
    assert not result["matched"]
    assert result["status"] == "conflict"


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项讲解命令匹配测试")
