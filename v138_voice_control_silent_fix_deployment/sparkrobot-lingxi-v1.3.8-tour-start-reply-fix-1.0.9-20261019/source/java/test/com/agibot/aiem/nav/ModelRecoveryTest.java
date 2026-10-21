package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.Properties;
import java.util.List;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;

/** Local HTTP tests exercise the production text-to-model path without credentials. */
public final class ModelRecoveryTest {
    public static void main(String[] args) throws Exception {
        AtomicInteger mode = new AtomicInteger();
        AtomicInteger requests = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/model", exchange -> {
            int call = requests.incrementAndGet();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            JSONObject request = JSONObject.parseObject(input);
            if (!(request.getJSONArray("messages").getJSONObject(1).get("content") instanceof String))
                throw new AssertionError("model must receive ASR text");
            int status = mode.get() == 1 ? 401 : mode.get() == 2 ? 503 : mode.get() == 3 && call == 1 ? 503 : 200;
            String reply = mode.get() == 4 ? "not-json" : "{\"intent\":\"chat\",\"question\":\"问题\",\"reply\":\"回答\"}";
            byte[] body = (mode.get() == 5 ? "{\"choices\":[null]}" : "{\"choices\":[{\"message\":{\"content\":" + JSONObject.toJSONString(reply) + "}}]}").getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.createContext("/task/match", exchange -> {
            exchange.sendResponseHeaders(503, -1); exchange.close();
        });
        server.createContext("/points", exchange -> {
            exchange.sendResponseHeaders(503, -1); exchange.close();
        });
        server.start();
        Path qa = Files.createTempFile("model-recovery-", ".json");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model")) props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + server.getAddress().getPort();
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("nav.baseUrl", base);
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(props);
            DoubaoArkClient model = new DoubaoArkClient(config);
            for (int value : new int[]{0, 3}) {
                mode.set(value); requests.set(0);
                if (!"chat".equals(model.parseInteraction("问题", List.of()).intent)) throw new AssertionError("recovery");
                if (requests.get() != (value == 3 ? 2 : 1)) throw new AssertionError("retry bound");
            }
            Method process = LingxiNavAgentApp.class.getDeclaredMethod("processTextRequest", String.class, String.class,
                    AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class, AtomicBoolean.class);
            process.setAccessible(true);
            for (int value : new int[]{1, 2, 4, 5}) {
                mode.set(value); requests.set(0);
                Object result = process.invoke(null, "failure", "介绍一下机器人", config, new NavBridgeClient(config), model, null, new AtomicBoolean());
                Method reply = result.getClass().getDeclaredMethod("reply"); reply.setAccessible(true);
                if (!((String) reply.invoke(result)).contains("暂时不可用")) throw new AssertionError("failure must produce user reply");
                if (requests.get() != (value == 2 ? 2 : 1)) throw new AssertionError("no auth/format retry");
            }
            mode.set(0); requests.set(0);
            Object recovered = process.invoke(null, "recovered", "介绍一下机器人", config, new NavBridgeClient(config), model, new QaStore(qa.toString()), new AtomicBoolean());
            Method recoveredReply = recovered.getClass().getDeclaredMethod("reply"); recoveredReply.setAccessible(true);
            if (((String) recoveredReply.invoke(recovered)).contains("暂时不可用") || requests.get() != 2)
                throw new AssertionError("next production request must recover despite bridge outage");
            System.out.println("ModelRecoveryTest passed: text input, transient retry, auth rejection, malformed intent, bridge outage, next-request recovery");
        } finally { server.stop(0); Files.deleteIfExists(qa); }
    }
}
