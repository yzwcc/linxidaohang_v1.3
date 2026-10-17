const inFlight = new Map();
const cache = new Map();

function defaultCacheMs(path) {
  if (path.includes("/maps/")) return 10000;
  if (path.endsWith("/maps")) return 15000;
  if (path.endsWith("/config")) return 60000;
  if (path.endsWith("/startup") || path.endsWith("/status")) return 2500;
  return 0;
}

export async function api(path, options = {}) {
  const {
    timeoutMs = options.method ? 30000 : 10000,
    cacheMs = defaultCacheMs(path),
    force = false,
    ...fetchOptions
  } = options;
  const method = String(fetchOptions.method || "GET").toUpperCase();
  const cacheKey = method === "GET" ? path : "";
  const cached = cacheKey ? cache.get(cacheKey) : null;
  if (!force && cached?.expiresAt > Date.now()) return cached.value;
  if (!force && cacheKey && inFlight.has(cacheKey)) return inFlight.get(cacheKey);

  const headers = { ...(options.headers || {}) };
  if (fetchOptions.body != null && !headers["content-type"] && !headers["Content-Type"]) {
    headers["content-type"] = "application/json";
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const request = (async () => {
    try {
      const response = await fetch(path, { ...fetchOptions, headers, signal: controller.signal });
      const text = await response.text();
      let body = {};
      if (text) {
        try {
          body = JSON.parse(text);
        } catch {
          body = { message: text.slice(0, 500) };
        }
      }
      if (!response.ok) {
        throw new Error(toChineseMessage(body.message || body.error || `请求失败：${response.status}`));
      }
      if (cacheKey && cacheMs > 0) cache.set(cacheKey, { value: body, expiresAt: Date.now() + cacheMs });
      if (method !== "GET") cache.clear();
      return body;
    } catch (error) {
      if (error?.name === "AbortError") {
        throw new Error(`操作等待超过 ${Math.round(timeoutMs / 1000)} 秒，请检查服务状态后重试`);
      }
      throw error;
    } finally {
      clearTimeout(timer);
      if (cacheKey && inFlight.get(cacheKey) === request) inFlight.delete(cacheKey);
    }
  })();
  if (cacheKey) inFlight.set(cacheKey, request);
  return request;
}

export function toChineseMessage(message) {
  const text = String(message || "");
  const rules = [
    [/Body cannot be empty/i, "请求内容为空，请刷新页面后重试"],
    [/Body is not valid JSON/i, "请求数据格式不正确"],
    [/unauthorized/i, "登录已失效，请重新进入"],
    [/target_name is required/i, "请选择导航点位"],
    [/active map is unavailable/i, "当前地图不可用"],
    [/current map has no points/i, "当前地图没有可用于重定位的点位"],
    [/底层定位未输出|slam_lidar_odom_publishers.*0|\/slam\/lidar_odom/i, "底层定位暂未输出，请确认机器人已退出建图状态并进入定位模式，必要时重启底层导航服务后再重定位"],
    [/navigation is/i, "机器人正在导航，请先停止导航"],
    [/This operation was aborted|aborted/i, "机器人本机服务响应超时，请稍后重试或点击一键启动"],
    [/Connection reset|ECONNRESET|reset by peer/i, "机器人本机服务连接被重置，请稍后重试"],
    [/fetch failed/i, "无法连接机器人本机服务，请确认设备与机器人在同一网络"],
    [/ECONNREFUSED|ETIMEDOUT|ENOTFOUND/i, "机器人服务未启动或暂未就绪，请点击一键启动全部服务"],
    [/required file not found/i, "语音服务文件缺失，请检查部署目录"],
    [/ROBOT_SSH_PASSWORD is required/i, "缺少机器人 SSH 密码，请检查后端环境变量"]
  ];
  const matched = rules.find(([pattern]) => pattern.test(text));
  return matched ? matched[1] : text;
}
