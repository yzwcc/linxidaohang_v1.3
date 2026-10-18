package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.agibot.aiem.sdk.enums.AgentCallbackType;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Fault injection at the actual SDK callback and HTTP acknowledgement boundaries. */
public final class ControlOwnershipFaultTest {
    private static int checks;
    private static final List<String> failures = new ArrayList<>();

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) failures.add(message);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Long> registry(String name) throws Exception {
        Field field = LingxiNavAgentApp.class.getDeclaredField(name);
        field.setAccessible(true);
        return (Map<String, Long>) field.get(null);
    }

    private static void clear() throws Exception {
        for (String name : List.of("LOCAL_COMMAND_DEDUP", "CONTROL_RESPONSE_DEDUP", "NATIVE_INTERRUPT_DEDUP"))
            registry(name).clear();
    }

    public static void main(String[] args) throws Exception {
        ((ch.qos.logback.classic.LoggerContext) org.slf4j.LoggerFactory.getILoggerFactory())
                .getLoggerList().forEach(logger -> logger.setLevel(ch.qos.logback.classic.Level.OFF));
        AtomicReference<String> intent = new AtomicReference<>();
        AtomicInteger modelCode = new AtomicInteger(200);
        AtomicInteger modelCalls = new AtomicInteger();
        AtomicInteger wakeCalls = new AtomicInteger();
        AtomicReference<String> fault = new AtomicReference<>("");
        Map<String, AtomicInteger> calls = new ConcurrentHashMap<>();
        List<String> speech = new CopyOnWriteArrayList<>();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = "{\"ok\":true}";
            int code = 200;
            if (path.equals("/model")) {
                modelCalls.incrementAndGet();
                code = modelCode.get();
                body = "{\"choices\":[{\"message\":{\"content\":" + JSONObject.toJSONString(intent.get()) + "}}]}";
            } else if (path.equals("/points")) {
                body = "{\"ok\":true,\"point_names\":[\"大厅\"]}";
            } else if (path.equals("/task/match")) {
                body = input.contains("欢迎参观")
                        ? "{\"matched\":true,\"task_id\":\"hotel\"}" : "{\"matched\":false}";
            } else if (path.equals("/interaction/wake")) {
                wakeCalls.incrementAndGet();
                body = "{\"handled\":false,\"accepted\":true}";
            } else if (path.equals("/tts/speak")) {
                speech.add(input);
                body = "{\"accepted\":true}";
            } else if (List.of("/nav/start", "/nav/stop", "/task/start", "/task/pause",
                    "/task/resume", "/task/stop", "/motion/play", "/entertainment/dance").contains(path)) {
                int attempt = calls.computeIfAbsent(path, key -> new AtomicInteger()).incrementAndGet();
                body = "{\"accepted\":true,\"cancelled\":true,\"paused\":true,\"resumed\":true,\"stopped\":true}";
                switch (fault.get()) {
                    case "lost-then-busy" -> {
                        code = attempt == 1 ? 503 : 200;
                        body = attempt == 1 ? "response lost after dispatch" : "{\"accepted\":false,\"status\":\"busy\"}";
                    }
                    case "malformed" -> body = "not-json";
                    case "null" -> body = "null";
                    case "empty" -> body = "{}";
                    case "missing-acceptance" -> body = "{\"status\":\"pnc_unavailable\"}";
                    case "http-error" -> { code = 503; body = "response lost after dispatch"; }
                    case "disconnected" -> { exchange.close(); return; }
                    case "transient-then-ok" -> {
                        if (attempt < 3) body = "{\"accepted\":false,\"status\":\"localization_unavailable\"}";
                    }
                    case "transient-exhausted" ->
                        body = "{\"accepted\":false,\"status\":\"localization_unavailable\",\"message\":\"定位尚未就绪\"}";
                    case "point-not-found" ->
                        body = "{\"accepted\":false,\"status\":\"point_not_found\"}";
                    default -> { }
                }
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(code, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        CapturingClient client = new CapturingClient();
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "fault-test", "test", "test");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("nav.timeoutMs", "2000");
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            var register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            for (String route : List.of("lingxi", "robot")) {
                props.setProperty("tts.mode", route);
                props.setProperty("tts.suppressLingxiText", "true");
                AppConfig config = constructor.newInstance(props);
                register.invoke(null, sdk, config, new NavBridgeClient(config), new DoubaoArkClient(config), null);
                Asr2LlmCallback llm = (Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM);
                Asr2TtsCallback tts = (Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS);
                Asr2LlmResponse llmReply = new Asr2LlmResponse(client, "llm", "robot");
                Asr2TtsResponse ttsReply = new Asr2TtsResponse(client, "tts", "robot");

                for (String kind : List.of("nav", "tour", "motion")) {
                    clear(); calls.clear(); speech.clear(); client.messages.clear();
                    fault.set(""); modelCode.set(200);
                    intent.set(modelIntent(kind));
                    String text = text(kind), event = route + "-late-" + kind;
                    llm.onRequest("llm", event, text, VoiceGateTest.acceptedParams(), llmReply);
                    check(total(calls) == 1, "initial dispatch: " + event);
                    // Model the expiry of the short 3s registry, retaining the 60s event ownership.
                    registry("LOCAL_COMMAND_DEDUP").replaceAll((key, timestamp) -> timestamp - 4000);
                    speech.clear(); client.messages.clear();
                    int before = modelCalls.get();
                    int wakeBefore = wakeCalls.get();
                    modelCode.set(503);
                    tts.onRequest("tts", event, text, VoiceGateTest.acceptedParams(), ttsReply);
                    check(modelCalls.get() == before, "owned callback bypasses unavailable model: " + event);
                    check(wakeCalls.get() == wakeBefore, "owned callback cannot pause or wake an active tour: " + event);
                    check(total(calls) == 1, "late callback never resubmits: " + event);
                    check(speech.isEmpty() && !client.hasSpeechOrError(), "late callback silent: " + event);
                }
                modelCode.set(200);
                for (String kind : List.of("nav", "tour", "pause", "resume", "stop", "end", "motion", "dance")) {
                    for (String mode : List.of("llm", "tts")) {
                        for (String value : List.of("lost-then-busy", "malformed", "null", "empty",
                                "missing-acceptance", "http-error", "disconnected")) {
                            clear(); calls.clear(); speech.clear(); client.messages.clear();
                            fault.set(value); intent.set(modelIntent(kind));
                            String event = route + mode + kind + value;
                            if (mode.equals("llm")) llm.onRequest("llm", event, text(kind), VoiceGateTest.acceptedParams(), llmReply);
                            else tts.onRequest("tts", event, text(kind), VoiceGateTest.acceptedParams(), ttsReply);
                            check(total(calls) == 1, "ambiguous POST never retried: " + event);
                            check(speech.isEmpty() && !client.hasSpeechOrError(), "unknown acknowledgement silent: " + event);
                        }
                    }
                }
                for (String value : List.of("transient-then-ok", "transient-exhausted", "point-not-found")) {
                    clear(); calls.clear(); speech.clear(); client.messages.clear();
                    fault.set(value); intent.set(modelIntent("nav"));
                    llm.onRequest("llm", route + value, text("nav"), VoiceGateTest.acceptedParams(), llmReply);
                    check(total(calls) == (value.equals("point-not-found") ? 1 : 3), "only explicit transient rejection retries: " + value);
                    String spoken = speech.toString() + client.messages.toString();
                    check(spoken.contains(value.equals("transient-then-ok") ? "正在前往"
                            : value.equals("point-not-found") ? "没有找到" : "定位尚未就绪"),
                            "actual final controller result retained: " + value);
                }
                fault.set("");
                for (String negative : List.of("不要开始讲解", "别继续讲解", "他说开始讲解",
                        "如果开始讲解会怎么样", "不要带我去大厅", "不要欢迎参观")) {
                    clear(); calls.clear(); speech.clear(); client.messages.clear();
                    intent.set(modelIntent(negative.contains("去") ? "nav" : "tour"));
                    llm.onRequest("llm", route + negative, "嗨灵犀" + negative, VoiceGateTest.acceptedParams(), llmReply);
                    check(total(calls) == 0, "negative/quoted text cannot dispatch even with wrong model intent: " + negative);
                    check(!client.messages.toString().contains("agentsdk.error"), "nonimperative response finishes normally");
                }
                for (String stop : List.of("停止导航", "不要走", "结束讲解", "取消讲解任务")) {
                    clear(); calls.clear();
                    for (int repeat = 0; repeat < 2; repeat++)
                        llm.onRequest("llm", route + stop, "嗨灵犀" + stop, VoiceGateTest.acceptedParams(), llmReply);
                    check(total(calls) == 2, "urgent stop remains repeatable: " + stop);
                }
                clear(); calls.clear();
                llm.onRequest("llm", route + "-polite-tour", "嗨灵犀能不能开始讲解", VoiceGateTest.acceptedParams(), llmReply);
                check(total(calls) == 1, "polite request not rejected as negation");
            }
        } finally {
            sdk.release(); client.stop(); http.stop(0);
        }
        if (!failures.isEmpty()) {
            System.err.println("ControlOwnershipFaultTest failures: " + failures.size() + "/" + checks);
            failures.forEach(System.err::println);
            throw new AssertionError("Control acknowledgement/ownership regression");
        }
        System.out.println("ControlOwnershipFaultTest passed: " + checks + " assertions");
    }

    private static int total(Map<String, AtomicInteger> calls) {
        return calls.values().stream().mapToInt(AtomicInteger::get).sum();
    }

    private static String modelIntent(String kind) {
        return switch (kind) {
            case "nav" -> "{\"intent\":\"navigate\",\"target_name\":\"大厅\",\"reply\":\"做不到\"}";
            case "motion" -> "{\"intent\":\"preset_motion\",\"target_name\":\"handshake\"}";
            case "dance" -> "{\"intent\":\"dance\"}";
            default -> "{\"intent\":\"start_task\"}";
        };
    }

    private static String text(String kind) {
        return "嗨灵犀" + switch (kind) {
            case "nav" -> "请引领我前往大厅";
            case "tour" -> "欢迎参观";
            case "pause" -> "暂停讲解";
            case "resume" -> "继续讲解";
            case "stop" -> "停止导航";
            case "end" -> "结束讲解";
            case "dance" -> "跳个舞";
            default -> "咱俩友好地握一下手怎么样";
        };
    }

    private static final class CapturingClient extends LinkskyClient {
        final List<String> messages = new CopyOnWriteArrayList<>();
        CapturingClient() { super((AgentSdk) null); }
        boolean hasSpeechOrError() {
            return messages.stream().anyMatch(value -> value.contains("agentsdk.llm_response.item.delta")
                    || value.contains("agentsdk.error"));
        }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            messages.add(payload);
        }
    }
}
