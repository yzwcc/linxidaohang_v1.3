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
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class LingxiNavAgentApp {
    private static final Logger log = LoggerFactory.getLogger(LingxiNavAgentApp.class);
    private static volatile RetrieverClient RETRIEVER_CLIENT = new RetrieverClient();
    private static volatile VoiceGate VOICE_GATE;
    private static volatile WakeSessionGate WAKE_SESSION_GATE;
    private static volatile Soc2WakeEvidenceProvider SOC2_WAKE_EVIDENCE;
    private static final String FIXED_DIALOG_TTS_EVENT_ID = "normal_dialog_test";
    private static final String FIXED_DIALOG_TTS_TEXT = "\u6536\u5230\uff0c\u6211\u6765\u56de\u7b54\u4f60\u3002";
    private static final String STREAMING_REPLY_INTERRUPTED_TEXT =
            "\u521a\u624d\u7684\u56de\u7b54\u4e2d\u65ad\u4e86\uff0c\u8bf7\u518d\u95ee\u4e00\u6b21\u3002";
    private static final Map<String, AudioSession> AUDIO_2_LLM_SESSIONS = new ConcurrentHashMap<String, AudioSession>();
    private static final Map<String, AudioSession> AUDIO_2_TTS_SESSIONS = new ConcurrentHashMap<String, AudioSession>();
    private static final AtomicInteger VOICE_METADATA_LOGS = new AtomicInteger();
    private static final TtsController TTS_CONTROLLER = new TtsController(1000L, 1800L, 6500L, 0.86);
    private static final int AUDIO_WORKER_THREADS = 3;
    private static final int AUDIO_WORKER_QUEUE_CAPACITY = 3;
    private static final AtomicInteger AUDIO_TASKS_IN_FLIGHT = new AtomicInteger();
    private static final ThreadPoolExecutor AUDIO_WORKERS = new ThreadPoolExecutor(3, 3, 0L, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<Runnable>(3), runnable -> {
        Thread thread = new Thread(runnable, "lingxi-audio-worker");
        thread.setDaemon(true);
        return thread;
    }, new ThreadPoolExecutor.AbortPolicy());
    private static final int KNOWLEDGE_WORKER_THREADS = 2;
    private static final int KNOWLEDGE_WORKER_QUEUE_CAPACITY = 4;
    private static final ThreadPoolExecutor KNOWLEDGE_WORKERS = new ThreadPoolExecutor(
            KNOWLEDGE_WORKER_THREADS, KNOWLEDGE_WORKER_THREADS, 0L, TimeUnit.MILLISECONDS,
            new ArrayBlockingQueue<Runnable>(KNOWLEDGE_WORKER_QUEUE_CAPACITY), runnable -> {
        Thread thread = new Thread(runnable, "lingxi-knowledge-worker");
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
        VOICE_GATE = new VoiceGate(appConfig);
        WAKE_SESSION_GATE = new WakeSessionGate(appConfig);
        SOC2_WAKE_EVIDENCE = new Soc2WakeEvidenceProvider(appConfig);
        SOC2_WAKE_EVIDENCE.warmup();
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
        log.info("Voice gate policy: metadataRequired={}, frontHalfAngleDeg={}, minSnrDb={}, maxEchoRms={}, minVadProbability={}, minAsrConfidence={}",
                appConfig.voiceGateMetadataRequired, appConfig.voiceGateFrontHalfAngleDeg,
                appConfig.voiceGateMinSnrDb, appConfig.voiceGateMaxEchoRms,
                appConfig.voiceGateMinVadProbability, appConfig.voiceGateMinAsrConfidence);
        log.info("Wake-word policy: mode={}, textAliasFallback={}, wakeSignalMaxAgeMs={}, sessionWindowMs={}",
                appConfig.voiceGateWakeMode,
                appConfig.voiceGateAllowAddressedWake && "observe".equals(appConfig.voiceGateWakeMode),
                appConfig.voiceGateWakeSignalMaxAgeMs, appConfig.voiceGateSessionWindowMs);
        log.info("SOC2 wake evidence source: {}", SOC2_WAKE_EVIDENCE.description());
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

    private static void handleRobotOnline(String source, String agentId, AgentMeta agentMeta) {
        if (WAKE_SESSION_GATE == null) {
            log.info("Robot online({}): agentId={}", source, agentId);
            return;
        }
        WakeSessionGate.OnlineObservation observation = WAKE_SESSION_GATE.onRobotOnline(agentId, agentMeta);
        log.info("Robot online({}): agentId={}, wakeMode={}, wakeWords={}, aliasCount={}",
                source, agentId, observation.mode(), observation.reportedWakeWords(),
                observation.normalizedAliases().size());
    }

    private static void handleRobotOffline(String source, String agentId) {
        if (WAKE_SESSION_GATE != null) WAKE_SESSION_GATE.onRobotOffline(agentId);
        if (VOICE_GATE != null) VOICE_GATE.reset();
        log.info("Robot offline({}): agentId={}, wake session closed", source, agentId);
    }

    private static void handleRobotState(String source, String agentId, String eventId,
                                         String stateName, String stateValue) {
        if (WAKE_SESSION_GATE == null) {
            log.debug("Robot state ignored before wake gate initialization: source={}, stateName={}",
                    source, stateName);
            return;
        }
        WakeSessionGate.StateObservation observation = WAKE_SESSION_GATE.onState(
                agentId, eventId, stateName, stateValue);
        if (!observation.relevant()) {
            int length = stateValue == null ? 0 : stateValue.length();
            log.debug("Robot state ignored: source={}, agentId={}, eventId={}, stateName={}, payloadLength={}",
                    source, agentId, eventId, stateName, length);
            return;
        }
        log.info("Wake state: source={}, agentId={}, callbackEventId={}, payloadEventId={}, state={}, wakeupState={}, awake={}, result={}",
                source, agentId, eventId, observation.eventId(), observation.listeningState(),
                observation.wakeupState(), observation.awake(), observation.result());
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
                    LingxiNavAgentApp.handleRobotOnline("audio2llm", string, agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    LingxiNavAgentApp.handleRobotOffline("audio2llm", string);
                }

                @Override
                public void onState(String agentId, String eventId, String stateName, String stateValue) {
                    LingxiNavAgentApp.handleRobotState("audio2llm", agentId, eventId, stateName, stateValue);
                }

                @Override
                public void onRequest(String string, String string2, int n, byte[] byArray, AgentParam agentParam, Audio2LlmResponse audio2LlmResponse) {
                    LingxiNavAgentApp.logAudioRequest("Audio2Llm", string, string2, null, n, byArray);
                    LingxiNavAgentApp.handleAudio2LlmChunk(string, string2, n, byArray, agentParam, audio2LlmResponse, appConfig, navBridgeClient, doubaoArkClient, qaStore);
                }
            });
        } else {
            log.info("Audio2Llm callback disabled by lingxi.callbacks={}", (Object)appConfig.lingxiCallbacks);
        }
        if (appConfig.isCallbackEnabled("audio2tts")) {
            agentSdk.registerAudio2Tts(new Audio2TtsCallback(agentSdk){

                @Override
                public void onRobotOnline(String string, AgentMeta agentMeta) {
                    LingxiNavAgentApp.handleRobotOnline("audio2tts", string, agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    LingxiNavAgentApp.handleRobotOffline("audio2tts", string);
                }

                @Override
                public void onState(String agentId, String eventId, String stateName, String stateValue) {
                    LingxiNavAgentApp.handleRobotState("audio2tts", agentId, eventId, stateName, stateValue);
                }

                @Override
                public void onRequest(String string, String string2, String string3, int n, byte[] byArray, AgentParam agentParam, Audio2TtsResponse audio2TtsResponse) {
                    LingxiNavAgentApp.logAudioRequest("Audio2Tts", string, string2, string3, n, byArray);
                    LingxiNavAgentApp.handleAudio2TtsChunk(string, string2, string3, n, byArray, agentParam, audio2TtsResponse, appConfig, navBridgeClient, doubaoArkClient, qaStore);
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
                    LingxiNavAgentApp.handleRobotOnline("asr2llm", string, agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    LingxiNavAgentApp.handleRobotOffline("asr2llm", string);
                }

                @Override
                public void onState(String agentId, String eventId, String stateName, String stateValue) {
                    LingxiNavAgentApp.handleRobotState("asr2llm", agentId, eventId, stateName, stateValue);
                }

                /*
                 * WARNING - Removed try catching itself - possible behaviour change.
                 */
                @Override
                public void onRequest(String string, String string2, String string3, AgentParam agentParam, Asr2LlmResponse asr2LlmResponse) {
                    log.info("Asr2Llm request: agentId={}, eventId={}, text={}", string, string2, string3);
                    String string4 = LingxiNavAgentApp.newItemId();
                    AtomicBoolean atomicBoolean = new AtomicBoolean(false);
                    StreamingTtsSession streamingTts =
                            new StreamingTtsSession(string2, appConfig, navBridgeClient);
                    try {
                        if (LingxiNavAgentApp.shouldIgnoreTextCallback(
                                "Asr2Llm", string, string2, string3, agentParam, appConfig, navBridgeClient)) {
                            LingxiNavAgentApp.finishIgnoredAsr2Llm(string2, asr2LlmResponse);
                            return;
                        }
                        InteractionResult interactionResult = LingxiNavAgentApp.processTextRequest(
                                string2, string3, appConfig, navBridgeClient, doubaoArkClient, qaStore,
                                atomicBoolean, streamingTts);
                        if (LingxiNavAgentApp.shouldReturnLingxiText(appConfig)) {
                            asr2LlmResponse.onLlmItemDelta(string2, string4, interactionResult.reply);
                        } else {
                            log.info("Lingxi LLM text suppressed for local robot TTS test: eventId={}, text={}", (Object)string2, (Object)LingxiNavAgentApp.abbreviate(interactionResult.reply));
                        }
                        asr2LlmResponse.onLlmItemDone(string2, string4);
                        LingxiNavAgentApp.dispatchSkillIfNeeded(asr2LlmResponse, string2, string4, interactionResult.skillAction);
                        asr2LlmResponse.onLlmDone(string2);
                        if (!interactionResult.localTtsHandled) {
                            LingxiNavAgentApp.speakIfNeeded(
                                    appConfig, string2, interactionResult.reply,
                                    interactionResult.ttsPriority, navBridgeClient);
                        }
                    }
                    catch (Exception exception) {
                        log.error("Asr2Llm handling failed", exception);
                        streamingTts.abort();
                        if (streamingTts.hasAccepted()) {
                            LingxiNavAgentApp.speakIfNeeded(
                                    appConfig, string2, STREAMING_REPLY_INTERRUPTED_TEXT,
                                    TtsPriority.URGENT, navBridgeClient);
                        }
                        asr2LlmResponse.onError(string2, 5001, "\u5904\u7406\u5931\u8d25: " + exception.getMessage());
                    }
                    finally {
                        streamingTts.abort();
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
                    LingxiNavAgentApp.handleRobotOnline("asr2tts", string, agentMeta);
                }

                @Override
                public void onRobotOffline(String string) {
                    LingxiNavAgentApp.handleRobotOffline("asr2tts", string);
                }

                @Override
                public void onState(String agentId, String eventId, String stateName, String stateValue) {
                    LingxiNavAgentApp.handleRobotState("asr2tts", agentId, eventId, stateName, stateValue);
                }

                /*
                 * WARNING - Removed try catching itself - possible behaviour change.
                 */
                @Override
                public void onRequest(String string, String string2, String string3, AgentParam agentParam, Asr2TtsResponse asr2TtsResponse) {
                    log.info("Asr2Tts request: agentId={}, eventId={}, text={}", string, string2, string3);
                    String string4 = LingxiNavAgentApp.newItemId();
                    AtomicBoolean atomicBoolean = new AtomicBoolean(false);
                    StreamingTtsSession streamingTts =
                            new StreamingTtsSession(string2, appConfig, navBridgeClient);
                    try {
                        if (LingxiNavAgentApp.shouldIgnoreTextCallback(
                                "Asr2Tts", string, string2, string3, agentParam, appConfig, navBridgeClient)) {
                            LingxiNavAgentApp.finishIgnoredAsr2Tts(string2, asr2TtsResponse);
                            return;
                        }
                        InteractionResult interactionResult = LingxiNavAgentApp.processTextRequest(
                                string2, string3, appConfig, navBridgeClient, doubaoArkClient, qaStore,
                                atomicBoolean, streamingTts);
                        if (LingxiNavAgentApp.shouldReturnLingxiText(appConfig)) {
                            asr2TtsResponse.onLlmItemDelta(string2, string4, interactionResult.reply);
                        } else {
                            log.info("Lingxi LLM text suppressed for local robot TTS test: eventId={}, text={}", (Object)string2, (Object)LingxiNavAgentApp.abbreviate(interactionResult.reply));
                        }
                        asr2TtsResponse.onLlmItemDone(string2, string4);
                        LingxiNavAgentApp.dispatchSkillIfNeeded(asr2TtsResponse, string2, string4, interactionResult.skillAction);
                        asr2TtsResponse.onLlmDone(string2);
                        if (!interactionResult.localTtsHandled) {
                            LingxiNavAgentApp.speakIfNeeded(
                                    appConfig, string2, interactionResult.reply,
                                    interactionResult.ttsPriority, navBridgeClient);
                        }
                        asr2TtsResponse.onTtsItemDone(string2, string4);
                        asr2TtsResponse.onTtsDone(string2);
                    }
                    catch (Exception exception) {
                        log.error("Asr2Tts handling failed", exception);
                        streamingTts.abort();
                        if (streamingTts.hasAccepted()) {
                            LingxiNavAgentApp.speakIfNeeded(
                                    appConfig, string2, STREAMING_REPLY_INTERRUPTED_TEXT,
                                    TtsPriority.URGENT, navBridgeClient);
                        }
                        asr2TtsResponse.onError(string2, 5001, "\u5904\u7406\u5931\u8d25: " + exception.getMessage());
                    }
                    finally {
                        streamingTts.abort();
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

    private static AudioSession newAudioSession(String itemId, String agentId, String eventId,
                                                String source, AgentParam agentParam,
                                                NavBridgeClient navBridgeClient) {
        logVoiceMetadata(source, eventId, agentParam);
        if (VOICE_GATE != null) {
            VoiceGate.Decision voiceDecision = VOICE_GATE.admit(null, agentParam);
            if (!voiceDecision.accepted()) {
                log.info("Audio session rejected by quality gate: eventId={}, reason={}",
                        eventId, voiceDecision.reason());
                return new AudioSession(itemId, voiceDecision.reason());
            }
        }
        if (WAKE_SESSION_GATE != null) {
            WakeSessionGate.Decision wakeDecision = WAKE_SESSION_GATE.admit(
                    agentId, eventId, null, agentParam);
            if (!wakeDecision.accepted()) {
                log.info("Audio session rejected by wake gate: eventId={}, reason={}",
                        eventId, wakeDecision.reason());
                return new AudioSession(itemId, wakeDecision.reason());
            }
            if (wakeDecision.wouldReject()) {
                log.warn("Audio wake gate observe mode accepted request that enforce mode would reject: eventId={}, reason={}",
                        eventId, wakeDecision.reason());
            }
        }
        WakeGate wakeGate = LingxiNavAgentApp.checkTourWake(eventId, source, navBridgeClient);
        String ignoreReason = wakeGate.handled
                ? (wakeGate.accepted ? "" : wakeGate.reason)
                : LingxiNavAgentApp.busyInteractionIgnoreReason();
        if (!ignoreReason.isBlank()) LingxiNavAgentApp.resetVoiceGates();
        return new AudioSession(itemId, ignoreReason);
    }

    private static AudioSession newAudioSession(String itemId, String eventId,
                                                String source, AgentParam agentParam,
                                                NavBridgeClient navBridgeClient) {
        return newAudioSession(itemId, "", eventId, source, agentParam, navBridgeClient);
    }

    private static AudioSession newAudioSession(String itemId, String eventId,
                                                String source, NavBridgeClient navBridgeClient) {
        return newAudioSession(itemId, "", eventId, source, null, navBridgeClient);
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
        long submittedAtNanos = System.nanoTime();
        AUDIO_TASKS_IN_FLIGHT.incrementAndGet();
        try {
            AUDIO_WORKERS.execute(() -> {
                int n;
                long queueWaitMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - submittedAtNanos);
                log.info("{} audio task started: eventId={}, queueWaitMs={}, active={}, queued={}",
                        string, string2, queueWaitMs, AUDIO_WORKERS.getActiveCount(), AUDIO_WORKERS.getQueue().size());
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

    private static void handleAudio2LlmChunk(String agentId, String eventId, int flag, byte[] audio,
                                              AgentParam agentParam, Audio2LlmResponse response,
                                              AppConfig appConfig, NavBridgeClient navBridgeClient,
                                              DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        if (flag == 0) {
            AudioSession audioSession = LingxiNavAgentApp.newAudioSession(
                    LingxiNavAgentApp.newItemId(), agentId, eventId, "audio2llm_wake", agentParam, navBridgeClient);
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, audio);
            }
            AUDIO_2_LLM_SESSIONS.put(eventId, audioSession);
            if (audioSession.shouldIgnore()) {
                log.info("Audio2Llm event ignored: eventId={}, reason={}", eventId, audioSession.ignoreReason);
            }
            return;
        }
        if (flag == 1) {
            AudioSession audioSession = AUDIO_2_LLM_SESSIONS.computeIfAbsent(eventId,
                    ignored -> LingxiNavAgentApp.newAudioSession(
                            LingxiNavAgentApp.newItemId(), agentId, eventId,
                            "audio2llm_wake", agentParam, navBridgeClient));
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, audio);
            }
            return;
        }
        if (flag == 2) {
            String ignoreReason;
            AudioSession audioSession = AUDIO_2_LLM_SESSIONS.remove(eventId);
            if (audioSession == null) {
                audioSession = LingxiNavAgentApp.newAudioSession(
                        LingxiNavAgentApp.newItemId(), agentId, eventId,
                        "audio2llm_wake", agentParam, navBridgeClient);
            }
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, audio);
            }
            if (!(ignoreReason = LingxiNavAgentApp.completedAudioIgnoreReason(audioSession)).isBlank()) {
                log.info("Audio2Llm completed event ignored: eventId={}, reason={}", eventId, ignoreReason);
                LingxiNavAgentApp.speakLowConfidencePrompt(
                        appConfig, eventId, ignoreReason, navBridgeClient);
                LingxiNavAgentApp.finishIgnoredAudio2Llm(eventId, response);
                return;
            }
            AudioSession audioSession2 = audioSession;
            if (!LingxiNavAgentApp.submitAudioTask("Audio2Llm", eventId,
                    () -> LingxiNavAgentApp.processCompletedAudio2Llm(
                            eventId, audioSession2, response, appConfig, navBridgeClient,
                            doubaoArkClient, qaStore))) {
                LingxiNavAgentApp.finishIgnoredAudio2Llm(eventId, response);
            }
            return;
        }
        response.onError(eventId, 4001, "\u672a\u77e5 Audio2Llm flag: " + flag);
    }

    private static void handleAudio2TtsChunk(String agentId, String eventId, String itemId,
                                              int flag, byte[] audio, AgentParam agentParam,
                                              Audio2TtsResponse response, AppConfig appConfig,
                                              NavBridgeClient navBridgeClient,
                                              DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        if (flag == 0) {
            AudioSession audioSession = LingxiNavAgentApp.newAudioSession(
                    LingxiNavAgentApp.normalizeItemId(itemId), agentId, eventId,
                    "audio2tts_wake", agentParam, navBridgeClient);
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, audio);
            }
            AUDIO_2_TTS_SESSIONS.put(eventId, audioSession);
            if (audioSession.shouldIgnore()) {
                log.info("Audio2Tts event ignored: eventId={}, reason={}", eventId, audioSession.ignoreReason);
            }
            return;
        }
        if (flag == 1) {
            AudioSession audioSession = AUDIO_2_TTS_SESSIONS.computeIfAbsent(eventId,
                    ignored -> LingxiNavAgentApp.newAudioSession(
                            LingxiNavAgentApp.normalizeItemId(itemId), agentId, eventId,
                            "audio2tts_wake", agentParam, navBridgeClient));
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, audio);
            }
            return;
        }
        if (flag == 2) {
            String ignoreReason;
            AudioSession audioSession = AUDIO_2_TTS_SESSIONS.remove(eventId);
            if (audioSession == null) {
                audioSession = LingxiNavAgentApp.newAudioSession(
                        LingxiNavAgentApp.normalizeItemId(itemId), agentId, eventId,
                        "audio2tts_wake", agentParam, navBridgeClient);
            }
            if (!audioSession.shouldIgnore()) {
                LingxiNavAgentApp.appendAudio(audioSession, audio);
            }
            if (!(ignoreReason = LingxiNavAgentApp.completedAudioIgnoreReason(audioSession)).isBlank()) {
                log.info("Audio2Tts completed event ignored: eventId={}, reason={}", eventId, ignoreReason);
                LingxiNavAgentApp.speakLowConfidencePrompt(
                        appConfig, eventId, ignoreReason, navBridgeClient);
                LingxiNavAgentApp.finishIgnoredAudio2Tts(eventId, audioSession.itemId, response);
                return;
            }
            AudioSession audioSession2 = audioSession;
            if (!LingxiNavAgentApp.submitAudioTask("Audio2Tts", eventId,
                    () -> LingxiNavAgentApp.processCompletedAudio2Tts(
                            eventId, audioSession2, response, appConfig, navBridgeClient,
                            doubaoArkClient, qaStore))) {
                LingxiNavAgentApp.finishIgnoredAudio2Tts(eventId, audioSession2.itemId, response);
            }
            return;
        }
        response.onError(eventId, 4001, "\u672a\u77e5 Audio2Tts flag: " + flag);
    }

    /*
     * WARNING - Removed try catching itself - possible behaviour change.
     */
    private static void processCompletedAudio2Llm(String string, AudioSession audioSession, Audio2LlmResponse audio2LlmResponse, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) {
        long startedAtNanos = System.nanoTime();
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
            log.info("Audio2Llm response dispatch finished: eventId={}, elapsedMs={}",
                    string, TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos));
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
        long startedAtNanos = System.nanoTime();
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
            log.info("Audio2Tts response dispatch finished: eventId={}, elapsedMs={}",
                    string, TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos));
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
        long l = System.nanoTime();
        List<String> list = navBridgeClient.listPointNames();
        long l2 = System.nanoTime();
        log.info("Audio request point names loaded: eventId={}, audioBytes={}, points={}, elapsedMs={}",
                string, byArray.length, list.size(), TimeUnit.NANOSECONDS.toMillis(l2 - l));
        long modelStartedAtNanos = System.nanoTime();
        NavigationIntent navigationIntent = doubaoArkClient.parseInteractionAudio(byArray, list);
        String rawQuestion = navigationIntent.question;
        navigationIntent = LingxiNavAgentApp.verifyVoicePresetMotion(rawQuestion, navigationIntent);
        NavigationIntent stopCommand = LocalNavigationCommand.resolveStopCommand(rawQuestion);
        if (stopCommand != null) {
            navigationIntent = stopCommand;
            log.info("Local stop command matched after audio transcription: eventId={}, intent={}",
                    string, stopCommand.intent);
        }
        LocalNavigationCommand.Resolution localResolution = LocalNavigationCommand.resolveDetailed(
                rawQuestion, list, navBridgeClient.listAmbiguousPointNames());
        boolean ambiguousNavigation = localResolution.ambiguous();
        if (ambiguousNavigation) {
            navigationIntent = NavigationIntent.chatQuestion(
                    rawQuestion, LocalNavigationCommand.ambiguousPointReply());
            log.info("Ambiguous navigation alias clarified after audio transcription: eventId={}, alias={}",
                    string, localResolution.ambiguousName());
        } else if (localResolution.matched() && stopCommand == null && !navigationIntent.isPresetMotion()) {
            NavigationIntent localNavigation = NavigationIntent.navigate(
                    localResolution.targetName(), rawQuestion);
            log.info("Local navigation alias matched after audio transcription: eventId={}, target={}",
                    string, localNavigation.targetName);
            navigationIntent = localNavigation;
        }
        log.info("Audio request parsed by Doubao: eventId={}, elapsedMs={}", string,
                TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - modelStartedAtNanos));
        String string2 = LingxiNavAgentApp.describeIntent(navigationIntent);
        LingxiNavAgentApp.logIntent("audio_asr_final", string, string2, navigationIntent);
        boolean motionRelated = PresetMotionCatalog.mentionsMotionText(rawQuestion);
        boolean motionOnlyRequest = motionRelated
                && ("chat".equals(navigationIntent.intent) || navigationIntent.isPresetMotion());
        if (!motionOnlyRequest) {
            LingxiNavAgentApp.markUserInteraction(string, "audio_asr_final", navigationIntent, navBridgeClient, atomicBoolean);
        }
        InteractionResult interactionResult;
        if (ambiguousNavigation) {
            interactionResult = new InteractionResult(
                    LocalNavigationCommand.ambiguousPointReply(), TtsPriority.NORMAL);
        } else if ("chat".equals(navigationIntent.intent) && !navigationIntent.reply.isBlank()) {
            String directReply = navigationIntent.reply;
            if (LingxiNavAgentApp.mentionsForbiddenIdentity(directReply)) {
                directReply = doubaoArkClient.identityReply();
            }
            String question = navigationIntent.questionOrDefault(string2);
            if (LingxiNavAgentApp.isIdentityQuestion(question)) {
                directReply = doubaoArkClient.identityReply();
            } else if (LingxiNavAgentApp.isDateQuestion(question)) {
                directReply = LingxiNavAgentApp.dateReply();
            } else if (LingxiNavAgentApp.isTimeQuestion(question)) {
                directReply = LingxiNavAgentApp.timeReply();
            } else {
                String knowledgeAnswer = KnowledgeAnswerPolicy.shouldLookup(question)
                        ? LingxiNavAgentApp.knowledgeAnswer(string, question)
                        : null;
                if (knowledgeAnswer != null) {
                    log.info("Audio knowledge base answer selected: eventId={}", string);
                }
                directReply = KnowledgeAnswerPolicy.chooseReply(question, directReply, knowledgeAnswer);
            }
            log.info("Audio chat answered: eventId={}, grounded={}",
                    string, KnowledgeAnswerPolicy.requiresGrounding(question));
            interactionResult = new InteractionResult(directReply, TtsPriority.NORMAL);
        } else {
            interactionResult = LingxiNavAgentApp.handleIntent(string, string2, navigationIntent, navBridgeClient, doubaoArkClient, qaStore, true);
        }
        if (interactionResult.markInteractionAfter) {
            LingxiNavAgentApp.markUserInteraction(string, "audio_asr_final", navigationIntent, navBridgeClient, atomicBoolean);
        }
        return new AudioResult(string2, interactionResult.reply, interactionResult.ttsPriority, interactionResult.skillAction);
    }

    private static InteractionResult processTextRequest(String string, String string2, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore, AtomicBoolean atomicBoolean) throws Exception {
        return LingxiNavAgentApp.processTextRequest(
                string, string2, appConfig, navBridgeClient, doubaoArkClient, qaStore,
                atomicBoolean, null);
    }

    static NavigationIntent parseTextInteraction(
            String eventId, String text, List<String> pointNames,
            DoubaoArkClient doubaoArkClient, StreamingTtsSession streamingTts) throws Exception {
        if (streamingTts == null || !streamingTts.isEligibleForChat(text)) {
            return doubaoArkClient.parseInteraction(text, pointNames);
        }
        try {
            return doubaoArkClient.parseInteractionStreaming(text, pointNames, streamingTts::accept);
        } catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                streamingTts.abort();
                Thread.currentThread().interrupt();
                throw exception;
            }
            if (streamingTts.hasAccepted()) {
                throw exception;
            }
            streamingTts.abort();
            log.warn(
                    "Doubao streaming failed before local speech; retrying non-streaming: eventId={}, error={}",
                    eventId, exception.getMessage());
            return doubaoArkClient.parseInteraction(text, pointNames);
        }
    }

    private static InteractionResult processTextRequest(
            String string, String string2, AppConfig appConfig, NavBridgeClient navBridgeClient,
            DoubaoArkClient doubaoArkClient, QaStore qaStore, AtomicBoolean atomicBoolean,
            StreamingTtsSession streamingTts) throws Exception {
        NavigationIntent stopCommand = LocalNavigationCommand.resolveStopCommand(string2);
        if (stopCommand != null) {
            log.info("Local stop command matched before intent model: eventId={}, intent={}",
                    string, stopCommand.intent);
            LingxiNavAgentApp.markUserInteraction(
                    string, "text_asr_final", stopCommand, navBridgeClient, atomicBoolean);
            return LingxiNavAgentApp.handleIntent(
                    string, string2, stopCommand, navBridgeClient, doubaoArkClient, qaStore, true);
        }
        PresetMotionCatalog.Selection voiceMotion = PresetMotionCatalog.resolveVoiceCommand(string2);
        if (voiceMotion == null) {
            String quickReply = KnowledgeAnswerPolicy.quickReply(string2, appConfig.robotDisplayName);
            if (quickReply != null) {
                LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", NavigationIntent.chat(string2), navBridgeClient, atomicBoolean);
                return new InteractionResult(quickReply, TtsPriority.NORMAL);
            }
            if (LingxiNavAgentApp.isIdentityQuestion(string2)) {
                LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", NavigationIntent.chat(string2), navBridgeClient, atomicBoolean);
                return new InteractionResult(doubaoArkClient.identityReply(), TtsPriority.NORMAL);
            }
            if (LingxiNavAgentApp.isDateQuestion(string2)) {
                LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", NavigationIntent.chat(string2), navBridgeClient, atomicBoolean);
                return new InteractionResult(LingxiNavAgentApp.dateReply(), TtsPriority.NORMAL);
            }
            if (LingxiNavAgentApp.isTimeQuestion(string2)) {
                LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", NavigationIntent.chat(string2), navBridgeClient, atomicBoolean);
                return new InteractionResult(LingxiNavAgentApp.timeReply(), TtsPriority.NORMAL);
            }
        }
        NavigationIntent navigationIntent;
        CompletableFuture<String> pendingKnowledgeLookup = null;
        boolean knowledgeLookupRequested = false;
        if (voiceMotion != null) {
            navigationIntent = NavigationIntent.presetMotion(
                    voiceMotion.motion().name(), voiceMotion.area(), string2);
        } else {
            navigationIntent = LingxiNavAgentApp.taskControlIntentFromText(string2);
            if (navigationIntent == null) {
                List<String> list = navBridgeClient.listPointNames();
                LocalNavigationCommand.Resolution localResolution =
                        LocalNavigationCommand.resolveDetailed(
                                string2, list, navBridgeClient.listAmbiguousPointNames());
                if (localResolution.ambiguous()) {
                    NavigationIntent clarification = NavigationIntent.chatQuestion(
                            string2, LocalNavigationCommand.ambiguousPointReply());
                    log.info("Ambiguous navigation alias clarified before intent model: eventId={}, alias={}",
                            string, localResolution.ambiguousName());
                    LingxiNavAgentApp.markUserInteraction(
                            string, "text_asr_final", clarification, navBridgeClient, atomicBoolean);
                    return new InteractionResult(
                            LocalNavigationCommand.ambiguousPointReply(), TtsPriority.NORMAL);
                } else if (localResolution.matched()) {
                    navigationIntent = NavigationIntent.navigate(
                            localResolution.targetName(), string2);
                    log.info("Local navigation command matched: eventId={}, target={}",
                            string, navigationIntent.targetName);
                } else {
                    knowledgeLookupRequested = KnowledgeAnswerPolicy.shouldLookup(string2);
                    if (knowledgeLookupRequested) {
                        pendingKnowledgeLookup =
                                LingxiNavAgentApp.startKnowledgeLookup(string, string2);
                    }
                    navigationIntent = LingxiNavAgentApp.parseTextInteraction(
                            string, string2, list, doubaoArkClient, streamingTts);
                }
            } else {
                log.info("Local tour control rule matched: eventId={}, intent={}, text={}", string, navigationIntent.intent, LingxiNavAgentApp.abbreviate(string2));
            }
            navigationIntent = LingxiNavAgentApp.verifyVoicePresetMotion(string2, navigationIntent);
        }
        LingxiNavAgentApp.logIntent("text_asr_final", string, string2, navigationIntent);
        boolean motionRelated = PresetMotionCatalog.mentionsMotionText(string2);
        boolean motionOnlyRequest = motionRelated
                && ("chat".equals(navigationIntent.intent) || navigationIntent.isPresetMotion());
        if (!motionOnlyRequest) {
            LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", navigationIntent, navBridgeClient, atomicBoolean);
        }
        InteractionResult interactionResult = LingxiNavAgentApp.handleIntent(
                string, string2, navigationIntent, navBridgeClient, doubaoArkClient, qaStore, true,
                pendingKnowledgeLookup, knowledgeLookupRequested);
        if (interactionResult.markInteractionAfter) {
            LingxiNavAgentApp.markUserInteraction(string, "text_asr_final", navigationIntent, navBridgeClient, atomicBoolean);
        }
        if (streamingTts != null) {
            boolean localTtsHandled =
                    streamingTts.complete(navigationIntent.reply, interactionResult.reply);
            if (localTtsHandled) {
                interactionResult = interactionResult.withLocalTtsHandled();
            }
        }
        return interactionResult;
    }

    private static void handleLiveDanmu(LiveDanmuServer.IncomingDanmu incomingDanmu, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) throws Exception {
        log.info("Live danmu accepted: eventId={}, msgId={}, userId={}, nickname={}, text={}", incomingDanmu.eventId(), incomingDanmu.msgId(), incomingDanmu.userId(), incomingDanmu.nickname(), LingxiNavAgentApp.abbreviate(incomingDanmu.text()));
        InteractionResult interactionResult = LingxiNavAgentApp.processLiveTextRequest(incomingDanmu.eventId(), incomingDanmu.text(), appConfig, navBridgeClient, doubaoArkClient, qaStore);
        RobotTtsResult robotTtsResult = LingxiNavAgentApp.speakIfNeeded(appConfig, incomingDanmu.eventId(), interactionResult.reply, interactionResult.ttsPriority, navBridgeClient);
        log.info("Live danmu replied: eventId={}, msgId={}, ttsRequested={}, ttsAccepted={}, text={}", incomingDanmu.eventId(), incomingDanmu.msgId(), robotTtsResult.requested, robotTtsResult.accepted, LingxiNavAgentApp.abbreviate(interactionResult.reply));
    }

    private static InteractionResult processLiveTextRequest(String string, String string2, AppConfig appConfig, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore) throws Exception {
        PresetMotionCatalog.Selection voiceMotion = PresetMotionCatalog.resolveVoiceCommand(string2);
        if (voiceMotion != null) {
            return new InteractionResult("直播弹幕暂不支持触发机器人预置动作。", TtsPriority.NORMAL);
        }
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
        if (navigationIntent.isPresetMotion()) {
            return new InteractionResult("直播弹幕暂不支持触发机器人预置动作。", TtsPriority.NORMAL);
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

    private static NavigationIntent verifyVoicePresetMotion(String rawText, NavigationIntent intent) {
        PresetMotionCatalog.Selection selection = PresetMotionCatalog.resolveVoiceCommand(rawText);
        if (selection != null) {
            return NavigationIntent.presetMotion(
                    selection.motion().name(), selection.area(), rawText);
        }
        if (intent.isPresetMotion()) {
            return NavigationIntent.chatQuestion(rawText, intent.reply);
        }
        return intent;
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
        return LingxiNavAgentApp.speakIfNeeded(
                appConfig, string, string2, ttsPriority, navBridgeClient, null);
    }

    private static RobotTtsResult speakIfNeeded(
            AppConfig appConfig, String eventId, String text, TtsPriority priority,
            NavBridgeClient navBridgeClient, TtsDecision replacedDecision) {
        String string = eventId;
        String string2 = text;
        TtsPriority ttsPriority = priority;
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
        TtsDecision ttsDecision = replacedDecision == null
                ? TTS_CONTROLLER.tryAcquire(string2, ttsPriority)
                : TTS_CONTROLLER.tryAcquireReplacement(
                        string2, TtsPriority.URGENT, replacedDecision);
        if (!ttsDecision.allowed) {
            log.info("Robot TTS skipped: eventId={}, priority={}, reason={}, text={}", new Object[]{string, ttsPriority, ttsDecision.reason, LingxiNavAgentApp.abbreviate(string2)});
            return RobotTtsResult.skipped(ttsDecision.reason);
        }
        boolean bl = false;
        String dispatchReason = ttsDecision.reason;
        long delayStartedAtNanos = System.nanoTime();
        long bridgeStartedAtNanos = 0L;
        try {
            LingxiNavAgentApp.delayBeforeRobotTts(appConfig);
            if (!TTS_CONTROLLER.isCurrent(ttsDecision)) {
                dispatchReason = "tts_decision_superseded";
                log.info("Robot TTS dispatch skipped after preemption: eventId={}, text={}",
                        string, LingxiNavAgentApp.abbreviate(string2));
            } else {
                bridgeStartedAtNanos = System.nanoTime();
                // Lingxi submits an L6 placeholder for the same audio turn. Keep every
                // robot-local reply at L7 so the placeholder cannot preempt playback.
                JSONObject jSONObject = navBridgeClient.speak(
                        string, string2, ttsDecision.interrupt, true);
                bl = jSONObject.getBooleanValue("accepted");
                if (!bl) {
                    log.warn("Robot TTS was not accepted: {}", (Object)jSONObject);
                }
            }
        }
        catch (Exception exception) {
            log.warn("Robot TTS request failed: {}", (Object)exception.getMessage());
        }
        finally {
            TTS_CONTROLLER.finish(ttsDecision, bl);
            long now = System.nanoTime();
            long delayMs = bridgeStartedAtNanos == 0L
                    ? TimeUnit.NANOSECONDS.toMillis(now - delayStartedAtNanos)
                    : TimeUnit.NANOSECONDS.toMillis(bridgeStartedAtNanos - delayStartedAtNanos);
            long bridgeMs = bridgeStartedAtNanos == 0L
                    ? 0L
                    : TimeUnit.NANOSECONDS.toMillis(now - bridgeStartedAtNanos);
            log.info("Robot TTS dispatch timing: eventId={}, delayMs={}, bridgeMs={}, accepted={}",
                    string, delayMs, bridgeMs, bl);
        }
        return new RobotTtsResult(
                true, bl, bl ? ttsDecision.estimatedPlaybackMillis : 0L, dispatchReason);
    }

    private static void delayBeforeRobotTts(AppConfig appConfig) throws InterruptedException {
        if (appConfig.ttsDelayBeforeSpeakMs > 0L) {
            Thread.sleep(appConfig.ttsDelayBeforeSpeakMs);
        }
    }

    private static InteractionResult handleIntent(String string, String string2, NavigationIntent navigationIntent, NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore, boolean bl) throws Exception {
        return LingxiNavAgentApp.handleIntent(
                string, string2, navigationIntent, navBridgeClient, doubaoArkClient, qaStore, bl,
                null, false);
    }

    private static InteractionResult handleIntent(
            String string, String string2, NavigationIntent navigationIntent,
            NavBridgeClient navBridgeClient, DoubaoArkClient doubaoArkClient, QaStore qaStore,
            boolean bl, CompletableFuture<String> pendingKnowledgeLookup,
            boolean knowledgeLookupRequested) throws Exception {
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
            String string4 = navigationRejectionReply(
                    jSONObject, "\u5bfc\u822a\u672a\u80fd\u542f\u52a8\uff0c\u8bf7\u68c0\u67e5\u5bfc\u822a\u72b6\u6001\u548c\u5b89\u5168\u8bbe\u7f6e");
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
            String string6 = navigationRejectionReply(
                    jSONObject, "\u8bb2\u89e3\u4efb\u52a1\u672a\u80fd\u542f\u52a8\uff0c\u8bf7\u68c0\u67e5\u9996\u4e2a\u70b9\u4f4d\u548c\u5bfc\u822a\u5b89\u5168\u8bbe\u7f6e");
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
        if (navigationIntent.isPresetMotion()) {
            PresetMotionCatalog.Selection selection = PresetMotionCatalog.resolveVoiceCommand(string2);
            if (selection == null
                    || !selection.motion().name().equals(navigationIntent.targetName)
                    || !selection.area().equals(navigationIntent.motionArea)) {
                return new InteractionResult("动作口令未通过白名单核验。", TtsPriority.NORMAL);
            }
            JSONObject result = navBridgeClient.presetMotion(
                    string, selection.motion().name(), selection.area(), string2);
            boolean accepted = result.getBooleanValue("accepted");
            boolean submitted = result.getBooleanValue("submitted");
            if (accepted && submitted) {
                String message = result.getString("message");
                return new InteractionResult(
                        message == null || message.isBlank() ? "动作请求已提交" : message,
                        TtsPriority.NORMAL, "", true);
            }
            if (accepted) {
                return new InteractionResult("动作请求状态不明确，未能确认提交。", TtsPriority.NORMAL);
            }
            String message = result.getString("message");
            return new InteractionResult(
                    message == null || message.isBlank() ? "动作请求未受理。" : message,
                    TtsPriority.NORMAL);
        }
        String string9 = navigationIntent.questionOrDefault(string2);
        if (LingxiNavAgentApp.isDateQuestion(string9)) {
            return new InteractionResult(LingxiNavAgentApp.dateReply(), TtsPriority.NORMAL);
        }
        if (LingxiNavAgentApp.isTimeQuestion(string9)) {
            return new InteractionResult(LingxiNavAgentApp.timeReply(), TtsPriority.NORMAL);
        }
        if (LingxiNavAgentApp.isIdentityQuestion(string9)) {
            return new InteractionResult(doubaoArkClient.identityReply(), TtsPriority.NORMAL);
        }
        String knowledgeAnswer = KnowledgeAnswerPolicy.shouldLookup(string9)
                ? (knowledgeLookupRequested
                        ? LingxiNavAgentApp.finishKnowledgeLookup(
                                string, string9, pendingKnowledgeLookup)
                        : LingxiNavAgentApp.knowledgeAnswer(string, string9))
                : null;
        if (knowledgeAnswer != null) {
            log.info("Knowledge base answer selected: eventId={}, question={}", string, LingxiNavAgentApp.abbreviate(string9));
            return new InteractionResult(knowledgeAnswer, TtsPriority.NORMAL);
        }
        if (KnowledgeAnswerPolicy.requiresGrounding(string9)) {
            log.info("Grounded answer unavailable; refusing unsupported reply: eventId={}, question={}",
                    string, LingxiNavAgentApp.abbreviate(string9));
            return new InteractionResult(KnowledgeAnswerPolicy.GROUNDED_FALLBACK, TtsPriority.NORMAL);
        }
        String modelReply = navigationIntent.reply;
        if (modelReply.isBlank()) {
            modelReply = doubaoArkClient.chat(string9);
        }
        log.info("Text chat answer source: eventId={}, source={}",
                string, navigationIntent.reply.isBlank() ? "fallback_chat" : "intent_reply");
        object = KnowledgeAnswerPolicy.chooseReply(string9, modelReply, null);
        if (LingxiNavAgentApp.mentionsForbiddenIdentity((String)object)) {
            object = doubaoArkClient.identityReply();
        }
        return new InteractionResult((String)object, TtsPriority.NORMAL);
    }

    private static String navigationRejectionReply(JSONObject response, String fallback) {
        String message = response.getString("message");
        if (message != null && !message.isBlank()) {
            return message.trim();
        }
        String reason = response.getString("reason");
        if (reason != null && !reason.isBlank()) {
            return reason.trim();
        }
        if ("path_safety_rejected".equals(response.getString("status"))) {
            return "\u5168\u5c40\u8def\u5f84\u5b89\u5168\u68c0\u67e5\u672a\u901a\u8fc7\uff0c\u5df2\u62d2\u7edd\u4e0b\u53d1\u5bfc\u822a\uff0c\u8bf7\u68c0\u67e5\u5730\u56fe\u4e2d\u7684\u8def\u5f84\u5b89\u5168\u8bbe\u7f6e";
        }
        return fallback;
    }

    private static String knowledgeAnswer(String eventId, String question) {
        long startedAtNanos = System.nanoTime();
        try {
            String answer = RETRIEVER_CLIENT.findAnswer(question);
            log.info("Knowledge lookup finished: eventId={}, elapsedMs={}, matched={}",
                    eventId, TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos),
                    answer != null);
            return answer;
        }
        catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("Knowledge base lookup failed: eventId={}, elapsedMs={}, question={}, error={}",
                    eventId, TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos),
                    LingxiNavAgentApp.abbreviate(question), exception.getMessage());
            return null;
        }
    }

    private static CompletableFuture<String> startKnowledgeLookup(String eventId, String question) {
        try {
            return CompletableFuture.supplyAsync(
                    () -> LingxiNavAgentApp.knowledgeAnswer(eventId, question),
                    KNOWLEDGE_WORKERS);
        } catch (RejectedExecutionException exception) {
            log.warn("Knowledge worker queue full; retrieval will run serially after intent parsing: eventId={}",
                    eventId);
            return null;
        }
    }

    private static String finishKnowledgeLookup(
            String eventId, String question, CompletableFuture<String> pendingLookup) {
        if (pendingLookup == null) {
            return LingxiNavAgentApp.knowledgeAnswer(eventId, question);
        }
        try {
            return pendingLookup.get();
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            log.warn("Knowledge lookup wait interrupted: eventId={}", eventId);
            return null;
        } catch (ExecutionException exception) {
            log.warn("Knowledge lookup task failed: eventId={}, error={}",
                    eventId, exception.getCause() == null
                            ? exception.getMessage() : exception.getCause().getMessage());
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
        return string2.contains("\u4eca\u5929\u661f\u671f\u51e0") || string2.contains("\u4eca\u5929\u5468\u51e0") || string2.contains("\u4eca\u5929\u793c\u62dc\u51e0") || string2.contains("\u4eca\u5929\u51e0\u53f7") || string2.contains("\u4eca\u5929\u65e5\u671f") || string2.contains("\u73b0\u5728\u661f\u671f\u51e0") || string2.contains("\u73b0\u5728\u5468\u51e0") || string2.contains("\u73b0\u5728\u51e0\u53f7") || string2.contains("\u73b0\u5728\u65e5\u671f");
    }

    private static boolean isTimeQuestion(String string) {
        if (string == null) {
            return false;
        }
        String normalized = string.replaceAll("\\s+", "");
        return normalized.contains("\u73b0\u5728\u51e0\u70b9")
                || normalized.contains("\u73b0\u5728\u51e0\u70b9\u949f")
                || normalized.contains("\u5f53\u524d\u65f6\u95f4")
                || normalized.contains("\u73b0\u5728\u65f6\u95f4")
                || normalized.contains("\u51e0\u70b9\u4e86");
    }

    private static String dateReply() {
        LocalDate localDate = LocalDate.now(ZoneId.of("Asia/Shanghai"));
        String[] stringArray = new String[]{"\u4e00", "\u4e8c", "\u4e09", "\u56db", "\u4e94", "\u516d", "\u65e5"};
        String string = stringArray[localDate.getDayOfWeek().getValue() - 1];
        return "\u4eca\u5929\u662f" + localDate.format(DateTimeFormatter.ofPattern("yyyy\u5e74M\u6708d\u65e5")) + "\uff0c\u661f\u671f" + string + "\u3002";
    }

    private static String timeReply() {
        return "\u73b0\u5728\u662f" + LocalTime.now(ZoneId.of("Asia/Shanghai"))
                .format(DateTimeFormatter.ofPattern("HH:mm")) + "\u3002";
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
        if (navigationIntent.isPresetMotion()) {
            return navigationIntent.questionOrDefault("预置动作");
        }
        return navigationIntent.questionOrDefault("\u666e\u901a\u95ee\u7b54");
    }

    private static String normalizeItemId(String string) {
        return string == null || string.isBlank() ? LingxiNavAgentApp.newItemId() : string;
    }

    private static String newItemId() {
        return "item_" + UUID.randomUUID().toString().replace("-", "");
    }

    private static boolean shouldIgnoreTextCallback(String source, String eventId, String text,
                                                    NavBridgeClient navBridgeClient) {
        return shouldIgnoreTextCallback(source, "", eventId, text, null, navBridgeClient);
    }

    private static boolean shouldIgnoreTextCallback(String source, String eventId, String text,
                                                    AgentParam agentParam, NavBridgeClient navBridgeClient) {
        return shouldIgnoreTextCallback(source, "", eventId, text, agentParam, navBridgeClient);
    }

    private static boolean shouldIgnoreTextCallback(String source, String agentId, String eventId,
                                                    String text, AgentParam agentParam,
                                                    NavBridgeClient navBridgeClient) {
        return shouldIgnoreTextCallback(
                source, agentId, eventId, text, agentParam, null, navBridgeClient);
    }

    private static boolean shouldIgnoreTextCallback(
            String source, String agentId, String eventId, String text, AgentParam agentParam,
            AppConfig appConfig, NavBridgeClient navBridgeClient) {
        logVoiceMetadata(source, eventId, agentParam);
        if (LingxiNavAgentApp.looksLikeUrgentStop(text)) {
            LingxiNavAgentApp.resetVoiceGates();
            log.info("{} urgent stop bypasses voice rejection: eventId={}, text={}",
                    source, eventId, LingxiNavAgentApp.abbreviate(text));
            return false;
        }
        if (SpeechInputPolicy.isFiller(text)) {
            log.info("Text ignored: eventId={}, reason=filler", eventId);
            return true;
        }
        if (TTS_CONTROLLER.isInputEcho(text)) {
            log.info("Text ignored: eventId={}, reason=playback_echo", eventId);
            return true;
        }
        boolean playbackUnaddressed = TTS_CONTROLLER.isSilentWindowActive()
                && !SpeechInputPolicy.isAddressed(text);
        if (VOICE_GATE != null) {
            VoiceGate.Decision voiceDecision = VOICE_GATE.admit(text, agentParam);
            if (!voiceDecision.accepted()) {
                log.info("Text ignored by quality gate: eventId={}, reason={}",
                        eventId, voiceDecision.reason());
                LingxiNavAgentApp.speakLowConfidencePrompt(
                        appConfig, eventId, voiceDecision.reason(), navBridgeClient);
                return true;
            }
        }

        boolean freshWake = false;
        if (WAKE_SESSION_GATE != null) {
            WakeSessionGate.Decision wakeDecision = WAKE_SESSION_GATE.admit(
                    agentId, eventId, text, agentParam);
            if (!wakeDecision.accepted() && shouldProbeSoc2Wake(wakeDecision)
                    && SOC2_WAKE_EVIDENCE != null) {
                Soc2WakeEvidenceProvider.Evidence evidence = SOC2_WAKE_EVIDENCE.find(eventId);
                if (evidence.found()) {
                    wakeDecision = WAKE_SESSION_GATE.admitVerifiedSoc2Wake(
                            agentId, eventId, evidence.ageMs());
                    log.info("SOC2 wake evidence matched: agentId={}, eventId={}, ageMs={}, accepted={}, reason={}",
                            agentId, eventId, evidence.ageMs(),
                            wakeDecision.accepted(), wakeDecision.reason());
                } else {
                    log.info("SOC2 wake evidence unavailable: agentId={}, eventId={}, reason={}",
                            agentId, eventId, evidence.reason());
                }
            }
            if (!wakeDecision.accepted()) {
                log.info("Text ignored by wake gate: eventId={}, reason={}",
                        eventId, wakeDecision.reason());
                return true;
            }
            freshWake = switch (wakeDecision.reason()) {
                case "metadata_wake", "state_wake_event_match", "state_wake_recent",
                        "soc2_log_wake_verified" -> true;
                default -> false;
            };
            if (wakeDecision.wouldReject()) {
                log.warn("Wake gate observe mode accepted request that enforce mode would reject: eventId={}, reason={}",
                        eventId, wakeDecision.reason());
            }
        }
        if (playbackUnaddressed && !freshWake) {
            LingxiNavAgentApp.resetVoiceGates();
            log.info("Text ignored: eventId={}, reason=playback_not_addressed", eventId);
            return true;
        }

        WakeGate wakeGate = LingxiNavAgentApp.checkTourWake(
                eventId, source.toLowerCase() + "_wake", navBridgeClient);
        if (wakeGate.handled) {
            if (wakeGate.accepted) return false;
            log.info("{} ignored during protected tour stage: eventId={}, reason={}, state={}, text={}",
                    source, eventId, wakeGate.reason, wakeGate.state,
                    LingxiNavAgentApp.abbreviate(text));
            LingxiNavAgentApp.resetVoiceGates();
            return true;
        }
        String busyReason = LingxiNavAgentApp.busyInteractionIgnoreReason();
        if (busyReason.isBlank()) return false;
        log.info("{} ignored during busy interaction: eventId={}, reason={}, text={}",
                source, eventId, busyReason, LingxiNavAgentApp.abbreviate(text));
        LingxiNavAgentApp.resetVoiceGates();
        return true;
    }

    private static boolean shouldProbeSoc2Wake(WakeSessionGate.Decision decision) {
        return "wake_required_or_session_expired".equals(decision.reason())
                || "text_wake_fallback_disabled".equals(decision.reason());
    }

    private static void resetVoiceGates() {
        if (VOICE_GATE != null) VOICE_GATE.reset();
        if (WAKE_SESSION_GATE != null) WAKE_SESSION_GATE.reset();
    }

    private static void logVoiceMetadata(String source, String eventId, AgentParam agentParam) {
        if (VOICE_METADATA_LOGS.getAndIncrement() >= 128) return;
        log.info("Voice metadata probe: source={}, eventId={}, {}",
                source, eventId, VoiceMetadataDiagnostics.summarize(agentParam));
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
        return LocalNavigationCommand.resolveStopCommand(string) != null;
    }

    private static void speakLowConfidencePrompt(
            AppConfig appConfig, String eventId, String reason, NavBridgeClient navBridgeClient) {
        if (!"low_asr_confidence".equals(reason)
                || appConfig == null
                || navBridgeClient == null
                || !"robot".equals(appConfig.ttsMode)) {
            return;
        }
        RobotTtsResult result = LingxiNavAgentApp.speakIfNeeded(
                appConfig, eventId, "刚才没有听清，请再说一遍。", TtsPriority.NORMAL, navBridgeClient);
        log.info("Low-confidence ASR clarification dispatched: eventId={}, requested={}, accepted={}, reason={}",
                eventId, result.requested, result.accepted, result.reason);
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

    static final class StreamingTtsSession {
        private final String eventId;
        private final AppConfig appConfig;
        private final NavBridgeClient navBridgeClient;
        private final String streamId = "lingxi-chat-" + UUID.randomUUID();
        private final SpeechSegmenter segmenter = new SpeechSegmenter();
        private final List<String> pendingSegments = new ArrayList<>();
        private final StringBuilder observedText = new StringBuilder();
        private final StringBuilder acceptedText = new StringBuilder();
        private final AtomicBoolean cancelled = new AtomicBoolean();
        private final AtomicBoolean preempted = new AtomicBoolean();
        private final AtomicBoolean bridgeStreamClosed = new AtomicBoolean();
        private TtsDecision decision;
        private int nextSequence;
        private volatile boolean accepted;
        private volatile boolean disabled;
        private volatile boolean closed;
        private volatile boolean attempted;

        StreamingTtsSession(String eventId, AppConfig appConfig, NavBridgeClient navBridgeClient) {
            this.eventId = eventId;
            this.appConfig = appConfig;
            this.navBridgeClient = navBridgeClient;
        }

        boolean isEligibleForChat(String text) {
            return !this.closed
                    && this.appConfig.doubaoStreamingReplies
                    && "robot".equals(this.appConfig.ttsMode)
                    && !this.appConfig.ttsFixedDialogTest
                    && text != null
                    && !text.isBlank()
                    && !KnowledgeAnswerPolicy.shouldLookup(text)
                    && !KnowledgeAnswerPolicy.requiresGrounding(text)
                    && !LingxiNavAgentApp.isIdentityQuestion(text)
                    && !LingxiNavAgentApp.isDateQuestion(text)
                    && !LingxiNavAgentApp.isTimeQuestion(text)
                    && LocalNavigationCommand.resolveStopCommand(text) == null
                    && LingxiNavAgentApp.taskControlIntentFromText(text) == null
                    && PresetMotionCatalog.resolveVoiceCommand(text) == null
                    && !PresetMotionCatalog.mentionsMotionText(text)
                    && LingxiNavAgentApp.localEntertainmentIntentFromRequest(
                            text, NavigationIntent.chat(text)) == null;
        }

        void accept(String delta) {
            if (this.closed || this.cancelled.get() || this.disabled
                    || delta == null || delta.isEmpty()) {
                return;
            }
            this.attempted = true;
            this.observedText.append(delta);
            for (String segment : this.segmenter.append(delta)) {
                if (!this.accepted) {
                    if (!this.submit(segment, true)) {
                        this.disabled = true;
                        this.segmenter.cancel();
                        this.pendingSegments.clear();
                        this.closeBridgeStream();
                        return;
                    }
                } else {
                    this.pendingSegments.add(segment);
                }
            }
        }

        boolean complete(String modelReply, String finalReply) {
            if (this.preempted.get()) {
                this.segmenter.cancel();
                this.pendingSegments.clear();
                this.closeBridgeStream();
                this.closed = true;
                return true;
            }
            if (this.closed || !this.attempted || this.disabled) {
                this.segmenter.cancel();
                this.pendingSegments.clear();
                if (!this.closed && !this.cancelled.get() && this.accepted) {
                    return this.replaceWithFinalReply(finalReply);
                }
                this.closeBridgeStream();
                if (this.decision != null) {
                    TTS_CONTROLLER.finishStreaming(
                            this.decision, this.accepted, this.acceptedText.toString());
                    this.decision = null;
                }
                this.closed = true;
                return false;
            }
            for (String segment : this.segmenter.finish()) {
                if (!this.accepted) {
                    if (!this.submit(segment, true)) {
                        this.segmenter.cancel();
                        this.pendingSegments.clear();
                        this.closeBridgeStream();
                        this.closed = true;
                        return false;
                    }
                } else {
                    this.pendingSegments.add(segment);
                }
            }
            if (!this.accepted) {
                this.closeBridgeStream();
                this.closed = true;
                return false;
            }
            if (!this.sameText(modelReply, this.observedText.toString())
                    || !this.sameText(finalReply, modelReply)) {
                return this.replaceWithFinalReply(finalReply);
            }
            for (String segment : this.pendingSegments) {
                if (!this.submit(segment, false)) {
                    return this.replaceWithFinalReply(finalReply);
                }
            }
            this.pendingSegments.clear();
            this.closeBridgeStream();
            TTS_CONTROLLER.finishStreaming(this.decision, true, finalReply);
            this.decision = null;
            this.closed = true;
            return true;
        }

        boolean hasAccepted() {
            return this.accepted;
        }

        void abort() {
            if (this.closed) {
                return;
            }
            this.cancelled.set(true);
            this.segmenter.cancel();
            this.pendingSegments.clear();
            this.closeBridgeStream();
            if (this.decision != null) {
                TTS_CONTROLLER.finishStreaming(
                        this.decision, this.accepted, this.acceptedText.toString());
                this.decision = null;
            }
            this.closed = true;
        }

        void cancelAsSuperseded() {
            if (!this.preempted.compareAndSet(false, true)) {
                return;
            }
            this.cancelled.set(true);
            this.closed = true;
            this.closeBridgeStream();
        }

        private boolean submit(String text, boolean first) {
            if (text == null || text.isBlank()) {
                return true;
            }
            if (this.cancelled.get() || this.preempted.get()) {
                return false;
            }
            if (first) {
                this.decision = TTS_CONTROLLER.tryAcquireStreaming(text, this);
                if (!this.decision.allowed) {
                    this.decision = null;
                    this.disabled = true;
                    return false;
                }
                try {
                    LingxiNavAgentApp.delayBeforeRobotTts(this.appConfig);
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    TTS_CONTROLLER.finishStreaming(this.decision, false, "");
                    this.decision = null;
                    this.disabled = true;
                    return false;
                }
            }
            if (!this.ownsSpeech()) {
                this.cancelAsSuperseded();
                return false;
            }
            try {
                JSONObject response = this.navBridgeClient.speakStreamSegment(
                        this.eventId, this.streamId, this.nextSequence, text, first);
                if (!this.ownsSpeech()) {
                    this.cancelAsSuperseded();
                    return false;
                }
                if (response == null || !response.getBooleanValue("accepted")) {
                    log.warn(
                            "Robot streaming TTS segment was not accepted: eventId={}, sequence={}, response={}",
                            this.eventId, this.nextSequence, response);
                    if (response != null
                            && "stream_preempted".equals(response.getString("status"))) {
                        this.cancelAsSuperseded();
                        return false;
                    }
                    if (first && this.decision != null) {
                        TTS_CONTROLLER.finishStreaming(this.decision, false, "");
                        this.decision = null;
                        this.disabled = true;
                    }
                    return false;
                }
                this.accepted = true;
                this.nextSequence++;
                this.acceptedText.append(text);
                TTS_CONTROLLER.recordStreamingProgress(
                        this.decision, this.acceptedText.toString());
                log.info(
                        "Robot streaming TTS segment accepted: eventId={}, sequence={}, chars={}",
                        this.eventId, this.nextSequence - 1, text.length());
                return true;
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                log.warn(
                        "Robot streaming TTS interrupted: eventId={}, sequence={}",
                        this.eventId, this.nextSequence);
                this.cancelAsSuperseded();
                return false;
            } catch (Exception exception) {
                log.warn(
                        "Robot streaming TTS failed: eventId={}, sequence={}, error={}",
                        this.eventId, this.nextSequence, exception.getMessage());
                if (!this.ownsSpeech()) {
                    this.cancelAsSuperseded();
                    return false;
                }
                if (first && this.decision != null) {
                    TTS_CONTROLLER.finishStreaming(this.decision, false, "");
                    this.decision = null;
                    this.disabled = true;
                }
                return false;
            }
        }

        private boolean ownsSpeech() {
            return !this.cancelled.get()
                    && this.decision != null
                    && TTS_CONTROLLER.isCurrent(this.decision);
        }

        private boolean replaceWithFinalReply(String finalReply) {
            this.segmenter.cancel();
            this.pendingSegments.clear();
            this.closeBridgeStream();
            if (this.preempted.get()
                    || (this.decision != null && !TTS_CONTROLLER.isCurrent(this.decision))) {
                this.cancelAsSuperseded();
                return true;
            }
            if (this.decision != null) {
                if (finalReply == null || finalReply.isBlank()) {
                    TTS_CONTROLLER.finishStreaming(
                            this.decision, this.accepted, this.acceptedText.toString());
                    this.decision = null;
                    this.closed = true;
                    return false;
                }
                TtsDecision replacedDecision = this.decision;
                this.decision = null;
                this.closed = true;
                RobotTtsResult fallback = LingxiNavAgentApp.speakIfNeeded(
                        this.appConfig, this.eventId, finalReply, TtsPriority.URGENT,
                        this.navBridgeClient, replacedDecision);
                return fallback.accepted;
            }
            this.closed = true;
            if (finalReply == null || finalReply.isBlank()) {
                return false;
            }
            RobotTtsResult fallback = LingxiNavAgentApp.speakIfNeeded(
                    this.appConfig, this.eventId, finalReply, TtsPriority.URGENT,
                    this.navBridgeClient);
            return fallback.accepted;
        }

        private void closeBridgeStream() {
            if (!this.attempted || !this.bridgeStreamClosed.compareAndSet(false, true)) {
                return;
            }
            try {
                JSONObject response =
                        this.navBridgeClient.closeSpeechStream(this.eventId, this.streamId);
                if (response == null || !response.getBooleanValue("accepted")) {
                    log.warn(
                            "Robot speech stream close was not accepted: eventId={}, streamId={}, response={}",
                            this.eventId, this.streamId, response);
                }
            } catch (Exception exception) {
                log.warn(
                        "Robot speech stream close failed: eventId={}, streamId={}, error={}",
                        this.eventId, this.streamId, exception.getMessage());
            }
        }

        private boolean sameText(String left, String right) {
            return left != null && right != null && left.strip().equals(right.strip());
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
        private long nextRequestToken;
        private long activeRequestToken;
        private String lastNormalizedText = "";
        private StreamingTtsSession activeStreamingSession;

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

        boolean isInputEcho(String text) {
            Object object = this.lock;
            synchronized (object) {
                return System.currentTimeMillis() < this.silentUntilMillis
                        && SpeechInputPolicy.isEcho(text, this.lastNormalizedText);
            }
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        TtsDecision tryAcquire(String string, TtsPriority ttsPriority) {
            return this.tryAcquire(string, ttsPriority, null, null);
        }

        TtsDecision tryAcquireStreaming(String text, StreamingTtsSession session) {
            return this.tryAcquire(text, TtsPriority.NORMAL, session, null);
        }

        TtsDecision tryAcquireReplacement(
                String text, TtsPriority priority, TtsDecision replacedDecision) {
            return this.tryAcquire(text, priority, null, replacedDecision);
        }

        private TtsDecision tryAcquire(
                String string, TtsPriority ttsPriority, StreamingTtsSession session,
                TtsDecision replacedDecision) {
            String string2 = LingxiNavAgentApp.normalizeText(string);
            if (string2.isBlank()) {
                return TtsDecision.skip("empty_text");
            }
            StreamingTtsSession preemptedSession = null;
            TtsDecision decision;
            synchronized (this.lock) {
                if (replacedDecision != null && !this.ownsLocked(replacedDecision)) {
                    return TtsDecision.skip("tts_decision_superseded");
                }
                boolean supersedesStreamingSession = replacedDecision == null
                        && this.activeStreamingSession != null
                        && this.activeStreamingSession != session;
                boolean bl;
                long now = System.currentTimeMillis();
                boolean bl2 = bl = ttsPriority == TtsPriority.NAVIGATION || ttsPriority == TtsPriority.URGENT;
                if (!bl && this.isDuplicate(string2, now)) {
                    return TtsDecision.skip("duplicate_or_similar_reply");
                }
                if (!bl && this.requestInFlight && !supersedesStreamingSession) {
                    return TtsDecision.skip("tts_request_in_flight");
                }
                if (!bl && now - this.lastAcceptedAtMillis < this.minIntervalMillis
                        && !supersedesStreamingSession) {
                    return TtsDecision.skip("normal_tts_throttled");
                }
                if (supersedesStreamingSession) {
                    preemptedSession = this.activeStreamingSession;
                }
                this.requestInFlight = true;
                long token = ++this.nextRequestToken;
                this.activeRequestToken = token;
                this.activeStreamingSession = session;
                long estimate = this.estimatePlaybackMillis(string2);
                decision = new TtsDecision(
                        true, "accepted", true, bl, string2, ttsPriority, estimate, token);
            }
            if (preemptedSession != null) {
                preemptedSession.cancelAsSuperseded();
            }
            return decision;
        }

        boolean isCurrent(TtsDecision decision) {
            if (decision == null || !decision.allowed) {
                return false;
            }
            synchronized (this.lock) {
                return this.ownsLocked(decision);
            }
        }

        private boolean ownsLocked(TtsDecision decision) {
            return decision != null
                    && decision.allowed
                    && this.requestInFlight
                    && decision.token == this.activeRequestToken;
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

        void recordStreamingProgress(TtsDecision decision, String spokenText) {
            if (decision == null || !decision.allowed || spokenText == null || spokenText.isBlank()) {
                return;
            }
            synchronized (this.lock) {
                if (!this.ownsLocked(decision)) {
                    return;
                }
                long now = System.currentTimeMillis();
                this.lastAcceptedAtMillis = now;
                this.lastNormalizedText = LingxiNavAgentApp.normalizeText(spokenText);
                this.silentUntilMillis = Math.max(
                        this.silentUntilMillis, now + this.estimatePlaybackMillis(spokenText));
            }
        }

        void finishStreaming(TtsDecision decision, boolean accepted, String spokenText) {
            if (decision == null || !decision.allowed) {
                return;
            }
            synchronized (this.lock) {
                if (!this.ownsLocked(decision)) {
                    return;
                }
                long now = System.currentTimeMillis();
                this.requestInFlight = false;
                this.activeRequestToken = 0L;
                this.activeStreamingSession = null;
                if (!accepted || spokenText == null || spokenText.isBlank()) {
                    return;
                }
                this.lastAcceptedAtMillis = now;
                this.lastNormalizedText = LingxiNavAgentApp.normalizeText(spokenText);
                this.silentUntilMillis = Math.max(
                        this.silentUntilMillis, now + this.estimatePlaybackMillis(spokenText));
            }
        }

        /*
         * WARNING - Removed try catching itself - possible behaviour change.
         */
        void finish(TtsDecision ttsDecision, boolean bl) {
            if (!ttsDecision.allowed) {
                return;
            }
            synchronized (this.lock) {
                if (!this.ownsLocked(ttsDecision)) {
                    return;
                }
                long l;
                this.requestInFlight = false;
                this.activeRequestToken = 0L;
                this.activeStreamingSession = null;
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

    private record InteractionResult(
            String reply, TtsPriority ttsPriority, String skillAction, boolean markInteractionAfter,
            boolean localTtsHandled) {
        private InteractionResult(
                String reply, TtsPriority ttsPriority, String skillAction,
                boolean markInteractionAfter) {
            this(reply, ttsPriority, skillAction, markInteractionAfter, false);
        }

        private InteractionResult(String string, TtsPriority ttsPriority) {
            this(string, ttsPriority, "", false, false);
        }

        private InteractionResult(String string, TtsPriority ttsPriority, String skillAction) {
            this(string, ttsPriority, skillAction, false, false);
        }

        private InteractionResult withLocalTtsHandled() {
            return new InteractionResult(
                    this.reply, this.ttsPriority, this.skillAction,
                    this.markInteractionAfter, true);
        }
    }

    private record TtsDecision(
            boolean allowed, String reason, boolean interrupt, boolean force, String normalizedText,
            TtsPriority priority, long estimatedPlaybackMillis, long token) {
        static TtsDecision skip(String string) {
            return new TtsDecision(false, string, false, false, "", TtsPriority.NORMAL, 0L, 0L);
        }
    }
}
