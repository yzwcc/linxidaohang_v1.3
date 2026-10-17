import type { AppConfig } from "./config.js";
import { runRobotSystemSshCommand } from "./sshControl.js";

export type TunnelTargetIdentity = {
  ok: boolean;
  expectedHost: string;
  hostname: string;
  addresses: string[];
  detail: string;
  error?: string;
};

let cachedIdentity: { expectedHost: string; checkedAt: number; value: TunnelTargetIdentity } | null = null;
let lastVerifiedIdentity: { expectedHost: string; checkedAt: number; value: TunnelTargetIdentity } | null = null;

export function parseTunnelIdentity(output: string): { hostname: string; addresses: string[] } {
  const lines = output.split(/\r?\n/);
  const hostname = lines.find((line) => line.startsWith("hostname="))?.slice("hostname=".length).trim() || "";
  const addresses = (lines.find((line) => line.startsWith("addresses="))?.slice("addresses=".length) || "")
    .trim()
    .split(/\s+/)
    .map((address) => address.split("/")[0])
    .filter(Boolean);
  return { hostname, addresses };
}

export function invalidateTunnelTargetIdentity(): void {
  cachedIdentity = null;
  lastVerifiedIdentity = null;
}

export async function verifyTunnelTarget(
  config: AppConfig,
  expectedHost: string,
  options: { force?: boolean; cacheMs?: number } = {}
): Promise<TunnelTargetIdentity> {
  const expected = expectedHost.trim();
  if (config.robot.connectionMode !== "tunnel") {
    return {
      ok: true,
      expectedHost: config.robot.host,
      hostname: config.robot.host,
      addresses: [config.robot.host],
      detail: "当前为直连模式"
    };
  }
  if (!expected) {
    return { ok: false, expectedHost: "", hostname: "", addresses: [], detail: "尚未填写目标机器人 IP" };
  }

  const now = Date.now();
  const cacheMs = options.cacheMs ?? (cachedIdentity?.value.ok ? 300000 : 5000);
  if (!options.force && cachedIdentity?.expectedHost === expected && now - cachedIdentity.checkedAt < cacheMs) {
    return cachedIdentity.value;
  }

  let value: TunnelTargetIdentity;
  try {
    const output = await runRobotSystemSshCommand(
      "printf 'hostname='; hostname; printf 'addresses='; hostname -I",
      config,
      15000
    );
    const identity = parseTunnelIdentity(output);
    const ok = identity.addresses.includes(expected) || identity.hostname === expected;
    value = {
      ok,
      expectedHost: expected,
      hostname: identity.hostname,
      addresses: identity.addresses,
      detail: ok
        ? `隧道已连接目标机器人 ${expected}`
        : `隧道当前连接 ${identity.addresses.join("、") || identity.hostname || "未知机器人"}，不是 ${expected}`,
      ...(ok ? {} : { error: "隧道目标与网页填写的机器人 IP 不一致" })
    };
    if (ok) lastVerifiedIdentity = { expectedHost: expected, checkedAt: now, value };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const previous = lastVerifiedIdentity;
    value = previous?.expectedHost === expected && now - previous.checkedAt < 120000
      ? {
          ...previous.value,
          detail: `隧道已连接目标机器人 ${expected}，当前复检繁忙，沿用最近核验结果`,
          error: message
        }
      : {
          ok: false,
          expectedHost: expected,
          hostname: "",
          addresses: [],
          detail: `无法核验隧道目标 ${expected}`,
          error: message
        };
  }
  cachedIdentity = { expectedHost: expected, checkedAt: now, value };
  return value;
}

export async function waitForTunnelTarget(
  config: AppConfig,
  expectedHost: string,
  timeoutMs = 45000
): Promise<TunnelTargetIdentity> {
  const deadline = Date.now() + timeoutMs;
  let identity = await verifyTunnelTarget(config, expectedHost, { force: true, cacheMs: 0 });
  while (!identity.ok && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    identity = await verifyTunnelTarget(config, expectedHost, { force: true, cacheMs: 0 });
  }
  return identity;
}
