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
}
