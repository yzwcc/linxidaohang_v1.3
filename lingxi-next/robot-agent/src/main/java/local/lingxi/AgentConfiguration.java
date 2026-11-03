package local.lingxi;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFileAttributes;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.util.*;

/** Resolves one callback owner and its matching private credentials before SDK startup. */
final class AgentConfiguration {
    private final URI api;
    private final String token;
    private final Path credentials;
    private final Path tlsCa;
    private final String agentId;
    private final InetSocketAddress playbackAddress;

    private AgentConfiguration(URI api, String token, Path credentials, Path tlsCa,
            String agentId, InetSocketAddress playbackAddress) {
        this.api = api; this.token = token; this.credentials = credentials;
        this.tlsCa = tlsCa; this.agentId = agentId; this.playbackAddress = playbackAddress;
    }

    static AgentConfiguration load(Map<String, String> env) throws Exception {
        String agentId = required(env, "LINKSOUL_AGENT_ID");
        Path ca = Path.of(required(env, "LINGXI_TLS_CA"));
        boolean selected = !env.getOrDefault("LINGXI_PRODUCT_CONFIG", "").isEmpty() ||
                !env.getOrDefault("LINGXI_PRODUCT_CONFIG_SHA256", "").isEmpty();
        if (!selected) {
            return new AgentConfiguration(localApi(env.getOrDefault("LINGXI_API_URL", "https://127.0.0.1:3000")),
                    required(env, "LINGXI_AGENT_INTERNAL_TOKEN"), Path.of(required(env, "LINGXI_CREDENTIALS_FILE")),
                    ca, agentId, playback(env.getOrDefault("LINGXI_AGENT_URL", "http://127.0.0.1:18180")));
        }
        if (!"1".equals(env.get("LINGXI_INDEPENDENT_PRODUCT"))) throw invalid();
        String digest = required(env, "LINGXI_PRODUCT_CONFIG_SHA256");
        if (!digest.matches("[a-f0-9]{64}")) throw invalid();
        Path file = privatePath(required(env, "LINGXI_PRODUCT_CONFIG"), false);
        byte[] bytes = boundedRead(file, 16384);
        if (!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)).equals(digest)) throw invalid();
        JSONObject value = JSON.parseObject(new String(bytes, StandardCharsets.UTF_8));
        Set<String> fields = Set.of("schemaVersion", "stateDirectory", "agentUrl", "agentTokenFile");
        Set<String> withNavigation = new HashSet<>(fields);
        withNavigation.add("navigationSocket");
        Set<String> withActions = new HashSet<>(fields);
        withActions.add("actionsBridgeEndpoint");
        Set<String> withNavigationAndActions = new HashSet<>(withNavigation);
        withNavigationAndActions.add("actionsBridgeEndpoint");
        if (value == null || !(fields.equals(value.keySet()) || withNavigation.equals(value.keySet()) ||
                withActions.equals(value.keySet()) || withNavigationAndActions.equals(value.keySet())) ||
                !Integer.valueOf(1).equals(value.get("schemaVersion"))) throw invalid();
        if (value.containsKey("actionsBridgeEndpoint") &&
                !"http://127.0.0.1:18082/v1".equals(value.get("actionsBridgeEndpoint"))) throw invalid();
        for (String field : List.of("stateDirectory", "agentUrl", "agentTokenFile")) {
            if (!(value.get(field) instanceof String text) || text.isBlank()) throw invalid();
        }
        if (value.containsKey("navigationSocket") &&
                (!(value.get("navigationSocket") instanceof String text) || text.isBlank())) throw invalid();
        Path state = privatePath(value.getString("stateDirectory"), true);
        Path tokenFile = privatePath(value.getString("agentTokenFile"), false);
        for (Path[] pair : List.of(new Path[]{file, state}, new Path[]{file, tokenFile}, new Path[]{state, tokenFile})) {
            if (pair[0].startsWith(pair[1]) || pair[1].startsWith(pair[0])) throw invalid();
        }
        String token = new String(boundedRead(tokenFile, 4096), StandardCharsets.UTF_8).strip();
        if (!token.matches("[\\x21-\\x7e]{32,4096}")) throw invalid();
        privatePath(state.resolve("credentials").toString(), true);
        Path credentials = privatePath(state.resolve("credentials/ai.json").toString(), false);
        if (Files.size(credentials) == 0 || Files.size(credentials) > 1024 * 1024) throw invalid();
        int port = port(env.getOrDefault("LINGXI_SLAM_PORT", "3444"));
        if (port == 3000) throw invalid();
        InetSocketAddress playback = playback(value.getString("agentUrl"));
        if (port == playback.getPort()) throw invalid();
        return new AgentConfiguration(URI.create("https://127.0.0.1:" + port), token, credentials, ca, agentId, playback);
    }

    private static Path privatePath(String text, boolean directory) throws IOException {
        Path path = Path.of(text);
        if (!path.isAbsolute() || !path.toString().equals(text) || !path.normalize().equals(path) ||
                !path.toRealPath().equals(path) || !(directory ? Files.isDirectory(path) : Files.isRegularFile(path))) throw invalid();
        if (path.getFileSystem().supportedFileAttributeViews().contains("posix")) {
            PosixFileAttributes info = Files.readAttributes(path, PosixFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (!info.owner().equals(Files.getOwner(Path.of("/proc/self"))) || info.permissions().stream().anyMatch(
                    permission -> Set.of(PosixFilePermission.GROUP_READ, PosixFilePermission.GROUP_WRITE,
                            PosixFilePermission.GROUP_EXECUTE, PosixFilePermission.OTHERS_READ,
                            PosixFilePermission.OTHERS_WRITE, PosixFilePermission.OTHERS_EXECUTE).contains(permission))) throw invalid();
        }
        return path;
    }

    private static byte[] boundedRead(Path file, int maximum) throws IOException {
        try (var stream = Files.newInputStream(file)) {
            byte[] bytes = stream.readNBytes(maximum + 1);
            if (bytes.length == 0 || bytes.length > maximum) throw invalid();
            return bytes;
        }
    }

    private static URI localApi(String text) {
        URI uri = URI.create(text);
        if (!"https".equals(uri.getScheme()) || !Set.of("127.0.0.1", "localhost", "[::1]").contains(uri.getHost()) ||
                uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null ||
                !(uri.getRawPath().isEmpty() || "/".equals(uri.getRawPath())) || uri.getPort() == 0 || uri.getPort() > 65535) throw invalid();
        return uri;
    }

    private static InetSocketAddress playback(String text) {
        if (!text.matches("http://127\\.0\\.0\\.1:[1-9][0-9]{0,4}")) throw invalid();
        return new InetSocketAddress("127.0.0.1", port(text.substring(text.lastIndexOf(':') + 1)));
    }

    private static int port(String text) {
        if (!text.matches("[1-9][0-9]{0,4}") || Integer.parseInt(text) > 65535) throw invalid();
        return Integer.parseInt(text);
    }

    private static String required(Map<String, String> env, String name) {
        String value = env.get(name);
        if (value == null || value.isBlank()) throw invalid();
        return value;
    }

    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid Agent configuration"); }
    URI api() { return api; }
    String token() { return token; }
    Path credentials() { return credentials; }
    Path tlsCa() { return tlsCa; }
    String agentId() { return agentId; }
    InetSocketAddress playbackAddress() { return playbackAddress; }
}
