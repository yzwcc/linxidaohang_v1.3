import { afterEach, describe, expect, it, vi } from "vitest";
import { AimdkAdapter } from "../src/adapters/aimdk";
import { ActionAuthorizationRevoked } from "../src/action-authorization";
import { Answerer } from "../src/voice/answerer";
import { MemoryStore } from "./fixtures";

const response = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});

describe("AIMDK HTTP adapter action semantics", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([0, 5000, -60_000, null])("preserves observation age through native HTTP state and voice: %s", async (ageMs) => {
    const observedAtMs = ageMs === null ? null : Date.now() - ageMs;
    const fetchMock = vi.fn(async (_url: unknown) => response(200, {
      connected: true, mapping: false, checks: [{ id: "odom", label: "odom", ready: true }],
      pose: { x: 1.5, y: 2.5, yaw: 0 }, navigation: { status: "idle" },
      poseEvidence: observedAtMs === null ? null
        : { source: "aimdk-map-odometry", coordinateUnits: "metres", observedAtMs },
      updatedAt: new Date().toISOString(),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await new Answerer(new MemoryStore(), new AimdkAdapter()).answer("你现在的位置在哪里");
    expect(result.source).toBe(ageMs === 0 ? "robot-state" : "refusal");
    if (ageMs === 0) expect(result.text).toContain("1.50 米");
    else expect(result.text).not.toContain("米");
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:18082/v1/state");
  });

  it("does not write a stale tour expression through the native adapter", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(new AimdkAdapter().playExpression("开心", () => false))
      .rejects.toBeInstanceOf(ActionAuthorizationRevoked);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("dispatches one preset request and polls its matching task to completion", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(202, { accepted: true, taskId: "42", status: "running" }))
      .mockResolvedValueOnce(response(200, { taskId: "42", status: "pending" }))
      .mockResolvedValueOnce(response(200, { taskId: "42", status: "running" }))
      .mockResolvedValueOnce(response(200, { taskId: "42", status: "finished" }));
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new AimdkAdapter("http://127.0.0.1:18082/v1", "test-token");

    const taskId = await adapter.startPresetMotion("挥手");
    await adapter.waitPresetMotion(taskId);

    expect(taskId).toBe("42");
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      name: "挥手", monitorCompletion: true,
    });
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(fetchMock.mock.calls[1][1].method).toBe("GET");
  });

  it("does not treat an ambiguous motion state as completion", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      response(200, { taskId: "42", status: "unknown" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new AimdkAdapter();

    await expect(adapter.waitPresetMotion("42")).rejects.toThrow("未确认");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("accepts LinkCraft service acknowledgement without claiming playback completion", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(202, {
      accepted: true, completed: false, message: "Play success",
    }));
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new AimdkAdapter();

    await expect(adapter.executeLinkcraftResource("a".repeat(32))).resolves.toBeUndefined();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ id: "a".repeat(32) });
  });

  it("rejects a response that claims LinkCraft completion without a supported status", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(202, {
      accepted: true, completed: true, message: "Play success",
    }));
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new AimdkAdapter();

    await expect(adapter.executeLinkcraftResource("b".repeat(32))).rejects.toThrow("未确认");
  });
});
