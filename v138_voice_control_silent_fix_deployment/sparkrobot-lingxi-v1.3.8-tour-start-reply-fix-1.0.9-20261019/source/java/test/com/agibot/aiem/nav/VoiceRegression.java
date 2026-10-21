package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentParam;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Base64;
import java.util.Properties;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

public final class VoiceRegression {
    private static int checks;
    static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
        System.out.println("PASS " + name);
    }
    public static void main(String[] args) throws Exception {
        check(SpeechInputPolicy.isFiller("嗯，嗯。"), "filler rejected");
        check(SpeechInputPolicy.isFiller("好好好嗯"), "acknowledgement rejected");
        check(SpeechInputPolicy.isLikelyNoise("4"), "digit-only ASR fragment rejected");
        check(SpeechInputPolicy.isLikelyNoise("哈哈哈哈"), "repeated syllable fragment rejected");
        check(SpeechInputPolicy.isLikelyNoise("你好你好"), "repeated ASR fragment rejected");
        check(!SpeechInputPolicy.isLikelyNoise("继续"), "short control command retained");
        check(!SpeechInputPolicy.isFiller("停止"), "short stop retained");
        check(!SpeechInputPolicy.isFiller("我需要办理业务"), "custom command retained");
        check(!SpeechInputPolicy.isFiller("什么是智元机器人"), "question retained");
        check(RetrieverClient.candidateContext(com.alibaba.fastjson2.JSON.parseArray("[{\"score\":0.2,\"answer\":\"wrong\"}]")) == null, "low score candidate rejected");
        check(RetrieverClient.candidateContext(com.alibaba.fastjson2.JSON.parseArray("[{\"answer\":\"wrong\"}]")) == null, "missing score candidate rejected");
        String candidates = RetrieverClient.candidateContext(com.alibaba.fastjson2.JSON.parseArray("[{\"score\":0.8,\"question\":\"原问题\",\"answer\":\"原答案\"},{\"score\":0.7,\"content\":\"正文\"}]"));
        check(candidates.contains("原问题") && candidates.contains("正文"), "QA and document candidates retain context");
        check(SpeechInputPolicy.isEcho("我会做甜甜的星星梦", "我会做甜甜的星星梦，梦里全是小朋友"), "echo fragment rejected");
        check(!SpeechInputPolicy.isEcho("什么是智元机器人", "我会做甜甜的星星梦"), "new question not echo");
        check(DoubaoArkClient.validatedGroundedAnswer("{\"relevant\":false,\"answer\":\"wrong\"}") == null, "irrelevant candidate rejected");
        check(DoubaoArkClient.validatedGroundedAnswer("raw document") == null, "raw response rejected");
        check(DoubaoArkClient.validatedGroundedAnswer("{\"relevant\":true,\"answer\":\"" + "x".repeat(181) + "\"}") == null, "long response rejected");
        NavigationIntent handshake = NavigationIntent.fromJsonText("{\"intent\":\"preset_motion\",\"target_name\":\"shake_hand\",\"motion_area\":\"right_hand\"}");
        check(handshake.isPresetMotion() && "handshake".equals(handshake.targetName) && "right_hand".equals(handshake.motionArea), "preset handshake intent normalized");
        NavigationIntent heart = NavigationIntent.fromJsonText("{\"intent\":\"gesture\",\"target_name\":\"比心\",\"motion_area\":\"双手\"}");
        check(heart.isPresetMotion() && "sweetheart".equals(heart.targetName) && "both_hands".equals(heart.motionArea), "natural model output normalized to sweetheart");
        PresetMotionCatalog.Selection localHandshake = PresetMotionCatalog.resolveVoiceCommand("能不能跟我握一下手");
        check(localHandshake != null && "handshake".equals(localHandshake.motion().name())
                && "right_hand".equals(localHandshake.area()), "explicit handshake bypasses model");
        PresetMotionCatalog.Selection localHeart = PresetMotionCatalog.resolveVoiceCommand("比个心兄弟你好");
        check(localHeart != null && "sweetheart".equals(localHeart.motion().name())
                && "both_hands".equals(localHeart.area()), "explicit sweetheart bypasses model");
        check(PresetMotionCatalog.resolveVoiceCommand("你会握手吗") == null, "motion capability question stays chat");
        boolean unsupportedRejected = false;
        try { NavigationIntent.fromJsonText("{\"intent\":\"preset_motion\",\"target_name\":\"invented_motion\"}"); }
        catch (IllegalArgumentException expected) { unsupportedRejected = true; }
        check(unsupportedRejected, "hallucinated preset motion rejected");
        AtomicBoolean protectedStage = new AtomicBoolean(false);
        AtomicBoolean textOnly = new AtomicBoolean(false);
        AtomicBoolean motionPrompt = new AtomicBoolean(false);
        AtomicReference<String> motionRequest = new AtomicReference<>("");
        AtomicReference<JSONObject> modelRequest = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/interaction/wake", exchange -> {
            exchange.getRequestBody().readAllBytes();
            byte[] result = (protectedStage.get() ? "{\"handled\":true,\"accepted\":false,\"state\":\"task_linkcraft_action\"}" : "{\"handled\":false,\"accepted\":true}").getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, result.length);
            exchange.getResponseBody().write(result); exchange.close();
        });
        server.createContext("/model", exchange -> {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            modelRequest.set(JSON.parseObject(body));
            textOnly.set(!body.contains("input_audio") && body.contains("reference") && body.contains("question"));
            motionPrompt.set(body.contains("preset_motion") && body.contains("handshake") && body.contains("sweetheart"));
            String content = body.contains("reference")
                    ? "{\\\"relevant\\\":true,\\\"answer\\\":\\\"相关回答\\\"}"
                    : "{\\\"intent\\\":\\\"preset_motion\\\",\\\"target_name\\\":\\\"handshake\\\",\\\"motion_area\\\":\\\"right_hand\\\"}";
            byte[] result = ("{\"choices\":[{\"message\":{\"content\":\"" + content + "\"}}]}").getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, result.length);
            exchange.getResponseBody().write(result); exchange.close();
        });
        server.createContext("/motion/play", exchange -> {
            motionRequest.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] result = "{\"ok\":true,\"accepted\":true,\"status\":\"submitted\"}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, result.length);
            exchange.getResponseBody().write(result); exchange.close();
        });
        server.start();
        try {
            Properties props = new Properties();
            props.setProperty("doubao.apiKey", "test");
            for (String key : new String[]{"lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.model"}) props.setProperty(key, "test");
            String base = "http://127.0.0.1:" + server.getAddress().getPort();
            props.setProperty("nav.baseUrl", base);
            props.setProperty("doubao.url", base + "/model");
            props.setProperty("voice.gate.allowAddressedWake", "true");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(props);
            Field voiceGate = LingxiNavAgentApp.class.getDeclaredField("VOICE_GATE");
            voiceGate.setAccessible(true);
            voiceGate.set(null, new VoiceGate(config, new VoiceGateTest.FakeClock()));
            Field wakeSessionGate = LingxiNavAgentApp.class.getDeclaredField("WAKE_SESSION_GATE");
            wakeSessionGate.setAccessible(true);
            wakeSessionGate.set(null, new WakeSessionGate(config, new WakeSessionGateTest.FakeClock()));
            check("相关回答".equals(new DoubaoArkClient(config).groundedAnswer("问题", "资料")) && textOnly.get(), "mock model receives text and grounds answer");
            NavigationIntent modeledMotion = new DoubaoArkClient(config).parseInteraction("能不能跟我握一下手", List.of());
            check(modeledMotion.isPresetMotion() && motionPrompt.get(), "LLM prompt exposes structured preset motion intent");
            JSONObject textRequest = modelRequest.get();
            JSONArray textMessages = textRequest.getJSONArray("messages");
            check(textMessages.getJSONObject(1).get("content") instanceof String, "ASR text model input stays text-only");
            check(!textRequest.toJSONString().contains("input_audio"), "ASR text request never invents audio");
            checkIntentConstraints(textMessages.getJSONObject(0).getString("content"));
            check("disabled".equals(textRequest.getJSONObject("thinking").getString("type"))
                    && !textRequest.getBooleanValue("stream") && textRequest.getDoubleValue("temperature") == 0.0,
                    "intent model transport constraints retained");
            byte[] pcm = new byte[]{0, 0, 1, 0, 2, 0, 3, 0};
            check(new DoubaoArkClient(config).parseInteractionAudio(pcm, List.of("大厅")).isPresetMotion(), "audio model entry retained");
            JSONArray audioMessages = modelRequest.get().getJSONArray("messages");
            String audioPrompt = audioMessages.getJSONObject(0).getString("content");
            checkIntentConstraints(audioPrompt);
            check(audioPrompt.contains("一次完成语音识别、意图判断和回答") && audioPrompt.contains("大厅"), "audio prompt retains one-pass recognition and point catalog");
            JSONArray audioContent = audioMessages.getJSONObject(1).getJSONArray("content");
            check(audioContent.size() == 2 && "input_audio".equals(audioContent.getJSONObject(0).getString("type"))
                    && "text".equals(audioContent.getJSONObject(1).getString("type")), "audio keeps audio plus text instruction content types");
            JSONObject inputAudio = audioContent.getJSONObject(0).getJSONObject("input_audio");
            byte[] wav = Base64.getDecoder().decode(inputAudio.getString("data"));
            check("wav".equals(inputAudio.getString("format")) && wav.length == pcm.length + 44
                    && "RIFF".equals(new String(wav, 0, 4, StandardCharsets.US_ASCII)), "audio carries valid WAV envelope");
            ByteBuffer header = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN);
            check(header.getInt(24) == 16000 && header.getShort(22) == 1 && header.getShort(34) == 16, "PCM remains 16k mono 16-bit");
            Method gate = LingxiNavAgentApp.class.getDeclaredMethod("shouldIgnoreTextCallback", String.class, String.class, String.class, AgentParam.class, NavBridgeClient.class);
            gate.setAccessible(true);
            check((boolean)gate.invoke(null, "Asr2Tts", "filler", "嗯", AgentParam.create(), null), "filler exits before robot API");
            Field field = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER"); field.setAccessible(true);
            Object controller = field.get(null);
            Method window = controller.getClass().getDeclaredMethod("openSilentWindow", long.class); window.setAccessible(true); window.invoke(controller, 10000L);
            NavBridgeClient bridge = new NavBridgeClient(config);
            check(bridge.presetMotion("motion-test", modeledMotion.targetName, modeledMotion.motionArea).getBooleanValue("accepted")
                    && motionRequest.get().contains("handshake") && motionRequest.get().contains("right_hand"), "preset motion sent to robot bridge");
            check((boolean)gate.invoke(null, "Asr2Tts", "noise", "这个你我们", AgentParam.create(), bridge), "background fragment blocked in playback");
            check((boolean)gate.invoke(null, "Asr2Tts", "unaddressed", "什么是智元机器人", AgentParam.create(), bridge), "unaddressed speech cannot interrupt playback");
            check(!(boolean)gate.invoke(null, "Asr2Tts", "question", "灵犀灵犀，什么是智元机器人",
                    AgentParam.create().setBoolean("kws_detected", true), bridge),
                    "addressed question with KWS evidence accepted in playback window");
            check(!SpeechInputPolicy.cleanReply("来呀", "你好呀！我是灵犀，有什么我能帮到你的吗？").contains("我是"), "unsolicited identity removed");
            check(SpeechInputPolicy.cleanReply("你是谁", "我是灵犀。").contains("我是"), "requested identity retained");
            protectedStage.set(true);
            check((boolean)gate.invoke(null, "Asr2Tts", "protected", "灵犀灵犀，什么是智元机器人",
                    AgentParam.create().setBoolean("kws_detected", true), bridge),
                    "linkcraft protection retained");
        } finally { server.stop(0); }
        VoiceAgentGateRegression.run();
        System.out.println("VoiceRegression passed: " + checks + " assertions plus real Agent gate regression");
    }

    private static void checkIntentConstraints(String prompt) {
        check(prompt.contains("只输出一个 JSON 对象") && prompt.contains("用户原话"), "structured intent and original question retained");
        check(prompt.contains("不支持的动作必须用 chat") && prompt.contains("禁止编造动作名或数值 ID"), "unsupported motion safety constraints retained");
        check(prompt.contains("询问‘你会握手吗’属于 chat") && prompt.contains("其他内容全部为 chat"), "ordinary questions are not rejected as noise or actions");
    }
}
