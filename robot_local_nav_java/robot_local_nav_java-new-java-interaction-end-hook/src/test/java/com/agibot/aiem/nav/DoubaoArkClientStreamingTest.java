package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

final class DoubaoArkClientStreamingTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    private static AppConfig config(String modelUrl, String navUrl, boolean streaming) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test-model");
        properties.setProperty("doubao.url", modelUrl);
        properties.setProperty("doubao.streamingReplies", Boolean.toString(streaming));
        properties.setProperty("tts.delayBeforeSpeakMs", "0");
        properties.setProperty("nav.baseUrl", navUrl);
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    private static String event(String content, String finishReason) {
        JSONObject delta = new JSONObject();
        delta.put("content", content);
        JSONObject choice = new JSONObject();
        choice.put("delta", delta);
        if (finishReason != null) {
            choice.put("finish_reason", finishReason);
        }
        JSONArray choices = new JSONArray();
        choices.add(choice);
        JSONObject root = new JSONObject();
        root.put("choices", choices);
        return "data: " + root.toJSONString() + "\n\n";
    }

    private static void resetTtsController() throws Exception {
        Field controllerField = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        controllerField.setAccessible(true);
        Object controller = controllerField.get(null);
        for (String name : List.of(
                "requestInFlight", "lastAcceptedAtMillis", "silentUntilMillis",
                "nextRequestToken", "activeRequestToken", "activeStreamingSession",
                "lastNormalizedText")) {
            Field field = controller.getClass().getDeclaredField(name);
            field.setAccessible(true);
            if (field.getType() == boolean.class) {
                field.setBoolean(controller, false);
            } else if (field.getType() == long.class) {
                field.setLong(controller, 0L);
            } else if (name.equals("activeStreamingSession")) {
                field.set(controller, null);
            } else {
                field.set(controller, "");
            }
        }
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static Object acquireTts(String text, String priorityName) throws Exception {
        Field controllerField = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        controllerField.setAccessible(true);
        Object controller = controllerField.get(null);
        Class<?> priorityClass = null;
        for (Class<?> nested : LingxiNavAgentApp.class.getDeclaredClasses()) {
            if (nested.getSimpleName().equals("TtsPriority")) {
                priorityClass = nested;
                break;
            }
        }
        if (priorityClass == null) {
            throw new AssertionError("TtsPriority class not found");
        }
        Object priority = Enum.valueOf((Class) priorityClass, priorityName);
        java.lang.reflect.Method method = controller.getClass().getDeclaredMethod(
                "tryAcquire", String.class, priorityClass);
        method.setAccessible(true);
        return method.invoke(controller, text, priority);
    }

    private static boolean isCurrentTts(Object decision) throws Exception {
        Field controllerField = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        controllerField.setAccessible(true);
        Object controller = controllerField.get(null);
        java.lang.reflect.Method method =
                controller.getClass().getDeclaredMethod("isCurrent", decision.getClass());
        method.setAccessible(true);
        return (boolean) method.invoke(controller, decision);
    }

    private static void finishStreamingDecision(Object decision) throws Exception {
        Field controllerField = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        controllerField.setAccessible(true);
        Object controller = controllerField.get(null);
        java.lang.reflect.Method method = controller.getClass().getDeclaredMethod(
                "finishStreaming", decision.getClass(), boolean.class, String.class);
        method.setAccessible(true);
        method.invoke(controller, decision, false, "");
    }

    private static boolean requestInFlight() throws Exception {
        Field controllerField = LingxiNavAgentApp.class.getDeclaredField("TTS_CONTROLLER");
        controllerField.setAccessible(true);
        Object controller = controllerField.get(null);
        Field inFlight = controller.getClass().getDeclaredField("requestInFlight");
        inFlight.setAccessible(true);
        return inFlight.getBoolean(controller);
    }

    private static final class Rig {
        private final HttpServer server;
        private final ExecutorService executor = Executors.newCachedThreadPool();
        private final List<JSONObject> speechRequests = new CopyOnWriteArrayList<>();
        private final CountDownLatch firstSpeechAccepted = new CountDownLatch(1);
        private final CountDownLatch continuationReceived = new CountDownLatch(1);
        private final CountDownLatch releaseContinuation = new CountDownLatch(1);
        private final AtomicBoolean firstSpeechBeforeModelFinished = new AtomicBoolean();
        private final AtomicInteger modelRequests = new AtomicInteger();
        private final boolean failContinuation;
        private final boolean blockContinuation;

        private Rig(boolean truncated, boolean failBeforeFirst) throws IOException {
            this(truncated, failBeforeFirst, false);
        }

        private Rig(boolean truncated, boolean failBeforeFirst, boolean failContinuation)
                throws IOException {
            this(truncated, failBeforeFirst, failContinuation, false);
        }

        private Rig(
                boolean truncated, boolean failBeforeFirst,
                boolean failContinuation, boolean blockContinuation)
                throws IOException {
            this.failContinuation = failContinuation;
            this.blockContinuation = blockContinuation;
            this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            this.server.setExecutor(this.executor);
            this.server.createContext("/chat/completions", exchange -> {
                JSONObject request = JSONObject.parseObject(
                        new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                this.modelRequests.incrementAndGet();
                if (failBeforeFirst && request.getBooleanValue("stream")) {
                    byte[] response = "{\"error\":\"stream unavailable\"}".getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(503, response.length);
                    exchange.getResponseBody().write(response);
                    exchange.close();
                    return;
                }
                if (failBeforeFirst) {
                    String content =
                            "{\"intent\":\"chat\",\"question\":\"q\",\"reply\":\"Recovered answer.\"}";
                    JSONObject message = new JSONObject();
                    message.put("content", content);
                    JSONObject choice = new JSONObject();
                    choice.put("message", message);
                    JSONArray choices = new JSONArray();
                    choices.add(choice);
                    JSONObject response = new JSONObject();
                    response.put("choices", choices);
                    byte[] bytes = response.toJSONString().getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(200, bytes.length);
                    exchange.getResponseBody().write(bytes);
                    exchange.close();
                    return;
                }
                exchange.getResponseHeaders().set("Content-Type", "text/event-stream; charset=utf-8");
                exchange.sendResponseHeaders(200, 0);
                try (OutputStream output = exchange.getResponseBody()) {
                    String first = truncated
                            ? "{\"intent\":\"chat\",\"question\":\"q\",\"reply\":\"Third sentence.Fourth sentence."
                            : "{\"intent\":\"chat\",\"question\":\"q\",\"reply\":\"First sentence.";
                    output.write(event(first, null).getBytes(StandardCharsets.UTF_8));
                    output.flush();
                    boolean receivedEarly;
                    try {
                        receivedEarly = this.firstSpeechAccepted.await(5, TimeUnit.SECONDS);
                    } catch (InterruptedException exception) {
                        Thread.currentThread().interrupt();
                        throw new IOException("Interrupted while waiting for first TTS segment", exception);
                    }
                    this.firstSpeechBeforeModelFinished.set(receivedEarly);
                    if (!truncated) {
                        output.write(event(" Second sentence.\"}", "stop").getBytes(StandardCharsets.UTF_8));
                        output.write("data: [DONE]\n\n".getBytes(StandardCharsets.UTF_8));
                        output.flush();
                    }
                } finally {
                    exchange.close();
                }
            });
            this.server.createContext("/tts/speak", exchange -> {
                JSONObject payload = JSONObject.parseObject(
                        new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                this.speechRequests.add(payload);
                if (this.blockContinuation
                        && payload.getBooleanValue("streaming")
                        && payload.getIntValue("stream_sequence") == 1) {
                    this.continuationReceived.countDown();
                    try {
                        if (!this.releaseContinuation.await(5, TimeUnit.SECONDS)) {
                            throw new IOException("Timed out waiting to release continuation");
                        }
                    } catch (InterruptedException exception) {
                        Thread.currentThread().interrupt();
                        throw new IOException("Interrupted while holding continuation", exception);
                    }
                }
                if (failContinuation
                        && payload.getBooleanValue("streaming")
                        && payload.getIntValue("stream_sequence") == 1) {
                    byte[] response =
                            "{\"ok\":false,\"accepted\":false}".getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(503, response.length);
                    exchange.getResponseBody().write(response);
                    exchange.close();
                    return;
                }
                if (payload.getIntValue("stream_sequence") == 0
                        && !"end".equals(payload.getString("stream_action"))) {
                    this.firstSpeechAccepted.countDown();
                }
                byte[] response = "{\"ok\":true,\"accepted\":true}".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(200, response.length);
                exchange.getResponseBody().write(response);
                exchange.close();
            });
            this.server.start();
        }

        private String baseUrl() {
            return "http://127.0.0.1:" + this.server.getAddress().getPort();
        }

        private void close() {
            this.server.stop(0);
            this.executor.shutdownNow();
        }
    }

    private static void testFirstSegmentPrecedesCompletedSse() throws Exception {
        resetTtsController();
        Rig rig = new Rig(false, false);
        try {
            AppConfig config = config(
                    rig.baseUrl() + "/chat/completions", rig.baseUrl(), true);
            LingxiNavAgentApp.StreamingTtsSession session =
                    new LingxiNavAgentApp.StreamingTtsSession(
                            "stream-happy", config, new NavBridgeClient(config));
            NavigationIntent intent = new DoubaoArkClient(config).parseInteractionStreaming(
                    "tell me something", List.of(), session::accept);

            check(rig.firstSpeechBeforeModelFinished.get(),
                    "first TTS segment is accepted while the SSE response is still open");
            check(session.complete(intent.reply, intent.reply),
                    "complete matching chat response closes the local streaming TTS path");
            check("First sentence. Second sentence.".equals(intent.reply),
                    "final JSON reply is reconstructed without loss");
            check(rig.speechRequests.size() == 3,
                    "two speech segments and one stream close are submitted");

            JSONObject first = rig.speechRequests.get(0);
            JSONObject second = rig.speechRequests.get(1);
            JSONObject end = rig.speechRequests.get(2);
            check(first.getIntValue("stream_sequence") == 0
                            && first.getBooleanValue("interrupt"),
                    "first segment starts the stream with interruption enabled");
            check(second.getIntValue("stream_sequence") == 1
                            && !second.getBooleanValue("interrupt"),
                    "continuation is submitted in order without interrupting its predecessor");
            check("end".equals(end.getString("stream_action")),
                    "completed response closes its TTS stream");
        } finally {
            rig.close();
        }
    }

    private static void testTruncatedSseDiscardsUncommittedSegments() throws Exception {
        resetTtsController();
        Rig rig = new Rig(true, false);
        try {
            AppConfig config = config(
                    rig.baseUrl() + "/chat/completions", rig.baseUrl(), true);
            LingxiNavAgentApp.StreamingTtsSession session =
                    new LingxiNavAgentApp.StreamingTtsSession(
                            "stream-truncated", config, new NavBridgeClient(config));
            boolean failed = false;
            try {
                new DoubaoArkClient(config).parseInteractionStreaming(
                        "tell me something", List.of(), session::accept);
            } catch (IOException expected) {
                failed = true;
            } finally {
                session.abort();
            }

            check(failed, "truncated SSE response is rejected");
            check(rig.firstSpeechBeforeModelFinished.get(),
                    "the first sentence may be accepted before a later stream failure");
            check(rig.speechRequests.size() == 2,
                    "abort submits no buffered continuation and sends one close request");
            check(rig.speechRequests.get(0).getIntValue("stream_sequence") == 0,
                    "only the already accepted first sequence reached the speech bridge");
            check("end".equals(rig.speechRequests.get(1).getString("stream_action")),
                    "aborting releases the bridge stream state");
        } finally {
            rig.close();
        }
    }

    private static void testHttpFailureBeforeSpeechRetriesNonStreaming() throws Exception {
        resetTtsController();
        Rig rig = new Rig(false, true);
        try {
            AppConfig config = config(
                    rig.baseUrl() + "/chat/completions", rig.baseUrl(), true);
            LingxiNavAgentApp.StreamingTtsSession session =
                    new LingxiNavAgentApp.StreamingTtsSession(
                            "stream-fallback", config, new NavBridgeClient(config));
            NavigationIntent intent = LingxiNavAgentApp.parseTextInteraction(
                    "stream-fallback",
                    "tell me something",
                    List.of(),
                    new DoubaoArkClient(config),
                    session);

            check("Recovered answer.".equals(intent.reply),
                    "a failed stream before speech retries through the ordinary model path");
            check(rig.modelRequests.get() == 2,
                    "stream failure is followed by exactly one non-streaming request");
            check(rig.speechRequests.isEmpty() && !session.hasAccepted(),
                    "no partial TTS is submitted before successful non-streaming fallback");
        } finally {
            rig.close();
        }
    }

    private static void testContinuationFailureFallsBackToFinalReply() throws Exception {
        resetTtsController();
        Rig rig = new Rig(false, false, true);
        try {
            AppConfig config = config(
                    rig.baseUrl() + "/chat/completions", rig.baseUrl(), true);
            LingxiNavAgentApp.StreamingTtsSession session =
                    new LingxiNavAgentApp.StreamingTtsSession(
                            "stream-continuation-fallback", config, new NavBridgeClient(config));
            NavigationIntent intent = LingxiNavAgentApp.parseTextInteraction(
                    "stream-continuation-fallback",
                    "tell me something",
                    List.of(),
                    new DoubaoArkClient(config),
                    session);

            check("First sentence. Second sentence.".equals(intent.reply),
                    "the complete model reply remains available after a continuation failure");
            check(session.complete(intent.reply, intent.reply),
                    "continuation failure replaces partial speech with the complete final reply");
            JSONObject fallback = rig.speechRequests.get(rig.speechRequests.size() - 1);
            check(!fallback.getBooleanValue("streaming")
                            && intent.reply.equals(fallback.getString("text")),
                    "the fallback submits the full reply through the ordinary TTS path");
            check(fallback.getBooleanValue("interrupt") && fallback.getBooleanValue("force"),
                    "the fallback request interrupts and replaces the partial stream");
        } finally {
            rig.close();
        }
    }

    private static void testStaleTtsDecisionCannotFinishCurrentOwner() throws Exception {
        resetTtsController();
        Object oldDecision = acquireTts("old reply", "NORMAL");
        Object newDecision = acquireTts("navigation reply", "URGENT");

        check(isCurrentTts(newDecision), "new urgent TTS decision becomes the current owner");
        check(!isCurrentTts(oldDecision), "older streaming decision loses ownership");
        finishStreamingDecision(oldDecision);
        check(isCurrentTts(newDecision) && requestInFlight(),
                "finishing a stale stream cannot clear the newer request state");
        finishStreamingDecision(newDecision);
        check(!requestInFlight(), "the owning decision can finish its request state");
    }

    private static void testNormalSpeechPreemptsStreamWithoutFullReplyFallback()
            throws Exception {
        resetTtsController();
        Rig rig = new Rig(false, false);
        try {
            AppConfig config = config(
                    rig.baseUrl() + "/chat/completions", rig.baseUrl(), true);
            LingxiNavAgentApp.StreamingTtsSession session =
                    new LingxiNavAgentApp.StreamingTtsSession(
                            "stream-preempted", config, new NavBridgeClient(config));
            NavigationIntent intent = new DoubaoArkClient(config).parseInteractionStreaming(
                    "tell me something", List.of(), session::accept);

            Object newerDecision = acquireTts("new normal chat reply", "NORMAL");
            check(isCurrentTts(newerDecision),
                    "a new normal chat TTS decision takes ownership from the active stream");
            check(session.complete(intent.reply, intent.reply),
                    "a preempted stream suppresses its full-reply fallback");
            check(rig.speechRequests.size() == 2,
                    "preemption closes the stream without submitting a full-reply fallback");
            check("end".equals(rig.speechRequests.get(1).getString("stream_action")),
                    "the preempted stream sends its close before returning");
            check(isCurrentTts(newerDecision) && requestInFlight(),
                    "old stream completion leaves the normal request owner intact");
            finishStreamingDecision(newerDecision);
        } finally {
            rig.close();
        }
    }

    private static void testInFlightContinuationFailureCannotFallbackAfterPreemption()
            throws Exception {
        resetTtsController();
        Rig rig = new Rig(false, false, true, true);
        AtomicBoolean completeResult = new AtomicBoolean();
        AtomicReference<Throwable> completionFailure = new AtomicReference<>();
        try {
            AppConfig config = config(
                    rig.baseUrl() + "/chat/completions", rig.baseUrl(), true);
            LingxiNavAgentApp.StreamingTtsSession session =
                    new LingxiNavAgentApp.StreamingTtsSession(
                            "stream-inflight-preempted", config, new NavBridgeClient(config));
            NavigationIntent intent = new DoubaoArkClient(config).parseInteractionStreaming(
                    "tell me something", List.of(), session::accept);

            Thread completion = new Thread(() -> {
                try {
                    completeResult.set(session.complete(intent.reply, intent.reply));
                } catch (Throwable failure) {
                    completionFailure.set(failure);
                }
            });
            completion.start();
            check(rig.continuationReceived.await(5, TimeUnit.SECONDS),
                    "continuation reaches the bridge before preemption");
            Object newerDecision = acquireTts("new normal chat reply", "NORMAL");
            rig.releaseContinuation.countDown();
            completion.join(5000);

            check(!completion.isAlive(), "completion exits after the in-flight request returns");
            check(completionFailure.get() == null,
                    "the in-flight continuation completes without an unexpected exception");
            check(completeResult.get(),
                    "preemption suppresses fallback after the continuation failure");
            check(rig.speechRequests.size() == 3,
                    "only first segment, in-flight continuation, and close are submitted");
            check("end".equals(rig.speechRequests.get(2).getString("stream_action")),
                    "the old stream closes while its continuation is in flight");
            check(isCurrentTts(newerDecision) && requestInFlight(),
                    "late stream completion cannot clear the newer TTS owner");
            finishStreamingDecision(newerDecision);
        } finally {
            rig.releaseContinuation.countDown();
            rig.close();
        }
    }

    public static void main(String[] args) throws Exception {
        AppConfig defaults = config(
                "http://127.0.0.1:1/chat/completions", "http://127.0.0.1:1", false);
        check(!defaults.doubaoStreamingReplies,
                "streaming replies remain disabled unless explicitly opted in");
        testFirstSegmentPrecedesCompletedSse();
        testTruncatedSseDiscardsUncommittedSegments();
        testHttpFailureBeforeSpeechRetriesNonStreaming();
        testContinuationFailureFallsBackToFinalReply();
        testStaleTtsDecisionCannotFinishCurrentOwner();
        testNormalSpeechPreemptsStreamWithoutFullReplyFallback();
        testInFlightContinuationFailureCannotFallbackAfterPreemption();
        System.out.println("DoubaoArkClientStreamingTest passed: " + checks + " assertions");
    }
}
