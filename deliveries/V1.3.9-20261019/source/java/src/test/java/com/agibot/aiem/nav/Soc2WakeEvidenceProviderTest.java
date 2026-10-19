package com.agibot.aiem.nav;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;

public final class Soc2WakeEvidenceProviderTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
        checks++;
    }

    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("soc2-wake-test-");
        try {
            Path oldLog = writeLog(root, "log_20260920_120000",
                    "event_id: old-event, final asr result: old, \u8ddd\u79bb\u4e0a\u6b21\u5524\u9192: 10\n");
            Path currentLog = writeLog(root, "log_20260920_131528",
                    "event_id: fresh-event, final asr result: start, \u8ddd\u79bb\u4e0a\u6b21\u5524\u9192: 2777, extend_mic: false\n"
                            + "event_id: stale-event, final asr result: start, \u8ddd\u79bb\u4e0a\u6b21\u5524\u9192: 11667, extend_mic: false\n"
                            + "event_id: fresh-event-suffix, final asr result: start, \u8ddd\u79bb\u4e0a\u6b21\u5524\u9192: 10, extend_mic: false\n");
            check(Files.isRegularFile(oldLog) && Files.isRegularFile(currentLog), "test logs created");
            Soc2WakeEvidenceProvider provider = new Soc2WakeEvidenceProvider(
                    true, root, 8000, 1, 0, 65536);
            Soc2WakeEvidenceProvider.Evidence fresh = provider.find("fresh-event");
            check(fresh.found() && fresh.fresh() && fresh.ageMs() == 2777,
                    "fresh exact-event evidence is found");
            Soc2WakeEvidenceProvider.Evidence stale = provider.find("stale-event");
            check(stale.found() && !stale.fresh() && stale.ageMs() == 11667,
                    "stale exact-event evidence is retained for rejection");
            check(!provider.find("old-event").found(), "older session logs cannot authorize a request");
            check(provider.find("fresh-event").ageMs() == 2777,
                    "event id matching cannot use a longer id as a substring");
            check(!provider.find("bad event id").found(), "unsafe event ids are rejected");
            check(!new Soc2WakeEvidenceProvider(false, root, 8000, 1, 0, 65536)
                    .find("fresh-event").found(), "disabled provider fails closed");

            String padding = "x".repeat(140 * 1024);
            Path fallbackLog = writeLog(root, "log_20260920_140000",
                    "event_id: fallback-event, final asr result: start, 距离上次唤醒: 1234, extend_mic: false\n"
                            + padding + "\n");
            Soc2WakeEvidenceProvider fallbackProvider = new Soc2WakeEvidenceProvider(
                    true, root, 8000, 1, 0, 256 * 1024);
            fallbackProvider.warmup();
            Soc2WakeEvidenceProvider.Evidence fallback = fallbackProvider.find("fallback-event");
            check(Files.isRegularFile(fallbackLog) && fallback.found() && fallback.ageMs() == 1234,
                    "final attempt falls back beyond the 128 KiB fast tail");
            System.out.println("Soc2WakeEvidenceProviderTest passed: " + checks + " assertions");
        } finally {
            try (var paths = Files.walk(root)) {
                paths.sorted(Comparator.reverseOrder()).forEach(path -> {
                    try { Files.deleteIfExists(path); }
                    catch (Exception ignored) { }
                });
            }
        }
    }

    private static Path writeLog(Path root, String session, String text) throws Exception {
        Path log = root.resolve(session).resolve("agent/agent.log");
        Files.createDirectories(log.getParent());
        Files.writeString(log, text, StandardCharsets.UTF_8);
        return log;
    }
}
