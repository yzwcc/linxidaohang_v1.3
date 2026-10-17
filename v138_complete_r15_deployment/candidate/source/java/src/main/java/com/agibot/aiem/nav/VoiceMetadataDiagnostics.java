package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentParam;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Produces a bounded, privacy-safe view of SDK voice metadata.
 *
 * Unknown values are never rendered because AgentParam may contain audio,
 * voiceprints, credentials, or vendor-specific payloads.
 */
final class VoiceMetadataDiagnostics {
    private static final int MAX_UNKNOWN_KEYS = 16;
    private static final int MAX_KNOWN_KEYS = 32;
    private static final Pattern SAFE_KEY = Pattern.compile("[A-Za-z0-9_.-]{1,64}");
    private static final Pattern SAFE_STATE = Pattern.compile("[A-Za-z0-9_.:-]{1,48}");
    private static final Set<String> KNOWN_KEYS = Set.of(
            "kws_detected", "wake_detected", "wake_active", "wake_word_detected",
            "doa_angle_deg", "doa_angle", "direction_deg",
            "snr_db", "noise_snr_db",
            "aec_residual_rms", "echo_rms", "aec_echo_rms",
            "rnnoise_vad_probability", "vad_probability", "speech_probability",
            "asr_confidence", "confidence",
            "listening_status", "listeningstatus", "wakeup_status", "wakeupstatus",
            "wakeup_state", "wakeupState", "wake_state", "wakeState", "wakeup",
            "state", "listening_state", "listeningState", "status", "event_id");

    private VoiceMetadataDiagnostics() {}

    static String summarize(AgentParam params) {
        final Map<?, ?> metadata;
        try {
            metadata = params == null ? null : params.getExtParam();
        } catch (RuntimeException error) {
            return "present=error";
        }
        if (metadata == null || metadata.isEmpty()) {
            return "present=false";
        }

        List<Object> rawKeys = new ArrayList<>(metadata.keySet());
        rawKeys.sort(Comparator.comparing(String::valueOf));
        List<String> known = new ArrayList<>();
        List<String> unknown = new ArrayList<>();
        for (Object rawKey : rawKeys) {
            String key = String.valueOf(rawKey);
            if (KNOWN_KEYS.contains(key)) {
                if (known.size() < MAX_KNOWN_KEYS) {
                    known.add(key + "=" + describeKnown(key, metadata.get(rawKey)));
                }
            } else if (unknown.size() < MAX_UNKNOWN_KEYS && SAFE_KEY.matcher(key).matches()) {
                unknown.add(key);
            }
        }
        StringBuilder result = new StringBuilder("present=true, known=[");
        result.append(String.join(", ", known));
        result.append("], unknownKeys=[").append(String.join(", ", unknown)).append(']');
        if (rawKeys.size() > known.size() + unknown.size()) {
            result.append(", omittedKeyCount=")
                    .append(rawKeys.size() - known.size() - unknown.size());
        }
        return result.toString();
    }

    private static String describeKnown(String key, Object value) {
        if (value == null) return "null";
        if ("event_id".equals(key)) return "present";
        if (value instanceof Boolean) return value.toString();
        if (value instanceof Number number) {
            double numeric = number.doubleValue();
            return Double.isFinite(numeric)
                    ? String.format(Locale.ROOT, "%.3f", numeric)
                    : "invalid_number";
        }
        if (value instanceof String text) {
            String trimmed = text.trim();
            if (isStateKey(key) && SAFE_STATE.matcher(trimmed).matches()) {
                return "state:" + trimmed.toLowerCase(Locale.ROOT);
            }
            return "string(length=" + text.length() + ")";
        }
        return "type:" + value.getClass().getSimpleName();
    }

    private static boolean isStateKey(String key) {
        return key.contains("state") || "status".equals(key) || "wakeup".equals(key);
    }
}
