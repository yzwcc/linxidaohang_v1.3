from threading import Event, Thread

from robot_local_navigation_v3.tts_stream_protocol import TtsStreamProtocol


class FakePlayTts:
    def __init__(self):
        self.calls = []

    def speak(self, text, *, interrupt):
        self.calls.append((text, interrupt))


def test_stream_segments_are_ordered_and_only_first_interrupts():
    protocol = TtsStreamProtocol()
    speaker = FakePlayTts()

    first_interrupt = protocol.claim("reply-a", 0, 10.0)
    speaker.speak("第一句。", interrupt=first_interrupt)
    next_interrupt = protocol.claim("reply-a", 1, 10.1)
    speaker.speak("第二句。", interrupt=next_interrupt)
    last_interrupt = protocol.claim("reply-a", 2, 10.2)
    speaker.speak("结束。", interrupt=last_interrupt)

    assert speaker.calls == [
        ("第一句。", True),
        ("第二句。", False),
        ("结束。", False),
    ]


def test_rejects_out_of_order_duplicate_and_invalid_sequence():
    protocol = TtsStreamProtocol()
    protocol.claim("reply-b", 0, 10.0)

    for stream_id, sequence in (
        ("reply-b", 0),
        ("reply-b", 2),
        ("", 1),
        ("reply-b", True),
        ("reply-b", -1),
    ):
        try:
            protocol.claim(stream_id, sequence, 10.1)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection for {stream_id!r}/{sequence!r}")


def test_close_and_idle_expiry_release_stream_state():
    protocol = TtsStreamProtocol(idle_ttl_sec=5.0)
    assert protocol.claim("reply-c", 0, 10.0)
    protocol.close("reply-c", 10.1)

    try:
        protocol.claim("reply-c", 0, 11.0)
    except ValueError:
        pass
    else:
        raise AssertionError("closed stream id must not be restarted")
    assert protocol.claim("reply-c", 0, 15.2)

    try:
        protocol.claim("reply-c", 1, 21.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expired stream must not accept a stale continuation")


def test_stop_before_first_segment_rejects_late_stream_start():
    protocol = TtsStreamProtocol(idle_ttl_sec=5.0)
    protocol.close("reply-stop-first", 10.0)

    try:
        protocol.claim("reply-stop-first", 0, 10.1)
    except ValueError:
        pass
    else:
        raise AssertionError("a late first segment must not revive a stopped stream")


def test_dispatch_waiting_for_service_is_cancelled_by_stop():
    protocol = TtsStreamProtocol()
    assert protocol.claim("reply-wait", 0, 10.0)
    generation = protocol.generation
    service_ready = Event()
    dispatch_started = Event()
    dispatched = []
    result = []

    def delayed_dispatch():
        dispatch_started.set()
        service_ready.wait()
        result.append(
            protocol.dispatch_if_current(
                generation,
                "reply-wait",
                lambda: dispatched.append("old") or object(),
                now=10.2,
            )
        )

    worker = Thread(target=delayed_dispatch)
    worker.start()
    assert dispatch_started.wait(1.0)
    protocol.close("reply-wait", 10.1)
    service_ready.set()
    worker.join(1.0)

    assert not worker.is_alive()
    assert dispatched == []
    assert result == [None]


def test_new_stream_preempts_waiting_stream_and_stale_close_is_scoped():
    protocol = TtsStreamProtocol()
    assert protocol.claim("reply-old", 0, 10.0)
    old_generation = protocol.generation
    assert protocol.claim("reply-new", 0, 10.1)
    new_generation = protocol.generation
    dispatched = []

    assert protocol.dispatch_if_current(
        old_generation, "reply-old", lambda: dispatched.append("old"), now=10.2
    ) is None
    assert protocol.dispatch_if_current(
        new_generation,
        "reply-new",
        lambda: dispatched.append("new") or "submitted",
        now=10.2,
    ) == "submitted"
    protocol.close("reply-old", 10.3)
    assert protocol.claim("reply-new", 1, 10.4) is False
    assert dispatched == ["new"]


def test_continuation_submitted_before_stop_is_linearized_before_stop():
    protocol = TtsStreamProtocol()
    assert protocol.claim("reply-continuation-first", 0, 10.0)
    generation = protocol.generation
    assert protocol.claim("reply-continuation-first", 1, 10.1) is False
    dispatched = []

    result = protocol.dispatch_if_current(
        generation,
        "reply-continuation-first",
        lambda: dispatched.append("continuation") or "submitted",
        now=10.2,
    )
    assert result == "submitted"
    protocol.close("reply-continuation-first", 10.3)
    try:
        protocol.claim("reply-continuation-first", 2, 10.4)
    except ValueError:
        pass
    else:
        raise AssertionError("a stopped stream must reject later continuations")
    assert dispatched == ["continuation"]


def test_legacy_speech_invalidates_a_stream_waiting_for_service():
    protocol = TtsStreamProtocol()
    assert protocol.claim("reply-before-legacy", 0, 10.0)
    stream_generation = protocol.generation
    legacy_generation = protocol.invalidate(10.1)
    dispatched = []

    assert protocol.dispatch_if_current(
        stream_generation,
        "reply-before-legacy",
        lambda: dispatched.append("stale-stream"),
        now=10.2,
    ) is None
    assert protocol.dispatch_if_current(
        legacy_generation,
        None,
        lambda: dispatched.append("legacy") or "submitted",
        now=10.2,
    ) == "submitted"
    assert dispatched == ["legacy"]


if __name__ == "__main__":
    tests = [
        value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项流式 TTS 序号协议测试")
