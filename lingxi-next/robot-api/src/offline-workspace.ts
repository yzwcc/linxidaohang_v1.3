import { execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { OfflineNavigationBackend } from "./offline-navigation-backend";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const scenes = ["open", "blocked", "detour", "appearing-wall"] as const;
export interface OfflineWorkspacePlan {
  executable: string;
  args: string[];
  checkArgs: string[];
  cwd: string;
  environment: NodeJS.ProcessEnv;
  stateDirectory: string;
  staticDirectory: string;
  port: number;
}

/** Keep robot credentials, ROS graphs and Node/shell injection settings out of this workspace. */
export function offlineEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const allowed = new Set(["path", "pathext", "systemroot", "windir", "comspec", "temp", "tmp", "tmpdir",
    "home", "user", "logname", "userprofile", "homedrive", "homepath", "appdata", "localappdata", "lang", "lc_all", "term"]);
  return Object.fromEntries(Object.entries(source).filter(([key]) => allowed.has(key.toLowerCase()) ||
    ["LINGXI_OFFLINE_NAV2_ROOTFS", "LINGXI_OFFLINE_NAV2_CASE", "LINGXI_OFFLINE_PORT"].includes(key)));
}

export function offlineWorkspacePlan(environment: NodeJS.ProcessEnv, platform = process.platform,
  uid = process.getuid?.()): OfflineWorkspacePlan {
  if (platform !== "win32" && platform !== "linux") throw new Error("windows_wsl_or_linux_required");
  if (platform === "linux" && (uid === undefined || uid === 0)) throw new Error("offline_api_must_run_as_ordinary_user");
  const rootfs = environment.LINGXI_OFFLINE_NAV2_ROOTFS;
  if (!rootfs || rootfs.trim() !== rootfs || !/^\/[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(rootfs) ||
      rootfs.split("/").some((part) => part === "." || part === "..")) {
    throw new Error("explicit_separate_humble_rootfs_required");
  }
  const scene = environment.LINGXI_OFFLINE_NAV2_CASE ?? "detour";
  if (!scenes.includes(scene as typeof scenes[number])) throw new Error("known_offline_scene_required");
  const rawPort = environment.LINGXI_OFFLINE_PORT ?? "3018";
  const port = Number(rawPort);
  if (String(port) !== rawPort || !Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error("bounded_local_port_required");
  }
  const output = `/product-offline-${randomBytes(12).toString("hex")}`;
  const prefix = platform === "win32" ? ["-u", "root", "--", "bash"] : ["-n", "--", "bash"];
  const script = "tools/nav2-offline-service-isolated.sh";
  return {
    executable: platform === "win32" ? "wsl.exe" : "sudo",
    args: [...prefix, script, rootfs, scene, output],
    checkArgs: [...prefix, script, "--check", rootfs, scene, output],
    cwd: projectRoot, environment: offlineEnvironment(environment),
    stateDirectory: path.join(projectRoot, ".local", output.slice(1)),
    staticDirectory: path.join(projectRoot, "dist"), port,
  };
}

export async function checkOfflineWorkspace(plan: OfflineWorkspacePlan, signal?: AbortSignal): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    execFile(plan.executable, plan.checkArgs, { cwd: plan.cwd, env: plan.environment, signal,
      timeout: 15_000, maxBuffer: 8192, windowsHide: true }, (error, stdout) => {
      if (signal?.aborted) { reject(signal.reason); return; }
      if (!error && stdout.trim() === "lingxi_offline_nav2_preflight_ok_v1") { resolve(); return; }
      reject(new Error("offline_nav2_preflight_failed: verify the separate Humble/Nav2 rootfs and namespace tools; " +
        "Linux requires existing administrator-approved noninteractive sudo access to the isolation wrapper. " +
        "No permissions or packages were changed."));
    });
  });
}

interface WorkspaceApp {
  listen(options: { host: string; port: number }): Promise<unknown>;
  close(): Promise<void>;
}
interface WorkspaceDependencies {
  check(plan: OfflineWorkspacePlan, signal?: AbortSignal): Promise<void>;
  backend(plan: OfflineWorkspacePlan): OfflineNavigationBackend;
  app(backend: OfflineNavigationBackend, plan: OfflineWorkspacePlan): Promise<WorkspaceApp>;
}

export async function startOfflineWorkspace(plan: OfflineWorkspacePlan, dependencies: WorkspaceDependencies,
  signal?: AbortSignal): Promise<{ close(): Promise<void> }> {
  let backend: OfflineNavigationBackend | undefined, app: WorkspaceApp | undefined;
  let backendClose: Promise<void> | undefined, closing: Promise<void> | undefined;
  const closeBackend = () => backend ? backendClose ??= backend.close() : Promise.resolve();
  const close = () => closing ??= (async () => {
    try { await app?.close(); } finally { await closeBackend(); }
  })();
  const abort = () => { void closeBackend().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    signal?.throwIfAborted();
    await dependencies.check(plan, signal);
    signal?.throwIfAborted();
    backend = dependencies.backend(plan);
    const owned = backend;
    // The API close hook and startup-failure path share one backend close operation.
    app = await dependencies.app({ offlineOnly: true, state: () => owned.state(), scene: () => owned.scene(),
      command: (value) => owned.command(value), close: closeBackend }, plan);
    signal?.throwIfAborted();
    await backend.scene();
    signal?.throwIfAborted();
    await app.listen({ host: "127.0.0.1", port: plan.port });
    signal?.throwIfAborted();
    return { close };
  } catch (error) {
    await close();
    throw error;
  } finally {
    signal?.removeEventListener("abort", abort);
  }
}
