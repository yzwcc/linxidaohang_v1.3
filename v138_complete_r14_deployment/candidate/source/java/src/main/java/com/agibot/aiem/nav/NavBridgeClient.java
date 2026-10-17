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
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

final class NavBridgeClient {
    private static final Logger log = LoggerFactory.getLogger(NavBridgeClient.class);
    private final HttpClient httpClient;
    private final String baseUrl;
    private final Duration timeout;

    NavBridgeClient(AppConfig appConfig) {
        this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(appConfig.navTimeoutMs)).build();
        this.baseUrl = appConfig.navBaseUrl;
        this.timeout = Duration.ofMillis(appConfig.navTimeoutMs);
    }

    List<String> listPointNames() throws IOException, InterruptedException {
        JSONObject jSONObject = this.getJson("/points");
        if (jSONObject == null || !jSONObject.getBooleanValue("ok")) {
            throw new IOException("Robot bridge returned an invalid point catalog response: " + jSONObject);
        }
        JSONArray jSONArray = jSONObject.getJSONArray("point_names");
        if (jSONArray == null) {
            throw new IOException("Robot bridge point catalog is missing point_names");
        }
        ArrayList<String> arrayList = new ArrayList<String>();
        for (Object e : jSONArray) {
            String name = String.valueOf(e).trim();
            if (!name.isBlank()) {
                arrayList.add(name);
            }
        }
        return arrayList;
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
        return this.startTask(string, "");
    }

    JSONObject startTask(String string, String taskId) throws IOException, InterruptedException {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("event_id", string);
        if (taskId != null && !taskId.isBlank()) {
            jSONObject.put("task_id", taskId.trim());
        }
        log.info("Robot bridge /task/start request: eventId={}, taskId={}", string, taskId == null || taskId.isBlank() ? "<active>" : taskId);
        return this.postJson("/task/start", jSONObject);
    }

    JSONObject matchTaskCommand(String text) {
        JSONObject payload = new JSONObject();
        payload.put("text", text == null ? "" : text);
        try {
            return this.postJson("/task/match", payload);
        }
        catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("Robot bridge /task/match unavailable: {}", exception.getMessage());
            return null;
        }
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

    JSONObject presetMotion(String eventId, String motion, String area) throws IOException, InterruptedException {
        JSONObject payload = new JSONObject();
        payload.put("event_id", eventId);
        payload.put("motion", motion);
        payload.put("area", area);
        log.info("Robot bridge /motion/play request: eventId={}, motion={}, area={}", eventId, motion, area);
        // The bridge can wait six seconds for AIMDK, plus input-source setup.
        // Do not abort first and turn an executing motion into a voice error.
        return this.postJson("/motion/play", payload,
                Duration.ofMillis(Math.max(15000, this.timeout.toMillis())));
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
        return this.postJson(string, jSONObject, this.timeout);
    }

    private JSONObject postJson(String string, JSONObject jSONObject, Duration requestTimeout) throws IOException, InterruptedException {
        HttpRequest httpRequest = HttpRequest.newBuilder().uri(URI.create(this.baseUrl + string)).timeout(requestTimeout).header("Content-Type", "application/json; charset=utf-8").POST(HttpRequest.BodyPublishers.ofString(jSONObject.toJSONString(new JSONWriter.Feature[0]))).build();
        return this.send(httpRequest);
    }

    private JSONObject send(HttpRequest httpRequest) throws IOException, InterruptedException {
        HttpResponse<String> httpResponse = this.httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());
        String string = httpRequest.uri().getPath();
        if (string.endsWith("/nav/start") || string.endsWith("/nav/stop") || string.endsWith("/task/start") || string.endsWith("/task/match") || string.endsWith("/task/pause") || string.endsWith("/task/resume") || string.endsWith("/task/stop") || string.endsWith("/entertainment/sing") || string.endsWith("/entertainment/dance") || string.endsWith("/motion/play") || string.endsWith("/tts/speak") || string.endsWith("/interaction/end") || string.endsWith("/interaction/mark") || string.endsWith("/interaction/wake")) {
            log.info("Robot bridge {} response: httpStatus={}, body={}", string, httpResponse.statusCode(), httpResponse.body());
        }
        if (httpResponse.statusCode() < 200 || httpResponse.statusCode() >= 300) {
            throw new IOException("Nav bridge HTTP " + httpResponse.statusCode() + ": " + httpResponse.body());
        }
        return JSON.parseObject(httpResponse.body());
    }
}
