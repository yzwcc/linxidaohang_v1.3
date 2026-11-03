import { afterEach, describe, expect, it, vi } from "vitest";
import { DemoAdapter } from "../src/adapters/demo";
import { HttpActionsBackend, ProductActionsAdapter, ProductOperations } from "../src/actions-backend";

const endpoint = "http://127.0.0.1:18082/v1";
const token = "synthetic-private-action-token-not-a-secret";
const firstEpoch = "a".repeat(32), nextEpoch = "b".repeat(32);
const now = () => Date.now();
const state = (options: { epoch?: string; sequence?: number; busy?: boolean; observedAtMs?: number } = {}) => ({
  bridgeMode: "actions-only", physicalActionsEnabled: false, bridgeEpoch: options.epoch ?? firstEpoch,
  sequence: options.sequence ?? 1, observedAtMs: options.observedAtMs ?? now(), actionBusy: options.busy ?? false,
  actionStatus: options.busy ? "linkcraft-awaiting-operator" : null,
  motionPresets: [], linkcraftResources: [], expressions: ["开心", "平静"],
  actionServices: { presetMotion: true, expression: true, linkcraftCatalog: true,
    linkcraftExecution: true, linkcraftCatalogFresh: true },
});
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});

afterEach(() => vi.unstubAllGlobals());

describe("independent product actions bridge", () => {
  it("reads only a fresh actions-only service state and retains no physical capability", async () => {
    const fetcher = vi.fn(async () => json(state()));
    vi.stubGlobal("fetch", fetcher);
    const actions = new HttpActionsBackend(endpoint, token);
    const result = await actions.state();
    expect(result.bridgeMode).toBe("actions-only");
    expect(result.physicalActionsEnabled).toBe(false);
    expect(result.motionPresets).toEqual([]);
    expect(result.linkcraftResources).toEqual([]);
    expect(result.actionServices.presetMotion).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe(endpoint + "/state");
    expect(JSON.stringify(result)).not.toContain(token);
  });

  it("rejects unsupported, stale, reordered, and wrong-mode state before writes", async () => {
    for (const input of [state({ observedAtMs: now() - 2000 }), state({ sequence: 0 }),
      { ...state(), bridgeMode: "full" }, { ...state(), physicalActionsEnabled: true },
      { ...state(), motionPresets: ["举手"] }]) {
      const fetcher = vi.fn(async () => json(input));
      vi.stubGlobal("fetch", fetcher);
      const actions = new HttpActionsBackend(endpoint, token);
      await expect(actions.state()).rejects.toThrow();
      expect(fetcher).toHaveBeenCalledOnce();
    }
    const fetcher = vi.fn().mockResolvedValueOnce(json(state({ sequence: 4 })))
      .mockResolvedValueOnce(json(state({ sequence: 4 })));
    vi.stubGlobal("fetch", fetcher);
    const actions = new HttpActionsBackend(endpoint, token);
    await actions.state();
    await expect(actions.state()).rejects.toThrow("过期或乱序");
  });

  it("correlates expression dispatch with the same bridge epoch and a unique request ID", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(state({ sequence: 1 })))
      .mockImplementationOnce(async (_url, init) => {
        const body = JSON.parse(String(init?.body));
        return json({ accepted: true, completed: false, bridgeEpoch: firstEpoch, requestId: body.requestId });
      });
    vi.stubGlobal("fetch", fetcher);
    const actions = new HttpActionsBackend(endpoint, token);
    await actions.playExpression("开心");
    const [url, init] = fetcher.mock.calls[1];
    expect(url).toBe(endpoint + "/expression");
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ name: "开心", bridgeEpoch: firstEpoch });
    expect(body.requestId).toMatch(/^[a-f0-9]{32}$/);
  });

  it("keeps an uncertain dispatched expression interlocked across service restart until confirmation", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(state({ sequence: 1 })))
      .mockResolvedValueOnce(json({ message: "timeout", uncertain: true }, 504))
      .mockResolvedValueOnce(json(state({ epoch: nextEpoch, sequence: 1 })))
      .mockResolvedValueOnce(json(state({ epoch: nextEpoch, sequence: 2 })))
      .mockResolvedValueOnce(json(state({ epoch: nextEpoch, sequence: 3 })))
      .mockImplementationOnce(async (_url, init) => {
        const body = JSON.parse(String(init?.body));
        return json({ accepted: true, completed: false, bridgeEpoch: nextEpoch, requestId: body.requestId });
      });
    vi.stubGlobal("fetch", fetcher);
    const actions = new HttpActionsBackend(endpoint, token);
    await expect(actions.playExpression("开心")).rejects.toThrow("未确认");
    expect(await actions.state()).toMatchObject({ actionBusy: true, actionStatus: "uncertain", expressions: [] });
    await actions.confirmIdle();
    await actions.playExpression("平静");
    const posted = fetcher.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(posted).toHaveLength(2);
    expect(JSON.parse(String(posted[1][1]?.body)).bridgeEpoch).toBe(nextEpoch);
  });

  it("surfaces read unavailability as busy without inventing a dispatched-action interlock", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(json(state({ sequence: 1 })));
    vi.stubGlobal("fetch", fetcher);
    const actions = new HttpActionsBackend(endpoint, token);
    await expect(actions.state()).rejects.toThrow();
    expect(await actions.state()).toMatchObject({ actionBusy: false, expressions: ["开心", "平静"] });
  });
});

describe("product adapter operation ownership", () => {
  const actions = (overrides = {}) => ({ state: vi.fn(async () => state()),
    playExpression: vi.fn(async () => undefined), confirmIdle: vi.fn(async () => undefined), ...overrides });

  it("reports unknown action readiness as locked and never advertises physical motions", async () => {
    const backend = actions({ state: vi.fn(async () => { throw new Error("offline"); }) });
    const adapter = new ProductActionsAdapter(new DemoAdapter(), backend, new ProductOperations());
    const snapshot = await adapter.getSnapshot();
    expect(snapshot).toMatchObject({ actionBusy: true, actionStatus: "uncertain", motionPresets: [], expressions: [], linkcraftResources: [] });
    expect(snapshot.checks.find((check) => check.id === "actions")?.ready).toBe(false);
  });

  it("rejects entity actions and mapping while busy, but serializes safe local operations", async () => {
    const operations = new ProductOperations();
    const physical = new DemoAdapter();
    const start = vi.spyOn(physical, "startMapping");
    let release!: () => void;
    start.mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve; }));
    const backend = actions();
    const adapter = new ProductActionsAdapter(physical, backend, operations);
    const pending = adapter.startMapping();
    await vi.waitFor(() => expect(start).toHaveBeenCalledOnce());
    await expect(adapter.playExpression("开心")).rejects.toThrow("正在处理");
    await expect(adapter.startPresetMotion("举手")).rejects.toThrow("未准入");
    await expect(adapter.executeLinkcraftResource("f".repeat(32))).rejects.toThrow("未准入");
    release(); await pending;
    expect(backend.playExpression).not.toHaveBeenCalled();
  });

  it("allows generation-current tour expressions only and refuses stale callbacks", async () => {
    let current = true;
    const backend = actions();
    const adapter = new ProductActionsAdapter(new DemoAdapter(), backend, new ProductOperations());
    adapter.setJobBusy(() => true);
    await adapter.playExpression("开心", () => current);
    current = false;
    await expect(adapter.playExpression("开心", () => current)).rejects.toThrow("任务运行期间");
    expect(backend.playExpression).toHaveBeenCalledOnce();
  });

  it("refuses expressions while localization is starting, running, stopping, or unconfirmed", async () => {
    let localizationBusy = true;
    const backend = actions();
    const delegate = new DemoAdapter();
    const startMapping = vi.spyOn(delegate, "startMapping");
    const adapter = new ProductActionsAdapter(delegate, backend, new ProductOperations());
    adapter.setActivityBusy(async () => localizationBusy);
    await expect(adapter.playExpression("开心")).rejects.toThrow("定位运行期间");
    await expect(adapter.startMapping()).rejects.toThrow("定位正在运行");
    expect((await adapter.getSnapshot()).expressions).toEqual([]);
    expect((await adapter.getSnapshot()).checks.find((check) => check.id === "actions")?.ready).toBe(false);
    expect(backend.playExpression).not.toHaveBeenCalled();
    expect(startMapping).not.toHaveBeenCalled();
    localizationBusy = false;
    await adapter.playExpression("开心");
    await adapter.startMapping();
    expect(backend.playExpression).toHaveBeenCalledOnce();
    expect(startMapping).toHaveBeenCalledOnce();
  });

  it("aborts queued work when the owner closes instead of dispatching it late", async () => {
    const operations = new ProductOperations();
    let release!: () => void;
    const pending = operations.run(async (current) => {
      await new Promise<void>((resolve) => { release = resolve; });
      if (!current()) throw new Error("stale_operation");
    });
    await Promise.resolve();
    operations.close(); release();
    await expect(pending).rejects.toThrow("stale_operation");
    await expect(operations.run(async (current) => {
      if (!current()) throw new Error("stale");
      return "dispatched";
    })).rejects.toThrow("已有产品操作");
  });

  it("does not queue a localization mutation behind a pending expression or vice versa", async () => {
    let release!: () => void;
    const backend = actions({ playExpression: vi.fn(() => new Promise<void>((resolve) => { release = resolve; })) });
    const adapter = new ProductActionsAdapter(new DemoAdapter(), backend, new ProductOperations());
    const lifecycle = vi.fn(async () => "started");
    const expression = adapter.playExpression("开心");
    await vi.waitFor(() => expect(backend.playExpression).toHaveBeenCalledOnce());
    await expect(adapter.runActivityControl(lifecycle)).rejects.toThrow("正在处理");
    expect(lifecycle).not.toHaveBeenCalled();
    release(); await expression;
    const pending = adapter.runActivityControl(() => new Promise<string>((resolve) => { release = () => resolve("started"); }));
    await vi.waitFor(() => expect(backend.state).toHaveBeenCalledOnce());
    await expect(adapter.playExpression("开心")).rejects.toThrow("正在处理");
    release(); await pending;
    expect(lifecycle).not.toHaveBeenCalled();
    expect(backend.playExpression).toHaveBeenCalledOnce();
  });

  it("fails closed on localization read errors but does not confuse active localization with a physical action", async () => {
    const backend = actions();
    const delegate = new DemoAdapter();
    const navigate = vi.spyOn(delegate, "startNavigation").mockResolvedValue();
    const adapter = new ProductActionsAdapter(delegate, backend, new ProductOperations());
    adapter.setActivityBusy(async () => true);
    expect((await adapter.getSnapshot()).actionBusy).toBe(false);
    await adapter.startNavigation({ id: "p", name: "P", mapId: "m", x: 0, y: 0, yaw: 0, isStart: false },
      { id: "m", name: "M", active: true, createdAt: "2026-11-03" }, "synthetic-navigation");
    expect(navigate).toHaveBeenCalledOnce();
    adapter.setActivityBusy(async () => { throw new Error("localization offline"); });
    await expect(adapter.playExpression("开心")).rejects.toThrow("定位运行期间");
    await expect(adapter.startMapping()).rejects.toThrow("定位正在运行");
    expect(backend.playExpression).not.toHaveBeenCalled();
  });

  it("does not begin mapping or localization changes while navigation is active", async () => {
    const backend = actions();
    const delegate = new DemoAdapter();
    const original = await delegate.getSnapshot();
    vi.spyOn(delegate, "getSnapshot").mockResolvedValue({ ...original,
      navigation: { status: "running", taskId: "navigation-1", detail: "active" } });
    const adapter = new ProductActionsAdapter(delegate, backend, new ProductOperations());
    const startMapping = vi.spyOn(delegate, "startMapping");
    const startLocalization = vi.fn(async () => undefined);
    await expect(adapter.startMapping()).rejects.toThrow("停止并确认导航");
    await expect(adapter.runActivityControl(startLocalization)).rejects.toThrow("导航运行期间");
    expect(startMapping).not.toHaveBeenCalled();
    expect(startLocalization).not.toHaveBeenCalled();
  });
});
