import { describe, expect, it, vi } from "vitest";
import { createHmac } from "node:crypto";
import { Answerer } from "../src/voice/answerer";
import { DemoAdapter } from "../src/adapters/demo";
import { MemoryStore } from "./fixtures";

describe("grounded answers and bounded cloud calls", () => {
  it("answers robot pose from live state without model or knowledge calls", async () => {
    const fetcher = vi.fn();
    const result = await new Answerer(new MemoryStore(), new DemoAdapter(), { fetcher }).answer("机器人当前坐标是什么");
    expect(result.source).toBe("robot-state");
    expect(result.text).toContain("3.10");
    expect(fetcher).not.toHaveBeenCalled();
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
});

describe("streaming model replies", () => {
  const stream = (chunks: string[]) => new Response(new ReadableStream({
    start(controller) {
      const bytes = new TextEncoder().encode(chunks.join(""));
      // Split inside UTF-8 characters and SSE frame boundaries.
      for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7));
      controller.close();
    },
  }), { headers: { "content-type": "text/event-stream" } });
  const delta = (text: string) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;
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
