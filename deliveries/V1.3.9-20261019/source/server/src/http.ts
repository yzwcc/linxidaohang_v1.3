import { withRobotTransportSlot } from "./robotTransportLimiter.js";

export class UpstreamError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
    public readonly upstreamBody?: unknown
  ) {
    super(message);
  }
}

export async function requestJson<T>(
  url: string,
  options: RequestInit & { timeoutMs?: number } = {}
): Promise<T> {
  const priority = Boolean(options.method && options.method.toUpperCase() !== "GET");
  return withRobotTransportSlot(() => requestJsonNow<T>(url, options), priority);
}

async function requestJsonNow<T>(
  url: string,
  options: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? 5000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body ? { "content-type": "application/json; charset=utf-8" } : {}),
        ...(options.headers || {})
      }
    });
    const text = await response.text();
    let body: unknown = {};
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = { message: text.slice(0, 500) };
      }
    }
    if (!response.ok) {
      throw new UpstreamError(`Upstream HTTP ${response.status}`, response.status, body);
    }
    return body as T;
  } catch (error) {
    if (error instanceof UpstreamError) throw error;
    const message = error instanceof Error && error.name === "AbortError"
      ? `机器人本机服务响应超时（${timeoutMs}ms）`
      : (error instanceof Error ? error.message : String(error));
    throw new UpstreamError(message, 502);
  } finally {
    clearTimeout(timer);
  }
}

export async function requestBuffer(url: string, timeoutMs = 5000): Promise<{ data: Buffer; contentType: string }> {
  return withRobotTransportSlot(() => requestBufferNow(url, timeoutMs));
}

async function requestBufferNow(url: string, timeoutMs: number): Promise<{ data: Buffer; contentType: string }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new UpstreamError(`Upstream HTTP ${response.status}`, response.status);
    }
    return {
      data: Buffer.from(await response.arrayBuffer()),
      contentType: response.headers.get("content-type") || "application/octet-stream"
    };
  } catch (error) {
    if (error instanceof UpstreamError) throw error;
    const message = error instanceof Error && error.name === "AbortError"
      ? `机器人地图图片响应超时（${timeoutMs}ms）`
      : (error instanceof Error ? error.message : String(error));
    throw new UpstreamError(message, 502);
  } finally {
    clearTimeout(timer);
  }
}
