package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentMeta;
import com.agibot.aiem.sdk.AgentParam;
import com.alibaba.fastjson2.JSONObject;
import java.lang.reflect.Constructor;
import java.util.Properties;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

public final class WakeSessionGateTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
        checks++;
    }

    private static AppConfig config(String mode, boolean allowAddressedWake) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test");
        properties.setProperty("voice.gate.wakeMode", mode);
        properties.setProperty("voice.gate.allowAddressedWake", Boolean.toString(allowAddressedWake));
        properties.setProperty("voice.gate.addressAliases", "hello,lumi");
        properties.setProperty("voice.gate.sessionWindowMs", "8000");
        properties.setProperty("voice.gate.wakeSignalMaxAgeMs", "8000");
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    private static AgentMeta meta(String wakeWords) {
        JSONObject value = new JSONObject();
        value.put("def.wakeupWord", wakeWords);
        return new AgentMeta(value);
    }

    public static void main(String[] args) throws Exception {
        testColdRequestsAndTextFallback();
        testMetadataWakeAndSlidingSession();
        testListeningStateAndReplayProtection();
        testVerifiedSoc2WakeEvidence();
        testSilentAndOfflineCloseSession();
        testObserveAndOffModes();
        System.out.println("WakeSessionGateTest passed: " + checks + " assertions");
    }

    private static void testColdRequestsAndTextFallback() throws Exception {
        FakeClock clock = new FakeClock();
        WakeSessionGate gate = new WakeSessionGate(config("enforce", true), clock);
        gate.onRobotOnline("agent", meta("hello, lumi"));
        check(!gate.admit("agent", "cold", "what time is it", AgentParam.create()).accepted(),
                "cold request requires wake evidence");
        WakeSessionGate.Decision transcript = gate.admit(
                "agent", "transcript", "hello what time is it", AgentParam.create());
        check(!transcript.accepted() && "text_wake_fallback_disabled".equals(transcript.reason()),
                "enforce mode rejects transcript-only wake");
        check(gate.admit("agent", "metadata", "hello", AgentParam.create()
                .setBoolean("kws_detected", true)).accepted(),
                "explicit SDK KWS evidence opens session");
        gate.reset();

        WakeSessionGate observe = new WakeSessionGate(config("observe", true), clock);
        observe.onRobotOnline("agent", meta("hello, lumi"));
        WakeSessionGate.Decision addressed = observe.admit(
                "agent", "addressed", "hello what time is it", AgentParam.create());
        check(addressed.accepted() && addressed.wouldReject()
                        && addressed.reason().startsWith("observe_would_reject:addressed_wake_observe_fallback"),
                "observe mode records transcript fallback without treating it as production evidence");
        gate.reset();
        check(!gate.admit("agent", "false", "hello question",
                AgentParam.create().setBoolean("kws_detected", false)).accepted(),
                "explicit negative KWS cannot be bypassed");
    }

    private static void testMetadataWakeAndSlidingSession() throws Exception {
        FakeClock clock = new FakeClock();
        WakeSessionGate gate = new WakeSessionGate(config("enforce", false), clock);
        check(gate.admit("agent", "wake", "hello", AgentParam.create()
                .setBoolean("kws_detected", true)).accepted(),
                "metadata wake opens session");
        check(gate.admit("agent", "followup-with-negative-kws", "tell me more",
                AgentParam.create().setBoolean("kws_detected", false)).accepted(),
                "active session does not require a new KWS event on every utterance");
        clock.advanceMillis(7999);
        check(gate.admit("agent", "followup", "tell me more", AgentParam.create()).accepted(),
                "followup accepted before expiry");
        clock.advanceMillis(7999);
        check(gate.admit("agent", "followup-2", "and then", AgentParam.create()).accepted(),
                "accepted followup extends session");
        clock.advanceMillis(8000);
        check(!gate.admit("agent", "expired", "still there", AgentParam.create()).accepted(),
                "session expires at configured boundary");
        check(!gate.admit("agent", "explicit-false", "still there",
                AgentParam.create().setBoolean("kws_detected", false)).accepted(),
                "negative metadata remains rejected");
    }

    private static void testListeningStateAndReplayProtection() throws Exception {
        FakeClock clock = new FakeClock();
        WakeSessionGate gate = new WakeSessionGate(config("enforce", false), clock);
        String aliasedAwake = "{\"state\":\"listening\",\"wakeup_state\":\"already_wake_up\","
                + "\"event_id\":\"event-aliased\",\"updated_at_ms\":1}";
        gate.onState("agent", "callback-aliased", "listening_status", aliasedAwake);
        check(gate.admit("agent", "event-aliased", "hello what time is it", AgentParam.create()).accepted(),
                "real state wake is accepted even when ASR text retains the wake word");
        gate.reset();
        String awake = "{\"state\":\"listening\",\"wakeup_state\":\"already_wake_up\","
                + "\"event_id\":\"event-1\",\"updated_at_ms\":1}";
        WakeSessionGate.StateObservation observation = gate.onState(
                "agent", "callback-1", "listening_status", awake);
        check(observation.relevant() && observation.awake(), "listening state records wake");
        check(gate.admit("agent", "event-1", "what time is it", AgentParam.create()).accepted(),
                "matching state wake is accepted");
        gate.reset();
        gate.onState("agent", "callback-alt", "wakeup_status",
                "{\"listeningState\":\"listening\",\"wakeupState\":\"awake\","
                        + "\"event_id\":\"event-alt\"}");
        check(gate.admit("agent", "event-alt", "how is the weather", AgentParam.create()).accepted(),
                "alternate wake state fields are accepted");
        gate.reset();
        gate.onState("agent", "callback-1", "listening_status", awake);
        check(!gate.admit("agent", "event-1", "replayed state", AgentParam.create()).accepted(),
                "replayed event cannot reopen a reset session");
        gate.onState("agent", "callback-1", "listening_status",
                "{\"state\":\"listening\",\"wakeup_state\":\"already_wake_up\","
                        + "\"event_id\":\"event-1\",\"updated_at_ms\":99}");
        check(!gate.admit("agent", "event-1", "refreshed replay", AgentParam.create()).accepted(),
                "same event id cannot rearm from a timestamp refresh");
        check(!gate.onState("agent", "ignored", "vpr_id",
                "{\"voice_embedding\":[1,2,3]}").relevant(),
                "voiceprint state is ignored");
        check(!gate.onState("agent", "bad", "listening_status", "not-json").awake(),
                "malformed state cannot wake agent");
    }

    private static void testSilentAndOfflineCloseSession() throws Exception {
        FakeClock clock = new FakeClock();
        WakeSessionGate gate = new WakeSessionGate(config("enforce", false), clock);
        gate.admit("agent", "wake", "hello", AgentParam.create().setBoolean("kws_detected", true));
        gate.onState("agent", "silent", "listening_status",
                "{\"state\":\"non_wakeup_silent\",\"wakeup_state\":\"idle\",\"updated_at_ms\":4}");
        check(!gate.admit("agent", "after-silent", "question", AgentParam.create()).accepted(),
                "silent state closes session");
        gate.admit("agent", "wake-2", "hello", AgentParam.create().setBoolean("kws_detected", true));
        gate.onRobotOffline("agent");
        check(!gate.admit("agent", "after-offline", "question", AgentParam.create()).accepted(),
                "offline state closes session");
    }

    private static void testVerifiedSoc2WakeEvidence() throws Exception {
        FakeClock clock = new FakeClock();
        WakeSessionGate gate = new WakeSessionGate(config("enforce", false), clock);
        check(gate.admitVerifiedSoc2Wake("agent", "soc2-fresh", 2777).accepted(),
                "fresh exact-event SOC2 evidence opens session");
        check(gate.admit("agent", "followup", "tell me more", AgentParam.create()).accepted(),
                "SOC2 evidence opens the normal sliding session");
        gate.reset();
        check(!gate.admitVerifiedSoc2Wake("agent", "soc2-stale", 11667).accepted(),
                "stale SOC2 evidence is rejected");
        check(!gate.admitVerifiedSoc2Wake("agent", "", 1000).accepted(),
                "SOC2 evidence requires an exact event id");
        check(!gate.admitVerifiedSoc2Wake("agent", "soc2-fresh", 1000).accepted(),
                "verified SOC2 events cannot be replayed after reset");
    }

    private static void testObserveAndOffModes() throws Exception {
        WakeSessionGate observe = new WakeSessionGate(config("observe", true), new FakeClock());
        WakeSessionGate.Decision observed = observe.admit(
                "agent", "cold", "ordinary question", AgentParam.create());
        check(observed.accepted() && observed.wouldReject()
                        && observed.reason().startsWith("observe_would_reject:"),
                "observe mode records cold rejection without blocking");
        WakeSessionGate off = new WakeSessionGate(config("off", false), new FakeClock());
        WakeSessionGate.Decision disabled = off.admit(
                "agent", "cold", "ordinary question", AgentParam.create());
        check(disabled.accepted() && !disabled.wouldReject(), "off mode bypasses wake gate");
    }

    static final class FakeClock implements LongSupplier {
        private long nanos;

        @Override
        public long getAsLong() {
            return nanos;
        }

        void advanceMillis(long millis) {
            nanos += TimeUnit.MILLISECONDS.toNanos(millis);
        }
    }
}
