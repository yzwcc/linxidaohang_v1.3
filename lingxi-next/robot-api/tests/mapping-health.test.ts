import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LidarEvent } from "../../shared/contracts";
import type { RobotSnapshot } from "../src/domain";
import { mappingConsoleAdapter, mappingGuard, type MappingBackend, type MappingBackendState } from "../src/mapping-backend";

const identity = "a".repeat(32);
const now = 1_800_000_000_000;
const liveEvent = (): Extract<LidarEvent, { type: "slam-preview" }> => ({
  type: "slam-preview", inputMode: "live", coordinateUnits: "native-raw",
  frame: { sequence: 1, epoch: identity, source: "point-cloud", frameId: "synthetic-test",
    sensorFrame: "synthetic-test", fixedFrame: true, stampMs: 1000, receivedAtMs: now - 500,
    points: [1, 2, 3] },
});

describe("isolated mapping live-point health with synthetic fixtures", () => {
  let state: MappingBackendState, event: LidarEvent, backend: MappingBackend;
  let points: { getPointEvent: ReturnType<typeof vi.fn<() => LidarEvent>> };

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(now);
    state = { backend: "glim", inputMode: "live", phase: "running", session: identity,
      mapping: true, cleanupComplete: false };
    event = liveEvent();
    backend = { state: vi.fn(async () => state), start: vi.fn(), finish: vi.fn() };
    points = { getPointEvent: vi.fn(() => event) };
  });
  afterEach(() => { vi.useRealTimers(); });
  const lidar = (snapshot: RobotSnapshot) =>
    snapshot.checks.find((check) => check.id === "lidar");
  const guard = () => mappingGuard(mappingConsoleAdapter(), backend, true, points);

  it("accepts only fresh live points and does not promote other robot capabilities", async () => {
    const result = await guard().getSnapshot();
    expect(lidar(result)).toMatchObject({ ready: true, detail: "当前建图会话实时点云已接收" });
    for (const id of ["aimdk", "odom", "navigation", "linksoul"]) {
      expect(result.checks.find((check) => check.id === id)?.ready).toBe(false);
    }
    expect(backend.state).toHaveBeenCalledOnce();
    expect(points.getPointEvent).toHaveBeenCalledOnce();
  });

  it.each([
    ["recorded preview", (value: LidarEvent) => ({ ...value, inputMode: "recorded" })],
    ["synthetic preview", (value: LidarEvent) => ({ ...value, inputMode: "synthetic" })],
    ["ordinary lidar", (value: LidarEvent) => ({ ...value, type: "lidar" })],
    ["unavailable", () => ({ type: "lidar-unavailable", message: "Synthetic disconnect" })],
    ["old session", (value: LidarEvent) => ({ ...value, frame: { ...liveEvent().frame, epoch: "b".repeat(32) } })],
    ["empty points", (value: LidarEvent) => ({ ...value, frame: { ...liveEvent().frame, points: [] } })],
    ["expired", (value: LidarEvent) => ({ ...value, frame: { ...liveEvent().frame, receivedAtMs: now - 2001 } })],
    ["future", (value: LidarEvent) => ({ ...value, frame: { ...liveEvent().frame, receivedAtMs: now + 51 } })],
    ["invalid time", (value: LidarEvent) => ({ ...value, frame: { ...liveEvent().frame, receivedAtMs: NaN } })],
  ] as const)("rejects %s as evidence of current live input", async (_label, change) => {
    event = change(event) as LidarEvent;
    expect(lidar(await guard().getSnapshot())?.ready).toBe(false);
  });

  it.each(["idle", "requested", "saving", "stopping", "failed"] as const)(
    "does not consult points in phase %s", async (phase) => {
      state = { ...state, phase };
      expect(lidar(await guard().getSnapshot())?.ready).toBe(false);
      expect(points.getPointEvent).not.toHaveBeenCalled();
    });

  it.each([now - 2000, now + 50])("accepts the freshness boundary %i", async (receivedAtMs) => {
    event = { ...liveEvent(), frame: { ...liveEvent().frame, receivedAtMs } };
    expect(lidar(await guard().getSnapshot())?.ready).toBe(true);
  });

  it.each([
    { inputMode: "recorded" as const }, { mapping: false }, { session: null },
  ])("requires active live mapping with a session: %j", async (change) => {
    state = { ...state, ...change };
    expect(lidar(await guard().getSnapshot())?.ready).toBe(false);
    expect(points.getPointEvent).not.toHaveBeenCalled();
  });

  it("clears readiness on timeout, stop and session replacement", async () => {
    const adapter = guard();
    expect(lidar(await adapter.getSnapshot())?.ready).toBe(true);
    vi.setSystemTime(now + 1501);
    expect(lidar(await adapter.getSnapshot())?.ready).toBe(false);
    vi.setSystemTime(now);
    state = { ...state, phase: "idle", mapping: false, cleanupComplete: true };
    expect(lidar(await adapter.getSnapshot())?.ready).toBe(false);
    state = { ...state, phase: "running", mapping: true, session: "b".repeat(32) };
    expect(lidar(await adapter.getSnapshot())?.ready).toBe(false);
    event = { ...liveEvent(), frame: { ...liveEvent().frame, epoch: state.session! } };
    expect(lidar(await adapter.getSnapshot())?.ready).toBe(true);
  });

  it("fails closed for a missing point source, disconnected pipe or unavailable backend", async () => {
    expect(lidar(await mappingGuard(mappingConsoleAdapter(), backend, true).getSnapshot())?.ready).toBe(false);
    points.getPointEvent.mockImplementation(() => { throw new Error("Synthetic disconnected pipe"); });
    expect(lidar(await guard().getSnapshot())?.ready).toBe(false);
    vi.mocked(backend.state).mockRejectedValue(new Error("Synthetic backend unavailable"));
    expect(lidar(await guard().getSnapshot())?.ready).toBe(false);
  });

  it("does not override vendor lidar checks outside the isolated console", async () => {
    const base = mappingConsoleAdapter(), snapshot = await base.getSnapshot();
    base.getSnapshot = async () => ({ ...snapshot, checks: snapshot.checks.map((check) =>
      check.id === "lidar" ? { ...check, ready: true, detail: "Vendor status" } : check) });
    expect(lidar(await mappingGuard(base, backend, false, points).getSnapshot())).toMatchObject({
      ready: true, detail: "Vendor status",
    });
    expect(points.getPointEvent).not.toHaveBeenCalled();
  });
});
