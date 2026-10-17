/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import com.agibot.aiem.nav.AppConfig;
import com.agibot.aiem.nav.DoubaoArkClient;
import com.agibot.aiem.nav.LiveDanmuServer;
import com.agibot.aiem.nav.NavBridgeClient;
import com.agibot.aiem.nav.NavigationIntent;
import com.agibot.aiem.nav.QaStore;
import com.agibot.aiem.sdk.AgentMeta;
import com.agibot.aiem.sdk.AgentParam;
import com.agibot.aiem.sdk.AgentAuthCallback;
import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.agibot.aiem.sdk.passive.AsrVideo2TtsCallback;
import com.agibot.aiem.sdk.passive.AsrVideo2TtsResponse;
import com.agibot.aiem.sdk.passive.AsrVideo2VlmCallback;
import com.agibot.aiem.sdk.passive.AsrVideo2VlmResponse;
import com.agibot.aiem.sdk.passive.Audio2LlmCallback;
import com.agibot.aiem.sdk.passive.Audio2LlmResponse;
import com.agibot.aiem.sdk.passive.Audio2TtsCallback;
import com.agibot.aiem.sdk.passive.Audio2TtsResponse;
import com.agibot.aiem.sdk.passive.AudioVideo2TtsCallback;
import com.agibot.aiem.sdk.passive.AudioVideo2TtsResponse;
import com.agibot.aiem.sdk.passive.AudioVideo2VlmCallback;
import com.agibot.aiem.sdk.passive.AudioVideo2VlmResponse;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.fastjson2.JSONWriter;
import java.io.ByteArrayOutputStream;
import java.net.http.HttpTimeoutException;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class LingxiNavAgentApp {
    private static final Logger log = LoggerFactory.getLogger(LingxiNavAgentApp.class);
    private static final RetrieverClient RETRIEVER_CLIENT = new RetrieverClient();
    private static final String FIXED_DIALOG_TTS_EVENT_ID = "normal_dialog_test";
    private static final String FIXED_DIALOG_TTS_TEXT = "\u6536\u5230\uff0c\u6211\u6765\u56de\u7b54\u4f60\u3002";
    private static final Map<String, AudioSession> AUDIO_2_LLM_SESSIONS = new ConcurrentHashMap<String, AudioSession>();
    private static final Map<String, AudioSession> AUDIO_2_TTS_SESSIONS = new ConcurrentHashMap<String, AudioSession>();
    private static final TtsController TTS_CONTROLLER = new TtsController(1000L, 1800L, 6500L, 0.86);
    private static final int AUDIO_WORKER_THREADS = 3;
    private static final int AUDIO_WORKER_QUEUE_CAPACITY = 3;
    private static final AtomicInteger AUDIO_TASKS_IN_FLIGHT = new AtomicInteger();
    private static final ThreadPoolExecutor AUDIO_WORKERS = new ThreadPoolExecutor(3, 3, 0L, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<Runnable>(3), runnable -> {
        Thread thread = new Thread(runnable, "lingxi-audio-worker");
        thread.setDaemon(true);
        return thread;
    }, new ThreadPoolExecutor.AbortPolicy());

    public static void main(String[] stringArray) throws Exception {
        AppConfig appConfig = AppConfig.load(stringArray);
        if (LingxiNavAgentApp.hasArg(stringArray, "--live-danmu-test-server")) {
            LingxiNavAgentApp.runLiveDanmuTestServer(appConfig);
            return;
        }
        NavBridgeClient navBridgeClient = new NavBridgeClient(appConfig);
        if (LingxiNavAgentApp.hasArg(stringArray, "--fixed-tts-test")) {
            LingxiNavAgentApp.runFixedTtsTest(navBridgeClient);
            return;
        }
        DoubaoArkClient doubaoArkClient = new DoubaoArkClient(appConfig);
        QaStore qaStore = new QaStore(appConfig.qaStorePath);
        LiveDanmuServer liveDanmuServer = null;
        if (appConfig.liveEnabled) {
            liveDanmuServer = new LiveDanmuServer(appConfig, incomingDanmu -> LingxiNavAgentApp.handleLiveDanmu(incomingDanmu, appConfig, navBridgeClient, doubaoArkClient, qaStore));
            liveDanmuServer.start();
        }
        AgentSdk agentSdk = AgentSdk.create(
                appConfig.lingxiUrl,
                appConfig.lingxiAppId,
                appConfig.lingxiAppKey,
                appConfig.lingxiAppSecret);
        agentSdk.registerAuth(new AgentAuthCallback(){

            @Override
            public void onAuthState(String appId, int code, String message) {
                log.info("Lingxi auth state: appId={}, code={}, msg={}", appId, code, message);
            }
        });
        LingxiNavAgentApp.registerAudioCallbacks(agentSdk, appConfig, navBridgeClient, doubaoArkClient, qaStore);
        LingxiNavAgentApp.registerAsrCallbacks(agentSdk, appConfig, navBridgeClient, doubaoArkClient, qaStore);
        if (appConfig.isCallbackEnabled("video")) {
            LingxiNavAgentApp.registerUnsupportedVideoCallbacks(agentSdk);
        }
        agentSdk.initialize();
        log.info("Lingxi navigation agent started. navBaseUrl={}, ttsMode={}, callbacks={}", appConfig.navBaseUrl, appConfig.ttsMode, appConfig.lingxiCallbacks);
        log.info("Speech output policy: tts.mode={} means local robot speech is {}. suppressLingxiText={}, fixedDialogTest={}, delayBeforeSpeakMs={}", appConfig.ttsMode, "robot".equals(appConfig.ttsMode) ? "sent to Python bridge /tts/speak" : "not sent to Python bridge", appConfig.ttsSuppressLingxiText, appConfig.ttsFixedDialogTest, appConfig.ttsDelayBeforeSpeakMs);
        if (liveDanmuServer != null) {
            log.info("Live danmu input enabled: inputMode={}, address=ws://{}:{}/, replyMode={}, allowNavigation={}, allowEntertainment={}, maxQueueSize={}, minIntervalMs={}, maxTextLength={}", appConfig.liveInputMode, appConfig.liveHost, appConfig.livePort, appConfig.liveReplyMode, appConfig.liveAllowNavigation, appConfig.liveAllowEntertainment, appConfig.liveMaxQueueSize, appConfig.liveMinIntervalMs, appConfig.liveMaxTextLength);
        }
    }

    private static boolean hasArg(String[] stringArray, String string) {
        for (String string2 : stringArray) {
            if (!string.equals(string2)) continue;
            return true;
        }
        return false;
    }

    private static void runFixedTtsTest(NavBridgeClient navBridgeClient) throws Exception {
        JSONObject jSONObject = navBridgeClient.speak("java_fixed_test", "\u8fd9\u662f Java \u540e\u7aef\u56fa\u5b9a\u8bed\u97f3\u6d4b\u8bd5", true, true);
        log.info("Fixed robot TTS test result: {}", (Object)jSONObject.toJSONString(new JSONWriter.Feature[0]));
    }

    private static void runLiveDanmuTestServer(AppConfig appConfig) throws Exception {
        LiveDanmuServer liveDanmuServer = new LiveDanmuServer(appConfig, incomingDanmu -> log.info("Live danmu test received: eventId={}, msgId={}, nickname={}, text={}", incomingDanmu.eventId(), incomingDanmu.msgId(), incomingDanmu.nickname(), LingxiNavAgentApp.abbreviate(incomingDanmu.text())));
        Runtime.getRuntime().addShutdownHook(new Thread(liveDanmuServer::close, "live-danmu-test-shutdown"));
        liveDanmuServer.start();
        log.info("Live danmu test server is running. Press Ctrl+C to stop.");
        new CountDownLatch(1).await();
    }

    private static void registerAudioCallbacks(AgentSdk agentSdk, final AppConfig appConfig, final NavBridgeClient navBridgeClient, final DoubaoArkClient doubaoArkClient, final QaStore qaStore) {
        if (!appConfig.isCallbackEnabled("audio2llm") && !appConfig.isCallbackEnabled("audio2tts")) {
            log.info("Audio callbacks disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
            return;
        }
        if (appConfig.isCallbackEnabled("audio2llm")) {
            agentSdk.registerAudio2Llm(new Audio2LlmCallback(agentSdk){

                @Override
                public void onRobotOnline(String string, AgentMeta agentMeta) {
                    log.info("Robot online(audio2llm): agentId={}, meta={}", (Object)string, (Object)agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    log.info("Robot offline(audio2llm): agentId={}", (Object)string);
                }

                @Override
                public void onRequest(String string, String string2, int n, byte[] byArray, AgentParam agentParam, Audio2LlmResponse audio2LlmResponse) {
                    LingxiNavAgentApp.logAudioRequest("Audio2Llm", string, string2, null, n, byArray);
                    LingxiNavAgentApp.handleAudio2LlmChunk(string2, n, byArray, audio2LlmResponse, appConfig, navBridgeClient, doubaoArkClient, qaStore);
                }
            });
        } else {
            log.info("Audio2Llm callback disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
        }
        if (appConfig.isCallbackEnabled("audio2tts")) {
            agentSdk.registerAudio2Tts(new Audio2TtsCallback(agentSdk){

                @Override
                public void onRobotOnline(String string, AgentMeta agentMeta) {
                    log.info("Robot online(audio2tts): agentId={}, meta={}", (Object)string, (Object)agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    log.info("Robot offline(audio2tts): agentId={}", (Object)string);
                }

                @Override
                public void onRequest(String string, String string2, String string3, int n, byte[] byArray, AgentParam agentParam, Audio2TtsResponse audio2TtsResponse) {
                    LingxiNavAgentApp.logAudioRequest("Audio2Tts", string, string2, string3, n, byArray);
                    LingxiNavAgentApp.handleAudio2TtsChunk(string2, string3, n, byArray, audio2TtsResponse, appConfig, navBridgeClient, doubaoArkClient, qaStore);
                }
            });
        } else {
            log.info("Audio2Tts callback disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
        }
    }

    private static void registerAsrCallbacks(AgentSdk agentSdk, final AppConfig appConfig, final NavBridgeClient navBridgeClient, final DoubaoArkClient doubaoArkClient, final QaStore qaStore) {
        if (!appConfig.isCallbackEnabled("asr2llm") && !appConfig.isCallbackEnabled("asr2tts")) {
            log.info("ASR text callbacks disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
            return;
        }
        if (appConfig.isCallbackEnabled("asr2llm")) {
            agentSdk.registerAsr2Llm(new Asr2LlmCallback(agentSdk){

                @Override
                public void onRobotOnline(String string, AgentMeta agentMeta) {
                    log.info("Robot online(asr2llm): agentId={}, meta={}", (Object)string, (Object)agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    log.info("Robot offline(asr2llm): agentId={}", (Object)string);
                }

                /*
                 * WARNING - Removed try catching itself - possible behaviour change.
                 */
                @Override
                public void onRequest(String string, String string2, String string3, AgentParam agentParam, Asr2LlmResponse asr2LlmResponse) {
                    log.info("Asr2Llm request: agentId={}, eventId={}, text={}", string, string2, string3);
                    String string4 = LingxiNavAgentApp.newItemId();
                    AtomicBoolean atomicBoolean = new AtomicBoolean(false);
                    try {
                        if (LingxiNavAgentApp.shouldIgnoreTextCallback("Asr2Llm", string2, string3, navBridgeClient)) {
                            LingxiNavAgentApp.finishIgnoredAsr2Llm(string2, asr2LlmResponse);
                            return;
                        }
                        InteractionResult interactionResult = LingxiNavAgentApp.processTextRequest(string2, string3, appConfig, navBridgeClient, doubaoArkClient, qaStore, atomicBoolean);
                        if (LingxiNavAgentApp.shouldReturnLingxiText(appConfig)) {
                            asr2LlmResponse.onLlmItemDelta(string2, string4, interactionResult.reply);
                        } else {
                            log.info("Lingxi LLM text suppressed for local robot TTS test: eventId={}, text={}", (Object)string2, (Object)LingxiNavAgentApp.abbreviate(interactionResult.reply));
                        }
                        asr2LlmResponse.onLlmItemDone(string2, string4);
                        LingxiNavAgentApp.dispatchSkillIfNeeded(asr2LlmResponse, string2, string4, interactionResult.skillAction);
                        asr2LlmResponse.onLlmDone(string2);
                        LingxiNavAgentApp.speakIfNeeded(appConfig, string2, interactionResult.reply, interactionResult.ttsPriority, navBridgeClient);
                    }
                    catch (Exception exception) {
                        log.error("Asr2Llm handling failed", exception);
                        asr2LlmResponse.onError(string2, 5001, "\u5904\u7406\u5931\u8d25: " + exception.getMessage());
                    }
                    finally {
                        if (atomicBoolean.get()) {
                            LingxiNavAgentApp.endUserInteraction(string2, "asr2llm_done", navBridgeClient);
                        }
                    }
                }
            });
        } else {
            log.info("Asr2Llm callback disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
        }
        if (appConfig.isCallbackEnabled("asr2tts")) {
            agentSdk.registerAsr2Tts(new Asr2TtsCallback(agentSdk){

                @Override
                public void onRobotOnline(String string, AgentMeta agentMeta) {
                    log.info("Robot online(asr2tts): agentId={}, meta={}", (Object)string, (Object)agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    log.info("Robot offline(asr2tts): agentId={}", (Object)string);
                }

                /*
                 * WARNING - Removed try catching itself - possible behaviour change.
                 */
                @Override
                public void onRequest(String string, String string2, String string3, AgentParam agentParam, Asr2TtsResponse asr2TtsResponse) {
                    log.info("Asr2Tts request: agentId={}, eventId={}, text={}", string, string2, string3);
                    String string4 = LingxiNavAgentApp.newItemId();
                    AtomicBoolean atomicBoolean = new AtomicBoolean(false);
                    try {
                        if (LingxiNavAgentApp.shouldIgnoreTextCallback("Asr2Tts", string2, string3, navBridgeClient)) {
                            LingxiNavAgentApp.finishIgnoredAsr2Tts(string2, asr2TtsResponse);
                            return;
                        }
                        InteractionResult interactionResult = LingxiNavAgentApp.processTextRequest(string2, string3, appConfig, navBridgeClient, doubaoArkClient, qaStore, atomicBoolean);
                        if (LingxiNavAgentApp.shouldReturnLingxiText(appConfig)) {
                            asr2TtsResponse.onLlmItemDelta(string2, string4, interactionResult.reply);
                        } else {
                            log.info("Lingxi LLM text suppressed for local robot TTS test: eventId={}, text={}", (Object)string2, (Object)LingxiNavAgentApp.abbreviate(interactionResult.reply));
                        }
                        asr2TtsResponse.onLlmItemDone(string2, string4);
                        LingxiNavAgentApp.dispatchSkillIfNeeded(asr2TtsResponse, string2, string4, interactionResult.skillAction);
                        asr2TtsResponse.onLlmDone(string2);
                        LingxiNavAgentApp.speakIfNeeded(appConfig, string2, interactionResult.reply, interactionResult.ttsPriority, navBridgeClient);
                        asr2TtsResponse.onTtsItemDone(string2, string4);
                        asr2TtsResponse.onTtsDone(string2);
                    }
                    catch (Exception exception) {
                        log.error("Asr2Tts handling failed", exception);
                        asr2TtsResponse.onError(string2, 5001, "\u5904\u7406\u5931\u8d25: " + exception.getMessage());
                    }
                    finally {
                        if (atomicBoolean.get()) {
                            LingxiNavAgentApp.endUserInteraction(string2, "asr2tts_done", navBridgeClient);
                        }
                    }
                }
            });
        } else {
            log.info("Asr2Tts callback disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
        }
    }

    private static void registerUnsupportedVideoCallbacks(AgentSdk agentSdk) {
        agentSdk.registerAsrVideo2Vlm(new AsrVideo2VlmCallback(agentSdk){

            @Override
            public void onRequest(String string, String string2, int n, String string3, byte[] byArray, AgentParam agentParam, AsrVideo2VlmResponse asrVideo2VlmResponse) {
                log.warn("Unsupported AsrVideo2Vlm request: agentId={}, eventId={}, flag={}", string, string2, n);
                asrVideo2VlmResponse.onError(string2, 4004, "\u5f53\u524d\u5bfc\u822a\u9002\u914d\u672a\u5b9e\u73b0\u89c6\u9891\u7406\u89e3");
            }
        });
        agentSdk.registerAsrVideo2Tts(new AsrVideo2TtsCallback(agentSdk){

            @Override
            public void onRequest(String string, String string2, int n, String string3, byte[] byArray, AgentParam agentParam, AsrVideo2TtsResponse asrVideo2TtsResponse) {
                log.warn("Unsupported AsrVideo2Tts request: agentId={}, eventId={}, flag={}", string, string2, n);
                asrVideo2TtsResponse.onError(string2, 4004, "\u5f53\u524d\u5bfc\u822a\u9002\u914d\u672a\u5b9e\u73b0\u89c6\u9891\u7406\u89e3");
            }
        });
        agentSdk.registerAudioVideo2Vlm(new AudioVideo2VlmCallback(agentSdk){

            @Override
            public void onRequest(String string, String string2, int n, byte[] byArray, AgentParam agentParam, AudioVideo2VlmResponse audioVideo2VlmResponse) {
                log.warn("Unsupported AudioVideo2Vlm request: agentId={}, eventId={}, flag={}", string, string2, n);
                audioVideo2VlmResponse.onError(string2, 4004, "\u5f53\u524d\u5bfc\u822a\u9002\u914d\u672a\u5b9e\u73b0\u97f3\u89c6\u9891\u7406\u89e3");
            }
        });
        agentSdk.registerAudioVideo2Tts(new AudioVideo2TtsCallback(agentSdk){

            @Override
            public void onRequest(String string, String string2, int n, byte[] byArray, AgentParam agentParam, AudioVideo2TtsResponse audioVideo2TtsResponse) {
                log.warn("Unsupported AudioVideo2Tts request: agentId={}, eventId={}, flag={}", string, string2, n);
                audioVideo2TtsResponse.onError(string2, 4004, "\u5f53\u524d\u5bfc\u822a\u9002\u914d\u672a\u5b9e\u73b0\u97f3\u89c6\u9891\u7406\u89e3");
            }
        });
    }

    private static void logAudioRequest(String string, String string2, String string3, String string4, int n, byte[] byArray) {
        int n2;
        int n3 = n2 = byArray == null ? 0 : byArray.length;
        if (n == 1) {
            log.debug("{} chunk: agentId={}, eventId={}, itemId={}, audioLen={}", string, string2, string3, string4, n2);
            return;
        }
        log.info("{} request: agentId={}, eventId={}, itemId={}, flag={}, audioLen={}", string, string2, string3, string4, n, n2);
    }

    private static AudioSession newAudioSession(String string, String string2, String string3, NavBridgeClient navBridgeClient) {
        WakeGate wakeGate = LingxiNavAgentApp.checkTourWake(string2, string3, navBridgeClient);
        String string4;
        if (wakeGate.handled) {
            string4 = wakeGate.accepted ? "" : wakeGate.reason;
        } else {
            string4 = LingxiNavAgentApp.busyInteractionIgnoreReason();
        }
        return new AudioSession(string, string4);
    }

    private static String busyInteractionIgnoreReason() {
        if (TTS_CONTROLLER.isSilentWindowActive()) {
            return "robot_tts_silent_window";
        }
        return "";
    }

    private static String completedAudioIgnoreReason(AudioSession audioSession) {
        if (audioSession.shouldIgnore()) {
            return audioSession.ignoreReason;
        }
        return "";
    }

    private static boolean submitAudioTask(String string, String string2, Runnable runnable) {
        AUDIO_TASKS_IN_FLIGHT.incrementAndGet();
        try {
            AUDIO_WORKERS.execute(() -> {
                int n;
                try {
                    runnable.run();
                    n = AUDIO_TASKS_IN_FLIGHT.decrementAndGet();
                }
                catch (Throwable throwable) {
                    int n2 = AUDIO_TASKS_IN_FLIGHT.decrementAndGet();
                    log.debug("{} audio task finished: eventId={}, remaining={}", string, string2, n2);
                    throw throwable;
                }
                log.debug("{} audio task finished: eventId={}, remaining={}", string, string2, n);
            });
            return true;
        }
        catch (RejectedExecutionException rejectedExecutionException) {
            int n = AUDIO_TASKS_IN_FLIGHT.decrementAndGet();
            log.warn("{} audio task rejected: eventId={}, active={}, queued={}, remaining={}", string, string2, AUDIO_WORKERS.getActiveCount(), AUDIO_WORKERS.getQueue().size(), n);
            return false;
        }
    }

    private static void handleAudio2LlmChunk(String string2, int n, byte[] byArray, Audio2LlmResponse audio2LlmResponse, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        if (n == 0) {
            AudioSession audioSession = LingxiNavAgentApp.newAudioSession(LingxiNavAgentApp.newItemId(), string2, "audio2llm_wake", navBridgeClient);
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, byArray);
            }
            AUDIO_2_LLM_SESSIONS.put(string2, audioSession);
            if (audioSession.shouldIgnore()) {
                log.info("Audio2Llm event ignored: eventId={}, reason={}", (Object)string2, (Object)audioSession.ignoreReason);
            }
            return;
        }
        if (n == 1) {
            AudioSession audioSession = AUDIO_2_LLM_SESSIONS.computeIfAbsent(string2, string -> LingxiNavAgentApp.newAudioSession(LingxiNavAgentApp.newItemId(), string2, "audio2llm_wake", navBridgeClient));
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, byArray);
            }
            return;
        }
        if (n == 2) {
            String string3;
            AudioSession audioSession = AUDIO_2_LLM_SESSIONS.remove(string2);
            if (audioSession == null) {
                audioSession = LingxiNavAgentApp.newAudioSession(LingxiNavAgentApp.newItemId(), string2, "audio2llm_wake", navBridgeClient);
            }
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, byArray);
            }
            if (!(string3 = LingxiNavAgentApp.completedAudioIgnoreReason(audioSession)).isBlank()) {
                log.info("Audio2Llm completed event ignored: eventId={}, reason={}", (Object)string2, (Object)string3);
                LingxiNavAgentApp.finishIgnoredAudio2Llm(string2, audio2LlmResponse);
                return;
            }
            AudioSession audioSession2 = audioSession;
            if (!LingxiNavAgentApp.submitAudioTask("Audio2Llm", string2, () -> LingxiNavAgentApp.processCompletedAudio2Llm(string2, audioSession2, audio2LlmResponse, appConfig, navBridgeClient, doubaoArkClient, qaStore))) {
                LingxiNavAgentApp.finishIgnoredAudio2Llm(string2, audio2LlmResponse);
            }
            return;
        }
        audio2LlmResponse.onError(string2, 4001, "\u672a\u77e5 Audio2Llm flag: " + n);
    }

    private static void handleAudio2TtsChunk(String string, String string3, int n, byte[] byArray, Audio2TtsResponse audio2TtsResponse, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        if (n == 0) {
            AudioSession audioSession = LingxiNavAgentApp.newAudioSession(LingxiNavAgentApp.normalizeItemId(string3), string, "audio2tts_wake", navBridgeClient);
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, byArray);
            }
            AUDIO_2_TTS_SESSIONS.put(string, audioSession);
            if (audioSession.shouldIgnore()) {
                log.info("Audio2Tts event ignored: eventId={}, reason={}", (Object)string, (Object)audioSession.ignoreReason);
            }
            return;
        }
        if (n == 1) {
            AudioSession audioSession = AUDIO_2_TTS_SESSIONS.computeIfAbsent(string, string2 -> LingxiNavAgentApp.newAudioSession(LingxiNavAgentApp.normalizeItemId(string3), string, "audio2tts_wake", navBridgeClient));
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, byArray);
            }
            return;
        }
        if (n == 2) {
            String string4;
            AudioSession audioSession = AUDIO_2_TTS_SESSIONS.remove(string);
            if (audioSession == null) {
                audioSession = LingxiNavAgentApp.newAudioSession(LingxiNavAgentApp.normalizeItemId(string3), string, "audio2tts_wake", navBridgeClient);
            }
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, byArray);
            }
            if (!(string4 = LingxiNavAgentApp.completedAudioIgnoreReason(audioSession)).isBlank()) {
                log.info("Audio2Tts completed event ignored: eventId={}, reason={}", (Object)string, (Object)string4);
                LingxiNavAgentApp.finishIgnoredAudio2Tts(string, audioSession.itemId, audio2TtsResponse);
                return;
            }
            AudioSession audioSession2 = audioSession;
            if (!LingxiNavAgentApp.submitAudioTask("Audio2Tts", string, () -> LingxiNavAgentApp.processCompletedAudio2Tts(string, audioSession2, audio2TtsResponse, appConfig, navBridgeClient, doubaoArkClient, qaStore))) {
                LingxiNavAgentApp.finishIgnoredAudio2Tts(string, audioSession2.itemId, audio2TtsResponse);
            }
            return;
        }
        audio2TtsResponse.onError(string, 4001, "\u672a\u77e5 Audio2Tts flag: " + n);
    }

    /*
     * WARNING - Removed try catching itself - possible behaviour change.
     */
    private static void processCompletedAudio2Llm(String string, AudioSession audioSession, Audio2LlmResponse audio2LlmResponse, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        AtomicBoolean atomicBoolean = new AtomicBoolean(false);
        try {
            AudioResult audioResult = LingxiNavAgentApp.processAudioRequest(string, audioSession, appConfig, navBridgeClient, doubaoArkClient, qaStore, atomicBoolean);
            log.info("Audio2Llm final: eventId={}, asrText={}, reply={}, priority={}", new Object[]{string, LingxiNavAgentApp.abbreviate(audioResult.asrText), LingxiNavAgentApp.abbreviate(audioResult.reply), audioResult.ttsPriority});
            audio2LlmResponse.onAsrFinal(string, audioResult.asrText);
            if (LingxiNavAgentApp.shouldReturnLingxiText(appConfig)) {
                audio2LlmResponse.onLlmItemDelta(string, audioSession.itemId, audioResult.reply);
            } else {
                log.info("Lingxi LLM text suppressed for local robot TTS test: eventId={}, text={}", (Object)string, (Object)LingxiNavAgentApp.abbreviate(audioResult.reply));
            }
            audio2LlmResponse.onLlmItemDone(string, audioSession.itemId);
            audio2LlmResponse.onLlmDone(string);
            LingxiNavAgentApp.dispatchSkillIfNeeded(audio2LlmResponse, string, audioSession.itemId, audioResult.skillAction);
            LingxiNavAgentApp.speakIfNeeded(appConfig, string, audioResult.reply, audioResult.ttsPriority, navBridgeClient);
        }
        catch (Exception exception) {
            LingxiNavAgentApp.logAudioHandlingFailure("Audio2Llm", string, audioSession, exception);
            LingxiNavAgentApp.speakIfNeeded(appConfig, string, "网络暂时有点慢，请再说一遍。", TtsPriority.NORMAL, navBridgeClient);
            audio2LlmResponse.onError(string, 5001, "\u5904\u7406\u5931\u8d25: " + exception.getMessage());
        }
        finally {
            if (atomicBoolean.get()) {
                LingxiNavAgentApp.endUserInteraction(string, "audio2llm_done", navBridgeClient);
            }
        }
    }

    /*
     * WARNING - Removed try catching itself - possible behaviour change.
     */
    private static void processCompletedAudio2Tts(String string, AudioSession audioSession, Audio2TtsResponse audio2TtsResponse, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        AtomicBoolean atomicBoolean = new AtomicBoolean(false);
        try {
            AudioResult audioResult = LingxiNavAgentApp.processAudioRequest(string, audioSession, appConfig, navBridgeClient, doubaoArkClient, qaStore, atomicBoolean);
            log.info("Audio2Tts final: eventId={}, asrText={}, reply={}, priority={}", new Object[]{string, LingxiNavAgentApp.abbreviate(audioResult.asrText), LingxiNavAgentApp.abbreviate(audioResult.reply), audioResult.ttsPriority});
            audio2TtsResponse.onAsrFinal(string, audioResult.asrText);
            if (LingxiNavAgentApp.shouldReturnLingxiText(appConfig)) {
                audio2TtsResponse.onLlmItemDelta(string, audioSession.itemId, audioResult.reply);
            } else {
                log.info("Lingxi LLM text suppressed for local robot TTS test: eventId={}, text={}", (Object)string, (Object)LingxiNavAgentApp.abbreviate(audioResult.reply));
            }
            audio2TtsResponse.onLlmItemDone(string, audioSession.itemId);
            audio2TtsResponse.onLlmDone(string);
            LingxiNavAgentApp.dispatchSkillIfNeeded(audio2TtsResponse, string, audioSession.itemId, audioResult.skillAction);
            LingxiNavAgentApp.speakIfNeeded(appConfig, string, audioResult.reply, audioResult.ttsPriority, navBridgeClient);
            audio2TtsResponse.onTtsItemDone(string, audioSession.itemId);
            audio2TtsResponse.onTtsDone(string);
        }
        catch (Exception exception) {
            LingxiNavAgentApp.logAudioHandlingFailure("Audio2Tts", string, audioSession, exception);
            LingxiNavAgentApp.speakIfNeeded(appConfig, string, "网络暂时有点慢，请再说一遍。", TtsPriority.NORMAL, navBridgeClient);
            audio2TtsResponse.onError(string, 5001, "\u5904\u7406\u5931\u8d25: " + exception.getMessage());
        }
        finally {
            if (atomicBoolean.get()) {
                LingxiNavAgentApp.endUserInteraction(string, "audio2tts_done", navBridgeClient);
            }
        }
    }

    private static AudioResult processAudioRequest(String string, AudioSession audioSession, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore, AtomicBoolean atomicBoolean) throws Exception {
        byte[] byArray = audioSession.toByteArray();
        if (byArray.length == 0) {
            throw new IllegalArgumentException("\u97f3\u9891\u4e3a\u7a7a");
        }
        long l = System.currentTimeMillis();
        List<String> list = navBridgeClient.listPointNames();
        long l2 = System.currentTimeMillis();
        log.info("Audio request point names loaded: eventId={}, audioBytes={}, points={}, elapsedMs={}", string, byArray.length, list.size(), l2 - l);
        NavigationIntent navigationIntent = doubaoArkClient.parseInteractionAudio(byArray, list);
        log.info("Audio request parsed by Doubao: eventId={}, elapsedMs={}", (Object)string, (Object)(System.currentTimeMillis() - l2));
        String string2 = LingxiNavAgentApp.describeIntent(navigationIntent);
        LingxiNavAgentApp.logIntent("audio_asr_final", string, string2, navigationIntent);
        LingxiNavAgentApp.markUserInteraction(string, "audio_asr_final", navigationIntent, navBridgeClient, atomicBoolean);
        InteractionResult interactionResult;
        if ("chat".equals(navigationIntent.intent) && !navigationIntent.reply.isBlank()) {
            String directReply = navigationIntent.reply;
            if (LingxiNavAgentApp.mentionsForbiddenIdentity(directReply)) {
                directReply = doubaoArkClient.identityReply();
            }
            String question = navigationIntent.questionOrDefault(string2);
            if (!LingxiNavAgentApp.isIdentityQuestion(question)) {
                String knowledgeAnswer = LingxiNavAgentApp.knowledgeAnswer(question);
                if (knowledgeAnswer != null) {
                    directReply = knowledgeAnswer;
                    log.info("Audio knowledge base answer selected: eventId={}", (Object)string);
                }
                qaStore.savePair(question, directReply);
            }
            log.info("Audio chat answered in one model request: eventId={}", (Object)string);
            interactionResult = new InteractionResult(directReply, TtsPriority.NORMAL);
        } else {
            interactionResult = LingxiNavAgentApp.handleIntent(string, string2, navigationIntent, navBridgeClient, doubaoArkClient, qaStore, true);
        }
        return new AudioResult(string2, interactionResult.reply, interactionResult.ttsPriority, interactionResult.skillAction);
    }

    private static InteractionResult processTextRequest(String string, String string2, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore, AtomicBoolean atomicBoolean) throws Exception {
        List<String> list = navBridgeClient.listPointNames();
        NavigationIntent navigationIntent = LingxiNavAgentApp.taskControlIntentFromText(string2);
        if (navigationIntent == null) {
            navigationIntent = doubaoArkClient.parseInteraction(string2, list);
        } else {
            log.info("Local tour control rule matched: eventId={}, intent={}, text={}", string, navigationIntent.intent, LingxiNavAgentApp.abbreviate(string2));
        }
        LingxiNavAgentApp.logIntent("text_asr_final", string, string2, navigationIntent);
        LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", navigationIntent, navBridgeClient, atomicBoolean);
        InteractionResult interactionResult = LingxiNavAgentApp.handleIntent(string, string2, navigationIntent, navBridgeClient, doubaoArkClient, qaStore, true);
        return interactionResult;
    }

    private static void handleLiveDanmu(LiveDanmuServer.IncomingDanmu incomingDanmu, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) throws Exception {
        log.info("Live danmu accepted: eventId={}, msgId={}, userId={}, nickname={}, text={}", incomingDanmu.eventId(), incomingDanmu.msgId(), incomingDanmu.userId(), incomingDanmu.nickname(), LingxiNavAgentApp.abbreviate(incomingDanmu.text()));
        InteractionResult interactionResult = LingxiNavAgentApp.processLiveTextRequest(incomingDanmu.eventId(), incomingDanmu.text(), appConfig, navBridgeClient, doubaoArkClient, qaStore);
        RobotTtsResult robotTtsResult = LingxiNavAgentApp.speakIfNeeded(appConfig, incomingDanmu.eventId(), interactionResult.reply, interactionResult.ttsPriority, navBridgeClient);
        log.info("Live danmu replied: eventId={}, msgId={}, ttsRequested={}, ttsAccepted={}, text={}", incomingDanmu.eventId(), incomingDanmu.msgId(), robotTtsResult.requested, robotTtsResult.accepted, LingxiNavAgentApp.abbreviate(interactionResult.reply));
    }

    private static InteractionResult processLiveTextRequest(String string, String string2, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) throws Exception {
        NavigationIntent navigationIntent = LingxiNavAgentApp.entertainmentIntentFromText(string2);
        if (navigationIntent != null) {
            log.info("Live entertainment rule matched: eventId={}, intent={}, text={}", string, navigationIntent.intent, LingxiNavAgentApp.abbreviate(string2));
        } else {
            List<String> list = appConfig.liveAllowNavigation ? navBridgeClient.listPointNames() : List.of();
            navigationIntent = doubaoArkClient.parseInteraction(string2, list);
        }
        LingxiNavAgentApp.logIntent("live_danmu", string, string2, navigationIntent);
        if (!appConfig.liveAllowNavigation && LingxiNavAgentApp.isRobotControlIntent(navigationIntent)) {
            return new InteractionResult("\u76f4\u64ad\u5f39\u5e55\u6682\u4e0d\u652f\u6301\u63a7\u5236\u673a\u5668\u4eba\u79fb\u52a8\u3002", TtsPriority.NORMAL);
        }
        if (!appConfig.liveAllowEntertainment && LingxiNavAgentApp.isRobotEntertainmentIntent(navigationIntent)) {
            return new InteractionResult("\u76f4\u64ad\u5f39\u5e55\u6682\u4e0d\u652f\u6301\u89e6\u53d1\u673a\u5668\u4eba\u8868\u6f14\u3002", TtsPriority.NORMAL);
        }
        return LingxiNavAgentApp.handleIntent(string, string2, navigationIntent, navBridgeClient, doubaoArkClient, qaStore, true);
    }

    private static NavigationIntent entertainmentIntentFromText(String string) {
        String string2 = LingxiNavAgentApp.normalizeText(string);
        if (string2.isBlank()) {
            return null;
        }
        if (LingxiNavAgentApp.containsAny(string2, "\u8df3\u592a\u6781", "\u592a\u6781\u821e", "\u6765\u6bb5\u592a\u6781", "\u6765\u4e00\u6bb5\u592a\u6781", "taiji", "tai chi", "taichi")) {
            return NavigationIntent.dance("\u592a\u6781", "\u597d\u7684\uff0c\u6211\u6765\u8df3\u592a\u6781\u3002");
        }
        if (LingxiNavAgentApp.containsAny(string2, "\u8df3\u821e", "\u8df3\u4e2a\u821e", "\u8df3\u652f\u821e", "\u821e\u8e48", "\u6765\u6bb5\u821e", "\u6765\u4e00\u6bb5\u821e", "dance", "dancing")) {
            return NavigationIntent.dance("\u597d\u7684\uff0c\u6211\u6765\u8df3\u4e00\u6bb5\u3002");
        }
        if (LingxiNavAgentApp.containsAny(string2, "\u5531\u6b4c", "\u5531\u9996\u6b4c", "\u5531\u4e00\u9996", "\u6765\u9996\u6b4c", "\u6765\u4e00\u9996\u6b4c", "\u653e\u6b4c", "\u64ad\u6b4c", "\u70b9\u6b4c", "sing", "song", "music")) {
            return NavigationIntent.sing("\u597d\u7684\uff0c\u6211\u6765\u5531\u4e00\u9996\u3002");
        }
        return null;
    }

    private static NavigationIntent taskControlIntentFromText(String string) {
        String string2 = LingxiNavAgentApp.normalizeText(string);
        if (string2.isBlank()) {
            return null;
        }
        if (LingxiNavAgentApp.containsAny(string2, "暂停讲解", "暂停任务", "讲解暂停", "先暂停", "暂停一下")) {
            return NavigationIntent.pauseTask();
        }
        if (LingxiNavAgentApp.containsAny(string2, "继续讲解", "继续任务", "恢复讲解", "恢复任务", "接着讲解")) {
            return NavigationIntent.resumeTask();
        }
        if (LingxiNavAgentApp.containsAny(string2, "停止讲解", "结束讲解", "停止任务", "结束任务", "终止讲解", "终止任务")) {
            return NavigationIntent.endTask();
        }
        if (LingxiNavAgentApp.containsAny(string2, "开始讲解", "启动讲解", "开始任务", "开始参观", "启动任务")) {
            return NavigationIntent.startTask();
        }
        return null;
    }

    private static NavigationIntent localEntertainmentIntentFromRequest(String string, NavigationIntent navigationIntent) {
        StringBuilder stringBuilder = new StringBuilder();
        LingxiNavAgentApp.appendIfNotBlank(stringBuilder, string);
        LingxiNavAgentApp.appendIfNotBlank(stringBuilder, navigationIntent.targetName);
        LingxiNavAgentApp.appendIfNotBlank(stringBuilder, navigationIntent.question);
        LingxiNavAgentApp.appendIfNotBlank(stringBuilder, navigationIntent.reply);
        return LingxiNavAgentApp.entertainmentIntentFromText(stringBuilder.toString());
    }

    private static void appendIfNotBlank(StringBuilder stringBuilder, String string) {
        if (string == null || string.isBlank()) {
            return;
        }
        if (stringBuilder.length() > 0) {
            stringBuilder.append(' ');
        }
        stringBuilder.append(string);
    }

    private static boolean containsAny(String string, String ... stringArray) {
        for (String string2 : stringArray) {
            if (!string.contains(string2)) continue;
            return true;
        }
        return false;
    }

    private static boolean isRobotControlIntent(NavigationIntent navigationIntent) {
        return navigationIntent.isNavigate() || navigationIntent.isStop() || navigationIntent.isStartTask() || navigationIntent.isPauseTask() || navigationIntent.isResumeTask() || navigationIntent.isEndTask();
    }

    private static boolean isRobotEntertainmentIntent(NavigationIntent navigationIntent) {
        return navigationIntent.isSing() || navigationIntent.isDance();
    }

    private static void dispatchSkillIfNeeded(Object object, String string, String string2, String string3) {
    }

    private static boolean shouldReturnLingxiText(AppConfig appConfig) {
        return !"robot".equals(appConfig.ttsMode) || !appConfig.ttsSuppressLingxiText;
    }

    private static void markUserInteraction(String string, String string2, NavigationIntent navigationIntent, NavBridgeClient navBridgeClient, AtomicBoolean atomicBoolean) {
        atomicBoolean.set(true);
        try {
            JSONObject jSONObject = navBridgeClient.markInteraction(string, string2, navigationIntent.intent);
            String string3 = jSONObject == null ? "unknown" : jSONObject.getString("state");
            log.info("User interaction marked: eventId={}, source={}, intent={}, state={}", string, string2, navigationIntent.intent, string3);
        }
        catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("User interaction mark failed: eventId={}, source={}, intent={}, error={}", string, string2, navigationIntent.intent, exception.getMessage());
        }
    }

    private static void endUserInteraction(String string, String string2, NavBridgeClient navBridgeClient) {
        try {
            JSONObject jSONObject = navBridgeClient.endInteraction(string, string2, "");
            String string3 = jSONObject == null ? "unknown" : jSONObject.getString("state");
            log.info("User interaction ended: eventId={}, source={}, state={}", string, string2, string3);
        }
        catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("User interaction end failed: eventId={}, source={}, error={}", string, string2, exception.getMessage());
        }
    }

    private static void logIntent(String string, String string2, String string3, NavigationIntent navigationIntent) {
        log.info("User intent parsed: eventId={}, source={}, intent={}, targetName={}, question={}, text={}", string2, string, navigationIntent.intent, navigationIntent.targetName, LingxiNavAgentApp.abbreviate(navigationIntent.questionOrDefault("")), LingxiNavAgentApp.abbreviate(string3));
    }

    /*
     * WARNING - Removed try catching itself - possible behaviour change.
     */
    private static RobotTtsResult speakIfNeeded(AppConfig appConfig, String string, String string2, TtsPriority ttsPriority, NavBridgeClient navBridgeClient) {
        if (string2 == null || string2.isBlank()) {
            log.info("Robot TTS skipped: eventId={}, reason=empty_reply", (Object)string);
            return RobotTtsResult.skipped("empty_reply");
        }
        if (!"robot".equals(appConfig.ttsMode)) {
            log.info("Robot TTS not requested because tts.mode={}: eventId={}, text={}", appConfig.ttsMode, string, LingxiNavAgentApp.abbreviate(string2));
            return RobotTtsResult.skipped("tts_mode_" + appConfig.ttsMode);
        }
        if (appConfig.ttsFixedDialogTest) {
            boolean bl = false;
            long l = TTS_CONTROLLER.estimatePlaybackMillis(FIXED_DIALOG_TTS_TEXT);
            try {
                LingxiNavAgentApp.delayBeforeRobotTts(appConfig);
                log.info("Fixed normal dialog TTS test enabled: originalEventId={}, fixedEventId={}, originalText={}, fixedText={}", string, FIXED_DIALOG_TTS_EVENT_ID, LingxiNavAgentApp.abbreviate(string2), FIXED_DIALOG_TTS_TEXT);
                JSONObject jSONObject = navBridgeClient.speak(FIXED_DIALOG_TTS_EVENT_ID, FIXED_DIALOG_TTS_TEXT, true, true);
                bl = jSONObject.getBooleanValue("accepted");
                if (!bl) {
                    log.warn("Robot fixed test TTS was not accepted: {}", (Object)jSONObject);
                }
            }
            catch (Exception exception) {
                log.warn("Robot fixed test TTS request failed: {}", (Object)exception.getMessage());
            }
            finally {
                if (bl) {
                    TTS_CONTROLLER.openSilentWindow(l);
                }
            }
            return new RobotTtsResult(true, bl, bl ? l : 0L, "fixed_dialog_test");
        }
        TtsDecision ttsDecision = TTS_CONTROLLER.tryAcquire(string2, ttsPriority);
        if (!ttsDecision.allowed) {
            log.info("Robot TTS skipped: eventId={}, priority={}, reason={}, text={}", new Object[]{string, ttsPriority, ttsDecision.reason, LingxiNavAgentApp.abbreviate(string2)});
            return RobotTtsResult.skipped(ttsDecision.reason);
        }
        boolean bl = false;
        try {
            LingxiNavAgentApp.delayBeforeRobotTts(appConfig);
            // Lingxi submits an L6 placeholder for the same audio turn. Keep every
            // robot-local reply at L7 so the placeholder cannot preempt playback.
            JSONObject jSONObject = navBridgeClient.speak(string, string2, ttsDecision.interrupt, true);
            bl = jSONObject.getBooleanValue("accepted");
            if (!bl) {
                log.warn("Robot TTS was not accepted: {}", (Object)jSONObject);
            }
        }
        catch (Exception exception) {
            log.warn("Robot TTS request failed: {}", (Object)exception.getMessage());
        }
        finally {
            TTS_CONTROLLER.finish(ttsDecision, bl);
        }
        return new RobotTtsResult(true, bl, bl ? ttsDecision.estimatedPlaybackMillis : 0L, ttsDecision.reason);
    }

    private static void delayBeforeRobotTts(AppConfig appConfig) throws InterruptedException {
        if (appConfig.ttsDelayBeforeSpeakMs > 0L) {
            Thread.sleep(appConfig.ttsDelayBeforeSpeakMs);
        }
    }

    private static InteractionResult handleIntent(String string, String string2, NavigationIntent navigationIntent, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore, boolean bl) throws Exception {
        Object object;
        NavigationIntent navigationIntent2 = LingxiNavAgentApp.localEntertainmentIntentFromRequest(string2, navigationIntent);
        if (navigationIntent2 != null && (navigationIntent.isSing() || navigationIntent.isDance() || "chat".equals(navigationIntent.intent))) {
            log.info("Local entertainment rule matched: eventId={}, originalIntent={}, localIntent={}, text={}, question={}, reply={}", string, navigationIntent.intent, navigationIntent2.intent, LingxiNavAgentApp.abbreviate(string2), LingxiNavAgentApp.abbreviate(navigationIntent.question), LingxiNavAgentApp.abbreviate(navigationIntent.reply));
            navigationIntent = navigationIntent2;
        }
        if (navigationIntent.isNavigate()) {
            if (navigationIntent.targetName.isBlank()) {
                return new InteractionResult(navigationIntent.replyOrDefault("\u4f60\u60f3\u8ba9\u6211\u5e26\u4f60\u53bb\u54ea\u91cc\uff1f"), TtsPriority.NORMAL);
            }
            JSONObject jSONObject = navBridgeClient.startNavigation(string, navigationIntent.targetName);
            boolean bl2 = jSONObject.getBooleanValue("accepted");
            String string3 = jSONObject.getString("status");
            if (bl2) {
                return new InteractionResult(navigationIntent.replyOrDefault("\u597d\u7684\uff0c\u6211\u5e26\u4f60\u53bb" + navigationIntent.targetName), TtsPriority.NAVIGATION);
            }
            if ("point_not_found".equals(string3)) {
                return new InteractionResult("\u6211\u6ca1\u6709\u627e\u5230" + navigationIntent.targetName + "\u8fd9\u4e2a\u5bfc\u822a\u70b9", TtsPriority.NAVIGATION);
            }
            if ("busy".equals(string3)) {
                return new InteractionResult("\u5f53\u524d\u5df2\u6709\u5bfc\u822a\u4efb\u52a1\u5728\u8fdb\u884c\u4e2d\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5", TtsPriority.NAVIGATION);
            }
            String string4 = jSONObject.getString("message") == null ? navigationIntent.replyOrDefault("\u5bfc\u822a\u8bf7\u6c42\u6ca1\u6709\u88ab\u63a5\u53d7") : jSONObject.getString("message");
            return new InteractionResult(string4, TtsPriority.NAVIGATION);
        }
        if (navigationIntent.isStop()) {
            JSONObject jSONObject = navBridgeClient.stopNavigation(string);
            boolean bl3 = jSONObject.getBooleanValue("cancelled");
            return new InteractionResult(bl3 ? "\u5df2\u53d6\u6d88\u5bfc\u822a" : "\u5f53\u524d\u6ca1\u6709\u6b63\u5728\u8fdb\u884c\u7684\u5bfc\u822a", TtsPriority.URGENT);
        }
        if (navigationIntent.isStartTask()) {
            JSONObject jSONObject = navBridgeClient.startTask(string);
            boolean bl4 = jSONObject.getBooleanValue("accepted");
            String string5 = jSONObject.getString("status");
            if (bl4) {
                return new InteractionResult(navigationIntent.replyOrDefault("\u597d\u7684\uff0c\u5f00\u59cb\u8bb2\u89e3\u4efb\u52a1"), TtsPriority.NAVIGATION);
            }
            if ("no_task_steps".equals(string5)) {
                return new InteractionResult("\u5f53\u524d\u5730\u56fe\u8fd8\u6ca1\u6709\u914d\u7f6e\u8bb2\u89e3\u4efb\u52a1", TtsPriority.NAVIGATION);
            }
            if ("busy".equals(string5)) {
                return new InteractionResult("\u5f53\u524d\u5df2\u6709\u5bfc\u822a\u4efb\u52a1\u5728\u8fdb\u884c\u4e2d\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5", TtsPriority.NAVIGATION);
            }
            String string6 = jSONObject.getString("message") == null ? navigationIntent.replyOrDefault("\u8bb2\u89e3\u4efb\u52a1\u6ca1\u6709\u88ab\u63a5\u53d7") : jSONObject.getString("message");
            return new InteractionResult(string6, TtsPriority.NAVIGATION);
        }
        if (navigationIntent.isPauseTask()) {
            JSONObject jSONObject = navBridgeClient.pauseTask(string);
            boolean paused = jSONObject.getBooleanValue("paused");
            String status = jSONObject.getString("status");
            if (paused && "already_paused".equals(status)) {
                return new InteractionResult("讲解任务已经暂停", TtsPriority.URGENT);
            }
            return new InteractionResult(paused ? "已暂停讲解任务" : "当前没有正在执行的讲解任务", TtsPriority.URGENT);
        }
        if (navigationIntent.isResumeTask()) {
            JSONObject jSONObject = navBridgeClient.resumeTask(string);
            boolean resumed = jSONObject.getBooleanValue("resumed");
            String status = jSONObject.getString("status");
            if ("busy".equals(status)) {
                return new InteractionResult("导航正在停止，请稍后再说继续讲解", TtsPriority.NAVIGATION);
            }
            return new InteractionResult(resumed ? "好的，继续讲解任务" : "当前没有暂停的讲解任务", TtsPriority.NAVIGATION);
        }
        if (navigationIntent.isEndTask()) {
            JSONObject jSONObject = navBridgeClient.stopTask(string);
            boolean bl5 = jSONObject.getBooleanValue("stopped");
            return new InteractionResult(bl5 ? "\u5df2\u7ed3\u675f\u8bb2\u89e3\u4efb\u52a1" : "\u5f53\u524d\u6ca1\u6709\u6b63\u5728\u6267\u884c\u7684\u8bb2\u89e3\u4efb\u52a1", TtsPriority.URGENT);
        }
        if (navigationIntent.isSing()) {
            return new InteractionResult("\u5531\u6b4c\u529f\u80fd\u6682\u65f6\u5173\u95ed\u4e86\uff0c\u6211\u73b0\u5728\u53ef\u4ee5\u7ed9\u4f60\u8df3\u821e\u3002", TtsPriority.NORMAL);
        }
        if (navigationIntent.isDance()) {
            if (!bl) {
                return new InteractionResult(navigationIntent.replyOrDefault("\u597d\u7684\uff0c\u6211\u6765\u8df3\u4e00\u6bb5\u3002"), TtsPriority.NORMAL, "dance");
            }
            String string7 = navigationIntent.targetName.isBlank() ? string2 : navigationIntent.targetName;
            JSONObject jSONObject = navBridgeClient.dance(string, string7);
            boolean bl6 = jSONObject.getBooleanValue("accepted");
            if (bl6) {
                return new InteractionResult("", TtsPriority.NORMAL);
            }
            String string8 = jSONObject.getString("message") == null ? navigationIntent.replyOrDefault("\u8df3\u821e\u8bf7\u6c42\u6ca1\u6709\u88ab\u673a\u5668\u4eba\u63a5\u53d7\u3002") : jSONObject.getString("message");
            return new InteractionResult(string8, TtsPriority.NORMAL);
        }
        String string9 = navigationIntent.questionOrDefault(string2);
        if (LingxiNavAgentApp.isDateQuestion(string9)) {
            return new InteractionResult(LingxiNavAgentApp.dateReply(), TtsPriority.NORMAL);
        }
        boolean bl7 = LingxiNavAgentApp.isIdentityQuestion(string9);
        if (!bl7 && ((Optional)(object = qaStore.getAnswer(string9))).isPresent()) {
            return new InteractionResult((String)((Optional)object).get(), TtsPriority.NORMAL);
        }
        String knowledgeAnswer = LingxiNavAgentApp.knowledgeAnswer(string9);
        if (knowledgeAnswer != null) {
            log.info("Knowledge base answer selected: eventId={}, question={}", string, LingxiNavAgentApp.abbreviate(string9));
            if (!bl7) {
                qaStore.savePair(string9, knowledgeAnswer);
            }
            return new InteractionResult(knowledgeAnswer, TtsPriority.NORMAL);
        }
        object = doubaoArkClient.chat(string9);
        if (LingxiNavAgentApp.mentionsForbiddenIdentity((String)object)) {
            object = doubaoArkClient.identityReply();
        }
        if (!bl7 && !LingxiNavAgentApp.mentionsForbiddenIdentity((String)object)) {
            qaStore.savePair(string9, (String)object);
        }
        return new InteractionResult((String)object, TtsPriority.NORMAL);
    }

    private static String knowledgeAnswer(String question) {
        try {
            return RETRIEVER_CLIENT.findAnswer(question);
        }
        catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("Knowledge base lookup failed; falling back to normal dialog: question={}, error={}", LingxiNavAgentApp.abbreviate(question), exception.getMessage());
            return null;
        }
    }

    private static boolean isIdentityQuestion(String string) {
        if (string == null) {
            return false;
        }
        String string2 = string.replaceAll("\\s+", "");
        return string2.contains("\u4f60\u662f\u8c01") || string2.contains("\u4f60\u53eb\u4ec0\u4e48") || string2.contains("\u4ecb\u7ecd\u4e00\u4e0b\u4f60\u81ea\u5df1") || string2.contains("\u4ecb\u7ecd\u4e0b\u81ea\u5df1") || string2.contains("\u4f60\u662f\u4ec0\u4e48") || string2.contains("\u4ec0\u4e48\u5927\u6a21\u578b") || string2.contains("\u4ec0\u4e48\u6a21\u578b") || string2.contains("\u7528\u7684\u662f\u4ec0\u4e48\u6a21\u578b") || string2.contains("\u4f7f\u7528\u4ec0\u4e48\u6a21\u578b") || string2.contains("\u4f7f\u7528\u4ec0\u4e48\u5927\u6a21\u578b");
    }

    private static boolean isDateQuestion(String string) {
        if (string == null) {
            return false;
        }
        String string2 = string.replaceAll("\\s+", "");
        return string2.contains("\u4eca\u5929\u661f\u671f\u51e0") || string2.contains("\u4eca\u5929\u5468\u51e0") || string2.contains("\u4eca\u5929\u793c\u62dc\u51e0") || string2.contains("\u4eca\u5929\u51e0\u53f7") || string2.contains("\u4eca\u5929\u65e5\u671f") || string2.contains("\u73b0\u5728\u661f\u671f\u51e0") || string2.contains("\u73b0\u5728\u5468\u51e0") || string2.contains("\u73b0\u5728\u51e0\u53f7");
    }

    private static String dateReply() {
        LocalDate localDate = LocalDate.now(ZoneId.of("Asia/Shanghai"));
        String[] stringArray = new String[]{"\u4e00", "\u4e8c", "\u4e09", "\u56db", "\u4e94", "\u516d", "\u65e5"};
        String string = stringArray[localDate.getDayOfWeek().getValue() - 1];
        return "\u4eca\u5929\u662f" + localDate.format(DateTimeFormatter.ofPattern("yyyy\u5e74M\u6708d\u65e5")) + "\uff0c\u661f\u671f" + string + "\u3002";
    }

    private static boolean mentionsForbiddenIdentity(String string) {
        if (string == null) {
            return false;
        }
        String string2 = string.toLowerCase();
        return string2.contains("\u8c46\u5305") || string2.contains("doubao") || string2.contains("\u5b57\u8282\u8df3\u52a8");
    }

    /*
     * WARNING - Removed try catching itself - possible behaviour change.
     */
    private static void appendAudio(AudioSession audioSession, byte[] byArray) {
        if (byArray == null || byArray.length == 0) {
            return;
        }
        ByteArrayOutputStream byteArrayOutputStream = audioSession.buffer;
        synchronized (byteArrayOutputStream) {
            audioSession.buffer.writeBytes(byArray);
        }
    }

    private static void logAudioHandlingFailure(String string, String string2, AudioSession audioSession, Exception exception) {
        String string3;
        String string4 = string3 = exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
        if (exception instanceof HttpTimeoutException) {
            log.warn("{} handling timed out: eventId={}, itemId={}, audioBytes={}, error={}", string, string2, audioSession.itemId, audioSession.byteLength(), string3);
            return;
        }
        log.error("{} handling failed: eventId={}, itemId={}, audioBytes={}", string, string2, audioSession.itemId, audioSession.byteLength(), exception);
    }

    private static String describeIntent(NavigationIntent navigationIntent) {
        if (navigationIntent.isNavigate()) {
            return "\u5bfc\u822a\u5230" + navigationIntent.targetName;
        }
        if (navigationIntent.isStop()) {
            return "\u505c\u6b62\u5bfc\u822a";
        }
        if (navigationIntent.isStartTask()) {
            return "\u5f00\u59cb\u8bb2\u89e3\u4efb\u52a1";
        }
        if (navigationIntent.isPauseTask()) {
            return "暂停讲解任务";
        }
        if (navigationIntent.isResumeTask()) {
            return "继续讲解任务";
        }
        if (navigationIntent.isEndTask()) {
            return "\u7ed3\u675f\u8bb2\u89e3\u4efb\u52a1";
        }
        if (navigationIntent.isSing()) {
            return "\u5531\u6b4c";
        }
        if (navigationIntent.isDance()) {
            return navigationIntent.targetName.isBlank() ? "\u8df3\u821e" : "\u8df3" + navigationIntent.targetName;
        }
        return navigationIntent.questionOrDefault("\u666e\u901a\u95ee\u7b54");
    }

    private static String normalizeItemId(String string) {
        return string == null || string.isBlank() ? LingxiNavAgentApp.newItemId() : string;
    }

    private static String newItemId() {
        return "item_" + UUID.randomUUID().toString().replace("-", "");
    }

    private static boolean shouldIgnoreTextCallback(String string, String string2, String string3, NavBridgeClient navBridgeClient) {
        WakeGate wakeGate = LingxiNavAgentApp.checkTourWake(string2, string.toLowerCase() + "_wake", navBridgeClient);
        if (wakeGate.handled) {
            if (wakeGate.accepted) {
                return false;
            }
            log.info("{} ignored during protected tour stage: eventId={}, reason={}, state={}, text={}", string, string2, wakeGate.reason, wakeGate.state, LingxiNavAgentApp.abbreviate(string3));
            return true;
        }
        String string4 = LingxiNavAgentApp.busyInteractionIgnoreReason();
        if (string4.isBlank()) {
            return false;
        }
        if (LingxiNavAgentApp.looksLikeUrgentStop(string3)) {
            log.info("{} allowed during busy interaction as urgent stop: eventId={}, reason={}, text={}", string, string2, string4, LingxiNavAgentApp.abbreviate(string3));
            return false;
        }
        log.info("{} ignored during busy interaction: eventId={}, reason={}, text={}", string, string2, string4, LingxiNavAgentApp.abbreviate(string3));
        return true;
    }

    private static WakeGate checkTourWake(String string, String string2, NavBridgeClient navBridgeClient) {
        try {
            JSONObject jSONObject = navBridgeClient.wakeInteraction(string, string2);
            boolean bl = jSONObject.getBooleanValue("handled");
            boolean bl2 = jSONObject.getBooleanValue("accepted");
            String string3 = jSONObject.getString("state");
            String string4 = jSONObject.getString("status");
            if (bl) {
                log.info("Tour wake decision: eventId={}, source={}, accepted={}, state={}, status={}", string, string2, bl2, string3, string4);
            }
            return new WakeGate(bl, bl2, string3 == null ? "" : string3, string4 == null ? "tour_wake_rejected" : string4);
        }
        catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("Tour wake check failed: eventId={}, source={}, error={}", string, string2, exception.getMessage());
            return new WakeGate(false, true, "", "bridge_unavailable");
        }
    }

    private static boolean looksLikeUrgentStop(String string) {
        String string2 = LingxiNavAgentApp.normalizeText(string);
        return Arrays.asList("\u505c\u4e0b", "\u505c\u6b62", "\u53d6\u6d88\u5bfc\u822a", "\u522b\u8d70", "\u4e0d\u8981\u8d70", "\u6025\u505c", "\u505c\u4e00\u505c", "\u7ed3\u675f\u4efb\u52a1", "\u505c\u6b62\u4efb\u52a1", "\u7ec8\u6b62\u4efb\u52a1", "\u7ed3\u675f\u8bb2\u89e3", "\u5f00\u59cb\u4efb\u52a1", "\u5f00\u59cb\u8bb2\u89e3", "暂停讲解", "暂停任务", "继续讲解", "继续任务", "恢复讲解").stream().anyMatch(string2::contains);
    }

    private static void finishIgnoredAudio2Llm(String string, Audio2LlmResponse audio2LlmResponse) {
        audio2LlmResponse.onLlmDone(string);
    }

    private static void finishIgnoredAudio2Tts(String string, String string2, Audio2TtsResponse audio2TtsResponse) {
        audio2TtsResponse.onLlmDone(string);
        audio2TtsResponse.onTtsItemDone(string, LingxiNavAgentApp.normalizeItemId(string2));
        audio2TtsResponse.onTtsDone(string);
    }

    private static void finishIgnoredAsr2Llm(String string, Asr2LlmResponse asr2LlmResponse) {
        asr2LlmResponse.onLlmDone(string);
    }

    private static void finishIgnoredAsr2Tts(String string, Asr2TtsResponse asr2TtsResponse) {
        asr2TtsResponse.onLlmDone(string);
        asr2TtsResponse.onTtsDone(string);
    }

    private static String abbreviate(String string) {
        if (string == null) {
            return "";
        }
        String string2 = string.replaceAll("\\s+", " ").trim();
        return string2.length() <= 60 ? string2 : string2.substring(0, 60) + "...";
    }

    private static String normalizeText(String string) {
        if (string == null) {
            return "";
        }
        return string.toLowerCase().replaceAll("[\\s\\p{Punct}\uff0c\u3002\uff01\uff1f\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019\uff08\uff09\u3010\u3011\u300a\u300b\u2026\u2014-]+", "").trim();
    }

    private static final class AudioSession {
        private final String itemId;
        private final String ignoreReason;
        private final ByteArrayOutputStream buffer = new ByteArrayOutputStream();

        private AudioSession(String string, String string2) {
            this.itemId = LingxiNavAgentApp.normalizeItemId(string);
            this.ignoreReason = string2 == null ? "" : string2;
        }

        private boolean shouldIgnore() {
            return !this.ignoreReason.isBlank();
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        private byte[] toByteArray() {
            ByteArrayOutputStream byteArrayOutputStream = this.buffer;
            synchronized (byteArrayOutputStream) {
                return this.buffer.toByteArray();
            }
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        private int byteLength() {
            ByteArrayOutputStream byteArrayOutputStream = this.buffer;
            synchronized (byteArrayOutputStream) {
                return this.buffer.size();
            }
        }
    }

    private static final class TtsController {
        private final long minIntervalMillis;
        private final long minSilentWindowMillis;
        private final long maxSilentWindowMillis;
        private final double duplicateSimilarityThreshold;
        private final Object lock = new Object();
        private boolean requestInFlight;
        private long lastAcceptedAtMillis;
        private long silentUntilMillis;
        private String lastNormalizedText = "";

        private TtsController(long l, long l2, long l3, double d) {
            this.minIntervalMillis = l;
            this.minSilentWindowMillis = l2;
            this.maxSilentWindowMillis = l3;
            this.duplicateSimilarityThreshold = d;
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        boolean isSilentWindowActive() {
            Object object = this.lock;
            synchronized (object) {
                return System.currentTimeMillis() < this.silentUntilMillis;
            }
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        TtsDecision tryAcquire(String string, TtsPriority ttsPriority) {
            String string2 = LingxiNavAgentApp.normalizeText(string);
            if (string2.isBlank()) {
                return TtsDecision.skip("empty_text");
            }
            Object object = this.lock;
            synchronized (object) {
                boolean bl;
                long l = System.currentTimeMillis();
                boolean bl2 = bl = ttsPriority == TtsPriority.NAVIGATION || ttsPriority == TtsPriority.URGENT;
                if (!bl && this.isDuplicate(string2, l)) {
                    return TtsDecision.skip("duplicate_or_similar_reply");
                }
                if (!bl && this.requestInFlight) {
                    return TtsDecision.skip("tts_request_in_flight");
                }
                if (!bl && l - this.lastAcceptedAtMillis < this.minIntervalMillis) {
                    return TtsDecision.skip("normal_tts_throttled");
                }
                this.requestInFlight = true;
                long l2 = this.estimatePlaybackMillis(string2);
                return new TtsDecision(true, "accepted", true, bl, string2, ttsPriority, l2);
            }
        }

        long estimatePlaybackMillis(String string) {
            String string2 = LingxiNavAgentApp.normalizeText(string);
            if (string2.isBlank()) {
                return this.minSilentWindowMillis;
            }
            long l = 1200L + (long)string2.length() * 260L;
            return Math.max(this.minSilentWindowMillis, Math.min(this.maxSilentWindowMillis, l));
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        void openSilentWindow(long l) {
            Object object = this.lock;
            synchronized (object) {
                this.silentUntilMillis = Math.max(this.silentUntilMillis, System.currentTimeMillis() + l);
            }
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        void finish(TtsDecision ttsDecision, boolean bl) {
            if (!ttsDecision.allowed) {
                return;
            }
            Object object = this.lock;
            synchronized (object) {
                long l;
                this.requestInFlight = false;
                if (!bl) {
                    return;
                }
                this.lastAcceptedAtMillis = l = System.currentTimeMillis();
                this.lastNormalizedText = ttsDecision.normalizedText;
                this.silentUntilMillis = Math.max(this.silentUntilMillis, l + ttsDecision.estimatedPlaybackMillis);
            }
        }

        private boolean isDuplicate(String string, long nowMillis) {
            if (this.lastNormalizedText.isBlank()) {
                return false;
            }
            if (this.lastAcceptedAtMillis <= 0L
                    || nowMillis - this.lastAcceptedAtMillis > this.maxSilentWindowMillis) {
                return false;
            }
            if (this.lastNormalizedText.equals(string)) {
                return true;
            }
            int n = Math.min(this.lastNormalizedText.length(), string.length());
            if (n >= 8 && (this.lastNormalizedText.contains(string) || string.contains(this.lastNormalizedText))) {
                return true;
            }
            return TtsController.similarity(this.lastNormalizedText, string) >= this.duplicateSimilarityThreshold;
        }

        private static double similarity(String string, String string2) {
            int n = Math.max(string.length(), string2.length());
            if (n == 0) {
                return 1.0;
            }
            return 1.0 - (double)TtsController.levenshtein(string, string2) / (double)n;
        }

        private static int levenshtein(String string, String string2) {
            int n;
            int[] nArray = new int[string2.length() + 1];
            int[] nArray2 = new int[string2.length() + 1];
            for (n = 0; n <= string2.length(); ++n) {
                nArray[n] = n;
            }
            for (n = 1; n <= string.length(); ++n) {
                nArray2[0] = n;
                for (int i = 1; i <= string2.length(); ++i) {
                    int n2 = string.charAt(n - 1) == string2.charAt(i - 1) ? 0 : 1;
                    nArray2[i] = Math.min(Math.min(nArray2[i - 1] + 1, nArray[i] + 1), nArray[i - 1] + n2);
                }
                int[] nArray3 = nArray;
                nArray = nArray2;
                nArray2 = nArray3;
            }
            return nArray[string2.length()];
        }
    }

    private record AudioResult(String asrText, String reply, TtsPriority ttsPriority, String skillAction) {
    }

    private static enum TtsPriority {
        NORMAL,
        NAVIGATION,
        URGENT;

    }

    private record RobotTtsResult(boolean requested, boolean accepted, long estimatedPlaybackMillis, String reason) {
        static RobotTtsResult skipped(String string) {
            return new RobotTtsResult(false, false, 0L, string);
        }
    }

    private record WakeGate(boolean handled, boolean accepted, String state, String reason) {
    }

    private record InteractionResult(String reply, TtsPriority ttsPriority, String skillAction) {
        private InteractionResult(String string, TtsPriority ttsPriority) {
            this(string, ttsPriority, "");
        }
    }

    private record TtsDecision(boolean allowed, String reason, boolean interrupt, boolean force, String normalizedText, TtsPriority priority, long estimatedPlaybackMillis) {
        static TtsDecision skip(String string) {
            return new TtsDecision(false, string, false, false, "", TtsPriority.NORMAL, 0L);
        }
    }
}
