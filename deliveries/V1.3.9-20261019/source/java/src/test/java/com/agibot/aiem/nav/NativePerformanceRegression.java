package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.agibot.aiem.sdk.enums.AgentCallbackType;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.alibaba.fastjson2.JSON;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicInteger;

/** Native singing/dancing must have one owner, with no local media takeover. */
public final class NativePerformanceRegression {
    private static int checks;

    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
    }

    public static void main(String[] args) throws Exception {
        Method handle = LingxiNavAgentApp.class.getDeclaredMethod("handleIntent",
                String.class, String.class, NavigationIntent.class, NavBridgeClient.class,
                DoubaoArkClient.class, QaStore.class, boolean.class);
        handle.setAccessible(true);
        Class<?> senderType = Class.forName("com.agibot.aiem.nav.LingxiNavAgentApp$InterruptSender");
        Method factory = LingxiNavAgentApp.class.getDeclaredMethod("nativeTourActionInterrupt",
                String.class, String.class, senderType);
        factory.setAccessible(true);
        for (String text : List.of("唱歌", "唱首歌", "来一首歌", "跳舞", "跳个舞", "跳太极", "dance", "sing")) {
            Object sender = Proxy.newProxyInstance(senderType.getClassLoader(), new Class<?>[]{senderType},
                    (proxy, method, values) -> { throw new AssertionError("native performance interrupted: " + text); });
            ((Runnable) factory.invoke(null, "native-" + checks, text, sender)).run();
            checks++;
            NavigationIntent intent = text.contains("舞") || text.contains("太极") || text.equals("dance")
                    ? NavigationIntent.dance("model-success") : NavigationIntent.sing("model-success");
            // A null bridge proves neither local HTTP media playback nor resource submission occurs.
            Object result = handle.invoke(null, "native-" + checks, text, intent, null, null, null, true);
            check("".equals(access(result, "reply")), "no disable/prepare/duplicate speech: " + text);
            check("".equals(access(result, "skillAction")), "no second native skill submission");
        }
        Method local = LingxiNavAgentApp.class.getDeclaredMethod("localEntertainmentIntentFromRequest",
                String.class, NavigationIntent.class);
        local.setAccessible(true);
        check(local.invoke(null, "今天天气如何", NavigationIntent.chat("我也可以跳舞")) == null,
                "model reply cannot turn an unrelated question into a dance");
        for (String text : List.of("不要跳舞", "如果唱歌会怎样", "如何跳舞", "你会唱歌吗", "他说“跳舞”")) {
            check(local.invoke(null, text, NavigationIntent.chat("唱歌")) == null,
                    "non-command cannot hijack native performance: " + text);
        }
        callbackWire();
        System.out.println("NativePerformanceRegression passed: " + checks + " assertions");
    }

    private static void callbackWire() throws Exception {
        AtomicInteger unexpected = new AtomicInteger();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            exchange.getRequestBody().readAllBytes();
            if (!exchange.getRequestURI().getPath().equals("/interaction/wake")) unexpected.incrementAndGet();
            byte[] bytes = "{\"handled\":false,\"accepted\":true}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        CapturingClient client = new CapturingClient();
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "fixture", "fixture", "fixture");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "fixture");
            props.setProperty("nav.baseUrl", "http://127.0.0.1:" + http.getAddress().getPort());
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            int event = 0;
            for (String route : List.of("lingxi", "robot")) {
                props.setProperty("tts.mode", route);
                AppConfig config = constructor.newInstance(props);
                register.invoke(null, sdk, config, new NavBridgeClient(config), null, null);
                Asr2LlmCallback llm = (Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM);
                Asr2TtsCallback tts = (Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS);
                for (String mode : List.of("llm", "tts")) {
                    for (String text : List.of("唱首歌", "跳个舞", "跳太极")) {
                        client.messages.clear();
                        var dedup = LingxiNavAgentApp.class.getDeclaredField("LOCAL_COMMAND_DEDUP");
                        dedup.setAccessible(true);
                        ((Map<?, ?>) dedup.get(null)).clear();
                        String id = "native-wire-" + event++;
                        if (mode.equals("llm")) {
                            llm.onRequest("fixture", id, text, VoiceGateTest.acceptedParams(),
                                    new Asr2LlmResponse(client, "fixture", "robot"));
                        } else {
                            tts.onRequest("fixture", id, text, VoiceGateTest.acceptedParams(),
                                    new Asr2TtsResponse(client, "fixture", "robot"));
                        }
                        check(client.messages.stream().noneMatch(s -> s.contains("interrupt")
                                || s.contains("item.delta") || s.contains("error") || s.contains("skill")),
                                "SDK wire retains native performance with no speech/error/interrupt/skill: " + route + mode + text);
                        check(client.messages.stream().anyMatch(s -> s.contains("llm_response.done")),
                                "SDK callback completes without stranding its event");
                    }
                }
            }
            check(unexpected.get() == 0, "native performance never calls model/local playback/motion/TTS/task matching");
        } finally {
            client.stop();
            http.stop(0);
        }
    }

    private static final class CapturingClient extends LinkskyClient {
        private final List<String> messages = new ArrayList<>();
        CapturingClient() { super((AgentSdk) null); }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            JSON.parseObject(payload);
            messages.add(payload);
        }
    }

    private static Object access(Object value, String name) throws Exception {
        Method method = value.getClass().getDeclaredMethod(name);
        method.setAccessible(true);
        return method.invoke(value);
    }
}
