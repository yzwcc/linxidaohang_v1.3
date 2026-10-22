package com.agibot.aiem.nav;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;

final class RetrieverClientTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    public static void main(String[] args) throws Exception {
        AtomicReference<String> responseBody = new AtomicReference<>(
                "{\"ok\":true,\"data\":{\"success\":true,\"data\":["
                        + "{\"score\":0.59,\"answer\":\"低相关答案\"},"
                        + "{\"score\":0.60,\"answer\":\"达到阈值答案\"}]}}" );
        AtomicReference<String> requestBody = new AtomicReference<>("");
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/search", exchange -> {
            requestBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] bytes = responseBody.get().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            URI endpoint = URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/search");
            RetrieverClient client = new RetrieverClient(endpoint, Duration.ofSeconds(2), 0.60);
            check("达到阈值答案".equals(client.findAnswer("展厅开放时间")),
                    "low-score retrieval is skipped and calibrated threshold is accepted");
            String sent = requestBody.get();
            check(sent.contains("\"score_threshold\":0.6"), "score threshold is passed to proxy");
            check(sent.contains("\"rerank\":false"), "latency-sensitive lookup disables rerank");
            check(sent.contains("\"check_qa_first\":false"), "latency-sensitive lookup disables QA pre-check");

            responseBody.set("{\"ok\":true,\"data\":{\"success\":true,\"data\":["
                    + "{\"score\":0.59,\"answer\":\"低相关答案\"}]}}");
            check(client.findAnswer("展厅开放时间") == null,
                    "retrieval below the minimum relevance threshold is rejected");
        } finally {
            server.stop(0);
        }
        System.out.println("RetrieverClientTest passed: " + checks + " assertions");
    }
}
