package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentParam;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

final class InteractionGateRegression {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
        checks++;
    }

    private static AppConfig config(Path soc2Root) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test");
        properties.setProperty("voice.gate.wakeMode", "enforce");
        properties.setProperty("voice.gate.soc2LogEnabled", Boolean.toString(soc2Root != null));
        properties.setProperty("voice.gate.soc2LogAttempts", "1");
        properties.setProperty("voice.gate.soc2LogRetryMs", "0");
        if (soc2Root != null) properties.setProperty("voice.gate.soc2LogRoot", soc2Root.toString());
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    private static Field field(String name) throws Exception {
        Field field = LingxiNavAgentApp.class.getDeclaredField(name);
        field.setAccessible(true);
        return field;
    }

    private static boolean shouldIgnore(Method callback, String eventId, String text,
                                        AgentParam params) throws Exception {
        return (boolean) callback.invoke(null, "Asr2Llm", "agent", eventId, text, params, null);
    }

    public static void main(String[] args) throws Exception {
        Field voiceField = field("VOICE_GATE");
        Field wakeField = field("WAKE_SESSION_GATE");
        Field soc2Field = field("SOC2_WAKE_EVIDENCE");
        Field ttsField = field("TTS_CONTROLLER");
        Object originalVoice = voiceField.get(null);
        Object originalWake = wakeField.get(null);
        Object originalSoc2 = soc2Field.get(null);
        Object tts = ttsField.get(null);
        Field silentUntilField = tts.getClass().getDeclaredField("silentUntilMillis");
        Field spokenField = tts.getClass().getDeclaredField("lastNormalizedText");
        silentUntilField.setAccessible(true);
        spokenField.setAccessible(true);
        long originalSilentUntil = silentUntilField.getLong(tts);
        Object originalSpoken = spokenField.get(tts);
        Path soc2Root = Files.createTempDirectory("lingxi-soc2-gate-");
        try {
            Path log = soc2Root.resolve("log_20261022_120000/agent/agent.log");
            Files.createDirectories(log.getParent());
            Files.writeString(log,
                    "event_id: soc2-fresh, final asr result: question, 距离上次唤醒: 1000\n",
                    StandardCharsets.UTF_8);

            AppConfig config = config(soc2Root);
            FakeClock clock = new FakeClock();
            voiceField.set(null, new VoiceGate(config, clock));
            WakeSessionGate wake = new WakeSessionGate(config, clock);
            wakeField.set(null, wake);
            soc2Field.set(null, new Soc2WakeEvidenceProvider(config));
            Method callback = LingxiNavAgentApp.class.getDeclaredMethod(
                    "shouldIgnoreTextCallback", String.class, String.class, String.class,
                    String.class, AgentParam.class, NavBridgeClient.class);
            callback.setAccessible(true);

            AgentParam good = VoiceGateTest.acceptedParams();
            check(shouldIgnore(callback, "bad-snr", "今天星期几",
                    good.setDouble("snr_db", 1.0)), "low SNR rejected at text callback");
            check(shouldIgnore(callback, "after-bad-snr", "今天星期几",
                    AgentParam.create().setBoolean("kws_detected", false)),
                    "quality rejection does not open a wake session");
            check(shouldIgnore(callback, "text-wake", "你好灵犀今天星期几", AgentParam.create()),
                    "transcript-only wake phrase is not KWS evidence");
            check(shouldIgnore(callback, "filler", "嗯，嗯。", good),
                    "filler rejected before bridge entry");

            check(!shouldIgnore(callback, "kws", "今天星期几", VoiceGateTest.acceptedParams()),
                    "valid KWS and quality reach the existing interaction path");
            clock.advanceMillis(7999);
            check(!shouldIgnore(callback, "followup", "再多说一点",
                    AgentParam.create().setBoolean("kws_detected", false)),
                    "active wake session accepts follow-up without repeated KWS");
            clock.advanceMillis(8000);
            check(shouldIgnore(callback, "expired", "再多说一点", AgentParam.create()),
                    "wake session expires at the configured boundary");

            check(!shouldIgnore(callback, "urgent-stop", "停",
                    AgentParam.create().setBoolean("kws_detected", false)
                            .setDouble("snr_db", Double.NaN)),
                    "single-word urgent stop bypasses invalid quality and wake metadata");

            Method audio = LingxiNavAgentApp.class.getDeclaredMethod(
                    "newAudioSession", String.class, String.class, String.class,
                    AgentParam.class, NavBridgeClient.class);
            audio.setAccessible(true);
            wake.reset();
            Object rejectedAudio = audio.invoke(null, "item", "audio-cold", "audio2llm_wake",
                    AgentParam.create().setBoolean("kws_detected", false), null);
            Method ignored = rejectedAudio.getClass().getDeclaredMethod("shouldIgnore");
            ignored.setAccessible(true);
            check((boolean) ignored.invoke(rejectedAudio), "raw audio requires KWS or an active session");
            Object admittedAudio = audio.invoke(null, "item", "audio-kws", "audio2llm_wake",
                    VoiceGateTest.acceptedParams(), null);
            check(!(boolean) ignored.invoke(admittedAudio), "valid audio metadata opens the session");

            spokenField.set(tts, SpeechInputPolicy.normalize("我会做甜甜的星星梦，梦里全是小朋友"));
            silentUntilField.setLong(tts, Long.MAX_VALUE);
            wake.reset();
            check(shouldIgnore(callback, "echo", "我会做甜甜的星星梦",
                    VoiceGateTest.acceptedParams()), "robot playback echo rejected at callback entry");
            check(!shouldIgnore(callback, "stop-during-echo", "停",
                    AgentParam.create().setBoolean("kws_detected", false)),
                    "urgent stop remains available during robot playback");
            silentUntilField.setLong(tts, 0L);

            AppConfig soc2Config = config(soc2Root);
            voiceField.set(null, new VoiceGate(soc2Config, clock));
            WakeSessionGate soc2Wake = new WakeSessionGate(soc2Config, clock);
            wakeField.set(null, soc2Wake);
            soc2Field.set(null, new Soc2WakeEvidenceProvider(soc2Config));
            check(!shouldIgnore(callback, "soc2-fresh", "今天星期几", AgentParam.create()),
                    "fresh exact-event SOC2 evidence reaches callback path");
            soc2Wake.reset();
            check(shouldIgnore(callback, "soc2-fresh", "今天星期几", AgentParam.create()),
                    "SOC2 wake evidence cannot be replayed after reset");
            System.out.println("InteractionGateRegression passed: " + checks + " assertions");
        } finally {
            voiceField.set(null, originalVoice);
            wakeField.set(null, originalWake);
            soc2Field.set(null, originalSoc2);
            silentUntilField.setLong(tts, originalSilentUntil);
            spokenField.set(tts, originalSpoken);
            try (var paths = Files.walk(soc2Root)) {
                paths.sorted(Comparator.reverseOrder()).forEach(path -> {
                    try { Files.deleteIfExists(path); }
                    catch (Exception ignored) { }
                });
            }
        }
    }

    private static final class FakeClock implements LongSupplier {
        private long nanos;

        @Override public long getAsLong() { return nanos; }

        void advanceMillis(long millis) {
            nanos += TimeUnit.MILLISECONDS.toNanos(millis);
        }
    }
}
