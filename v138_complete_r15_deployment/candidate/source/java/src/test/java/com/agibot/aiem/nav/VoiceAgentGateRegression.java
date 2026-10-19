package com.agibot.aiem.nav;

import com.agibot.aiem.sdk.AgentParam;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.lang.reflect.Proxy;

/** Calls the production SDK entry methods; only the HTTP robot bridge is fake. */
final class VoiceAgentGateRegression {
    private static int checks;

    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
    }

    private static Method method(String name, Class<?>... types) throws Exception {
        Method method = LingxiNavAgentApp.class.getDeclaredMethod(name, types);
        method.setAccessible(true);
        return method;
    }

    private static void respond(HttpExchange exchange, String body) throws java.io.IOException {
        exchange.getRequestBody().readAllBytes();
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    static void run() throws Exception {
        Method actual = method("shouldIgnoreTextCallback", String.class, String.class, String.class,
                AgentParam.class, NavBridgeClient.class);
        Method actualWithAgent = method("shouldIgnoreTextCallback", String.class, String.class,
                String.class, String.class, AgentParam.class, NavBridgeClient.class);
        Method compatible = method("shouldIgnoreTextCallback", String.class, String.class, String.class, NavBridgeClient.class);
        Method audio = method("newAudioSession", String.class, String.class, String.class, AgentParam.class, NavBridgeClient.class);
        Method process = method("processTextRequest", String.class, String.class, AppConfig.class, NavBridgeClient.class,
                DoubaoArkClient.class, QaStore.class, AtomicBoolean.class);
        Method shouldInterruptNativeTour = method("shouldInterruptNativeTourAction", String.class);
        Class<?> interruptSenderType = Class.forName(
                "com.agibot.aiem.nav.LingxiNavAgentApp$InterruptSender");
        Method nativeTourInterruptFactory = method(
                "nativeTourActionInterrupt", String.class, String.class, interruptSenderType);
        Field gateField = LingxiNavAgentApp.class.getDeclaredField("VOICE_GATE");
        gateField.setAccessible(true);
        Object originalGate = gateField.get(null);
        Field wakeGateField = LingxiNavAgentApp.class.getDeclaredField("WAKE_SESSION_GATE");
        wakeGateField.setAccessible(true);
        Object originalWakeGate = wakeGateField.get(null);
        Field soc2WakeField = LingxiNavAgentApp.class.getDeclaredField("SOC2_WAKE_EVIDENCE");
        soc2WakeField.setAccessible(true);
        Object originalSoc2Wake = soc2WakeField.get(null);
        Field ttsField = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        ttsField.setAccessible(true);
        Object tts = ttsField.get(null);
        Field silentUntil = tts.getClass().getDeclaredField("silentUntilMillis");
        silentUntil.setAccessible(true);
        Field spoken = tts.getClass().getDeclaredField("lastNormalizedText");
        spoken.setAccessible(true);
        long originalSilentUntil = silentUntil.getLong(tts);
        Object originalSpoken = spoken.get(tts);
        AtomicBoolean linkcraft = new AtomicBoolean();
        AtomicBoolean statusOnly = new AtomicBoolean();
        AtomicInteger wakeCalls = new AtomicInteger();
        AtomicInteger stopCalls = new AtomicInteger();
        AtomicInteger endTaskCalls = new AtomicInteger();
        AtomicInteger startTaskCalls = new AtomicInteger();
        AtomicInteger navStartCalls = new AtomicInteger();
        AtomicInteger motionCalls = new AtomicInteger();
        AtomicInteger unexpectedCalls = new AtomicInteger();
        AtomicBoolean transientNavStart = new AtomicBoolean(true);
        Path soc2Root = Files.createTempDirectory("soc2-production-gate-");
        Path soc2Log = soc2Root.resolve("log_20260920_131528/agent/agent.log");
        Files.createDirectories(soc2Log.getParent());
        Files.writeString(soc2Log,
                "event_id: soc2-fresh, final asr result: 开始讲解, 距离上次唤醒: 2777, extend_mic: false\n"
                        + "event_id: soc2-stale, final asr result: 开始讲解, 距离上次唤醒: 11667, extend_mic: false\n",
                StandardCharsets.UTF_8);
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/interaction/wake", exchange -> {
            wakeCalls.incrementAndGet();
            respond(exchange, statusOnly.get() ? "{\"handled\":true,\"accepted\":false,\"status\":\"linkcraft_protected\"}" : linkcraft.get()
                    ? "{\"handled\":true,\"accepted\":false,\"state\":\"task_linkcraft_action\",\"status\":\"linkcraft_protected\"}"
                    : "{\"handled\":false,\"accepted\":true,\"state\":\"idle\"}");
        });
        server.createContext("/nav/stop", exchange -> {
            stopCalls.incrementAndGet();
            respond(exchange, "{\"ok\":true,\"cancelled\":true}");
        });
        server.createContext("/task/stop", exchange -> {
            endTaskCalls.incrementAndGet();
            respond(exchange, "{\"ok\":true,\"stopped\":true}");
        });
        server.createContext("/task/match", exchange ->
                respond(exchange, "{\"ok\":true,\"matched\":false,\"status\":\"not_found\"}"));
        server.createContext("/task/start", exchange -> {
            startTaskCalls.incrementAndGet();
            respond(exchange, "{\"ok\":true,\"accepted\":true,\"status\":\"started\",\"task_name\":\"测试讲解任务\"}");
        });
        server.createContext("/nav/start", exchange -> {
            int call = navStartCalls.incrementAndGet();
            if (transientNavStart.get() && call == 1) {
                respond(exchange, "{\"ok\":false,\"accepted\":false,\"status\":\"pnc_unavailable\"}");
            } else {
                respond(exchange, "{\"ok\":true,\"accepted\":true,\"status\":\"started\"}");
            }
        });
        server.createContext("/motion/play", exchange -> {
            motionCalls.incrementAndGet();
            respond(exchange, "{\"ok\":true,\"accepted\":true,\"status\":\"submitted\"}");
        });
        server.createContext("/interaction/mark", exchange ->
                respond(exchange, "{\"ok\":true,\"state\":\"task_interaction\"}"));
        server.createContext("/points", exchange ->
                respond(exchange, "{\"ok\":true,\"point_names\":[\"大堂\"]}"));
        server.createContext("/model", exchange ->
                respond(exchange, "{\"choices\":[{\"message\":{\"content\":\"{\\\"intent\\\":\\\"preset_motion\\\",\\\"target_name\\\":\\\"handshake\\\"}\"}}]}"));
        server.createContext("/", exchange -> {
            unexpectedCalls.incrementAndGet();
            respond(exchange, "{}");
        });
        server.start();
        try {
            Properties props = new Properties();
            for (String key : List.of("lingxi.appId", "lingxi.appKey", "lingxi.appSecret", "doubao.apiKey", "doubao.model")) {
                props.setProperty(key, "test");
            }
            props.setProperty("nav.baseUrl", "http://127.0.0.1:" + server.getAddress().getPort());
            props.setProperty("doubao.url", "http://127.0.0.1:" + server.getAddress().getPort() + "/model");
            props.setProperty("voice.gate.wakeMode", "enforce");
            props.setProperty("voice.gate.allowAddressedWake", "true");
            props.setProperty("voice.gate.sessionWindowMs", "5000");
            Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
            constructor.setAccessible(true);
            AppConfig config = constructor.newInstance(props);
            check(!config.voiceGateMetadataRequired, "metadata optional default remains unchanged");
            check(config.isCallbackEnabled("asr2llm") && !config.isCallbackEnabled("asr2tts")
                    && !config.isCallbackEnabled("audio2tts") && !config.isCallbackEnabled("audio2llm"),
                    "callback defaults use Lingxi ASR text path");
            NavBridgeClient bridge = new NavBridgeClient(config);
            Method startNavigationWithRetry = method(
                    "startNavigationWithRetry", String.class, String.class, NavBridgeClient.class);
            JSONObject retriedNavigation = (JSONObject) startNavigationWithRetry.invoke(
                    null, "nav-retry", "大堂", bridge);
            check(retriedNavigation.getBooleanValue("accepted") && navStartCalls.get() == 2,
                    "direct navigation retries transient PNC rejection");
            transientNavStart.set(false);
            navStartCalls.set(0);
            JSONObject stableNavigation = (JSONObject) startNavigationWithRetry.invoke(
                    null, "nav-stable", "大堂", bridge);
            check(stableNavigation.getBooleanValue("accepted") && navStartCalls.get() == 1,
                    "direct navigation does not retry accepted request");
            for (String text : List.of("开始讲解", "灵犀，开始讲解一下", "暂停讲解", "继续讲解", "结束讲解任务")) {
                check(!(boolean) shouldInterruptNativeTour.invoke(null, text),
                        "explicit tour control keeps the reference SDK channel: " + text);
            }
            for (String text : List.of("握手", "请给我比个心", "挥手一下", "打个招呼")) {
                check((boolean) shouldInterruptNativeTour.invoke(null, text),
                        "local performance interrupts native duplicate: " + text);
            }
            for (String text : List.of("如何开始讲解", "不要停止讲解", "如果继续讲解会怎样", "今天星期几", "跳一段舞", "唱首歌")) {
                check(!(boolean) shouldInterruptNativeTour.invoke(null, text),
                        "question or negation does not interrupt native action: " + text);
            }
            check(!(boolean) shouldInterruptNativeTour.invoke(null, "你会握手吗"),
                    "motion capability question does not interrupt native action");
            AtomicInteger nativeInterruptCalls = new AtomicInteger();
            Object sender = Proxy.newProxyInstance(
                    interruptSenderType.getClassLoader(),
                    new Class<?>[]{interruptSenderType},
                    (proxy, invoked, args) -> {
                        nativeInterruptCalls.incrementAndGet();
                        check("native-event".equals(args[0]), "native interrupt keeps callback event id");
                        check("chat".equals(args[1]), "local control uses chat response type");
                        check(args[2] == null, "native interrupt leaves tips empty");
                        return null;
                    });
            Runnable nativeInterrupt = (Runnable) nativeTourInterruptFactory.invoke(
                    null, "native-event", "握手", sender);
            nativeInterrupt.run();
            nativeInterrupt.run();
            check(nativeInterruptCalls.get() == 1, "preset motion interrupt is idempotent per callback");
            VoiceGateTest.FakeClock clock = new VoiceGateTest.FakeClock();
            VoiceGate gate = new VoiceGate(config, clock);
            WakeSessionGate wakeGate = new WakeSessionGate(config, clock);
            gateField.set(null, gate);
            wakeGateField.set(null, wakeGate);
            silentUntil.setLong(tts, 0);
            gate.reset();
            wakeGate.reset();
            check(!(boolean) actualWithAgent.invoke(null, "Asr2Tts", "agent-tts", "motion-event", "握手",
                    VoiceGateTest.acceptedParams(), bridge),
                    "first local motion callback is admitted");
            check((boolean) actualWithAgent.invoke(null, "Asr2Llm", "agent-llm", "motion-event", "握手",
                    VoiceGateTest.acceptedParams(), bridge),
                    "duplicate local motion callback is ignored");
            Object motionResult = process.invoke(null, "motion-process", "请握手", config,
                    bridge, new DoubaoArkClient(config), null, new AtomicBoolean());
            Method localPerformanceAccepted = motionResult.getClass().getDeclaredMethod("localPerformanceAccepted");
            localPerformanceAccepted.setAccessible(true);
            check((boolean) localPerformanceAccepted.invoke(motionResult),
                    "accepted local motion carries success state through interaction result");
            check(motionCalls.get() == 1, "local motion is submitted exactly once in direct processing");
            for (String source : List.of("Asr2Tts", "Asr2Llm")) {
                for (String text : List.of("开始讲解", "开始任务", "继续讲解", "继续任务", "恢复讲解", "暂停讲解",
                        "为什么停止导航", "不要停止讲解", "请解释什么是急停", "如果停止任务会怎样")) {
                    gate.reset();
                    wakeGate.reset();
                    int before = wakeCalls.get();
                    check((boolean) actual.invoke(null, source, "bad-wake", text,
                            VoiceGateTest.acceptedParams().setBoolean("kws_detected", false), bridge), "not urgent: " + text);
                    check(wakeCalls.get() == before, "nonurgent bad wake never reaches tour bridge");
                    check((boolean) actual.invoke(null, source, "bad-snr", text,
                            VoiceGateTest.acceptedParams().setDouble("snr_db", 1.0), bridge), "nonurgent quality check: " + text);
                    check((boolean) actual.invoke(null, source, "bad-doa", text,
                            VoiceGateTest.acceptedParams().setDouble("doa_angle_deg", 180.0), bridge), "nonurgent direction check: " + text);
                }
                for (String text : List.of("是", "否", "什么是智元机器人", "如何开始讲解", "我需要办理业务", "你好", "继续讲解")) {
                    check(!(boolean) actual.invoke(null, source, "valid", text, VoiceGateTest.acceptedParams(), bridge), "ordinary request reaches actual entry: " + text);
                }
                for (String key : List.of("doa_angle_deg", "snr_db", "aec_residual_rms", "rnnoise_vad_probability", "asr_confidence")) {
                    for (Object value : List.of(Double.NaN, Double.POSITIVE_INFINITY, "garbage", "1e9999", List.of())) {
                        int before = wakeCalls.get();
                        check((boolean) actual.invoke(null, source, "malformed", "正常问题",
                                VoiceGateTest.acceptedParams().setObject(key, value), bridge), "actual entry rejects malformed " + key);
                        check(wakeCalls.get() == before, "malformed metadata never calls bridge");
                    }
                }
                gateField.set(null, new VoiceGate(VoiceGateTest.config(true), clock));
                int before = wakeCalls.get();
                check((boolean) actual.invoke(null, source, "strict", "正常问题", null, bridge), "actual strict null metadata rejected");
                check((boolean) compatible.invoke(null, source, "strict-compat", "正常问题", bridge), "compat overload cannot bypass strict gate");
                check(wakeCalls.get() == before, "strict overloads reject before bridge");
                gateField.set(null, gate);
                check(!(boolean) actual.invoke(null, source, "optional", "正常问题", null, bridge), "actual optional metadata accepted");
                check(!(boolean) compatible.invoke(null, source, "optional-compat", "正常问题", bridge), "compat overload delegates optional policy");

                gate.reset();
                wakeGate.reset();
                int warmBefore = wakeCalls.get();
                check((boolean) actual.invoke(null, source, "quality-first", "你好", VoiceGateTest.acceptedParams().setDouble("snr_db", 1.0), bridge), "failed quality rejected at actual entry");
                check((boolean) actual.invoke(null, source, "not-warmed", "是",
                        VoiceGateTest.acceptedParams().setObject("kws_detected", null), bridge),
                        "failed entry did not warm wake session");
                check(wakeCalls.get() == warmBefore, "quality failure and cold followup never reach bridge");
                check(!(boolean) actual.invoke(null, source, "wrapped-front", "你好", VoiceGateTest.acceptedParams().setDouble("doa_angle_deg", 359.0), bridge), "wrapped DOA accepted at actual entry");
                clock.advanceMillis(4999);
                check(!(boolean) actual.invoke(null, source, "warm", "是",
                        VoiceGateTest.acceptedParams().setBoolean("kws_detected", false), bridge),
                        "actual warm followup accepted without duplicate KWS enforcement");
                clock.advanceMillis(5000);
                check((boolean) actual.invoke(null, source, "expired", "否",
                        VoiceGateTest.acceptedParams().setBoolean("kws_detected", false), bridge),
                        "actual wake session expires at monotonic boundary");

                linkcraft.set(true);
                check((boolean) actual.invoke(null, source, "linkcraft", "灵犀你好", VoiceGateTest.acceptedParams(), bridge), "linkcraft rejects a valid wake");
                linkcraft.set(false);
                before = wakeCalls.get();
                check((boolean) actual.invoke(null, source, "after-linkcraft", "是",
                        VoiceGateTest.acceptedParams().setObject("kws_detected", null), bridge),
                        "linkcraft reset prevents warm followup");
                check(wakeCalls.get() == before, "reset rejection is local");
                statusOnly.set(true);
                check((boolean) actual.invoke(null, source, "status-only", "你好", VoiceGateTest.acceptedParams(), bridge), "LinkCraft status-only response protected");
                statusOnly.set(false);
                check(!wakeGate.admit("", "status-reset", "是",
                        VoiceGateTest.acceptedParams().setObject("kws_detected", null)).accepted(),
                        "status-only LinkCraft wake reset retained");

                silentUntil.setLong(tts, Long.MAX_VALUE);
                spoken.set(tts, SpeechInputPolicy.normalize("灵犀，今天我们介绍机器人导航和讲解功能"));
                gate.reset();
                wakeGate.reset();
                before = wakeCalls.get();
                check((boolean) actual.invoke(null, source, "echo", "灵犀，今天我们介绍机器人导航和讲解功能", VoiceGateTest.acceptedParams(), bridge), "addressed playback echo rejected");
                check((boolean) actual.invoke(null, source, "background", "正常问题", VoiceGateTest.acceptedParams().setBoolean("kws_detected", false), bridge), "unaddressed playback rejected without fresh wake");
                check(wakeCalls.get() == before, "echo and background do not touch bridge");
                check((boolean) actual.invoke(null, source, "after-playback-reject", "是",
                        VoiceGateTest.acceptedParams().setObject("kws_detected", null), bridge),
                        "echo and background cannot warm wake gate");
                check((boolean) actual.invoke(null, source, "addressed-bad", "灵犀，继续讲解", VoiceGateTest.acceptedParams().setDouble("snr_db", 1.0), bridge), "addressed request still needs valid quality");
                check(!(boolean) actual.invoke(null, source, "addressed-good", "灵犀灵犀，正常问题", VoiceGateTest.acceptedParams(), bridge), "valid addressed question interrupts playback");
                check(!(boolean) actual.invoke(null, source, "kws-without-prefix", "正常问题", VoiceGateTest.acceptedParams(), bridge),
                        "fresh KWS interrupts playback when ASR omits wake phrase");

                linkcraft.set(true);
                for (String text : List.of("停", "停止", "请立即停止导航", "灵犀，停下！", "别走", "取消导航", "急停", "stop",
                        "停止讲解", "结束任务", "请结束讲解任务", "停止灵创")) {
                    before = wakeCalls.get();
                    int oldStop = stopCalls.get();
                    int oldEnd = endTaskCalls.get();
                    AgentParam invalid = VoiceGateTest.acceptedParams().setDouble("snr_db", Double.NaN).setDouble("doa_angle_deg", 180.0);
                    check(!(boolean) actual.invoke(null, source, "safety", text, invalid, bridge), "urgent stop survives gates: " + text);
                    check(wakeCalls.get() == before, "urgent stop bypasses tour wake request: " + text);
                    // Null model/config/store makes any accidental dependency fail immediately.
                    process.invoke(null, "safety", text, null, bridge, null, null, new AtomicBoolean());
                    check(stopCalls.get() + endTaskCalls.get() == oldStop + oldEnd + 1, "urgent stop dispatched exactly once: " + text);
                    boolean taskStop = text.contains("讲解") || text.contains("任务") || text.contains("灵创");
                    check(taskStop ? endTaskCalls.get() == oldEnd + 1 : stopCalls.get() == oldStop + 1, "stop uses expected endpoint: " + text);
                }
                check(unexpectedCalls.get() == 0, "safety never lists points, marks interaction or matches tasks");
                silentUntil.setLong(tts, 0);
                linkcraft.set(false);
            }

            for (String source : List.of("audio2llm_wake", "audio2tts_wake")) {
                gate.reset();
                wakeGate.reset();
                int before = wakeCalls.get();
                Object session = audio.invoke(null, "item", "bad-audio", source, VoiceGateTest.acceptedParams().setString("snr_db", "bad"), bridge);
                Method ignored = session.getClass().getDeclaredMethod("shouldIgnore");
                ignored.setAccessible(true);
                check((boolean) ignored.invoke(session), "audio metadata rejected through actual session entry");
                check(wakeCalls.get() == before, "bad audio never reaches bridge");
                linkcraft.set(true);
                session = audio.invoke(null, "item", "linkcraft-audio", source, VoiceGateTest.acceptedParams(), bridge);
                check((boolean) ignored.invoke(session), "linkcraft blocks actual audio session");
                linkcraft.set(false);
                session = audio.invoke(null, "item", "after-linkcraft-audio", source,
                        VoiceGateTest.acceptedParams().setObject("kws_detected", null), bridge);
                check((boolean) ignored.invoke(session), "audio linkcraft rejection resets wake session");
                session = audio.invoke(null, "item", "valid-audio", source, VoiceGateTest.acceptedParams(), bridge);
                check(!(boolean) ignored.invoke(session), "valid audio entry retained");
                silentUntil.setLong(tts, Long.MAX_VALUE);
                session = audio.invoke(null, "item", "busy-audio", source, VoiceGateTest.acceptedParams(), bridge);
                check((boolean) ignored.invoke(session), "busy playback still rejects audio session");
                silentUntil.setLong(tts, 0);
                session = audio.invoke(null, "item", "after-busy-audio", source,
                        VoiceGateTest.acceptedParams().setObject("kws_detected", null), bridge);
                check((boolean) ignored.invoke(session), "rejected audio cannot warm subsequent audio");
            }
            Properties enforceProps = new Properties();
            enforceProps.putAll(props);
            enforceProps.setProperty("voice.gate.wakeMode", "enforce");
            enforceProps.setProperty("voice.gate.soc2LogEnabled", "true");
            enforceProps.setProperty("voice.gate.soc2LogRoot", soc2Root.toString());
            enforceProps.setProperty("voice.gate.soc2LogAttempts", "1");
            enforceProps.setProperty("voice.gate.soc2LogRetryMs", "0");
            AppConfig enforceConfig = constructor.newInstance(enforceProps);
            WakeSessionGate enforceWakeGate = new WakeSessionGate(enforceConfig, clock);
            wakeGateField.set(null, enforceWakeGate);
            gateField.set(null, new VoiceGate(enforceConfig, clock));
            soc2WakeField.set(null, new Soc2WakeEvidenceProvider(enforceConfig));
            int beforeEnforce = wakeCalls.get();
            check((boolean) actual.invoke(null, "Asr2Tts", "cold-enforce", "今天星期几", null, bridge),
                    "production entry rejects cold request without wake evidence");
            check(wakeCalls.get() == beforeEnforce, "cold request is rejected before tour bridge");
            check((boolean) actual.invoke(null, "Asr2Tts", "addressed-enforce", "灵犀灵犀，今天星期几", null, bridge),
                    "production entry rejects transcript-only addressed wake");
            enforceWakeGate.reset();
            enforceWakeGate.onState("", "state-callback", "listening_status",
                    "{\"state\":\"listening\",\"wakeup_state\":\"already_wake_up\","
                            + "\"event_id\":\"state-enforce\",\"updated_at_ms\":10}");
            check(!(boolean) actual.invoke(null, "Asr2Tts", "state-enforce", "今天星期几", null, bridge),
                    "production entry accepts matching SDK listening state");
            enforceWakeGate.reset();
            ((VoiceGate) gateField.get(null)).reset();
            enforceWakeGate.onState("", "state-addressed-callback", "listening_status",
                    "{\"state\":\"listening\",\"wakeup_state\":\"already_wake_up\","
                            + "\"event_id\":\"state-addressed\",\"updated_at_ms\":11}");
            check(!(boolean) actual.invoke(null, "Asr2Tts", "state-addressed", "灵犀灵犀，今天星期几", null, bridge),
                    "production entry accepts real SDK wake when ASR retains the wake phrase");

            enforceWakeGate.reset();
            ((VoiceGate) gateField.get(null)).reset();
            int beforeSoc2Wake = wakeCalls.get();
            check(!(boolean) actual.invoke(null, "Asr2Tts", "soc2-fresh", "开始讲解", null, bridge),
                    "production entry accepts exact fresh SOC2 wake evidence");
            check(wakeCalls.get() == beforeSoc2Wake + 1,
                    "fresh SOC2 wake reaches the interaction bridge exactly once");
            AtomicBoolean interaction = new AtomicBoolean(false);
            process.invoke(null, "soc2-fresh", "开始讲解", enforceConfig, bridge, null, null, interaction);
            check(startTaskCalls.get() == 1,
                    "fresh SOC2 wake continues through the local rule to task start");

            enforceWakeGate.reset();
            ((VoiceGate) gateField.get(null)).reset();
            int beforeRejectedSoc2 = wakeCalls.get();
            check((boolean) actual.invoke(null, "Asr2Tts", "soc2-stale", "开始讲解", null, bridge),
                    "production entry rejects stale SOC2 wake evidence");
            check((boolean) actual.invoke(null, "Asr2Tts", "soc2-missing", "开始讲解", null, bridge),
                    "production entry rejects missing SOC2 wake evidence");
            check(wakeCalls.get() == beforeRejectedSoc2,
                    "stale and missing SOC2 evidence never reach the bridge");

            enforceWakeGate.reset();
            ((VoiceGate) gateField.get(null)).reset();
            soc2WakeField.set(null, new Soc2WakeEvidenceProvider(
                    true, soc2Root.resolve("missing"), 8000, 1, 0, 65536));
            check((boolean) actual.invoke(null, "Asr2Tts", "soc2-unavailable", "开始讲解", null, bridge),
                    "production entry fails closed when the SOC2 log is unavailable");
            check(unexpectedCalls.get() == 0, "regression uses only expected local bridge endpoints");
            System.out.println("VoiceAgentGateRegression passed: " + checks + " assertions");
        } finally {
            server.stop(0);
            gateField.set(null, originalGate);
            wakeGateField.set(null, originalWakeGate);
            soc2WakeField.set(null, originalSoc2Wake);
            silentUntil.setLong(tts, originalSilentUntil);
            spoken.set(tts, originalSpoken);
            try (var paths = Files.walk(soc2Root)) {
                paths.sorted(Comparator.reverseOrder()).forEach(path -> {
                    try { Files.deleteIfExists(path); }
                    catch (Exception ignored) { }
                });
            }
        }
    }
}
