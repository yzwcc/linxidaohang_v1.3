package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSONObject;
import java.nio.file.Files;
import java.nio.file.Path;

public final class AgentConnectionStateTest {
    private static void check(boolean value) { if (!value) throw new AssertionError("connection state"); }
    public static void main(String[] args) throws Exception {
        Path directory = Files.createTempDirectory("agent-connection-");
        Path file = directory.resolve("state.json");
        try {
            AgentConnectionState state = new AgentConnectionState(file, 1234);
            check(JSONObject.parseObject(Files.readString(file)).get("auth_code") == null);
            state.online("robot");
            state.auth(0);
            JSONObject live = JSONObject.parseObject(Files.readString(file));
            check(live.getLongValue("pid") == 1234 && live.getBooleanValue("robot_online") && live.getIntValue("auth_code") == 0);
            state.offline("robot");
            check(!JSONObject.parseObject(Files.readString(file)).getBooleanValue("robot_online"));
            state.online("robot"); state.auth(401);
            check(!JSONObject.parseObject(Files.readString(file)).getBooleanValue("robot_online"));
            new AgentConnectionState(file, 2345);
            JSONObject restarted = JSONObject.parseObject(Files.readString(file));
            check(restarted.getLongValue("pid") == 2345 && restarted.get("auth_code") == null && !restarted.getBooleanValue("robot_online"));
            try (var files = Files.list(directory)) { check(files.count() == 1); }
            System.out.println("AgentConnectionStateTest passed: early callbacks, auth failure, offline, restart, atomic state");
        } finally { Files.deleteIfExists(file); Files.delete(directory); }
    }
}
