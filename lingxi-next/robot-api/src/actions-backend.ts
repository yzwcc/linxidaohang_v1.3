import { randomBytes } from "node:crypto";
import { z } from "zod";
import type { RobotAdapter, RobotSnapshot } from "./domain";
import { assertActionAuthorized } from "./action-authorization";

const expressions = ["眨眼", "平静", "卖萌", "开心", "悲伤", "同情", "疑惑", "震惊", "严肃", "思考"] as const;
const stateSchema = z.object({
  bridgeMode: z.literal("actions-only"), physicalActionsEnabled: z.literal(false),
  bridgeEpoch: z.string().regex(/^[a-f0-9]{32}$/), sequence: z.number().int().positive().safe(),
  observedAtMs: z.number().int().nonnegative().safe(), actionBusy: z.boolean(),
  actionStatus: z.enum(["uncertain", "linkcraft-awaiting-operator", "preset-motion", "dispatching"]).nullable(),
  motionPresets: z.array(z.never()).max(0), linkcraftResources: z.array(z.never()).max(0),
  expressions: z.array(z.enum(expressions)).max(expressions.length).refine((items) => new Set(items).size === items.length),
  actionServices: z.object({ presetMotion: z.boolean(), expression: z.boolean(),
    linkcraftCatalog: z.boolean(), linkcraftExecution: z.boolean(), linkcraftCatalogFresh: z.boolean() }),
});
export type ActionsState = z.infer<typeof stateSchema>;
export interface ActionsBackend {
  state(): Promise<ActionsState>;
  playExpression(name: string, authorize?: () => boolean): Promise<void>;
  confirmIdle(): Promise<void>;
}

const denied = (message: string, statusCode = 409) => Object.assign(new Error(message), { statusCode });
const uncertainty = (error: unknown) => (error as { uncertain?: boolean })?.uncertain !== false;

/** No motion, navigation or map endpoint exists on this transport. */
export class HttpActionsBackend implements ActionsBackend {
  private previous?: ActionsState;
  private uncertain = false;
  private tail: Promise<void> = Promise.resolve();

  constructor(private readonly endpoint: string, private readonly token: string) {
    if (!/^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/v1$/.test(endpoint) ||
        Number(new URL(endpoint).port) > 65535 || !/^[\x21-\x7e]{32,4096}$/.test(token)) {
      throw new Error("explicit_local_actions_endpoint_and_private_token_required");
    }
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.tail.then(operation);
    this.tail = result.then(() => undefined, () => undefined);
    return result;
  }

  private async request(route: string, body?: object): Promise<unknown> {
    const response = await fetch(this.endpoint + route, {
      method: body ? "POST" : "GET", redirect: "error",
      headers: { Authorization: `Bearer ${this.token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(body ? 3500 : 1200),
    });
    if (!response.ok) {
      const text = await response.text();
      const remote = text.length <= 64000 ? JSON.parse(text) as { uncertain?: unknown } : {};
      const error = denied("动作服务未确认请求", response.status === 409 ? 409 : 503);
      if (response.status === 409 && remote.uncertain === false) Object.assign(error, { uncertain: false });
      else Object.assign(error, { uncertain: true });
      throw error;
    }
    const reader = response.body?.getReader();
    if (!reader) throw denied("动作服务返回空响应", 503);
    const parts: Buffer[] = [];
    let length = 0;
    try {
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.byteLength;
        if (length > 64000) throw denied("动作服务响应超过限制", 503);
        parts.push(Buffer.from(part.value));
      }
      return JSON.parse(Buffer.concat(parts, length).toString("utf8"));
    } finally { await reader.cancel().catch(() => undefined); }
  }

  private async read(): Promise<ActionsState> {
    const state = stateSchema.parse(await this.request("/state"));
    const age = Date.now() - state.observedAtMs;
    if (age < -50 || age > 1200 || (this.previous?.bridgeEpoch === state.bridgeEpoch &&
        state.sequence <= this.previous.sequence)) throw denied("动作服务状态已过期或乱序", 503);
    if (this.previous && state.bridgeEpoch !== this.previous.bridgeEpoch && this.previous.actionBusy) this.uncertain = true;
    this.previous = state;
    return state;
  }

  state(): Promise<ActionsState> {
    return this.serialized(async () => {
      const state = await this.read();
      return this.uncertain ? { ...state, actionBusy: true, actionStatus: "uncertain", expressions: [] } : state;
    });
  }

  private async command(route: string, value: object, state: ActionsState) {
    const requestId = randomBytes(16).toString("hex");
    const result = z.object({ bridgeEpoch: z.literal(state.bridgeEpoch), requestId: z.literal(requestId),
      ...(route === "/expression" ? { accepted: z.literal(true), completed: z.literal(false) }
        : { confirmed: z.literal(true), actionBusy: z.literal(false) }),
    }).parse(await this.request(route, { ...value, bridgeEpoch: state.bridgeEpoch, requestId }));
    return result;
  }

  playExpression(name: string, authorize: () => boolean = () => true): Promise<void> {
    return this.serialized(async () => {
      let dispatched = false;
      try {
        assertActionAuthorized(authorize);
        const state = await this.read();
        if (this.uncertain || state.actionBusy || !state.expressions.includes(name as typeof expressions[number])) {
          throw denied("表情不可用或动作状态尚未确认");
        }
        assertActionAuthorized(authorize);
        dispatched = true;
        await this.command("/expression", { name }, state);
      } catch (error) { if (dispatched && uncertainty(error)) this.uncertain = true; throw error; }
    });
  }

  confirmIdle(): Promise<void> {
    return this.serialized(async () => {
      try {
        const state = await this.read();
        if (state.actionBusy) await this.command("/actions/confirm-idle", { confirmed: true }, state);
        this.uncertain = false;
      } catch (error) { this.uncertain = true; throw error; }
    });
  }
}

/** Fail-fast ownership for product API mutations; never queue stale commands. */
export class ProductOperations {
  busy = false;
  private closed = false;
  private jobBusy: () => boolean = () => false;
  setJobBusy(jobBusy: () => boolean) { this.jobBusy = jobBusy; }
  isJobBusy() { return this.jobBusy(); }
  async run<T>(operation: (current: () => boolean) => Promise<T>): Promise<T> {
    if (this.closed || this.busy) throw denied("已有产品操作正在处理，请稍后重试");
    this.busy = true;
    try { return await operation(() => !this.closed); }
    finally { this.busy = false; }
  }
  close() { this.closed = true; }
}

export class ProductActionsAdapter implements RobotAdapter {
  readonly demoMode = false;
  private readonly operations: ProductOperations;
  private activityBusy: () => Promise<boolean> = async () => false;
  private expressionLocalizationReady: () => Promise<boolean> = async () => false;
  constructor(private readonly delegate: RobotAdapter, private readonly actions: ActionsBackend,
    operations = new ProductOperations()) { this.operations = operations; }
  setJobBusy(jobBusy: () => boolean) { this.operations.setJobBusy(jobBusy); }
  setActivityBusy(activityBusy: () => Promise<boolean>) { this.activityBusy = activityBusy; }
  setExpressionLocalizationReady(ready: () => Promise<boolean>) { this.expressionLocalizationReady = ready; }
  close() { this.operations.close(); }

  private async localizationBlocksExpression() {
    try { return await this.activityBusy() && !(await this.expressionLocalizationReady()); }
    catch { return true; }
  }

  async getSnapshot(): Promise<RobotSnapshot> {
    const original = await this.delegate.getSnapshot();
    try {
      const [state, localizationBlocked] = await Promise.all([this.actions.state(), this.localizationBlocksExpression()]);
      const navigationBusy = !!original.mapping || original.navigation.requiresStop ||
        ["requested", "running", "paused"].includes(original.navigation.status);
      const locked = !!original.actionBusy || state.actionBusy || this.operations.busy || localizationBlocked || navigationBusy;
      return { ...original, actionBusy: !!original.actionBusy || state.actionBusy || this.operations.busy,
        actionStatus: state.actionStatus ?? (this.operations.busy ? "dispatching" : undefined),
        motionPresets: [], linkcraftResources: [], expressions: locked ? [] : state.expressions,
        checks: [...original.checks.filter((check) => check.id !== "actions"), { id: "actions", label: "动作服务",
          ready: state.actionServices.expression && !locked,
          detail: state.actionBusy ? "动作状态待确认" : localizationBlocked ? "定位未稳定跟踪，表情暂不可用"
            : navigationBusy ? "建图或导航期间动作暂不可用" : "独立表情已接入，实体动作未准入" }],
      };
    } catch {
      return { ...original, actionBusy: true, actionStatus: "uncertain", motionPresets: [], expressions: [],
        linkcraftResources: [], checks: [...original.checks.filter((check) => check.id !== "actions"),
          { id: "actions", label: "动作服务", ready: false, detail: "动作服务状态未确认" }],
      };
    }
  }
  private mutate<T>(operation: (current: () => boolean) => Promise<T>, requireLocalizationIdle = false) {
    return this.operations.run(async (current) => {
      const state = await this.actions.state();
      if (!current() || state.actionBusy) throw denied("动作状态未解除，操作已拒绝");
      if (requireLocalizationIdle) {
        let localizationBusy: boolean;
        try { localizationBusy = await this.activityBusy(); } catch { localizationBusy = true; }
        if (localizationBusy) throw denied("定位正在运行或状态未确认，当前操作已拒绝");
      }
      if (!current()) throw denied("产品操作已失效");
      return operation(current);
    });
  }
  runMappingControl<T>(operation: () => Promise<T>) {
    if (this.operations.isJobBusy()) return Promise.reject(denied("讲解任务运行期间不接受互斥运行控制"));
    return this.mutate(async (current) => {
      const snapshot = await this.delegate.getSnapshot();
      if (!snapshot.mapping && (snapshot.navigation.requiresStop ||
          ["requested", "running", "paused"].includes(snapshot.navigation.status))) {
        throw denied("请先停止并确认导航，再开始建图");
      }
      if (!current()) throw denied("产品操作已失效");
      return operation();
    }, true);
  }
  runActivityControl<T>(operation: () => Promise<T>) {
    if (this.operations.isJobBusy()) return Promise.reject(denied("讲解任务运行期间不接受互斥运行控制"));
    return this.operations.run(async (current) => {
      const state = await this.actions.state();
      if (!current() || state.actionBusy) throw denied("动作状态未解除，操作已拒绝");
      const snapshot = await this.delegate.getSnapshot();
      if (snapshot.mapping || snapshot.navigation.requiresStop ||
          ["requested", "running", "paused"].includes(snapshot.navigation.status)) {
        throw denied("建图或导航运行期间不接受定位变更");
      }
      if (!current()) throw denied("产品操作已失效");
      return operation();
    });
  }
  listMaps() { return this.delegate.listMaps(); }
  getLidarFrame() {
    return this.delegate.getLidarFrame ? this.delegate.getLidarFrame() : Promise.reject(denied("雷达服务不可用", 503));
  }
  getStoredMapGrid: RobotAdapter["getStoredMapGrid"] = (map) => this.delegate.getStoredMapGrid(map);
  startMapping() {
    return this.runMappingControl(() => this.delegate.startMapping());
  }
  stopAndSaveMap(name: string) { return this.runMappingControl(() => this.delegate.stopAndSaveMap(name)); }
  activateMap: RobotAdapter["activateMap"] = (map, pose) => this.mutate(() => this.delegate.activateMap(map, pose), true);
  startNavigation: RobotAdapter["startNavigation"] = (point, map, id) => this.mutate(() => this.delegate.startNavigation(point, map, id));
  controlNavigation: RobotAdapter["controlNavigation"] = (action, id) => action === "stop"
    ? this.delegate.controlNavigation(action, id) : this.mutate(() => this.delegate.controlNavigation(action, id));
  async startPresetMotion(): Promise<never> { throw denied("独立模式实体动作未准入"); }
  async waitPresetMotion(): Promise<never> { throw denied("独立模式实体动作未准入"); }
  async executeLinkcraftResource(): Promise<never> { throw denied("独立模式实体动作未准入"); }
  async playExpression(name: string, authorize?: () => boolean) {
    assertActionAuthorized(authorize);
    const internal = authorize !== undefined;
    if (this.operations.isJobBusy() && !internal) return Promise.reject(denied("讲解任务运行期间不接受独立表情请求"));
    return this.operations.run(async (current) => {
      const state = await this.delegate.getSnapshot();
      const localizationBlocked = await this.localizationBlocksExpression();
      assertActionAuthorized(authorize);
      if (localizationBlocked) throw denied("定位未稳定跟踪，当前不接受表情请求");
      if (!current() || state.mapping || state.actionBusy || state.navigation.requiresStop ||
          ["requested", "running", "paused"].includes(state.navigation.status)) {
        throw denied("建图、导航或未确认状态期间不接受表情请求");
      }
      return this.actions.playExpression(name, () => current() && (internal ? authorize?.() === true : !this.operations.isJobBusy()));
    });
  }
  confirmActionsIdle() { return this.operations.run(() => this.actions.confirmIdle()); }
}
