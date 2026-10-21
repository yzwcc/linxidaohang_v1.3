package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Checks takeover timing with real SDK frames, without assuming native speaker behavior. */
public final class TourStartTakeoverTest {
    private static int checks;
    private static final List<String> failures = new CopyOnWriteArrayList<>();

    private static void check(boolean ok, String label) {
        checks++;
        if (!ok) failures.add(label);
    }

    public static void main(String[] args) throws Exception {
        ((ch.qos.logback.classic.LoggerContext) org.slf4j.LoggerFactory.getILoggerFactory())
                .getLoggerList().forEach(logger -> logger.setLevel(ch.qos.logback.classic.Level.OFF));
        List<String> trace = new CopyOnWriteArrayList<>();
        List<String> dispatches = new CopyOnWriteArrayList<>();
        List<String> speech = new CopyOnWriteArrayList<>();
        AtomicReference<String> receipt = new AtomicReference<>();
        AtomicReference<String> intent = new AtomicReference<>();
        AtomicBoolean protectedStage = new AtomicBoolean(false);
        AtomicBoolean conflict = new AtomicBoolean(false);
        AtomicReference<CountDownLatch> startObserved = new AtomicReference<>();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String text = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            trace.add(path);
            String body = "{\"ok\":true,\"accepted\":true}";
            if (path.equals("/model")) {
                body = "{\"choices\":[{\"message\":{\"content\":" + JSON.toJSONString(intent.get()) + "}}]}";
            } else if (path.equals("/points")) {
                body = "{\"ok\":true,\"point_names\":[\"门口\"]}";
            } else if (path.equals("/interaction/wake")) {
                body = protectedStage.get()
                        ? "{\"handled\":true,\"accepted\":false,\"state\":\"task_linkcraft_action\"}"
                        : "{\"handled\":false,\"accepted\":true}";
            } else if (path.equals("/task/match")) {
                body = conflict.get() ? "{\"matched\":false,\"status\":\"conflict\",\"message\":\"任务执行失败\"}"
                        : text.contains("欢迎参观") ? "{\"matched\":true,\"task_id\":\"custom-tour\"}"
                        : "{\"matched\":false}";
            } else if (path.equals("/task/start") || path.equals("/nav/start")) {
                dispatches.add(text);
                CountDownLatch observed = startObserved.get();
                if (observed != null) observed.countDown();
                body = receipt.get().equals("http503") ? "{}" : receipt.get();
            } else if (path.equals("/tts/speak")) {
                speech.add(text);
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(path.equals("/task/start") && receipt.get().equals("http503") ? 503 : 200,
                    bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        Client client = new Client(trace);
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "tour-timing", "test", "test");
        var qa = Files.createTempFile("tour-timing-", ".json");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            int event = 0;
            for (String route : List.of("robot", "lingxi")) {
                props.setProperty("tts.mode", route);
                AppConfig config = constructor.newInstance(props);
                NavBridgeClient bridge = new NavBridgeClient(config);
                DoubaoArkClient model = new DoubaoArkClient(config);
                QaStore store = new QaStore(qa.toString());
                register.invoke(null, sdk, config, bridge, model, store);
                for (String mode : List.of("asr2llm", "asr2tts", "audio2llm", "audio2tts")) {
                    for (String text : List.of("开始讲解任务", "请开始一下讲解任务", "欢迎参观", "请为大家介绍一下展厅")) {
                        for (String response : List.of("{\"accepted\":true}", "{\"accepted\":false,\"status\":\"busy\"}",
                                "{\"accepted\":false,\"status\":\"invalid_task\",\"message\":\"任务执行失败\"}",
                                "{}", "{\"accepted\":\"true\"}", "bad-json", "http503")) {
                            SilentStartControlTest.reset();
                            trace.clear(); dispatches.clear(); speech.clear(); client.messages.clear();
                            client.failAt = ""; client.failed = false;
                            receipt.set(response);
                            intent.set("{\"intent\":\"start_task\",\"question\":" + JSON.toJSONString(text) + "}");
                            String id = "tour-timing-" + event++;
                            ControlReplyTextWireTest.invoke(mode, sdk, client, id, text, config, bridge, model, store);
                            String label = route + mode + text + response;
                            check(dispatches.size() == 1, "one task start " + label);
                            check(client.replies().equals(List.of("收到")), "only neutral receipt " + label);
                            check(speech.isEmpty(), "no local status TTS " + label);
                            check(trace.indexOf("agentsdk.llm_response.done") >= 0
                                    && trace.indexOf("agentsdk.llm_response.done") < trace.indexOf("/task/start"),
                                    "SDK response completed before task dispatch " + label);
                            check(trace.indexOf("agentsdk.xlm_response.interrupt") < trace.indexOf("agentsdk.llm_response.item.delta")
                                    && trace.indexOf("agentsdk.llm_response.item.delta") < trace.indexOf("agentsdk.llm_response.done"),
                                    "takeover then neutral receipt then completion " + label);
                            if (mode.startsWith("asr") && (text.startsWith("开始") || text.startsWith("请开始"))) {
                                check(trace.indexOf("agentsdk.llm_response.done") < trace.indexOf("/task/match"),
                                        "builtin reply completed before command lookup " + label);
                            }
                            if (text.equals("欢迎参观")) check(dispatches.get(0).contains("custom-tour"), "custom task preserved");
                            check(client.messages.stream().noneMatch(m -> m.contains("agentsdk.error")
                                    || m.contains("agentsdk.xlm_response.skill")), "no SDK failure or duplicate skill " + label);
                            if (mode.startsWith("asr")) {
                                ControlReplyTextWireTest.invoke(mode.equals("asr2llm") ? "asr2tts" : "asr2llm",
                                        sdk, client, id, text, config, bridge, model, store);
                                check(dispatches.size() == 1 && client.replies().equals(List.of("收到")),
                                        "alternate callback cannot repeat receipt or command " + label);
                            }
                        }
                    }
                    for (String frame : List.of("agentsdk.xlm_response.interrupt", "agentsdk.llm_response.item.delta",
                            "agentsdk.llm_response.item.done", "agentsdk.llm_response.done")) {
                        SilentStartControlTest.reset();
                        trace.clear(); dispatches.clear(); speech.clear(); client.messages.clear();
                        client.failAt = frame; client.failed = false;
                        receipt.set("{\"accepted\":true}");
                        intent.set("{\"intent\":\"start_task\",\"question\":\"开始讲解任务\"}");
                        ControlReplyTextWireTest.invoke(mode, sdk, client, "tour-fault-" + event++, "开始讲解任务",
                                config, bridge, model, store);
                        check(client.failed, "fault injected " + mode + frame);
                        check(dispatches.size() == 1 && speech.isEmpty(), "fault cannot prevent or repeat local start");
                        check(client.messages.stream().noneMatch(m -> m.contains("agentsdk.error")), "fault never becomes failure prompt");
                    }
                    client.failAt = "";
                    for (String text : List.of("如何开始讲解任务", "不要开始讲解任务", "他说开始讲解任务")) {
                        SilentStartControlTest.reset();
                        trace.clear(); dispatches.clear(); speech.clear(); client.messages.clear();
                        intent.set("{\"intent\":\"chat\",\"question\":" + JSON.toJSONString(text) + ",\"reply\":\"普通问答保留\"}");
                        ControlReplyTextWireTest.invoke(mode, sdk, client, "not-tour-" + event++, text, config, bridge, model, store);
                        check(dispatches.isEmpty(), "question/negation/quotation has no task side effect");
                        check(!client.replies().contains("收到"), "noncommand has no tour receipt");
                    }
                    if (mode.startsWith("asr")) {
                        SilentStartControlTest.reset();
                        trace.clear(); dispatches.clear(); speech.clear(); client.messages.clear();
                        conflict.set(true);
                        ControlReplyTextWireTest.invoke(mode, sdk, client, "conflict-" + event++, "开始讲解任务",
                                config, bridge, model, store);
                        check(dispatches.isEmpty() && speech.isEmpty(), "ambiguous task configuration has no side effect or local error voice");
                        check(client.replies().equals(List.of("收到")), "ambiguous task has only neutral input receipt");
                        conflict.set(false);
                        SilentStartControlTest.reset();
                        trace.clear(); dispatches.clear(); speech.clear(); client.messages.clear();
                        protectedStage.set(true);
                        ControlReplyTextWireTest.invoke(mode, sdk, client, "protected-" + event++, "开始讲解任务",
                                config, bridge, model, store);
                        check(dispatches.isEmpty() && client.replies().isEmpty(), "LinkCraft protection precedes receipt");
                        check(!trace.contains("agentsdk.xlm_response.interrupt"), "protected stage is not interrupted");
                        protectedStage.set(false);
                    }
                }
                SilentStartControlTest.reset();
                trace.clear(); dispatches.clear(); speech.clear(); client.messages.clear();
                receipt.set("{\"accepted\":true}");
                intent.set("{\"intent\":\"start_task\"}");
                client.completionEntered = new CountDownLatch(1);
                client.releaseCompletion = new CountDownLatch(1);
                client.blocked.set(false);
                CountDownLatch observed = new CountDownLatch(1);
                startObserved.set(observed);
                String id = "tour-concurrent-" + route;
                CompletableFuture<Void> first = CompletableFuture.runAsync(() ->
                        request("asr2llm", sdk, client, id, config, bridge, model, store));
                CompletableFuture<Void> second = null;
                try {
                    check(client.completionEntered.await(3, TimeUnit.SECONDS), "first response completion was blocked");
                    second = CompletableFuture.runAsync(() ->
                            request("asr2tts", sdk, client, id, config, bridge, model, store));
                    check(!observed.await(150, TimeUnit.MILLISECONDS), "concurrent callback cannot dispatch before receipt completes");
                } finally {
                    client.releaseCompletion.countDown();
                    first.get(6, TimeUnit.SECONDS);
                    if (second != null) second.get(6, TimeUnit.SECONDS);
                    client.completionEntered = null;
                    client.releaseCompletion = null;
                    startObserved.set(null);
                }
                check(dispatches.size() == 1 && client.replies().equals(List.of("收到")),
                        "concurrent callbacks share one receipt and one physical start");
                check(trace.indexOf("agentsdk.llm_response.done") < trace.indexOf("/task/start"),
                        "concurrent dispatch follows actual response completion");
            }
        } finally { http.stop(0); sdk.release(); client.stop(); Files.deleteIfExists(qa); }
        if (!failures.isEmpty()) throw new AssertionError("Tour takeover failures " + failures.size() + "/" + checks
                + "\n" + String.join("\n", failures.subList(0, Math.min(20, failures.size()))));
        System.out.println("TourStartTakeoverTest passed: " + checks + " assertions");
    }

    private static void request(String mode, AgentSdk sdk, Client client, String id, AppConfig config,
                                NavBridgeClient bridge, DoubaoArkClient model, QaStore store) {
        try {
            ControlReplyTextWireTest.invoke(mode, sdk, client, id, "开始讲解任务", config, bridge, model, store);
        } catch (Exception exception) {
            throw new java.util.concurrent.CompletionException(exception);
        }
    }

    private static final class Client extends LinkskyClient {
        final List<String> messages = new CopyOnWriteArrayList<>();
        final List<String> trace;
        String failAt = "";
        boolean failed;
        volatile CountDownLatch completionEntered;
        volatile CountDownLatch releaseCompletion;
        final AtomicBoolean blocked = new AtomicBoolean(false);
        Client(List<String> trace) { super((AgentSdk) null); this.trace = trace; }
        List<String> replies() {
            return messages.stream().map(JSON::parseObject)
                    .filter(m -> "agentsdk.llm_response.item.delta".equals(m.getString("type")))
                    .map(m -> m.getString("text")).toList();
        }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            String type = JSON.parseObject(payload).getString("type");
            if (type.equals("agentsdk.llm_response.done") && completionEntered != null
                    && blocked.compareAndSet(false, true)) {
                completionEntered.countDown();
                try {
                    if (!releaseCompletion.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("completion block expired");
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException(interrupted);
                }
            }
            messages.add(payload);
            trace.add(type);
            if (type.equals(failAt) && !failed) { failed = true; throw new IllegalStateException("injected SDK send failure"); }
        }
    }
}
