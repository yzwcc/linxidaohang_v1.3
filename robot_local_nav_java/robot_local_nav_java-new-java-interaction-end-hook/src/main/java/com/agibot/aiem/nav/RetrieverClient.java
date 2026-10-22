package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/** Calls the independent local Retriever Proxy; app secrets remain in the Python service. */
final class RetrieverClient {
    private static final double MIN_SCORE = 0.60;
    private static final Duration REQUEST_TIMEOUT = Duration.ofMillis(1200);
    private final HttpClient httpClient;
    private final URI endpoint;
    private final Duration requestTimeout;
    private final double minScore;

    RetrieverClient() {
        this(URI.create("http://127.0.0.1:18084/search"), REQUEST_TIMEOUT, MIN_SCORE);
    }

    RetrieverClient(URI endpoint, Duration requestTimeout, double minScore) {
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(500))
                .build();
        this.endpoint = endpoint;
        this.requestTimeout = requestTimeout;
        this.minScore = minScore;
    }

    String findAnswer(String query) throws IOException, InterruptedException {
        if (query == null || query.isBlank()) {
            return null;
        }
        JSONObject body = new JSONObject();
        body.put("query", query.trim());
        body.put("top_k", 3);
        body.put("rerank", false);
        body.put("check_qa_first", false);
        body.put("score_threshold", this.minScore);
        HttpRequest request = HttpRequest.newBuilder(endpoint)
                .timeout(this.requestTimeout)
                .header("Content-Type", "application/json; charset=utf-8")
                .POST(HttpRequest.BodyPublishers.ofString(body.toJSONString(), StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IOException("Knowledge base proxy HTTP " + response.statusCode());
        }
        JSONObject envelope = JSONObject.parseObject(response.body());
        if (!envelope.getBooleanValue("ok")) {
            return null;
        }
        JSONObject upstream = envelope.getJSONObject("data");
        if (upstream == null || !upstream.getBooleanValue("success")) {
            return null;
        }
        JSONArray results = upstream.getJSONArray("data");
        if (results == null) {
            return null;
        }
        for (int index = 0; index < results.size(); index++) {
            JSONObject item = results.getJSONObject(index);
            if (item == null || item.getDoubleValue("score") < this.minScore) {
                continue;
            }
            String answer = extractText(item.getString("answer"));
            if (answer == null || answer.isBlank()) {
                answer = extractText(item.getString("content"));
            }
            if (answer != null && !answer.isBlank()) {
                return answer.trim();
            }
        }
        return null;
    }

    private static String extractText(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String text = value.trim();
        if (text.startsWith("{") && text.endsWith("}")) {
            try {
                JSONObject object = JSONObject.parseObject(text);
                JSONArray behaviors = object.getJSONArray("behaviors");
                if (behaviors != null && !behaviors.isEmpty()) {
                    JSONObject behavior = behaviors.getJSONObject(0);
                    String behaviorText = behavior == null ? null : behavior.getString("text");
                    if (behaviorText != null && !behaviorText.isBlank()) {
                        return behaviorText;
                    }
                }
            } catch (RuntimeException ignored) {
                // Keep the original text if a provider returns non-standard JSON content.
            }
        }
        return text;
    }
}
