package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentSdk;
import com.agibot.aiem.sdk.client.LinkskyClient;
import com.agibot.aiem.sdk.passive.Asr2LlmResponse;
import com.agibot.aiem.sdk.passive.Asr2TtsResponse;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;

/** Verifies the exact AgentSDK message emitted for native action interruption. */
public final class AgentSdkInterruptWireTest {
    private static int checks;

    private AgentSdkInterruptWireTest() {
    }

    private static void check(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
        checks++;
    }

    public static void main(String[] args) throws Exception {
        run();
    }

    static void run() throws Exception {
        CapturingLinkskyClient client = new CapturingLinkskyClient();
        try {
            Asr2TtsResponse ttsResponse = new Asr2TtsResponse(
                    client, "agent-tts", "robot-tts");
            Runnable ttsInterrupt = productionInterrupt(
                    "event-tts", "\u5f00\u59cb\u8bb2\u89e3", ttsResponse::onInterrupt);
            ttsInterrupt.run();
            ttsInterrupt.run();

            Asr2LlmResponse llmResponse = new Asr2LlmResponse(
                    client, "agent-llm", "robot-llm");
            Runnable llmInterrupt = productionInterrupt(
                    "event-llm", "\u7ee7\u7eed\u8bb2\u89e3", llmResponse::onInterrupt);
            llmInterrupt.run();
            llmInterrupt.run();

            Runnable motionInterrupt = productionInterrupt(
                    "event-motion", "\u63e1\u624b", ttsResponse::onInterrupt);
            motionInterrupt.run();
            motionInterrupt.run();

            Runnable question = productionInterrupt(
                    "event-question", "\u5982\u4f55\u5f00\u59cb\u8bb2\u89e3", ttsResponse::onInterrupt);
            question.run();

            check(client.messages.size() == 3,
                    "both callback modes and local motion emit one interrupt, questions emit none");
            verify(client.messages.get(0), "agent-tts", "robot-tts", "event-tts");
            verify(client.messages.get(1), "agent-llm", "robot-llm", "event-llm");
            verify(client.messages.get(2), "agent-tts", "robot-tts", "event-motion");
            System.out.println("AgentSdkInterruptWireTest passed: " + checks + " assertions");
        } finally {
            client.stop();
        }
    }

    private static Runnable productionInterrupt(String eventId, String text,
                                                InterruptCall call) throws Exception {
        Class<?> senderType = Class.forName(
                "com.agibot.aiem.nav.LingxiNavAgentApp$InterruptSender");
        Method factory = LingxiNavAgentApp.class.getDeclaredMethod(
                "nativeTourActionInterrupt", String.class, String.class, senderType);
        factory.setAccessible(true);
        Object sender = Proxy.newProxyInstance(
                senderType.getClassLoader(),
                new Class<?>[]{senderType},
                (proxy, method, args) -> {
                    call.send((String) args[0], (String) args[1], (String) args[2]);
                    return null;
                });
        return (Runnable) factory.invoke(null, eventId, text, sender);
    }

    private static void verify(CapturedMessage message, String agentId, String robotCid,
                               String eventId) {
        check(agentId.equals(message.agentId), "sendText keeps agent id");
        check(eventId.equals(message.eventId), "sendText keeps event id");
        check(message.payload.equals(message.logPayload), "wire and log payloads are identical");

        JSONObject payload = JSON.parseObject(message.payload);
        check("agentsdk.xlm_response.interrupt".equals(payload.getString("type")),
                "SDK emits the interrupt event type");
        check(agentId.equals(payload.getString("agentId")), "payload keeps agent id");
        check("passive".equals(payload.getString("agentMode")), "payload keeps passive mode");
        check(robotCid.equals(payload.getString("robotCid")), "payload keeps robot cid");
        check(robotCid.equals(payload.getString("cid")), "payload keeps cid alias");
        check(eventId.equals(payload.getString("eventId")), "payload keeps callback event id");
        check("action".equals(payload.getString("interruptType")),
                "payload interrupts the native action channel");
        check(payload.get("interruptTips") == null, "payload leaves interrupt tips empty");
    }

    private static final class CapturingLinkskyClient extends LinkskyClient {
        private final List<CapturedMessage> messages = new ArrayList<>();

        private CapturingLinkskyClient() {
            super((AgentSdk) null);
        }

        @Override
        public void sendText(String agentId, String eventId, String payload, String logPayload) {
            messages.add(new CapturedMessage(agentId, eventId, payload, logPayload));
        }
    }

    private record CapturedMessage(String agentId, String eventId, String payload,
                                   String logPayload) {
    }

    @FunctionalInterface
    private interface InterruptCall {
        void send(String eventId, String interruptType, String interruptTips);
    }
}
