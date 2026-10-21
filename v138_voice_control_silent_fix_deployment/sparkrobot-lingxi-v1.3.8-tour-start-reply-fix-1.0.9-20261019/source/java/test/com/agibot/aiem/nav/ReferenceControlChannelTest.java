package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.agibot.aiem.sdk.enums.AgentCallbackType;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.agibot.aiem.sdk.passive.Audio2LlmResponse;
import com.agibot.aiem.sdk.passive.Audio2TtsResponse;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicReference;

/** Preserves the reference channel for other controls; start receipts are now silent. */
public final class ReferenceControlChannelTest {
    private static int checks;
    private static final List<String> failures = new ArrayList<>();

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) failures.add(message);
    }

    private static void reset() throws Exception {
        for (String name : List.of("LOCAL_COMMAND_DEDUP", "CONTROL_RESPONSE_DEDUP", "NATIVE_INTERRUPT_DEDUP")) {
            try {
                Field field = LingxiNavAgentApp.class.getDeclaredField(name);
                field.setAccessible(true);
                ((Map<?, ?>) field.get(null)).clear();
            } catch (NoSuchFieldException absentInReference) {
                // The old JAR has no control ownership registries.
            }
        }
        Field field = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        field.setAccessible(true);
        Object controller = field.get(null);
        for (String name : List.of("lastAcceptedAtMillis", "silentUntilMillis")) {
            Field time = controller.getClass().getDeclaredField(name);
            time.setAccessible(true);
            time.setLong(controller, 0L);
        }
        Field text = controller.getClass().getDeclaredField("lastNormalizedText");
        text.setAccessible(true);
        text.set(controller, "");
    }

    public static void main(String[] args) throws Exception {
        ((ch.qos.logback.classic.LoggerContext) org.slf4j.LoggerFactory.getILoggerFactory())
                .getLoggerList().forEach(logger -> logger.setLevel(ch.qos.logback.classic.Level.OFF));
        List<String> trace = new CopyOnWriteArrayList<>();
        List<String> speech = new CopyOnWriteArrayList<>();
        AtomicReference<Scenario> scenario = new AtomicReference<>();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            exchange.getRequestBody().readAllBytes();
            String body = "{\"ok\":true,\"accepted\":true}";
            if (path.equals("/model")) {
                Scenario current = scenario.get();
                String intent = "{\"intent\":\"" + current.intent + "\",\"target_name\":\""
                        + current.target + "\",\"question\":" + JSON.toJSONString(current.text)
                        + ",\"reply\":\"\u786e\u8ba4\u6267\u884c\u3002\"}";
                body = "{\"choices\":[{\"message\":{\"content\":" + JSON.toJSONString(intent) + "}}]}";
            } else if (path.equals("/points")) {
                body = "{\"point_names\":[\"\u5927\u5385\"]}";
            } else if (path.equals("/interaction/wake")) {
                body = "{\"handled\":false,\"accepted\":true}";
            } else if (path.equals("/task/match")) {
                body = "{\"matched\":false}";
            } else if (path.equals("/tts/speak")) {
                trace.add("local-tts");
                speech.add("accepted");
            } else if (List.of("/nav/start", "/nav/stop", "/task/start", "/task/pause",
                    "/task/resume", "/task/stop").contains(path)) {
                trace.add("dispatch:" + path);
                body = "{\"accepted\":true,\"cancelled\":true,\"paused\":true,\"resumed\":true,\"stopped\":true}";
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        CapturingClient client = new CapturingClient(trace);
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "reference-wire", "test", "test");
        try {
            Properties properties = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                properties.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            properties.setProperty("nav.baseUrl", base);
            properties.setProperty("doubao.url", base + "/model");
            properties.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            properties.setProperty("tts.mode", "robot");
            properties.setProperty("tts.suppressLingxiText", "true");
            properties.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(properties);
            NavBridgeClient bridge = new NavBridgeClient(config);
            DoubaoArkClient model = new DoubaoArkClient(config);
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            register.invoke(null, sdk, config, bridge, model, null);
            int event = 0;
            for (String mode : List.of("asr2llm", "asr2tts", "audio2llm", "audio2tts")) {
                for (Scenario current : List.of(
                        new Scenario("\u5e26\u6211\u53bb\u5927\u5385", "navigate", "\u5927\u5385", "/nav/start"),
                        new Scenario("\u5f00\u59cb\u8bb2\u89e3\u4efb\u52a1", "start_task", "", "/task/start"),
                        new Scenario("\u6682\u505c\u8bb2\u89e3", "pause_task", "", "/task/pause"),
                        new Scenario("\u7ee7\u7eed\u8bb2\u89e3", "resume_task", "", "/task/resume"),
                        new Scenario("\u7ed3\u675f\u8bb2\u89e3\u4efb\u52a1", "end_task", "", "/task/stop"),
                        new Scenario("\u505c\u6b62\u5bfc\u822a", "stop_navigation", "", "/nav/stop"))) {
                    reset(); trace.clear(); speech.clear(); client.messages.clear();
                    scenario.set(current);
                    String id = "reference-" + event++;
                    if (mode.equals("asr2llm")) {
                        ((Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM)).onRequest(
                                "test", id, current.text, null, new Asr2LlmResponse(client, "test", "robot"));
                    } else if (mode.equals("asr2tts")) {
                        ((Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS)).onRequest(
                                "test", id, current.text, null, new Asr2TtsResponse(client, "test", "robot"));
                    } else {
                        Class<?> sessionType = Class.forName("com.agibot.aiem.nav.LingxiNavAgentApp$AudioSession");
                        var sessionConstructor = sessionType.getDeclaredConstructor(String.class, String.class);
                        sessionConstructor.setAccessible(true);
                        Object session = sessionConstructor.newInstance("item-" + id, "");
                        Field buffer = sessionType.getDeclaredField("buffer");
                        buffer.setAccessible(true);
                        ((ByteArrayOutputStream) buffer.get(session)).write(new byte[320]);
                        boolean llm = mode.equals("audio2llm");
                        Class<?> responseType = llm ? Audio2LlmResponse.class : Audio2TtsResponse.class;
                        Method process = LingxiNavAgentApp.class.getDeclaredMethod(
                                llm ? "processCompletedAudio2Llm" : "processCompletedAudio2Tts", String.class,
                                sessionType, responseType, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
                        process.setAccessible(true);
                        process.invoke(null, id, session, llm ? new Audio2LlmResponse(client, "test", "robot")
                                : new Audio2TtsResponse(client, "test", "robot"), config, bridge, model, null);
                    }
                    String label = mode + "/" + current.intent;
                    boolean silent = current.intent.equals("navigate") || current.intent.equals("start_task");
                    List<String> types = client.messages.stream().map(JSON::parseObject)
                            .map(message -> message.getString("type")).toList();
                    check(trace.stream().filter(s -> s.startsWith("dispatch:")).toList()
                            .equals(List.of("dispatch:" + current.path)), "single dispatch " + label);
                    check(types.stream().filter(t -> t.contains("interrupt")).count() == (silent ? 1 : 0),
                            "silent start uses one SDK takeover " + label);
                    List<String> sdkText = client.messages.stream().map(JSON::parseObject)
                            .filter(m -> "agentsdk.llm_response.item.delta".equals(m.getString("type")))
                            .map(m -> m.getString("text")).toList();
                    check(sdkText.equals(current.intent.equals("start_task") ? List.of("收到") : List.of()),
                            "single speech owner, with neutral SDK tour receipt " + label);
                    check(types.stream().noneMatch(t -> t.contains("error") || t.contains("skill")), "no SDK error/skill " + label);
                    check(speech.size() == (silent ? 0 : 1), "expected local speech request count " + label);
                    check(trace.contains("agentsdk.llm_response.done"), "SDK completion " + label);
                    check(trace.indexOf("agentsdk.llm_response.item.done") >= 0
                            && trace.indexOf("agentsdk.llm_response.item.done") < trace.indexOf("agentsdk.llm_response.done"),
                            "SDK item completion before completion " + label);
                    check(silent ? !trace.contains("local-tts")
                                    : trace.indexOf("agentsdk.llm_response.done") < trace.indexOf("local-tts"),
                            "SDK completion before local TTS " + label);
                    if (mode.endsWith("tts")) {
                        check(trace.indexOf("agentsdk.tts_response.item.done") > trace.indexOf("local-tts")
                                && trace.indexOf("agentsdk.tts_response.done") > trace.indexOf("agentsdk.tts_response.item.done"),
                                "TTS completion after local speech " + label);
                    }
                }
            }
        } finally {
            http.stop(0); client.stop(); sdk.release();
        }
        if (!failures.isEmpty()) throw new AssertionError("Reference control channel failures: "
                + failures.size() + "/" + checks + "\n" + String.join("\n", failures));
        System.out.println("ReferenceControlChannelTest passed: " + checks + " assertions");
    }

    private record Scenario(String text, String intent, String target, String path) { }

    private static final class CapturingClient extends LinkskyClient {
        final List<String> trace;
        final List<String> messages = new CopyOnWriteArrayList<>();
        CapturingClient(List<String> trace) { super((AgentSdk) null); this.trace = trace; }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            messages.add(payload);
            trace.add(JSON.parseObject(payload).getString("type"));
        }
    }
}
