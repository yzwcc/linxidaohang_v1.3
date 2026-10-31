import { randomUUID } from "node:crypto";
import type { MapPoint, NavigationState, RobotMap, TourTask } from "../../shared/contracts";
import type { RobotAdapter } from "./domain";
import type { StateStore } from "./store";

export interface SpeechOutput {
  // Resolves when playback ends; firstAudioMs is measured by the output adapter.
  speak(text: string, correlationId: string): Promise<{ firstAudioMs: number }>;
  cancel?(correlationId: string): Promise<void>;
}

type Phase = "navigating" | "narrating" | "waiting" | "idle-return" | "halted";
type ActiveJob = {
  runId: string;
  kind: "point" | "tour";
  tourId?: string;
  taskId?: string;
  pointId?: string;
  manualPaused: boolean;
  cancelled: boolean;
  generation: number;
  phase: Phase;
  status: NavigationState["status"];
  detail: string;
  interruption: number;
  speechId?: string;
  pendingDispatch?: Promise<void>;
  pendingNavigationControls: Set<Promise<void>>;
};
export type JobRecord = { task: string; point: string; at: string; reason: string };

let sequence = 0;
function createTaskId(): string {
  sequence = (sequence + 1) % 1000;
  return (BigInt(Date.now()) * 1000n + BigInt(sequence)).toString();
}

export class JobManager {
  private current?: ActiveJob;
  private generation = 0;
  private lastInteractionAt = Date.now();
  private dialogueCount = 0;
  private readonly records: JobRecord[] = [];
  private lastState: NavigationState = { status: "idle", detail: "无活动任务" };

  constructor(
    private readonly adapter: RobotAdapter,
    private readonly store: StateStore,
    private readonly speaker: SpeechOutput,
    private readonly timing = { pollMs: 250, navigationTimeoutMs: 120_000, quietResumeMs: 5000 },
  ) {}

  state(): NavigationState {
    const job = this.current;
    if (!job) return { ...this.lastState };
    return {
      status: job.manualPaused ? "paused" : job.status,
      requiresStop: job.phase === "halted" || job.cancelled,
      taskId: job.tourId ?? job.runId,
      pointId: job.pointId,
      detail: job.manualPaused ? "人工暂停，等待继续" : job.detail,
    };
  }

  history(): JobRecord[] {
    return structuredClone(this.records);
  }

  hasActiveJob(): boolean {
    return Boolean(this.current);
  }

  async startPoint(point: MapPoint, authorize: () => boolean = () => true): Promise<void> {
    const db = await this.store.read();
    const map = db.maps.find((candidate) => candidate.id === point.mapId);
    await this.assertReady(map, point.mapId, db.activeMapId);
    this.assertAuthorized(authorize);
    const job = this.begin("point", `正在请求前往 ${point.name}`);
    job.pointId = point.id;
    job.taskId = createTaskId();
    try {
      job.pendingDispatch = this.adapter.startNavigation(point, map!, job.taskId);
      await job.pendingDispatch;
    } catch (error) {
      this.halt(job, "导航请求未确认，请检查状态并停止后重试");
      throw error;
    }
    if (this.isCurrent(job)) {
      void this.runPoint(job, point, map!, job.taskId, db.idleReturnSeconds)
        .catch(() => this.halt(job, "导航状态不可用，请检查状态并停止后重试"));
    }
  }

  async startTour(tour: TourTask, authorize: () => boolean = () => true): Promise<void> {
    if (tour.requiresReview) throw new Error("迁移任务需要先核对动作与资源，并在讲解管理中保存确认");
    const db = await this.store.read();
    const map = db.maps.find((candidate) => candidate.id === tour.mapId);
    await this.assertReady(map, tour.mapId, db.activeMapId);
    if (!tour.steps.length || tour.repeatCount < 1 || tour.repeatCount > 20) throw new Error("讲解任务参数无效");
    if (tour.steps.some((step) =>
      !db.points.some((point) => point.id === step.pointId && point.mapId === tour.mapId))) {
      throw new Error("讲解任务包含无效或跨地图点位");
    }
    const resources = await this.adapter.getSnapshot();
    const resourceSteps = tour.steps.filter((step) => step.linkcraftResourceId);
    if (resourceSteps.length && (
      resourceSteps.length !== 1 ||
      tour.repeatCount !== 1 ||
      tour.steps.at(-1)?.linkcraftResourceId !== resourceSteps[0].linkcraftResourceId
    )) {
      throw new Error("AIMDK 未提供灵创播放完成状态；灵创资源只能配置在单轮讲解的最后一步");
    }
    for (const step of tour.steps) {
      if (step.presetMotion && !resources.motionPresets.includes(step.presetMotion)) throw new Error("任务包含未配置预设动作");
      if (step.expression && !resources.expressions.includes(step.expression)) {
        throw new Error("任务包含未适配表情");
      }
      if (step.linkcraftResourceId &&
          !resources.linkcraftResources.some((resource) => resource.id === step.linkcraftResourceId)) {
        throw new Error("任务包含未适配灵创资源");
      }
    }
    this.assertAuthorized(authorize);
    const job = this.begin("tour", `准备讲解：${tour.name}`);
    job.tourId = tour.id;
    void this.runTour(job, tour, map!, db.points, authorize)
      .catch(() => {
        if (!job.taskId && !authorize()) this.end(job, "语音会话已失效，任务未下发", "idle");
        else this.halt(job, "任务接口异常，请检查状态并停止后重试");
      });
  }

  async control(action: "pause" | "resume" | "stop", authorize: () => boolean = () => true): Promise<void> {
    this.assertAuthorized(authorize);
    const job = this.current;
    if (!job) throw new Error("当前没有活动任务");
    if (action !== "stop" && job.phase === "halted") throw new Error("任务状态未确认，请先停止任务");
    if (action !== "stop" && !this.isCurrent(job)) throw new Error("任务状态已失效");
    if (action === "stop") {
      await job.pendingDispatch?.catch(() => undefined);
      this.assertAuthorized(authorize);
      if (this.current !== job) return;
      // Invalidate synchronously with dispatch, before any stop I/O can yield.
      job.cancelled = true;
      this.generation += 1;
      try {
        await Promise.allSettled([...job.pendingNavigationControls]);
        const results = await Promise.allSettled([
          job.speechId ? this.speaker.cancel?.(job.speechId) : undefined,
          job.taskId ? this.adapter.controlNavigation("stop", job.taskId) : undefined,
        ]);
        const failed = results.find((result) => result.status === "rejected");
        if (failed?.status === "rejected") throw failed.reason;
        if (this.current === job) {
          this.current = undefined;
          this.lastState = { status: "idle", detail: "任务已停止" };
        }
      } catch (error) {
        job.manualPaused = false;
        job.phase = "halted";
        job.status = "failed";
        job.detail = "停止请求未确认，禁止开始新任务；请检查机器人并重试停止";
        throw error;
      }
      return;
    }
    if (action === "pause") {
      await job.pendingDispatch;
      this.assertAuthorized(authorize);
      if (!this.isCurrent(job)) return;
      job.interruption += 1;
      job.manualPaused = true;
      try {
        if (job.speechId) await this.speaker.cancel?.(job.speechId);
        this.assertAuthorized(authorize);
        // Once accepted, finish this pause even if the voice session ends.
        if (this.isCurrent(job) && job.phase === "navigating" && job.taskId) {
          await this.requestNavigationControl(job, "pause");
        }
      } catch (error) {
        job.manualPaused = false;
        if (!authorize()) throw error;
        this.halt(job, "暂停请求未确认，请检查状态并停止后重试");
        throw error;
      }
      return;
    }
    if (!job.manualPaused) return;
    try {
      if (job.phase === "navigating" && job.taskId) await this.requestNavigationControl(job, "resume");
    } catch (error) {
      job.manualPaused = false;
      this.halt(job, "继续请求未确认，请检查状态并停止后重试");
      throw error;
    }
    if (this.isCurrent(job)) {
      job.manualPaused = false;
      job.detail = "继续执行";
    }
  }

  markInteraction(): void {
    this.lastInteractionAt = Date.now();
  }

  async interruptTour(): Promise<void> {
    this.markInteraction();
    const job = this.current;
    if (!job || job.kind !== "tour" || job.phase === "halted") return;
    job.interruption += 1;
    if (job.speechId) await this.speaker.cancel?.(job.speechId);
  }

  beginDialogue(): void {
    this.dialogueCount += 1;
    this.markInteraction();
  }

  endDialogue(): void {
    this.dialogueCount = Math.max(0, this.dialogueCount - 1);
    this.markInteraction();
  }

  async runAllowlistedMotion(name: string, authorize: () => boolean = () => true): Promise<void> {
    const snapshot = await this.adapter.getSnapshot();
    if (this.current || snapshot.actionBusy ||
        ["requested", "running", "paused"].includes(snapshot.navigation.status)) {
      throw new Error("任务执行期间不接受独立预设动作");
    }
    if (!snapshot.motionPresets.includes(name)) throw new Error("该预设动作未配置");
    this.assertAuthorized(authorize);
    await this.adapter.startPresetMotion(name);
  }

  private assertAuthorized(authorize: () => boolean) {
    if (!authorize()) throw new Error("语音指令已失效");
  }

  private async assertReady(map: RobotMap | undefined, mapId: string, activeMapId?: string) {
    if (!map) throw new Error("关联地图不存在");
    if (mapId !== activeMapId) throw new Error("目标不属于当前活动地图");
    const snapshot = await this.adapter.getSnapshot();
    if (!snapshot.connected || !snapshot.checks.find((check) => check.id === "navigation")?.ready) {
      throw new Error("导航接口尚未就绪");
    }
    if (snapshot.navigation.requiresStop) throw new Error("导航状态未确认，请先停止任务");
    if (snapshot.actionBusy) throw new Error("机器人仍在执行或等待确认一个动作");
    if (snapshot.mapping) throw new Error("建图期间不能开始导航");
    if (["requested", "running", "paused"].includes(snapshot.navigation.status)) {
      throw new Error("机器人已有导航任务");
    }
  }

  private begin(kind: ActiveJob["kind"], detail: string): ActiveJob {
    if (this.current) throw new Error("已有任务正在执行或等待确认");
    const job: ActiveJob = {
      runId: randomUUID(), kind, manualPaused: false, cancelled: false,
      generation: ++this.generation, phase: "navigating", status: "requested",
      detail, interruption: 0, pendingNavigationControls: new Set(),
    };
    this.current = job;
    return job;
  }

  private async runPoint(job: ActiveJob, point: MapPoint, map: RobotMap, taskId: string, idleReturnSeconds: number) {
    const arrived = await this.waitForCompletion(job, taskId);
    if (!this.isCurrent(job)) return;
    if (!arrived) return this.end(job, "导航失败", "failed");
    const start = (await this.store.read()).points.find((candidate) => candidate.mapId === map.id && candidate.isStart);
    if (!this.isCurrent(job)) return;
    if (idleReturnSeconds <= 0 || !start || start.id === point.id) return this.end(job, `已到达 ${point.name}`);
    job.phase = "idle-return";
    job.status = "running";
    job.detail = `已到达 ${point.name}，等待 ${idleReturnSeconds} 秒无交互返航`;
    await this.waitQuiet(job, idleReturnSeconds * 1000, idleReturnSeconds * 1000);
    if (!this.isCurrent(job)) return;
    job.phase = "navigating";
    job.taskId = createTaskId();
    job.pointId = start.id;
    job.status = "requested";
    job.detail = "正在请求返回起点";
    job.pendingDispatch = this.adapter.startNavigation(start, map, job.taskId);
    await job.pendingDispatch;
    if (!this.isCurrent(job)) return;
    const returned = await this.waitForCompletion(job, job.taskId);
    if (this.isCurrent(job)) this.end(job, returned ? "已返回起点" : "返航失败", returned ? "finished" : "failed");
  }

  private async runTour(job: ActiveJob, tour: TourTask, map: RobotMap, points: MapPoint[], authorize: () => boolean) {
    let dispatched = false;
    let hadErrors = false;
    for (let round = 1; round <= tour.repeatCount && this.isCurrent(job); round += 1) {
      for (const [index, step] of tour.steps.entries()) {
        await this.waitReady(job);
        if (!this.isCurrent(job)) return;
        const point = points.find((candidate) => candidate.id === step.pointId)!;
        job.pointId = point.id;
        job.phase = "navigating";
        job.detail = `${tour.name} · 第 ${round}/${tour.repeatCount} 轮 · ${index + 1}/${tour.steps.length}`;
        let arrived = false;
        for (let attempt = 0; attempt < 2; attempt += 1) {
          await this.waitReady(job);
          if (!this.isCurrent(job)) return;
          if (!dispatched) this.assertAuthorized(authorize);
          job.taskId = createTaskId();
          job.status = "requested";
          // Only a matching explicit failed state permits a retry. A timeout
          // or ambiguous transport response stops the runner.
          job.pendingDispatch = this.adapter.startNavigation(point, map, job.taskId);
          dispatched = true;
          await job.pendingDispatch;
          if (!this.isCurrent(job)) return;
          arrived = await this.waitForCompletion(job, job.taskId);
          if (!this.isCurrent(job)) return;
          if (arrived) break;
        }
        if (!arrived) {
          hadErrors = true;
          this.record(tour.id, point.id, "导航重试一次后失败，已跳过");
          continue;
        }
        job.taskId = undefined;
        job.phase = "narrating";
        try {
          await this.waitReady(job);
          if (!this.isCurrent(job)) return;
          if (step.presetMotion) {
            const motionTaskId = await this.adapter.startPresetMotion(step.presetMotion, false);
            await this.adapter.waitPresetMotion(motionTaskId);
          }
          await this.waitReady(job);
          if (!this.isCurrent(job)) return;
          if (step.expression) await this.adapter.playExpression(step.expression);
        } catch {
          if (!this.isCurrent(job)) return;
          hadErrors = true;
          this.record(tour.id, point.id, "动作请求或结束状态未确认，已停止后续任务");
          this.halt(job, "动作请求或结束状态未确认，请检查机器人后手动停止任务");
          return;
        }
        if (step.narration.trim()) {
          let delivered = false;
          try {
            while (!delivered && this.isCurrent(job)) {
              await this.waitReady(job);
              if (!this.isCurrent(job)) return;
              const interruption = job.interruption;
              const speechId = `${job.runId}:${round}:${index}:${interruption}`;
              job.speechId = speechId;
              try {
                await this.speaker.speak(step.narration, speechId);
                delivered = interruption === job.interruption;
              } catch (error) {
                if (interruption === job.interruption) throw error;
              } finally {
                if (job.speechId === speechId) job.speechId = undefined;
              }
            }
          } catch {
            if (!this.isCurrent(job)) return;
            hadErrors = true;
            this.record(tour.id, point.id, "点位语音未完成");
          }
        }
        if (step.linkcraftResourceId) {
          try {
            await this.waitReady(job);
            if (!this.isCurrent(job)) return;
            await this.adapter.executeLinkcraftResource(step.linkcraftResourceId);
          } catch {
            if (!this.isCurrent(job)) return;
            this.record(tour.id, point.id, "灵创资源请求未获服务确认，已停止后续任务");
            this.halt(job, "灵创资源请求未获确认，请检查机器人后手动停止任务");
            return;
          }
          this.record(tour.id, point.id, "灵创服务已接受资源请求；SDK 未提供播放完成状态");
          this.end(job, "讲解结束；灵创资源请求已接受，但播放完成状态不可见");
          return;
        }
        if (!this.isCurrent(job)) return;
        job.phase = "waiting";
        await this.waitQuiet(job, step.waitSeconds * 1000, this.timing.quietResumeMs);
        if (!this.isCurrent(job)) return;
      }
    }
    if (this.isCurrent(job)) this.end(job, hadErrors ? "讲解已结束，存在失败或跳过项" : "讲解已完成");
  }

  private async waitForCompletion(job: ActiveJob, taskId: string): Promise<boolean> {
    let remaining = this.timing.navigationTimeoutMs;
    while (this.isCurrent(job)) {
      await this.waitReady(job, false);
      if (!this.isCurrent(job)) return false;
      const began = Date.now();
      const snapshot = await this.adapter.getSnapshot();
      if (!this.isCurrent(job)) return false;
      const nav = snapshot.navigation;
      if (nav.taskId === taskId) {
        if (nav.requiresStop) throw new Error("导航执行器状态未确认，禁止重试或推进任务");
        if (nav.status === "finished") return true;
        if (nav.status === "failed") return false;
        job.status = nav.status === "paused" ? "paused" : nav.status === "running" ? "running" : "requested";
      }
      await delay(this.timing.pollMs);
      remaining -= Date.now() - began;
      if (remaining <= 0) throw new Error("导航状态未在期限内确认");
    }
    return false;
  }

  private async waitQuiet(job: ActiveJob, firstMs: number, afterInteractionMs: number) {
    let deadline = Date.now() + firstMs;
    let observed = this.lastInteractionAt;
    while (this.isCurrent(job)) {
      const began = Date.now();
      await this.waitReady(job);
      deadline += Date.now() - began;
      if (!this.isCurrent(job)) return;
      if (observed !== this.lastInteractionAt) {
        observed = this.lastInteractionAt;
        deadline = Date.now() + afterInteractionMs;
      }
      if (Date.now() >= deadline) return;
      await delay(Math.min(this.timing.pollMs, deadline - Date.now()));
    }
  }

  private async waitReady(job: ActiveJob, waitForDialogue = true) {
    while (this.isCurrent(job) && (
      job.manualPaused ||
      (waitForDialogue && job.kind === "tour" &&
        (this.dialogueCount > 0 || (job.interruption > 0 && Date.now() - this.lastInteractionAt < this.timing.quietResumeMs)))
    )) await delay(this.timing.pollMs);
  }

  private record(task: string, point: string, reason: string) {
    const record = { task, point, reason, at: new Date().toISOString() };
    this.records.push(record);
    if (this.records.length > 500) this.records.shift();
    void this.store.update((db) => ({ ...db, jobRecords: [...db.jobRecords.slice(-499), record] })).catch(() => undefined);
  }

  private halt(job: ActiveJob, detail: string) {
    if (!this.isCurrent(job)) return;
    job.phase = "halted";
    job.status = "failed";
    job.detail = detail;
  }

  private end(job: ActiveJob, detail: string, status: NavigationState["status"] = "finished") {
    if (!this.isCurrent(job)) return;
    this.lastState = { status, detail, taskId: job.tourId ?? job.runId, pointId: job.pointId };
    this.current = undefined;
  }

  private isCurrent(job: ActiveJob) {
    // Retain halted jobs for an explicit stop, but never advance their runners.
    return this.current === job && !job.cancelled && job.generation === this.generation && job.phase !== "halted";
  }

  private requestNavigationControl(job: ActiveJob, action: "pause" | "resume"): Promise<void> {
    const taskId = job.taskId;
    if (!taskId) return Promise.resolve();
    const request = this.adapter.controlNavigation(action, taskId);
    job.pendingNavigationControls.add(request);
    void request.then(
      () => job.pendingNavigationControls.delete(request),
      () => job.pendingNavigationControls.delete(request),
    );
    return request;
  }

  async dispose(): Promise<void> {
    if (this.current) await this.control("stop");
  }
}

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, Math.max(1, ms)));
