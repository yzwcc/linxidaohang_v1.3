import { describe, expect, it, vi } from "vitest";
import { buildApp } from "../src/app.js";
import type { AppConfig } from "../src/config.js";
import type { RobotClient } from "../src/robotClient.js";

const config: AppConfig = {
  deploymentMode: "cloud",
  host: "127.0.0.1",
  port: 0,
  publicBaseUrl: "http://127.0.0.1",
  robot: {
    id: "default",
    connectionMode: "direct",
    host: "192.168.0.203",
    bridgePort: 18080,
    webPort: 8002,
    mapRoot: "/maps",
    sshUser: "agi",
    sshPassword: "",
    sshPort: 22,
    systemSshUser: "run",
    systemSshPassword: "",
    systemSshPort: 22,
    navHome: "/home/agi/robot_local_navigation_v3",
    javaAgentHome: "../robot_local_nav_java/robot_local_nav_java-new-java-interaction-end-hook",
    javaAgentExecutionMode: "local",
    robotJavaAgentHome: "/home/run/lingxi_java_agent",
    robotJavaAgentBridgePort: 18083,
    javaAgentAutoStart: true
  },
  auth: {
    appId: "",
    appSecret: "",
    tokenSecret: "test-secret",
    devBypass: true,
    tokenTtlSec: 3600
  }
};

function robot(overrides: Partial<RobotClient> = {}): RobotClient {
  return {
    invalidateStatus: () => undefined,
    status: async () => ({ bridge_online: true }),
    navStart: async (targetName: string) => ({ ok: true, target_name: targetName }),
    navStop: async () => ({ ok: true }),
    taskStart: async () => ({ ok: true }),
    taskPause: async () => ({ ok: true, paused: true }),
    taskResume: async () => ({ ok: true, resumed: true }),
    taskStop: async () => ({ ok: true }),
    speak: async (text: string, traceId?: string) => ({ ok: true, text, trace_id: traceId }),
    setActiveMap: async (mapName: string) => ({ ok: true, active_map_name: mapName }),
    reloadNavigation: async () => ({ ok: true }),
    currentLocalization: async () => ({
      ok: true,
      topic: "/slam/lidar_odom",
      pose: {
        position: { x: 1, y: 2, z: 0 },
        orientation: { x: 0, y: 0, z: 0, w: 1 },
        yaw_rad: 0,
        yaw_deg: 0,
        updated_at: 1783410000
      }
    }),
    maps: async () => ({ maps: [] }),
    linkcraftResources: async () => ({ ok: true, resources: [] }),
    map: async (mapName: string) => ({ map_name: mapName, points: [] }),
    mapImage: async () => ({ data: Buffer.from("image"), contentType: "image/png" }),
    savePoint: async (point: unknown) => point,
    deletePoint: async (pointId: string) => ({ ok: true, point_id: pointId }),
    saveTask: async (task: unknown) => task,
    pathSafety: async () => ({
      path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.1 }
    }),
    savePathSafety: async (pathSafety: { robot_radius_m: number; extra_clearance_m: number }) => ({
      path_safety: pathSafety
    }),
    relocalizationStatus: async () => ({ running: false, result: {} }),
    startRelocalization: async (payload: unknown) => payload,
    ...overrides
  };
}

describe("app", () => {
  it("prevents target IP changes in robot product mode", async () => {
    const app = buildApp({ ...config, deploymentMode: "robot" }, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/config",
      payload: { robot_host: "192.168.1.221" }
    });
    expect(response.statusCode).toBe(409);
    expect(response.json().message).toContain("浏览器地址栏");
  });

  it("logs in with dev bypass", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({ method: "POST", url: "/api/auth/login", payload: {} });
    expect(response.statusCode).toBe(200);
    expect(response.json().token).toBeTruthy();
  });

  it("validates navigation target", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({ method: "POST", url: "/api/robots/default/nav/start", payload: {} });
    expect(response.statusCode).toBe(400);
  });

  it("exposes mapping status and validates the save-map name", async () => {
    const app = buildApp(config, robot());
    const invalid = await app.inject({
      method: "POST",
      url: "/api/robots/default/mapping/stop-save",
      payload: { map_name: "../unsafe" }
    });
    expect(invalid.statusCode).toBe(409);
    expect(invalid.json().message).toContain("地图名称须为");
    const badBody = await app.inject({
      method: "POST",
      url: "/api/robots/default/mapping/stop-save",
      payload: { map_name: "valid", extra: true }
    });
    expect(badBody.statusCode).toBe(400);
  });

  it("proxies navigation start", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/nav/start",
      payload: { target_name: "起点" }
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ ok: true, target_name: "起点" });
  });

  it("reads and saves global path safety without mixing it into point data", async () => {
    const savePathSafety = vi.fn(async (pathSafety: { robot_radius_m: number; extra_clearance_m: number }) => ({
      path_safety: pathSafety
    }));
    const app = buildApp(config, robot({
      pathSafety: async () => ({ path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.12 } }),
      savePathSafety
    }));

    const loaded = await app.inject({ method: "GET", url: "/api/robots/default/navigation/path-safety" });
    expect(loaded.statusCode).toBe(200);
    expect(loaded.json()).toMatchObject({
      ok: true,
      path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.12 }
    });

    const saved = await app.inject({
      method: "POST",
      url: "/api/robots/default/navigation/path-safety",
      payload: { robot_radius_m: 0.253, extra_clearance_m: 0.18 }
    });
    expect(saved.statusCode).toBe(200);
    expect(saved.json()).toMatchObject({
      ok: true,
      path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.18 }
    });
    expect(savePathSafety).toHaveBeenCalledWith({ robot_radius_m: 0.253, extra_clearance_m: 0.18 });
  });

  it("requires a measured positive radius and rejects invalid global clearances", async () => {
    const savePathSafety = vi.fn();
    const app = buildApp(config, robot({ savePathSafety }));
    for (const payload of [
      { robot_radius_m: null, extra_clearance_m: 0.1 },
      { robot_radius_m: 0, extra_clearance_m: 0.1 },
      { robot_radius_m: 2.01, extra_clearance_m: 0.1 },
      { robot_radius_m: 0.253, extra_clearance_m: -0.01 },
      { robot_radius_m: 0.253, extra_clearance_m: 2.01 }
    ]) {
      const response = await app.inject({
        method: "POST",
        url: "/api/robots/default/navigation/path-safety",
        payload
      });
      expect(response.statusCode).toBe(400);
    }
    expect(savePathSafety).not.toHaveBeenCalled();
  });

  it("proxies the complete tour lifecycle", async () => {
    const app = buildApp(config, robot());
    for (const action of ["start", "pause", "resume", "stop"]) {
      const response = await app.inject({
        method: "POST",
        url: `/api/robots/default/task/${action}`
      });
      expect(response.statusCode).toBe(200);
      expect(response.json().ok).toBe(true);
    }
  });

  it("validates and proxies Chinese TTS", async () => {
    const app = buildApp(config, robot());
    const invalid = await app.inject({ method: "POST", url: "/api/robots/default/tts/speak", payload: {} });
    expect(invalid.statusCode).toBe(400);

    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/tts/speak",
      payload: { text: "语音服务测试成功", trace_id: "acceptance_test" }
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ ok: true, text: "语音服务测试成功", trace_id: "acceptance_test" });
  });

  it("saves a map task with map_name injected", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/maps/1779436804244/task",
      payload: { name: "默认讲解任务", enabled: true, steps: [{ point_id: "p1", enabled: true }] }
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ map_name: "1779436804244", steps: [{ point_id: "p1" }] });
  });

  it("preserves per-step arrival motion and expression settings", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/maps/1779436804244/task",
      payload: {
        id: "museum-tour",
        name: "展厅讲解",
        steps: [{
          point_id: "p1",
          speech_text: "欢迎来到第一展区。",
          arrival_motion: { motion: "wave", area: "right_hand" },
          arrival_emoji: { emotion: "happy", mode: "once" }
        }, {
          point_id: "p2",
          arrival_motion: null,
          arrival_emoji: null
        }, {
          point_id: "p3",
          arrival_motion: { motion: "clap" }
        }]
      }
    });
    expect(response.statusCode).toBe(200);
    expect(response.json().steps).toMatchObject([{
      point_id: "p1",
      arrival_motion: { motion: "wave", area: "right_hand", interrupt: true, force: true },
      arrival_emoji: { emotion: "happy", mode: "once" }
    }, {
      point_id: "p2",
      arrival_motion: null,
      arrival_emoji: null
    }, {
      point_id: "p3",
      arrival_motion: { motion: "clap", area: "whole_body" }
    }]);
  });

  it("lists and preserves a selected LinkCraft resource", async () => {
    const app = buildApp(config, robot({
      linkcraftResources: async () => ({
        ok: true,
        resources: [{
          resource_key: "linkcraft_resource_onnx_taiji",
          resource_version: "0.0.1",
          name: "太极",
          resource_type: "BODY_MONTION",
          duration_sec: 31.22,
          available: true
        }]
      })
    }));
    const list = await app.inject({
      method: "GET",
      url: "/api/robots/default/linkcraft/resources"
    });
    expect(list.statusCode).toBe(200);
    expect(list.json().resources[0].name).toBe("太极");

    const save = await app.inject({
      method: "POST",
      url: "/api/robots/default/maps/1779436804244/task",
      payload: {
        name: "灵创讲解任务",
        steps: [{
          point_id: "p1",
          linkcraft_action: {
            resource_key: "linkcraft_resource_onnx_taiji",
            resource_version: "0.0.1",
            name: "太极",
            resource_type: "BODY_MONTION",
            duration_sec: 31.22
          }
        }]
      }
    });
    expect(save.statusCode).toBe(200);
    expect(save.json().steps[0].linkcraft_action).toMatchObject({
      name: "太极",
      duration_sec: 31.22
    });
  });

  it("rejects non-LinkCraft resource keys and excessive durations", async () => {
    for (const linkcraftAction of [{
      resource_key: "app_123",
      resource_version: "1.0.0",
      name: "错误资源",
      resource_type: "BODY_MONTION",
      duration_sec: 30
    }, {
      resource_key: "linkcraft_resource_onnx_too_long",
      resource_version: "0.0.1",
      name: "超时资源",
      resource_type: "BODY_MONTION",
      duration_sec: 301
    }]) {
      const app = buildApp(config, robot());
      const response = await app.inject({
        method: "POST",
        url: "/api/robots/default/maps/1779436804244/task",
        payload: {
          name: "错误任务",
          steps: [{ point_id: "p1", linkcraft_action: linkcraftAction }]
        }
      });
      expect(response.statusCode).toBe(400);
      expect(response.json().message).toContain("灵创资源");
    }
  });

  it("rejects unsupported tour-step effects with a Chinese message", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/maps/1779436804244/task",
      payload: {
        name: "错误任务",
        steps: [{ point_id: "p1", arrival_motion: { motion: "unknown_motion" } }]
      }
    });
    expect(response.statusCode).toBe(400);
    expect(response.json().message).toBe("讲解任务中的点位、动作或表情配置不正确");
  });

  it("rejects a documented motion paired with the wrong body area", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/maps/1779436804244/task",
      payload: {
        name: "错误部位",
        steps: [{ point_id: "p1", arrival_motion: { motion: "wave", area: "whole_body" } }]
      }
    });
    expect(response.statusCode).toBe(400);
    expect(response.json().message).toContain("动作或表情配置不正确");
  });

  it("accepts random speech and empty interrupt motions", async () => {
    const app = buildApp(config, robot());
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/maps/1779436804244/task",
      payload: {
        name: "特殊动作任务",
        steps: [{
          point_id: "p1",
          arrival_motion: { motion: "random_speech", area: "21" }
        }, {
          point_id: "p2",
          arrival_motion: { motion: "random_speech", area: "48" }
        }, {
          point_id: "p3",
          arrival_motion: { motion: "empty_motion", area: "0", interrupt: false }
        }]
      }
    });
    expect(response.statusCode).toBe(200);
    expect(response.json().steps).toMatchObject([{
      arrival_motion: { motion: "random_speech", area: "21" }
    }, {
      arrival_motion: { motion: "random_speech", area: "48" }
    }, {
      arrival_motion: { motion: "empty_motion", area: "0", interrupt: true }
    }]);
  });

  it("rejects random speech and empty motions with invalid domains", async () => {
    for (const arrivalMotion of [
      { motion: "random_speech", area: "0" },
      { motion: "empty_motion", area: "21" }
    ]) {
      const app = buildApp(config, robot());
      const response = await app.inject({
        method: "POST",
        url: "/api/robots/default/maps/1779436804244/task",
        payload: {
          name: "错误动作域",
          steps: [{ point_id: "p1", arrival_motion: arrivalMotion }]
        }
      });
      expect(response.statusCode).toBe(400);
    }
  });
});
