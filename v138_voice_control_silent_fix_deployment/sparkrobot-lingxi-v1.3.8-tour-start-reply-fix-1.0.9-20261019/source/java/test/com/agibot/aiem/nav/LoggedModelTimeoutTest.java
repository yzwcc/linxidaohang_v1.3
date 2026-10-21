package com.agibot.aiem.nav;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** Exercise the field-log HttpTimeoutException using a real nonresponding HTTP peer. */
public final class LoggedModelTimeoutTest {
    public static void main(String[] args) throws Exception {
        AtomicInteger navigations = new AtomicInteger();
        AtomicInteger models = new AtomicInteger();
        List<HttpExchange> pending = new CopyOnWriteArrayList<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            exchange.getRequestBody().readAllBytes();
            String path = exchange.getRequestURI().getPath();
            if (path.equals("/model")) {
                models.incrementAndGet();
                pending.add(exchange);
                return;
            }
            String response = switch (path) {
                case "/points" -> "{\"ok\":true,\"point_names\":[\"魔箱\"]}";
                case "/task/match" -> "{\"matched\":false}";
                case "/nav/start" -> {
                    navigations.incrementAndGet();
                    yield "{\"accepted\":true}";
                }
                default -> "{\"ok\":true}";
            };
            byte[] data = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, data.length);
            exchange.getResponseBody().write(data);
            exchange.close();
        });
        server.start();
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + server.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(props);
            Method process = LingxiNavAgentApp.class.getDeclaredMethod("processTextRequest", String.class, String.class,
                    AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class, AtomicBoolean.class);
            process.setAccessible(true);
            for (String text : List.of("带我去魔箱", "介绍一下你自己")) {
                long start = System.nanoTime();
                Object result = process.invoke(null, "logged-model-timeout", text, config,
                        new NavBridgeClient(config), new DoubaoArkClient(config), null, new AtomicBoolean());
                long elapsed = (System.nanoTime() - start) / 1_000_000L;
                Method reply = result.getClass().getDeclaredMethod("reply");
                reply.setAccessible(true);
                String spoken = (String) reply.invoke(result);
                if (text.startsWith("带我")) {
                    if (navigations.get() != 1 || !spoken.isBlank() || elapsed > 3000 || models.get() != 0)
                        throw new AssertionError("exact configured navigation must bypass classifier and stay silent");
                } else {
                    if (elapsed < 14000 || elapsed > 25000)
                        throw new AssertionError("must exercise the production 15-second HTTP timeout: " + elapsed);
                    if (!spoken.contains("暂时不可用") || navigations.get() != 1)
                        throw new AssertionError("chat timeout must return availability notice without a robot command");
                }
            }
            if (models.get() != 1) throw new AssertionError("timeout must not replay model/action requests");
            System.out.println("LoggedModelTimeoutTest passed: real 15s chat timeout, exact navigation without model wait, no repeat dispatch");
        } finally {
            for (HttpExchange exchange : pending) exchange.close();
            server.stop(0);
        }
    }
}
