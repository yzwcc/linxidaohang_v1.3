package local.lingxi;

import com.alibaba.fastjson2.JSON;
import javax.net.ssl.*;
import java.net.URI;
import java.net.http.*;
import java.nio.file.Files;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.*;

/** Authenticated loopback HTTPS callbacks; a failed event is never replayed. */
final class AgentApiClient {
    private final HttpClient http;
    private final URI api;
    private final String token;

    AgentApiClient(AgentConfiguration config) throws Exception {
        api = config.api(); token = config.token();
        KeyStore roots = KeyStore.getInstance(KeyStore.getDefaultType());
        roots.load(null, null);
        try (var input = Files.newInputStream(config.tlsCa())) {
            roots.setCertificateEntry("robot-ca", CertificateFactory.getInstance("X.509").generateCertificate(input));
        }
        TrustManagerFactory trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        trust.init(roots);
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, trust.getTrustManagers(), null);
        http = HttpClient.newBuilder().sslContext(tls).connectTimeout(Duration.ofSeconds(2))
                .followRedirects(HttpClient.Redirect.NEVER).build();
    }

    CompletableFuture<HttpResponse<String>> postEvent(Map<String, Object> event) {
        HttpRequest request = HttpRequest.newBuilder(api.resolve("/api/v1/agent/events"))
                .timeout(Duration.ofSeconds("recognition".equals(event.get("kind")) ? 180 : 5))
                .header("Content-Type", "application/json")
                .header("X-Agent-Token", token).POST(HttpRequest.BodyPublishers.ofString(JSON.toJSONString(event))).build();
        return http.sendAsync(request, HttpResponse.BodyHandlers.ofString()).thenApply(response -> {
            if (response.statusCode() != 200 && response.statusCode() != 204)
                throw new CompletionException(new IllegalStateException("Event rejected"));
            return response;
        });
    }
}
