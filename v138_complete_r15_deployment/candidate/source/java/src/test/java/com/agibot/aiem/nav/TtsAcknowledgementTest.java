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

/** Actual HTTP receipts must not produce duplicate SDK speech after a late ack. */
public final class TtsAcknowledgementTest {
    private static int checks;
    private static void check(boolean value, String label) {
        checks++;
        if (!value) throw new AssertionError(label);
    }

    public static void main(String[] args) throws Exception {
        AtomicReference<String> body = new AtomicReference<>();
        AtomicInteger requests = new AtomicInteger();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/tts/speak", exchange -> {
            exchange.getRequestBody().readAllBytes();
            requests.incrementAndGet();
            byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret",
                    "doubao.apiKey", "doubao.model")) props.setProperty(key, "test");
            props.setProperty("tts.mode", "robot");
            props.setProperty("tts.suppressLingxiText", "true");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            props.setProperty("nav.baseUrl", "http://127.0.0.1:" + http.getAddress().getPort());
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(props);
            NavBridgeClient bridge = new NavBridgeClient(config);
            Class<?> priority = Class.forName("com.agibot.aiem.nav.LingxiNavAgentApp$TtsPriority");
            Class<?> resultClass = Class.forName("com.agibot.aiem.nav.LingxiNavAgentApp$RobotTtsResult");
            Object normal = priority.getEnumConstants()[0];
            Method speak = LingxiNavAgentApp.class.getDeclaredMethod("speakIfNeeded",
                    AppConfig.class, String.class, String.class, priority, NavBridgeClient.class);
            Method fallback = LingxiNavAgentApp.class.getDeclaredMethod("shouldReturnLingxiText",
                    AppConfig.class, priority, resultClass);
            Method reset = ControlReplyTextWireTest.class.getDeclaredMethod("reset");
            Method accepted = resultClass.getDeclaredMethod("accepted");
            Method uncertain = resultClass.getDeclaredMethod("deliveryUncertain");
            for (Method method : List.of(speak, fallback, reset, accepted, uncertain)) method.setAccessible(true);
            String[] receipts = {
                "{\"accepted\":true,\"status\":\"accepted\"}",
                "{\"accepted\":false,\"status\":\"rejected\",\"message\":\"priority_rejected\"}",
                "{\"accepted\":null,\"status\":\"ack_pending\"}",
                "{\"accepted\":null,\"status\":\"ack_unknown\"}",
                "{}",
                "{\"accepted\":\"true\"}",
                "{\"accepted\":1}",
            };
            for (boolean fixed : List.of(false, true)) {
                props.setProperty("tts.fixedDialogTest", Boolean.toString(fixed));
                config = constructor.newInstance(props);
                for (int i = 0; i < receipts.length; i++) {
                    reset.invoke(null);
                    body.set(receipts[i]);
                    int before = requests.get();
                    Object result = speak.invoke(null, config, "ack-" + fixed + i, "answer", normal, bridge);
                    check((boolean) accepted.invoke(result) == (i == 0), "real boolean success only");
                    check((boolean) uncertain.invoke(result) == (i >= 2), "missing ack stays uncertain");
                    check((boolean) fallback.invoke(null, config, normal, result) == (i == 1),
                            "SDK fallback only on definitive rejection");
                    check(requests.get() == before + 1, "Java never resubmits pending speech");
                }
            }
            System.out.println("TTS acknowledgement checks passed: " + checks);
        } finally {
            http.stop(0);
        }
    }
}
