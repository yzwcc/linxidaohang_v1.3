package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
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
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Real SDK callbacks and wire messages; native firmware audio is outside this fixture. */
public final class SilentStartControlTest {
    private static int checks;
    private static final List<String> failures = new CopyOnWriteArrayList<>();

    private static void check(boolean value, String label) {
        checks++;
        if (!value) failures.add(label);
    }

    static void reset() throws Exception {
        for (String name : List.of("LOCAL_COMMAND_DEDUP", "CONTROL_RESPONSE_DEDUP",
                "NATIVE_INTERRUPT_DEDUP", "SILENT_START_CONTROL_EVENTS", "TOUR_START_RECEIPTS")) {
            try {
                Field field = LingxiNavAgentApp.class.getDeclaredField(name);
                field.setAccessible(true);
                ((Map<?, ?>) field.get(null)).clear();
            } catch (NoSuchFieldException absentInOldJar) { }
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
        AtomicReference<String> receipt = new AtomicReference<>();
        AtomicReference<String> modeled = new AtomicReference<>();
        AtomicInteger status = new AtomicInteger(200);
        AtomicInteger modelCalls = new AtomicInteger();
        List<String> dispatches = new CopyOnWriteArrayList<>();
        List<String> speech = new CopyOnWriteArrayList<>();
        List<String> trace = new CopyOnWriteArrayList<>();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = "{\"ok\":true,\"accepted\":true}";
            if (path.equals("/model")) {
                modelCalls.incrementAndGet();
                body = "{\"choices\":[{\"message\":{\"content\":" + JSON.toJSONString(modeled.get()) + "}}]}";
            } else if (path.equals("/points")) {
                body = "{\"ok\":true,\"point_names\":[\"门口\"]}";
            } else if (path.equals("/task/match")) {
                body = input.contains("欢迎参观")
                        ? "{\"matched\":true,\"task_id\":\"custom-tour\"}" : "{\"matched\":false}";
            } else if (path.equals("/interaction/wake")) {
                body = "{\"handled\":false,\"accepted\":true}";
            } else if (path.equals("/nav/start") || path.equals("/task/start")) {
                dispatches.add(path + ":" + input);
                trace.add("dispatch");
                body = receipt.get();
            } else if (path.equals("/tts/speak")) {
                speech.add(input);
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(path.equals("/nav/start") || path.equals("/task/start")
                    ? status.get() : 200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        Client client = new Client(trace);
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "silent-start", "test", "test");
        var qa = Files.createTempFile("silent-start-qa-", ".json");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> factory = AppConfig.class.getDeclaredConstructor(Properties.class);
            factory.setAccessible(true);
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            int event = 0;
            for (String route : List.of("robot", "lingxi")) {
                props.setProperty("tts.mode", route);
                for (boolean suppress : List.of(true, false)) {
                    props.setProperty("tts.suppressLingxiText", Boolean.toString(suppress));
                    AppConfig config = factory.newInstance(props);
                    NavBridgeClient bridge = new NavBridgeClient(config);
                    DoubaoArkClient model = new DoubaoArkClient(config);
                    QaStore store = new QaStore(qa.toString());
                    register.invoke(null, sdk, config, bridge, model, store);
                    for (String mode : List.of("asr2llm", "asr2tts", "audio2llm", "audio2tts")) {
                        for (String text : List.of("带我去门口", "开始讲解任务", "欢迎参观")) {
                            for (String response : List.of(
                                    "{\"accepted\":true,\"task_name\":\"酒店导览\"}",
                                    "{\"accepted\":false,\"status\":\"busy\"}",
                                    "{\"accepted\":false,\"status\":\"point_not_found\"}",
                                    "{\"accepted\":false,\"status\":\"invalid_task\"}",
                                    "{\"accepted\":false,\"status\":\"rejected\",\"message\":\"任务执行失败\"}",
                                    "{}", "{\"accepted\":\"true\"}", "bad-json", "http503")) {
                                reset(); dispatches.clear(); speech.clear(); trace.clear(); client.messages.clear();
                                client.failAt = ""; client.failed = false;
                                receipt.set(response.equals("http503") ? "{}" : response);
                                status.set(response.equals("http503") ? 503 : 200);
                                String intent = text.startsWith("带") ? "navigate" : "start_task";
                                modeled.set("{\"intent\":\"" + intent + "\",\"target_name\":\""
                                        + (intent.equals("navigate") ? "门口" : "")
                                        + "\",\"question\":" + JSON.toJSONString(text)
                                        + ",\"reply\":\"抱歉，暂时没有学会这个动作\"}");
                                String id = "silent-" + event++;
                                int beforeModel = modelCalls.get();
                                ControlReplyTextWireTest.invoke(mode, sdk, client, id, text, config, bridge, model, store);
                                String label = route + suppress + mode + text + response;
                                check(dispatches.size() == 1, "one physical dispatch " + label);
                                check(speech.isEmpty(), "no local success/failure TTS " + label);
                                List<String> replies = client.messages.stream().map(JSON::parseObject)
                                        .filter(m -> "agentsdk.llm_response.item.delta".equals(m.getString("type")))
                                        .map(m -> m.getString("text")).toList();
                                check(replies.equals(intent.equals("start_task") ? List.of("收到") : List.of())
                                        && client.messages.stream().noneMatch(s -> s.contains("agentsdk.error")
                                        || s.contains("agentsdk.xlm_response.skill") || s.contains("agentsdk.tts_response.item.delta")),
                                        "only neutral tour receipt; no result/audio/error/skill " + label);
                                List<JSONObject> interrupts = client.messages.stream().map(JSON::parseObject)
                                        .filter(m -> m.getString("type").endsWith(".interrupt")).toList();
                                check(interrupts.size() == 1 && "chat".equals(interrupts.get(0).getString("interruptType"))
                                        && interrupts.get(0).get("interruptTips") == null, "one silent SDK takeover " + label);
                                check(trace.indexOf("interrupt") >= 0
                                        && trace.indexOf("interrupt") < trace.indexOf("dispatch"), "takeover before dispatch " + label);
                                check(client.messages.stream().anyMatch(s -> s.contains("agentsdk.llm_response.done")),
                                        "stream completed " + label);
                                check(dispatches.size() == 1 && dispatches.get(0).contains("\"silent_control_reply\":true"),
                                        "bridge receives silent receipt scope " + label);
                                if (mode.startsWith("asr")) {
                                    check(modelCalls.get() == beforeModel, "explicit saved command avoids classifier " + label);
                                    ControlReplyTextWireTest.invoke(mode.equals("asr2llm") ? "asr2tts" : "asr2llm",
                                            sdk, client, id, text, config, bridge, model, store);
                                    check(dispatches.size() == 1 && speech.isEmpty(), "cross callback duplicate remains silent " + label);
                                }
                            }
                        }
                        for (String frame : List.of("agentsdk.xlm_response.interrupt",
                                "agentsdk.llm_response.item.done", "agentsdk.llm_response.done",
                                "agentsdk.tts_response.item.done", "agentsdk.tts_response.done")) {
                            if (mode.endsWith("llm") && frame.contains("tts_response")) continue;
                            reset(); dispatches.clear(); speech.clear(); trace.clear(); client.messages.clear();
                            client.failAt = frame; client.failed = false;
                            status.set(200); receipt.set("{\"accepted\":true}");
                            modeled.set("{\"intent\":\"navigate\",\"target_name\":\"门口\",\"question\":\"带我去门口\"}");
                            ControlReplyTextWireTest.invoke(mode, sdk, client, "fault-" + event++, "带我去门口",
                                    config, bridge, model, store);
                            check(client.failed, "fault was injected " + mode + frame);
                            check(dispatches.size() == 1 && speech.isEmpty(), "fault cannot replay or speak " + mode + frame);
                            check(client.messages.stream().noneMatch(s -> s.contains("agentsdk.error")),
                                    "fault has no SDK failure " + mode + frame);
                        }
                    }
                }
            }
        } finally { http.stop(0); sdk.release(); client.stop(); Files.deleteIfExists(qa); }
        if (!failures.isEmpty()) throw new AssertionError("Silent start failures " + failures.size() + "/" + checks
                + "\n" + String.join("\n", failures.subList(0, Math.min(30, failures.size()))));
        System.out.println("SilentStartControlTest passed: " + checks + " assertions");
    }

    private static final class Client extends LinkskyClient {
        final List<String> messages = new CopyOnWriteArrayList<>();
        final List<String> trace;
        String failAt = "";
        boolean failed;
        Client(List<String> trace) { super((AgentSdk) null); this.trace = trace; }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            messages.add(payload);
            String type = JSON.parseObject(payload).getString("type");
            if (type.endsWith(".interrupt")) trace.add("interrupt");
            if (type.equals(failAt) && !failed) {
                failed = true;
                throw new IllegalStateException("injected SDK send failure");
            }
        }
    }
}
