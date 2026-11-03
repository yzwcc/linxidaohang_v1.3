package local.lingxi;

import com.alibaba.fastjson2.JSON;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class AgentConfigurationTest {
    @TempDir Path temporary;
    static final String TOKEN = "synthetic-product-token-not-a-real-secret";

    static Map<String, String> coreEnvironment(Path root) {
        return new HashMap<>(Map.of("LINKSOUL_AGENT_ID", "synthetic-agent", "LINGXI_TLS_CA", root.resolve("ca.crt").toString(),
                "LINGXI_AGENT_INTERNAL_TOKEN", "synthetic-unrelated-core-token", "LINGXI_CREDENTIALS_FILE", root.resolve("unrelated-ai.json").toString()));
    }

    static Path privateWrite(Path file, String value) throws Exception {
        Files.writeString(file, value);
        if (file.getFileSystem().supportedFileAttributeViews().contains("posix"))
            Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("rw-------"));
        return file;
    }

    static Map<String, String> productEnvironment(Path root) throws Exception {
        root = root.toRealPath();
        Path state = Files.createDirectory(root.resolve("state"));
        Path credentials = Files.createDirectory(state.resolve("credentials"));
        if (root.getFileSystem().supportedFileAttributeViews().contains("posix")) {
            Files.setPosixFilePermissions(state, PosixFilePermissions.fromString("rwx------"));
            Files.setPosixFilePermissions(credentials, PosixFilePermissions.fromString("rwx------"));
        }
        privateWrite(credentials.resolve("ai.json"), "{\"linksoulAppId\":\"synthetic\"}");
        privateWrite(root.resolve("token"), TOKEN + "\n");
        Map<String, String> env = coreEnvironment(root);
        env.put("LINGXI_INDEPENDENT_PRODUCT", "1");
        save(env, root, Map.of());
        return env;
    }

    static void save(Map<String, String> env, Path root, Map<String, Object> changes) throws Exception {
        root = root.toRealPath();
        Map<String, Object> value = new HashMap<>(Map.of("schemaVersion", 1,
                "stateDirectory", root.resolve("state").toString(), "agentTokenFile", root.resolve("token").toString(),
                "agentUrl", "http://127.0.0.1:18180"));
        value.putAll(changes);
        Path file = privateWrite(root.resolve("product selection.json"), JSON.toJSONString(value));
        env.put("LINGXI_PRODUCT_CONFIG", file.toString());
        pin(env, file);
    }

    static void pin(Map<String, String> env, Path file) throws Exception {
        env.put("LINGXI_PRODUCT_CONFIG_SHA256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file))));
    }

    @Test void coreDefaultsAndExplicitLoopbackConfigurationRemainAvailable() throws Exception {
        var env = coreEnvironment(temporary);
        var config = AgentConfiguration.load(env);
        assertEquals("https://127.0.0.1:3000", config.api().toString());
        assertEquals(18180, config.playbackAddress().getPort());
        assertEquals(env.get("LINGXI_AGENT_INTERNAL_TOKEN"), config.token());
        assertEquals(Path.of(env.get("LINGXI_CREDENTIALS_FILE")), config.credentials());
        env.put("LINGXI_API_URL", "https://localhost:3445");
        env.put("LINGXI_AGENT_URL", "http://127.0.0.1:18181");
        config = AgentConfiguration.load(env);
        assertEquals("https://localhost:3445", config.api().toString());
        assertEquals(18181, config.playbackAddress().getPort());
    }

    @Test void productSelectsMatchingOwnerTokenCredentialsAndPlaybackIgnoringCoreEnvironment() throws Exception {
        var env = productEnvironment(temporary);
        env.put("LINGXI_API_URL", "http://invalid.example");
        env.put("LINGXI_AGENT_URL", "http://invalid.example");
        env.remove("LINGXI_AGENT_INTERNAL_TOKEN");
        env.remove("LINGXI_CREDENTIALS_FILE");
        var config = AgentConfiguration.load(env);
        assertEquals("https://127.0.0.1:3444", config.api().toString());
        assertEquals(TOKEN, config.token());
        assertEquals(temporary.toRealPath().resolve("state/credentials/ai.json"), config.credentials());
        assertEquals("127.0.0.1", config.playbackAddress().getAddress().getHostAddress());
        assertFalse(config.toString().contains(TOKEN));
        env.put("LINGXI_SLAM_PORT", "3456");
        save(env, temporary, Map.of("agentUrl", "http://127.0.0.1:18181", "navigationSocket", "/synthetic/unused.sock"));
        config = AgentConfiguration.load(env);
        assertEquals("https://127.0.0.1:3456", config.api().toString());
        assertEquals(18181, config.playbackAddress().getPort());
        save(env, temporary, Map.of("actionsBridgeEndpoint", "http://127.0.0.1:18082/v1"));
        assertEquals("https://127.0.0.1:3456", AgentConfiguration.load(env).api().toString());
        save(env, temporary, Map.of("actionsBridgeEndpoint", "http://0.0.0.0:18082/v1"));
        assertThrows(Exception.class, () -> AgentConfiguration.load(env));
    }

    @Test void incompleteUnpinnedOrDisabledSelectionNeverFallsBackToCore() throws Exception {
        var env = productEnvironment(temporary);
        for (String key : List.of("LINGXI_PRODUCT_CONFIG", "LINGXI_PRODUCT_CONFIG_SHA256", "LINGXI_INDEPENDENT_PRODUCT")) {
            var invalid = new HashMap<>(env);
            invalid.remove(key);
            assertThrows(Exception.class, () -> AgentConfiguration.load(invalid), key);
        }
        for (String digest : List.of("0".repeat(64), "A".repeat(64), "short")) {
            env.put("LINGXI_PRODUCT_CONFIG_SHA256", digest);
            assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        }
    }

    @Test void changedBoundedAndStrictSchemaAreCheckedBeforeSdkStartup() throws Exception {
        var env = productEnvironment(temporary);
        Path file = Path.of(env.get("LINGXI_PRODUCT_CONFIG"));
        Files.writeString(file, "{}");
        assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        Files.writeString(file, " ".repeat(16385));
        pin(env, file);
        assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        for (var change : List.<Map<String, Object>>of(Map.of("apiKey", "unpermitted"), Map.of("schemaVersion", true),
                Map.of("schemaVersion", 1.0), Map.of("stateDirectory", 5), Map.of("navigationSocket", false))) {
            save(env, temporary, change);
            assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        }
    }

    @Test void productTokenAndStatePathsCannotBeAliasedOrOverlap() throws Exception {
        var env = productEnvironment(temporary);
        for (var change : List.<Map<String, Object>>of(Map.of("stateDirectory", temporary.toRealPath().toString()),
                Map.of("agentTokenFile", "relative-token"), Map.of("agentTokenFile", temporary.toRealPath() + "/./token"))) {
            save(env, temporary, change);
            assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        }
        save(env, temporary, Map.of());
        for (String value : List.of("short", "a".repeat(4097), "a".repeat(32) + "\nextra", "a".repeat(31) + " ")) {
            privateWrite(temporary.resolve("token"), value);
            assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        }
    }

    @Test void missingProductCredentialsDoNotUseTheUnrelatedCoreFile() throws Exception {
        var env = productEnvironment(temporary);
        privateWrite(Path.of(env.get("LINGXI_CREDENTIALS_FILE")), "{}");
        Files.delete(temporary.resolve("state/credentials/ai.json"));
        assertThrows(Exception.class, () -> AgentConfiguration.load(env));
    }

    @Test void productPortsAreCanonicalAndCannotSelectCoreOrCollideWithPlayback() throws Exception {
        var env = productEnvironment(temporary);
        for (String value : List.of("0", "03000", "3000", "18180", "65536", "-1", "3444/path")) {
            env.put("LINGXI_SLAM_PORT", value);
            assertThrows(Exception.class, () -> AgentConfiguration.load(env), value);
        }
        env.remove("LINGXI_SLAM_PORT");
        for (String value : List.of("https://127.0.0.1:18180", "http://localhost:18180", "http://192.0.2.1:18180",
                "http://127.0.0.1:018180", "http://127.0.0.1:65536", "http://user@127.0.0.1:18180", "http://127.0.0.1:18180/")) {
            save(env, temporary, Map.of("agentUrl", value));
            assertThrows(Exception.class, () -> AgentConfiguration.load(env), value);
        }
    }

    @Test void coreCallbackCannotLeaveLocalHttpsOrigin() {
        var env = coreEnvironment(temporary);
        for (String value : List.of("http://127.0.0.1:3000", "https://192.0.2.1:3000", "https://user@127.0.0.1:3000",
                "https://127.0.0.1:3000/path", "https://127.0.0.1:3000?query", "https://127.0.0.1:3000#fragment")) {
            env.put("LINGXI_API_URL", value);
            assertThrows(Exception.class, () -> AgentConfiguration.load(env), value);
        }
    }

    @Test void linuxPathsMustBePrivateOwnedAndDirectIncludingCredentialParents() throws Exception {
        assumeTrue(temporary.getFileSystem().supportedFileAttributeViews().contains("posix"));
        var env = productEnvironment(temporary);
        for (Path file : List.of(Path.of(env.get("LINGXI_PRODUCT_CONFIG")), temporary.resolve("token"),
                temporary.resolve("state"), temporary.resolve("state/credentials"), temporary.resolve("state/credentials/ai.json"))) {
            var previous = Files.getPosixFilePermissions(file);
            Files.setPosixFilePermissions(file, PosixFilePermissions.fromString(Files.isDirectory(file) ? "rwxr-xr-x" : "rw-r--r--"));
            assertThrows(Exception.class, () -> AgentConfiguration.load(env));
            Files.setPosixFilePermissions(file, previous);
        }
        Path link = temporary.resolve("token-link");
        Files.createSymbolicLink(link, temporary.resolve("token"));
        save(env, temporary, Map.of("agentTokenFile", link.toString()));
        assertThrows(Exception.class, () -> AgentConfiguration.load(env));
        save(env, temporary, Map.of());
        Path credentials = temporary.resolve("state/credentials/ai.json");
        Files.delete(credentials);
        Files.createSymbolicLink(credentials, temporary.resolve("token"));
        assertThrows(Exception.class, () -> AgentConfiguration.load(env));
    }

    @Test void invalidSelectionExitsBeforeSdkInitializationWithoutLoggingSecrets() throws Exception {
        var env = productEnvironment(temporary);
        env.put("LINGXI_PRODUCT_CONFIG_SHA256", "0".repeat(64));
        var builder = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp", System.getProperty("surefire.test.class.path", System.getProperty("java.class.path")), "local.lingxi.AgentMain");
        builder.environment().keySet().removeIf(key -> key.startsWith("LINGXI_") || key.startsWith("LINKSOUL_"));
        builder.environment().putAll(env);
        Process child = builder.start();
        try {
            assertTrue(child.waitFor(8, TimeUnit.SECONDS));
            assertEquals(78, child.exitValue());
            String output = new String(child.getInputStream().readAllBytes(), StandardCharsets.UTF_8) +
                    new String(child.getErrorStream().readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(output.contains("Agent configuration invalid"));
            assertFalse(output.contains(TOKEN));
            assertFalse(output.contains("synthetic-unrelated-core-token"));
            assertFalse(output.contains(temporary.toString()));
            assertFalse(output.contains("agent-sdk"));
        } finally { child.destroyForcibly(); }
    }
}
