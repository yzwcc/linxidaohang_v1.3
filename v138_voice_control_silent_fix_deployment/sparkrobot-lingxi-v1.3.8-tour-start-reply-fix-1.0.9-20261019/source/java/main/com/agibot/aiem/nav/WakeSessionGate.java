package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentMeta;
import com.agibot.aiem.sdk.AgentParam;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** Enforces wake-word sessions independently from optional audio-quality metadata. */
final class WakeSessionGate {
    private static final List<String> WAKE_KEYS = List.of(
            "kws_detected", "wake_detected", "wake_active", "wake_word_detected");
    private static final List<String> LISTENING_STATE_NAMES = List.of(
            "listening_status", "listeningstatus", "wakeup_status", "wakeupstatus", "wake_status");
    private static final List<String> LISTENING_STATE_KEYS = List.of(
            "state", "listening_state", "listeningStatus", "listeningState", "status");
    private static final List<String> WAKE_STATE_KEYS = List.of(
            "wakeup_state", "wakeupState", "wake_state", "wakeState", "wakeup");
    private static final String DEFAULT_AGENT = "_default";
    private static final int MAX_SEEN_WAKE_EVENT_IDS = 128;

    enum Mode {
        OFF,
        OBSERVE,
        ENFORCE;

        static Mode parse(String value) {
            return switch (value == null ? "" : value.trim().toLowerCase(Locale.ROOT)) {
                case "off" -> OFF;
                case "observe" -> OBSERVE;
                case "enforce", "" -> ENFORCE;
                default -> throw new IllegalArgumentException(
                        "voice.gate.wakeMode must be off, observe, or enforce");
            };
        }
    }

    private final Mode mode;
    private final boolean allowAddressedWake;
    private final long wakeSignalMaxAgeNanos;
    private final long sessionWindowNanos;
    private final LongSupplier nanoTime;
    private final Set<String> configuredAliases;
    private final Map<String, AgentState> agents = new HashMap<>();

    WakeSessionGate(AppConfig config) {
        this(config, System::nanoTime);
    }

    WakeSessionGate(AppConfig config, LongSupplier nanoTime) {
        mode = Mode.parse(config.voiceGateWakeMode);
        allowAddressedWake = config.voiceGateAllowAddressedWake;
        wakeSignalMaxAgeNanos = TimeUnit.MILLISECONDS.toNanos(config.voiceGateWakeSignalMaxAgeMs);
        sessionWindowNanos = TimeUnit.MILLISECONDS.toNanos(config.voiceGateSessionWindowMs);
        this.nanoTime = Objects.requireNonNull(nanoTime);
        configuredAliases = normalizeAliases(config.voiceGateAddressAliases);
    }

    synchronized OnlineObservation onRobotOnline(String agentId, AgentMeta meta) {
        AgentState state = state(agentId);
        state.online = true;
        LinkedHashSet<String> aliases = new LinkedHashSet<>(configuredAliases);
        String reported = meta == null ? "" : safe(meta.getWakeupWord());
        aliases.addAll(normalizeAliases(splitAliases(reported)));
        state.aliases = Set.copyOf(aliases);
        return new OnlineObservation(mode, reported, List.copyOf(state.aliases));
    }

    synchronized void onRobotOffline(String agentId) {
        AgentState state = state(agentId);
        state.online = false;
        closeSession(state);
        state.consumedWakeGeneration = state.wakeGeneration;
    }

    synchronized StateObservation onState(String agentId, String callbackEventId, String stateName, String stateValue) {
        String normalizedStateName = normalized(stateName);
        if (!LISTENING_STATE_NAMES.contains(normalizedStateName)) {
            return StateObservation.ignored(stateName);
        }
        final JSONObject payload;
        try {
            payload = JSON.parseObject(stateValue);
        } catch (RuntimeException invalid) {
            return new StateObservation(true, "", "", "", false, "invalid_json");
        }
        if (payload == null) {
            return new StateObservation(true, "", "", "", false, "empty_payload");
        }
        String listeningState = normalized(firstString(payload, LISTENING_STATE_KEYS));
        String wakeupState = normalized(firstString(payload, WAKE_STATE_KEYS));
        String payloadEventId = safe(payload.getString("event_id"));
        String effectiveEventId = payloadEventId.isBlank() ? safe(callbackEventId) : payloadEventId;
        AgentState state = state(agentId);
        long now = nanoTime.getAsLong();

        if (isSilentState(listeningState, wakeupState)) {
            closeSession(state);
            state.consumedWakeGeneration = state.wakeGeneration;
            state.lastListeningSignature = signature(listeningState, wakeupState, effectiveEventId,
                    payload.getLong("updated_at_ms"));
            return new StateObservation(true, listeningState, wakeupState, effectiveEventId, false,
                    "session_closed");
        }

        Boolean explicitWake = readBoolean(payload, WAKE_KEYS);
        boolean awake = ("listening".equals(listeningState)
                && isAwakeState(wakeupState)) || Boolean.TRUE.equals(explicitWake);
        String signature = signature(listeningState, wakeupState, effectiveEventId,
                payload.getLong("updated_at_ms"));
        if (awake && !signature.equals(state.lastListeningSignature)) {
            if (!effectiveEventId.isBlank() && state.seenWakeEventIds.contains(effectiveEventId)) {
                state.lastListeningSignature = signature;
                return new StateObservation(true, listeningState, wakeupState, effectiveEventId, false,
                        "wake_event_replay_ignored");
            }
            rememberWakeEvent(state, effectiveEventId);
            state.wakeGeneration++;
            state.wakeSignalNanos = now;
            state.wakeEventId = effectiveEventId;
        }
        state.lastListeningSignature = signature;
        return new StateObservation(true, listeningState, wakeupState, effectiveEventId, awake,
                awake ? "wake_signal_recorded" : "not_awake");
    }

    synchronized Decision admit(String agentId, String eventId, String text, AgentParam params) {
        if (mode == Mode.OFF) {
            return Decision.accept("wake_gate_off");
        }
        AgentState state = state(agentId);
        long now = nanoTime.getAsLong();
        String normalizedText = SpeechInputPolicy.normalize(text);
        if (text != null && SpeechInputPolicy.isLikelyNoise(text)) {
            return decide(false, "noise_or_low_information");
        }

        Boolean metadataWake;
        try {
            metadataWake = readMetadataWake(params);
        } catch (IllegalArgumentException invalid) {
            return decide(false, "invalid_wake_metadata");
        }
        if (Boolean.TRUE.equals(metadataWake)) {
            openSession(state, now);
            return Decision.accept("metadata_wake");
        }
        long wakeAge = now - state.wakeSignalNanos;
        boolean freshWake = state.wakeGeneration > state.consumedWakeGeneration
                && wakeAge >= 0
                && wakeAge <= wakeSignalMaxAgeNanos;
        boolean matchingEvent = freshWake
                && !safe(eventId).isBlank()
                && !state.wakeEventId.isBlank()
                && safe(eventId).equals(state.wakeEventId);
        if (freshWake && (matchingEvent || wakeAge <= wakeSignalMaxAgeNanos)) {
            state.consumedWakeGeneration = state.wakeGeneration;
            openSession(state, now);
            return Decision.accept(matchingEvent ? "state_wake_event_match" : "state_wake_recent");
        }

        long sessionAge = now - state.lastAcceptedNanos;
        if (state.sessionOpen && sessionAge >= 0 && sessionAge < sessionWindowNanos) {
            state.lastAcceptedNanos = now;
            return Decision.accept(Boolean.FALSE.equals(metadataWake)
                    ? "active_wake_session_no_new_kws"
                    : "active_wake_session");
        }
        // A negative KWS flag means this packet did not carry a new wake. It
        // blocks a cold request, but must not cancel an already-open session or
        // an independent positive SDK listening-state wake event.
        if (Boolean.FALSE.equals(metadataWake)) {
            return decide(false, "wake_explicitly_rejected");
        }
        if (!normalizedText.isBlank() && startsWithAlias(state, normalizedText)) {
            // A transcript containing the wake word is not proof that the local
            // KWS front end fired. Check real SDK/state evidence first because a
            // valid KWS event and its ASR text commonly carry the same wake word.
            // Text-only fallback remains observe-mode diagnostics, never enforce.
            if (allowAddressedWake && mode == Mode.OBSERVE) {
                openSession(state, now);
                return decide(false, "addressed_wake_observe_fallback");
            }
            return decide(false, "text_wake_fallback_disabled");
        }
        closeSession(state);
        return decide(false, "wake_required_or_session_expired");
    }

    synchronized Decision admitVerifiedSoc2Wake(String agentId, String eventId, long wakeAgeMs) {
        if (mode == Mode.OFF) return Decision.accept("wake_gate_off");
        if (wakeAgeMs < 0 || wakeAgeMs > TimeUnit.NANOSECONDS.toMillis(wakeSignalMaxAgeNanos)) {
            return decide(false, "soc2_log_wake_stale");
        }
        String normalizedEventId = safe(eventId);
        if (normalizedEventId.isBlank()) return decide(false, "soc2_log_event_id_missing");
        AgentState state = state(agentId);
        if (state.seenWakeEventIds.contains(normalizedEventId)) {
            return decide(false, "soc2_log_wake_replay_ignored");
        }
        long now = nanoTime.getAsLong();
        rememberWakeEvent(state, normalizedEventId);
        state.wakeGeneration++;
        state.consumedWakeGeneration = state.wakeGeneration;
        state.wakeSignalNanos = now - TimeUnit.MILLISECONDS.toNanos(wakeAgeMs);
        state.wakeEventId = normalizedEventId;
        openSession(state, now);
        return Decision.accept("soc2_log_wake_verified");
    }

    synchronized void reset() {
        for (AgentState state : agents.values()) {
            closeSession(state);
            state.consumedWakeGeneration = state.wakeGeneration;
        }
    }

    synchronized void reset(String agentId) {
        AgentState state = state(agentId);
        closeSession(state);
        state.consumedWakeGeneration = state.wakeGeneration;
    }

    Mode mode() {
        return mode;
    }

    private Decision decide(boolean accepted, String reason) {
        if (accepted || mode == Mode.ENFORCE) {
            return new Decision(accepted, reason, !accepted);
        }
        return new Decision(true, "observe_would_reject:" + reason, true);
    }

    private static void openSession(AgentState state, long now) {
        state.sessionOpen = true;
        state.lastAcceptedNanos = now;
    }

    private static void closeSession(AgentState state) {
        state.sessionOpen = false;
        state.lastAcceptedNanos = 0L;
    }

    private static void rememberWakeEvent(AgentState state, String eventId) {
        String normalizedEventId = safe(eventId);
        if (normalizedEventId.isBlank()) return;
        state.seenWakeEventIds.add(normalizedEventId);
        while (state.seenWakeEventIds.size() > MAX_SEEN_WAKE_EVENT_IDS) {
            Iterator<String> iterator = state.seenWakeEventIds.iterator();
            if (!iterator.hasNext()) return;
            iterator.next();
            iterator.remove();
        }
    }

    private AgentState state(String agentId) {
        return agents.computeIfAbsent(agentKey(agentId), ignored -> new AgentState(configuredAliases));
    }

    private static String agentKey(String agentId) {
        String value = safe(agentId);
        return value.isBlank() ? DEFAULT_AGENT : value;
    }

    private static boolean startsWithAlias(AgentState state, String normalizedText) {
        for (String alias : state.aliases) {
            if (!alias.isBlank() && normalizedText.startsWith(alias)) {
                return true;
            }
        }
        return false;
    }

    private static Boolean readMetadataWake(AgentParam params) {
        if (params == null || params.getExtParam() == null) {
            return null;
        }
        Map<?, ?> metadata = params.getExtParam();
        Boolean result = null;
        for (String key : WAKE_KEYS) {
            Object raw = metadata.get(key);
            if (raw == null) continue;
            Boolean value;
            if (raw instanceof Boolean bool) value = bool;
            else if (raw instanceof Number number
                    && (number.doubleValue() == 0.0 || number.doubleValue() == 1.0)) {
                value = number.doubleValue() == 1.0;
            } else if (raw instanceof String string && (string.trim().equalsIgnoreCase("true")
                    || string.trim().equals("1"))) {
                value = true;
            } else if (raw instanceof String string && (string.trim().equalsIgnoreCase("false")
                    || string.trim().equals("0"))) {
                value = false;
            } else {
                throw new IllegalArgumentException(key);
            }
            if (result != null && !result.equals(value)) {
                throw new IllegalArgumentException(key + "_conflict");
            }
            result = value;
        }
        return result;
    }

    private static String firstString(JSONObject payload, List<String> keys) {
        for (String key : keys) {
            String value = payload.getString(key);
            if (value != null && !value.isBlank()) return value;
        }
        return "";
    }

    private static Boolean readBoolean(JSONObject payload, List<String> keys) {
        Boolean result = null;
        for (String key : keys) {
            Object raw = payload.get(key);
            if (raw == null) continue;
            Boolean value;
            if (raw instanceof Boolean bool) value = bool;
            else if (raw instanceof Number number && (number.doubleValue() == 0.0 || number.doubleValue() == 1.0)) {
                value = number.doubleValue() == 1.0;
            } else if (raw instanceof String string && string.trim().equalsIgnoreCase("true")) {
                value = true;
            } else if (raw instanceof String string && string.trim().equalsIgnoreCase("false")) {
                value = false;
            } else {
                throw new IllegalArgumentException(key);
            }
            if (result != null && !result.equals(value)) throw new IllegalArgumentException(key + "_conflict");
            result = value;
        }
        return result;
    }

    private static boolean isAwakeState(String value) {
        return "already_wake_up".equals(value)
                || "already_wakeup".equals(value)
                || "wake_up".equals(value)
                || "wakeup".equals(value)
                || "awake".equals(value)
                || "active".equals(value);
    }

    private static boolean isSilentState(String listeningState, String wakeupState) {
        return "non_wakeup_silent".equals(listeningState)
                || "non_wakeup_silence".equals(listeningState)
                || "idle".equals(listeningState)
                || "sleep".equals(listeningState)
                || "idle".equals(wakeupState)
                || "not_wakeup".equals(wakeupState)
                || "not_awake".equals(wakeupState);
    }

    private static Set<String> normalizeAliases(Iterable<String> aliases) {
        LinkedHashSet<String> normalized = new LinkedHashSet<>();
        for (String alias : aliases) {
            String value = SpeechInputPolicy.normalize(alias);
            if (!value.isBlank()) normalized.add(value);
        }
        return Set.copyOf(normalized);
    }

    private static List<String> splitAliases(String value) {
        ArrayList<String> aliases = new ArrayList<>();
        for (String alias : safe(value).split("[,，;；|/]+")) {
            if (!alias.isBlank()) aliases.add(alias.trim());
        }
        return aliases;
    }

    private static String signature(String state, String wakeupState, String eventId, Long updatedAt) {
        // The SDK may refresh updated_at_ms while repeating the same wake event. A non-empty
        // event_id is the stable identity; only fall back to the timestamp when no event id exists.
        if (!eventId.isBlank()) return state + "|" + wakeupState + "|" + eventId;
        return state + "|" + wakeupState + "||" + (updatedAt == null ? "" : updatedAt);
    }

    private static String normalized(String value) {
        return safe(value).toLowerCase(Locale.ROOT);
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    record Decision(boolean accepted, String reason, boolean wouldReject) {
        static Decision accept(String reason) {
            return new Decision(true, reason, false);
        }
    }

    record OnlineObservation(Mode mode, String reportedWakeWords, List<String> normalizedAliases) { }

    record StateObservation(boolean relevant, String listeningState, String wakeupState,
                            String eventId, boolean awake, String result) {
        static StateObservation ignored(String stateName) {
            return new StateObservation(false, "", "", "", false, "ignored:" + safe(stateName));
        }
    }

    private static final class AgentState {
        private boolean online;
        private Set<String> aliases;
        private long wakeGeneration;
        private long consumedWakeGeneration;
        private long wakeSignalNanos;
        private String wakeEventId = "";
        private final LinkedHashSet<String> seenWakeEventIds = new LinkedHashSet<>();
        private String lastListeningSignature = "";
        private boolean sessionOpen;
        private long lastAcceptedNanos;

        private AgentState(Set<String> aliases) {
            this.aliases = aliases;
        }
    }
}
