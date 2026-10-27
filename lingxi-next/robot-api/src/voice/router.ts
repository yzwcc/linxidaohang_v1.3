import type { MapPoint, TourTask, VoiceEvent, VoiceReply } from "../../../shared/contracts";

export type VoiceIntent =
  | { type: "navigate"; point: MapPoint }
  | { type: "tour-start"; tour: TourTask }
  | { type: "tour-pause" }
  | { type: "tour-resume" }
  | { type: "tour-stop" }
  | { type: "motion"; name: string }
  | { type: "question"; text: string }
  | { type: "clarify"; text: string }
  | { type: "ignore" };

type VoiceContext = {
  points: MapPoint[];
  tours: TourTask[];
  selectedTourId?: string;
  motions: string[];
};

const EVENT_TTL_MS = 10 * 60_000;
const SESSION_TTL_MS = 60_000;
const MAX_EVENT_AGE_MS = 30_000;

export class VoiceRouter {
  private readonly seen = new Map<string, number>();
  private readonly sessions = new Map<string, number>();

  constructor(private readonly confidenceThreshold = 0.78) {}

  route(event: VoiceEvent, context: VoiceContext): { intent: VoiceIntent; reply: VoiceReply } {
    this.prune();
    const start = performance.now();
    if (
      !event.eventId ||
      !event.sessionId ||
      !Number.isFinite(event.occurredAt) ||
      Math.abs(Date.now() - event.occurredAt) > MAX_EVENT_AGE_MS
    ) {
      return this.result({ type: "ignore" }, false, "rejected", "", start);
    }

    const dedupeKey = `${event.sessionId}:${event.eventId}`;
    if (this.seen.has(dedupeKey)) return this.result({ type: "ignore" }, false, "rejected", "", start);
    this.seen.set(dedupeKey, Date.now());

    if (event.kind === "wake") {
      this.sessions.clear();
      this.sessions.set(event.sessionId, Date.now() + SESSION_TTL_MS);
      return this.result({ type: "ignore" }, true, "command", "", start);
    }
    if (event.kind === "session-ended") {
      this.sessions.delete(event.sessionId);
      return this.result({ type: "ignore" }, true, "command", "", start);
    }
    if (!this.isSessionActive(event.sessionId) || !event.isFinal || !event.transcript.trim()) {
      return this.result({ type: "ignore" }, false, "rejected", "", start);
    }
    this.sessions.set(event.sessionId, Date.now() + SESSION_TTL_MS);

    const transcript = normalize(event.transcript);
    if (!transcript || !Number.isFinite(event.confidence) || event.confidence < this.confidenceThreshold) {
      return this.result(
        { type: "clarify", text: transcript },
        true,
        "clarification",
        "我没听清，请再说一次。",
        start,
      );
    }

    const control = this.matchControl(transcript, context);
    if (control) return this.replyForIntent(control, start);

    const navigation = transcript.match(/^(?:请)?(?:带我去|导航到|前往|去)(.+?)(?:吧|谢谢)?$/);
    if (navigation) {
      const requested = cleanName(navigation[1]);
      const matches = context.points.filter((point) => normalize(point.name) === requested);
      if (matches.length === 1) return this.replyForIntent({ type: "navigate", point: matches[0] }, start);
      return this.result(
        { type: "clarify", text: requested },
        true,
        "clarification",
        matches.length > 1
          ? "这个点位名称不唯一，请说完整名称。"
          : "没有匹配到这个点位，请说地图中的完整点位名称。",
        start,
      );
    }

    const motionRequest = transcript.match(/^(?:请)?(?:执行|表演|做)(.+?)(?:动作|吧)?$/);
    if (motionRequest) {
      const requested = cleanName(motionRequest[1]);
      const matches = context.motions.filter((motion) => normalize(motion) === requested);
      if (matches.length === 1) return this.replyForIntent({ type: "motion", name: matches[0] }, start);
      return this.result(
        { type: "clarify", text: requested },
        true,
        "clarification",
        "这个动作不在已配置的预设列表中，不会执行。",
        start,
      );
    }

    if (looksLikeControl(transcript)) {
      return this.result(
        { type: "clarify", text: transcript },
        true,
        "clarification",
        "我不能确认这条操作指令，请明确说出点位、讲解任务或已配置的预设动作。",
        start,
      );
    }

    if (looksLikeQuestion(transcript, event.transcript)) {
      return this.result({ type: "question", text: event.transcript.trim() }, true, "question", "", start);
    }
    return this.result(
      { type: "clarify", text: transcript },
      true,
      "clarification",
      "我没理解，请换一种明确的说法。",
      start,
    );
  }

  isSessionActive(sessionId: string): boolean {
    const expiresAt = this.sessions.get(sessionId);
    if (!expiresAt) return false;
    if (expiresAt <= Date.now()) {
      this.sessions.delete(sessionId);
      return false;
    }
    return true;
  }

  private matchControl(transcript: string, context: VoiceContext): VoiceIntent | null {
    if (/^(?:暂停讲解|暂停当前讲解|暂停导览)$/.test(transcript)) return { type: "tour-pause" };
    if (/^(?:继续讲解|恢复讲解|继续导览)$/.test(transcript)) return { type: "tour-resume" };
    if (/^(?:停止讲解|结束讲解|停止导览)$/.test(transcript)) return { type: "tour-stop" };

    const start = transcript.match(/^(?:开始|启动)(?:讲解|导览)(?:任务)?(.*)$/);
    if (!start) return null;
    const requested = cleanName(start[1]);
    if (!requested) {
      const selected = context.tours.find((tour) => tour.id === context.selectedTourId);
      return selected
        ? { type: "tour-start", tour: selected }
        : { type: "clarify", text: "请先选择一个讲解任务。" };
    }
    const matches = context.tours.filter((tour) => normalize(tour.name) === requested);
    if (matches.length === 1) return { type: "tour-start", tour: matches[0] };
    return {
      type: "clarify",
      text: matches.length ? "任务名称不唯一。" : "没有找到同名讲解任务。",
    };
  }

  private replyForIntent(intent: VoiceIntent, start: number) {
    const text: Partial<Record<VoiceIntent["type"], string>> = {
      navigate: intent.type === "navigate" ? `收到，正在前往${intent.point.name}。` : "",
      "tour-start": intent.type === "tour-start" ? `收到，开始${intent.tour.name}。` : "",
      "tour-pause": "讲解已暂停。",
      "tour-resume": "继续讲解。",
      "tour-stop": "讲解已停止。",
      motion: intent.type === "motion" ? `收到，执行${intent.name}。` : "",
      clarify: intent.type === "clarify" ? intent.text : "",
    };
    return this.result(
      intent,
      intent.type !== "ignore",
      intent.type === "clarify" ? "clarification" : "command",
      text[intent.type] ?? "",
      start,
    );
  }

  private result(
    intent: VoiceIntent,
    accepted: boolean,
    category: VoiceReply["category"],
    text: string,
    start: number,
  ) {
    return {
      intent,
      reply: { accepted, category, text, latencyMs: Math.round(performance.now() - start) } satisfies VoiceReply,
    };
  }

  private prune() {
    const cutoff = Date.now() - EVENT_TTL_MS;
    for (const [key, timestamp] of this.seen) {
      if (timestamp < cutoff) this.seen.delete(key);
    }
    for (const [sessionId, expiresAt] of this.sessions) {
      if (expiresAt <= Date.now()) this.sessions.delete(sessionId);
    }
    while (this.seen.size > 5000) this.seen.delete(this.seen.keys().next().value!);
  }
}

function normalize(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("zh-CN")
    .replace(/[，。！？!?、；：,.?;:\s]/g, "")
    .replace(/^(?:机器人|灵犀)[，、]?/, "");
}

function cleanName(value: string): string {
  return normalize(value.replace(/^(?:一下|帮我|我想|请)/, "").replace(/(?:一下|谢谢|吧|哦)$/, ""));
}

function looksLikeControl(text: string): boolean {
  return /(导航|带路|开始|停止|暂停|继续|执行|表演|动作|返回|回到|跳舞|挥手)/.test(text);
}

function looksLikeQuestion(normalized: string, original: string): boolean {
  return /[吗呢么?？]$/.test(original.trim()) ||
    /^(请问|什么|为什么|怎么|如何|哪里|哪儿|多少|介绍|能否|能不能|是否|告诉我|讲一下)/.test(normalized);
}
