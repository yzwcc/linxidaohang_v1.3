package local.lingxi;

import com.alibaba.fastjson2.JSONObject;
import java.util.LinkedHashMap;
import java.util.Map;

/** Consumes documented listening status, never a word match in an ASR string. */
final class WakeGate {
    private String session;
    private long expiresAt;
    private long latestAt;
    private final Map<String, Long> seen = new LinkedHashMap<>();

    synchronized String acceptListening(JSONObject state, long now) {
        long at = state.getLongValue("updated_at_ms");
        if (at <= 0 || Math.abs(now - at) > 30_000 || at < latestAt) return null;
        String id = state.getString("event_id");
        if (id == null || id.isBlank() || id.length() > 180) return null;
        seen.entrySet().removeIf(entry -> entry.getValue() < now - 600_000);
        if (!"listening".equals(state.getString("state")) ||
                !"already_wake_up".equals(state.getString("wakeup_state"))) {
            if (!"non_wakeup_silent".equals(state.getString("state"))) return null;
            latestAt = at;
            if (!id.equals(session)) {
                seen.putIfAbsent(id, now);
                return null;
            }
            seen.putIfAbsent(id, now);
            session = null;
            expiresAt = 0;
            return null;
        }
        if (id.equals(session)) {
            if (at > latestAt) {
                latestAt = at;
                expiresAt = now + 60_000;
            }
            return null;
        }
        if (seen.containsKey(id) || at <= latestAt || seen.size() >= 5000) return null;
        latestAt = at;
        seen.put(id, now);
        session = id;
        expiresAt = now + 60_000;
        return id;
    }

    synchronized String current(long now) {
        if (expiresAt <= now) session = null;
        return session;
    }

    synchronized void renew(String id, long now) {
        if (id.equals(current(now))) expiresAt = now + 60_000;
    }

    synchronized void clear(long now) {
        session = null;
        expiresAt = 0;
        latestAt = Math.max(latestAt, now);
    }
}
