package com.agibot.aiem.nav;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Comparator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/** Reads the robot's own SOC2 speech log to recover KWS evidence omitted by AgentSDK text callbacks. */
final class Soc2WakeEvidenceProvider {
    private static final int FAST_TAIL_BYTES = 128 * 1024;
    private static final Pattern SAFE_EVENT_ID = Pattern.compile("[A-Za-z0-9_.:-]{1,160}");
    private static final Pattern LAST_WAKE_AGE = Pattern.compile(
            "(?:\\u8ddd\\u79bb\\u4e0a\\u6b21\\u5524\\u9192|distance\\s+since\\s+last\\s+wake(?:up)?)"
                    + "[^0-9]{0,32}([0-9]{1,9})",
            Pattern.CASE_INSENSITIVE);

    private final boolean enabled;
    private final Path logRoot;
    private final long maxWakeAgeMs;
    private final int attempts;
    private final long retryDelayMs;
    private final int tailBytes;
    private volatile Path cachedLog;

    Soc2WakeEvidenceProvider(AppConfig config) {
        this(config.voiceGateSoc2LogEnabled, Path.of(config.voiceGateSoc2LogRoot),
                config.voiceGateWakeSignalMaxAgeMs, config.voiceGateSoc2LogAttempts,
                config.voiceGateSoc2LogRetryMs, config.voiceGateSoc2LogTailBytes);
    }

    Soc2WakeEvidenceProvider(boolean enabled, Path logRoot, long maxWakeAgeMs,
                             int attempts, long retryDelayMs, int tailBytes) {
        this.enabled = enabled;
        this.logRoot = logRoot.toAbsolutePath().normalize();
        this.maxWakeAgeMs = maxWakeAgeMs;
        this.attempts = attempts;
        this.retryDelayMs = retryDelayMs;
        this.tailBytes = tailBytes;
    }

    void warmup() {
        if (!enabled) return;
        try {
            latestAgentLog(true);
        } catch (IOException | SecurityException ignored) {
            // A later request retries after the SOC2 log mount becomes available.
        }
    }

    Evidence find(String eventId) {
        String normalizedEventId = eventId == null ? "" : eventId.trim();
        if (!enabled) return Evidence.unavailable("soc2_log_disabled");
        if (!SAFE_EVENT_ID.matcher(normalizedEventId).matches()) {
            return Evidence.unavailable("invalid_event_id");
        }

        String lastReason = "soc2_log_unavailable";
        String lastLog = "";
        for (int attempt = 0; attempt < attempts; attempt++) {
            try {
                Path log = latestAgentLog(false);
                if (log == null) {
                    lastReason = "soc2_agent_log_missing";
                } else {
                    lastLog = log.toString();
                    Evidence evidence = scan(log, normalizedEventId, attempt + 1 == attempts);
                    if (evidence.found()) return evidence;
                    lastReason = evidence.reason();

                    // SOC2 can rotate to a new log directory without restarting this
                    // Java process. Refresh the cached path only after a miss so normal
                    // wake callbacks stay on the fast path.
                    Path refreshedLog = latestAgentLog(true);
                    if (refreshedLog != null && !refreshedLog.equals(log)) {
                        lastLog = refreshedLog.toString();
                        evidence = scan(refreshedLog, normalizedEventId, attempt + 1 == attempts);
                        if (evidence.found()) return evidence;
                        lastReason = evidence.reason();
                    }
                }
            } catch (IOException | SecurityException exception) {
                lastReason = "soc2_log_read_failed:" + exception.getClass().getSimpleName();
            }
            if (attempt + 1 < attempts && retryDelayMs > 0) {
                try {
                    Thread.sleep(retryDelayMs);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return Evidence.unavailable("soc2_log_retry_interrupted");
                }
            }
        }
        return new Evidence(false, false, -1L, lastReason, lastLog);
    }

    String description() {
        return enabled ? logRoot.toString() : "disabled";
    }

    private Path latestAgentLog(boolean refresh) throws IOException {
        Path cached = cachedLog;
        if (!refresh && cached != null
                && Files.isRegularFile(cached, LinkOption.NOFOLLOW_LINKS)) {
            return cached;
        }
        if (!Files.isDirectory(logRoot, LinkOption.NOFOLLOW_LINKS)) return null;
        try (Stream<Path> entries = Files.list(logRoot)) {
            Path latest = entries
                    .filter(path -> path.getFileName().toString().startsWith("log_"))
                    .filter(path -> Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS))
                    .sorted(Comparator.comparing((Path path) -> path.getFileName().toString()).reversed())
                    .map(path -> path.resolve("agent/agent.log"))
                    .filter(path -> Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
                    .findFirst()
                    .orElse(null);
            cachedLog = latest;
            return latest;
        }
    }

    private Evidence scan(Path log, String eventId, boolean allowFullFallback) throws IOException {
        int fastBytes = Math.min(tailBytes, FAST_TAIL_BYTES);
        Evidence fast = scanText(log, eventId, readTail(log, fastBytes));
        if (fast.found() || !allowFullFallback || fastBytes >= tailBytes
                || "soc2_log_invalid_wake_age".equals(fast.reason())) {
            return fast;
        }
        return scanText(log, eventId, readTail(log, tailBytes));
    }

    private Evidence scanText(Path log, String eventId, String tail) {
        String[] lines = tail.split("\\R");
        for (int index = lines.length - 1; index >= 0; index--) {
            String line = lines[index];
            if (!containsExactEventId(line, eventId)) continue;
            Matcher matcher = LAST_WAKE_AGE.matcher(line);
            if (!matcher.find()) continue;
            long ageMs;
            try {
                ageMs = Long.parseLong(matcher.group(1));
            } catch (NumberFormatException invalid) {
                return new Evidence(false, false, -1L, "soc2_log_invalid_wake_age", log.toString());
            }
            boolean fresh = ageMs <= maxWakeAgeMs;
            return new Evidence(true, fresh, ageMs,
                    fresh ? "soc2_log_wake_fresh" : "soc2_log_wake_stale", log.toString());
        }
        return new Evidence(false, false, -1L, "soc2_event_not_yet_logged", log.toString());
    }

    private static boolean containsExactEventId(String line, String eventId) {
        int offset = 0;
        while (offset <= line.length() - eventId.length()) {
            int match = line.indexOf(eventId, offset);
            if (match < 0) return false;
            int end = match + eventId.length();
            boolean leftBoundary = match == 0 || !isEventIdCharacter(line.charAt(match - 1));
            boolean rightBoundary = end == line.length() || !isEventIdCharacter(line.charAt(end));
            if (leftBoundary && rightBoundary) return true;
            offset = match + 1;
        }
        return false;
    }

    private static boolean isEventIdCharacter(char value) {
        return (value >= 'A' && value <= 'Z')
                || (value >= 'a' && value <= 'z')
                || (value >= '0' && value <= '9')
                || value == '_' || value == '.' || value == ':' || value == '-';
    }

    private String readTail(Path log, int maxBytes) throws IOException {
        try (FileChannel channel = FileChannel.open(log, StandardOpenOption.READ)) {
            long size = channel.size();
            int length = (int)Math.min(size, maxBytes);
            ByteBuffer buffer = ByteBuffer.allocate(length);
            channel.position(size - length);
            while (buffer.hasRemaining() && channel.read(buffer) >= 0) {
                // Continue until the selected tail is complete or EOF is reached.
            }
            return new String(buffer.array(), 0, buffer.position(), StandardCharsets.UTF_8);
        }
    }

    record Evidence(boolean found, boolean fresh, long ageMs, String reason, String logFile) {
        static Evidence unavailable(String reason) {
            return new Evidence(false, false, -1L, reason, "");
        }
    }
}
