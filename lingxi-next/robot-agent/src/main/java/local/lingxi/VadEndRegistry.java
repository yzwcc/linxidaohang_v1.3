package local.lingxi;

import com.alibaba.fastjson2.JSONObject;
import java.util.ArrayDeque;
import java.util.LinkedHashMap;
import java.util.Map;

/** Matches VAD EOS timestamps to the following ASR callback by dialog event ID. */
final class VadEndRegistry {
    private static final long MAX_AGE_MS = 30_000;
    private static final long RETENTION_MS = 60_000;
    private static final int MAX_EVENTS = 2_000;
    private static final int MAX_ENDS_PER_EVENT = 8;

    private record Entry(long timestamp, long receivedAt) {}

    private final Map<String, Entry> latest = new LinkedHashMap<>();
    private final Map<String, ArrayDeque<Long>> pending = new LinkedHashMap<>();

    synchronized void accept(JSONObject state, long now) {
        prune(now);
        if (!"eos".equals(state.getString("state"))) return;
        String eventId = state.getString("event_id");
        long timestamp = state.getLongValue("updated_at_ms");
        if (eventId == null || eventId.isBlank() || eventId.length() > 180 ||
                timestamp <= 0 || timestamp > now + 2_000 || now - timestamp > MAX_AGE_MS) return;
        Entry previous = latest.get(eventId);
        if (previous != null && timestamp <= previous.timestamp()) return;

        latest.put(eventId, new Entry(timestamp, now));
        ArrayDeque<Long> ends = pending.computeIfAbsent(eventId, ignored -> new ArrayDeque<>());
        ends.addLast(timestamp);
        while (ends.size() > MAX_ENDS_PER_EVENT) ends.removeFirst();
        while (latest.size() > MAX_EVENTS) {
            String oldest = latest.keySet().iterator().next();
            latest.remove(oldest);
            pending.remove(oldest);
        }
    }

    synchronized Long consume(String eventId, long now) {
        prune(now);
        ArrayDeque<Long> ends = pending.remove(eventId);
        if (ends == null || ends.isEmpty()) return null;
        long timestamp = ends.peekLast();
        if (timestamp > now || now - timestamp > MAX_AGE_MS) return null;
        return timestamp;
    }

    private void prune(long now) {
        latest.entrySet().removeIf(entry -> now - entry.getValue().receivedAt() > RETENTION_MS);
        pending.keySet().retainAll(latest.keySet());
    }
}
