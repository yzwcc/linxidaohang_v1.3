/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import com.agibot.aiem.nav.AppConfig;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.fastjson2.JSONWriter;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.function.LongSupplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

final class NavBridgeClient {
    private static final long DEFAULT_POINT_NAMES_CACHE_TTL_MS = 10_000L;
    private static final Logger log = LoggerFactory.getLogger(NavBridgeClient.class);
    private final HttpClient httpClient;
    private final String baseUrl;
    private final Duration timeout;
    private final Object pointNamesLock = new Object();
    private final LongSupplier nanoTime;
    private final long pointNamesCacheTtlNanos;
    private volatile PointNamesSnapshot pointNamesSnapshot;

    NavBridgeClient(AppConfig appConfig) {
        this(appConfig, System::nanoTime, DEFAULT_POINT_NAMES_CACHE_TTL_MS);
    }

    NavBridgeClient(AppConfig appConfig, LongSupplier nanoTime, long pointNamesCacheTtlMs) {
        this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(appConfig.navTimeoutMs)).build();
        this.baseUrl = appConfig.navBaseUrl;
        this.timeout = Duration.ofMillis(appConfig.navTimeoutMs);
        this.nanoTime = nanoTime;
        this.pointNamesCacheTtlNanos = Duration.ofMillis(Math.max(0L, pointNamesCacheTtlMs)).toNanos();
    }

    List<String> listPointNames() {
        long now = this.nanoTime.getAsLong();
        PointNamesSnapshot cached = this.pointNamesSnapshot;
        if (this.isFresh(cached, now)) {
            log.debug("Navigation point names served from cache: count={}", cached.names.size());
            return cached.names;
        }
        synchronized (this.pointNamesLock) {
            now = this.nanoTime.getAsLong();
            cached = this.pointNamesSnapshot;
            if (this.isFresh(cached, now)) {
                log.debug("Navigation point names served from cache: count={}", cached.names.size());
                return cached.names;
            }
            long startedAt = System.nanoTime();
            try {
                JSONObject response = this.getJson("/points");
                JSONArray values = response.getJSONArray("point_names");
                ArrayList<String> names = new ArrayList<String>();
                if (values != null) {
                    for (Object value : values) {
                        String name = String.valueOf(value).trim();
                        if (!name.isEmpty()) {
                            names.add(name);
                        }
                    }
                }
                List<String> result = List.copyOf(names);
                this.pointNamesSnapshot = new PointNamesSnapshot(this.nanoTime.getAsLong(), result);
                log.info("Navigation point names refreshed: count={}, elapsedMs={}",
                        result.size(), (System.nanoTime() - startedAt) / 1_000_000L);
                return result;
            } catch (Exception exception) {
                if (exception instanceof InterruptedException) {
                    Thread.currentThread().interrupt();
                }
                cached = this.pointNamesSnapshot;
                log.warn("Navigation point lookup failed: elapsedMs={}, usingCached={}, error={}",
                        (System.nanoTime() - startedAt) / 1_000_000L,
                        cached != null,
                        exception.getMessage());
                return cached == null ? List.of() : cached.names;
            }
        }
    }

    private boolean isFresh(PointNamesSnapshot snapshot, long now) {
        return snapshot != null
                && this.pointNamesCacheTtlNanos > 0L
                && now - snapshot.refreshedAtNanos < this.pointNamesCacheTtlNanos;
    }

    private static final class PointNamesSnapshot {
        private final long refreshedAtNanos;
        private final List<String> names;

        private PointNamesSnapshot(long refreshedAtNanos, List<String> names) {
            this.refreshedAtNanos = refreshedAtNanos;
            this.names = names;
        }
    }

    JSONObject startNavigation(String string, String string2) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("target_name", string2);
        log.info("Robot bridge /nav/start request: eventId={}, targetName={}", (Object)string, (Object)string2);
        return this.postJson("/nav/start", jSONObject);
    }

    JSONObject stopNavigation(String string) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        log.info("Robot bridge /nav/stop request: eventId={}", (Object)string);
        return this.postJson("/nav/stop", jSONObject);
    }

    JSONObject startTask(String string) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        log.info("Robot bridge /task/start request: eventId={}", (Object)string);
        return this.postJson("/task/start", jSONObject);
    }

    JSONObject stopTask(String string) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        log.info("Robot bridge /task/stop request: eventId={}", (Object)string);
        return this.postJson("/task/stop", jSONObject);
    }

    JSONObject pauseTask(String string) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        log.info("Robot bridge /task/pause request: eventId={}", (Object)string);
        return this.postJson("/task/pause", jSONObject);
    }

    JSONObject resumeTask(String string) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        log.info("Robot bridge /task/resume request: eventId={}", (Object)string);
        return this.postJson("/task/resume", jSONObject);
    }

    JSONObject sing(String string, String string2) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("text", string2);
        log.info("Robot bridge /entertainment/sing request: eventId={}, text={}", (Object)string, (Object)string2);
        return this.postJson("/entertainment/sing", jSONObject);
    }

    JSONObject dance(String string, String string2) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("text", string2);
        log.info("Robot bridge /entertainment/dance request: eventId={}, text={}", (Object)string, (Object)string2);
        return this.postJson("/entertainment/dance", jSONObject);
    }

    JSONObject presetMotion(String eventId, String motion, String area, String text)
            throws IOException, InterruptedException {
        JSONObject payload = new JSONObject();
        payload.put("event_id", eventId);
        payload.put("motion", motion);
        payload.put("area", area);
        payload.put("text", text);
        log.info("Robot bridge /motion/play request: eventId={}, motion={}, area={}", eventId, motion, area);
        return this.postJson("/motion/play", payload);
    }

    JSONObject markInteraction(String string, String string2, String string3) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("source", string2);
        jSONObject.put("intent", string3);
        return this.postJson("/interaction/mark", jSONObject);
    }

    JSONObject wakeInteraction(String string, String string2) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("source", string2);
        return this.postJson("/interaction/wake", jSONObject);
    }

    JSONObject endInteraction(String string, String string2, String string3) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("source", string2);
        jSONObject.put("intent", string3);
        return this.postJson("/interaction/end", jSONObject);
    }

    JSONObject speak(String string, String string2, boolean bl, boolean bl2) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        jSONObject.put("text", string2);
        jSONObject.put("interrupt", bl);
        jSONObject.put("force", bl2);
        log.info("Robot bridge /tts/speak request: eventId={}, interrupt={}, force={}, text={}", string, bl, bl2, string2);
        return this.postJson("/tts/speak", jSONObject);
    }

    private JSONObject getJson(String string) throws IOException, InterruptedException {
        HttpRequest httpRequest = HttpRequest.newBuilder().uri(URI.create(this.baseUrl + string)).timeout(this.timeout).GET().build();
        return this.send(httpRequest);
    }

    private JSONObject postJson(String string, JSONObject jSONObject) throws IOException, InterruptedException {
        HttpRequest httpRequest = HttpRequest.newBuilder().uri(URI.create(this.baseUrl + string)).timeout(this.timeout).header("Content-Type", "application/json; charset=utf-8").POST(HttpRequest.BodyPublishers.ofString(jSONObject.toJSONString(new JSONWriter.Feature[0]))).build();
        return this.send(httpRequest);
    }

    private JSONObject send(HttpRequest httpRequest) throws IOException, InterruptedException {
        HttpResponse<String> httpResponse = this.httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());
        String string = httpRequest.uri().getPath();
        if (string.endsWith("/nav/start") || string.endsWith("/nav/stop") || string.endsWith("/task/start") || string.endsWith("/task/pause") || string.endsWith("/task/resume") || string.endsWith("/task/stop") || string.endsWith("/entertainment/sing") || string.endsWith("/entertainment/dance") || string.endsWith("/motion/play") || string.endsWith("/tts/speak") || string.endsWith("/interaction/end") || string.endsWith("/interaction/mark") || string.endsWith("/interaction/wake")) {
            log.info("Robot bridge {} response: httpStatus={}, body={}", string, httpResponse.statusCode(), httpResponse.body());
        }
        if (httpResponse.statusCode() < 200 || httpResponse.statusCode() >= 300) {
            throw new IOException("Nav bridge HTTP " + httpResponse.statusCode() + ": " + httpResponse.body());
        }
        return JSON.parseObject(httpResponse.body());
    }
}
