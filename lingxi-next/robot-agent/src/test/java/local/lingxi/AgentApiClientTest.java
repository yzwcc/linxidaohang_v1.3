package local.lingxi;

import com.alibaba.fastjson2.JSON;
import com.sun.net.httpserver.*;
import javax.net.ssl.*;
import java.net.*;
import java.net.http.*;
import java.nio.file.*;
import java.security.KeyStore;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class AgentApiClientTest {
    @TempDir Path temporary;

    @Test void actualLoopbackHttpsUsesSelectedTokenAndNeverFollowsRedirectsOrReplaysRejectedEvents() throws Exception {
        Path store = temporary.resolve("test.p12"), ca = temporary.resolve("ca.crt");
        String keytool = Path.of(System.getProperty("java.home"), "bin", "keytool").toString();
        command(keytool, "-genkeypair", "-alias", "robot", "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "2", "-dname", "CN=synthetic-loopback", "-ext", "SAN=IP:127.0.0.1",
                "-storetype", "PKCS12", "-keystore", store.toString(), "-storepass", "synthetic-only", "-keypass", "synthetic-only");
        command(keytool, "-exportcert", "-alias", "robot", "-rfc", "-keystore", store.toString(),
                "-storepass", "synthetic-only", "-file", ca.toString());
        KeyStore keys = KeyStore.getInstance("PKCS12");
        try (var input = Files.newInputStream(store)) { keys.load(input, "synthetic-only".toCharArray()); }
        var manager = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        manager.init(keys, "synthetic-only".toCharArray());
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(manager.getKeyManagers(), null, null);
        HttpsServer server = HttpsServer.create(new InetSocketAddress("127.0.0.1", 0), 4);
        server.setHttpsConfigurator(new HttpsConfigurator(tls));
        AtomicInteger requests = new AtomicInteger(), redirected = new AtomicInteger(), status = new AtomicInteger(204);
        List<String> tokens = new CopyOnWriteArrayList<>(), events = new CopyOnWriteArrayList<>();
        server.createContext("/api/v1/agent/events", exchange -> {
            requests.incrementAndGet();
            tokens.add(exchange.getRequestHeaders().getFirst("X-Agent-Token"));
            events.add(JSON.parseObject(new String(exchange.getRequestBody().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8)).getString("eventId"));
            exchange.getResponseHeaders().set("Location", "/unexpected");
            exchange.sendResponseHeaders(status.get(), -1);
            exchange.close();
        });
        server.createContext("/unexpected", exchange -> {
            redirected.incrementAndGet(); exchange.sendResponseHeaders(204, -1); exchange.close();
        });
        server.start();
        HttpServer playback = null;
        try {
            var env = AgentConfigurationTest.productEnvironment(temporary);
            env.put("LINGXI_SLAM_PORT", Integer.toString(server.getAddress().getPort()));
            // Reserve an actual loopback port to exercise the same bind address used by AgentMain.
            try (var reservation = new java.net.ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) {
                AgentConfigurationTest.save(env, temporary, Map.of("agentUrl", "http://127.0.0.1:" + reservation.getLocalPort()));
            }
            var config = AgentConfiguration.load(env);
            playback = HttpServer.create(config.playbackAddress(), 4);
            playback.createContext("/health", exchange -> { exchange.sendResponseHeaders(204, -1); exchange.close(); });
            playback.start();
            assertTrue(playback.getAddress().getAddress().isLoopbackAddress());
            assertEquals(config.playbackAddress().getPort(), playback.getAddress().getPort());
            var health = HttpClient.newHttpClient().send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + playback.getAddress().getPort() + "/health"))
                    .timeout(Duration.ofSeconds(3)).build(), HttpResponse.BodyHandlers.discarding());
            assertEquals(204, health.statusCode());
            var client = new AgentApiClient(config);
            assertEquals(204, client.postEvent(Map.of("kind", "wake", "eventId", "synthetic-wake")).get(8, TimeUnit.SECONDS).statusCode());
            status.set(401);
            assertThrows(ExecutionException.class, () -> client.postEvent(Map.of("kind", "recognition", "eventId", "rejected-event")).get(8, TimeUnit.SECONDS));
            status.set(307);
            assertThrows(ExecutionException.class, () -> client.postEvent(Map.of("kind", "wake", "eventId", "redirected-event")).get(8, TimeUnit.SECONDS));
            assertEquals(3, requests.get());
            assertEquals(0, redirected.get());
            assertEquals(List.of("synthetic-wake", "rejected-event", "redirected-event"), events);
            assertEquals(List.of(AgentConfigurationTest.TOKEN, AgentConfigurationTest.TOKEN, AgentConfigurationTest.TOKEN), tokens);
            server.stop(0);
            assertThrows(ExecutionException.class, () -> client.postEvent(Map.of("kind", "wake", "eventId", "offline-event")).get(8, TimeUnit.SECONDS));
            assertEquals(3, requests.get());
        } finally {
            server.stop(0);
            if (playback != null) playback.stop(0);
        }
    }

    private static void command(String... args) throws Exception {
        Process process = new ProcessBuilder(args).redirectErrorStream(true).start();
        try {
            assertTrue(process.waitFor(20, TimeUnit.SECONDS), "Synthetic certificate fixture timed out");
            assertEquals(0, process.exitValue(), () -> "Synthetic certificate fixture failed");
        } finally { process.destroyForcibly(); }
    }
}
