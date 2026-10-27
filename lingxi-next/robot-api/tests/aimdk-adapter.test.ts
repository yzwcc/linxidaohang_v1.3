import { afterEach, describe, expect, it, vi } from "vitest";
import { AimdkAdapter } from "../src/adapters/aimdk";

const response = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});

describe("AIMDK HTTP adapter action semantics", () => {
  afterEach(() => vi.unstubAllGlobals());

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
