package com.agibot.aiem.nav;

import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicInteger;

final class LingxiChatFlowTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    private static AppConfig config(String modelUrl, String navUrl) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test-model");
        properties.setProperty("doubao.url", modelUrl);
        properties.setProperty("nav.baseUrl", navUrl);
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    public static void main(String[] args) throws Exception {
        AtomicInteger requests = new AtomicInteger();
        AtomicInteger promptHasGroundingRules = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/chat/completions", exchange -> {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            if (body.contains("不要编造") && body.contains("reply")) {
                promptHasGroundingRules.incrementAndGet();
            }
            requests.incrementAndGet();
            String content = "{\"intent\":\"chat\",\"question\":\"为什么天空是蓝色的？\","
                    + "\"reply\":\"天空呈蓝色，主要是因为大气对短波蓝光的散射更明显。\"}";
            String response = "{\"choices\":[{\"message\":{\"content\":"
                    + com.alibaba.fastjson2.JSON.toJSONString(content) + "}}]}";
            byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
            AppConfig config = config(baseUrl + "/chat/completions", baseUrl);
            DoubaoArkClient doubao = new DoubaoArkClient(config);
            NavigationIntent intent = doubao.parseInteraction("为什么天空是蓝色的？", List.of());
            Method handleIntent = LingxiNavAgentApp.class.getDeclaredMethod(
                    "handleIntent",
                    String.class,
                    String.class,
                    NavigationIntent.class,
                    NavBridgeClient.class,
                    DoubaoArkClient.class,
                    QaStore.class,
                    boolean.class);
            handleIntent.setAccessible(true);
            Object result = handleIntent.invoke(
                    null, "chat-flow-test", intent.question, intent, null, doubao, null, false);
            Field replyField = result.getClass().getDeclaredField("reply");
            replyField.setAccessible(true);

            check("天空呈蓝色，主要是因为大气对短波蓝光的散射更明显。"
                    .equals(replyField.get(result)), "parsed chat reply becomes the final response");
            check(requests.get() == 1, "ordinary text chat uses one model request");
            check(promptHasGroundingRules.get() == 1, "intent prompt carries concise grounding rules");
        } finally {
            server.stop(0);
        }
        System.out.println("LingxiChatFlowTest passed: " + checks + " assertions");
    }
}
