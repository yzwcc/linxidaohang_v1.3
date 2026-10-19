package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.agibot.aiem.sdk.enums.AgentCallbackType;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Runs real SDK callbacks and serialization against fake local model/robot HTTP peers. */
public final class ControlReplyRegressionTest {
    private static int checks;

    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
    }

    private static void clear(String field) throws Exception {
        Field f = LingxiNavAgentApp.class.getDeclaredField(field);
        f.setAccessible(true);
        ((Map<?, ?>) f.get(null)).clear();
    }

    public static void main(String[] args) throws Exception {
        AtomicInteger motions = new AtomicInteger();
        AtomicInteger navs = new AtomicInteger();
        AtomicInteger tours = new AtomicInteger();
        AtomicInteger motionCode = new AtomicInteger(200);
        List<String> robotSpeech = new CopyOnWriteArrayList<>();
        AtomicReference<String> modelIntent = new AtomicReference<>(
                "{\"intent\":\"navigate\",\"target_name\":\"大厅\",\"reply\":\"我做不到\"}");
        AtomicReference<String> motionResult = new AtomicReference<>("{\"accepted\":true}");
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        CapturingClient client = new CapturingClient();
        http.createContext("/", exchange -> {
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = switch (exchange.getRequestURI().getPath()) {
                case "/interaction/wake" -> "{\"handled\":false,\"accepted\":true}";
                case "/points" -> "{\"point_names\":[\"大厅\"]}";
                case "/task/match" -> input.contains("欢迎参观")
                        ? "{\"matched\":true,\"task_id\":\"hotel\",\"task_name\":\"酒店导览\"}" : "{\"matched\":false}";
                case "/nav/start" -> {
                    check(client.hasInterrupt(), "navigation claims a silent SDK response before dispatch");
                    navs.incrementAndGet();
                    yield "{\"accepted\":true}";
                }
                case "/task/start" -> {
                    check(client.hasInterrupt(), "custom tour claims a silent SDK response before dispatch");
                    tours.incrementAndGet();
                    yield "{\"accepted\":true,\"task_name\":\"酒店导览\"}";
                }
                case "/motion/play" -> {
                    check(client.hasInterrupt(), "flexible motion interrupted before motion side effect");
                    motions.incrementAndGet();
                    yield motionResult.get();
                }
                case "/model" -> "{\"choices\":[{\"message\":{\"content\":"
                        + JSONObject.toJSONString(modelIntent.get()) + "}}]}";
                default -> "{\"ok\":true}";
            };
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            if (exchange.getRequestURI().getPath().equals("/tts/speak")) robotSpeech.add(input);
            exchange.sendResponseHeaders(exchange.getRequestURI().getPath().equals("/motion/play")
                    ? motionCode.get() : 200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "reply-regression", "test", "test");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.mode", "lingxi");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(props);
            Method register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            register.invoke(null, sdk, config, new NavBridgeClient(config), new DoubaoArkClient(config), null);
            Asr2LlmCallback llm = (Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM);
            Asr2TtsCallback tts = (Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS);
            Asr2LlmResponse llmResponse = new Asr2LlmResponse(client, "llm", "robot");
            Asr2TtsResponse ttsResponse = new Asr2TtsResponse(client, "tts", "robot");
            for (String mode : List.of("llm", "tts")) {
                for (String kind : List.of("nav", "tour", "motion")) {
                    for (String failAt : mode.equals("llm")
                            ? List.of("", "agentsdk.llm_response.item.done", "agentsdk.llm_response.done")
                            : List.of("", "agentsdk.llm_response.item.done", "agentsdk.llm_response.done",
                                      "agentsdk.tts_response.item.done", "agentsdk.tts_response.done")) {
                        clear("LOCAL_COMMAND_DEDUP");
                        clear("CONTROL_RESPONSE_DEDUP");
                        clear("NATIVE_INTERRUPT_DEDUP");
                        client.reset(failAt);
                        String text = switch (kind) {
                            case "nav" -> "请引领我前往大厅";
                            case "tour" -> "欢迎参观";
                            default -> "咱俩友好地握一下手怎么样";
                        };
                        modelIntent.set(kind.equals("motion")
                                ? "{\"intent\":\"preset_motion\",\"target_name\":\"handshake\",\"reply\":\"动作被拒绝\"}"
                                : "{\"intent\":\"navigate\",\"target_name\":\"大厅\",\"reply\":\"我做不到\"}");
                        int before = navs.get() + tours.get() + motions.get();
                        String event = mode + kind + failAt;
                        if (mode.equals("llm")) llm.onRequest("llm", event, text, VoiceGateTest.acceptedParams(), llmResponse);
                        else tts.onRequest("tts", event, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        check(navs.get() + tours.get() + motions.get() == before + 1, "accepted control executes once: " + event);
                        check(failAt.isEmpty() || client.failed, "test actually injects a transport exception: " + event);
                        check(client.messages.stream().noneMatch(s -> s.contains("我做不到") || s.contains("动作被拒绝")
                                        || s.contains("处理失败") || s.contains("agentsdk.error")),
                                "accepted command never produces a contradictory reply/error: " + event);
                        int replies = client.replyCount();
                        if (mode.equals("llm")) tts.onRequest("tts", event, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        else llm.onRequest("llm", event, text, VoiceGateTest.acceptedParams(), llmResponse);
                        check(navs.get() + tours.get() + motions.get() == before + 1, "other callback cannot resubmit: " + event);
                        check(client.replyCount() == replies, "duplicate callback cannot announce busy/rejection: " + event);
                        client.reset("agentsdk.llm_response.done");
                        if (mode.equals("llm")) tts.onRequest("tts", event, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        else llm.onRequest("llm", event, text, VoiceGateTest.acceptedParams(), llmResponse);
                        check(client.failed && client.messages.stream().noneMatch(s -> s.contains("agentsdk.error")),
                                "late completion error in duplicate callback stays silent: " + event);
                    }
                }
            }
            clear("CONTROL_RESPONSE_DEDUP");
            clear("LOCAL_COMMAND_DEDUP");
            client.reset("");
            motionResult.set("{\"accepted\":false,\"status\":\"rejected\",\"message\":\"motion=1003, area=2\"}");
            llm.onRequest("llm", "real-rejection", "咱俩友好地握一下手怎么样", VoiceGateTest.acceptedParams(), llmResponse);
            check(client.replyCount() == 0, "low-level rejection is silent without claiming success");
            check(client.messages.stream().noneMatch(s -> s.contains("motion=1003")), "protocol diagnostics remain in logs");

            clear("CONTROL_RESPONSE_DEDUP");
            clear("LOCAL_COMMAND_DEDUP");
            clear("NATIVE_INTERRUPT_DEDUP");
            client.reset("interrupt");
            modelIntent.set("{\"intent\":\"navigate\",\"target_name\":\"大厅\"}");
            int before = navs.get();
            llm.onRequest("llm", "interrupt-unavailable", "带我去大厅", VoiceGateTest.acceptedParams(), llmResponse);
            check(navs.get() == before + 1, "SDK interruption transport failure does not block navigation");
            for (String kind : List.of("nav", "tour", "motion")) {
                clear("CONTROL_RESPONSE_DEDUP");
                clear("LOCAL_COMMAND_DEDUP");
                clear("NATIVE_INTERRUPT_DEDUP");
                client.reset("");
                motionResult.set("{\"accepted\":true}");
                String text = kind.equals("tour") ? "欢迎参观" : kind.equals("nav")
                        ? "请引领我前往大厅" : "咱俩友好地握一下手怎么样";
                modelIntent.set(kind.equals("motion")
                        ? "{\"intent\":\"preset_motion\",\"target_name\":\"handshake\"}"
                        : "{\"intent\":\"navigate\",\"target_name\":\"大厅\"}");
                int count = navs.get() + tours.get() + motions.get();
                var pool = Executors.newFixedThreadPool(2);
                CountDownLatch start = new CountDownLatch(1);
                try {
                    var one = pool.submit(() -> {
                        start.await();
                        llm.onRequest("llm", "race-" + kind, text, VoiceGateTest.acceptedParams(), llmResponse);
                        return null;
                    });
                    var two = pool.submit(() -> {
                        start.await();
                        tts.onRequest("tts", "race-" + kind, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        return null;
                    });
                    start.countDown();
                    one.get(15, TimeUnit.SECONDS);
                    two.get(15, TimeUnit.SECONDS);
                } finally {
                    pool.shutdownNow();
                }
                check(navs.get() + tours.get() + motions.get() == count + 1,
                        "concurrent callbacks submit one control: " + kind);
                check(client.replyCount() == (kind.equals("motion") ? 1 : 0),
                        "concurrent callbacks preserve silent start receipts: " + kind);
            }
            // Replay the field-log rejection through both speech routes and
            // inject failures after the controller has returned its result.
            for (String speechRoute : List.of("lingxi", "robot")) {
                props.setProperty("tts.mode", speechRoute);
                props.setProperty("tts.delayBeforeSpeakMs", "0");
                props.setProperty("tts.suppressLingxiText", "true");
                config = constructor.newInstance(props);
                register.invoke(null, sdk, config, new NavBridgeClient(config), new DoubaoArkClient(config), null);
                Asr2LlmCallback logLlm = (Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM);
                Asr2TtsCallback logTts = (Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS);
                modelIntent.set("{\"intent\":\"preset_motion\",\"target_name\":\"sweetheart\",\"motion_area\":\"both_hands\"}");
                for (String status : List.of("rejected", "confirmation_timeout", "confirmation_unknown", "http_unavailable")) {
                    motionResult.set("{\"ok\":false,\"accepted\":false,\"status\":\"" + status
                            + "\",\"message\":\"机器人动作服务拒绝请求: motion=3004, area=3；请确认机器人已进入稳定站立模式\"}");
                    motionCode.set(status.equals("http_unavailable") ? 503 : 200);
                    for (String mode : List.of("llm", "tts")) {
                        for (String failAt : mode.equals("llm")
                                ? List.of("", "agentsdk.llm_response.item.done", "agentsdk.llm_response.done")
                                : List.of("", "agentsdk.llm_response.item.done", "agentsdk.llm_response.done",
                                          "agentsdk.tts_response.item.done", "agentsdk.tts_response.done")) {
                            clear("CONTROL_RESPONSE_DEDUP");
                            clear("LOCAL_COMMAND_DEDUP");
                            clear("NATIVE_INTERRUPT_DEDUP");
                            client.reset(failAt);
                            robotSpeech.clear();
                            int motionBefore = motions.get();
                            String event = "logged-" + speechRoute + mode + status + failAt;
                            if (mode.equals("llm")) logLlm.onRequest("llm", event, "比个心", VoiceGateTest.acceptedParams(), llmResponse);
                            else logTts.onRequest("tts", event, "比个心", VoiceGateTest.acceptedParams(), ttsResponse);
                            check(motions.get() == motionBefore + 1, "silent acknowledgement never resubmits: " + event);
                            check(client.hasInterrupt(), "native duplicate still cancelled: " + event);
                            check(robotSpeech.isEmpty() && client.replyCount() == 0,
                                    "neither local TTS nor SDK text announces rejection/success: " + event);
                            check(client.messages.stream().noneMatch(s -> s.contains("agentsdk.error")),
                                    "late completion cannot trigger platform fallback: " + event);
                            check(failAt.isEmpty() || client.failed, "late fault was actually exercised: " + event);
                        }
                    }
                }
            }
            System.out.println("ControlReplyRegressionTest passed: " + checks + " assertions");
        } finally {
            sdk.release();
            client.stop();
            http.stop(0);
        }
    }

    private static final class CapturingClient extends LinkskyClient {
        private final List<String> messages = new CopyOnWriteArrayList<>();
        private String failAt = "";
        private boolean failed;
        CapturingClient() { super((AgentSdk) null); }
        void reset(String failAt) { messages.clear(); this.failAt = failAt; failed = false; }
        boolean hasInterrupt() { return messages.stream().anyMatch(s -> s.contains("xlm_response.interrupt")); }
        int replyCount() {
            return (int) messages.stream().filter(s -> s.contains("agentsdk.llm_response.item.delta")).count();
        }
        @Override
        public void sendText(String agent, String event, String payload, String logged) {
            messages.add(payload);
            if (!failed && !failAt.isEmpty() && payload.contains(failAt)) {
                failed = true;
                throw new IllegalStateException("simulated SDK completion transport error");
            }
        }
    }
}
