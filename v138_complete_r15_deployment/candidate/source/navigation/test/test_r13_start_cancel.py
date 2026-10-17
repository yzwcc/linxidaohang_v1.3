"""Cancellation must invalidate starts that are still probing the ROS graph."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test.test_pnc_discovery import FakeNode, navigation_client


def test_stop_during_preflight_never_submits_a_late_start():
    client = navigation_client.NavigationClient(FakeNode(), SimpleNamespace(timeout_sec=30))
    entered, release = threading.Event(), threading.Event()
    results = []

    def preflight():
        entered.set()
        assert release.wait(3), "test did not release preflight"
        return None

    client._preflight_start = preflight
    point = SimpleNamespace(id="p1", name="point one")
    session = Mock()
    session.start.return_value = navigation_client.NavigationSessionHandle(
        task_id=2, goal_id="p1", point_name="point one", accepted=True, status="started"
    )
    with patch.object(navigation_client, "NavigationSession", return_value=session) as factory:
        thread = threading.Thread(target=lambda: results.append(client.start(point)))
        thread.start()
        try:
            assert entered.wait(3)
            cancelled = client.cancel("operator stop")
        finally:
            release.set()
            thread.join(3)
        assert not thread.is_alive()
        assert cancelled, "a pending start must count as cancelled"
        assert len(results) == 1 and not results[0].accepted
        assert results[0].status == "cancelled"
        factory.assert_not_called()
        client._preflight_start = lambda: None
        assert client.start(point).accepted, "a later explicit start must still work"
        assert factory.call_count == 1


def test_preflight_exception_does_not_leave_a_phantom_pending_start():
    client = navigation_client.NavigationClient(FakeNode(), SimpleNamespace(timeout_sec=30))
    client._preflight_start = Mock(side_effect=RuntimeError("probe failed"))
    try:
        client.start(SimpleNamespace(id="p1", name="point one"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("the probe failure should propagate")
    assert not client.cancel(), "failed probe must not remain pending"
