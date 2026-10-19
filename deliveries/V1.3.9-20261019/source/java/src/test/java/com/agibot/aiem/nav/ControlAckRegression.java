package com.agibot.aiem.nav;

import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Fault injection against the production command path and real HTTP client. */
public final class ControlAckRegression {
    private static int checks;

    private static void check(boolean condition, String description) {
        if (!condition) throw new AssertionError(description);
        checks++;
    }

    public static void main(String[] args) throws Exception {
        AtomicReference<String> response = new AtomicReference<>("{}");
        AtomicInteger status = new AtomicInteger(200);
        AtomicInteger requests = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            requests.incrementAndGet();
            exchange.getRequestBody().readAllBytes();
            byte[] bytes = response.get().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status.get(), bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret",
                    "doubao.apiKey", "doubao.model")) props.setProperty(key, "fixture");
            props.setProperty("nav.baseUrl", "http://127.0.0.1:" + server.getAddress().getPort());
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            NavBridgeClient bridge = new NavBridgeClient(constructor.newInstance(props));
            Method handle = LingxiNavAgentApp.class.getDeclaredMethod(
                    "handleIntent", String.class, String.class, NavigationIntent.class,
                    NavBridgeClient.class, DoubaoArkClient.class, QaStore.class, boolean.class);
            handle.setAccessible(true);
            String[] fields = {"accepted", "cancelled", "accepted", "paused", "resumed", "stopped", "accepted", "accepted"};
            NavigationIntent[] intents = {
                NavigationIntent.fromJsonText("{\"intent\":\"navigate\",\"target_name\":\"fixture\",\"reply\":\"success\"}"),
                NavigationIntent.stop(), NavigationIntent.startTask(), NavigationIntent.pauseTask(),
                NavigationIntent.resumeTask(), NavigationIntent.endTask(),
                NavigationIntent.dance("fixture", "success"), NavigationIntent.presetMotion("handshake", "right_hand"),
            };
            for (int index = 0; index < intents.length; index++) {
                String field = fields[index];
                for (String body : List.of("{}", "null", "{\"" + field + "\":null}",
                        "{\"" + field + "\":\"true\"}", "{\"" + field + "\":\"false\"}",
                        "{\"" + field + "\":1}", "{\"" + field + "\":0}",
                        "{\"ok\":false,\"" + field + "\":true}", "invalid-json")) {
                    response.set(body);
                    requests.set(0);
                    Object result = handle.invoke(null, "fault-" + index, "fixture", intents[index], bridge, null, null, true);
                    Method reply = result.getClass().getDeclaredMethod("reply");
                    reply.setAccessible(true);
                    check("".equals(reply.invoke(result)), "uncertain response must be silent: " + index + " " + body);
                    check(requests.get() == 1, "uncertain command must never be retried");
                }
                response.set("{\"ok\":true,\"" + field + "\":true}");
                Object result = handle.invoke(null, "accepted-" + index, "fixture", intents[index], bridge, null, null, true);
                Method accepted = result.getClass().getDeclaredMethod("localPerformanceAccepted");
                accepted.setAccessible(true);
                check(Boolean.TRUE.equals(accepted.invoke(result)), "accepted command survives late SDK completion failure");
                response.set("{\"ok\":false,\"" + field + "\":false}");
                result = handle.invoke(null, "rejected-" + index, "fixture", intents[index], bridge, null, null, true);
                Method reply = result.getClass().getDeclaredMethod("reply");
                reply.setAccessible(true);
                check(!"success".equals(reply.invoke(result)), "rejected command must not repeat model success");
                for (int code : List.of(500, 503)) {
                    status.set(code);
                    requests.set(0);
                    result = handle.invoke(null, "http-fault-" + index, "fixture", intents[index], bridge, null, null, true);
                    check("".equals(reply.invoke(result)), "HTTP failure after dispatch is uncertain");
                    check(requests.get() == 1, "HTTP failure must not retry a possibly accepted command");
                }
                status.set(200);
            }
            Method taskRule = LingxiNavAgentApp.class.getDeclaredMethod("taskControlIntentFromText", String.class, NavBridgeClient.class);
            taskRule.setAccessible(true);
            requests.set(0);
            for (String text : List.of("不要开始讲解", "先别继续讲解", "别暂停讲解", "如果暂停讲解会怎样", "如何开始讲解", "他说“停止讲解”")) {
                check(taskRule.invoke(null, text, bridge) == null, "non-command must not execute: " + text);
            }
            check(requests.get() == 0, "non-command must not request a task match");
            response.set("{\"ok\":true,\"cancelled\":true}");
            Object stop = handle.invoke(null, "negative-safety-stop", "不要走", NavigationIntent.stop(), bridge, null, null, true);
            check(requests.get() == 1, "negative-form safety stop still reaches hardware");
            Method stopReply = stop.getClass().getDeclaredMethod("reply");
            stopReply.setAccessible(true);
            check(!"".equals(stopReply.invoke(stop)), "confirmed safety stop is acknowledged");
            System.out.println("ControlAckRegression passed: " + checks + " assertions");
        } finally {
            server.stop(0);
        }
    }
}
