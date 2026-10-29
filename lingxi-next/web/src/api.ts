import type {
  PublicSettings,
  LidarEvent,
  RobotMap,
  RobotState,
  TourTask,
  VoiceEvent,
  VoiceReply,
} from "../../shared/contracts";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export type Connection = {
  baseUrl: string;
  token: string;
  demo: boolean;
};

export type EventSubscription = { close(): void };

function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();
  const withScheme = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  const url = new URL(withScheme);
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error("请输入机器人 IP 和端口");
  if (url.protocol !== "https:" && !(import.meta.env.DEV && ["localhost", "127.0.0.1"].includes(url.hostname))) {
    throw new Error("机器人连接必须使用 HTTPS");
  }
  return url.origin;
}

export async function pairRobot(address: string, code: string): Promise<Connection> {
  const baseUrl = normalizeBaseUrl(address);
  const response = await fetch(`${baseUrl}/api/v1/session/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairingCode: code }),
    signal: AbortSignal.timeout(10_000),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.message ?? "无法与机器人完成配对", response.status);
  const connection = {
    baseUrl,
    token: String(body.token),
    demo: Boolean(body.demoMode),
  };
  localStorage.setItem("lingxi.robotAddress", baseUrl);
  return connection;
}

export async function api<T>(
  connection: Connection,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${connection.token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${connection.baseUrl}${path}`, {
    signal: AbortSignal.timeout(path.includes("stop-save") ? 130_000
      : path.includes("stop-discard") ? 110_000 : path.includes("activate") ? 35_000 : 10_000),
    ...init, headers,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.message ?? "机器人请求失败", response.status);
  return body as T;
}

export function openEvents(
  connection: Connection,
  onState: (state: RobotState) => void,
  onError: () => void,
): EventSubscription {
  const target = new URL("/api/v1/events", connection.baseUrl);
  target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
  let socket: WebSocket | undefined;
  let retryTimer: number | undefined;
  let stableTimer: number | undefined;
  let attempt = 0;
  let closed = false;

  const scheduleReconnect = () => {
    if (closed || retryTimer !== undefined) return;
    const delay = Math.min(500 * 2 ** Math.min(attempt, 5), 15_000);
    attempt += 1;
    retryTimer = window.setTimeout(() => {
      retryTimer = undefined;
      connect();
    }, delay);
  };

  const connect = () => {
    if (closed) return;
    let current: WebSocket;
    try {
      current = new WebSocket(target, ["lingxi-v1", `bearer.${connection.token}`]);
    } catch {
      onError();
      scheduleReconnect();
      return;
    }
    socket = current;
    let disconnected = false;
    const disconnect = () => {
      if (closed || disconnected) return;
      disconnected = true;
      if (stableTimer !== undefined) window.clearTimeout(stableTimer);
      stableTimer = undefined;
      onError();
      scheduleReconnect();
      try { current.close(); } catch { /* The socket may already be closed. */ }
    };
    current.addEventListener("open", () => {
      if (closed || socket !== current) return;
      stableTimer = window.setTimeout(() => { attempt = 0; stableTimer = undefined; }, 30_000);
    }, { once: true });
    current.addEventListener("message", (event) => {
      if (closed || socket !== current) return;
      try {
        const payload = JSON.parse(String(event.data)) as { type?: string; state?: RobotState };
        if (payload.type === "state" && payload.state) onState(payload.state);
      } catch {
        // Ignore malformed events and recover with the next full snapshot.
      }
    });
    current.addEventListener("close", disconnect, { once: true });
    current.addEventListener("error", disconnect, { once: true });
  };

  connect();
  return {
    close() {
      closed = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      if (stableTimer !== undefined) window.clearTimeout(stableTimer);
      retryTimer = undefined;
      stableTimer = undefined;
      socket?.close();
    },
  };
}

export function openLidarEvents(
  connection: Connection,
  onEvent: (event: LidarEvent) => void,
  onError: () => void,
): EventSubscription {
  const target = new URL("/api/v1/lidar/events", connection.baseUrl);
  target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
  let socket: WebSocket | undefined;
  let retryTimer: number | undefined;
  let stableTimer: number | undefined;
  let livenessTimer: number | undefined;
  let attempt = 0;
  let closed = false;

  const clearLivenessTimer = () => {
    if (livenessTimer !== undefined) window.clearTimeout(livenessTimer);
    livenessTimer = undefined;
  };

  const scheduleReconnect = () => {
    if (closed || retryTimer !== undefined) return;
    retryTimer = window.setTimeout(() => {
      retryTimer = undefined;
      connect();
    }, Math.min(500 * 2 ** Math.min(attempt++, 5), 15_000));
  };

  const connect = () => {
    if (closed) return;
    let current: WebSocket;
    try {
      current = new WebSocket(target, ["lingxi-v1", `bearer.${connection.token}`]);
    } catch {
      onError();
      scheduleReconnect();
      return;
    }
    socket = current;
    let disconnected = false;
    const scheduleLivenessCheck = () => {
      clearLivenessTimer();
      livenessTimer = window.setTimeout(disconnect, 2500);
    };
    const disconnect = () => {
      if (closed || disconnected || socket !== current) return;
      disconnected = true;
      if (stableTimer !== undefined) window.clearTimeout(stableTimer);
      stableTimer = undefined;
      clearLivenessTimer();
      onError();
      scheduleReconnect();
      try { current.close(); } catch { /* The socket may already be closed. */ }
    };
    current.addEventListener("open", () => {
      if (closed || socket !== current) return;
      scheduleLivenessCheck();
      stableTimer = window.setTimeout(() => {
        attempt = 0;
        stableTimer = undefined;
      }, 30_000);
    }, { once: true });
    current.addEventListener("message", (event) => {
      if (closed || disconnected || socket !== current) return;
      try {
        const payload = JSON.parse(String(event.data)) as LidarEvent;
        if (payload.type !== "lidar" && payload.type !== "lidar-unavailable") return;
        scheduleLivenessCheck();
        onEvent(payload);
      } catch { disconnect(); }
    });
    current.addEventListener("close", disconnect, { once: true });
    current.addEventListener("error", disconnect, { once: true });
  };

  connect();
  return {
    close() {
      closed = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      if (stableTimer !== undefined) window.clearTimeout(stableTimer);
      clearLivenessTimer();
      retryTimer = undefined;
      stableTimer = undefined;
      socket?.close();
    },
  };
}

export const jsonBody = (value: unknown) => JSON.stringify(value);

export type { PublicSettings, RobotMap, RobotState, TourTask, VoiceEvent, VoiceReply };
