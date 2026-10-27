package local.lingxi;

import com.alibaba.fastjson2.JSONObject;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class WakeGateTest {
    private JSONObject wake(long now) {
        return JSONObject.of("state", "listening", "wakeup_state", "already_wake_up", "event_id", "session", "updated_at_ms", now);
    }
    @Test void rejectsMissingOrStaleEvidence() {
        WakeGate gate = new WakeGate();
        assertNull(gate.current(100_000));
        assertNull(gate.acceptListening(JSONObject.of("text", "你好灵犀"), 100_000));
        assertNull(gate.acceptListening(wake(10), 100_000));
    }
    @Test void acceptsOnlyRealStateAndDeduplicatesIt() {
        WakeGate gate = new WakeGate();
        assertEquals("session", gate.acceptListening(wake(100_000), 100_000));
        assertNull(gate.acceptListening(wake(100_000), 100_001));
        assertEquals("session", gate.current(100_002));
        assertNull(gate.current(161_000));
    }
    @Test void endsSessionOnSilentState() {
        WakeGate gate = new WakeGate();
        gate.acceptListening(wake(100_000), 100_000);
        gate.acceptListening(JSONObject.of("state", "non_wakeup_silent", "wakeup_state", "idle",
                "event_id", "session", "updated_at_ms", 100_010), 100_010);
        assertNull(gate.current(100_011));
    }
    @Test void rejectsLateWakeAndLateSilenceFromPreviousSession() {
        WakeGate gate = new WakeGate();
        gate.acceptListening(wake(100_000), 100_000);
        var next = wake(100_100);
        next.put("event_id", "next");
        assertEquals("next", gate.acceptListening(next, 100_100));
        gate.acceptListening(JSONObject.of("state", "non_wakeup_silent", "event_id", "session",
                "updated_at_ms", 100_101), 100_102);
        assertNull(gate.acceptListening(wake(100_050), 100_103));
        assertEquals("next", gate.current(100_104));
    }
    @Test void ignoresOlderSilenceForSameSessionAndUnknownStates() {
        WakeGate gate = new WakeGate();
        gate.acceptListening(wake(100_000), 100_000);
        gate.acceptListening(JSONObject.of("state", "non_wakeup_silent", "event_id", "session",
                "updated_at_ms", 99_999), 100_001);
        gate.acceptListening(JSONObject.of("state", "unrecognized", "event_id", "session",
                "updated_at_ms", 100_001), 100_002);
        assertEquals("session", gate.current(100_003));
    }
    @Test void disconnectRetainsReplayBarrierAndRequiresNewEvidence() {
        WakeGate gate = new WakeGate();
        gate.acceptListening(wake(100_000), 100_000);
        gate.clear(100_010);
        assertNull(gate.current(100_011));
        assertNull(gate.acceptListening(wake(100_000), 100_020));
        assertNull(gate.acceptListening(wake(100_030), 100_030));
        var next = wake(100_031);
        next.put("event_id", "next");
        assertEquals("next", gate.acceptListening(next, 100_031));
    }
    @Test void activeRecognitionRenewsOnlyItsOwnLiveSession() {
        WakeGate gate = new WakeGate();
        gate.acceptListening(wake(100_000), 100_000);
        gate.renew("other", 150_000);
        gate.renew("session", 159_000);
        assertEquals("session", gate.current(200_000));
        gate.renew("session", 220_000);
        assertNull(gate.current(220_001));
    }
    @Test void repeatedFreshListeningSnapshotRenewsButNeverReopensEndedSession() {
        WakeGate gate = new WakeGate();
        gate.acceptListening(wake(100_000), 100_000);
        var refreshed = wake(150_000);
        assertNull(gate.acceptListening(refreshed, 150_000));
        assertEquals("session", gate.current(209_999));
        gate.acceptListening(JSONObject.of("state", "non_wakeup_silent", "event_id", "session",
                "updated_at_ms", 210_000), 210_000);
        assertNull(gate.acceptListening(wake(220_000), 220_000));
    }
}
