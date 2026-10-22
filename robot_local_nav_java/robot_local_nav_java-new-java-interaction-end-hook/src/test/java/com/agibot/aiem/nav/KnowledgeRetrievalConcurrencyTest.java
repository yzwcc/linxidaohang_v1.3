package com.agibot.aiem.nav;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Properties;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

final class KnowledgeRetrievalConcurrencyTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    private static AppConfig config(String modelUrl, String navUrl) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test-model");
        properties.setProperty("doubao.url", modelUrl);
        properties.setProperty("nav.baseUrl", navUrl);
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    private static void respond(HttpExchange exchange, String value) throws IOException {
        byte[] body = value.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(200, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static Object processText(
            Method method, AppConfig config, NavBridgeClient bridge, String eventId, String text)
            throws Exception {
        return method.invoke(
                null, eventId, text, config, bridge, new DoubaoArkClient(config), null,
                new AtomicBoolean());
    }

    private static String reply(Object result) throws Exception {
        Field field = result.getClass().getDeclaredField("reply");
        field.setAccessible(true);
        return (String) field.get(result);
    }

    private static void awaitPoolIdle(ThreadPoolExecutor workers) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (System.nanoTime() < deadline) {
            if (workers.getActiveCount() == 0 && workers.getQueue().isEmpty()) {
                return;
            }
            Thread.sleep(10);
        }
        throw new AssertionError("knowledge worker pool did not become idle");
    }

    public static void main(String[] args) throws Exception {
        AtomicInteger modelRequests = new AtomicInteger();
        AtomicInteger retrievalRequests = new AtomicInteger();
        AtomicInteger navigationRequests = new AtomicInteger();
        AtomicBoolean modelCompleted = new AtomicBoolean();
        AtomicBoolean waitForRetrieval = new AtomicBoolean(true);
        AtomicBoolean retrievalStartedDuringModel = new AtomicBoolean();
        AtomicReference<CountDownLatch> retrievalStarted =
                new AtomicReference<>(new CountDownLatch(1));
        ExecutorService modelExecutor = Executors.newCachedThreadPool();
        ExecutorService navExecutor = Executors.newCachedThreadPool();
        ExecutorService retrieverExecutor = Executors.newCachedThreadPool();

        HttpServer modelServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        modelServer.setExecutor(modelExecutor);
        modelServer.createContext("/chat/completions", exchange -> {
            modelRequests.incrementAndGet();
            if (waitForRetrieval.get()) {
                try {
                    if (retrievalStarted.get().await(3, TimeUnit.SECONDS)) {
                        retrievalStartedDuringModel.set(!modelCompleted.get());
                    }
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                }
            }
            modelCompleted.set(true);
            String content = "{\"intent\":\"chat\",\"question\":\"你们展厅几点开放？\","
                    + "\"reply\":\"模型猜测开放时间\"}";
            String response = "{\"choices\":[{\"message\":{\"content\":"
                    + com.alibaba.fastjson2.JSON.toJSONString(content) + "}}]}";
            try {
                respond(exchange, response);
            } catch (Exception exception) {
                throw new RuntimeException(exception);
            }
        });
        modelServer.start();

        HttpServer navServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        navServer.setExecutor(navExecutor);
        navServer.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            if ("/points".equals(path)) {
                respond(exchange, "{\"ok\":true,\"active_map_name\":\"floor-a\","
                        + "\"point_names\":[\"正门\"],\"ambiguous_point_names\":[]}");
            } else if ("/interaction/mark".equals(path)) {
                respond(exchange, "{\"ok\":true,\"state\":\"active\"}");
            } else if ("/nav/start".equals(path)) {
                navigationRequests.incrementAndGet();
                respond(exchange, "{\"ok\":true,\"accepted\":true,\"status\":\"accepted\"}");
            } else {
                respond(exchange, "{\"ok\":true}");
            }
        });
        navServer.start();

        HttpServer retrieverServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        retrieverServer.setExecutor(retrieverExecutor);
        retrieverServer.createContext("/search", exchange -> {
            retrievalRequests.incrementAndGet();
            if (!modelCompleted.get()) {
                retrievalStartedDuringModel.set(true);
            }
            retrievalStarted.get().countDown();
            respond(exchange, "{\"ok\":true,\"data\":{\"success\":true,\"data\":["
                    + "{\"score\":0.82,\"answer\":\"知识库核实：展厅十点开放。\"}]}}");
        });
        retrieverServer.start();

        Field retrieverField = LingxiNavAgentApp.class.getDeclaredField("RETRIEVER_CLIENT");
        retrieverField.setAccessible(true);
        RetrieverClient originalRetriever = (RetrieverClient) retrieverField.get(null);
        Field workersField = LingxiNavAgentApp.class.getDeclaredField("KNOWLEDGE_WORKERS");
        workersField.setAccessible(true);
        ThreadPoolExecutor workers = (ThreadPoolExecutor) workersField.get(null);
        try {
            String modelUrl = "http://127.0.0.1:" + modelServer.getAddress().getPort()
                    + "/chat/completions";
            String navUrl = "http://127.0.0.1:" + navServer.getAddress().getPort();
            AppConfig config = config(modelUrl, navUrl);
            retrieverField.set(null, new RetrieverClient(
                    URI.create("http://127.0.0.1:" + retrieverServer.getAddress().getPort()
                            + "/search"),
                    Duration.ofSeconds(2), 0.60));
            Method processText = LingxiNavAgentApp.class.getDeclaredMethod(
                    "processTextRequest",
                    String.class, String.class, AppConfig.class, NavBridgeClient.class,
                    DoubaoArkClient.class, QaStore.class, AtomicBoolean.class);
            processText.setAccessible(true);
            NavBridgeClient bridge = new NavBridgeClient(config);

            Object factualAnswer = processText(
                    processText, config, bridge, "parallel-kb-test", "你们展厅几点开放？");
            check("知识库核实：展厅十点开放。".equals(reply(factualAnswer)),
                    "grounded answer uses the retrieval result");
            check(modelRequests.get() == 1 && retrievalRequests.get() == 1,
                    "factual text starts one model request and one retrieval request");
            check(retrievalStartedDuringModel.get(),
                    "knowledge retrieval begins while intent parsing is still in flight");

            waitForRetrieval.set(false);
            modelCompleted.set(false);
            int priorModelRequests = modelRequests.get();
            int priorRetrievalRequests = retrievalRequests.get();
            processText(processText, config, bridge, "local-nav-no-kb", "请带我去正门吧");
            check(modelRequests.get() == priorModelRequests,
                    "exact local navigation continues to skip intent parsing");
            check(retrievalRequests.get() == priorRetrievalRequests,
                    "exact local navigation does not schedule knowledge retrieval");
            check(navigationRequests.get() == 1,
                    "exact local navigation still submits one navigation request");

            awaitPoolIdle(workers);
            CountDownLatch releaseWorkers = new CountDownLatch(1);
            CountDownLatch startedWorkers = new CountDownLatch(2);
            Runnable blocker = () -> {
                startedWorkers.countDown();
                try {
                    releaseWorkers.await();
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                }
            };
            for (int index = 0; index < 2; index++) {
                workers.execute(blocker);
            }
            check(startedWorkers.await(2, TimeUnit.SECONDS),
                    "both bounded knowledge workers are occupied for saturation test");
            for (int index = 0; index < 4; index++) {
                check(workers.getQueue().offer(blocker),
                        "bounded knowledge queue accepts one saturation fixture");
            }
            check(workers.getQueue().size() == 4,
                    "knowledge queue is bounded at four pending tasks");
            retrievalStarted.set(new CountDownLatch(1));
            retrievalStartedDuringModel.set(false);
            modelCompleted.set(false);
            waitForRetrieval.set(false);
            try {
                Object fallbackAnswer = processText(
                        processText, config, bridge, "serial-kb-fallback", "你们展厅几点开放？");
                check("知识库核实：展厅十点开放。".equals(reply(fallbackAnswer)),
                        "queue saturation preserves the grounded answer");
                check(modelCompleted.get(),
                        "queue saturation fallback retrieves only after intent parsing");
                check(retrievalRequests.get() == priorRetrievalRequests + 1,
                        "queue saturation falls back to one serial retrieval request");
            } finally {
                releaseWorkers.countDown();
                awaitPoolIdle(workers);
            }
        } finally {
            retrieverField.set(null, originalRetriever);
            modelServer.stop(0);
            navServer.stop(0);
            retrieverServer.stop(0);
            modelExecutor.shutdownNow();
            navExecutor.shutdownNow();
            retrieverExecutor.shutdownNow();
        }
        System.out.println("KnowledgeRetrievalConcurrencyTest passed: " + checks + " assertions");
    }
}
