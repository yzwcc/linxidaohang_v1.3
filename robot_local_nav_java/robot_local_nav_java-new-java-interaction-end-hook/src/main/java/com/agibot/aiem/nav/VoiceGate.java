package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentParam;
import java.util.Map;
import java.util.List;
import java.util.function.LongSupplier;
import java.util.regex.Pattern;

/** Consumes SDK front-end metadata before text reaches navigation or the LLM. */
final class VoiceGate {
    private static final List<String> DOA_KEYS = List.of("doa_angle_deg", "doa_angle", "direction_deg");
    private static final Pattern DECIMAL = Pattern.compile("[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?");
    private final boolean metadataRequired;
    private final double frontHalfAngleDeg;
    private final double minSnrDb;
    private final double maxEchoRms;
    private final double minVadProbability;
    private final double minAsrConfidence;

    VoiceGate(AppConfig config) {
        this(config, System::nanoTime);
    }

    VoiceGate(AppConfig config, LongSupplier nanoTime) {
        metadataRequired = config.voiceGateMetadataRequired;
        frontHalfAngleDeg = config.voiceGateFrontHalfAngleDeg;
        minSnrDb = config.voiceGateMinSnrDb;
        maxEchoRms = config.voiceGateMaxEchoRms;
        minVadProbability = config.voiceGateMinVadProbability;
        minAsrConfidence = config.voiceGateMinAsrConfidence;
    }

    synchronized Decision admit(String text, AgentParam params) {
        if (text != null && SpeechInputPolicy.isLikelyNoise(text)) return reject("noise_or_low_information");
        try {
            Map<?, ?> metadata = params == null ? Map.of() : params.getExtParam();
            if (metadata == null) metadata = Map.of();
            Double doa = readDouble(metadata, DOA_KEYS);
            if (metadataRequired && doa == null) return reject("missing_frontend_metadata");
            if (doa != null && Math.abs(doa) > frontHalfAngleDeg) return reject("doa_side_or_rear");
            Decision quality = checkQuality(metadata);
            if (!quality.accepted()) return quality;
            return new Decision(true, "accepted");
        } catch (IllegalArgumentException invalid) {
            return reject("invalid_frontend_metadata:" + invalid.getMessage());
        }
    }

    synchronized void reset() { }

    private Decision checkQuality(Map<?, ?> metadata) {
        Double snr = readDouble(metadata, List.of("snr_db", "noise_snr_db"));
        if (snr == null && metadataRequired) return reject("missing_snr");
        if (snr != null && snr < minSnrDb) return reject("low_snr");
        Double echo = unitInterval(readDouble(metadata, List.of("aec_residual_rms", "echo_rms", "aec_echo_rms")), "echo");
        if (echo == null && metadataRequired) return reject("missing_aec_metadata");
        if (echo != null && echo > maxEchoRms) return reject("echo_residual_too_high");
        Double vad = unitInterval(readDouble(metadata, List.of("rnnoise_vad_probability", "vad_probability", "speech_probability")), "vad");
        if (vad == null && metadataRequired) return reject("missing_vad_metadata");
        if (vad != null && vad < minVadProbability) return reject("noise_only");
        Double confidence = unitInterval(readDouble(metadata, List.of("asr_confidence", "confidence")), "confidence");
        if (confidence == null && metadataRequired) return reject("missing_asr_confidence");
        if (confidence != null && confidence < minAsrConfidence) return reject("low_asr_confidence");
        return new Decision(true, "quality_ok");
    }

    private static Decision reject(String reason) { return new Decision(false, reason); }

    private static Double readDouble(Map<?, ?> metadata, List<String> keys) {
        Double result = null;
        for (String key : keys) {
            Object raw = metadata.get(key);
            if (raw == null) continue;
            double value;
            if (raw instanceof Number number) value = number.doubleValue();
            else if (raw instanceof String text && DECIMAL.matcher(text.trim()).matches()) value = Double.parseDouble(text.trim());
            else throw new IllegalArgumentException(key);
            if (!Double.isFinite(value)) throw new IllegalArgumentException(key);
            if (keys == DOA_KEYS) value = wrapDegrees(value);
            // Do not let a good alias hide a conflicting or malformed value.
            if (result != null && result.doubleValue() != value) throw new IllegalArgumentException(key + "_conflict");
            result = value;
        }
        return result;
    }

    private static Double unitInterval(Double value, String name) {
        if (value != null && (value < 0.0 || value > 1.0)) throw new IllegalArgumentException(name);
        return value;
    }

    private static double wrapDegrees(double angle) {
        double wrapped = angle % 360.0;
        if (wrapped > 180.0) wrapped -= 360.0;
        if (wrapped < -180.0) wrapped += 360.0;
        return wrapped;
    }

    record Decision(boolean accepted, String reason) { }
}
