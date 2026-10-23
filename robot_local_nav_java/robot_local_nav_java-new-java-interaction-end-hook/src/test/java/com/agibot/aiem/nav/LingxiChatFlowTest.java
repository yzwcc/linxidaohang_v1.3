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
import java.util.concurrent.atomic.AtomicReference;

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
        AtomicReference<String> latestRequestBody = new AtomicReference<>("");
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/chat/completions", exchange -> {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            latestRequestBody.set(body);
            if (body.contains("使用模型已有知识正常回答") && body.contains("reply")) {
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
            String textPrompt = latestRequestBody.get();
            check(textPrompt.contains("使用模型已有知识正常回答"),
                    "text intent prompt explicitly permits model knowledge for ordinary questions");
            check(textPrompt.contains("不要因为当前上下文或知识库没有提供常识答案而拒答"),
                    "missing local context does not block general answers");
            check(textPrompt.contains("只有用户询问本机机器人的专属状态/能力"),
                    "grounding refusal is limited to local and current facts");
            check(!textPrompt.contains("上下文和可用资料没有依据时，明确说目前无法核实"),
                    "text prompt does not apply a blanket no-evidence refusal");

            Method audioPromptBuilder = DoubaoArkClient.class.getDeclaredMethod(
                    "buildAudioSystemPrompt", List.class);
            audioPromptBuilder.setAccessible(true);
            String audioPrompt = (String) audioPromptBuilder.invoke(doubao, List.of());
            check(audioPrompt.contains("使用模型已有知识正常回答"),
                    "audio intent prompt permits model knowledge for ordinary questions");
            check(!audioPrompt.contains("不确定、听不清或涉及实时、场馆、企业、机器人专属事实"),
                    "audio prompt does not turn all uncertainty into a refusal");

            doubao.chat("什么是机器人？");
            String fallbackPrompt = latestRequestBody.get();
            check(fallbackPrompt.contains("使用模型已有知识正常回答"),
                    "chat fallback prompt permits model knowledge for ordinary questions");
            check(requests.get() == 2, "chat fallback makes only its requested model call");
        } finally {
            server.stop(0);
        }
        System.out.println("LingxiChatFlowTest passed: " + checks + " assertions");
    }
}
