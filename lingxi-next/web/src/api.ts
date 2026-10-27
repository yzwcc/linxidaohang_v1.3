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
    signal: AbortSignal.timeout(path.includes("stop-save") ? 130_000 : path.includes("activate") ? 35_000 : 10_000),
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
): WebSocket {
  const target = new URL("/api/v1/events", connection.baseUrl);
  target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(target, ["lingxi-v1", `bearer.${connection.token}`]);
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as { type?: string; state?: RobotState };
      if (payload.type === "state" && payload.state) onState(payload.state);
    } catch {
      // Ignore malformed events and recover with the next full snapshot.
    }
  });
  socket.addEventListener("close", onError);
  socket.addEventListener("error", onError);
  return socket;
}

export function openLidarEvents(connection: Connection, onEvent: (event: LidarEvent) => void, onError: () => void): WebSocket {
  const target = new URL("/api/v1/lidar/events", connection.baseUrl);
  target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(target, ["lingxi-v1", `bearer.${connection.token}`]);
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as LidarEvent;
      if (payload.type === "lidar" || payload.type === "lidar-unavailable") onEvent(payload);
    } catch { onError(); }
  });
  socket.addEventListener("close", onError);
  return socket;
}

export const jsonBody = (value: unknown) => JSON.stringify(value);

export type { PublicSettings, RobotMap, RobotState, TourTask, VoiceEvent, VoiceReply };
