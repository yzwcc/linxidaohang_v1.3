package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Exercises production ASR-text intent and bridge transports with local HTTP peers. */
public final class FunctionalControlTest {
    private static int checks;
    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
    }

    public static void main(String[] args) throws Exception {
        for (String text : List.of("不要握手", "别给我比心", "你会握手吗", "我喜欢爱心", "他说握个手", "如果握手会怎样")) {
            check(PresetMotionCatalog.resolveVoiceCommand(text) == null, "not an imperative: " + text);
        }
        check(PresetMotionCatalog.resolveVoiceCommand("能不能跟我握一下手") != null, "polite request is not negation");
        check(NavigationCommandFallback.resolve("灵犀，请带我们去大厅", List.of("大厅")) != null, "exact destination fallback");
        for (String text : List.of("不要带我去大厅", "带我去大厅好吗", "大厅在哪", "带我去大厅和门口", "带我去不存在", "如果带我去大厅")) {
            check(NavigationCommandFallback.resolve(text, List.of("大厅", "门口")) == null, "no destination guessing: " + text);
        }
        check(NavigationCommandFallback.resolve("带我去大厅", List.of("大厅", "大 厅")) == null, "ambiguous catalog refused");

        AtomicInteger motions = new AtomicInteger();
        AtomicInteger navigations = new AtomicInteger();
        AtomicInteger modelCalls = new AtomicInteger();
        AtomicBoolean modelOffline = new AtomicBoolean();
        AtomicBoolean rejectMotion = new AtomicBoolean();
        AtomicReference<String> intent = new AtomicReference<>("{\"intent\":\"preset_motion\",\"target_name\":\"handshake\",\"motion_area\":\"right_hand\"}");
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = "{\"ok\":true}";
            int code = 200;
            switch (path) {
                case "/motion/play" -> {
                    motions.incrementAndGet();
                    try { Thread.sleep(300); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
                    body = rejectMotion.get()
                            ? "{\"accepted\":false,\"status\":\"rejected\",\"message\":\"motion=1003, area=2 机器人动作服务拒绝请求\"}"
                            : "{\"accepted\":true,\"status\":\"accepted\"}";
                }
                case "/points" -> body = "{\"ok\":true,\"point_names\":[\"大厅\",\"门口\"]}";
                case "/nav/start" -> { navigations.incrementAndGet(); body = "{\"accepted\":true}"; }
                case "/model" -> {
                    modelCalls.incrementAndGet();
                    check(JSONObject.parseObject(input).getJSONArray("messages").getJSONObject(1).get("content") instanceof String,
                            "model receives text only");
                    if (modelOffline.get()) code = 503;
                    body = "{\"choices\":[{\"message\":{\"content\":" + JSONObject.toJSONString(intent.get()) + "}}]}";
                }
                default -> { }
            }
            byte[] data = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(code, data.length);
            exchange.getResponseBody().write(data);
            exchange.close();
        });
        server.start();
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model")) props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + server.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            props.setProperty("nav.timeoutMs", "100");
            AppConfig fastConfig = constructor.newInstance(props);
            check(new NavBridgeClient(fastConfig).presetMotion("slow-ack", "handshake", "right_hand").getBooleanValue("accepted"),
                    "motion acknowledgement can exceed ordinary navigation timeout");
            check(motions.get() == 1, "slow acknowledgement never duplicated");
            props.setProperty("nav.timeoutMs", "5000");
            AppConfig config = constructor.newInstance(props);
            NavBridgeClient bridge = new NavBridgeClient(config);
            DoubaoArkClient model = new DoubaoArkClient(config);
            Method process = LingxiNavAgentApp.class.getDeclaredMethod("processTextRequest", String.class, String.class,
                    AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class, AtomicBoolean.class);
            process.setAccessible(true);
            Object result = process.invoke(null, "flex-motion", "咱俩友好地握一下手怎么样", config, bridge, model, null, new AtomicBoolean());
            Method reply = result.getClass().getDeclaredMethod("reply"); reply.setAccessible(true);
            check(!((String) reply.invoke(result)).contains("失败") && motions.get() == 2 && modelCalls.get() == 1,
                    "flexible motion intent goes through model and acknowledges once");
            process.invoke(null, "negative-motion", "不要握手", config, bridge, model, null, new AtomicBoolean());
            check(motions.get() == 2, "negative request cannot execute even if model misclassifies");
            rejectMotion.set(true);
            result = process.invoke(null, "rejected-motion", "握个手", config, bridge, model, null, new AtomicBoolean());
            check(((String) reply.invoke(result)).contains("未被接受") && !((String) reply.invoke(result)).contains("motion="),
                    "real rejection retained without reading protocol details aloud");
            check(LingxiNavAgentApp.motionFailureReply(JSONObject.parseObject("{\"status\":\"confirmation_unknown\"}")).contains("暂未收到"),
                    "unknown acknowledgement is not physical failure");
            modelOffline.set(true);
            result = process.invoke(null, "offline-nav", "带我去大厅", config, bridge, model, null, new AtomicBoolean());
            check(navigations.get() == 1 && ((String) reply.invoke(result)).contains("大厅"), "explicit exact navigation survives model outage");
            result = process.invoke(null, "offline-unknown", "带我去不存在", config, bridge, model, null, new AtomicBoolean());
            check(navigations.get() == 1 && ((String) reply.invoke(result)).contains("暂时不可用"), "unknown destination not sent");
            modelOffline.set(false);
            intent.set("{\"intent\":\"navigate\",\"target_name\":\"门口\"}");
            process.invoke(null, "model-recovered", "请引领我前往入口处", config, bridge, model, null, new AtomicBoolean());
            check(navigations.get() == 2, "flexible model navigation resumes after outage");
        } finally { server.stop(0); }
        System.out.println("FunctionalControlTest passed: " + checks + " assertions");
    }
}
