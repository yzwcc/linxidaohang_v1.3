package local.lingxi;

import com.agibot.aiem.sdk.*;
import com.agibot.aiem.sdk.active.*;
import com.agibot.aiem.sdk.enums.FlowMode;
import com.agibot.aiem.sdk.passive.*;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import javax.net.ssl.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.cert.CertificateFactory;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Single-robot, loopback-only integration with the pinned AgentSDK artifact. */
public final class AgentMain {
    private static final String GATEWAY = "wss://open.agibot.com/api/V1/open-portal/app/wss/agent-sdk";
    private final WakeGate gate = new WakeGate();
    private final VadEndRegistry vadEnds = new VadEndRegistry();
    private final SpeechRequests speechRequests = new SpeechRequests();
    private final String agentId = required("LINKSOUL_AGENT_ID");
    private final String token = required("LINGXI_AGENT_INTERNAL_TOKEN");
    private final URI api = URI.create(System.getenv().getOrDefault("LINGXI_API_URL", "https://127.0.0.1:3000"));
    private final ConcurrentMap<String, ReplyContext> replies = new ConcurrentHashMap<>();
    private final ConcurrentMap<String, Playback> playing = new ConcurrentHashMap<>();
    private final Semaphore speakerLock = new Semaphore(1);
    private final ExecutorService workers = new ThreadPoolExecutor(4, 4, 0, TimeUnit.MILLISECONDS,
            new ArrayBlockingQueue<>(8), new ThreadPoolExecutor.AbortPolicy());
    private final ScheduledExecutorService monitor = Executors.newSingleThreadScheduledExecutor();
    private final HttpClient http;
    private final AgentSdk sdk;
    private volatile boolean online;
    private volatile boolean authenticated;
    private volatile String lastGateReason = "waiting_for_real_wakeup";
    private WakeSession wakeSession;

    private record WakeSession(String id, CompletableFuture<Void> delivered) {}
    private record ReplyContext(String sessionId, String eventId, Asr2LlmResponse response,
            AtomicBoolean interrupted, AtomicBoolean closed, AtomicBoolean done) {}
    static final class Playback {
        final String correlation;
        volatile String event;
        final String item;
        final long createdAt;
        final long began = System.nanoTime();
        final CompletableFuture<Double> complete = new CompletableFuture<>();
        final CompletableFuture<Void> terminated = new CompletableFuture<>();
        volatile Double firstAudio;
        volatile TaskFlowRequest flow;
        ReplyContext reply;
        private long latestAt;
        private final List<JSONObject> earlyStates = new ArrayList<>();
        Playback(String correlation, String event, String item) {
            this(correlation, event, item, System.currentTimeMillis());
        }
        Playback(String correlation, String event, String item, long createdAt) {
            this.correlation = correlation; this.event = event; this.item = item;
            this.createdAt = createdAt;
        }
        synchronized void bindEvent(String id, long now) {
            if (id == null || id.isBlank() || (event != null && !event.equals(id))) {
                complete.completeExceptionally(new IllegalStateException("Unconfirmed flow event"));
                return;
            }
            event = id;
            var pending = new ArrayList<>(earlyStates);
            earlyStates.clear();
            for (JSONObject state : pending) accept(state, now);
        }
        synchronized void accept(JSONObject state, long now) {
            long at = state.getLongValue("updated_at_ms");
            if (at < createdAt - 2000 || at <= 0 || Math.abs(now - at) > 30_000 || terminated.isDone()) return;
            if (event == null && item == null) {
                if (earlyStates.size() < 16) earlyStates.add(new JSONObject(state));
                return;
            }
            boolean matches = item != null ? item.equals(state.getString("item_id")) &&
                    event != null && event.equals(state.getString("event_id")) :
                    event != null && event.equals(state.getString("event_id"));
            if (!matches || at < latestAt) return;
            latestAt = at;
            String status = state.getString("state");
            if ("playing".equals(status) && firstAudio == null) {
                firstAudio = (System.nanoTime() - began) / 1_000_000.0;
            }
            if ("finished".equals(status)) {
                terminated.complete(null);
                if (firstAudio != null) complete.complete(firstAudio);
                else complete.completeExceptionally(new IllegalStateException("First audio was not observed"));
            }
            if ("interrupted".equals(status) || "failed".equals(status)) {
                terminated.complete(null);
                complete.completeExceptionally(new IllegalStateException("Playback interrupted or failed"));
            }
        }
    }

    AgentMain() throws Exception {
        if (!"https".equals(api.getScheme()) || !Set.of("127.0.0.1", "localhost", "[::1]").contains(api.getHost())) {
            throw new IllegalArgumentException("Agent API must be local HTTPS");
        }
        KeyStore roots = KeyStore.getInstance(KeyStore.getDefaultType());
        roots.load(null, null);
        try (var input = Files.newInputStream(Path.of(required("LINGXI_TLS_CA")))) {
            roots.setCertificateEntry("robot-ca", CertificateFactory.getInstance("X.509").generateCertificate(input));
        }
        TrustManagerFactory trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        trust.init(roots);
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, trust.getTrustManagers(), null);
        http = HttpClient.newBuilder().sslContext(tls).connectTimeout(Duration.ofSeconds(2)).build();
        Path credentialsPath = Path.of(required("LINGXI_CREDENTIALS_FILE"));
        JSONObject credentials = JSON.parseObject(Files.readString(credentialsPath));
        sdk = AgentSdk.create(GATEWAY, credential(credentials, "linksoulAppId"),
                credential(credentials, "linksoulAppKey"), credential(credentials, "linksoulAppSecret"));
    }

    private void start() throws Exception {
        sdk.registerAuth((id, code, ignored) -> { authenticated = code == 0; if (!authenticated) clearSession(); });
        sdk.registerAsr2Llm(new Asr2LlmCallback(sdk) {
            @Override public void onRobotOnline(String id, AgentMeta meta) { if (agentId.equals(id)) online = true; }
            @Override public void onRobotOffline(String id) { if (agentId.equals(id)) { online = false; clearSession(); } }
            @Override public void onFaceInfo(String id, String event, AgentParam param) {}
            @Override public void onVideoFrame(String id, String event, int flag, byte[] bytes, boolean key, long ts, AgentParam param) {}
            @Override public void onGreetSignal(String id, String event, AgentParam param, GreetResponse response) {}
            @Override public void onHistories(String id, String event, List<HistoryInfo> histories) {}
            @Override public void onState(String id, String event, String name, String value) {
                if (!agentId.equals(id) || !authenticated) return;
                try {
                    JSONObject state = JSON.parseObject(value);
                    if ("listening_status".equals(name)) {
                        acceptListening(state);
                    } else if ("vad_status".equals(name)) {
                        vadEnds.accept(state, System.currentTimeMillis());
                    } else if ("tts_status".equals(name)) {
                        acceptTts(state);
                    }
                } catch (RuntimeException ignored) { /* Ignore modules with a different JSON shape. */ }
            }
            @Override public void onRequest(String id, String event, String text, AgentParam param, Asr2LlmResponse response) {
                acceptRecognition(id, event, text, param, response);
            }
        });
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 18180), 8);
        server.setExecutor(workers);
        server.createContext("/", this::handle);
        server.start();
        monitor.scheduleAtFixedRate(() -> {
            synchronized (speechRequests) {
                if (wakeSession != null && !wakeSession.id.equals(gate.current(System.currentTimeMillis()))) {
                    endWake("waiting_for_real_wakeup");
                }
            }
        }, 1, 1, TimeUnit.SECONDS);
        sdk.initialize();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            clearSession(); server.stop(0); sdk.release(); workers.shutdownNow(); monitor.shutdownNow();
        }));
    }

    private void clearSession() {
        synchronized (speechRequests) {
            gate.clear(System.currentTimeMillis());
            endWake("waiting_for_real_wakeup");
            for (Playback playback : playing.values()) {
                playback.complete.completeExceptionally(new IllegalStateException("Agent disconnected"));
            }
        }
    }

    private void acceptListening(JSONObject state) {
        synchronized (speechRequests) {
            String session = gate.acceptListening(state, System.currentTimeMillis());
            if (session != null) {
                endWake("waiting_for_real_wakeup");
                lastGateReason = "ready";
                var delivered = postEvent(Map.of("kind", "wake", "sessionId", session,
                        "eventId", "wake:" + state.getLongValue("updated_at_ms"),
                        "occurredAt", state.getLongValue("updated_at_ms"))).thenApply(ignored -> (Void) null);
                wakeSession = new WakeSession(session, delivered);
                delivered.whenComplete((ignored, error) -> {
                    if (error == null) return;
                    synchronized (speechRequests) {
                        if (wakeSession != null && wakeSession.id.equals(session)) {
                            gate.clear(System.currentTimeMillis());
                            endWake("wake_delivery_failed");
                        }
                    }
                });
            } else if (wakeSession != null && !wakeSession.id.equals(gate.current(System.currentTimeMillis()))) {
                endWake("waiting_for_real_wakeup");
            }
        }
    }

    // Called under speechRequests so emission and invalidation cannot cross.
    private void endWake(String reason) {
        WakeSession previous = wakeSession;
        if (previous == null) return;
        wakeSession = null;
        lastGateReason = reason;
        for (var entry : replies.entrySet()) {
            ReplyContext context = entry.getValue();
            if (context.sessionId.equals(previous.id) && replies.remove(entry.getKey(), context)) {
                interruptReply(context);
                finishReply(context);
            }
        }
        for (Playback playback : playing.values()) {
            if (playback.reply != null && playback.reply.sessionId.equals(previous.id)) {
                playback.complete.completeExceptionally(new IllegalStateException("Voice session ended"));
            }
        }
        postEvent(Map.of("kind", "session-ended", "sessionId", previous.id,
                "eventId", "end:" + UUID.randomUUID(), "occurredAt", System.currentTimeMillis()));
    }

    private void acceptRecognition(String id, String event, String text, AgentParam param, Asr2LlmResponse response) {
        synchronized (speechRequests) {
            if (!agentId.equals(id) || !online || !authenticated ||
                    event == null || event.isBlank() || event.length() > 180) return;
            long occurredAt = System.currentTimeMillis();
            String session = gate.current(occurredAt);
            WakeSession wake = wakeSession;
            if (session == null || wake == null || !session.equals(wake.id)) {
                lastGateReason = "missing_real_wakeup"; return;
            }
            // Confidence is not guaranteed by the generic SDK contract. Never coerce or synthesize it.
            AsrConfidence.Result evidence = AsrConfidence.read(param);
            if (!evidence.accepted()) { lastGateReason = evidence.rejection(); return; }
            double confidence = evidence.value();
            if (text == null || text.isBlank() || text.length() > 4000) {
                lastGateReason = "asr_payload_invalid"; return;
            }
            lastGateReason = "ready";
            gate.renew(session, occurredAt);
            String correlation = "reply:" + session + ":" + event;
            ReplyContext context = new ReplyContext(session, event, response,
                    new AtomicBoolean(), new AtomicBoolean(), new AtomicBoolean());
            if (replies.putIfAbsent(correlation, context) != null) return;
            Long vadEndedAt = vadEnds.consume(event, occurredAt);
            wake.delivered.thenCompose(ignored -> {
                synchronized (speechRequests) {
                    if (context.closed.get() || !session.equals(gate.current(System.currentTimeMillis()))) {
                        return CompletableFuture.completedFuture(null);
                    }
                    Map<String, Object> recognition = new HashMap<>();
                    recognition.put("kind", "recognition");
                    recognition.put("sessionId", session);
                    recognition.put("eventId", event);
                    recognition.put("occurredAt", occurredAt);
                    recognition.put("transcript", text);
                    recognition.put("confidence", confidence);
                    recognition.put("isFinal", true);
                    if (vadEndedAt != null) recognition.put("vadEndedAt", vadEndedAt);
                    return postEvent(recognition);
                }
            }).whenComplete((ignored, error) -> {
                synchronized (speechRequests) {
                    replies.remove(correlation, context);
                    finishReply(context);
                }
            });
        }
    }

    private void finishReply(ReplyContext context) {
        context.closed.set(true);
        if (context.done.compareAndSet(false, true)) {
            try { context.response.onLlmDone(context.eventId); }
            catch (RuntimeException ignored) { lastGateReason = "passive_reply_close_failed"; }
        }
    }

    private void interruptReply(ReplyContext context) {
        if (context.interrupted.compareAndSet(false, true)) {
            try { context.response.onInterrupt(context.eventId, "chat", null); }
            catch (RuntimeException ignored) { lastGateReason = "passive_reply_interrupt_failed"; }
        }
    }

    private void acceptTts(JSONObject state) {
        for (Playback playback : playing.values()) {
            playback.accept(state, System.currentTimeMillis());
        }
    }

    private void handle(HttpExchange exchange) {
        try {
            String supplied = exchange.getRequestHeaders().getFirst("Authorization");
            if (supplied == null || !MessageDigest.isEqual(supplied.getBytes(StandardCharsets.UTF_8),
                    ("Bearer " + token).getBytes(StandardCharsets.UTF_8))) {
                send(exchange, 401, Map.of("message", "Unauthorized")); return;
            }
            String route = exchange.getRequestURI().getPath();
            if ("/health".equals(route) && "GET".equals(exchange.getRequestMethod())) {
                boolean uncertain = playing.values().stream().anyMatch(p -> p.complete.isDone() && !p.terminated.isDone());
                send(exchange, 200, Map.of("online", online && authenticated, "sdkVersion", "1.5.0-SNAPSHOT",
                        "voiceGate", uncertain ? "tts_completion_unconfirmed" : lastGateReason)); return;
            }
            if (!"POST".equals(exchange.getRequestMethod())) { send(exchange, 405, Map.of("message", "Method not allowed")); return; }
            byte[] bytes = exchange.getRequestBody().readNBytes(32_001);
            if (bytes.length > 32_000) { send(exchange, 413, Map.of("message", "Too large")); return; }
            JSONObject body = JSON.parseObject(new String(bytes, StandardCharsets.UTF_8));
            String correlation = body.getString("correlationId");
            if (correlation == null || correlation.length() > 600) throw new IllegalArgumentException();
            if ("/v1/speech/cancel".equals(route)) {
                cancel(correlation); send(exchange, 200, Map.of("confirmed", true)); return;
            }
            if ("/v1/speak".equals(route)) {
                String text = body.getString("text");
                if (text == null || text.isBlank() || text.length() > 4000) throw new IllegalArgumentException();
                double firstAudio = speak(text, correlation, body.getLongValue("issuedAt"));
                send(exchange, 200, Map.of("completed", true, "firstAudioMs", firstAudio)); return;
            }
            // Effects require a target-verified manifest; no arbitrary SDK skill or motion endpoint.
            send(exchange, 501, Map.of("message", "Effect is not target-verified"));
        } catch (IllegalArgumentException error) {
            send(exchange, 400, Map.of("message", "Invalid request"));
        } catch (Exception error) {
            send(exchange, 503, Map.of("message", "Playback is not confirmed; check robot status"));
        } finally { exchange.close(); }
    }

    private double speak(String text, String correlation, long issuedAt) throws Exception {
        if (!online || !authenticated || !speakerLock.tryAcquire()) throw new IllegalStateException("Speaker busy/offline");
        Playback playback = null;
        String flowId = "lingxi_" + UUID.randomUUID();
        try {
          synchronized (speechRequests) {
            ReplyContext reply = replies.get(correlation);
            boolean live = reply != null && !reply.closed.get() &&
                    reply.sessionId.equals(gate.current(System.currentTimeMillis()));
            speechRequests.check(correlation, issuedAt, System.currentTimeMillis(), live);
            if (!online || !authenticated) throw new IllegalStateException("Agent disconnected");
            String item = reply == null ? null : "lingxi_" + UUID.randomUUID();
            Playback active = new Playback(correlation, reply == null ? null : reply.eventId, item);
            active.reply = reply;
            playback = active;
            playing.put(correlation, active);
            if (reply != null) {
                if (reply.interrupted.compareAndSet(false, true)) reply.response.onInterrupt(reply.eventId, "chat", null);
                reply.response.onLlmItemDelta(reply.eventId, item, text);
                reply.response.onLlmItemDone(reply.eventId, item);
            } else {
                TaskFlowRequest flow = new TaskFlowRequest(sdk, agentId, flowId, FlowMode.SIMPLE);
                active.flow = flow;
                sdk.registerTaskFlow(flow);
                String payload = JSON.toJSONString(List.of(Map.of("type", "action_group", "execution_mode", "sequential",
                        "actions", List.of(Map.of("action_type", "tts", "config", Map.of("tts", text, "interrupt", true))))));
                String requestEvent = flow.startRequest(payload, new FlowStartCallback() {
                    @Override public void onStartRequestAck(String f, String e, int code, String ignored) {
                        if (code != 0) {
                            active.complete.completeExceptionally(new IllegalStateException("Flow rejected"));
                            active.terminated.complete(null);
                        }
                    }
                    @Override public void onStartExecuteAck(String f, String e, int code, String ignored) {
                        if (code != 0) active.complete.completeExceptionally(new IllegalStateException("Flow failed"));
                        // A success ack can mean timeout; only matching tts_status completes playback.
                    }
                }, 120_000);
                // Keep returned request event for the documented tts_status correlation.
                active.bindEvent(requestEvent, System.currentTimeMillis());
            }
          }
            return playback.complete.get(120, TimeUnit.SECONDS);
        } finally {
            // A timeout/offline event cannot prove the physical speaker stopped.
            // Keep the lease until a matching terminal state, including a late one.
            Playback active = playback;
            if (active == null) speakerLock.release();
            else active.terminated.whenComplete((ignored, error) -> {
                if (playing.remove(correlation, active)) {
                    try { if (active.reply == null) sdk.unregisterTaskFlow(flowId); }
                    finally { speakerLock.release(); }
                }
            });
        }
    }

    private void cancel(String correlation) throws Exception {
        Playback playback;
        synchronized (speechRequests) {
            speechRequests.cancel(correlation, System.currentTimeMillis());
            playback = playing.get(correlation);
            ReplyContext context = replies.get(correlation);
            if (context != null) {
                interruptReply(context);
                finishReply(context);
            }
            if (playback == null) return;
        }
        if (playback.flow != null) {
            CompletableFuture<Void> ack = new CompletableFuture<>();
            playback.flow.interruptRequest(new FlowInterruptCallback() {
                @Override public void onInterruptRequestAck(String f, String e, int code, String ignored) {
                    if (code == 0) ack.complete(null); else ack.completeExceptionally(new IllegalStateException("Cancel rejected"));
                }
            }, 2000);
            ack.get(2500, TimeUnit.MILLISECONDS);
        } else {
            ReplyContext reply = playback.reply;
            if (reply == null) throw new IllegalStateException("Reply unavailable");
            interruptReply(reply);
            finishReply(reply);
        }
        playback.terminated.get(2500, TimeUnit.MILLISECONDS);
        playback.complete.completeExceptionally(new IllegalStateException("Cancelled"));
    }

    private CompletableFuture<HttpResponse<String>> postEvent(Map<String, Object> event) {
        HttpRequest request = HttpRequest.newBuilder(api.resolve("/api/v1/agent/events"))
                .timeout(Duration.ofSeconds("recognition".equals(event.get("kind")) ? 180 : 5))
                .header("Content-Type", "application/json")
                .header("X-Agent-Token", token).POST(HttpRequest.BodyPublishers.ofString(JSON.toJSONString(event))).build();
        return http.sendAsync(request, HttpResponse.BodyHandlers.ofString()).thenApply(response -> {
            if (response.statusCode() != 200 && response.statusCode() != 204) throw new CompletionException(new IllegalStateException("Event rejected"));
            return response;
        });
    }

    private static void send(HttpExchange exchange, int status, Object body) {
        try {
            byte[] bytes = JSON.toJSONBytes(body);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.getResponseHeaders().set("Cache-Control", "no-store");
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
        } catch (Exception ignored) { /* Client may have cancelled this request. */ }
    }
    private static String credential(JSONObject object, String name) {
        String value = object.getString(name);
        if (value == null || value.isBlank()) throw new IllegalStateException("Required credential not configured: " + name);
        return value;
    }
    private static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) throw new IllegalStateException("Required setting missing: " + name);
        return value;
    }
    public static void main(String[] args) throws Exception { new AgentMain().start(); }
}
