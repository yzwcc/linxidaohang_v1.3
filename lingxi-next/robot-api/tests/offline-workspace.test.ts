import { describe, expect, it, vi } from "vitest";
import path from "node:path";
import { checkOfflineWorkspace, offlineEnvironment, offlineWorkspacePlan, startOfflineWorkspace } from "../src/offline-workspace";
import type { OfflineNavigationBackend } from "../src/offline-navigation-backend";

const environment = { LINGXI_OFFLINE_NAV2_ROOTFS: "/home/test/.cache/humble/rootfs" };
const plan = () => offlineWorkspacePlan(environment, "win32");
function fixture() {
  const backend: OfflineNavigationBackend = { offlineOnly: true, state: vi.fn(), scene: vi.fn(), command: vi.fn(),
    close: vi.fn(async () => undefined) };
  const app = { listen: vi.fn(async (_options: { host: string; port: number }) => undefined),
    close: vi.fn(async () => undefined) };
  const dependencies = { check: vi.fn(async () => undefined), backend: vi.fn(() => backend),
    app: vi.fn(async (_backend: OfflineNavigationBackend) => app) };
  return { backend, app, dependencies };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("portable isolated Nav2 launch plan", () => {
  it("selects WSL on Windows and noninteractive sudo on ordinary-user Linux with identical isolation", () => {
    const windows = plan(), linux = offlineWorkspacePlan(environment, "linux", 1000);
    expect(windows.executable).toBe("wsl.exe");
    expect(windows.args.slice(0, 4)).toEqual(["-u", "root", "--", "bash"]);
    expect(linux.executable).toBe("sudo");
    expect(linux.args.slice(0, 3)).toEqual(["-n", "--", "bash"]);
    for (const value of [windows, linux]) {
      expect(value.checkArgs).toContain("--check");
      expect(value.args).not.toContain("--check");
      expect(path.isAbsolute(value.cwd)).toBe(true);
      expect(value.staticDirectory).toBe(path.join(value.cwd, "dist"));
      expect(value.stateDirectory).toMatch(/product-offline-[0-9a-f]{24}$/);
      expect(value.port).toBe(3018);
    }
    expect(windows.stateDirectory).not.toBe(plan().stateDirectory);
  });
  it.each(["/", "relative", "/tmp/../rootfs", "/tmp/./rootfs", "/tmp//rootfs", "/tmp/rootfs/", "/tmp/x;id", "/tmp/x\n", "C:\\rootfs"])("rejects rootfs %j before any process starts", (rootfs) => {
    expect(() => offlineWorkspacePlan({ ...environment, LINGXI_OFFLINE_NAV2_ROOTFS: rootfs }, "win32")).toThrow();
  });
  it.each(["0", "1023", "65536", "-3018", "3018.5", "3.018e3", " 3018", "", "03018"])("rejects port %j before any process starts", (port) => {
    expect(() => offlineWorkspacePlan({ ...environment, LINGXI_OFFLINE_PORT: port }, "win32")).toThrow();
  });
  it("rejects unknown scenes, root API and unsupported platforms", () => {
    expect(() => offlineWorkspacePlan({ ...environment, LINGXI_OFFLINE_NAV2_CASE: "live" }, "win32")).toThrow();
    expect(() => offlineWorkspacePlan(environment, "linux", 0)).toThrow("ordinary_user");
    expect(() => offlineWorkspacePlan(environment, "darwin", 1000)).toThrow("windows_wsl_or_linux");
  });
  it("does not inherit robot/cloud secrets, ROS peers, loader options or shell initialization", () => {
    const safe = offlineEnvironment({ ...environment, Path: "system-path", SystemRoot: "C:\\Windows", HOME: "/home/test",
      LINGXI_MAPPING_SOCKET: "/live.sock", AIMDK_BRIDGE_URL: "http://robot", OPENAI_API_KEY: "not-a-real-key",
      LINGXI_DATA_DIR: "/business", LINGXI_PAIRING_CODE_SHA256: "secret", NODE_OPTIONS: "--require=bad",
      BASH_ENV: "/bad", PYTHONPATH: "/bad", LD_PRELOAD: "/bad", WSLENV: "SECRETS/u", ROS_DOMAIN_ID: "1" });
    expect(safe).toEqual({ ...environment, Path: "system-path", SystemRoot: "C:\\Windows", HOME: "/home/test" });
  });
});

describe("workspace startup ownership", () => {
  it("preflights before constructing services and listens only after a real scene reply", async () => {
    const { backend, app, dependencies } = fixture(), order: string[] = [];
    dependencies.check.mockImplementation(async () => { order.push("check"); });
    dependencies.backend.mockImplementation(() => { order.push("backend"); return backend; });
    backend.scene = vi.fn(async () => { order.push("scene"); return undefined as never; });
    app.listen.mockImplementation(async () => { order.push("listen"); });
    const running = await startOfflineWorkspace(plan(), dependencies);
    expect(order).toEqual(["check", "backend", "scene", "listen"]);
    expect(app.listen).toHaveBeenCalledWith({ host: "127.0.0.1", port: 3018 });
    await Promise.all([running.close(), running.close()]);
    expect(app.close).toHaveBeenCalledTimes(1);
    expect(backend.close).toHaveBeenCalledTimes(1);
  });
  it.each(["check", "app", "scene", "listen"])("cleans up a failed %s without retry or serving a fallback", async (stage) => {
    const { backend, app, dependencies } = fixture();
    const fail = async () => { throw new Error(`failed_${stage}`); };
    if (stage === "check") dependencies.check.mockImplementation(fail);
    if (stage === "app") dependencies.app.mockImplementation(fail);
    if (stage === "scene") backend.scene = vi.fn(fail);
    if (stage === "listen") app.listen.mockImplementation(fail);
    await expect(startOfflineWorkspace(plan(), dependencies)).rejects.toThrow(`failed_${stage}`);
    expect(dependencies.backend).toHaveBeenCalledTimes(stage === "check" ? 0 : 1);
    expect(backend.close).toHaveBeenCalledTimes(stage === "check" ? 0 : 1);
    expect(app.close).toHaveBeenCalledTimes(["scene", "listen"].includes(stage) ? 1 : 0);
    expect(backend.command).not.toHaveBeenCalled();
  });
  it("closes the backend even if API shutdown fails, sharing ownership with its close hook", async () => {
    const { backend, app, dependencies } = fixture();
    dependencies.app.mockImplementation(async (owned: OfflineNavigationBackend) => {
      app.close.mockImplementation(async () => { await owned.close(); throw new Error("api_close_failed"); });
      return app;
    });
    const running = await startOfflineWorkspace(plan(), dependencies);
    await expect(running.close()).rejects.toThrow("api_close_failed");
    await expect(running.close()).rejects.toThrow("api_close_failed");
    expect(backend.close).toHaveBeenCalledTimes(1);
  });
  it("cancels preflight without creating an API or backend", async () => {
    const { dependencies } = fixture(), controller = new AbortController(), gate = deferred<void>();
    dependencies.check.mockImplementation(() => gate.promise);
    const starting = startOfflineWorkspace(plan(), dependencies, controller.signal);
    controller.abort(new Error("test_interrupt")); gate.resolve();
    await expect(starting).rejects.toThrow("test_interrupt");
    expect(dependencies.backend).not.toHaveBeenCalled();
    expect(dependencies.app).not.toHaveBeenCalled();
  });
  it("interrupts an in-flight scene request and never opens a listener afterward", async () => {
    const { backend, app, dependencies } = fixture(), controller = new AbortController(), gate = deferred<never>();
    const entered = deferred<void>();
    backend.scene = vi.fn(() => { entered.resolve(); return gate.promise; });
    backend.close = vi.fn(async () => { gate.resolve(undefined as never); });
    const starting = startOfflineWorkspace(plan(), dependencies, controller.signal);
    await entered.promise;
    controller.abort(new Error("test_interrupt"));
    await expect(starting).rejects.toThrow("test_interrupt");
    expect(app.listen).not.toHaveBeenCalled();
    expect(app.close).toHaveBeenCalledTimes(1);
    expect(backend.close).toHaveBeenCalledTimes(1);
  });
  it("closes an API that finishes construction after interruption", async () => {
    const { backend, app, dependencies } = fixture(), controller = new AbortController(), gate = deferred<typeof app>();
    const entered = deferred<void>();
    dependencies.app.mockImplementation(() => { entered.resolve(); return gate.promise; });
    const starting = startOfflineWorkspace(plan(), dependencies, controller.signal);
    await entered.promise;
    controller.abort(new Error("test_interrupt")); gate.resolve(app);
    await expect(starting).rejects.toThrow("test_interrupt");
    expect(app.close).toHaveBeenCalledTimes(1);
    expect(backend.close).toHaveBeenCalledTimes(1);
    expect(backend.scene).not.toHaveBeenCalled();
  });
  it("rejects a failed helper or contaminated preflight reply without accepting a mock marker", async () => {
    const value = plan();
    for (const source of ["process.exit(2)", "console.log('extra\\nlingxi_offline_nav2_preflight_ok_v1')"]) {
      await expect(checkOfflineWorkspace({ ...value, executable: process.execPath, checkArgs: ["-e", source] })).rejects.toThrow("preflight_failed");
    }
  });
});
