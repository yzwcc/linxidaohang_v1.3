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
    protocol.close("reply-c")
    assert protocol.claim("reply-c", 0, 10.1)

    try:
        protocol.claim("reply-c", 1, 16.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expired stream must not accept a stale continuation")
    assert protocol.claim("reply-c", 0, 16.1)


if __name__ == "__main__":
    tests = [
        value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项流式 TTS 序号协议测试")
