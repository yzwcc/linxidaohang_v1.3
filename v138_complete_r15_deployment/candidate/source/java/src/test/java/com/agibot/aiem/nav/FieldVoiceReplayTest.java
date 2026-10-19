package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.agibot.aiem.sdk.enums.AgentCallbackType;
import com.agibot.aiem.sdk.passive.Asr2LlmCallback;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsCallback;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Replays the 15:02-15:09 field failures through real AgentSDK callbacks. */
public final class FieldVoiceReplayTest {
    private static int checks;
    private static int events;
    private static final List<String> failures = new ArrayList<>();

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) failures.add(message);
    }

    @SuppressWarnings("unchecked")
    private static void reset() throws Exception {
        for (String name : List.of("LOCAL_COMMAND_DEDUP", "CONTROL_RESPONSE_DEDUP", "NATIVE_INTERRUPT_DEDUP")) {
            Field field = LingxiNavAgentApp.class.getDeclaredField(name);
            field.setAccessible(true);
            ((Map<String, Long>) field.get(null)).clear();
        }
        Field field = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        field.setAccessible(true);
        Object controller = field.get(null);
        for (String name : List.of("lastAcceptedAtMillis", "silentUntilMillis")) {
            Field time = controller.getClass().getDeclaredField(name);
            time.setAccessible(true);
            time.setLong(controller, 0L);
        }
        Field last = controller.getClass().getDeclaredField("lastNormalizedText");
        last.setAccessible(true);
        last.set(controller, "");
    }

    public static void main(String[] args) throws Exception {
        ((ch.qos.logback.classic.LoggerContext) org.slf4j.LoggerFactory.getILoggerFactory())
                .getLoggerList().forEach(logger -> logger.setLevel(ch.qos.logback.classic.Level.OFF));
        AtomicReference<String> intent = new AtomicReference<>();
        AtomicReference<String> answer = new AtomicReference<>("很高兴见到你。");
        AtomicInteger models = new AtomicInteger();
        AtomicInteger modelStatus = new AtomicInteger(200);
        AtomicInteger wakes = new AtomicInteger();
        AtomicInteger motions = new AtomicInteger();
        AtomicInteger navs = new AtomicInteger();
        AtomicInteger tours = new AtomicInteger();
        AtomicInteger performances = new AtomicInteger();
        AtomicReference<String> motionReply = new AtomicReference<>("{\"accepted\":true}");
        List<String> speech = new CopyOnWriteArrayList<>();
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        http.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            String input = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String body = "{\"ok\":true,\"accepted\":true}";
            switch (path) {
                case "/model" -> {
                    models.incrementAndGet();
                    JSONObject request = JSON.parseObject(input);
                    String prompt = request.getJSONArray("messages").getJSONObject(0).getString("content");
                    String value = prompt.contains("意图解析器") ? intent.get() : answer.get();
                    body = "{\"choices\":[{\"message\":{\"content\":" + JSON.toJSONString(value) + "}}]}";
                }
                case "/points" -> body = "{\"point_names\":[\"软着陆\",\"星球称重\"]}";
                case "/interaction/wake" -> {
                    wakes.incrementAndGet();
                    body = "{\"handled\":false,\"accepted\":true}";
                }
                case "/task/match" -> body = input.contains("带我们了解一下场馆")
                        ? "{\"matched\":true,\"task_id\":\"museum\",\"task_name\":\"场馆讲解\"}"
                        : "{\"matched\":false}";
                case "/motion/play" -> {
                    motions.incrementAndGet();
                    body = motionReply.get();
                }
                case "/nav/start" -> navs.incrementAndGet();
                case "/task/start" -> tours.incrementAndGet();
                case "/entertainment/dance", "/entertainment/sing" -> {
                    performances.incrementAndGet();
                    body = "{\"accepted\":true,\"prepare_text\":\"请给我一点空间\"}";
                }
                case "/tts/speak" -> speech.add(JSON.parseObject(input).getString("text"));
                default -> { }
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(path.equals("/model") ? modelStatus.get() : 200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        http.start();
        CapturingClient client = new CapturingClient();
        var qaPath = java.nio.file.Files.createTempFile("field-qa-", ".json");
        QaStore qa = new QaStore(qaPath.toString());
        AgentSdk sdk = AgentSdk.create("http://127.0.0.1:1", "field-replay", "test", "test");
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model"))
                props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + http.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("lingxi.callbacks", "asr2llm,asr2tts");
            props.setProperty("tts.delayBeforeSpeakMs", "0");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            var register = LingxiNavAgentApp.class.getDeclaredMethod("registerAsrCallbacks",
                    AgentSdk.class, AppConfig.class, NavBridgeClient.class, DoubaoArkClient.class, QaStore.class);
            register.setAccessible(true);
            for (String route : List.of("lingxi", "robot")) {
                props.setProperty("tts.mode", route);
                props.setProperty("tts.suppressLingxiText", "true");
                AppConfig config = constructor.newInstance(props);
                register.invoke(null, sdk, config, new NavBridgeClient(config), new DoubaoArkClient(config), qa);
                var llm = (Asr2LlmCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_LLM);
                var tts = (Asr2TtsCallback) sdk.getRobotCallback(AgentCallbackType.ASR_2_TTS);
                var llmResponse = new Asr2LlmResponse(client, "llm", "robot");
                var ttsResponse = new Asr2TtsResponse(client, "tts", "robot");
                for (String mode : List.of("llm", "tts")) {
                    for (String kind : List.of("nav", "tour", "motion")) {
                        reset(); client.messages.clear(); speech.clear();
                        intent.set(kind.equals("motion") ? "{\"intent\":\"preset_motion\",\"target_name\":\"sweetheart\"}"
                                : "{\"intent\":\"navigate\",\"target_name\":\"软着陆\",\"reply\":\"我做不到\"}");
                        String text = kind.equals("nav") ? "带我去软着陆"
                                : kind.equals("tour") ? "带我们了解一下场馆" : "比个心";
                        int before = navs.get() + tours.get() + motions.get();
                        if (mode.equals("llm")) llm.onRequest("llm", route + mode + kind, text, VoiceGateTest.acceptedParams(), llmResponse);
                        else tts.onRequest("tts", route + mode + kind, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        check(navs.get() + tours.get() + motions.get() == before + 1, "single local dispatch " + route + mode + kind);
                        check(client.interruptTypes().equals(List.of("chat")),
                                "navigation/tour claim silent response; motion retains its policy " + route + mode + kind);
                        check(!client.join(speech).contains("做不到"), "model refusal cannot precede accepted dispatch");
                    }
                    for (String text : List.of("打个招呼", "打个招呼呗", "跟大家打个招呼", "请你打一下招呼")) {
                        for (String modeled : List.of("{\"intent\":\"greet\"}", "{\"intent\":\"preset_motion\",\"target_name\":\"greeting\"}",
                                "{\"intent\":\"chat\",\"reply\":\"做不到\"}", "{\"intent\":\"preset_motion\",\"target_name\":\"wave\"}")) {
                            reset(); client.messages.clear(); speech.clear(); intent.set(modeled);
                            int before = motions.get();
                            String event = "greeting-" + events++;
                            if (mode.equals("llm")) llm.onRequest("llm", event, text, VoiceGateTest.acceptedParams(), llmResponse);
                            else tts.onRequest("tts", event, text, VoiceGateTest.acceptedParams(), ttsResponse);
                            check(motions.get() == before + 1, "greeting dispatched once despite invalid/aliased/chat intent " + route + mode + text + modeled);
                            check(!client.join(speech).contains("不可用") && !client.join(speech).contains("做不到"), "greeting never becomes QA outage/refusal");
                        }
                    }
                    for (String text : List.of("唱首歌", "跳个舞", "请给我唱一首歌", "跳一段舞")) {
                        for (String modeled : List.of("{\"intent\":\"" + (text.contains("唱") ? "sing" : "dance") + "\"}", "not-json")) {
                            reset(); client.messages.clear(); speech.clear(); intent.set(modeled);
                            int before = performances.get();
                            if (mode.equals("llm")) llm.onRequest("llm", route + mode + text + modeled, text, VoiceGateTest.acceptedParams(), llmResponse);
                            else tts.onRequest("tts", route + mode + text + modeled, text, VoiceGateTest.acceptedParams(), ttsResponse);
                            check(performances.get() == before, "native performance never resubmitted to prepare/resource bridge");
                            check(client.interruptTypes().isEmpty(), "native performance is not interrupted or claimed as empty action");
                            check(!client.hasSpeechOrError() && speech.isEmpty(), "native performance has no disabled/space/outage announcement");
                            check(client.messages.stream().noneMatch(s -> s.contains("agentsdk.skill")), "native performance has no duplicate skill dispatch");
                        }
                    }
                    modelStatus.set(503);
                    for (String text : List.of("打个招呼", "唱首歌", "跳个舞")) {
                        reset(); client.messages.clear(); speech.clear();
                        int motionBefore = motions.get(), before = performances.get(), modelBefore = models.get();
                        String event = "outage-" + events++;
                        if (mode.equals("llm")) llm.onRequest("llm", event, text, VoiceGateTest.acceptedParams(), llmResponse);
                        else tts.onRequest("tts", event, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        check(models.get() == modelBefore + 2, "actual HTTP 503 and bounded model retry exercised");
                        check(motions.get() == motionBefore + (text.contains("招呼") ? 1 : 0), "only explicit greeting falls back to local motion during outage");
                        check(performances.get() == before, "outage cannot resubmit native performance");
                        check(!client.join(speech).contains("不可用") && !client.join(speech).contains("关闭"), "explicit action/performance outage has no false QA speech");
                    }
                    modelStatus.set(200);
                    for (String status : List.of("rejected", "confirmation_unknown")) {
                        reset(); client.messages.clear(); speech.clear();
                        intent.set("{\"intent\":\"greet\"}");
                        motionReply.set("{\"accepted\":false,\"status\":\"" + status + "\",\"message\":\"动作执行失败\"}");
                        int before = motions.get();
                        String event = "greeting-receipt-" + events++;
                        if (mode.equals("llm")) llm.onRequest("llm", event, "打个招呼", VoiceGateTest.acceptedParams(), llmResponse);
                        else tts.onRequest("tts", event, "打个招呼", VoiceGateTest.acceptedParams(), ttsResponse);
                        check(motions.get() == before + 1, "greeting receipt never causes repeated motion");
                        check(!client.hasSpeechOrError() && speech.isEmpty(), "greeting rejection/unknown stays silent without claiming success");
                    }
                    motionReply.set("{\"accepted\":true}");
                    for (String text : List.of("那你是", "你是", "那你", "我想", "那个")) {
                        reset(); client.messages.clear(); speech.clear();
                        int before = models.get(), wakeBefore = wakes.get();
                        if (mode.equals("llm")) llm.onRequest("llm", route + mode + text, text, VoiceGateTest.acceptedParams(), llmResponse);
                        else tts.onRequest("tts", route + mode + text, text, VoiceGateTest.acceptedParams(), ttsResponse);
                        check(models.get() == before && wakes.get() == wakeBefore, "incomplete speech cannot wake tour or call model");
                        check(!client.hasSpeechOrError() && speech.isEmpty(), "fragment cannot produce unsolicited identity");
                    }
                    reset(); client.messages.clear(); speech.clear();
                    intent.set("{\"intent\":\"chat\",\"question\":\"今天有什么服务\"}");
                    answer.set("我是çµç，是现场服务和互动机器人。");
                    if (mode.equals("llm")) llm.onRequest("llm", route + mode + "identity-spam", "今天有什么服务", VoiceGateTest.acceptedParams(), llmResponse);
                    else tts.onRequest("tts", route + mode + "identity-spam", "今天有什么服务", VoiceGateTest.acceptedParams(), ttsResponse);
                    check(!client.join(speech).contains("现场服务"), "unsolicited identity with malformed display name stripped");

                    for (String question : List.of("你是谁", "你是什么机器人", "介绍下自己")) {
                        reset(); client.messages.clear(); speech.clear();
                        intent.set("{\"intent\":\"chat\",\"question\":" + JSON.toJSONString(question) + "}");
                        answer.set("我是灵犀，是现场服务和互动机器人。");
                        String event = "identity-" + events++;
                        if (mode.equals("llm")) llm.onRequest("llm", event, question, VoiceGateTest.acceptedParams(), llmResponse);
                        else tts.onRequest("tts", event, question, VoiceGateTest.acceptedParams(), ttsResponse);
                        check(client.join(speech).contains("我是灵犀"), "explicit identity question remains answerable " + question);
                    }

                    reset(); client.messages.clear(); speech.clear();
                    intent.set("{\"intent\":\"chat\",\"question\":\"唱歌是什么意思\",\"reply\":\"我可以跳舞\"}");
                    answer.set("唱歌是用人声演绎旋律和歌词。");
                    int before = performances.get();
                    if (mode.equals("llm")) llm.onRequest("llm", route + mode + "song-question", "唱歌是什么意思", VoiceGateTest.acceptedParams(), llmResponse);
                    else tts.onRequest("tts", route + mode + "song-question", "唱歌是什么意思", VoiceGateTest.acceptedParams(), ttsResponse);
                    check(performances.get() == before && client.interruptTypes().isEmpty(), "capability question/model suggestion cannot start performance");
                    check(client.join(speech).contains("旋律"), "ordinary question answered normally");
                }
            }
            for (String text : List.of("不要打个招呼", "他说打个招呼", "如果打招呼会怎么样", "你会打招呼吗")) {
                check(PresetMotionCatalog.resolveVoiceCommand(text) == null, "greeting fallback respects non-command " + text);
            }
        } finally {
            http.stop(0);
            client.stop();
            sdk.release();
            java.nio.file.Files.deleteIfExists(qaPath);
        }
        if (!failures.isEmpty()) throw new AssertionError("Field replay failures: " + failures.size() + "/" + checks
                + "\n" + String.join("\n", failures));
        System.out.println("FieldVoiceReplayTest passed: " + checks + " assertions");
    }

    private static final class CapturingClient extends LinkskyClient {
        final List<String> messages = new CopyOnWriteArrayList<>();
        CapturingClient() { super((AgentSdk) null); }
        List<String> interruptTypes() {
            return messages.stream().map(JSON::parseObject)
                    .filter(m -> "agentsdk.xlm_response.interrupt".equals(m.getString("type")))
                    .map(m -> m.getString("interruptType")).toList();
        }
        boolean hasSpeechOrError() {
            return messages.stream().anyMatch(s -> s.contains("agentsdk.llm_response.item.delta") || s.contains("agentsdk.error"));
        }
        String join(List<String> speech) { return String.join("\n", messages) + speech; }
        @Override public void sendText(String agent, String event, String payload, String logged) {
            messages.add(payload);
        }
    }
}
