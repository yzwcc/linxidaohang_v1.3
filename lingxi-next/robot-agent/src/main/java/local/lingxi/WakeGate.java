package local.lingxi;

import com.alibaba.fastjson2.JSONObject;
import java.util.HashSet;
import java.util.Set;

/** Consumes documented listening status, never a word match in an ASR string. */
final class WakeGate {
    private String session;
    private long expiresAt;
    private final Set<String> seen = new HashSet<>();

    synchronized String acceptListening(JSONObject state, long now) {
        long at = state.getLongValue("updated_at_ms");
        if (at <= 0 || Math.abs(now - at) > 30_000) return null;
        String id = state.getString("event_id");
        if (id == null || id.isBlank() || id.length() > 200) return null;
        if (!"listening".equals(state.getString("state")) ||
                !"already_wake_up".equals(state.getString("wakeup_state"))) {
            session = null;
            expiresAt = 0;
            return null;
        }
        String key = id;
        if (!seen.add(key)) return null;
        if (seen.size() > 2048) { seen.clear(); seen.add(key); }
        session = id;
        expiresAt = now + 60_000;
        return id;
    }

    synchronized String current(long now) {
        if (expiresAt <= now) session = null;
        return session;
    }

    synchronized void clear() { session = null; expiresAt = 0; seen.clear(); }
}
