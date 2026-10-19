/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Properties;
import java.util.Set;

final class AppConfig {
    final String lingxiUrl;
    final String lingxiAppId;
    final String lingxiAppKey;
    final String lingxiAppSecret;
    final Set<String> lingxiCallbacks;
    final String doubaoUrl;
    final String doubaoApiKey;
    final String doubaoModel;
    final String robotDisplayName;
    final String navBaseUrl;
    final int navTimeoutMs;
    final String ttsMode;
    final boolean ttsFixedDialogTest;
    final boolean ttsSuppressLingxiText;
    final long ttsDelayBeforeSpeakMs;
    final String qaStorePath;
    final boolean liveEnabled;
    final String liveInputMode;
    final String liveHost;
    final int livePort;
    final String liveReplyMode;
    final boolean liveAllowNavigation;
    final boolean liveAllowEntertainment;
    final int liveMaxQueueSize;
    final long liveMinIntervalMs;
    final int liveMaxTextLength;
    final boolean voiceGateMetadataRequired;
    final double voiceGateFrontHalfAngleDeg;
    final double voiceGateMinSnrDb;
    final double voiceGateMaxEchoRms;
    final double voiceGateMinVadProbability;
    final double voiceGateMinAsrConfidence;
    final long voiceGateSessionWindowMs;
    final String voiceGateWakeMode;
    final boolean voiceGateAllowAddressedWake;
    final long voiceGateWakeSignalMaxAgeMs;
    final Set<String> voiceGateAddressAliases;
    final boolean voiceGateSoc2LogEnabled;
    final String voiceGateSoc2LogRoot;
    final int voiceGateSoc2LogAttempts;
    final long voiceGateSoc2LogRetryMs;
    final int voiceGateSoc2LogTailBytes;

    private AppConfig(Properties properties) {
        this.lingxiUrl = AppConfig.read(properties, "lingxi.url", "LINGXI_URL", "wss://open.agibot.com/api/V1/open-portal/app/wss/agent-sdk");
        this.lingxiAppId = AppConfig.readRequired(properties, "lingxi.appId", "LINGXI_APP_ID");
        this.lingxiAppKey = AppConfig.readRequired(properties, "lingxi.appKey", "LINGXI_APP_KEY");
        this.lingxiAppSecret = AppConfig.readRequired(properties, "lingxi.appSecret", "LINGXI_APP_SECRET");
        this.lingxiCallbacks = AppConfig.parseCallbacks(AppConfig.read(properties, "lingxi.callbacks", "LINGXI_CALLBACKS", "asr2tts"));
        this.doubaoUrl = AppConfig.read(properties, "doubao.url", "DOUBAO_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions");
        this.doubaoApiKey = AppConfig.read(properties, "doubao.apiKey", "ARK_API_KEY", "");
        String string = System.getenv("DOUBAO_API_KEY");
        this.doubaoModel = AppConfig.readRequired(properties, "doubao.model", "DOUBAO_MODEL");
        this.robotDisplayName = AppConfig.read(properties, "robot.displayName", "ROBOT_DISPLAY_NAME", "\u7075\u7280");
        this.navBaseUrl = AppConfig.trimTrailingSlash(AppConfig.read(properties, "nav.baseUrl", "NAV_BASE_URL", "http://127.0.0.1:18080"));
        this.navTimeoutMs = Integer.parseInt(AppConfig.read(properties, "nav.timeoutMs", "NAV_TIMEOUT_MS", "5000"));
        this.ttsMode = AppConfig.normalizeTtsMode(AppConfig.read(properties, "tts.mode", "TTS_MODE", "robot"));
        this.ttsFixedDialogTest = Boolean.parseBoolean(AppConfig.read(properties, "tts.fixedDialogTest", "TTS_FIXED_DIALOG_TEST", "false"));
        this.ttsSuppressLingxiText = Boolean.parseBoolean(AppConfig.read(properties, "tts.suppressLingxiText", "TTS_SUPPRESS_LINGXI_TEXT", "false"));
        this.ttsDelayBeforeSpeakMs = Long.parseLong(AppConfig.read(properties, "tts.delayBeforeSpeakMs", "TTS_DELAY_BEFORE_SPEAK_MS", "500"));
        this.qaStorePath = AppConfig.read(properties, "qa.storePath", "QA_STORE_PATH", "config/qa-store.json");
        this.liveEnabled = Boolean.parseBoolean(AppConfig.read(properties, "live.enabled", "LIVE_ENABLED", "false"));
        this.liveInputMode = AppConfig.normalizeLiveInputMode(AppConfig.read(properties, "live.inputMode", "LIVE_INPUT_MODE", "websocket"));
        this.liveHost = AppConfig.read(properties, "live.host", "LIVE_HOST", "127.0.0.1");
        this.livePort = AppConfig.parseIntInRange(AppConfig.read(properties, "live.port", "LIVE_PORT", "18081"), "live.port", 1, 65535);
        this.liveReplyMode = AppConfig.normalizeLiveReplyMode(AppConfig.read(properties, "live.replyMode", "LIVE_REPLY_MODE", "ai"));
        this.liveAllowNavigation = Boolean.parseBoolean(AppConfig.read(properties, "live.allowNavigation", "LIVE_ALLOW_NAVIGATION", "false"));
        this.liveAllowEntertainment = Boolean.parseBoolean(AppConfig.read(properties, "live.allowEntertainment", "LIVE_ALLOW_ENTERTAINMENT", "true"));
        this.liveMaxQueueSize = AppConfig.parseIntInRange(AppConfig.read(properties, "live.maxQueueSize", "LIVE_MAX_QUEUE_SIZE", "20"), "live.maxQueueSize", 1, 1000);
        this.liveMinIntervalMs = AppConfig.parseLongAtLeast(AppConfig.read(properties, "live.minIntervalMs", "LIVE_MIN_INTERVAL_MS", "3000"), "live.minIntervalMs", 0L);
        this.liveMaxTextLength = AppConfig.parseIntInRange(AppConfig.read(properties, "live.maxTextLength", "LIVE_MAX_TEXT_LENGTH", "80"), "live.maxTextLength", 1, 1000);
        this.voiceGateMetadataRequired = Boolean.parseBoolean(AppConfig.read(properties, "voice.gate.metadataRequired", "VOICE_GATE_METADATA_REQUIRED", "false"));
        this.voiceGateFrontHalfAngleDeg = AppConfig.parseDoubleInRange(AppConfig.read(properties, "voice.gate.frontHalfAngleDeg", "VOICE_GATE_FRONT_HALF_ANGLE_DEG", "60"), "voice.gate.frontHalfAngleDeg", 1, 180);
        this.voiceGateMinSnrDb = AppConfig.parseDoubleInRange(AppConfig.read(properties, "voice.gate.minSnrDb", "VOICE_GATE_MIN_SNR_DB", "6"), "voice.gate.minSnrDb", -100, 100);
        this.voiceGateMaxEchoRms = AppConfig.parseDoubleInRange(AppConfig.read(properties, "voice.gate.maxEchoRms", "VOICE_GATE_MAX_ECHO_RMS", "0.12"), "voice.gate.maxEchoRms", 0, 1);
        this.voiceGateMinVadProbability = AppConfig.parseDoubleInRange(AppConfig.read(properties, "voice.gate.minVadProbability", "VOICE_GATE_MIN_VAD_PROBABILITY", "0.70"), "voice.gate.minVadProbability", 0, 1);
        this.voiceGateMinAsrConfidence = AppConfig.parseDoubleInRange(AppConfig.read(properties, "voice.gate.minAsrConfidence", "VOICE_GATE_MIN_ASR_CONFIDENCE", "0.65"), "voice.gate.minAsrConfidence", 0, 1);
        this.voiceGateSessionWindowMs = AppConfig.parseLongInRange(AppConfig.read(properties, "voice.gate.sessionWindowMs", "VOICE_GATE_SESSION_WINDOW_MS", "8000"), "voice.gate.sessionWindowMs", 1000, 30000);
        this.voiceGateWakeMode = AppConfig.normalizeWakeMode(AppConfig.read(properties, "voice.gate.wakeMode", "VOICE_GATE_WAKE_MODE", "enforce"));
        this.voiceGateAllowAddressedWake = Boolean.parseBoolean(AppConfig.read(properties, "voice.gate.allowAddressedWake", "VOICE_GATE_ALLOW_ADDRESSED_WAKE", "false"));
        this.voiceGateWakeSignalMaxAgeMs = AppConfig.parseLongInRange(AppConfig.read(properties, "voice.gate.wakeSignalMaxAgeMs", "VOICE_GATE_WAKE_SIGNAL_MAX_AGE_MS", "8000"), "voice.gate.wakeSignalMaxAgeMs", 1000, 30000);
        this.voiceGateAddressAliases = AppConfig.parseTextSet(AppConfig.read(properties, "voice.gate.addressAliases", "VOICE_GATE_ADDRESS_ALIASES", "\u4f60\u597d\u7075\u7280,\u7075\u7280\u7075\u7280,\u55e8\u7075\u7280,\u54c8\u55bd\u9732\u7c73,\u7075\u6eaa\u7075\u6eaa,\u55e8\u7075\u6eaa,hello lumi,hi lumi,hey lumi"));
        this.voiceGateSoc2LogEnabled = Boolean.parseBoolean(AppConfig.read(properties, "voice.gate.soc2LogEnabled", "VOICE_GATE_SOC2_LOG_ENABLED", "true"));
        this.voiceGateSoc2LogRoot = AppConfig.read(properties, "voice.gate.soc2LogRoot", "VOICE_GATE_SOC2_LOG_ROOT", "/agibot/nfs/soc2/log");
        this.voiceGateSoc2LogAttempts = AppConfig.parseIntInRange(AppConfig.read(properties, "voice.gate.soc2LogAttempts", "VOICE_GATE_SOC2_LOG_ATTEMPTS", "4"), "voice.gate.soc2LogAttempts", 1, 8);
        this.voiceGateSoc2LogRetryMs = AppConfig.parseLongInRange(AppConfig.read(properties, "voice.gate.soc2LogRetryMs", "VOICE_GATE_SOC2_LOG_RETRY_MS", "40"), "voice.gate.soc2LogRetryMs", 0, 500);
        this.voiceGateSoc2LogTailBytes = AppConfig.parseIntInRange(AppConfig.read(properties, "voice.gate.soc2LogTailBytes", "VOICE_GATE_SOC2_LOG_TAIL_BYTES", "1048576"), "voice.gate.soc2LogTailBytes", 65536, 8388608);
        if (this.doubaoApiKey.isBlank() && (string == null || string.isBlank())) {
            throw new IllegalArgumentException("Missing doubao.apiKey or ARK_API_KEY/DOUBAO_API_KEY");
        }
    }

    String effectiveDoubaoApiKey() {
        if (!this.doubaoApiKey.isBlank()) {
            return this.doubaoApiKey;
        }
        String string = System.getenv("DOUBAO_API_KEY");
        return string == null ? "" : string.trim();
    }

    boolean isCallbackEnabled(String string) {
        return this.lingxiCallbacks.contains(string);
    }

    static AppConfig load(String[] stringArray) throws IOException {
        String string = "config/lingxi-nav.properties";
        for (int i = 0; i < stringArray.length - 1; ++i) {
            if (!"--config".equals(stringArray[i])) continue;
            string = stringArray[i + 1];
        }
        Properties properties = new Properties();
        Path path = Path.of(string, new String[0]);
        if (Files.exists(path, new LinkOption[0])) {
            try (InputStream inputStream = Files.newInputStream(path, new OpenOption[0]);){
                properties.load(inputStream);
            }
        }
        return new AppConfig(properties);
    }

    private static String readRequired(Properties properties, String string, String string2) {
        String string3 = AppConfig.read(properties, string, string2, "");
        if (string3.isBlank() || string3.startsWith("YOUR_")) {
            throw new IllegalArgumentException("缺少必要配置：" + string + "（或环境变量 " + string2 + "）");
        }
        return string3;
    }

    private static String read(Properties properties, String string, String string2, String string3) {
        String string4 = System.getenv(string2);
        if (string4 != null && !string4.isBlank()) {
            return string4.trim();
        }
        return properties.getProperty(string, string3).trim();
    }

    private static String trimTrailingSlash(String string) {
        String string2 = string.trim();
        while (string2.endsWith("/")) {
            string2 = string2.substring(0, string2.length() - 1);
        }
        return string2;
    }

    private static String normalizeTtsMode(String string) {
        String string2;
        String string3 = string2 = string == null ? "" : string.trim().toLowerCase();
        if (string2.isBlank()) {
            return "robot";
        }
        if (!(string2.equals("robot") || string2.equals("lingxi") || string2.equals("off"))) {
            throw new IllegalArgumentException("tts.mode must be robot, lingxi, or off");
        }
        return string2;
    }

    private static String normalizeLiveInputMode(String string) {
        String string2;
        String string3 = string2 = string == null ? "" : string.trim().toLowerCase();
        if (string2.isBlank()) {
            return "websocket";
        }
        if (!string2.equals("websocket")) {
            throw new IllegalArgumentException("live.inputMode must be websocket");
        }
        return string2;
    }

    private static String normalizeLiveReplyMode(String string) {
        String string2;
        String string3 = string2 = string == null ? "" : string.trim().toLowerCase();
        if (string2.isBlank()) {
            return "ai";
        }
        if (!string2.equals("ai")) {
            throw new IllegalArgumentException("live.replyMode must be ai");
        }
        return string2;
    }

    private static String normalizeWakeMode(String value) {
        String normalized = value == null ? "" : value.trim().toLowerCase();
        if (normalized.isBlank()) normalized = "enforce";
        if (!(normalized.equals("off") || normalized.equals("observe") || normalized.equals("enforce"))) {
            throw new IllegalArgumentException("voice.gate.wakeMode must be off, observe, or enforce");
        }
        return normalized;
    }

    private static int parseIntInRange(String string, String string2, int n, int n2) {
        int n3;
        try {
            n3 = Integer.parseInt(string);
        }
        catch (NumberFormatException numberFormatException) {
            throw new IllegalArgumentException(string2 + " must be an integer", numberFormatException);
        }
        if (n3 < n || n3 > n2) {
            throw new IllegalArgumentException(string2 + " must be between " + n + " and " + n2);
        }
        return n3;
    }

    private static long parseLongAtLeast(String string, String string2, long l) {
        long l2;
        try {
            l2 = Long.parseLong(string);
        }
        catch (NumberFormatException numberFormatException) {
            throw new IllegalArgumentException(string2 + " must be a long integer", numberFormatException);
        }
        if (l2 < l) {
            throw new IllegalArgumentException(string2 + " must be >= " + l);
        }
        return l2;
    }

    private static long parseLongInRange(String value, String name, long min, long max) {
        long parsed = parseLongAtLeast(value, name, min);
        if (parsed > max) throw new IllegalArgumentException(name + " must be between " + min + " and " + max);
        return parsed;
    }

    private static double parseDoubleInRange(String value, String name, double min, double max) {
        final double parsed;
        try { parsed = Double.parseDouble(value); }
        catch (NumberFormatException exception) { throw new IllegalArgumentException(name + " must be a number", exception); }
        if (!Double.isFinite(parsed) || parsed < min || parsed > max) {
            throw new IllegalArgumentException(name + " must be between " + min + " and " + max);
        }
        return parsed;
    }

    private static Set<String> parseCallbacks(String string2) {
        LinkedHashSet<String> linkedHashSet = new LinkedHashSet<String>();
        Arrays.stream((string2 == null ? "" : string2).split(",")).map(String::trim).map(String::toLowerCase).filter(string -> !string.isBlank()).forEach(string -> {
            if (!(string.equals("asr2llm") || string.equals("asr2tts") || string.equals("audio2llm") || string.equals("audio2tts") || string.equals("video"))) {
                throw new IllegalArgumentException("Unsupported lingxi.callbacks value: " + string + ". Supported values: asr2llm, asr2tts, audio2llm, audio2tts, video");
            }
            linkedHashSet.add((String)string);
        });
        if (linkedHashSet.isEmpty()) {
            linkedHashSet.add("asr2llm");
        }
        return Set.copyOf(linkedHashSet);
    }

    private static Set<String> parseTextSet(String value) {
        LinkedHashSet<String> entries = new LinkedHashSet<String>();
        Arrays.stream((value == null ? "" : value).split("[,，;；|]+"))
                .map(String::trim)
                .filter(entry -> !entry.isBlank())
                .forEach(entries::add);
        return Set.copyOf(entries);
    }
}
