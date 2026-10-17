package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.HashSet;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Process-bound connection state; historical log lines cannot certify a new process. */
final class AgentConnectionState {
    private static final Logger log = LoggerFactory.getLogger(AgentConnectionState.class);
    private final Path file;
    private final long pid;
    private Integer authCode;
    private final Set<String> onlineAgents = new HashSet<>();

    AgentConnectionState(Path file, long pid) {
        this.file = file;
        this.pid = pid;
        publish();
    }

    synchronized void auth(int code) {
        authCode = code;
        if (code != 0) onlineAgents.clear();
        publish();
    }

    synchronized void online(String agentId) {
        onlineAgents.add(agentId);
        publish();
    }

    synchronized void offline(String agentId) {
        onlineAgents.remove(agentId);
        publish();
    }

    private void publish() {
        Path temporary = null;
        try {
            Files.createDirectories(file.getParent());
            JSONObject state = new JSONObject();
            state.put("pid", pid);
            state.put("auth_code", authCode);
            state.put("robot_online", !onlineAgents.isEmpty());
            state.put("updated_at_ms", System.currentTimeMillis());
            temporary = Files.createTempFile(file.getParent(), ".agent-state-", ".json");
            Files.writeString(temporary, state.toJSONString());
            Files.move(temporary, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException error) {
            log.warn("Cannot publish current Java connection state: {}", error.getMessage());
        } finally {
            if (temporary != null) {
                try { Files.deleteIfExists(temporary); } catch (IOException ignored) { }
            }
        }
    }
}
