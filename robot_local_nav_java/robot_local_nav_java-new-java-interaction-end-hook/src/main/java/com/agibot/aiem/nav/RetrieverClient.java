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
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofMillis(500))
            .build();
    private final URI endpoint = URI.create("http://127.0.0.1:18084/search");

    String findAnswer(String query) throws IOException, InterruptedException {
        if (query == null || query.isBlank()) {
            return null;
        }
        JSONObject body = new JSONObject();
        body.put("query", query.trim());
        body.put("top_k", 3);
        // Keep the lookup within the voice interaction deadline. The upstream
        // QA pre-check and reranker can each outlive the 2.5-second client timeout.
        body.put("rerank", false);
        body.put("check_qa_first", false);
        body.put("score_threshold", 0.45);
        HttpRequest request = HttpRequest.newBuilder(endpoint)
                .timeout(Duration.ofMillis(2500))
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
            if (item == null || item.getDoubleValue("score") < 0.45) {
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
