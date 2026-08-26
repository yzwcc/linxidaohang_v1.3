from robot_local_navigation_v3.voice_nav_gateway import uses_navigation_upstream


def test_navigation_queries_use_navigation_bridge():
    assert uses_navigation_upstream("GET", "/points")
    assert uses_navigation_upstream("GET", "/nav/status?fresh=1")
    assert uses_navigation_upstream("POST", "/nav/start")
    assert uses_navigation_upstream("POST", "/task/pause")


def test_interaction_and_tts_use_protected_navigation_bridge():
    assert uses_navigation_upstream("POST", "/interaction/mark")
    assert uses_navigation_upstream("POST", "/interaction/end")
    assert uses_navigation_upstream("POST", "/motion/stop")
    assert uses_navigation_upstream("POST", "/tts/speak")


def test_low_level_voice_features_stay_on_voice_bridge():
    assert not uses_navigation_upstream("GET", "/health")
    assert not uses_navigation_upstream("POST", "/entertainment/dance")


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项语音导航网关路由测试")
