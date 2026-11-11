import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, openEvents, type Connection, type RobotState } from "../../web/src/api";
import { RobotSession } from "../../web/src/robot-session";

vi.mock("../../web/src/api", async (original) => ({
  ...await original<typeof import("../../web/src/api")>(),
  api: vi.fn(), openEvents: vi.fn(),
}));

const connection: Connection = { baseUrl: "https://192.168.0.128:3445", token: "synthetic-session", demo: false };
const snapshot = (seconds = 1) => ({
  connected: true, updatedAt: new Date(seconds * 1000).toISOString(),
}) as RobotState;
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

let browser: EventTarget;
let page: EventTarget & { visibilityState: string };
let streams: Array<{ push(state: RobotState): void; fail(): void; close: ReturnType<typeof vi.fn> }>;
const sessions: RobotSession[] = [];
const request = vi.mocked(api);

function createSession(active = connection) {
  const listeners = { state: vi.fn(), settings: vi.fn(), disconnected: vi.fn(), expired: vi.fn(), notice: vi.fn() };
  const session = new RobotSession(active, listeners);
  sessions.push(session);
  return { session, listeners };
}

beforeEach(() => {
  vi.useFakeTimers();
  browser = new EventTarget();
  Object.assign(browser, { setInterval, clearInterval });
  page = Object.assign(new EventTarget(), { visibilityState: "visible" });
  vi.stubGlobal("window", browser);
  vi.stubGlobal("document", page);
  streams = [];
  request.mockReset();
  request.mockImplementation(async (_connection, path) => path === "/api/v1/state" ? snapshot() : {});
  vi.mocked(openEvents).mockReset().mockImplementation((_connection, push, fail) => {
    const stream = { push, fail, close: vi.fn() };
    streams.push(stream);
    return stream;
  });
});

afterEach(() => {
  for (const session of sessions.splice(0)) session.close();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("paired Web and Android session ownership", () => {
  it("coalesces polling instead of accumulating overlapping refresh requests", async () => {
    const state = deferred<RobotState>();
    request.mockImplementation(async (_connection, path) => path === "/api/v1/state" ? state.promise : {});
    const { session, listeners } = createSession();
    const pending = session.refresh();
    expect(session.refresh()).toBe(pending);
    expect(request).toHaveBeenCalledTimes(2);
    state.resolve(snapshot());
    await pending;
    expect(listeners.state).toHaveBeenCalledExactlyOnceWith(snapshot());
    await session.refresh();
    expect(request).toHaveBeenCalledTimes(4);
  });

  it("does not let an older poll overwrite a push received while it was in flight", async () => {
    const state = deferred<RobotState>();
    request.mockImplementation(async (_connection, path) => path === "/api/v1/state" ? state.promise : {});
    const { session, listeners } = createSession();
    session.start();
    const pending = session.refresh();
    streams[0].push(snapshot(2));
    state.resolve(snapshot(1));
    await pending;
    expect(listeners.state).toHaveBeenCalledExactlyOnceWith(snapshot(2));
    expect(listeners.settings).toHaveBeenCalledOnce();
    streams[0].push(snapshot(1));
    expect(listeners.state).toHaveBeenCalledOnce();
  });

  it("aborts closed-session requests and ignores late success, pushes and errors", async () => {
    const state = deferred<RobotState>();
    request.mockImplementation(async (_connection, path) => path === "/api/v1/state" ? state.promise : {});
    const { session, listeners } = createSession();
    session.start();
    const pending = session.refresh();
    const signal = request.mock.calls[0][2]!.signal!;
    session.close();
    expect(signal.aborted).toBe(true);
    state.resolve(snapshot());
    streams[0].push(snapshot(3));
    streams[0].fail();
    await pending;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listeners.state).not.toHaveBeenCalled();
    expect(listeners.settings).not.toHaveBeenCalled();
    expect(listeners.disconnected).not.toHaveBeenCalled();
    expect(listeners.notice).not.toHaveBeenCalled();
    expect(streams[0].close).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("does not let a retired connection's late 401 expire a replacement session", async () => {
    const old = deferred<RobotState>();
    request.mockImplementation(async (active, path) =>
      path !== "/api/v1/state" ? {} : active === connection ? old.promise : snapshot(2));
    const first = createSession();
    const pending = first.session.refresh();
    first.session.close();
    const next = createSession({ ...connection, token: "replacement-session" });
    await next.session.refresh();
    old.reject(new ApiError("expired", 401));
    await pending;
    expect(first.listeners.expired).not.toHaveBeenCalled();
    expect(next.listeners.expired).not.toHaveBeenCalled();
    expect(next.listeners.state).toHaveBeenCalledExactlyOnceWith(snapshot(2));
  });

  it("cancels an old command without displaying its late success or refreshing the new robot", async () => {
    const control = deferred<unknown>();
    request.mockImplementation(async () => control.promise);
    const { session, listeners } = createSession();
    const pending = session.run("/api/v1/navigation/start", { pointId: "synthetic-point" });
    session.close();
    expect(request.mock.calls[0][2]!.signal!.aborted).toBe(true);
    control.resolve({ accepted: true });
    expect(await pending).toBeUndefined();
    expect(await session.run("/api/v1/navigation/start")).toBeUndefined();
    expect(request).toHaveBeenCalledOnce();
    expect(listeners.notice).not.toHaveBeenCalled();
  });

  it("retires the current session on 401 and clears its polling and resume listeners", async () => {
    request.mockRejectedValue(new ApiError("expired", 401));
    const { session, listeners } = createSession();
    session.start();
    await session.refresh().catch(() => undefined);
    expect(listeners.expired).toHaveBeenCalledOnce();
    expect(streams[0].close).toHaveBeenCalledOnce();
    browser.dispatchEvent(new Event("online"));
    page.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(request).toHaveBeenCalledTimes(2);
    expect(openEvents).toHaveBeenCalledOnce();
  });

  it("reconnects on foreground/network return and cancels a suspended poll without replaying commands", async () => {
    const old = deferred<RobotState>();
    request.mockImplementationOnce(async () => old.promise);
    const { session, listeners } = createSession();
    session.start();
    const pending = session.refresh();
    const oldSignal = request.mock.calls[0][2]!.signal!;
    page.visibilityState = "hidden";
    page.dispatchEvent(new Event("visibilitychange"));
    expect(openEvents).toHaveBeenCalledOnce();
    page.visibilityState = "visible";
    page.dispatchEvent(new Event("visibilitychange"));
    expect(oldSignal.aborted).toBe(true);
    expect(openEvents).toHaveBeenCalledTimes(2);
    expect(streams[0].close).toHaveBeenCalledOnce();
    await session.refresh();
    old.reject(new Error("late old network failure"));
    await pending;
    expect(listeners.state).toHaveBeenCalledExactlyOnceWith(snapshot());
    expect(listeners.disconnected).toHaveBeenCalledOnce();
    expect(listeners.notice).not.toHaveBeenCalled();
    browser.dispatchEvent(new Event("online"));
    await session.refresh();
    expect(openEvents).toHaveBeenCalledTimes(3);
    expect(request.mock.calls.every((call) => !call[2]?.method)).toBe(true);
  });

  it("does not downgrade a newer push when an older polling request fails", async () => {
    const old = deferred<RobotState>();
    request.mockImplementationOnce(async () => old.promise);
    const { session, listeners } = createSession();
    session.start();
    const pending = session.refresh();
    streams[0].push(snapshot(3));
    old.reject(new Error("failed poll"));
    await pending.catch(() => undefined);
    expect(listeners.disconnected).not.toHaveBeenCalled();
    expect(listeners.state).toHaveBeenCalledExactlyOnceWith(snapshot(3));
  });

  it("does not retry an uncertain command after network return or periodic refresh", async () => {
    request.mockImplementation(async (_connection, path) => {
      if (path === "/api/v1/navigation/start") throw new Error("connection lost after send");
      return path === "/api/v1/state" ? snapshot() : {};
    });
    const { session, listeners } = createSession();
    session.start();
    await session.refresh();
    await expect(session.run("/api/v1/navigation/start")).rejects.toThrow("connection lost after send");
    browser.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(request.mock.calls.filter((call) => call[1] === "/api/v1/navigation/start")).toHaveLength(1);
    expect(listeners.notice).toHaveBeenCalledExactlyOnceWith("connection lost after send");
  });

  it("refreshes after a successful mutation and expires an unauthorized control session", async () => {
    const { session, listeners } = createSession();
    expect(await session.run("/api/v1/mapping/stop-save", { name: "synthetic-map" })).toEqual({});
    expect(listeners.notice).toHaveBeenCalledExactlyOnceWith("地图保存已确认。");
    expect(listeners.state).toHaveBeenCalledOnce();
    request.mockRejectedValueOnce(new ApiError("expired", 401));
    await expect(session.run("/api/v1/navigation/control")).rejects.toThrow("expired");
    expect(listeners.expired).toHaveBeenCalledOnce();
    const count = request.mock.calls.length;
    await session.run("/api/v1/navigation/start");
    expect(request).toHaveBeenCalledTimes(count);
  });
});
