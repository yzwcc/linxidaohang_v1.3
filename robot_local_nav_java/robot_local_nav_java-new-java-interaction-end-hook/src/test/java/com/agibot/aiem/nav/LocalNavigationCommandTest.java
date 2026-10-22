package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

final class LocalNavigationCommandTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    private static void checkTarget(String text, List<String> names, String expected) {
        NavigationIntent intent = LocalNavigationCommand.resolve(text, names);
        check(intent != null && intent.isNavigate(), "recognized navigation command: " + text);
        check(expected.equals(intent.targetName), "configured point name returned: " + text);
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

    private static void testExactCommandsAndGuards() {
        List<String> names = List.of("大门", "正门", "办公室A点", "B-1");
        checkTarget("请带我去正门吧。", names, "正门");
        checkTarget("麻烦你带我到办公室A点一下", names, "办公室A点");
        checkTarget("导航到B-1", names, "B-1");
        checkTarget("我想去大门，谢谢", names, "大门");
        checkTarget("请 带我 去 a-1", List.of("A-1"), "A-1");
        check(LocalNavigationCommand.resolve("大门在哪里", names) == null,
                "point question is not treated as a command");
        check(LocalNavigationCommand.resolve("请带我去正门？", names) == null,
                "question punctuation falls back to the intent model");
        check(LocalNavigationCommand.resolve("去大门吗", names) == null,
                "question particle is not stripped as a polite suffix");
        check(LocalNavigationCommand.resolve("请带我去正门可以吗", names) == null,
                "polite yes-no question falls back to the intent model");
        check(LocalNavigationCommand.resolve("\"请带我去正门\"", names) == null,
                "quoted navigation text is not executed");
        check(LocalNavigationCommand.resolve("不要带我去正门", names) == null,
                "negated command is not routed");
        check(LocalNavigationCommand.resolve("如果带我去正门", names) == null,
                "conditional statement is not routed");
        check(LocalNavigationCommand.resolve("带我去大门口", names) == null,
                "partial point-name matches are rejected");
        check(LocalNavigationCommand.resolve("带我去医院", names) == null,
                "unknown destinations fall back to the existing intent path");
        check(LocalNavigationCommand.resolve("去大门", List.of()) == null,
                "empty active-map point list disables the local route");
    }

    private static void testTextFastPath() throws Exception {
        AtomicInteger modelRequests = new AtomicInteger();
        AtomicInteger pointRequests = new AtomicInteger();
        AtomicInteger interactionMarks = new AtomicInteger();
        AtomicInteger navigationRequests = new AtomicInteger();
        AtomicReference<String> targetName = new AtomicReference<>();

        HttpServer modelServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        modelServer.createContext("/chat/completions", exchange -> {
            modelRequests.incrementAndGet();
            byte[] body = ("{\"choices\":[{\"message\":{\"content\":\""
                    + "{\"intent\":\"chat\",\"question\":\"请带我去正门\","
                    + "\"reply\":\"测试回复\"}".replace("\"", "\\\"")
                    + "\"}}]}").getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        modelServer.start();

        HttpServer navServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        navServer.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            byte[] request = exchange.getRequestBody().readAllBytes();
            String response;
            if ("/points".equals(path)) {
                pointRequests.incrementAndGet();
                response = "{\"ok\":true,\"active_map_name\":\"floor-a\","
                        + "\"point_names\":[\"入口\",\"正门\",\"大门\"]}";
            } else if ("/interaction/mark".equals(path)) {
                interactionMarks.incrementAndGet();
                response = "{\"ok\":true,\"state\":\"active\"}";
            } else if ("/nav/start".equals(path)) {
                navigationRequests.incrementAndGet();
                targetName.set(JSONObject.parseObject(
                        new String(request, StandardCharsets.UTF_8)).getString("target_name"));
                response = "{\"ok\":true,\"accepted\":true,\"status\":\"accepted\"}";
            } else {
                response = "{\"ok\":false,\"message\":\"unexpected path\"}";
            }
            byte[] body = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        navServer.start();

        try {
            String navUrl = "http://127.0.0.1:" + navServer.getAddress().getPort();
            AppConfig config = config(
                    "http://127.0.0.1:" + modelServer.getAddress().getPort() + "/chat/completions",
                    navUrl);
            Method processText = LingxiNavAgentApp.class.getDeclaredMethod(
                    "processTextRequest",
                    String.class,
                    String.class,
                    AppConfig.class,
                    NavBridgeClient.class,
                    DoubaoArkClient.class,
                    QaStore.class,
                    AtomicBoolean.class);
            processText.setAccessible(true);
            Object result = processText.invoke(
                    null,
                    "local-navigation-test",
                    "请带我去正门吧",
                    config,
                    new NavBridgeClient(config),
                    new DoubaoArkClient(config),
                    null,
                    new AtomicBoolean());
            Field reply = result.getClass().getDeclaredField("reply");
            reply.setAccessible(true);

            check(modelRequests.get() == 0, "matched transcript skips the intent model request");
            check(pointRequests.get() == 1, "active-map voice names are fetched once");
            check(interactionMarks.get() == 1, "local route keeps interaction bookkeeping");
            check(navigationRequests.get() == 1, "local route submits one navigation request");
            check("正门".equals(targetName.get()), "the configured alias is passed to the bridge");
            check("好的，我带你去正门".equals(reply.get(result)),
                    "local navigation receives the existing navigation acknowledgement");
        } finally {
            navServer.stop(0);
            modelServer.stop(0);
        }
    }

    public static void main(String[] args) throws Exception {
        testExactCommandsAndGuards();
        testTextFastPath();
        System.out.println("LocalNavigationCommandTest passed: " + checks + " assertions");
    }
}
