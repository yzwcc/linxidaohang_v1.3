package local.lingxi;

import com.alibaba.fastjson2.JSONObject;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PlaybackTest {
    private JSONObject status(String state, String item, String event, long at) {
        JSONObject value = new JSONObject();
        value.put("state", state);
        value.put("item_id", item);
        value.put("event_id", event);
        value.put("updated_at_ms", at);
        return value;
    }

    @Test void requiresMatchingPlayingAndFinishedForCompletion() {
        long now = System.currentTimeMillis();
        var playback = new AgentMain.Playback("reply", "event", "item");
        playback.accept(status("finished", "other", "event", now), now);
        assertFalse(playback.complete.isDone());
        playback.accept(status("playing", "item", "event", now), now);
        playback.accept(status("finished", "item", "event", now), now);
        assertTrue(playback.complete.isDone());
        assertTrue(playback.complete.join() >= 0);
    }

    @Test void doesNotInventFirstAudioWhenOnlyFinishedIsReceived() {
        long now = System.currentTimeMillis();
        var playback = new AgentMain.Playback("reply", "event", "item");
        playback.accept(status("finished", "item", "event", now), now);
        assertTrue(playback.complete.isCompletedExceptionally());
        assertTrue(playback.terminated.isDone());
    }

    @Test void timeoutRetainsLeaseUntilMatchingTerminalState() {
        long now = System.currentTimeMillis();
        var playback = new AgentMain.Playback("tour", "flow-event", null);
        var released = new AtomicBoolean();
        playback.terminated.thenRun(() -> released.set(true));
        playback.complete.completeExceptionally(new IllegalStateException("offline"));
        assertFalse(released.get());
        playback.accept(status("interrupted", null, "wrong-event", now), now);
        assertFalse(released.get());
        playback.accept(status("interrupted", null, "flow-event", now), now);
        assertTrue(released.get());
        assertTrue(playback.complete.isCompletedExceptionally());
    }

    @Test void ignoresStaleStatusAndUnconfirmedTaskflowEvents() {
        long now = System.currentTimeMillis();
        var playback = new AgentMain.Playback("tour", null, null);
        playback.accept(status("finished", null, null, now), now);
        assertFalse(playback.terminated.isDone());
        playback.event = "flow";
        playback.accept(status("playing", null, "flow", now - 31_000), now);
        playback.accept(status("finished", null, "flow", now - 31_000), now);
        assertFalse(playback.complete.isDone());
        assertFalse(playback.terminated.isDone());
    }
}
