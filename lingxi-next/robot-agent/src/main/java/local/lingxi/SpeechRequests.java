package local.lingxi;

import java.util.HashMap;
import java.util.Map;

/** Callers synchronize registration/emission and cancellation on this object. */
final class SpeechRequests {
    private final Map<String, Long> cancelled = new HashMap<>();

    synchronized void check(String correlation, long issuedAt, long now, boolean liveReply) {
        prune(now);
        if (issuedAt <= 0 || issuedAt < now - 30_000 || issuedAt > now + 2000 ||
                cancelled.containsKey(correlation) || cancelled.size() >= 5000) {
            throw new IllegalStateException("Speech request expired or cancelled");
        }
        if (correlation.startsWith("reply:") && !liveReply) {
            throw new IllegalStateException("Passive reply is no longer available");
        }
    }

    synchronized void cancel(String correlation, long now) {
        prune(now);
        if (!cancelled.containsKey(correlation) && cancelled.size() >= 5000) {
            throw new IllegalStateException("Cancellation capacity reached");
        }
        cancelled.put(correlation, now);
    }

    private void prune(long now) {
        cancelled.entrySet().removeIf(entry -> entry.getValue() < now - 600_000);
    }
}
