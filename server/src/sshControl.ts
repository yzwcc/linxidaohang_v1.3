import { Client } from "ssh2";
import fs from "node:fs";
import type { AppConfig } from "./config.js";
import { withRobotTransportSlot } from "./robotTransportLimiter.js";

export type BridgeAction = "start" | "stop" | "restart";

const actionToCommand: Record<BridgeAction, string> = {
  start: "./scripts/robot_local_navigation_v3.sh up",
  stop: "./scripts/robot_local_navigation_v3.sh stop",
  restart: "./scripts/robot_local_navigation_v3.sh stop && ./scripts/robot_local_navigation_v3.sh up"
};

export function isBridgeAction(value: string): value is BridgeAction {
  return value === "start" || value === "stop" || value === "restart";
}

export async function runBridgeAction(action: BridgeAction, config: AppConfig): Promise<{ action: BridgeAction; output: string }> {
  const command = `cd ${shellQuote(config.robot.navHome)} && ${actionToCommand[action]}`;
  const output = await runRobotSshCommand(command, config, 20000);
  return { action, output: output.trim() };
}

export async function restartRobotVoiceStack(config: AppConfig): Promise<{ output: string }> {
  const output = await runRobotSshCommand(shellQuote(config.robot.voiceRestartCommand), config, 90000);
  return { output: output.trim() };
}

export async function runRobotSshCommand(command: string, config: AppConfig, readyTimeout = 8000): Promise<string> {
  const privateKey = readPrivateKey(config.robot.sshPrivateKeyPath);
  if (!config.robot.sshPassword && !privateKey) {
    throw new Error("缺少机器人 SSH 凭据，请配置本机私钥或 SSH 密码");
  }
  return withRobotTransportSlot(() => new Promise((resolve, reject) => {
    const client = new Client();
    let output = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      client.end();
      reject(new Error(`机器人 SSH 命令执行超时（${readyTimeout}ms）：${command}`));
    }, readyTimeout);
    const fail = (error: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      client.end();
      reject(new Error(toChineseSshError(error, config)));
    };
    client.on("ready", () => {
      client.exec(command, (error, stream) => {
        if (error) {
          fail(error);
          return;
        }
        stream.on("close", (code: number) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          client.end();
          if (code === 0) {
            resolve(output);
          } else {
            reject(new Error(`机器人服务命令执行失败，退出码 ${code}：${output.trim() || "无输出"}`));
          }
        });
        stream.on("data", (data: Buffer) => {
          output += data.toString("utf8");
        });
        stream.stderr.on("data", (data: Buffer) => {
          output += data.toString("utf8");
        });
      });
    });
    client.on("error", fail);
    client.connect({
      host: config.robot.host,
      port: config.robot.sshPort,
      username: config.robot.sshUser,
      ...(privateKey ? { privateKey } : { password: config.robot.sshPassword }),
      readyTimeout
    });
  }), true);
}

export function runRobotSystemSshCommand(command: string, config: AppConfig, readyTimeout = 8000): Promise<string> {
  return runRobotSshCommand(command, {
    ...config,
    robot: {
      ...config.robot,
      sshUser: config.robot.systemSshUser,
      sshPassword: config.robot.systemSshPassword,
      sshPrivateKeyPath: config.robot.systemSshPrivateKeyPath,
      sshPort: config.robot.systemSshPort
    }
  }, readyTimeout);
}

function readPrivateKey(privateKeyPath: string | undefined): Buffer | undefined {
  if (!privateKeyPath) return undefined;
  try {
    return fs.readFileSync(privateKeyPath);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`无法读取机器人 SSH 私钥 ${privateKeyPath}：${message}`);
  }
}

function toChineseSshError(error: unknown, config: AppConfig): string {
  const message = error instanceof Error ? error.message : String(error);
  const target = `${config.robot.host}:${config.robot.sshPort}`;
  if (/Timed out while waiting for handshake|ready timeout|timeout/i.test(message)) {
    return `无法通过 SSH 连接机器人运维通道 ${target}：握手超时。请确认机器人已开机、SSH 可用，公网隧道/现场网关没有断开。`;
  }
  if (/ECONNRESET|reset/i.test(message)) {
    return `机器人运维通道 ${target} 已连接但被重置。公网隧道可能是旧连接，请在机器人侧重启反向隧道。`;
  }
  if (/ECONNREFUSED|refused/i.test(message)) {
    return `机器人运维通道 ${target} 未监听，请确认 SSH 服务或反向隧道已启动。`;
  }
  if (/All configured authentication methods failed|auth/i.test(message)) {
    return `机器人 SSH 认证失败，请检查用户名和密码配置。`;
  }
  return `机器人 SSH 操作失败：${message}`;
}

export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}
