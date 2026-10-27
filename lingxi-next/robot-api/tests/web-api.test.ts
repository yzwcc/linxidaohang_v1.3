import { afterEach, describe, expect, it, vi } from "vitest";
import type { RobotState } from "../../shared/contracts";
import { openEvents, type Connection } from "../../web/src/api";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readonly listeners = new Map<string, Array<(event: { data?: string }) => void>>();
  readyState = 1;

  constructor(readonly url: URL, readonly protocols: string[]) {
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: (event: { data?: string }) => void) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type: string, event: { data?: string } = {}) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  close() {
    this.readyState = 3;
    this.emit("close");
  }
}

const connection: Connection = {
  baseUrl: "https://192.168.1.20:3000",
  token: "a".repeat(43),
  demo: false,
};

function useFakeBrowserTimers() {
  vi.useFakeTimers();
  vi.stubGlobal("window", {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWebSocket.instances = [];
});

describe("state WebSocket subscription", () => {
  it("reconnects after disconnect and continues delivering state", async () => {
    useFakeBrowserTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const onState = vi.fn();
    const onError = vi.fn();
    const subscription = openEvents(connection, onState, onError);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url.href).toBe("wss://192.168.1.20:3000/api/v1/events");
    FakeWebSocket.instances[0].emit("close");
    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(499);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
    const state = { connected: true } as RobotState;
    FakeWebSocket.instances[1].emit("message", { data: JSON.stringify({ type: "state", state }) });
    expect(onState).toHaveBeenCalledWith(state);
    subscription.close();
    expect(FakeWebSocket.instances[1].readyState).toBe(3);
  });

  it("uses bounded exponential backoff and cancels retries on close", async () => {
    useFakeBrowserTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const subscription = openEvents(connection, vi.fn(), vi.fn());
    for (let attempt = 0; attempt < 6; attempt += 1) {
      FakeWebSocket.instances.at(-1)!.emit("error");
      await vi.advanceTimersByTimeAsync(Math.min(500 * 2 ** attempt, 15_000));
    }
    expect(FakeWebSocket.instances).toHaveLength(7);
    FakeWebSocket.instances.at(-1)!.emit("error");
    subscription.close();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeWebSocket.instances).toHaveLength(7);
  });

  it("retries when WebSocket construction fails", async () => {
    useFakeBrowserTimers();
    const instances: FakeWebSocket[] = [];
    let failed = false;
    class FailingOnceSocket extends FakeWebSocket {
      constructor(url: URL, protocols: string[]) {
        if (!failed) { failed = true; throw new Error("offline"); }
        super(url, protocols);
        instances.push(this);
      }
    }
    vi.stubGlobal("WebSocket", FailingOnceSocket as unknown as typeof WebSocket);
    const onError = vi.fn();
    const subscription = openEvents(connection, vi.fn(), onError);
    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(500);
    expect(instances).toHaveLength(1);
    subscription.close();
  });
});
