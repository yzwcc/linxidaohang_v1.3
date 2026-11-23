import { api, ApiError, jsonBody, openEvents, type Connection, type EventSubscription,
  type PublicSettings, type RobotState } from "./api";

type SessionListeners = {
  state(state: RobotState): void;
  settings(settings: PublicSettings): void;
  disconnected(): void;
  expired(): void;
  notice(message: string): void;
};

const staleControlMessage = "连接中断或正在同步，收到机器人状态后请重新操作。";

function requiresLiveState(path: string, body: unknown): boolean {
  return ["/api/v1/mapping/start", "/api/v1/navigation/point", "/api/v1/navigation/mode",
    "/api/v1/navigation/preview"].includes(path)
    || /^\/api\/v1\/(?:tours\/[^/]+\/start|maps\/[^/]+\/activate)$/.test(path)
    || (path === "/api/v1/navigation/control" && typeof body === "object" && body !== null
      && "action" in body && body.action === "resume");
}

export class RobotSession {
  private closed = false;
  private started = false;
  private liveState = false;
  private disconnectedReported = false;
  private readonly abort = new AbortController();
  private subscription?: EventSubscription;
  private subscriptionRevision = 0;
  private timer?: number;
  private refreshAbort?: AbortController;
  private pendingRefresh?: Promise<void>;
  private stateRevision = 0;
  private lastStateStamp = -Infinity;

  constructor(readonly connection: Connection, private readonly listeners: SessionListeners) {}

  start(): void {
    if (this.closed || this.started) return;
    this.started = true;
    this.subscribe();
    void this.refresh().catch((error: Error) => { if (!this.closed) this.listeners.notice(error.message); });
    this.timer = window.setInterval(() => { void this.refresh().catch(() => undefined); }, 5000);
    window.addEventListener("offline", this.suspend);
    window.addEventListener("online", this.resume);
    document.addEventListener("visibilitychange", this.resume);
  }

  private subscribe(): void {
    if (!this.canObserve()) return;
    const revision = ++this.subscriptionRevision;
    const current = () => this.canObserve() && revision === this.subscriptionRevision;
    this.subscription = openEvents(this.connection, (state) => { if (current()) this.acceptState(state); }, () => {
      if (!current()) return;
      this.markDisconnected();
      void this.refresh(true).catch(() => undefined);
    });
  }

  private canObserve(): boolean {
    return !this.closed && navigator.onLine !== false && document.visibilityState !== "hidden";
  }

  private acceptState(state: RobotState): void {
    if (!this.canObserve()) return;
    const stamp = Date.parse(state.updatedAt);
    if (!Number.isFinite(stamp) || stamp < this.lastStateStamp) return;
    this.lastStateStamp = stamp;
    this.stateRevision += 1;
    this.liveState = state.connected === true;
    this.disconnectedReported = !this.liveState;
    this.listeners.state(state);
  }

  private markDisconnected(): void {
    if (this.closed) return;
    this.liveState = false;
    this.stateRevision += 1;
    if (!this.disconnectedReported) {
      this.disconnectedReported = true;
      this.listeners.disconnected();
    }
  }

  private retireObservers(): void {
    this.subscriptionRevision += 1;
    this.subscription?.close();
    this.subscription = undefined;
    this.refreshAbort?.abort();
  }

  private readonly suspend = (): void => {
    this.markDisconnected();
    this.retireObservers();
  };

  private readonly resume = (): void => {
    if (this.closed) return;
    this.suspend();
    if (!this.canObserve()) return;
    this.subscribe();
    // A suspended WebView may retain a pending fetch and an apparently open socket.
    void this.refresh(true).catch(() => undefined);
  };

  refresh(replace = false): Promise<void> {
    if (!this.canObserve()) return Promise.resolve();
    if (this.pendingRefresh && !replace) return this.pendingRefresh;
    this.refreshAbort?.abort();
    const owner = new AbortController();
    this.refreshAbort = owner;
    const revision = this.stateRevision;
    const signal = AbortSignal.any([owner.signal, this.abort.signal]);
    const pending = Promise.all([
      api<RobotState>(this.connection, "/api/v1/state", { signal }),
      api<PublicSettings>(this.connection, "/api/v1/settings", { signal }),
    ]).then(([state, settings]) => {
      if (!this.canObserve() || owner.signal.aborted) return;
      // A push received after this poll began takes precedence over its snapshot.
      if (revision === this.stateRevision) this.acceptState(state);
      this.listeners.settings(settings);
    }).catch((error: unknown) => {
      if (!this.canObserve() || owner.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) {
        this.close();
        this.listeners.expired();
      } else if (revision === this.stateRevision) {
        this.markDisconnected();
      }
      throw error;
    }).finally(() => {
      if (this.refreshAbort === owner) {
        this.refreshAbort = undefined;
        this.pendingRefresh = undefined;
      }
    });
    this.pendingRefresh = pending;
    return pending;
  }

  async run(path: string, body?: unknown, method = "POST", timeoutMs?: number): Promise<unknown> {
    if (this.closed) return;
    try {
      if (method !== "GET" && requiresLiveState(path, body) && (!this.liveState || !this.canObserve())) {
        throw new Error(staleControlMessage);
      }
      const result = await api<unknown>(this.connection, path, {
        method, signal: this.abort.signal,
        ...(body === undefined ? {} : { body: jsonBody(body) }),
      }, timeoutMs);
      if (this.closed) return;
      if (method !== "GET") {
        this.listeners.notice(path.includes("stop-save") ? "地图保存已确认。" : "请求已处理");
        await this.refresh(true);
      }
      return this.closed ? undefined : result;
    } catch (error) {
      if (this.closed) return;
      this.listeners.notice((error as Error).message);
      if (error instanceof ApiError && error.status === 401) {
        this.close();
        this.listeners.expired();
      }
      // Control requests are never replayed on reconnect, including uncertain outcomes.
      throw error;
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.abort.abort();
    this.retireObservers();
    if (this.timer !== undefined) window.clearInterval(this.timer);
    window.removeEventListener("offline", this.suspend);
    window.removeEventListener("online", this.resume);
    document.removeEventListener("visibilitychange", this.resume);
  }
}
