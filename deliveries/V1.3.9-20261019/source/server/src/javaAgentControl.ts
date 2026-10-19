import { spawn } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import type { AppConfig } from "./config.js";
import { createRobotClient } from "./robotClient.js";
import { restartRobotVoiceStack, runBridgeAction, runRobotSystemSshCommand, shellQuote } from "./sshControl.js";

export type JavaAgentStatus = {
  configured: boolean;
  running: boolean;
  pid: number | null;
  home: string;
  navBaseUrl: string | null;
  expectedNavBaseUrl: string;
  navBaseUrlMatchesRobot: boolean | null;
  lingxiConnected: boolean;
  robotOnline: boolean;
  lastAuthState: string | null;
  lastStartedLine: string | null;
  logFile: string;
  recentLog: string;
  navigationService?: {
    checked: boolean;
    alreadyOnline: boolean;
    started: boolean;
    ok: boolean;
    message: string;
    output?: string;
  };
};

function resolveHome(config: AppConfig): string {
  return path.resolve(process.cwd(), config.robot.javaAgentHome);
}

function logFile(config: AppConfig): string {
  return path.resolve(process.cwd(), "logs", "java-agent-managed.log");
}

async function readTextIfExists(file: string): Promise<string> {
  try {
    return await fsp.readFile(file, "utf8");
  } catch {
    return "";
  }
}

async function readRecentLog(config: AppConfig): Promise<string> {
  const managed = await readTextIfExists(logFile(config));
  return redactLog(managed.split(/\r?\n/).slice(-80).join("\n"));
}

function isRobotOnline(recentLog: string): boolean {
  const startedAt = recentLog.lastIndexOf("Lingxi navigation agent started.");
  const onlineAt = recentLog.lastIndexOf("Robot online(");
  const offlineAt = recentLog.lastIndexOf("Robot offline(");
  return onlineAt > startedAt && onlineAt > offlineAt;
}

async function findJavaAgentPid(): Promise<number | null> {
  const command = process.platform === "win32"
    ? {
        file: "powershell.exe",
        args: [
          "-NoProfile",
          "-Command",
          "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'java*' -and $_.CommandLine -like '*com.agibot.aiem.nav.LingxiNavAgentApp*' } | Select-Object -First 1 -ExpandProperty ProcessId"
        ]
      }
    : {
        file: "pgrep",
        args: ["-f", "com.agibot.aiem.nav.LingxiNavAgentApp"]
      };
  const ps = spawn(command.file, command.args, { windowsHide: true });
  let out = "";
  ps.stdout.on("data", (data) => { out += data.toString(); });
  await new Promise((resolve) => ps.on("close", resolve));
  const pid = Number(out.trim().split(/\s+/)[0]);
  return Number.isFinite(pid) && pid > 0 ? pid : null;
}

function readProperties(file: string): Record<string, string> {
  if (!fs.existsSync(file)) return {};
  const props: Record<string, string> = {};
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const index = trimmed.indexOf("=");
    if (index <= 0) continue;
    props[trimmed.slice(0, index).trim()] = trimmed.slice(index + 1).trim();
  }
  return props;
}

export async function getJavaAgentStatus(config: AppConfig): Promise<JavaAgentStatus> {
  if (config.robot.javaAgentExecutionMode === "robot") {
    return getRobotJavaAgentStatus(config);
  }
  const home = resolveHome(config);
  const configFile = path.join(home, "config", "lingxi-nav.properties");
  const props = readProperties(configFile);
  const pid = await findJavaAgentPid();
  const expectedNavBaseUrl = `http://${config.robot.host}:${config.robot.bridgePort}`;
  const navBaseUrl = process.env.NAV_BASE_URL || props["nav.baseUrl"] || null;
  const recentLog = await readRecentLog(config);
  const authMatches = [...recentLog.matchAll(/Lingxi auth state: (?:appId=[^,\n]+,\s*)?code=([^,\n]+), msg=([^\n]+)/g)];
  const startedMatches = [...recentLog.matchAll(/Lingxi navigation agent started\. ([^\n]+)/g)];
  const lastAuthState = authMatches.length ? `code=${authMatches.at(-1)?.[1]}, msg=${authMatches.at(-1)?.[2]}` : null;
  const lastStartedLine = startedMatches.length ? startedMatches.at(-1)?.[1] || null : null;
  const logNavBaseUrl = lastStartedLine?.match(/navBaseUrl=([^,\s]+)/)?.[1] || null;
  const effectiveNavBaseUrl = logNavBaseUrl || navBaseUrl;
  return {
    configured: fs.existsSync(configFile),
    running: pid != null,
    pid,
    home,
    navBaseUrl: effectiveNavBaseUrl,
    expectedNavBaseUrl,
    navBaseUrlMatchesRobot: effectiveNavBaseUrl ? effectiveNavBaseUrl.replace(/\/$/, "") === expectedNavBaseUrl : null,
    lingxiConnected: pid != null && /Lingxi auth state: (?:appId=[^,\n]+,\s*)?code=0/.test(recentLog),
    robotOnline: pid != null && isRobotOnline(recentLog),
    lastAuthState,
    lastStartedLine,
    logFile: logFile(config),
    recentLog
  };
}

async function getRobotJavaAgentStatus(config: AppConfig): Promise<JavaAgentStatus> {
const controlScript = `${config.robot.robotJavaAgentHome}/java_agent_control.sh`;
  const output = await runRobotSystemSshCommand(`${shellQuote(controlScript)} status`, config, 25000);
  const marker = "\n---LOG---\n";
  const markerIndex = output.indexOf(marker);
  const header = markerIndex >= 0 ? output.slice(0, markerIndex) : output;
  const recentLog = redactLog(markerIndex >= 0 ? output.slice(markerIndex + marker.length) : "");
  const fields: Record<string, string> = {};
  for (const line of header.split(/\r?\n/)) {
    const index = line.indexOf("=");
    if (index > 0) fields[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  const authMatches = [...recentLog.matchAll(/Lingxi auth state: (?:appId=[^,\n]+,\s*)?code=([^,\n]+), msg=([^\n]+)/g)];
  const startedMatches = [...recentLog.matchAll(/Lingxi navigation agent started\. ([^\n]+)/g)];
  const lastAuthState = fields.last_auth_state
    || (authMatches.length ? `code=${authMatches.at(-1)?.[1]}, msg=${authMatches.at(-1)?.[2]}` : null);
  const lastStartedLine = fields.last_started_line
    || (startedMatches.length ? startedMatches.at(-1)?.[1] || null : null);
  const navBaseUrl = lastStartedLine?.match(/navBaseUrl=([^,\s]+)/)?.[1] || fields.nav_base_url || null;
  const expectedNavBaseUrl = `http://127.0.0.1:${config.robot.robotJavaAgentBridgePort}`;
  const running = fields.running === "true";
  const pid = Number(fields.pid);
  return {
    configured: fields.configured === "true",
    running,
    pid: running && Number.isFinite(pid) && pid > 0 ? pid : null,
    home: fields.home || config.robot.robotJavaAgentHome,
    navBaseUrl,
    expectedNavBaseUrl,
    navBaseUrlMatchesRobot: navBaseUrl ? navBaseUrl.replace(/\/$/, "") === expectedNavBaseUrl : null,
    lingxiConnected: running && (fields.lingxi_connected === "true"
      || (!fields.lingxi_connected && /Lingxi auth state: (?:appId=[^,\n]+,\s*)?code=0/.test(recentLog))),
    robotOnline: running && (fields.robot_online === "true"
      || (!fields.robot_online && isRobotOnline(recentLog))),
    lastAuthState,
    lastStartedLine,
    logFile: fields.log_file || `${config.robot.robotJavaAgentHome}/logs/java-agent-managed.log`,
    recentLog
  };
}

function redactLog(text: string): string {
  return text
    .replace(/AGIBOT_AUTHORIZATION:\s*Bearer\s+[^,\]\s]+/g, "AGIBOT_AUTHORIZATION: Bearer <redacted>")
    .replace(/Bearer\s+[A-Za-z0-9._-]+/g, "Bearer <redacted>")
    .replace(/(appSecret|APP_SECRET|doubao\.apiKey|ARK_API_KEY|DOUBAO_API_KEY)=([^,\s]+)/gi, "$1=<redacted>");
}

async function ensureNavigationServices(config: AppConfig): Promise<NonNullable<JavaAgentStatus["navigationService"]>> {
  const robot = createRobotClient(config);
  const status = await robot.status();
  if (status.bridge_online && status.map_web_online) {
    return {
      checked: true,
      alreadyOnline: true,
      started: false,
      ok: true,
      message: "机器人导航层和地图服务已在线"
    };
  }

  const result = await runBridgeAction("start", config);
  for (let i = 0; i < 12; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const nextStatus = await robot.status();
    if (nextStatus.bridge_online && nextStatus.map_web_online) {
      return {
        checked: true,
        alreadyOnline: false,
        started: true,
        ok: true,
        message: "已先启动机器人导航层和地图服务",
        output: result.output
      };
    }
  }

  return {
    checked: true,
    alreadyOnline: false,
    started: true,
    ok: false,
    message: "已提交机器人导航层启动命令，但等待服务在线超时",
    output: result.output
  };
}

export async function startJavaAgent(config: AppConfig): Promise<JavaAgentStatus> {
  if (config.robot.javaAgentExecutionMode === "robot") {
    const navigationService = await ensureNavigationServices(config);
    if (!navigationService.ok) {
      throw new Error(`${navigationService.message}：${navigationService.output || "无输出"}`);
    }
    const current = await getRobotJavaAgentStatus(config);
const controlScript = `${config.robot.robotJavaAgentHome}/java_agent_control.sh`;
    if (!current.running) {
      await runRobotSystemSshCommand(`${shellQuote(controlScript)} start`, config, 20000);
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
    return { ...(await getRobotJavaAgentStatus(config)), navigationService };
  }
  const home = resolveHome(config);
  const configFile = path.join(home, "config", "lingxi-nav.properties");
  const appJar = path.join(home, "target", "robot-local-navigation-java-0.1.0-all.jar");
  const sdkJar = path.join(
    home,
    process.env.LINGXI_AGENT_SDK_JAR ?? "linksoul-agentsdk-1.4.0-SNAPSHOT.jar"
  );
  for (const file of [configFile, appJar, sdkJar]) {
    if (!fs.existsSync(file)) {
      throw new Error(`Java agent required file not found: ${file}`);
    }
  }
  const navigationService = await ensureNavigationServices(config);
  if (!navigationService.ok) {
    throw new Error(`${navigationService.message}：${navigationService.output || "无输出"}`);
  }
  await stopJavaAgent(config);
  await fsp.mkdir(path.dirname(logFile(config)), { recursive: true });
  await fsp.writeFile(logFile(config), `Managed Java Agent start: ${new Date().toISOString()}\n`, "utf8");
  const out = fs.openSync(logFile(config), "a");
  const err = fs.openSync(logFile(config), "a");
  const child = spawn("java", [
    "-cp",
    [appJar, sdkJar].join(path.delimiter),
    "com.agibot.aiem.nav.LingxiNavAgentApp",
    "--config",
    configFile
  ], {
    cwd: home,
    detached: true,
    stdio: ["ignore", out, err],
    windowsHide: true,
    env: {
      ...process.env,
      NAV_BASE_URL: `http://${config.robot.host}:${config.robot.bridgePort}`
    }
  });
  child.unref();
  await new Promise((resolve) => setTimeout(resolve, 1500));
  return {
    ...(await getJavaAgentStatus(config)),
    navigationService
  };
}

export async function startJavaAgentAndRebindRobotVoice(
  config: AppConfig,
  timeoutMs = 20000,
  robotOnlineTimeoutMs = 90000
): Promise<JavaAgentStatus & { robotVoice: { output: string } }> {
  await startJavaAgent(config);
  const startedAt = Date.now();
  let status = await getJavaAgentStatus(config);
  while (Date.now() - startedAt < timeoutMs && !(status.running && status.lingxiConnected)) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    status = await getJavaAgentStatus(config);
  }
  if (!(status.running && status.lingxiConnected)) {
    throw new Error(`灵心平台连接等待超时：${status.lastAuthState || "尚未收到认证成功状态"}`);
  }
  if (status.robotOnline) {
    return { ...status, robotVoice: { output: "机器人语音回调已在线，无需重复重载" } };
  }
  const robotVoice = await restartRobotVoiceStack(config);
  const onlineStartedAt = Date.now();
  while (Date.now() - onlineStartedAt < robotOnlineTimeoutMs && !status.robotOnline) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    status = await getJavaAgentStatus(config);
  }
  if (!status.robotOnline) {
    throw new Error("机器人本机语音服务已启动，但等待灵心语音回调上线超时");
  }
  return { ...status, robotVoice };
}

export async function stopJavaAgent(config: AppConfig): Promise<JavaAgentStatus> {
  if (config.robot.javaAgentExecutionMode === "robot") {
const controlScript = `${config.robot.robotJavaAgentHome}/java_agent_control.sh`;
    await runRobotSystemSshCommand(`${shellQuote(controlScript)} stop`, config, 12000);
    return getRobotJavaAgentStatus(config);
  }
  if (process.platform === "win32") {
    await new Promise<void>((resolve) => {
      const ps = spawn("powershell.exe", [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'java*' -and $_.CommandLine -like '*com.agibot.aiem.nav.LingxiNavAgentApp*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
      ], { windowsHide: true });
      ps.on("close", () => resolve());
    });
  } else {
    await new Promise<void>((resolve) => {
      const ps = spawn("pkill", ["-f", "com.agibot.aiem.nav.LingxiNavAgentApp"]);
      ps.on("close", () => resolve());
      ps.on("error", () => resolve());
    });
  }
  await new Promise((resolve) => setTimeout(resolve, 800));
  return getJavaAgentStatus(config);
}
