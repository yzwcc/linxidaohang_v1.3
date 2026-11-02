import { api, ApiError, jsonBody, openEvents, type Connection, type EventSubscription,
  type PublicSettings, type RobotState } from "./api";

type SessionListeners = {
  state(state: RobotState): void;
  settings(settings: PublicSettings): void;
  disconnected(): void;
  expired(): void;
  notice(message: string): void;
};

export class RobotSession {
  private closed = false;
  private started = false;
  private readonly abort = new AbortController();
  private subscription?: EventSubscription;
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
    window.addEventListener("online", this.resume);
    document.addEventListener("visibilitychange", this.resume);
  }

  private subscribe(): void {
    this.subscription = openEvents(this.connection, (state) => this.acceptState(state), () => {
      if (this.closed) return;
      this.stateRevision += 1;
      this.listeners.disconnected();
      void this.refresh().catch(() => undefined);
    });
  }

  private acceptState(state: RobotState): void {
    if (this.closed) return;
    const stamp = Date.parse(state.updatedAt);
    if (Number.isFinite(stamp) && stamp < this.lastStateStamp) return;
    if (Number.isFinite(stamp)) this.lastStateStamp = stamp;
    this.stateRevision += 1;
    this.listeners.state(state);
  }

  private readonly resume = (): void => {
    if (this.closed || document.visibilityState === "hidden") return;
    this.stateRevision += 1;
    this.listeners.disconnected();
    this.subscription?.close();
    this.subscribe();
    // A suspended WebView may retain a pending fetch and an apparently open socket.
    void this.refresh(true).catch(() => undefined);
  };

  refresh(replace = false): Promise<void> {
    if (this.closed) return Promise.resolve();
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
      if (this.closed || owner.signal.aborted) return;
      // A push received after this poll began takes precedence over its snapshot.
      if (revision === this.stateRevision) this.acceptState(state);
      this.listeners.settings(settings);
    }).catch((error: unknown) => {
      if (this.closed || owner.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) {
        this.close();
        this.listeners.expired();
      } else if (revision === this.stateRevision) {
        this.stateRevision += 1;
        this.listeners.disconnected();
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

  async run(path: string, body?: unknown, method = "POST"): Promise<unknown> {
    if (this.closed) return;
    try {
      const result = await api<unknown>(this.connection, path, {
        method, signal: this.abort.signal,
        ...(body === undefined ? {} : { body: jsonBody(body) }),
      });
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
    this.refreshAbort?.abort();
    this.subscription?.close();
    if (this.timer !== undefined) window.clearInterval(this.timer);
    window.removeEventListener("online", this.resume);
    document.removeEventListener("visibilitychange", this.resume);
  }
}
