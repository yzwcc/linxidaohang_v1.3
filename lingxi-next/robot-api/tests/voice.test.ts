import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceRouter } from "../src/voice/router";
import { context, recognition } from "./fixtures";

describe("verified wake sessions and whitelist routing", () => {
  let router: VoiceRouter;
  beforeEach(() => {
    vi.useFakeTimers();
    router = new VoiceRouter();
  });
  afterEach(() => vi.useRealTimers());
  const wake = () => router.route({ kind: "wake", eventId: "wake", sessionId: "session", occurredAt: Date.now() }, context);

  it("never opens a session from a transcript containing a wake word", () => {
    expect(router.route(recognition("灵犀，去接待台"), context).intent.type).toBe("ignore");
  });
  it("requires a matching verified wake event", () => {
    wake();
    expect(router.route(recognition(), context).intent.type).toBe("navigate");
    expect(router.route(recognition("去接待台", { sessionId: "other" }), context).intent.type).toBe("ignore");
  });
  it("deduplicates final events before dispatch", () => {
    wake();
    const event = recognition();
    expect(router.route(event, context).intent.type).toBe("navigate");
    expect(router.route(event, context).intent.type).toBe("ignore");
  });
  it("expires sessions and rejects stale queued recognition", () => {
    wake();
    expect(router.route(recognition("去接待台", { occurredAt: Date.now() - 31_000 }), context).intent.type).toBe("ignore");
    vi.advanceTimersByTime(60_001);
    expect(router.route(recognition(), context).intent.type).toBe("ignore");
  });
  it("closes sessions explicitly", () => {
    wake();
    router.route({ kind: "session-ended", eventId: "end", sessionId: "session", occurredAt: Date.now() }, context);
    expect(router.route(recognition(), context).intent.type).toBe("ignore");
  });
  it("does not let an older wake replace a newer session", () => {
    const now = Date.now();
    router.route({ kind: "wake", eventId: "new", sessionId: "new", occurredAt: now }, context);
    expect(router.route({ kind: "wake", eventId: "old", sessionId: "old", occurredAt: now - 10 }, context).reply.accepted).toBe(false);
    expect(router.route(recognition("去接待台", { sessionId: "new" }), context).intent.type).toBe("navigate");
  });
  it("remembers an end that arrives before its delayed wake", () => {
    const now = Date.now();
    router.route({ kind: "session-ended", eventId: "end", sessionId: "session", occurredAt: now }, context);
    expect(router.route({ kind: "wake", eventId: "wake", sessionId: "session", occurredAt: now - 1 }, context).reply.accepted).toBe(false);
    expect(router.route(recognition(), context).intent.type).toBe("ignore");
  });
  it("does not reopen an ended wake ID or close a newer session with an old end", () => {
    wake();
    router.route({ kind: "session-ended", eventId: "end", sessionId: "session", occurredAt: Date.now() }, context);
    vi.advanceTimersByTime(1);
    expect(router.route({ kind: "wake", eventId: "replay", sessionId: "session", occurredAt: Date.now() }, context).reply.accepted).toBe(false);
    router.route({ kind: "wake", eventId: "next", sessionId: "next", occurredAt: Date.now() }, context);
    router.route({ kind: "session-ended", eventId: "late-end", sessionId: "session", occurredAt: Date.now() }, context);
    expect(router.isSessionActive("next")).toBe(true);
  });
  it("invalidates an admitted turn before its asynchronous context lookup finishes", () => {
    wake();
    const first = recognition("去接待台", { eventId: "first" });
    expect(router.admit(first)).toBe(true);
    const next = recognition("暂停讲解", { eventId: "next" });
    expect(router.admit(next)).toBe(true);
    expect(router.resolve(first, context).intent.type).toBe("ignore");
    expect(router.resolve(next, context).intent.type).toBe("tour-pause");
  });
  it("rejects recognition from before the wake and older final results", () => {
    wake();
    expect(router.route(recognition("去接待台", { occurredAt: Date.now() - 1 }), context).intent.type).toBe("ignore");
    vi.advanceTimersByTime(10);
    expect(router.route(recognition(), context).intent.type).toBe("navigate");
    expect(router.route(recognition("执行挥手", { occurredAt: Date.now() - 1 }), context).intent.type).toBe("ignore");
  });
  it("ignores interim results and clarifies low confidence", () => {
    wake();
    expect(router.route(recognition("去接待台", { isFinal: false }), context).intent.type).toBe("ignore");
    expect(router.route(recognition("去接待台", { confidence: 0.4 }), context).intent.type).toBe("clarify");
  });
  it.each(["去未知地点", "请表演后空翻", "做任意关节动作", "如果有人来就开始讲解", "不要开始讲解", "他说去接待台", "去接待台然后挥手", "执行shell脚本"])(
    "does not execute ambiguous or unauthorized text: %s", (text) => {
      wake();
      expect(["ignore", "clarify", "question"]).toContain(router.route(recognition(text), context).intent.type);
    },
  );
  it.each([
    ["去接待台", "navigate"], ["开始讲解", "tour-start"], ["暂停讲解", "tour-pause"],
    ["继续讲解", "tour-resume"], ["停止讲解", "tour-stop"], ["执行挥手", "motion"],
    ["请问什么是激光雷达", "question"],
  ])("routes an exact approved intent: %s", (text, expected) => {
    wake();
    expect(router.route(recognition(text), context).intent.type).toBe(expected);
  });
  it("clarifies duplicate point names instead of selecting arbitrarily", () => {
    wake();
    expect(router.route(recognition(), { ...context, points: [...context.points, { ...context.points[0], id: "duplicate" }] }).intent.type).toBe("clarify");
  });
});
