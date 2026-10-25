import cors from "@fastify/cors";
import Fastify, { type FastifyInstance } from "fastify";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import type { AppConfig } from "./config.js";
import { loginWithWxCode, verifyToken } from "./auth.js";
import { createRobotClient, type RobotClient } from "./robotClient.js";
import { isBridgeAction, restartRobotVoiceStack, runBridgeAction } from "./sshControl.js";
import { getJavaAgentStatus, startJavaAgent, stopJavaAgent } from "./javaAgentControl.js";
import { getCurrentLocalization } from "./localizationControl.js";
import { getMappingHealth, getMappingPreview, getMappingSession, startMapping, stopAndSaveMapping } from "./mappingControl.js";
import { loadRuntimeConfig, loadRuntimeRobotHost, runtimeConfigPath, saveRobotHost } from "./runtimeConfig.js";
import { getRobotSystemHealth, recoverRobotLocalization, restartRobotSystem } from "./systemControl.js";
import { getTunnelDiagnostics } from "./tunnelDiagnostics.js";
import {
  invalidateTunnelTargetIdentity,
  verifyTunnelTarget,
  waitForTunnelTarget
} from "./tunnelTarget.js";
import { buildStartupSnapshot } from "./startupSnapshot.js";
import {
  discoverLinkcraftResourcesAsRobotUser,
  discoverLocalLinkcraftResources
} from "./linkcraftResources.js";

const loginSchema = z.object({ code: z.string().optional() });
const navStartSchema = z.object({ target_name: z.string().trim().min(1) });
const ttsSpeakSchema = z.object({
  text: z.string().trim().min(1).max(500),
  trace_id: z.string().trim().min(1).max(100).optional()
});
const pathSafetySchema = z.object({
  robot_radius_m: z.number().finite().gt(0).max(2),
  extra_clearance_m: z.number().finite().min(0).max(2)
}).strict();
const mappingSaveSchema = z.object({
  map_name: z.string().trim().min(1).max(48)
}).strict();

const relocalizationSchema = z.object({
  map_id: z.number().positive(),
  x: z.number(),
  y: z.number(),
  map_root: z.string().optional(),
  z: z.number().optional(),
  qx: z.number().optional(),
  qy: z.number().optional(),
  qz: z.number().optional(),
  qw: z.number().optional(),
  timeout_sec: z.number().positive().optional(),
  verify_tolerance_m: z.number().positive().optional()
}).passthrough();
const speechMotionAreas = [
  "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34",
  "35", "36", "37", "38", "39", "40", "41", "42", "43", "44", "45", "46", "47", "48"
] as const;
const documentedMotionAreaValues = [
  "right_hand", "left_hand", "both_hands", "whole_body", "0", ...speechMotionAreas
] as const;
const documentedMotionAreas: Record<string, readonly string[]> = {
  random_speech: speechMotionAreas,
  empty_motion: ["0"],
  raise_hand: ["right_hand", "left_hand"],
  wave: ["right_hand", "left_hand"],
  handshake: ["right_hand", "left_hand"],
  flying_kiss: ["right_hand", "left_hand"],
  salute: ["right_hand", "left_hand"],
  sweetheart: ["both_hands", "right_hand", "left_hand"],
  horizontal_arm: ["both_hands", "right_hand", "left_hand"],
  high_five: ["right_hand", "left_hand"],
  chestwave: ["right_hand", "left_hand"],
  clap: ["whole_body"],
  hug: ["whole_body"],
  cheer: ["whole_body"],
  bye: ["whole_body"],
  lightwave: ["whole_body"],
  handx: ["whole_body"],
  bow: ["whole_body"],
  scratch_head: ["whole_body"],
  scratch_back: ["whole_body"]
};
const arrivalMotionSchema = z.object({
  motion: z.enum([
    "random_speech", "empty_motion",
    "raise_hand", "wave", "handshake", "flying_kiss", "salute", "sweetheart",
    "horizontal_arm", "high_five", "chestwave", "clap", "hug", "cheer", "bye",
    "lightwave", "handx", "bow", "scratch_head", "scratch_back"
  ]),
  area: z.enum(documentedMotionAreaValues).optional(),
  interrupt: z.boolean().default(true),
  force: z.boolean().default(true)
}).superRefine((value, context) => {
  const allowedAreas = documentedMotionAreas[value.motion] || [];
  if (value.area && !allowedAreas.includes(value.area)) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ["area"], message: "动作部位不支持该动作" });
  }
}).transform((value) => ({
  ...value,
  area: value.area || documentedMotionAreas[value.motion][0],
  interrupt: value.motion === "empty_motion" ? true : value.interrupt
}));
const arrivalEmojiSchema = z.object({
  emotion: z.enum([
    "blink", "calm", "calm2", "game", "cute", "cute2", "cute3", "cute4",
    "eye_close", "eye_open", "boring", "abnormal", "sleepy", "happy",
    "extreme_happy", "extreme_happy2", "sad", "sympathy", "confuse", "shock",
    "act_cute", "serious", "thinking", "angry", "extreme_angry", "adore",
    "extreme_adore", "charge"
  ]),
  mode: z.enum(["once", "loop"]).default("once"),
  priority: z.number().int().min(0).max(255).optional()
});
const linkcraftActionSchema = z.object({
  resource_key: z.string().trim().min(1).max(256).refine(
    (value) => value.toLowerCase().startsWith("linkcraft_resource_"),
    "只能选择机器人已下发的灵创资源"
  ),
  resource_version: z.string().trim().min(1).max(64),
  name: z.string().trim().min(1).max(200),
  resource_type: z.enum(["BODY_MONTION", "ARM_MONTION"]),
  duration_sec: z.number().positive().max(300)
});
const taskSchema = z.object({
  id: z.string().trim().optional(),
  name: z.string().trim().min(1).default("默认讲解任务"),
  enabled: z.boolean().default(true),
  steps: z.array(z.object({
    point_id: z.string().min(1),
    enabled: z.boolean().default(true),
    speech_text: z.string().trim().max(2000).optional().default(""),
    arrival_motion: arrivalMotionSchema.nullable().optional(),
    arrival_emoji: arrivalEmojiSchema.nullable().optional(),
    linkcraft_action: linkcraftActionSchema.nullable().optional()
  })).default([])
}).passthrough();

function runLocalCommand(file: string, args: string[], timeoutMs = 8000): Promise<{ ok: boolean; output: string }> {
  return new Promise((resolve) => {
    const child = spawn(file, args, { windowsHide: true });
    let output = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      resolve({ ok: false, output: output.trim() || "本机恢复命令执行超时" });
    }, timeoutMs);
    child.stdout.on("data", (data) => { output += data.toString(); });
    child.stderr.on("data", (data) => { output += data.toString(); });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ ok: false, output: error.message });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ ok: code === 0, output: output.trim() });
    });
  });
}

async function clearStaleCloudTunnel(): Promise<{ ok: boolean; output: string }> {
  if (process.platform === "win32") {
    return { ok: false, output: "当前系统不支持自动清理云端隧道" };
  }
  return runLocalCommand("pkill", ["-f", "sshd: robot-tunnel"], 5000);
}

export function buildApp(config: AppConfig, robotClient?: RobotClient): FastifyInstance {
  const app = Fastify({ logger: true });
  // The compiled server and its bundled public assets are both under console/dist.
  const publicDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "public");
  let activeConfig = loadRuntimeConfig(config);
  const usesInjectedRobotClient = robotClient != null;
  let activeRobotClient = robotClient ?? createRobotClient(activeConfig);
  const getConfig = () => activeConfig;
  const getRobot = () => activeRobotClient;
  let javaStatusInFlight: Promise<any> | null = null;
  let javaStatusCache: { value: any; expiresAt: number } | null = null;

  async function readJavaAgentStatus(force = false) {
    if (!force && javaStatusCache && javaStatusCache.expiresAt > Date.now()) return javaStatusCache.value;
    if (javaStatusInFlight) return javaStatusInFlight;
    const request = getJavaAgentStatus(getConfig());
    javaStatusInFlight = request;
    try {
      const value = await request;
      javaStatusCache = { value, expiresAt: Date.now() + 60000 };
      return value;
    } finally {
      if (javaStatusInFlight === request) javaStatusInFlight = null;
    }
  }

  app.register(cors, { origin: true });

  app.addHook("onSend", async (request, reply, payload) => {
    if (/^\/console\/[^/?]+\.(css|js)(\?|$)/.test(request.url)) {
      reply.header("Cache-Control", "no-cache, must-revalidate");
    } else if (request.url.startsWith("/console/") || request.url.startsWith("/api/")) {
      reply.header("Cache-Control", "no-store, no-cache, must-revalidate");
      reply.header("Pragma", "no-cache");
      reply.header("Expires", "0");
    }
    return payload;
  });

  app.addHook("preHandler", async (request, reply) => {
    const publicPath = request.url.startsWith("/api/auth/login")
      || request.url === "/health"
      || request.url === "/"
      || request.url.startsWith("/console/");
    if (publicPath) return;
    if (config.auth.devBypass && !request.headers.authorization) return;
    const header = request.headers.authorization || "";
    const query = request.query as { access_token?: string };
    const token = header.startsWith("Bearer ") ? header.slice("Bearer ".length) : (query.access_token || "");
    if (!token || !verifyToken(token, config)) {
      await reply.code(401).send({ ok: false, message: "unauthorized" });
    }
  });

  app.addHook("preHandler", async (request, reply) => {
    const method = request.method.toUpperCase();
    const isRobotMutation = ["POST", "PUT", "PATCH", "DELETE"].includes(method)
      && request.url.startsWith("/api/robots/default/");
    const isTargetConfiguration = request.url.startsWith("/api/robots/default/config");
    if (!isRobotMutation || isTargetConfiguration || getConfig().robot.connectionMode !== "tunnel") return;

    const expectedHost = loadRuntimeRobotHost() || process.env.ROBOT_TUNNEL_TARGET_HOST || "";
    const identity = await verifyTunnelTarget(getConfig(), expectedHost);
    if (!identity.ok) {
      await reply.code(409).send({
        ok: false,
        status: "tunnel_target_mismatch",
        message: `已阻止操作：${identity.detail}。请保存正确 IP 并等待目标连接成功。`,
        target_identity: identity
      });
    }
  });

  app.get("/health", async () => ({
    ok: true,
    robot_id: config.robot.id,
    deployment_mode: config.deploymentMode,
    version: process.env.DEPLOYMENT_VERSION ?? "2026.07.22-v140-tour-control"
  }));

  app.get("/", async (_request, reply) => {
    return reply.redirect("/console/");
  });

  app.get("/console/", async (_request, reply) => {
    const html = await fs.readFile(path.join(publicDir, "index.html"));
    return reply.type("text/html; charset=utf-8").send(html);
  });

  app.get("/console/:file", async (request, reply) => {
    const params = request.params as { file: string };
    const allowedTypes: Record<string, string> = {
      ".css": "text/css; charset=utf-8",
      ".js": "application/javascript; charset=utf-8"
    };
    const ext = path.extname(params.file);
    if (!allowedTypes[ext] || params.file.includes("/") || params.file.includes("\\")) {
      return reply.code(404).send("not found");
    }
    const data = await fs.readFile(path.join(publicDir, params.file));
    return reply.type(allowedTypes[ext]).send(data);
  });

  app.post("/api/auth/login", async (request, reply) => {
    const parsed = loginSchema.safeParse(request.body ?? {});
    if (!parsed.success) return reply.code(400).send({ ok: false, message: "登录请求格式不正确" });
    if (!config.auth.devBypass && !parsed.data.code) {
      return reply.code(400).send({ ok: false, message: "code is required" });
    }
    const token = await loginWithWxCode(parsed.data.code || "dev", config);
    return { ok: true, token };
  });

  app.get("/api/robots/default/status", async (request) => {
    const query = request.query as { refresh?: string };
    return getRobot().status(query.refresh === "1");
  });
  let startupStateInFlight: Promise<any> | null = null;
  let startupStateCache: { value: any; expiresAt: number } | null = null;

  async function readStartupState(force = false) {
    if (!force && startupStateCache && startupStateCache.expiresAt > Date.now()) return startupStateCache.value;
    if (startupStateInFlight) return startupStateInFlight;
    const request = (async () => {
    const runtimeRobotHost = loadRuntimeRobotHost() || process.env.ROBOT_TUNNEL_TARGET_HOST || getConfig().robot.host;
    const [robot, relocalization, javaAgent, targetIdentity] = await Promise.allSettled([
      getRobot().status(force),
      getRobot().relocalizationStatus(),
      readJavaAgentStatus(force),
      verifyTunnelTarget(getConfig(), runtimeRobotHost, { force })
    ]);
    return buildStartupSnapshot({
      deploymentMode: getConfig().deploymentMode,
      connectionMode: getConfig().robot.connectionMode,
      proxyHost: getConfig().robot.host,
      targetHost: runtimeRobotHost,
      robot: robot.status === "fulfilled" ? robot.value as any : null,
      relocalization: relocalization.status === "fulfilled" ? relocalization.value as any : null,
      javaAgent: javaAgent.status === "fulfilled" ? javaAgent.value : null,
      javaAgentReachable: javaAgent.status === "fulfilled",
      javaAgentError: javaAgent.status === "rejected" ? String(javaAgent.reason?.message || javaAgent.reason) : undefined,
      targetIdentity: targetIdentity.status === "fulfilled" ? targetIdentity.value : null
    });
    })();
    startupStateInFlight = request;
    try {
      const value = await request;
      startupStateCache = { value, expiresAt: Date.now() + 10000 };
      return value;
    } finally {
      if (startupStateInFlight === request) startupStateInFlight = null;
    }
  }

  async function waitForStartupReady(timeoutMs = 25000) {
    const startedAt = Date.now();
    let latest = await readStartupState(true);
    while (Date.now() - startedAt < timeoutMs) {
      const javaReady = Boolean(latest.java_agent?.running && latest.java_agent?.lingxiConnected && latest.java_agent?.robotOnline);
      const navReady = Boolean(
        latest.robot?.bridge_online
        && latest.robot?.map_web_online
        && latest.robot?.navigation_gateway_online
        && latest.robot?.voice_gateway_online
      );
      const tunnelReady = Boolean(latest.tunnel?.ssh.ok);
      if (javaReady && navReady && tunnelReady) return latest;
      await new Promise((resolve) => setTimeout(resolve, 1500));
      latest = await readStartupState(true);
    }
    return latest;
  }

  async function waitForJavaAgentConnected(timeoutMs = 15000) {
    const startedAt = Date.now();
    let latest = await readJavaAgentStatus(true);
    while (Date.now() - startedAt < timeoutMs && !(latest.running && latest.lingxiConnected)) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      latest = await readJavaAgentStatus(true);
    }
    return latest;
  }

  async function waitForRobotVoiceOnline(timeoutMs = 90000) {
    const startedAt = Date.now();
    let latest = await readJavaAgentStatus(true);
    while (Date.now() - startedAt < timeoutMs && !latest.robotOnline) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      latest = await readJavaAgentStatus(true);
    }
    return latest;
  }

  async function startJavaAgentAndRebindRobotVoice() {
    await startJavaAgent(getConfig());
    const javaAgent = await waitForJavaAgentConnected();
    if (!(javaAgent.running && javaAgent.lingxiConnected)) {
      throw new Error(`灵心平台连接等待超时：${javaAgent.lastAuthState || "尚未收到认证成功状态"}`);
    }
    if (javaAgent.robotOnline) {
      return { ...javaAgent, robotVoice: { output: "机器人语音回调已在线，无需重复重载" } };
    }
    const robotVoice = await restartRobotVoiceStack(getConfig());
    const onlineStatus = await waitForRobotVoiceOnline();
    if (!onlineStatus.robotOnline) {
      throw new Error("机器人本机语音服务已启动，但等待灵心语音回调上线超时");
    }
    return { ...onlineStatus, robotVoice };
  }

  app.get("/api/robots/default/startup", async () => readStartupState());

  app.post("/api/robots/default/start-all", async () => {
    const steps: Array<{ key: string; label: string; ok: boolean; detail: string }> = [];
    javaStatusCache = null;
    startupStateCache = null;

    let tunnelBefore = await readStartupState();
    if (!tunnelBefore.tunnel?.ssh.ok && getConfig().robot.connectionMode === "tunnel") {
      const recovery = await clearStaleCloudTunnel();
      steps.push({
        key: "tunnel_recover",
        label: "公网通道自恢复",
        ok: true,
        detail: recovery.output || "已清理云端旧通道，正在等待机器人自动重连"
      });
      await new Promise((resolve) => setTimeout(resolve, 12000));
      tunnelBefore = await readStartupState();
    }

    if (!tunnelBefore.tunnel?.ssh.ok) {
      steps.push({
        key: "tunnel",
        label: "机器人运维通道",
        ok: false,
        detail: "网站还没有连到机器人。请确认机器人已开机、已联网，且机器人上的反向通道保活脚本正在运行。"
      });
      return {
        ok: false,
        message: "机器人运维通道未连接，暂时无法从网站启动机器人服务",
        steps,
        startup: tunnelBefore
      };
    }

    const alreadyReady = Boolean(
      tunnelBefore.robot?.bridge_online
      && tunnelBefore.robot?.map_web_online
      && tunnelBefore.robot?.navigation_gateway_online
      && tunnelBefore.robot?.voice_gateway_online
      && tunnelBefore.java_agent?.running
      && tunnelBefore.java_agent?.lingxiConnected
      && tunnelBefore.java_agent?.robotOnline
    );
    if (alreadyReady) {
      return {
        ok: true,
        message: "所有服务已经就绪，无需重复启动",
        steps: [
          { key: "bridge", label: "导航和地图服务", ok: true, detail: "服务已在线" },
          { key: "java_agent", label: "语音服务和灵心平台", ok: true, detail: "服务已在线" },
          { key: "robot_voice", label: "机器人语音回调", ok: true, detail: "服务已在线" }
        ],
        startup: tunnelBefore
      };
    }

    try {
      const bridge = await runBridgeAction("start", getConfig());
      getRobot().invalidateStatus();
      startupStateCache = null;
      for (let attempt = 0; attempt < 12; attempt += 1) {
        getRobot().invalidateStatus();
        const status = await getRobot().status();
        if ((status as any).bridge_online && (status as any).map_web_online) break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      steps.push({
        key: "bridge",
        label: "机器人导航和地图服务",
        ok: true,
        detail: bridge.output || "导航和地图服务启动命令已提交"
      });
    } catch (error) {
      steps.push({
        key: "bridge",
        label: "机器人导航和地图服务",
        ok: false,
        detail: error instanceof Error ? error.message : String(error)
      });
    }

    try {
      const javaAgent = await startJavaAgent(getConfig());
      steps.push({
        key: "java_agent",
        label: "语音服务和灵心平台连接",
        ok: Boolean(javaAgent.running),
        detail: javaAgent.lingxiConnected
          ? "语音服务已启动，灵心平台连接成功"
          : "语音服务已启动，正在等待灵心平台认证"
      });
    } catch (error) {
      steps.push({
        key: "java_agent",
        label: "语音服务和灵心平台连接",
        ok: false,
        detail: error instanceof Error ? error.message : String(error)
      });
    }

    try {
      const javaAgent = await waitForJavaAgentConnected();
      if (!(javaAgent.running && javaAgent.lingxiConnected)) {
        throw new Error(`灵心平台连接等待超时：${javaAgent.lastAuthState || "尚未收到认证成功状态"}`);
      }
      const robotStatus = await getRobot().status();
      const voiceBridgeOnline = Boolean((robotStatus as any).voice_gateway_online);
      const robotVoice = javaAgent.robotOnline && voiceBridgeOnline
        ? { output: "机器人语音回调和本机语音桥接均已在线，无需重复重载" }
        : await restartRobotVoiceStack(getConfig());
      if (!(javaAgent.robotOnline && voiceBridgeOnline)) {
        getRobot().invalidateStatus();
        javaStatusCache = null;
        startupStateCache = null;
      }
      const onlineStatus = javaAgent.robotOnline ? javaAgent : await waitForRobotVoiceOnline();
      if (!onlineStatus.robotOnline) {
        throw new Error("机器人本机语音服务已启动，但等待灵心语音回调上线超时");
      }
      steps.push({
        key: "robot_voice",
        label: "机器人本机语音与唤醒服务",
        ok: true,
        detail: robotVoice.output.includes("无需重复重载")
          ? robotVoice.output
          : "机器人本机语音、唤醒和音频服务已启动"
      });
    } catch (error) {
      steps.push({
        key: "robot_voice",
        label: "机器人本机语音与唤醒服务",
        ok: false,
        detail: error instanceof Error ? error.message : String(error)
      });
    }

    startupStateCache = null;
    const startup = await waitForStartupReady(25000);
    const requiredReady = Boolean(
      startup.tunnel?.ssh.ok
      && startup.robot?.bridge_online
      && startup.robot?.map_web_online
      && startup.robot?.navigation_gateway_online
      && startup.robot?.voice_gateway_online
      && startup.java_agent?.running
      && startup.java_agent?.lingxiConnected
      && startup.java_agent?.robotOnline
    );
    const finalStepState: Record<string, boolean> = {
      bridge: Boolean(startup.robot?.bridge_online && startup.robot?.map_web_online && startup.robot?.navigation_gateway_online),
      java_agent: Boolean(startup.java_agent?.running && startup.java_agent?.lingxiConnected),
      robot_voice: Boolean(startup.java_agent?.robotOnline && startup.robot?.voice_gateway_online)
    };
    for (const step of steps) {
      if (!step.ok && finalStepState[step.key]) {
        step.ok = true;
        step.detail = `${step.detail}；最终状态已确认正常`;
      }
    }
    const ok = steps.every((step) => step.ok) && requiredReady;
    return {
      ok,
      message: ok ? "所有服务已启动，可以开始使用机器人" : "服务已启动，但仍有项目需要确认",
      steps,
      startup
    };
  });

  app.post("/api/robots/default/nav/start", async (request, reply) => {
    const parsed = navStartSchema.safeParse(request.body);
    if (!parsed.success) return reply.code(400).send({ ok: false, message: "请选择导航点位" });
    return getRobot().navStart(parsed.data.target_name);
  });

  app.get("/api/robots/default/config", async () => ({
    ok: true,
    deployment_mode: getConfig().deploymentMode,
    connection_mode: getConfig().robot.connectionMode,
    host_editable: getConfig().deploymentMode !== "robot",
    robot_host: getConfig().robot.connectionMode === "tunnel"
      ? (loadRuntimeRobotHost() || process.env.ROBOT_TUNNEL_TARGET_HOST || "")
      : getConfig().robot.host,
    proxy_host: getConfig().robot.host,
    bridge_port: getConfig().robot.bridgePort,
    web_port: getConfig().robot.webPort,
    map_root: getConfig().robot.mapRoot,
    local_addresses: localIpv4Addresses(),
    access_urls: localIpv4Addresses().map((address) => `http://${address}:${getConfig().port}/console/`),
    runtime_config_path: runtimeConfigPath()
  }));
  app.post("/api/robots/default/config", async (request, reply) => {
    if (getConfig().deploymentMode === "robot") {
      return reply.code(409).send({
        ok: false,
        message: "当前控制台已部署在机器人本机。请直接在浏览器地址栏输入机器人 IP 和端口，无需在网页内填写 IP。"
      });
    }
    const parsed = z.object({ robot_host: z.string().trim().min(1) }).safeParse(request.body ?? {});
    if (!parsed.success) return reply.code(400).send({ ok: false, message: "请填写机器人 IP 或主机名" });
    const previousRobotHost = loadRuntimeRobotHost().trim();
    const targetChanged = previousRobotHost !== parsed.data.robot_host;
    const savedConfig = await saveRobotHost(config, parsed.data.robot_host);
    if (targetChanged) invalidateTunnelTargetIdentity();
    startupStateCache = null;
    if (getConfig().robot.connectionMode !== "tunnel") {
      activeConfig = savedConfig;
      if (!usesInjectedRobotClient) {
        activeRobotClient = createRobotClient(activeConfig);
      }
    }
    let tunnelRecovered = false;
    let targetIdentity;
    if (getConfig().robot.connectionMode === "tunnel") {
      const recovery = await clearStaleCloudTunnel();
      tunnelRecovered = recovery.ok;
      invalidateTunnelTargetIdentity();
      startupStateCache = null;
      await new Promise((resolve) => setTimeout(resolve, 2000));
      targetIdentity = await waitForTunnelTarget(getConfig(), parsed.data.robot_host, 40000);
    } else {
      targetIdentity = await verifyTunnelTarget(savedConfig, parsed.data.robot_host, { force: true });
    }
    return {
      ok: true,
      connection_mode: activeConfig.robot.connectionMode,
      host_editable: true,
      robot_host: parsed.data.robot_host,
      proxy_host: activeConfig.robot.host,
      bridge_port: activeConfig.robot.bridgePort,
      web_port: activeConfig.robot.webPort,
      tunnel_target_verified: targetIdentity.ok,
      tunnel_recovered: tunnelRecovered,
      target_changed: targetChanged,
      target_identity: targetIdentity,
      message: targetIdentity.ok
        ? `已连接机器人 ${parsed.data.robot_host}`
        : `IP 已保存，但隧道尚未连接 ${parsed.data.robot_host}，已禁止向旧机器人发送操作`
    };
  });

  app.post("/api/robots/default/nav/stop", async () => getRobot().navStop());
  app.post("/api/robots/default/task/start", async () => getRobot().taskStart());
  app.post("/api/robots/default/task/pause", async () => getRobot().taskPause());
  app.post("/api/robots/default/task/resume", async () => getRobot().taskResume());
  app.post("/api/robots/default/task/stop", async () => getRobot().taskStop());
  app.post("/api/robots/default/tts/speak", async (request, reply) => {
    const parsed = ttsSpeakSchema.safeParse(request.body ?? {});
    if (!parsed.success) return reply.code(400).send({ ok: false, message: "请输入要播报的中文内容" });
    return getRobot().speak(parsed.data.text, parsed.data.trace_id);
  });
  app.post("/api/robots/default/nav/map", async (request, reply) => {
    const parsed = z.object({ active_map_name: z.string().trim().min(1) }).safeParse(request.body ?? {});
    if (!parsed.success) return reply.code(400).send({ ok: false, message: "请选择要切换的地图" });
    return getRobot().setActiveMap(parsed.data.active_map_name);
  });
  app.post("/api/robots/default/nav/reload", async () => getRobot().reloadNavigation());
  app.get("/api/robots/default/localization/current", async () => {
    try {
      const payload = await getRobot().currentLocalization() as any;
      if (!payload?.ok || !payload?.pose) {
        return {
          ok: false,
          source: "bridge",
          status: payload?.status || "no_data",
          message: payload?.message || "暂未收到机器人定位数据",
          topic: payload?.topic || "/slam/lidar_odom",
          acquired_at: new Date().toISOString()
        };
      }
      const pose = payload.pose;
      return {
        ok: true,
        source: "bridge",
        topic: payload.topic || "/slam/lidar_odom",
        acquired_at: typeof pose.updated_at === "number" ? new Date(pose.updated_at * 1000).toISOString() : new Date().toISOString(),
        pose: {
          position: pose.position,
          orientation: pose.orientation,
          yaw_rad: pose.yaw_rad,
          yaw_deg: pose.yaw_deg
        }
      };
    } catch {
      return getCurrentLocalization(getConfig());
    }
  });
  app.get("/api/robots/default/java-agent/status", async () => readJavaAgentStatus());
  app.post("/api/robots/default/java-agent/start", async () => {
    javaStatusCache = null;
    startupStateCache = null;
    return startJavaAgentAndRebindRobotVoice();
  });
  app.post("/api/robots/default/java-agent/stop", async () => {
    javaStatusCache = null;
    startupStateCache = null;
    return stopJavaAgent(getConfig());
  });
  app.post("/api/robots/default/robot-voice/restart", async () => restartRobotVoiceStack(getConfig()));
  app.get("/api/robots/default/tunnel/status", async () => {
    const runtimeRobotHost = loadRuntimeRobotHost() || process.env.ROBOT_TUNNEL_TARGET_HOST || getConfig().robot.host;
    return getTunnelDiagnostics(getConfig(), runtimeRobotHost);
  });
  app.get("/api/robots/default/system/health", async () => getRobotSystemHealth(getConfig()));
  app.get("/api/robots/default/mapping/status", async () => ({
    session: getMappingSession(),
    health: await getMappingHealth(getConfig())
  }));
  app.get("/api/robots/default/mapping/preview", async () => getMappingPreview(getConfig()));
  app.post("/api/robots/default/mapping/start", async (_request, reply) => {
    try {
      return await startMapping(getConfig());
    } catch (error) {
      return reply.code(409).send({ ok: false, message: error instanceof Error ? error.message : String(error) });
    }
  });
  app.post("/api/robots/default/mapping/stop-save", async (request, reply) => {
    const parsed = mappingSaveSchema.safeParse(request.body ?? {});
    if (!parsed.success) return reply.code(400).send({ ok: false, message: "请填写有效的地图名称" });
    try {
      return await stopAndSaveMapping(getConfig(), parsed.data.map_name);
    } catch (error) {
      return reply.code(409).send({ ok: false, message: error instanceof Error ? error.message : String(error) });
    }
  });
  app.post("/api/robots/default/system/restart", async () => restartRobotSystem(getConfig()));
  app.post("/api/robots/default/system/recover-localization", async () => recoverRobotLocalization(getConfig()));

  app.get("/api/robots/default/maps", async (request) => {
    const query = request.query as { refresh?: string };
    return getRobot().maps(query.refresh === "1");
  });
  app.get("/api/robots/default/linkcraft/resources", async (request) => {
    const query = request.query as { refresh?: string };
    if (getConfig().deploymentMode === "robot") {
      let resources;
      try {
        resources = await discoverLocalLinkcraftResources();
      } catch (error) {
        const code = (error as NodeJS.ErrnoException)?.code;
        if (code !== "EACCES" && code !== "EPERM") throw error;
        resources = await discoverLinkcraftResourcesAsRobotUser();
      }
      return { ok: true, resource_count: resources.length, resources };
    }
    return getRobot().linkcraftResources(query.refresh === "1");
  });
  app.get("/api/robots/default/maps/:mapName", async (request) => {
    const params = request.params as { mapName: string };
    const query = request.query as { refresh?: string };
    return getRobot().map(params.mapName, query.refresh === "1");
  });
  app.get("/api/robots/default/maps/:mapName/image", async (request, reply) => {
    const params = request.params as { mapName: string };
    const image = await getRobot().mapImage(params.mapName);
    return reply.header("Cache-Control", "private, max-age=60, stale-while-revalidate=600")
      .type(image.contentType)
      .send(image.data);
  });
  app.post("/api/robots/default/maps/:mapName/points", async (request) => {
    const params = request.params as { mapName: string };
    const body = { ...(request.body as object), map_name: params.mapName };
    return getRobot().savePoint(body, params.mapName);
  });
  app.delete("/api/robots/default/maps/:mapName/points/:pointId", async (request) => {
    const params = request.params as { mapName: string; pointId: string };
    return getRobot().deletePoint(params.pointId, params.mapName);
  });
  app.post("/api/robots/default/maps/:mapName/task", async (request, reply) => {
    const params = request.params as { mapName: string };
    const parsed = taskSchema.safeParse(request.body ?? {});
    if (!parsed.success) {
      const linkcraftInvalid = parsed.error.issues.some((issue) => issue.path.includes("linkcraft_action"));
      return reply.code(400).send({
        ok: false,
        message: linkcraftInvalid
          ? "讲解任务中的灵创资源配置不正确"
          : "讲解任务中的点位、动作或表情配置不正确"
      });
    }
    const mapId = Number(params.mapName);
    return getRobot().saveTask({
      ...parsed.data,
      map_name: params.mapName,
      ...(Number.isFinite(mapId) && mapId > 0 ? { map_id: mapId } : {})
    }, params.mapName);
  });

  app.get("/api/robots/default/navigation/path-safety", async () => {
    const settings = await getRobot().pathSafety();
    return { ok: true, ...settings };
  });
  app.post("/api/robots/default/navigation/path-safety", async (request, reply) => {
    const parsed = pathSafetySchema.safeParse(request.body ?? {});
    if (!parsed.success) {
      return reply.code(400).send({
        ok: false,
        message: "机器人实测外接半径须大于 0 且不超过 2 米，额外障碍净空须在 0 到 2 米之间"
      });
    }
    const settings = await getRobot().savePathSafety(parsed.data);
    return { ok: true, ...settings };
  });

  app.get("/api/robots/default/relocalization/status", async () => getRobot().relocalizationStatus());
  app.post("/api/robots/default/relocalization/start", async (request, reply) => {
    const parsed = relocalizationSchema.safeParse(request.body ?? {});
    if (!parsed.success) return reply.code(400).send({ ok: false, message: parsed.error.message });
    return getRobot().startRelocalization(parsed.data);
  });
  app.post("/api/robots/default/bridge/:action", async (request, reply) => {
    const params = request.params as { action: string };
    if (!isBridgeAction(params.action)) {
      return reply.code(404).send({ ok: false, message: "未知的机器人服务操作" });
    }
    const result = await runBridgeAction(params.action, getConfig());
    return { ok: true, ...result };
  });

  app.setErrorHandler((error, _request, reply) => {
    const statusCode = typeof (error as { statusCode?: unknown }).statusCode === "number"
      ? (error as { statusCode: number }).statusCode
      : 500;
    const message = error instanceof Error ? error.message : String(error);
    reply.code(statusCode >= 400 && statusCode < 600 ? statusCode : 500).send({
      ok: false,
      message
    });
  });

  return app;
}

function localIpv4Addresses(): string[] {
  const addresses = new Set<string>();
  for (const entries of Object.values(os.networkInterfaces())) {
    for (const entry of entries || []) {
      if (entry.family === "IPv4" && !entry.internal) addresses.add(entry.address);
    }
  }
  return [...addresses];
}
