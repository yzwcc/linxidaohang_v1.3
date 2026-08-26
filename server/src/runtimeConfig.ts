import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import type { AppConfig } from "./config.js";
import { cloneConfigWithRobotHost } from "./config.js";

const runtimeSchema = z.object({
  robotHost: z.string().trim().min(1).optional()
});

const runtimeFile = path.resolve(process.cwd(), "data", "runtime-config.json");

export function loadRuntimeConfig(baseConfig: AppConfig): AppConfig {
  try {
    if (baseConfig.deploymentMode === "robot") return baseConfig;
    if (baseConfig.robot.connectionMode === "tunnel") return baseConfig;
    if (!fs.existsSync(runtimeFile)) return baseConfig;
    const parsed = runtimeSchema.parse(JSON.parse(fs.readFileSync(runtimeFile, "utf8")));
    return parsed.robotHost ? cloneConfigWithRobotHost(baseConfig, parsed.robotHost) : baseConfig;
  } catch {
    return baseConfig;
  }
}

export function loadRuntimeRobotHost(): string {
  try {
    if (!fs.existsSync(runtimeFile)) return "";
    const parsed = runtimeSchema.parse(JSON.parse(fs.readFileSync(runtimeFile, "utf8")));
    return parsed.robotHost || "";
  } catch {
    return "";
  }
}

export async function saveRobotHost(baseConfig: AppConfig, robotHost: string): Promise<AppConfig> {
  if (baseConfig.deploymentMode === "robot") {
    throw new Error("机器人本机模式不允许修改目标 IP，请在浏览器地址栏输入当前机器人 IP");
  }
  const host = robotHost.trim();
  if (!/^[a-zA-Z0-9_.:-]+$/.test(host)) {
    throw new Error("机器人 IP 或主机名只能包含字母、数字、点、冒号、下划线和横线");
  }
  await fsp.mkdir(path.dirname(runtimeFile), { recursive: true });
  await fsp.writeFile(runtimeFile, JSON.stringify({ robotHost: host }, null, 2), "utf8");
  return cloneConfigWithRobotHost(baseConfig, host);
}

export function runtimeConfigPath(): string {
  return runtimeFile;
}
