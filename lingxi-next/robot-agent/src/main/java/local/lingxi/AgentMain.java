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
    private final String agentId = required("LINKSOUL_AGENT_ID");
    private final String token = required("LINGXI_AGENT_INTERNAL_TOKEN");
    private final URI api = URI.create(System.getenv().getOrDefault("LINGXI_API_URL", "https://127.0.0.1:3000"));
    private final ConcurrentMap<String, ReplyContext> replies = new ConcurrentHashMap<>();
    private final ConcurrentMap<String, Playback> playing = new ConcurrentHashMap<>();
    private final Semaphore speakerLock = new Semaphore(1);
    private final ExecutorService workers = new ThreadPoolExecutor(4, 4, 0, TimeUnit.MILLISECONDS,
            new ArrayBlockingQueue<>(8), new ThreadPoolExecutor.AbortPolicy());
    private final HttpClient http;
    private final AgentSdk sdk;
    private volatile boolean online;
    private volatile boolean authenticated;
    private volatile String lastGateReason = "waiting_for_real_wakeup";
    private volatile CompletableFuture<Void> wakeDelivered = CompletableFuture.completedFuture(null);

    private record ReplyContext(String eventId, Asr2LlmResponse response, AtomicBoolean interrupted) {}
    static final class Playback {
        final String correlation;
        volatile String event;
        final String item;
        final long began = System.nanoTime();
        final CompletableFuture<Double> complete = new CompletableFuture<>();
        final CompletableFuture<Void> terminated = new CompletableFuture<>();
        volatile Double firstAudio;
        volatile TaskFlowRequest flow;
        Playback(String correlation, String event, String item) {
            this.correlation = correlation; this.event = event; this.item = item;
        }
        void accept(JSONObject state, long now) {
            long at = state.getLongValue("updated_at_ms");
            if (at <= 0 || Math.abs(now - at) > 30_000) return;
            boolean matches = item != null ? item.equals(state.getString("item_id")) :
                    event != null && event.equals(state.getString("event_id"));
            if (!matches) return;
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
                        String before = gate.current(System.currentTimeMillis());
                        String session = gate.acceptListening(state, System.currentTimeMillis());
                        if (session != null) {
                            lastGateReason = "ready";
                            wakeDelivered = postEvent(Map.of("kind", "wake", "sessionId", session,
                                    "eventId", "wake:" + state.getLongValue("updated_at_ms"),
                                    "occurredAt", state.getLongValue("updated_at_ms"))).thenApply(ignored -> null);
                        } else if (before != null && gate.current(System.currentTimeMillis()) == null) {
                            postEvent(Map.of("kind", "session-ended", "sessionId", before,
                                    "eventId", "end:" + UUID.randomUUID(), "occurredAt", System.currentTimeMillis()));
                        }
                    } else if ("tts_status".equals(name)) {
                        acceptTts(state);
                    }
                } catch (RuntimeException ignored) { /* Ignore modules with a different JSON shape. */ }
            }
            @Override public void onRequest(String id, String event, String text, AgentParam param, Asr2LlmResponse response) {
                if (!agentId.equals(id) || !online || !authenticated || event == null || event.length() > 200) return;
                String session = gate.current(System.currentTimeMillis());
                if (session == null) { lastGateReason = "missing_real_wakeup"; return; }
                // Confidence is not guaranteed by the generic SDK contract. Missing evidence blocks execution.
                Object confidenceValue = param == null ? null : param.getObject("confidence");
                if (!(confidenceValue instanceof Number number) || !Double.isFinite(number.doubleValue())) {
                    lastGateReason = "asr_confidence_unavailable";
                    return;
                }
                double confidence = number.doubleValue();
                if (confidence < 0 || confidence > 1 || text == null || text.length() > 4000) return;
                String correlation = "reply:" + session + ":" + event;
                ReplyContext context = new ReplyContext(event, response, new AtomicBoolean());
                if (replies.putIfAbsent(correlation, context) != null) return;
                wakeDelivered.thenCompose(ignored -> {
                    if (!session.equals(gate.current(System.currentTimeMillis()))) return CompletableFuture.completedFuture(null);
                    return postEvent(Map.of("kind", "recognition", "sessionId", session, "eventId", event,
                            "occurredAt", System.currentTimeMillis(), "transcript", text, "confidence", confidence, "isFinal", true));
                }).whenComplete((ignored, error) -> {
                    if (replies.remove(correlation, context) && context.interrupted.get()) response.onLlmDone(event);
                });
            }
        });
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 18180), 8);
        server.setExecutor(workers);
        server.createContext("/", this::handle);
        server.start();
        sdk.initialize();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            clearSession(); server.stop(0); sdk.release(); workers.shutdownNow();
        }));
    }

    private void clearSession() {
        gate.clear();
        replies.clear();
        for (Playback playback : playing.values()) playback.complete.completeExceptionally(new IllegalStateException("Session ended"));
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
                double firstAudio = speak(text, correlation);
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

    private double speak(String text, String correlation) throws Exception {
        if (!online || !authenticated || !speakerLock.tryAcquire()) throw new IllegalStateException("Speaker busy/offline");
        ReplyContext reply = replies.get(correlation);
        String item = reply == null ? null : "lingxi_" + UUID.randomUUID();
        String event = reply == null ? "pending" : reply.eventId;
        Playback playback = new Playback(correlation, event, item);
        playing.put(correlation, playback);
        String flowId = "lingxi_" + UUID.randomUUID();
        try {
            if (reply != null) {
                if (reply.interrupted.compareAndSet(false, true)) reply.response.onInterrupt(reply.eventId, "chat", null);
                reply.response.onLlmItemDelta(reply.eventId, item, text);
                reply.response.onLlmItemDone(reply.eventId, item);
            } else {
                TaskFlowRequest flow = new TaskFlowRequest(sdk, agentId, flowId, FlowMode.SIMPLE);
                playback.flow = flow;
                sdk.registerTaskFlow(flow);
                String payload = JSON.toJSONString(List.of(Map.of("type", "action_group", "execution_mode", "sequential",
                        "actions", List.of(Map.of("action_type", "tts", "config", Map.of("tts", text, "interrupt", true))))));
                String requestEvent = flow.startRequest(payload, new FlowStartCallback() {
                    @Override public void onStartRequestAck(String f, String e, int code, String ignored) {
                        if (code != 0) {
                            playback.terminated.complete(null);
                            playback.complete.completeExceptionally(new IllegalStateException("Flow rejected"));
                        }
                    }
                    @Override public void onStartExecuteAck(String f, String e, int code, String ignored) {
                        if (code != 0) playback.complete.completeExceptionally(new IllegalStateException("Flow failed"));
                        // A success ack can mean timeout; only matching tts_status completes playback.
                    }
                }, 120_000);
                // Keep returned request event for the documented tts_status correlation.
                playback.event = requestEvent;
            }
            return playback.complete.get(120, TimeUnit.SECONDS);
        } finally {
            // A timeout/offline event cannot prove the physical speaker stopped.
            // Keep the lease until a matching terminal state, including a late one.
            playback.terminated.whenComplete((ignored, error) -> {
                if (playing.remove(correlation, playback)) {
                    try { if (reply == null) sdk.unregisterTaskFlow(flowId); }
                    finally { speakerLock.release(); }
                }
            });
        }
    }

    private void cancel(String correlation) throws Exception {
        Playback playback = playing.get(correlation);
        if (playback == null) return;
        if (playback.flow != null) {
            CompletableFuture<Void> ack = new CompletableFuture<>();
            playback.flow.interruptRequest(new FlowInterruptCallback() {
                @Override public void onInterruptRequestAck(String f, String e, int code, String ignored) {
                    if (code == 0) ack.complete(null); else ack.completeExceptionally(new IllegalStateException("Cancel rejected"));
                }
            }, 2000);
            ack.get(2500, TimeUnit.MILLISECONDS);
        } else {
            ReplyContext reply = replies.get(correlation);
            if (reply == null) throw new IllegalStateException("Reply unavailable");
            reply.response.onInterrupt(reply.eventId, "chat", null);
        }
        playback.terminated.get(2500, TimeUnit.MILLISECONDS);
        playback.complete.completeExceptionally(new IllegalStateException("Cancelled"));
    }

    private CompletableFuture<HttpResponse<String>> postEvent(Map<String, Object> event) {
        HttpRequest request = HttpRequest.newBuilder(api.resolve("/api/v1/agent/events"))
                .timeout(Duration.ofSeconds(180)).header("Content-Type", "application/json")
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
