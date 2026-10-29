import cors from "@fastify/cors";
import staticFiles from "@fastify/static";
import websocket from "@fastify/websocket";
import Fastify, { type FastifyInstance, type FastifyRequest } from "fastify";
import { readFileSync } from "node:fs";
import { existsSync } from "node:fs";
import path from "node:path";
import { timingSafeEqual } from "node:crypto";
import type { TLSSocket } from "node:tls";
import { z } from "zod";
import type { MapPoint, RobotMap, RobotState, TourTask, VoiceDiagnostics, VoiceEvent, VoiceReply } from "../../shared/contracts";
import { extractBearer, pairingCodeHash, SessionManager, websocketBearer } from "./auth";
import { AimdkAdapter } from "./adapters/aimdk";
import { DemoAdapter } from "./adapters/demo";
import {
  aiSettingsSchema,
  pointSchema,
  tourSchema,
  type RobotAdapter,
  type RobotSnapshot,
} from "./domain";
import { JobManager, type SpeechOutput } from "./jobs";
import { Answerer } from "./voice/answerer";
import { VoiceRouter } from "./voice/router";
import { JsonStateStore } from "./store";
import { validatedLidarFrame } from "./lidar";
import { measureVadToFirstAudio, summarizeVoiceSamples } from "./voice/diagnostics";
import { SavedSlamMapCatalog } from "./slam-map-catalog";

type ServerOptions = {
  adapter?: RobotAdapter;
  lidarSource?: { getLidarFrame: NonNullable<RobotAdapter["getLidarFrame"]> };
  pairingCodeHash?: string;
  stateDirectory?: string;
  internalAgentToken?: string;
  tlsKey?: Buffer;
  tlsCert?: Buffer;
  staticDirectory?: string;
  demoMode?: boolean;
  agentSpeaker?: SpeechOutput;
  savedSlamDirectory?: string;
};

const emptySnapshot: RobotSnapshot = {
  connected: false,
  checks: [
    { id: "aimdk", label: "AIMDK", ready: false, detail: "未连接" },
    { id: "slam", label: "SLAM", ready: false, detail: "未连接" },
    { id: "lidar", label: "激光雷达", ready: false, detail: "未连接" },
    { id: "odom", label: "里程计", ready: false, detail: "未连接" },
    { id: "navigation", label: "原生导航", ready: false, detail: "未连接" },
    { id: "linksoul", label: "灵心", ready: false, detail: "未连接" },
  ],
  mapping: false,
  actionBusy: false,
  navigation: { status: "idle", detail: "机器人接口未连接" },
  motionPresets: [],
  expressions: [],
  linkcraftResources: [],
  updatedAt: new Date(0).toISOString(),
};

class HttpAgentSpeaker implements SpeechOutput {
  constructor(private readonly endpoint: string, private readonly token: string) {}

  async speak(text: string, correlationId: string): Promise<{ firstAudioMs: number }> {
    if (!this.token) throw new Error("灵心 AgentSDK 内部服务未配置");
    const response = await fetch(`${this.endpoint.replace(/\/+$/, "")}/v1/speak`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ text, correlationId, issuedAt: Date.now() }),
      signal: AbortSignal.timeout(125_000),
    });
    if (!response.ok) throw new Error(`灵心语音接口不可用 (${response.status})`);
    const result = await response.json() as { firstAudioMs?: number; completed?: boolean };
    if (!result.completed || !Number.isFinite(result.firstAudioMs)) throw new Error("语音播放没有完成确认");
    return { firstAudioMs: Number(result.firstAudioMs) };
  }

  async cancel(correlationId: string) {
    await this.control("/v1/speech/cancel", { correlationId });
  }

  private async control(route: string, body: unknown) {
    const response = await fetch(`${this.endpoint.replace(/\/+$/, "")}${route}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(6500),
    });
    if (!response.ok || !(await response.json() as { confirmed?: boolean }).confirmed) {
      throw new Error("灵心效果或语音停止没有完成确认");
    }
  }
}

export async function buildServer(options: ServerOptions = {}): Promise<FastifyInstance> {
  const demoMode = options.demoMode ?? process.env.LINGXI_DEMO_MODE === "1";
  const adapter = options.adapter ?? (demoMode ? new DemoAdapter() : new AimdkAdapter(process.env.AIMDK_BRIDGE_URL));
  const savedSlamDirectory = options.savedSlamDirectory ?? process.env.LINGXI_SAVED_SLAM_DIR;
  const slamCatalog = savedSlamDirectory ? new SavedSlamMapCatalog(savedSlamDirectory) : undefined;
  const store = new JsonStateStore(options.stateDirectory ?? process.env.LINGXI_DATA_DIR ?? path.resolve("var/lib/lingxi-next"));
  const hash = options.pairingCodeHash ?? process.env.LINGXI_PAIRING_CODE_SHA256 ??
    (demoMode ? pairingCodeHash(process.env.LINGXI_DEMO_PAIRING_CODE ?? "lingxi-dev-only") : "");
  if (!hash) throw new Error("LINGXI_PAIRING_CODE_SHA256 is required");
  const sessions = new SessionManager(hash);
  const agentToken = options.internalAgentToken ?? process.env.LINGXI_AGENT_INTERNAL_TOKEN ?? "";
  const speaker = options.agentSpeaker ?? (demoMode
    ? {
        speak: async () => ({ firstAudioMs: 80 }),
        cancel: async () => undefined,
      }
    : new HttpAgentSpeaker(process.env.LINGXI_AGENT_URL ?? "http://127.0.0.1:18180", agentToken));
  const jobs = new JobManager(adapter, store, speaker);
  const answerer = new Answerer(store, adapter);
  const voiceRouter = new VoiceRouter(Number(process.env.LINGXI_ASR_CONFIDENCE_THRESHOLD ?? "0.78"));
  const events = new Set<{ send(data: string): void; close(): void }>();
  const lidarClients = new Set<{ send(data: string): void; close(): void; bufferedAmount: number }>();
  const voiceSamples: NonNullable<VoiceDiagnostics["latest"]>[] = [];
  type PendingReply = { cancellation: AbortController; speechId: string; stop?: Promise<void> };
  const agentReplies = new Map<string, PendingReply>();
  const stoppingReplies = new Set<PendingReply>();
  let wakeReady: { sessionId: string; ready: Promise<unknown> } | undefined;

  function cancelReply(pending: PendingReply): Promise<void> {
    pending.cancellation.abort();
    stoppingReplies.add(pending);
    if (!pending.stop) {
      pending.stop = Promise.resolve().then(() => speaker.cancel?.(pending.speechId)).then(() => {
        stoppingReplies.delete(pending);
      }, (error) => {
        pending.stop = undefined; // A later interaction can retry the unconfirmed stop.
        throw error;
      });
      void pending.stop.catch(() => undefined);
    }
    return pending.stop;
  }

  function awaitStops(): Promise<void[]> {
    return Promise.all([...stoppingReplies].map(cancelReply));
  }
  const pairingAttempts = new Map<string, { began: number; count: number }>();
  const secureOptions = options.tlsKey && options.tlsCert
    ? { https: { key: options.tlsKey, cert: options.tlsCert } }
    : {};
  const app = Fastify({ ...secureOptions, logger: false, trustProxy: false, bodyLimit: 128 * 1024 });

  app.setErrorHandler((error, _request, reply) => {
    const status = Number((error as { statusCode?: unknown }).statusCode ?? 500);
    reply.code(status >= 400 && status < 600 ? status : 500).send({
      message: status < 500 && error instanceof Error ? error.message : "机器人服务内部错误",
    });
  });

  await app.register(cors, {
    delegator(request, callback) {
      const origin = request.headers.origin;
      const sameOrigin = `${request.protocol}://${request.headers.host}`;
      const allowed = !origin || origin === sameOrigin || origin === "https://localhost" ||
        origin === "capacitor://localhost" ||
        (demoMode && /^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin));
      if (!allowed) return callback(Object.assign(new Error("跨域来源未获准"), { statusCode: 403 }), {});
      callback(null, {
        origin: origin || false,
        credentials: false,
        methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allowedHeaders: ["Content-Type", "Authorization", "X-Agent-Token", "Sec-WebSocket-Protocol"],
      });
    },
  });
  await app.register(websocket);

  if (demoMode) await seedDemoData(store);

  function secureRequest(request: FastifyRequest): boolean {
    if ((request.raw.socket as TLSSocket).encrypted === true) return true;
    if (!demoMode) return false;
    return ["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(request.ip);
  }

  function deny(status: number, message: string): Error & { statusCode: number } {
    return Object.assign(new Error(message), { statusCode: status });
  }

  function requireSession(request: FastifyRequest): void {
    const token = extractBearer(request.headers.authorization);
    if (!sessions.isValid(token)) throw deny(401, "会话已失效，请重新配对");
  }

  app.addHook("preHandler", async (request) => {
    if (!request.url.startsWith("/api/v1/")) return;
    if (request.url.startsWith("/api/v1/session/pair")) return;
    if (request.url.startsWith("/api/v1/agent/events")) return;
    if (request.url.startsWith("/api/v1/events")) {
      const token = websocketBearer(request.headers["sec-websocket-protocol"]);
      if (!sessions.isValid(token)) throw deny(401, "WebSocket 会话无效");
      return;
    }
    if (request.url.split("?")[0] === "/api/v1/lidar/events") {
      if (!sessions.isValid(websocketBearer(request.headers["sec-websocket-protocol"]))) throw deny(401, "雷达会话无效");
      return;
    }
    requireSession(request);
  });

  async function runtimeSnapshot(): Promise<RobotSnapshot> {
    try {
      const snapshot = await adapter.getSnapshot();
      if (demoMode) return snapshot;
      const agent = await fetch(`${process.env.LINGXI_AGENT_URL ?? "http://127.0.0.1:18180"}/health`, {
        headers: { Authorization: `Bearer ${agentToken}` }, signal: AbortSignal.timeout(500),
      }).then(async (response) => response.ok ? response.json() : null).catch(() => null) as {
        online?: boolean; voiceGate?: string;
      } | null;
      snapshot.checks = snapshot.checks.map((check) => check.id === "linksoul" ? {
        ...check, ready: agent?.online === true && agent.voiceGate === "ready",
        detail: !agent?.online ? "AgentSDK 未连接" : agent.voiceGate === "asr_confidence_unavailable"
          ? "ASR 未提供置信度，语音路由已拒绝" : agent.voiceGate === "asr_confidence_invalid"
            ? "ASR 置信度格式或范围无效，语音路由已拒绝" : agent.voiceGate === "asr_payload_invalid"
              ? "ASR 内容格式无效，语音路由已拒绝" : agent.voiceGate === "tts_completion_unconfirmed"
                ? "语音结束未确认，后续播放已阻塞" : agent.voiceGate === "ready" ? "真实会话已验证" : "等待真实唤醒状态",
      } : check);
      return snapshot;
    } catch (error) {
      return {
        ...emptySnapshot,
        checks: emptySnapshot.checks.map((item) => ({
          ...item,
          detail: item.id === "aimdk" ? (error as Error).message : "等待 AIMDK 适配器",
        })),
        updatedAt: new Date().toISOString(),
      };
    }
  }

  async function stateResponse(): Promise<RobotState> {
    let [db, snapshot, discovered, savedSlam] = await Promise.all([
      store.read(),
      runtimeSnapshot(),
      adapter.listMaps().catch(() => []),
      slamCatalog ? slamCatalog.list().then((maps) => ({ maps, status: "ready" as const }))
        .catch(() => ({ maps: [], status: "unavailable" as const })) : undefined,
    ]);
    // Register native map references on first use, so an already active AIMDK
    // map can receive points without forcing an unnecessary relocalization.
    const newMaps = discovered.filter((map) => !db.maps.some((saved) => saved.id === map.id));
    if (newMaps.length || (!db.activeMapId && discovered.some((map) => map.active))) {
      db = await store.update((current) => ({
        ...current,
        maps: [...current.maps, ...newMaps.filter((map) => !current.maps.some((saved) => saved.id === map.id))],
        activeMapId: current.activeMapId ?? discovered.find((map) => map.active)?.id,
      }));
    }
    const byId = new Map<string, RobotMap>();
    for (const map of db.maps) byId.set(map.id, map);
    for (const map of discovered) {
      const saved = byId.get(map.id);
      byId.set(map.id, { ...saved, ...map, mapId: map.mapId ?? saved?.mapId });
    }
    const selected = db.activeMapId ?? [...byId.values()].find((map) => map.active)?.id;
    const maps = [...byId.values()].map((map) => ({
      ...map,
      active: map.id === selected,
    }));
    const jobState = jobs.state();
    const navigation = jobs.hasActiveJob() || snapshot.navigation.status === "idle" ? jobState : snapshot.navigation;
    return {
      ...snapshot,
      robotName: db.robotName,
      demoMode: adapter.demoMode,
      maps,
      ...(savedSlam ? { savedSlamMaps: savedSlam.maps, savedSlamCatalogStatus: savedSlam.status } : {}),
      points: db.points,
      tours: db.tours,
      activeMapId: selected,
      selectedTourId: db.selectedTourId,
      idleReturnSeconds: db.idleReturnSeconds,
      navigation,
      voiceDiagnostics: summarizeVoiceDiagnostics(),
      updatedAt: new Date().toISOString(),
    };
  }

  function summarizeVoiceDiagnostics(): VoiceDiagnostics {
    return summarizeVoiceSamples(voiceSamples);
  }

  function emitState(state: RobotState) {
    const payload = JSON.stringify({ type: "state", state });
    for (const client of events) {
      try { client.send(payload); } catch { events.delete(client); }
    }
  }

  app.get("/health", async () => ({ ok: true, service: "lingxi-next", demoMode: adapter.demoMode }));
  app.post<{ Body: { pairingCode: string } }>("/api/v1/session/pair", async (request, reply) => {
    if (!secureRequest(request)) throw deny(426, "配对必须通过 HTTPS");
    const now = Date.now();
    for (const [ip, attempt] of pairingAttempts) {
      if (now - attempt.began > 60_000) pairingAttempts.delete(ip);
    }
    const attempt = pairingAttempts.get(request.ip) ?? { began: now, count: 0 };
    if (attempt.count >= 8 || pairingAttempts.size >= 1000) throw deny(429, "配对尝试过多，请一分钟后重试");
    attempt.count += 1;
    pairingAttempts.set(request.ip, attempt);
    const token = sessions.pair(request.body?.pairingCode ?? "");
    if (!token) throw deny(401, "配对码错误");
    return reply.code(201).send({ token, expiresInSeconds: 8 * 60 * 60, demoMode: adapter.demoMode });
  });
  app.post("/api/v1/session/revoke", async (request, reply) => {
    sessions.revoke(extractBearer(request.headers.authorization));
    return reply.code(204).send();
  });
  app.get("/api/v1/state", async () => stateResponse());
  app.get("/api/v1/jobs/history", async () => ({ records: (await store.read()).jobRecords }));
  app.get("/api/v1/settings", async () => {
    const settings = await store.publicAiSettings();
    return { ...settings, firstAudioTargetMs: 2000 };
  });
  app.put("/api/v1/settings/ai", async (request) => {
    if (!secureRequest(request)) throw deny(426, "凭据配置必须通过 HTTPS");
    const parsed = aiSettingsSchema.safeParse(request.body);
    if (!parsed.success) throw deny(400, parsed.error.issues[0]?.message ?? "模型设置无效");
    await store.writeAiSettings(parsed.data);
    return { saved: true };
  });
  app.put("/api/v1/settings/credentials", async (request) => {
    if (!secureRequest(request)) throw deny(426, "凭据配置必须通过 HTTPS");
    const parsed = z.object({
      apiKey: z.string().max(4096).optional(),
      linksoulAppId: z.string().max(500).optional(),
      linksoulAppKey: z.string().max(2000).optional(),
      linksoulAppSecret: z.string().max(4000).optional(),
      agentCode: z.string().max(500).optional(),
    }).strict().safeParse(request.body);
    if (!parsed.success) throw deny(400, "凭据字段无效");
    const update = Object.fromEntries(Object.entries(parsed.data).filter(([, value]) => value !== undefined)) as Record<string, string>;
    const current = await store.readCredentials();
    const next = { ...current, ...update };
    const touchesRetriever = ["linksoulAppId", "linksoulAppKey", "linksoulAppSecret", "agentCode"].some((key) => key in update);
    if (touchesRetriever && ![next.linksoulAppId, next.linksoulAppKey, next.linksoulAppSecret, next.agentCode].every(Boolean)) {
      throw deny(400, "Agent Retriever 配置需要同时提供 App ID、App Key、App Secret 和 Agent Code");
    }
    await store.writeCredentials(update);
    return { saved: true };
  });

  app.get("/api/v1/maps", async () => ({ maps: (await stateResponse()).maps }));
  app.get("/api/v1/slam/maps", async () => {
    if (!slamCatalog) throw deny(503, "独立 SLAM 地图目录尚未配置");
    return { maps: await slamCatalog.list() };
  });
  app.get<{ Params: { id: string } }>("/api/v1/slam/maps/:id/points", async (request) => {
    if (!slamCatalog) throw deny(503, "独立 SLAM 地图目录尚未配置");
    return slamCatalog.preview(request.params.id);
  });
  app.post<{ Params: { id: string }; Body?: { initialPose?: { x: number; y: number; yaw: number } } }>(
    "/api/v1/maps/:id/activate",
    async (request) => {
      const snapshot = await runtimeSnapshot();
      if (snapshot.actionBusy) throw deny(409, "机器人动作状态未解除，请先检查动作服务状态");
      if (jobs.hasActiveJob() || snapshot.mapping || ["requested", "running", "paused"].includes(snapshot.navigation.status)) {
        throw deny(409, "请先停止当前建图或导航任务，再切换地图");
      }
      const parsedPose = z.object({
        initialPose: z.object({
          x: z.number().finite().min(-1000).max(1000),
          y: z.number().finite().min(-1000).max(1000),
          yaw: z.number().finite().min(-Math.PI * 2).max(Math.PI * 2),
        }).strict().optional(),
      }).strict().safeParse(request.body ?? {});
      if (!parsedPose.success) throw deny(400, "初始位姿参数无效");
      const state = await store.read();
      const maps = await adapter.listMaps().catch(() => []);
      const map = maps.find((item) => item.id === request.params.id) ?? state.maps.find((item) => item.id === request.params.id);
      if (!map) throw deny(404, "地图不存在或 AIMDK 未确认其可用");
      await adapter.activateMap(map, parsedPose.data.initialPose);
      await store.putMap(map);
      await store.setActiveMap(map.id);
      const response = await stateResponse();
      emitState(response);
      return { accepted: true, mapId: map.mapId };
    },
  );
  app.get<{ Params: { id: string } }>("/api/v1/maps/:id/grid", async (request) => {
    const method = adapter.getStoredMapGrid;
    if (!method) throw deny(501, "地图栅格读取尚未配置");
    const maps = await adapter.listMaps();
    const map = maps.find((item) => item.id === request.params.id);
    if (!map) throw deny(404, "地图未通过 AIMDK 确认");
    return method.call(adapter, map);
  });
  app.post("/api/v1/mapping/start", async (_request, reply) => {
    const state = await stateResponse();
    if (state.actionBusy) throw deny(409, "机器人动作状态未解除，请先检查动作服务状态");
    if (jobs.hasActiveJob() || ["requested", "running", "paused"].includes(state.navigation.status)) {
      throw deny(409, "请先停止导航或讲解任务，再开始建图");
    }
    const required = new Set(["aimdk", "slam", "lidar"]);
    const failures = state.checks.filter((item) => required.has(item.id) && !item.ready);
    if (failures.length) throw deny(409, `建图预检未通过：${failures.map((item) => item.label).join("、")}`);
    if (state.mapping) throw deny(409, "建图已在进行");
    await adapter.startMapping();
    emitState(await stateResponse());
    return reply.code(202).send({ accepted: true });
  });
  app.post<{ Body: { name: string } }>("/api/v1/mapping/stop-save", async (request) => {
    const name = request.body?.name?.trim();
    if (!name || name.length > 64) throw deny(400, "地图名称长度必须为 1–64 个字符");
    const map = await adapter.stopAndSaveMap(name);
    await store.putMap(map);
    await store.setActiveMap(map.id);
    emitState(await stateResponse());
    return { map, confirmed: true };
  });

  app.post<{ Body: unknown }>("/api/v1/points", async (request, reply) => {
    const parsed = pointSchema.omit({ id: true }).safeParse(request.body);
    if (!parsed.success) throw deny(400, "点位数据无效");
    const state = await store.read();
    if (!state.maps.some((map) => map.id === parsed.data.mapId)) throw deny(404, "地图不存在");
    const point = await store.addPoint(parsed.data);
    emitState(await stateResponse());
    return reply.code(201).send({ point });
  });
  app.put<{ Params: { id: string }; Body: unknown }>("/api/v1/points/:id", async (request) => {
    const parsed = pointSchema.safeParse({ ...(request.body as object), id: request.params.id });
    if (!parsed.success) throw deny(400, "点位数据无效");
    const existing = (await store.read()).points.find((point) => point.id === request.params.id);
    if (!existing) throw deny(404, "点位不存在");
    if (existing.mapId !== parsed.data.mapId) throw deny(400, "点位不能跨地图移动，请在目标地图新建点位");
    await store.updatePoint(request.params.id, parsed.data as MapPoint);
    emitState(await stateResponse());
    return { saved: true };
  });
  app.delete<{ Params: { id: string } }>("/api/v1/points/:id", async (request, reply) => {
    const existing = (await store.read()).points.find((point) => point.id === request.params.id);
    if (!existing) throw deny(404, "点位不存在");
    await store.deletePoint(request.params.id);
    emitState(await stateResponse());
    return reply.code(204).send();
  });

  app.post<{ Body: unknown }>("/api/v1/tours", async (request, reply) => {
    const parsed = tourSchema.safeParse(request.body);
    if (!parsed.success) throw deny(400, "讲解任务数据无效");
    await validateTour(parsed.data, store);
    await store.saveTour(parsed.data as TourTask);
    await store.selectTour(parsed.data.id);
    emitState(await stateResponse());
    return reply.code(201).send({ saved: true });
  });
  app.put<{ Params: { id: string }; Body: unknown }>("/api/v1/tours/:id", async (request) => {
    const parsed = tourSchema.safeParse({ ...(request.body as object), id: request.params.id, updatedAt: new Date().toISOString() });
    if (!parsed.success) throw deny(400, "讲解任务数据无效");
    await validateTour(parsed.data, store);
    await store.saveTour(parsed.data as TourTask);
    emitState(await stateResponse());
    return { saved: true };
  });
  app.delete<{ Params: { id: string } }>("/api/v1/tours/:id", async (request, reply) => {
    const existing = (await store.read()).tours.some((tour) => tour.id === request.params.id);
    if (!existing) throw deny(404, "讲解任务不存在");
    await store.deleteTour(request.params.id);
    emitState(await stateResponse());
    return reply.code(204).send();
  });
  app.post<{ Body: { taskId: string } }>("/api/v1/tours/select", async (request) => {
    const exists = (await store.read()).tours.some((tour) => tour.id === request.body?.taskId);
    if (!exists) throw deny(404, "讲解任务不存在");
    await store.selectTour(request.body.taskId);
    emitState(await stateResponse());
    return { selected: true };
  });
  app.post<{ Params: { id: string } }>("/api/v1/tours/:id/start", async (request, reply) => {
    const tour = (await store.read()).tours.find((item) => item.id === request.params.id);
    if (!tour) throw deny(404, "讲解任务不存在");
    await store.selectTour(tour.id);
    await jobs.startTour(tour);
    emitState(await stateResponse());
    return reply.code(202).send({ accepted: true });
  });

  app.post<{ Body: { pointId: string } }>("/api/v1/navigation/point", async (request, reply) => {
    const state = await stateResponse();
    if (state.actionBusy) throw deny(409, "机器人动作状态未解除，请先检查动作服务状态");
    const point = state.points.find((item) => item.id === request.body?.pointId);
    if (!point) throw deny(404, "点位不存在");
    if (state.activeMapId !== point.mapId) throw deny(409, "点位不属于当前活动地图");
    const ready = state.checks.find((item) => item.id === "navigation")?.ready;
    if (!ready) throw deny(409, "X2 原生导航组件尚未就绪");
    await jobs.startPoint(point);
    emitState(await stateResponse());
    return reply.code(202).send({ accepted: true });
  });
  app.post("/api/v1/actions/confirm-idle", async (request, reply) => {
    const body = z.object({ confirmed: z.literal(true) }).strict().safeParse(request.body);
    if (!body.success) throw deny(400, "必须明确确认机器人动作已停止");
    await adapter.confirmActionsIdle();
    emitState(await stateResponse());
    return reply.send({ confirmed: true, actionBusy: false });
  });
  app.post<{ Body: { action: "pause" | "resume" | "stop" } }>("/api/v1/navigation/control", async (request) => {
    const action = request.body?.action;
    if (!["pause", "resume", "stop"].includes(action)) throw deny(400, "导航控制命令无效");
    await jobs.control(action);
    emitState(await stateResponse());
    return { accepted: true };
  });
  app.put<{ Body: { idleReturnSeconds: number } }>("/api/v1/settings/navigation", async (request) => {
    const seconds = request.body?.idleReturnSeconds;
    if (!Number.isInteger(seconds) || seconds < 0 || seconds > 300) throw deny(400, "返航等待时间必须是 0–300 秒");
    await store.update((current) => ({ ...current, idleReturnSeconds: seconds }));
    return { saved: true };
  });

  app.post<{ Body: VoiceEvent }>("/api/v1/agent/events", async (request, reply) => {
    if (!isLoopback(request.ip)) throw deny(403, "Agent 回调仅允许机器人本机");
    if (!constantEqual(request.headers["x-agent-token"]?.toString() ?? "", agentToken)) throw deny(401, "Agent 回调鉴权失败");
    if (!agentToken) throw deny(503, "Agent 回调令牌未配置");
    const common = {
      eventId: z.string().min(1).max(180),
      sessionId: z.string().min(1).max(180),
      occurredAt: z.number().finite(),
    };
    const parsed = z.discriminatedUnion("kind", [
      z.object({ ...common, kind: z.literal("wake") }),
      z.object({ ...common, kind: z.literal("session-ended") }),
      z.object({
        ...common,
        kind: z.literal("recognition"),
        transcript: z.string().max(4000),
        confidence: z.number().min(0).max(1),
        isFinal: z.boolean(),
        vadEndedAt: z.number().int().positive().optional(),
      }),
    ]).safeParse(request.body);
    if (!parsed.success) throw deny(400, "语音事件格式无效");
    const event = parsed.data;
    const requestStartedAt = performance.now();
    const requestStartedWallAt = Date.now();
    if (!voiceRouter.admit(event)) return reply.code(204).send();
    if (event.kind === "wake") {
      for (const pending of agentReplies.values()) void cancelReply(pending);
      agentReplies.clear();
      const ready = Promise.all([awaitStops(), jobs.interruptTour()]);
      wakeReady = { sessionId: event.sessionId, ready };
      await ready;
      return reply.code(204).send();
    }
    if (event.kind === "session-ended") {
      const pending = agentReplies.get(event.sessionId);
      if (pending) {
        const stopped = cancelReply(pending);
        agentReplies.delete(event.sessionId);
        await stopped;
      }
      return reply.code(204).send();
    }
    const previous = agentReplies.get(event.sessionId);
    if (previous) void cancelReply(previous);
    const ready = Promise.all([
      wakeReady?.sessionId === event.sessionId ? wakeReady.ready : undefined,
      awaitStops(),
    ]);
    void ready.catch(() => undefined);
    const cancellation = new AbortController();
    const pendingReply: PendingReply = { cancellation, speechId: `reply:${event.sessionId}:${event.eventId}` };
    // Reserve ownership before the first await so a newer turn can invalidate it.
    agentReplies.set(event.sessionId, pendingReply);
    const authorized = () => !cancellation.signal.aborted && voiceRouter.isCurrent(event);
    const current = () => authorized() && agentReplies.get(event.sessionId) === pendingReply;
    jobs.markInteraction();
    jobs.beginDialogue();
    let text = "";
    let category: VoiceReply["category"] = "rejected";
    let accepted = false;
    let speechDelivered = false;
    let dispatchCompleted = false;
    let deliveryError: string | undefined;
    let firstAudioMs: number | undefined;
    let vadToFirstAudioMs: number | undefined;
    const speakSegment = async (segment: string) => {
      if (!current()) return;
      await ready;
      if (!current()) return;
      const sentAt = performance.now();
      const spoken = await speaker.speak(segment, pendingReply.speechId);
      if (!speechDelivered) {
        firstAudioMs = Math.round(sentAt - requestStartedAt + spoken.firstAudioMs);
        if (event.kind === "recognition") {
          vadToFirstAudioMs = measureVadToFirstAudio(event.vadEndedAt, requestStartedWallAt, firstAudioMs);
        }
      }
      speechDelivered = true;
    };
    try {
      await ready;
      if (!current()) return reply.code(204).send();
      const db = await store.read();
      const snapshot = await runtimeSnapshot();
      if (!current()) return reply.code(204).send();
      const routed = voiceRouter.resolve(event, {
        points: db.points.filter((point) => point.mapId === db.activeMapId),
        tours: db.tours, selectedTourId: db.selectedTourId, motions: snapshot.motionPresets,
      });
      if (routed.intent.type === "ignore") return reply.code(204).send();
      ({ text, category, accepted } = routed.reply);
      if (snapshot.actionBusy && ["navigate", "tour-start", "motion"].includes(routed.intent.type)) {
        throw new Error("机器人动作状态未解除，请先检查动作服务状态");
      }
      switch (routed.intent.type) {
        case "navigate":
          await jobs.startPoint(routed.intent.point, authorized);
          break;
        case "tour-start":
          await jobs.startTour(routed.intent.tour, authorized);
          break;
        case "tour-pause":
        case "tour-resume":
        case "tour-stop":
          await jobs.control(routed.intent.type === "tour-pause" ? "pause" : routed.intent.type === "tour-resume" ? "resume" : "stop", authorized);
          break;
        case "motion":
          if (snapshot.actionBusy || ["requested", "running", "paused"].includes(snapshot.navigation.status)) {
            throw new Error("任务执行期间不接受预设动作");
          }
          await jobs.runAllowlistedMotion(routed.intent.name, authorized);
          break;
        case "question": {
          let playback = Promise.resolve();
          const answer = await answerer.streamAnswer(routed.intent.text, (segment) => {
            playback = playback.then(() => speakSegment(segment));
            // Observe rejection immediately while the model may still stream.
            void playback.catch(() => { deliveryError = "语音输出失败"; });
          }, cancellation.signal);
          await playback;
          if (!current()) return reply.code(204).send();
          text = answer.text;
          category = answer.source === "refusal" ? "rejected" : "question";
          accepted = answer.source !== "refusal";
          break;
        }
        default:
          break;
      }
      dispatchCompleted = true;
      if (routed.intent.type !== "question") await speakSegment(text);
      if (!current()) return reply.code(204).send();
    } catch (error) {
      if (!current()) return reply.code(204).send();
      if (dispatchCompleted || deliveryError) {
        deliveryError = "语音输出失败，请在管理页面核对任务状态";
      } else {
        text = "请求未完成确认，请在管理页面核对状态后操作。";
        category = "rejected";
        accepted = false;
      }
    } finally {
      jobs.endDialogue();
      if (agentReplies.get(event.sessionId) === pendingReply) agentReplies.delete(event.sessionId);
    }
    const result: VoiceReply = {
      accepted,
      category,
      text,
      commandId: event.eventId,
      latencyMs: Math.round(performance.now() - requestStartedAt),
      speechDelivered,
      firstAudioMs,
      vadToFirstAudioMs,
      deliveryError,
    };
    voiceSamples.push({
      at: new Date().toISOString(),
      category: result.category,
      accepted: result.accepted,
      speechDelivered: result.speechDelivered ?? false,
      latencyMs: result.latencyMs,
      firstAudioMs: result.firstAudioMs,
      vadToFirstAudioMs: result.vadToFirstAudioMs,
    });
    if (voiceSamples.length > 100) voiceSamples.splice(0, voiceSamples.length - 100);
    return reply.code(200).send(result);
  });

  const protocolToken = (request: FastifyRequest) => websocketBearer(request.headers["sec-websocket-protocol"]);
  app.get("/api/v1/lidar/events", {
    websocket: true,
    preValidation: async (request) => {
      if (!sessions.isValid(protocolToken(request))) throw deny(401, "雷达会话无效");
    },
  }, (socket, request) => {
    lidarClients.add(socket);
    const expiryCheck = setInterval(() => {
      if (!sessions.isValid(protocolToken(request))) socket.close(1008, "Session expired");
    }, 1000);
    socket.on("close", () => { clearInterval(expiryCheck); lidarClients.delete(socket); });
  });
  app.get("/api/v1/events", {
    websocket: true,
    preValidation: async (request) => {
      if (!sessions.isValid(protocolToken(request))) throw deny(401, "WebSocket 会话无效");
    },
  }, (socket, _request) => {
    const client = {
      send(data: string) { socket.send(data); },
      close() { socket.close(); },
    };
    events.add(client);
    const expiryCheck = setInterval(() => {
      if (!sessions.isValid(protocolToken(_request))) socket.close(1008, "Session expired");
    }, 1000);
    socket.on("close", () => { clearInterval(expiryCheck); events.delete(client); });
    void stateResponse().then((state) => client.send(JSON.stringify({ type: "state", state }))).catch(() => undefined);
  });

  const webRoot = options.staticDirectory ?? process.env.LINGXI_WEB_ROOT ?? path.resolve("dist");
  if (existsSync(webRoot)) {
    await app.register(staticFiles, { root: webRoot, prefix: "/" });
    app.setNotFoundHandler((request, reply) => {
      if (request.method === "GET" && !request.url.startsWith("/api/")) return reply.sendFile("index.html", webRoot);
      return reply.code(404).send({ message: "接口不存在" });
    });
  } else {
    app.setNotFoundHandler((_request, reply) => reply.code(404).send({ message: "接口不存在" }));
  }

  const timer = setInterval(() => {
    if (!events.size) return;
    void stateResponse().then(emitState).catch(() => undefined);
  }, 1500);
  let lidarPending = false;
  const lidarTimer = setInterval(async () => {
    if (!lidarClients.size || lidarPending) return;
    lidarPending = true;
    let payload: string;
    try {
      const source = options.lidarSource ?? adapter;
      if (!source.getLidarFrame) throw new Error("雷达数据接口尚未配置");
      payload = JSON.stringify({ type: "lidar", frame: validatedLidarFrame(await source.getLidarFrame()) });
    } catch {
      payload = JSON.stringify({ type: "lidar-unavailable", message: "没有新雷达数据，请检查雷达话题与桥接连接" });
    } finally { lidarPending = false; }
    for (const client of lidarClients) {
      // Keep only current frames when a slow client cannot drain its socket.
      if (client.bufferedAmount > 512 * 1024) continue;
      try { client.send(payload); } catch { lidarClients.delete(client); }
    }
  }, 200);
  app.addHook("onClose", async () => {
    clearInterval(timer);
    clearInterval(lidarTimer);
    for (const client of lidarClients) client.close();
    lidarClients.clear();
    for (const event of events) event.close();
    events.clear();
    for (const pending of agentReplies.values()) void cancelReply(pending);
    await awaitStops().catch(() => undefined);
    await jobs.dispose().catch(() => undefined);
  });
  return app;
}

export async function startServer(): Promise<void> {
  const isDemo = process.env.LINGXI_DEMO_MODE === "1";
  const keyPath = process.env.LINGXI_TLS_KEY;
  const certPath = process.env.LINGXI_TLS_CERT;
  if (!isDemo && (!keyPath || !certPath)) throw new Error("生产模式必须配置 LINGXI_TLS_KEY 和 LINGXI_TLS_CERT");
  const app = await buildServer({
    tlsKey: keyPath ? readFileSync(keyPath) : undefined,
    tlsCert: certPath ? readFileSync(certPath) : undefined,
  });
  const host = isDemo ? "127.0.0.1" : process.env.LINGXI_HOST ?? "0.0.0.0";
  const port = Number(process.env.LINGXI_PORT ?? (isDemo ? "3010" : "3000"));
  await app.listen({ host, port });
}

async function validateTour(tour: TourTask, store: JsonStateStore) {
  const state = await store.read();
  if (!state.maps.some((map) => map.id === tour.mapId)) throw Object.assign(new Error("讲解任务地图不存在"), { statusCode: 404 });
  const points = new Map(state.points.map((point) => [point.id, point]));
  for (const step of tour.steps) {
    const point = points.get(step.pointId);
    if (!point || point.mapId !== tour.mapId) throw Object.assign(new Error("讲解步骤点位无效或地图不匹配"), { statusCode: 400 });
  }
}

async function seedDemoData(store: JsonStateStore) {
  const state = await store.read();
  if (state.maps.length) return;
  const mapId = "demo-hall";
  const points: MapPoint[] = [
    { id: "point-1", mapId, name: "入口", x: 1.4, y: 1.2, yaw: 0, isStart: true },
    { id: "point-2", mapId, name: "灵犀展台", x: 3.4, y: 2.2, yaw: 1.1, isStart: false },
    { id: "point-3", mapId, name: "互动区", x: 5.8, y: 3.7, yaw: 2.7, isStart: false },
  ];
  const tour: TourTask = {
    id: "tour-1",
    name: "展厅标准讲解",
    mapId,
    repeatCount: 1,
    idleReturnSeconds: 30,
    updatedAt: "2026-10-25T14:12:00.000Z",
    steps: [
      { id: "step-1", pointId: "point-2", waitSeconds: 3, narration: "欢迎来到灵犀展台。", expression: "微笑" },
      { id: "step-2", pointId: "point-3", waitSeconds: 5, narration: "这里是互动体验区。", presetMotion: "挥手" },
    ],
  };
  await store.update((current) => ({
    ...current,
    maps: [
      { id: mapId, name: "一层展厅", mapId: 1001, active: true, createdAt: "2026-10-24" },
      { id: "demo-lab", name: "体验区", mapId: 1002, active: false, createdAt: "2026-10-21" },
    ],
    activeMapId: mapId,
    points,
    tours: [tour],
    selectedTourId: tour.id,
  }));
}

function isLoopback(address: string): boolean {
  return ["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(address);
}

function constantEqual(left: string, right: string): boolean {
  const supplied = Buffer.from(left);
  const expected = Buffer.from(right);
  if (!right || supplied.length !== expected.length) return false;
  return timingSafeEqual(supplied, expected);
}
