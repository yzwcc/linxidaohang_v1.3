package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentParam;
import java.lang.reflect.Constructor;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

public final class VoiceGateTest {
    private static int checks;

    static AppConfig config(boolean metadataRequired) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test");
        properties.setProperty("voice.gate.metadataRequired", Boolean.toString(metadataRequired));
        properties.setProperty("voice.gate.sessionWindowMs", "5000");
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    static AgentParam acceptedParams() {
        return AgentParam.create()
                .setBoolean("kws_detected", true)
                .setDouble("doa_angle_deg", 0.0)
                .setDouble("snr_db", 12.0)
                .setDouble("aec_residual_rms", 0.02)
                .setDouble("rnnoise_vad_probability", 0.95)
                .setDouble("asr_confidence", 0.90);
    }

    private static void check(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
        checks++;
    }

    public static void main(String[] args) throws Exception {
        AppConfig defaults = config(false);
        check(defaults.lingxiCallbacks.size() == 1 && defaults.lingxiCallbacks.contains("asr2llm"),
                "Lingxi ASR text callback is the default");
        FakeClock clock = new FakeClock();
        VoiceGate gate = new VoiceGate(config(false), clock);
        check(gate.admit("你好", acceptedParams()).accepted(), "front quality accepted");
        check("doa_side_or_rear".equals(gate.admit("你好", AgentParam.create()
                .setBoolean("kws_detected", true).setDouble("doa_angle_deg", 90.0)
                .setDouble("snr_db", 12.0).setDouble("aec_residual_rms", 0.02)
                .setDouble("rnnoise_vad_probability", 0.95).setDouble("asr_confidence", 0.90)).reason()), "side rejected");
        check(gate.admit("你好", AgentParam.create()).accepted(), "missing metadata remains compatible");
        gate.reset();
        check(gate.admit("你好", AgentParam.create().setBoolean("kws_detected", false)).accepted(),
                "wake metadata is delegated to WakeSessionGate");
        check("low_snr".equals(gate.admit("你好", acceptedParams().setDouble("snr_db", 1.0)).reason()), "low snr rejected");
        check("echo_residual_too_high".equals(gate.admit("你好", acceptedParams().setDouble("aec_residual_rms", 0.5)).reason()), "echo rejected");
        VoiceGate strictGate = new VoiceGate(config(true));
        check("missing_frontend_metadata".equals(strictGate.admit("你好", AgentParam.create()).reason()), "strict metadata required");
        testQualityAlwaysApplies();
        testMalformedMetadata();
        testDirectionWrapping();
        testMissingMetadataAndAliases();
        testMeaningfulShortReplies();
        System.out.println("VoiceGateTest passed: " + checks + " assertions");
    }

    static final class FakeClock implements LongSupplier {
        long nanos;
        @Override public long getAsLong() { return nanos; }
        void advanceMillis(long millis) { nanos += TimeUnit.MILLISECONDS.toNanos(millis); }
    }

    private static void testQualityAlwaysApplies() throws Exception {
        for (String key : List.of("snr_db", "aec_residual_rms", "rnnoise_vad_probability", "asr_confidence", "doa_angle_deg")) {
            double bad = switch (key) {
                case "snr_db" -> 1.0;
                case "aec_residual_rms" -> 0.5;
                case "doa_angle_deg" -> 90.0;
                default -> 0.1;
            };
            VoiceGate gate = new VoiceGate(config(true), new FakeClock());
            check(!gate.admit("你好", acceptedParams().setDouble(key, bad)).accepted(), key + " quality rejected");
            check(gate.admit("是", acceptedParams().setBoolean("kws_detected", false)).accepted(),
                    key + " wake policy remains separate");
            check(!gate.admit("你好", acceptedParams().setDouble(key, bad)).accepted(),
                    key + " repeated bad quality remains rejected");
        }
    }

    private static void testMalformedMetadata() throws Exception {
        List<Object> invalidNumbers = List.of(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY,
                "NaN", "Infinity", "-Infinity", "1e9999", "12oops", "", "  ", "null", "0x1.0p0", "1d",
                "12,5", true, List.of(1), Map.of("value", 1));
        List<String> numericKeys = List.of("doa_angle_deg", "doa_angle", "direction_deg", "snr_db", "noise_snr_db",
                "aec_residual_rms", "echo_rms", "aec_echo_rms", "rnnoise_vad_probability", "vad_probability",
                "speech_probability", "asr_confidence", "confidence");
        for (boolean strict : List.of(false, true)) {
            VoiceGate gate = new VoiceGate(config(strict), new FakeClock());
            for (String key : numericKeys) {
                for (Object bad : invalidNumbers) {
                    gate.reset();
                    VoiceGate.Decision result = gate.admit("正常问题", acceptedParams().setObject(key, bad));
                    check(!result.accepted() && result.reason().startsWith("invalid_frontend_metadata:"),
                            "invalid numeric " + key + "=" + bad + " strict=" + strict);
                    check(gate.admit("正常问题", acceptedParams().setBoolean("kws_detected", false)).accepted(),
                            "invalid quality is not sticky " + key);
                    check(!gate.admit("正常问题", acceptedParams().setObject(key, bad)).accepted(),
                            "later requests cannot bypass invalid numeric " + key);
                }
            }
            for (String key : List.of("aec_residual_rms", "rnnoise_vad_probability", "asr_confidence")) {
                for (double bad : List.of(-0.01, 1.01)) {
                    check(!gate.admit("你好", acceptedParams().setDouble(key, bad)).accepted(), "unit interval " + key + "=" + bad);
                }
            }
        }
    }

    private static void testDirectionWrapping() throws Exception {
        VoiceGate gate = new VoiceGate(config(true), new FakeClock());
        for (double doa : List.of(0.0, 60.0, -60.0, 359.0, 360.0, 361.0, -359.0, -360.0, 720.0, -721.0, 300.0)) {
            check(gate.admit("你好", acceptedParams().setDouble("doa_angle_deg", doa)).accepted(), "front wrapped doa " + doa);
        }
        for (double doa : List.of(60.001, -60.001, 90.0, 180.0, 270.0, 450.0, -450.0)) {
            check("doa_side_or_rear".equals(gate.admit("你好", acceptedParams().setDouble("doa_angle_deg", doa)).reason()), "side/rear wrapped doa " + doa);
        }
        check(gate.admit("你好", acceptedParams().setDouble("doa_angle_deg", 359.0).setDouble("direction_deg", -1.0)).accepted(), "equivalent wrapped aliases");
        check(!gate.admit("你好", acceptedParams().setDouble("direction_deg", 90.0)).accepted(), "conflicting doa aliases rejected");
    }

    private static void testMissingMetadataAndAliases() throws Exception {
        VoiceGate compatible = new VoiceGate(config(false), new FakeClock());
        check(!config(false).voiceGateMetadataRequired, "metadata optional configuration retained");
        check(compatible.admit("正常问答", null).accepted(), "null metadata compatible");
        check(compatible.admit("正常问答", AgentParam.create()).accepted(), "empty metadata compatible");
        check(compatible.admit("正常问答", AgentParam.create().setObject("snr_db", null)).accepted(), "null field is absent");
        check(!compatible.admit("正常问答", AgentParam.create().setDouble("snr_db", 0.0)).accepted(), "present bad quality not optional");
        check(compatible.admit("正常问答", AgentParam.create().setBoolean("kws_detected", false)).accepted(),
                "quality gate does not duplicate wake policy");
        VoiceGate strict = new VoiceGate(config(true), new FakeClock());
        for (String key : List.of("doa_angle_deg", "snr_db", "aec_residual_rms", "rnnoise_vad_probability", "asr_confidence")) {
            AgentParam incomplete = acceptedParams().setObject(key, null);
            check(!strict.admit("你好", incomplete).accepted(), "strict requires " + key);
        }
        AgentParam aliases = AgentParam.create().setString("direction_deg", "3.59e2").setString("noise_snr_db", " 12.0 ")
                .setString("aec_echo_rms", ".02").setString("speech_probability", "9.5e-1").setString("confidence", "+0.90");
        check(strict.admit("你好", aliases).accepted(), "decimal metadata aliases supported");
        check(strict.admit("是", acceptedParams().setObject("kws_detected", null)).accepted(),
                "strict quality gate permits missing wake metadata");
        check(strict.admit("你好", acceptedParams().setString("kws_detected", "malformed")).accepted(),
                "wake metadata validation is delegated to WakeSessionGate");
        check(!strict.admit("你好", acceptedParams().setDouble("noise_snr_db", 1.0)).accepted(), "bad alias cannot be hidden");
        check(strict.admit("你好", acceptedParams().setDouble("noise_snr_db", 12.0)).accepted(), "equal aliases accepted");
    }

    private static void testMeaningfulShortReplies() throws Exception {
        VoiceGate gate = new VoiceGate(config(false), new FakeClock());
        for (String text : List.of("是", "否", "不", "对", "行", "停", "是。", "否！", "什么是智元机器人", "我需要办理业务")) {
            check(gate.admit(text, acceptedParams()).accepted(), "meaningful text retained: " + text);
        }
        for (String text : List.of("", " ", "。", "嗯", "啊啊", "好好好嗯")) {
            gate.reset();
            check(!gate.admit(text, acceptedParams()).accepted(), "filler rejected: " + text);
            check(gate.admit("是", acceptedParams().setBoolean("kws_detected", false)).accepted(),
                    "filler rejection is not sticky");
        }
        check(gate.admit(null, acceptedParams()).accepted(), "audio first-packet metadata gate supported");
    }
}
