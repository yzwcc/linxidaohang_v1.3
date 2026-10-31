import path from "node:path";
import { buildServer } from "./server";
import { StdioOfflineNavigationBackend } from "./offline-navigation-backend";

const rootfs = process.env.LINGXI_OFFLINE_NAV2_ROOTFS;
if (process.platform !== "win32" || !rootfs || !/^\/[A-Za-z0-9_./-]+$/.test(rootfs)) throw new Error("explicit_separate_wsl_rootfs_required");
const scene = process.env.LINGXI_OFFLINE_NAV2_CASE ?? "detour";
if (!["open", "blocked", "detour", "appearing-wall"].includes(scene)) throw new Error("known_offline_scene_required");
const output = `/product-offline-${Date.now()}`;
const backend = new StdioOfflineNavigationBackend("wsl.exe", ["-u", "root", "--", "bash",
  "tools/nav2-offline-service-isolated.sh", rootfs, scene, output], process.cwd());
const app = await buildServer({ offlineNavigationBackend: backend,
  stateDirectory: path.resolve(".local", output.slice(1)), internalAgentToken: "",
});
const port = Number(process.env.LINGXI_OFFLINE_PORT ?? "3018");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("bounded_local_port_required");
for (const event of ["SIGINT", "SIGTERM"] as const) process.once(event, () => { void app.close(); });
await backend.scene();
await app.listen({ host: "127.0.0.1", port });
console.info(`Offline Nav2 workspace: http://127.0.0.1:${port} (synthetic inputs, no robot IO)`);
