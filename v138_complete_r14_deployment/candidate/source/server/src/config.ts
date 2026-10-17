export type AppConfig = {
  deploymentMode: "cloud" | "robot";
  host: string;
  port: number;
  publicBaseUrl: string;
  robot: {
    id: "default";
    connectionMode: "direct" | "tunnel";
    host: string;
    bridgePort: number;
    webPort: number;
    mapRoot: string;
    sshUser: string;
    sshPassword: string;
    sshPrivateKeyPath?: string;
    sshPort: number;
    systemSshUser: string;
    systemSshPassword: string;
    systemSshPrivateKeyPath?: string;
    systemSshPort: number;
    navHome: string;
    javaAgentHome: string;
    javaAgentExecutionMode: "local" | "robot";
    robotJavaAgentHome: string;
    robotJavaAgentBridgePort: number;
    voiceRestartCommand: string;
    javaAgentAutoStart: boolean;
  };
  auth: {
    appId: string;
    appSecret: string;
    tokenSecret: string;
    devBypass: boolean;
    tokenTtlSec: number;
  };
};

export function cloneConfigWithRobotHost(config: AppConfig, host: string): AppConfig {
  return {
    ...config,
    robot: {
      ...config.robot,
      host
    }
  };
}

function readNumber(name: string, fallback: number): number {
  const value = process.env[name];
  if (!value) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${name} must be a number`);
  }
  return parsed;
}

function readBoolean(name: string, fallback: boolean): boolean {
  const value = process.env[name];
  if (value == null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

export function loadConfig(): AppConfig {
  const deploymentMode = process.env.DEPLOYMENT_MODE === "robot" ? "robot" : "cloud";
  return {
    deploymentMode,
    host: process.env.HOST || "0.0.0.0",
    port: readNumber("PORT", 3000),
    publicBaseUrl: process.env.PUBLIC_BASE_URL || "http://127.0.0.1:3000",
    robot: {
      id: "default",
      connectionMode: deploymentMode === "robot"
        ? "direct"
        : (process.env.ROBOT_CONNECTION_MODE === "tunnel" ? "tunnel" : "direct"),
      host: deploymentMode === "robot" ? "127.0.0.1" : (process.env.ROBOT_HOST || "127.0.0.1"),
      bridgePort: readNumber("ROBOT_BRIDGE_PORT", 18080),
      webPort: readNumber("ROBOT_WEB_PORT", 8002),
      mapRoot: process.env.ROBOT_MAP_ROOT || "/agibot/data/var/MapManagerModule/",
      sshUser: process.env.ROBOT_SSH_USER || "agi",
      sshPassword: process.env.ROBOT_SSH_PASSWORD || "",
      sshPrivateKeyPath: process.env.ROBOT_SSH_PRIVATE_KEY_PATH || undefined,
      sshPort: readNumber("ROBOT_SSH_PORT", 22),
      systemSshUser: process.env.ROBOT_SYSTEM_SSH_USER || "run",
      systemSshPassword: process.env.ROBOT_SYSTEM_SSH_PASSWORD || process.env.ROBOT_SSH_PASSWORD || "",
      systemSshPrivateKeyPath: process.env.ROBOT_SYSTEM_SSH_PRIVATE_KEY_PATH
        || process.env.ROBOT_SSH_PRIVATE_KEY_PATH
        || undefined,
      systemSshPort: readNumber("ROBOT_SYSTEM_SSH_PORT", readNumber("ROBOT_SSH_PORT", 22)),
      navHome: process.env.ROBOT_NAV_HOME || "/home/agi/robot_local_navigation_v3",
      javaAgentHome: process.env.JAVA_AGENT_HOME || "../robot_local_nav_java/robot_local_nav_java-new-java-interaction-end-hook",
      javaAgentExecutionMode: process.env.JAVA_AGENT_EXECUTION_MODE === "robot" ? "robot" : "local",
      robotJavaAgentHome: process.env.ROBOT_JAVA_AGENT_HOME || "/home/run/lingxi_java_agent",
      robotJavaAgentBridgePort: readNumber("ROBOT_JAVA_AGENT_BRIDGE_PORT", 18083),
      voiceRestartCommand: process.env.ROBOT_VOICE_RESTART_COMMAND || "/home/agi/restart_daohang_voice_stack.sh",
      javaAgentAutoStart: readBoolean("JAVA_AGENT_AUTO_START", true)
    },
    auth: {
      appId: process.env.MINIPROGRAM_APPID || "",
      appSecret: process.env.MINIPROGRAM_SECRET || "",
      tokenSecret: process.env.TOKEN_SECRET || "dev-only-token-secret",
      devBypass: readBoolean("DEV_AUTH_BYPASS", false),
      tokenTtlSec: readNumber("TOKEN_TTL_SEC", 7 * 24 * 60 * 60)
    }
  };
}
