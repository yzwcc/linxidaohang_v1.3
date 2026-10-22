package com.agibot.aiem.nav;

import com.sun.net.httpserver.HttpServer;
import java.lang.reflect.Constructor;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

final class NavBridgeClientTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    private static AppConfig config(String baseUrl) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("lingxi.appId", "test");
        properties.setProperty("lingxi.appKey", "test");
        properties.setProperty("lingxi.appSecret", "test");
        properties.setProperty("doubao.apiKey", "test");
        properties.setProperty("doubao.model", "test");
        properties.setProperty("nav.baseUrl", baseUrl);
        Constructor<AppConfig> constructor = AppConfig.class.getDeclaredConstructor(Properties.class);
        constructor.setAccessible(true);
        return constructor.newInstance(properties);
    }

    public static void main(String[] args) throws Exception {
        AtomicInteger requests = new AtomicInteger();
        AtomicBoolean fail = new AtomicBoolean(false);
        AtomicLong timeNanos = new AtomicLong();
        AtomicLong pointName = new AtomicLong(0);
        AtomicBoolean empty = new AtomicBoolean(false);
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/points", exchange -> {
            requests.incrementAndGet();
            int status = fail.get() ? 503 : 200;
            String body = fail.get()
                    ? "{\"ok\":false}"
                    : empty.get()
                    ? "{\"point_names\":[]}"
                    : "{\"point_names\":[\"point-" + pointName.get() + "\"]}";
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
            AppConfig config = config(baseUrl);
            NavBridgeClient client = new NavBridgeClient(config, timeNanos::get, 10_000L);
            check(client.listPointNames().equals(List.of("point-0")), "initial point list loaded");
            check(client.listPointNames().equals(List.of("point-0")), "fresh point list served from cache");
            check(requests.get() == 1, "cache prevents a second bridge request");

            pointName.set(1);
            timeNanos.addAndGet(10_001_000_000L);
            check(client.listPointNames().equals(List.of("point-1")), "expired point list is refreshed");
            check(requests.get() == 2, "expiry makes one bridge request");

            fail.set(true);
            timeNanos.addAndGet(10_001_000_000L);
            check(client.listPointNames().equals(List.of("point-1")), "bridge failure retains last known good points");
            check(requests.get() == 3, "failed refresh makes one bridge request");
            fail.set(false);

            empty.set(true);
            timeNanos.addAndGet(10_001_000_000L);
            check(client.listPointNames().isEmpty(), "authoritative empty point list replaces old cache");
            check(requests.get() == 4, "empty point list is fetched once");
            check(client.listPointNames().isEmpty(), "empty point list is cached");
            check(requests.get() == 4, "cached empty point list avoids a duplicate request");

            fail.set(true);
            timeNanos.addAndGet(10_001_000_000L);
            check(client.listPointNames().isEmpty(), "bridge failure retains last known empty point list");
            NavBridgeClient emptyClient = new NavBridgeClient(config, timeNanos::get, 10_000L);
            check(emptyClient.listPointNames().isEmpty(), "first lookup failure returns an empty safe list");
            check(requests.get() == 6, "expired and empty-cache failures reach the bridge");
        } finally {
            server.stop(0);
        }
        System.out.println("NavBridgeClientTest passed: " + checks + " assertions");
    }
}
