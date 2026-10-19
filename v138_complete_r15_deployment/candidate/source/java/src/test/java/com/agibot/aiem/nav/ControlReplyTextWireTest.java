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
import com.alibaba.fastjson2.JSONArray;
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
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Checks configured speech ownership and real SDK completion across all response paths. */
public final class ControlReplyTextWireTest {
    private static int checks;
    private static int events;
    private static final List<String> failures = new ArrayList<>();
    private static final String POINT = "\u8f6f\u7740\u9646";
    private static final String TOUR = "\u573a\u9986\u8bb2\u89e3";
    private static final String CUSTOM = "\u5e26\u6211\u4eec\u4e86\u89e3\u4e00\u4e0b\u573a\u9986";
    private static final String ANSWER = "\u4eca\u5929\u53ef\u4ee5\u53c2\u89c2\u79d1\u6280\u5c55\u5385\u3002";

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) failures.add(message);
    }

    private static void reset() throws Exception {
        for (String name : List.of("LOCAL_COMMAND_DEDUP", "CONTROL_RESPONSE_DEDUP", "NATIVE_INTERRUPT_DEDUP")) {
            Field field = LingxiNavAgentApp.class.getDeclaredField(name);
            field.setAccessible(true);
            ((Map<?, ?>) field.get(null)).clear();
        }
        Field field = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        field.setAccessible(true);
        Object controller = field.get(null);
        for (String name : List.of("lastAcceptedAtMillis", "silentUntilMillis")) {
            Field timestamp = controller.getClass().getDeclaredField(name);
            timestamp.setAccessible(true);
            timestamp.setLong(controller, 0L);
        }
        Field last = controller.getClass().getDeclaredField("lastNormalizedText");
        last.setAccessible(true);
        last.set(controller, "");
    }

    public static void main(String[] args) throws Exception {
        ((ch.qos.logback.classic.LoggerContext) org.slf4j.LoggerFactory.getILoggerFactory())
                .getLoggerList().forEach(logger -> logger.setLevel(ch.qos.logback.classic.Level.OFF));
        AtomicReference<String> modeled = new AtomicReference<>();
        AtomicReference<String> receipt = new AtomicReference<>("{\"accepted\":true}");
        AtomicInteger ttsCode = new AtomicInteger(200);
        AtomicInteger dispatches = new AtomicInteger();
        List<String> localSpeech = new CopyOnWriteArrayList<>();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = "{\"ok\":true,\"accepted\":true}";
            if (path.equals("/model")) {
                JSONObject request = JSON.parseObject(input);
                String prompt = request.getJSONArray("messages").getJSONObject(0).getString("content");
                Object content = request.getJSONArray("messages").getJSONObject(1).get("content");
                String value = content instanceof JSONArray || prompt.contains("\u610f\u56fe\u89e3\u6790\u5668")
                        ? modeled.get() : ANSWER;
                body = "{\"choices\":[{\"message\":{\"content\":" + JSON.toJSONString(value) + "}}]}";
            } else if (path.equals("/points")) {
                body = "{\"point_names\":[\"" + POINT + "\"]}";
            } else if (path.equals("/task/match")) {
                body = input.contains(CUSTOM)
                        ? "{\"matched\":true,\"task_id\":\"museum\",\"task_name\":\"" + TOUR + "\"}"
                        : "{\"matched\":false}";
            } else if (List.of("/nav/start", "/nav/stop", "/task/start", "/task/pause",
                    "/task/resume", "/task/stop", "/motion/play").contains(path)) {
                dispatches.incrementAndGet();
                body = receipt.get();
            } else if (path.equals("/tts/speak")) {
                localSpeech.add(JSON.parseObject(input).getString("text"));
                body = ttsCode.get() == 200 ? "{\"accepted\":true}" : "{\"accepted\":false}";
            } else if (path.equals("/interaction/wake")) {
                body = "{\"handled\":false,\"accepted\":true}";
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(path.equals("/tts/speak") ? ttsCode.get() : 200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        CapturingClient client = new CapturingClient();
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "text-wire", "test", "test");
        var qaPath = java.nio.file.Files.createTempFile("text-wire-qa-", ".json");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.suppressLingxiText", "true");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            List<Scenario> controls = List.of(
                    new Scenario("\u5e26\u6211\u53bb" + POINT, "navigate", POINT, "\u6536\u5230\uff0c\u6b63\u5728\u524d\u5f80" + POINT + "\u3002"),
                    new Scenario("\u5f00\u59cb\u8bb2\u89e3\u4efb\u52a1", "start_task", "", "\u6536\u5230\uff0c\u5f00\u59cb" + TOUR + "\u3002"),
                    new Scenario(CUSTOM, "start_task", "museum", "\u6536\u5230\uff0c\u5f00\u59cb" + TOUR + "\u3002"),
                    new Scenario("\u6682\u505c\u8bb2\u89e3", "pause_task", "", "\u5df2\u6682\u505c\u8bb2\u89e3\u4efb\u52a1"),
                    new Scenario("\u7ee7\u7eed\u8bb2\u89e3", "resume_task", "", "\u597d\u7684\uff0c\u7ee7\u7eed\u8bb2\u89e3\u4efb\u52a1"),
                    new Scenario("\u7ed3\u675f\u8bb2\u89e3\u4efb\u52a1", "end_task", "", "\u5df2\u7ed3\u675f\u8bb2\u89e3\u4efb\u52a1"),
                    new Scenario("\u505c\u6b62\u5bfc\u822a", "stop_navigation", "", "\u5df2\u53d6\u6d88\u5bfc\u822a"));
            for (String route : List.of("robot", "lingxi")) {
                props.setProperty("tts.mode", route);
                AppConfig config = constructor.newInstance(props);
                NavBridgeClient bridge = new NavBridgeClient(config);
                DoubaoArkClient model = new DoubaoArkClient(config);
                QaStore qa = new QaStore(qaPath.toString());
                register.invoke(null, sdk, config, bridge, model, qa);
                for (int code : List.of(200, 503)) {
                    ttsCode.set(code);
                    for (String mode : List.of("asr2llm", "asr2tts", "audio2llm", "audio2tts")) {
                        for (Scenario scenario : controls) {
                            reset(); client.messages.clear(); localSpeech.clear();
                            receipt.set("{\"accepted\":true,\"cancelled\":true,\"paused\":true,"
                                    + "\"resumed\":true,\"stopped\":true,\"task_name\":\"" + TOUR + "\"}");
                            modeled.set(scenario.model());
                            String event = "control-text-" + events++;
                            int before = dispatches.get();
                            invoke(mode, sdk, client, event, scenario.text, config, bridge, model, qa);
                            String label = route + "/" + code + "/" + mode + "/" + scenario.intent;
                            check(dispatches.get() == before + 1, "one control dispatch " + label);
                            check(client.replies().equals(route.equals("robot") ? List.of() : List.of(scenario.expected)),
                                    "configured single SDK/local speech owner " + label);
                            check(client.complete(), "SDK completion retained " + label);
                            check(!client.hasErrorOrSkill(), "no contradictory SDK error/skill " + label);
                            check(localSpeech.equals(route.equals("robot") ? List.of(scenario.expected) : List.of()),
                                    "one local TTS attempt at most " + label);
                            if (mode.startsWith("asr")) {
                                int messages = client.replies().size();
                                invoke(mode.equals("asr2llm") ? "asr2tts" : "asr2llm",
                                        sdk, client, event, scenario.text, config, bridge, model, qa);
                                if (!scenario.intent.equals("stop_navigation") && !scenario.intent.equals("end_task")) {
                                    check(dispatches.get() == before + 1 && client.replies().size() == messages,
                                            "duplicate cannot dispatch/reply twice " + label);
                                }
                            }
                        }
                        reset(); client.messages.clear(); localSpeech.clear();
                        modeled.set("{\"intent\":\"chat\",\"question\":\"\u4eca\u5929\u6709\u54ea\u4e9b\u670d\u52a1\",\"reply\":\"" + ANSWER + "\"}");
                        invoke(mode, sdk, client, "qa-" + events++, "\u4eca\u5929\u6709\u54ea\u4e9b\u670d\u52a1",
                                config, bridge, model, qa);
                        boolean suppress = route.equals("robot");
                        check(client.replies().equals(suppress ? List.of() : List.of(ANSWER)),
                                "QA local ownership retained on missing transport acknowledgement " + route + code + mode);

                        for (String result : List.of("{\"accepted\":false,\"status\":\"rejected\"}",
                                "{\"accepted\":false,\"status\":\"confirmation_unknown\"}", "{}")) {
                            reset(); client.messages.clear(); localSpeech.clear();
                            receipt.set(result);
                            modeled.set(controls.get(1).model());
                            int before = dispatches.get();
                            invoke(mode, sdk, client, "uncertain-" + events++, controls.get(1).text,
                                    config, bridge, model, qa);
                            check(dispatches.get() == before + 1, "uncertain receipt is not retried");
                            check(client.replies().isEmpty() && localSpeech.isEmpty() && !client.hasErrorOrSkill(),
                                    "uncertain/rejected tour never invents success/failure speech " + mode);
                        }
                    }
                }
            }
            // A completed local control must still speak once if any SDK frame
            // throws, without reopening the response or resubmitting the command.
            props.setProperty("tts.mode", "robot");
            for (boolean suppress : List.of(true, false)) {
                props.setProperty("tts.suppressLingxiText", Boolean.toString(suppress));
                AppConfig config = constructor.newInstance(props);
                NavBridgeClient bridge = new NavBridgeClient(config);
                DoubaoArkClient model = new DoubaoArkClient(config);
                QaStore qa = new QaStore(qaPath.toString());
                register.invoke(null, sdk, config, bridge, model, qa);
                for (int code : List.of(200, 503)) {
                    ttsCode.set(code);
                    for (String mode : List.of("asr2llm", "asr2tts", "audio2llm", "audio2tts")) {
                        List<String> frames = new ArrayList<>(List.of(
                                "agentsdk.llm_response.item.done", "agentsdk.llm_response.done"));
                        if (!suppress) frames.add("agentsdk.llm_response.item.delta");
                        if (mode.endsWith("tts")) frames.addAll(List.of(
                                "agentsdk.tts_response.item.done", "agentsdk.tts_response.done"));
                        for (Scenario scenario : List.of(controls.get(0), controls.get(2))) {
                            for (String frame : frames) {
                                reset(); client.messages.clear(); localSpeech.clear();
                                client.failAt = frame; client.failed = false;
                                receipt.set("{\"accepted\":true,\"task_name\":\"" + TOUR + "\"}");
                                modeled.set(scenario.model());
                                int before = dispatches.get();
                                String label = mode + "/" + suppress + "/" + code + "/" + frame + "/" + scenario.intent;
                                invoke(mode, sdk, client, "frame-fault-" + events++, scenario.text,
                                        config, bridge, model, qa);
                                check(client.failed, "actual SDK frame exception injected " + label);
                                check(dispatches.get() == before + 1, "frame fault never retries physical control " + label);
                                check(localSpeech.equals(List.of(scenario.expected)), "frame fault still attempts local TTS once " + label);
                                check(client.replies().equals(suppress ? List.of() : List.of(scenario.expected)),
                                        "frame fault preserves configured speech owner " + label);
                                check(client.complete(), "later completion remains attempted " + label);
                                check(!client.hasErrorOrSkill(), "frame fault never becomes SDK error or skill " + label);
                            }
                        }
                    }
                }
            }
        } finally {
            http.stop(0);
            client.stop();
            sdk.release();
            java.nio.file.Files.deleteIfExists(qaPath);
        }
        if (!failures.isEmpty()) throw new AssertionError("Control reply text failures: " + failures.size() + "/" + checks
                + "\n" + String.join("\n", failures));
        System.out.println("ControlReplyTextWireTest passed: " + checks + " assertions");
    }

    private static void invoke(String mode, AgentSdk sdk, CapturingClient client, String event, String text,
                               AppConfig config, NavBridgeClient bridge, DoubaoArkClient model, QaStore qa) throws Exception {
        if (mode.equals("asr2llm")) {
            var callback = (Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM);
            callback.onRequest("test", event, text, VoiceGateTest.acceptedParams(),
                    new Asr2LlmResponse(client, "test", "robot"));
        } else if (mode.equals("asr2tts")) {
            var callback = (Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS);
            callback.onRequest("test", event, text, VoiceGateTest.acceptedParams(),
                    new Asr2TtsResponse(client, "test", "robot"));
        } else {
            // Exercise compatibility entry points without enabling them in field configuration.
            Class<?> sessionType = Class.forName("com.agibot.aiem.nav.LingxiNavAgentApp$AudioSession");
            var constructor = sessionType.getDeclaredConstructor(String.class, String.class);
            constructor.setAccessible(true);
            Object session = constructor.newInstance("item-" + event, "");
            Field buffer = sessionType.getDeclaredField("buffer");
            buffer.setAccessible(true);
            ((ByteArrayOutputStream) buffer.get(session)).write(new byte[320]);
            boolean llm = mode.equals("audio2llm");
            Class<?> responseType = llm ? Audio2LlmResponse.class : Audio2TtsResponse.class;
            Method method = LingxiNavAgentApp.class.getDeclaredMethod(
                    llm ? "processCompletedAudio2Llm" : "processCompletedAudio2Tts", String.class, sessionType,
                    responseType, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            method.setAccessible(true);
            Object response = llm ? new Audio2LlmResponse(client, "test", "robot")
                    : new Audio2TtsResponse(client, "test", "robot");
            method.invoke(null, event, session, response, config, bridge, model, qa);
        }
    }

    private record Scenario(String text, String intent, String target, String expected) {
        String model() {
            return "{\"intent\":\"" + intent + "\",\"target_name\":\"" + target + "\",\"question\":"
                    + JSON.toJSONString(text) + ",\"reply\":\"\u62b1\u6b49\uff0c\u6267\u884c\u5931\u8d25\"}";
        }
    }

    private static final class CapturingClient extends LinkskyClient {
        final List<String> messages = new CopyOnWriteArrayList<>();
        String failAt = "";
        boolean failed;
        CapturingClient() { super((AgentSdk) null); }
        List<String> replies() {
            return messages.stream().map(JSON::parseObject)
                    .filter(m -> "agentsdk.llm_response.item.delta".equals(m.getString("type")))
                    .map(m -> m.getString("text")).toList();
        }
        boolean complete() {
            return messages.stream().anyMatch(s -> s.contains("agentsdk.llm_response.done"));
        }
        boolean hasErrorOrSkill() {
            return messages.stream().anyMatch(s -> s.contains("agentsdk.error") || s.contains("agentsdk.skill"));
        }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            messages.add(payload);
            if (!failed && !failAt.isEmpty() && failAt.equals(JSON.parseObject(payload).getString("type"))) {
                failed = true;
                throw new IllegalStateException("injected control completion transport failure");
            }
        }
    }
}
