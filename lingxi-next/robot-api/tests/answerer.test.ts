import { describe, expect, it, vi } from "vitest";
import { createHmac } from "node:crypto";
import { Answerer } from "../src/voice/answerer";
import { DemoAdapter } from "../src/adapters/demo";
import { MemoryStore } from "./fixtures";
import type { RobotSnapshot } from "../src/domain";

async function liveRobot(patch: Partial<RobotSnapshot> = {}) {
  const robot = Object.assign(new DemoAdapter(), { demoMode: false });
  const state = await robot.getSnapshot();
  robot.getSnapshot = async () => ({ ...state,
    poseEvidence: { source: "aimdk-map-odometry", coordinateUnits: "metres", observedAtMs: Date.now() },
    ...patch });
  return robot;
}

describe("grounded answers and bounded cloud calls", () => {
  it("answers robot pose from live state without model or knowledge calls", async () => {
    const fetcher = vi.fn();
    const result = await new Answerer(new MemoryStore(), await liveRobot(), { fetcher }).answer("机器人当前坐标是什么");
    expect(result.source).toBe("robot-state");
    expect(result.text).toContain("3.10");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each(["机器人在哪里", "你在哪", "你现在的位置是什么", "它的朝向是什么"])(
    "routes current robot location to evidence instead of the model: %s", async (question) => {
      const fetcher = vi.fn();
      const answer = await new Answerer(new MemoryStore(), await liveRobot(), { fetcher }).answer(question);
      expect(answer.source).toBe("robot-state");
      expect(fetcher).not.toHaveBeenCalled();
    });
  it.each([
    ["missing evidence", { poseEvidence: undefined }],
    ["stale observation", { poseEvidence: { source: "aimdk-map-odometry", coordinateUnits: "metres", observedAtMs: Date.now() - 2100 } }],
    ["future observation", { poseEvidence: { source: "aimdk-map-odometry", coordinateUnits: "metres", observedAtMs: Date.now() + 60_000 } }],
    ["unmeasured scale", { poseEvidence: { source: "independent-localization", coordinateUnits: "declared-metres", observedAtMs: Date.now() } }],
    ["unready localization", { checks: [{ id: "odom", label: "odom", ready: false }] }],
    ["missing localization check", { checks: [] }],
    ["invalid coordinates", { pose: { x: NaN, y: 0, yaw: 0 } }],
    ["no coordinates", { pose: undefined }],
    ["disconnected", { connected: false }],
    ["synthetic navigation", { navigationExecution: { backend: "nav2-offline", syntheticInputs: true, physicalMotionSent: false, navigationReady: false } }],
  ] satisfies [string, Partial<RobotSnapshot>][])("does not report %s as a live position", async (_name, patch) => {
    const fetcher = vi.fn();
    const emit = vi.fn();
    const answer = await new Answerer(new MemoryStore(), await liveRobot(patch), { fetcher })
      .streamAnswer("机器人当前坐标是什么", emit);
    expect(answer.source).toBe("refusal");
    expect(answer.text).not.toContain("米");
    expect(emit).toHaveBeenCalledExactlyOnceWith(answer.text);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("does not describe demo coordinates as the real robot's position", async () => {
    const fetcher = vi.fn();
    const robot = await liveRobot();
    Object.defineProperty(robot, "demoMode", { value: true });
    expect((await new Answerer(new MemoryStore(), robot, { fetcher }).answer("机器人当前坐标是什么")).source).toBe("refusal");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("does not replace an old observation time with a fresh snapshot time", async () => {
    const robot = await liveRobot({ poseEvidence: { source: "aimdk-map-odometry", coordinateUnits: "metres", observedAtMs: Date.now() - 5000 },
      updatedAt: new Date().toISOString() });
    expect((await new Answerer(new MemoryStore(), robot).answer("机器人当前坐标是什么")).source).toBe("refusal");
  });
  it("does not confuse an ordinary algorithm question with live robot status", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "model-test-key";
    const fetcher = vi.fn(async () => Response.json({ choices: [{ message: { content: "导航算法用于规划路线。" } }] }));
    const result = await new Answerer(store, await liveRobot(), { fetcher }).answer("你能介绍导航算法吗");
    expect(result.source).toBe("model");
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("refuses unsupported live state instead of inventing battery level", async () => {
    const fetcher = vi.fn();
    const result = await new Answerer(new MemoryStore(), new DemoAdapter(), { fetcher }).answer("机器人现在电量多少");
    expect(result.source).toBe("refusal");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("refuses venue questions when retrieval has no credentials", async () => {
    const fetcher = vi.fn();
    expect((await new Answerer(new MemoryStore(), new DemoAdapter(), { fetcher }).answer("展厅开放时间是什么")).source).toBe("refusal");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([
    "附近有什么好玩的",
    "周边有什么可以参观",
    "馆内卫生间在哪里",
    "厕所在哪",
    "哪里可以买水",
  ])("never sends local venue facts to the general model without retrieval evidence: %s", async (question) => {
    const store = new MemoryStore();
    store.credentials.apiKey = "model-test-key";
    const fetcher = vi.fn(async () => Response.json({
      choices: [{ message: { content: "模型猜测的地点信息" } }],
    }));
    const result = await new Answerer(store, new DemoAdapter(), { fetcher }).answer(question);
    expect(result.source).toBe("refusal");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([0.2, 0.59, NaN])("does not trust retrieval below the score threshold %s", async (score) => {
    const store = new MemoryStore();
    store.credentials = { linksoulAppId: "id", linksoulAppKey: "key", linksoulAppSecret: "secret", agentCode: "agent" };
    const fetcher = vi.fn(async () => Response.json({ success: true, data: [{ score, content: "不可靠内容" }] }));
    expect((await new Answerer(store, new DemoAdapter(), { fetcher }).answer("展厅开放时间")).source).toBe("refusal");
  });
  it("signs Retriever requests and uses only a scored text result", async () => {
    const store = new MemoryStore();
    store.credentials = { linksoulAppId: "id", linksoulAppKey: "key", linksoulAppSecret: "secret", agentCode: "agent" };
    const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
      const headers = init!.headers as Record<string, string>;
      const expected = createHmac("sha256", "secret")
        .update(`POST\n/api/V1/open-portal/app/retriever/search/agent_kb\n${headers["X-Timestamp"]}\n${headers["X-Nonce"]}`)
        .digest("hex");
      expect(headers["X-Signature"]).toBe(expected);
      expect(JSON.parse(String(init!.body)).agent_code).toBe("agent");
      return Response.json({ success: true, data: [{ score: 0.91, content: "开放时间为九点至十七点。" }] });
    });
    const result = await new Answerer(store, new DemoAdapter(), { fetcher }).answer("展厅开放时间");
    expect(result).toEqual({ source: "retriever", text: "开放时间为九点至十七点。" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("answers nearby venue questions only from a scored Retriever result", async () => {
    const store = new MemoryStore();
    store.credentials = { linksoulAppId: "id", linksoulAppKey: "key", linksoulAppSecret: "secret", agentCode: "agent" };
    const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
      const request = JSON.parse(String(init!.body));
      expect(request.agent_code).toBe("agent");
      expect(request.query).toBe("附近有什么好玩的");
      return Response.json({ success: true, data: [{ score: 0.91, content: "附近有一层机器人互动展区。" }] });
    });
    const result = await new Answerer(store, new DemoAdapter(), { fetcher }).answer("附近有什么好玩的");
    expect(result).toEqual({ source: "retriever", text: "附近有一层机器人互动展区。" });
  });
  it("keeps ordinary knowledge on the configured model and exposes no tools", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "model-test-key";
    const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
      const body = JSON.parse(String(init!.body));
      expect(body.tools).toBeUndefined();
      expect(body.temperature).toBe(0.2);
      return Response.json({ choices: [{ message: { content: "激光雷达通过测距感知周围环境。" } }] });
    });
    expect((await new Answerer(store, new DemoAdapter(), { fetcher }).answer("什么是激光雷达")).source).toBe("model");
  });
  it("refuses a cloud timeout without queuing a retry", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const fetcher = vi.fn(async () => { throw new DOMException("timeout", "TimeoutError"); });
    expect((await new Answerer(store, new DemoAdapter(), { fetcher }).answer("什么是激光雷达")).source).toBe("refusal");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(["length", "content_filter", "tool_calls", "function_call"])("refuses a nonstream model answer with unsuccessful finish reason %s", async (reason) => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const fetcher = vi.fn(async () => Response.json({
      choices: [{ message: { content: "不应作为完整回答接受。" }, finish_reason: reason }],
    }));
    const result = await new Answerer(store, new DemoAdapter(), { fetcher }).answer("什么是激光雷达");
    expect(result.source).toBe("refusal");
    expect(result.text).not.toContain("不应作为完整回答接受");
  });
});

describe("streaming model replies", () => {
  const stream = (chunks: string[], chunkBytes = 7) => new Response(new ReadableStream({
    start(controller) {
      const bytes = new TextEncoder().encode(chunks.join(""));
      // Split inside UTF-8 characters and SSE frame boundaries.
      for (let i = 0; i < bytes.length; i += chunkBytes) controller.enqueue(bytes.slice(i, i + chunkBytes));
      controller.close();
    },
  }), { headers: { "content-type": "text/event-stream" } });
  const delta = (text: string) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;
  const finish = (reason: string) => `data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: reason }] })}\n\n`;
  it("parses split UTF-8 SSE frames and emits the first sentence before completion", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const segments: string[] = [];
    const fetcher = vi.fn(async () => stream([delta("激光雷达用于测距。"), delta("它帮助机器人感知环境。"), "data: [DONE]\n\n"]));
    const result = await new Answerer(store, new DemoAdapter(), { fetcher }).streamAnswer("什么是激光雷达", (text) => segments.push(text));
    expect(result.source).toBe("model");
    expect(segments).toEqual(["激光雷达用于测距。", "它帮助机器人感知环境。"]);
  });
  it("reports an interrupted partial answer and never silently accepts a truncated stream", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const segments: string[] = [];
    const result = await new Answerer(store, new DemoAdapter(), {
      fetcher: vi.fn(async () => stream([delta("先给出一句话。")])),
    }).streamAnswer("什么是雷达", (text) => segments.push(text));
    expect(result.source).toBe("refusal");
    expect(segments[1]).toContain("回答中断");
  });
  it.each(["length", "content_filter", "tool_calls", "function_call", "unknown_reason"])("does not let DONE erase an unsuccessful terminal reason: %s", async (reason) => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const segments: string[] = [];
    const rejected = `data: ${JSON.stringify({ choices: [{ delta: { content: "不能播出的尾句。" }, finish_reason: reason }] })}\n\n`;
    const result = await new Answerer(store, new DemoAdapter(), {
      fetcher: vi.fn(async () => stream([delta("先给出一句话。"), rejected, "data: [DONE]\n\n"], 65536)),
    }).streamAnswer("什么是雷达", (text) => segments.push(text));
    expect(result.source).toBe("refusal");
    expect(segments).toEqual(["先给出一句话。", "回答中断，未完成的内容暂时无法核实。"]);
    expect(result.text).not.toContain("不能播出");
  });
  it.each(["data: [DONE]\n\n", finish("stop")])("ignores trailing events in the same network chunk after a successful terminal: %s", async (terminal) => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const segments: string[] = [];
    const result = await new Answerer(store, new DemoAdapter(), {
      fetcher: vi.fn(async () => stream([delta("正常回答。"), terminal, delta("终止后不允许追加。"), "data: invalid-json\n\n"], 65536)),
    }).streamAnswer("什么是雷达", (text) => segments.push(text));
    expect(result).toEqual({ source: "model", text: "正常回答。" });
    expect(segments).toEqual(["正常回答。"]);
  });
  it("never includes unsent pending text in an interrupted answer", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const segments: string[] = [];
    const result = await new Answerer(store, new DemoAdapter(), {
      fetcher: vi.fn(async () => stream([delta("已输出的句子。"), delta("尚未成句的内容"), finish("length")], 65536)),
    }).streamAnswer("什么是雷达", (text) => segments.push(text));
    expect(result.source).toBe("refusal");
    expect(result.text).toBe(segments.join("\n"));
    expect(result.text).not.toContain("尚未成句");
  });
  it("does not silently ignore a provider error frame after partial text", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const result = await new Answerer(store, new DemoAdapter(), {
      fetcher: vi.fn(async () => stream([delta("先给出一句话。"), 'data: {"error":{"code":"upstream_error"}}\n\n', "data: [DONE]\n\n"])),
    }).streamAnswer("什么是雷达", () => undefined);
    expect(result.source).toBe("refusal");
  });
  it("does not start cloud work for an already cancelled question", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const cancellation = new AbortController();
    cancellation.abort(new Error("superseded turn"));
    const fetcher = vi.fn(async () => stream([delta("旧回答。"), "data: [DONE]\n\n"]));
    const emit = vi.fn();
    await expect(new Answerer(store, new DemoAdapter(), { fetcher })
      .streamAnswer("什么是雷达", emit, cancellation.signal)).rejects.toThrow("superseded turn");
    expect(fetcher).not.toHaveBeenCalled();
    expect(emit).not.toHaveBeenCalled();
  });
  it("propagates session cancellation to a stalled Retriever call without a fallback", async () => {
    const store = new MemoryStore();
    store.credentials = { linksoulAppId: "id", linksoulAppKey: "key", linksoulAppSecret: "secret", agentCode: "agent" };
    const cancellation = new AbortController();
    const emit = vi.fn();
    let observed!: AbortSignal;
    let release!: () => void;
    const entered = new Promise<void>((resolve) => { release = resolve; });
    const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
      observed = init!.signal!;
      release();
      return new Promise<Response>((_resolve, reject) => {
        observed.addEventListener("abort", () => reject(observed.reason), { once: true });
      });
    });
    const result = new Answerer(store, new DemoAdapter(), { fetcher })
      .streamAnswer("展厅开放时间", emit, cancellation.signal);
    const rejected = expect(result).rejects.toThrow("session ended");
    await entered;
    cancellation.abort(new Error("session ended"));
    expect(observed.aborted).toBe(true);
    await rejected;
    expect(emit).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("cancels without emitting a late fallback reply", async () => {
    const store = new MemoryStore();
    store.credentials.apiKey = "test";
    const cancellation = new AbortController();
    const onSegment = vi.fn();
    const fetcher = vi.fn(async () => { cancellation.abort(new Error("new wake")); throw cancellation.signal.reason; });
    await expect(new Answerer(store, new DemoAdapter(), { fetcher })
      .streamAnswer("什么是雷达", onSegment, cancellation.signal)).rejects.toThrow("new wake");
    expect(onSegment).not.toHaveBeenCalled();
  });
  it("cancels a stalled SSE reader at the first-segment deadline", async () => {
    vi.useFakeTimers();
    try {
      const store = new MemoryStore();
      store.credentials.apiKey = "test";
      const cancel = vi.fn();
      const fetcher = vi.fn(async () => new Response(new ReadableStream({ cancel }), {
        headers: { "content-type": "text/event-stream" },
      }));
      const segments: string[] = [];
      const answering = new Answerer(store, new DemoAdapter(), { fetcher })
        .streamAnswer("什么是雷达", (text) => segments.push(text));
      await vi.advanceTimersByTimeAsync(1601);
      expect((await answering).source).toBe("refusal");
      expect(cancel).toHaveBeenCalledOnce();
      expect(segments).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
