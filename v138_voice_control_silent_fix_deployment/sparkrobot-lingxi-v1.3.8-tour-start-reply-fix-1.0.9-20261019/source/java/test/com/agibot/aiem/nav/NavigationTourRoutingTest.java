package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.enums.AgentCallbackType;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicReference;

/** Replays a successful model response with a wrong intent, not only model exceptions. */
public final class NavigationTourRoutingTest {
    private static int checks;
    private static final List<String> failures = new ArrayList<>();
    private static final String APOLOGY = "抱歉，暂时没有学会这个动作";

    private static void check(boolean value, String message) {
        checks++;
        if (!value) failures.add(message);
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
            Field time = controller.getClass().getDeclaredField(name);
            time.setAccessible(true); time.setLong(controller, 0L);
        }
        Field spoken = controller.getClass().getDeclaredField("lastNormalizedText");
        spoken.setAccessible(true); spoken.set(controller, "");
    }

    public static void main(String[] args) throws Exception {
        ((ch.qos.logback.classic.LoggerContext) org.slf4j.LoggerFactory.getILoggerFactory())
                .getLoggerList().forEach(logger -> logger.setLevel(ch.qos.logback.classic.Level.OFF));
        List<String> calls = new CopyOnWriteArrayList<>();
        List<String> speech = new CopyOnWriteArrayList<>();
        AtomicReference<String> modelIntent = new AtomicReference<>();
        AtomicReference<String> acknowledgement = new AtomicReference<>("{\"accepted\":true,\"task_name\":\"酒店讲解\"}");
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = switch (path) {
                case "/points" -> "{\"ok\":true,\"point_names\":[\"门口\",\"大厅\"]}";
                case "/task/match" -> input.contains("欢迎参观酒店")
                        ? "{\"matched\":true,\"task_id\":\"hotel\",\"task_name\":\"酒店讲解\"}" : "{\"matched\":false}";
                case "/model" -> "{\"choices\":[{\"message\":{\"content\":"
                        + JSON.toJSONString(modelIntent.get()) + "}}]}";
                case "/interaction/wake" -> "{\"handled\":false,\"accepted\":true}";
                default -> "{\"ok\":true,\"accepted\":true}";
            };
            if (path.equals("/nav/start") || path.equals("/task/start") || path.equals("/motion/play")) {
                calls.add(path + ":" + input);
                body = acknowledgement.get();
            }
            if (path.equals("/tts/speak")) speech.add(JSONObject.parseObject(input).getString("text"));
            byte[] data = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, data.length);
            exchange.getResponseBody().write(data); exchange.close();
        });
        http.start();
        Path qa = Files.createTempFile("navigation-tour-routing-", ".json");
        CapturingClient client = new CapturingClient();
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "routing-regression", "test", "test");
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
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            register.invoke(null, sdk, config, new NavBridgeClient(config), new DoubaoArkClient(config),
                    new QaStore(qa.toString()));
            int event = 0;
            for (String mode : List.of("asr2llm", "asr2tts")) {
                for (String wrong : List.of(
                        "{\"intent\":\"chat\",\"reply\":\"" + APOLOGY + "\"}",
                        "{\"intent\":\"preset_motion\",\"target_name\":\"handshake\"}",
                        "{\"intent\":\"navigate\",\"target_name\":\"大厅\"}")) {
                    for (String text : List.of("带我去门口", "灵犀，请导航去门口", "请你领我们前往门口", "前往门口")) {
                        reset(); calls.clear(); speech.clear(); client.messages.clear();
                        modelIntent.set(wrong);
                        String id = "route-nav-" + event++;
                        request(sdk, client, mode, id, text);
                        check(calls.size() == 1 && calls.get(0).startsWith("/nav/start:")
                                && JSONObject.parseObject(calls.get(0).substring("/nav/start:".length()))
                                        .getString("target_name").equals("门口"), "one correct navigation " + mode + wrong + text);
                        check(speech.isEmpty(), "navigation receipt is silent " + text);
                        check(speech.stream().noneMatch(s -> s.contains(APOLOGY)), "no application apology " + text);
                        check(client.messages.stream().noneMatch(s -> s.contains("agentsdk.error") || s.contains("skill")),
                                "no SDK error or duplicate skill");
                        int before = calls.size();
                        request(sdk, client, mode.equals("asr2llm") ? "asr2tts" : "asr2llm", id, text);
                        check(calls.size() == before && speech.isEmpty(), "cross callback deduplication");
                    }
                }
                for (String text : List.of("开始讲解任务", "开始一下讲解任务", "执行讲解任务",
                        "开启讲解", "开始导览", "讲解任务开始", "欢迎参观酒店")) {
                    reset(); calls.clear(); speech.clear(); client.messages.clear();
                    modelIntent.set("{\"intent\":\"chat\",\"reply\":\"" + APOLOGY + "\"}");
                    request(sdk, client, mode, "route-tour-" + event++, text);
                    check(calls.size() == 1 && calls.get(0).startsWith("/task/start:"), "tour dispatch " + text);
                    check(speech.isEmpty(), "tour acknowledgement is silent " + text);
                    if (text.equals("欢迎参观酒店")) {
                        check(calls.size() == 1 && calls.get(0).contains("\"task_id\":\"hotel\""), "configured task ID preserved");
                    }
                }
                for (String text : List.of("不要带我去门口", "他说带我去门口", "如何开始讲解任务", "怎么启动讲解任务")) {
                    reset(); calls.clear(); speech.clear(); client.messages.clear();
                    modelIntent.set("{\"intent\":\"chat\",\"reply\":\"这是操作说明。\"}");
                    request(sdk, client, mode, "route-negative-" + event++, text);
                    check(calls.isEmpty(), "no unintended dispatch " + text);
                }
                reset(); calls.clear(); speech.clear();
                acknowledgement.set("{\"accepted\":false,\"status\":\"busy\"}");
                modelIntent.set("{\"intent\":\"chat\",\"reply\":\"" + APOLOGY + "\"}");
                request(sdk, client, mode, "route-busy-" + event++, "带我去门口");
                check(calls.size() == 1 && speech.stream().noneMatch(s -> s.contains("正在前往")),
                        "real rejected navigation is not presented as success");
                acknowledgement.set("{\"accepted\":true,\"task_name\":\"酒店讲解\"}");
            }
            for (String text : List.of("带我去不存在", "带我去门口和大厅", "如果带我去门口", "门口在哪里")) {
                check(NavigationCommandFallback.resolve(text, List.of("门口", "大厅")) == null,
                        "exact catalog safety " + text);
            }
            check(NavigationCommandFallback.resolve("带我去门口", List.of("门口", "门 口")) == null, "ambiguous catalog");
        } finally { http.stop(0); client.stop(); sdk.release(); Files.deleteIfExists(qa); }
        if (!failures.isEmpty()) throw new AssertionError("Routing failures " + failures.size()
                + "/" + checks + "\n" + String.join("\n", failures));
        System.out.println("NavigationTourRoutingTest passed: " + checks + " assertions");
    }

    private static void request(AgentSdk sdk, CapturingClient client, String mode, String event, String text) {
        if (mode.equals("asr2llm")) {
            ((Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM)).onRequest(
                    "test", event, text, VoiceGateTest.acceptedParams(), new Asr2LlmResponse(client, "test", "robot"));
        } else {
            ((Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS)).onRequest(
                    "test", event, text, VoiceGateTest.acceptedParams(), new Asr2TtsResponse(client, "test", "robot"));
        }
    }

    private static final class CapturingClient extends LinkskyClient {
        final List<String> messages = new CopyOnWriteArrayList<>();
        CapturingClient() { super((AgentSdk) null); }
        @Override public void sendText(String agent, String event, String payload, String logged) { messages.add(payload); }
    }
}
