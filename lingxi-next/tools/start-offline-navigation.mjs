import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const rootfs = process.env.LINGXI_OFFLINE_NAV2_ROOTFS;
if (!rootfs || !/^\/[A-Za-z0-9_./-]+$/.test(rootfs)) throw new Error("LINGXI_OFFLINE_NAV2_ROOTFS must name the separate local Humble rootfs");
if (process.platform !== "win32") throw new Error("This launcher currently supports Windows + local WSL only");
// No robot address, vendor bridge or cloud credentials enter the offline process.
const child = spawn(process.execPath, ["--import", "tsx", "robot-api/src/offline-main.ts"], {
  cwd: root, stdio: "inherit", windowsHide: true,
  env: { ...process.env, LINGXI_OFFLINE_NAV2_ROOTFS: rootfs },
});
child.once("exit", (code) => { process.exitCode = code ?? 1; });
child.once("error", (error) => { console.error(error.message); process.exitCode = 1; });
