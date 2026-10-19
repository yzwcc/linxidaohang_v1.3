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

            check(client.messages.size() == 1,
                    "tour controls retain the reference channel; preset motion emits one interrupt");
            verify(client.messages.get(0), "agent-tts", "robot-tts", "event-motion");
            for (NavigationIntent intent : List.of(NavigationIntent.navigate("wire大厅"),
                    NavigationIntent.startTask("custom-tour"),
                    NavigationIntent.presetMotion("handshake", "right_hand"), NavigationIntent.chat("你好"))) {
                String event = "resolved-" + client.messages.size();
                Class<?> senderType = Class.forName("com.agibot.aiem.nav.LingxiNavAgentApp$InterruptSender");
                Object sender = Proxy.newProxyInstance(senderType.getClassLoader(), new Class<?>[]{senderType},
                        (proxy, method, values) -> { ttsResponse.onInterrupt((String) values[0], (String) values[1], (String) values[2]); return null; });
                Method factory = LingxiNavAgentApp.class.getDeclaredMethod("resolvedResponseInterrupt", String.class, String.class, senderType);
                factory.setAccessible(true);
                @SuppressWarnings("unchecked")
                java.util.function.Predicate<NavigationIntent> begin =
                        (java.util.function.Predicate<NavigationIntent>) factory.invoke(null, event, "wire-" + intent.intent, sender);
                int before = client.messages.size();
                check(begin.test(intent), "first resolved request admitted");
                if ("chat".equals(intent.intent)) {
                    check(begin.test(intent) && client.messages.size() == before,
                            "ordinary chat does not interrupt a native action");
                    continue;
                }
                check(!begin.test(intent), "same response cannot dispatch control twice");
                if (!intent.isPresetMotion()) {
                    check(client.messages.size() == before, "resolved HTTP navigation/tour emits no SDK interrupt");
                    continue;
                }
                check(client.messages.size() == before + 1, "resolved motion sends exactly one interrupt");
                JSONObject payload = JSON.parseObject(client.messages.get(before).payload);
                check("chat".equals(payload.getString("interruptType")),
                        "resolved local command uses chat rather than an empty skill action");
                check(payload.get("interruptTips") == null, "no fallback interrupt announcement");
            }
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
        check("chat".equals(payload.getString("interruptType")),
                "local HTTP control is a chat response, not an SDK skill action");
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
