import net from "node:net";
import { Client } from "ssh2";
import type { AppConfig } from "./config.js";
import { requestJson } from "./http.js";
import { withRobotTransportSlot } from "./robotTransportLimiter.js";
import { verifyTunnelTarget, type TunnelTargetIdentity } from "./tunnelTarget.js";
import { runRobotSshCommand, shouldExecuteRobotCommandLocally } from "./sshControl.js";

type CheckResult = {
  ok: boolean;
  detail: string;
  error?: string;
};

export type TunnelDiagnostics = {
  ok: boolean;
  mode: "direct" | "tunnel";
  targetHost: string;
  proxyHost: string;
  bridge: CheckResult;
  mapWeb: CheckResult;
  ssh: CheckResult;
  targetIdentity: TunnelTargetIdentity;
  advice: string[];
};

function readableError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/aborted/i.test(message)) return "连接超时或被中断";
  if (/reset/i.test(message)) return "连接被机器人侧重置";
  if (/refused/i.test(message)) return "端口未监听或服务未启动";
  if (/timed out|timeout/i.test(message)) return "连接超时";
  if (/fetch failed/i.test(message)) return "无法建立连接";
  return message;
}

function tcpCheck(host: string, port: number, timeoutMs = 2500): Promise<CheckResult> {
  return withRobotTransportSlot(() => new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    const timer = setTimeout(() => {
      socket.destroy();
      resolve({ ok: false, detail: `无法连接 ${host}:${port}`, error: "连接超时" });
    }, timeoutMs);

    socket.once("connect", () => {
      clearTimeout(timer);
      socket.end();
      resolve({ ok: true, detail: `${host}:${port} 可连接` });
    });
    socket.once("error", (error) => {
      clearTimeout(timer);
      resolve({ ok: false, detail: `无法连接 ${host}:${port}`, error: readableError(error) });
    });
  }));
}

function sshHandshakeCheck(config: AppConfig, timeoutMs = 15000): Promise<CheckResult> {
  if (shouldExecuteRobotCommandLocally(config)) {
    return runRobotSshCommand("true", config, 5000).then(
      () => ({ ok: true, detail: "机器人本机运维账号可执行命令" }),
      (error) => ({ ok: false, detail: "机器人本机运维账号不可用", error: readableError(error) })
    );
  }
  if (!config.robot.sshPassword) {
    return tcpCheck(config.robot.host, config.robot.sshPort, timeoutMs);
  }
  return new Promise((resolve) => {
    const client = new Client();
    let settled = false;
    let timer: NodeJS.Timeout | null = null;
    const finish = (result: CheckResult) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      client.end();
      resolve(result);
    };
    timer = setTimeout(() => {
      finish({
        ok: false,
        detail: `SSH ${config.robot.host}:${config.robot.sshPort} 握手超时`,
        error: "握手超时"
      });
    }, timeoutMs);

    client.once("ready", () => {
      finish({ ok: true, detail: `SSH ${config.robot.host}:${config.robot.sshPort} 可登录` });
    });
    client.on("error", (error) => {
      finish({
        ok: false,
        detail: `SSH ${config.robot.host}:${config.robot.sshPort} 不可用`,
        error: readableError(error)
      });
    });
    client.connect({
      host: config.robot.host,
      port: config.robot.sshPort,
      username: config.robot.sshUser,
      password: config.robot.sshPassword,
      readyTimeout: timeoutMs
    });
  });
}

async function httpCheck(url: string, label: string): Promise<CheckResult> {
  try {
    await requestJson(url, { timeoutMs: 3000 });
    return { ok: true, detail: `${label} 已响应` };
  } catch (error) {
    return {
      ok: false,
      detail: `${label} 未响应`,
      error: readableError(error)
    };
  }
}

export async function getTunnelDiagnostics(config: AppConfig, targetHost = ""): Promise<TunnelDiagnostics> {
  const bridgeUrl = `http://${config.robot.host}:${config.robot.bridgePort}/nav/status`;
  const mapUrl = `http://${config.robot.host}:${config.robot.webPort}/api/discover_maps?map_root=${encodeURIComponent(config.robot.mapRoot)}`;
  const [bridge, mapWeb, ssh, targetIdentity] = await Promise.all([
    httpCheck(bridgeUrl, "导航桥接"),
    httpCheck(mapUrl, "地图服务"),
    sshHandshakeCheck(config),
    verifyTunnelTarget(config, targetHost)
  ]);
  const ok = bridge.ok && mapWeb.ok && ssh.ok && targetIdentity.ok;
  const advice: string[] = [];

  if (!bridge.ok || !mapWeb.ok) {
    if (config.robot.connectionMode === "tunnel") {
      advice.push("公网隧道端口存在但业务转发失败时，请在机器人侧重启反向隧道，并确认填写的机器人 IP 是当前现场局域网 IP。");
    } else {
      advice.push("请确认后端服务器和机器人在同一网络内，并且机器人导航层、地图服务已启动。");
    }
  }
  if (!ssh.ok) {
    advice.push("SSH 运维通道不可用时，网页无法远程启动机器人导航层，请先恢复机器人 SSH 或现场网关隧道。");
  }
  if (!targetIdentity.ok) {
    advice.push(`${targetIdentity.detail}。为防止误操作，网站已禁止向当前隧道发送控制命令。`);
  }
  if (ok) {
    advice.push("网络通道正常，可以继续启动导航层、切换地图和重定位。");
  }

  return {
    ok,
    mode: config.robot.connectionMode,
    targetHost: config.robot.connectionMode === "tunnel" ? targetHost : config.robot.host,
    proxyHost: config.robot.host,
    bridge,
    mapWeb,
    ssh,
    targetIdentity,
    advice
  };
}
