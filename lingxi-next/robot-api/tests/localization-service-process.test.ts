/** Real Linux processes and socket, synthetic health only; no GLIM, sensors or robot. */
import { expect, it } from "vitest";
import { spawn } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { UnixLocalizationServiceBackend } from "../src/localization-backend";
import type { LocalizationState } from "../../shared/localization";

const python = process.env.LINGXI_LOCALIZATION_SERVICE_TEST_PYTHON;
const delay = () => new Promise((resolve) => setTimeout(resolve, 25));
async function until<T>(read: () => Promise<T>, ready: (value: T) => boolean, seconds = 8): Promise<T> {
  const deadline = performance.now() + seconds * 1000;
  while (performance.now() < deadline) { const value = await read(); if (ready(value)) return value; await delay(); }
  throw new Error("synthetic_service_observation_deadline");
}
it.skipIf(process.platform !== "linux" || !python || process.getuid?.() === 0)(
  "runs paired HTTP through the actual service and reaps replaced synthetic children", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "lx-si-"));
    const runs = path.join(root, "r"), socket = path.join(root, "s"), maps = path.join(root, "m");
    await mkdir(runs, { mode: 0o700 }); await mkdir(maps, { mode: 0o700 });
    const child = spawn(python!, ["-B", path.resolve("tools/tests/fixtures/localization_service_owner.py"), runs, socket],
      { env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", PYTHONDONTWRITEBYTECODE: "1" }, stdio: ["pipe", "pipe", "pipe"] });
    let output = "", error = "", closed = false;
    child.stdout.on("data", (bytes) => { output += bytes; });
    child.stderr.on("data", (bytes) => { error += bytes; });
    const exit = new Promise<{ code: number | null; signal: string | null }>((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (code, signal) => { closed = true; resolve({ code, signal }); });
    });
    const app = await buildServer({ demoMode: true, localizationConsole: true,
      localizationBackend: new UnixLocalizationServiceBackend(socket),
      savedSlamDirectory: maps, stateDirectory: path.join(root, "data"), staticDirectory: path.join(root, "none"),
      pairingCodeHash: pairingCodeHash("synthetic-service-process") });
    const pids: number[] = [];
    try {
      await until(async () => output, (text) => text.includes('"event": "listening"'));
      const base = await app.listen({ host: "127.0.0.1", port: 0 });
      const paired = await fetch(base + "/api/v1/session/pair", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pairingCode: "synthetic-service-process" }),
        signal: AbortSignal.timeout(2000) });
      expect(paired.status).toBe(201);
      const { token } = await paired.json() as { token: string };
      const headers = { authorization: `Bearer ${token}` };
      const get = (route = "/runtime") => fetch(base + "/api/v1/slam/localization" + route,
        { headers, signal: AbortSignal.timeout(2000) });
      const post = (action: string, payload: object) => fetch(base + "/api/v1/slam/localization/" + action,
        { method: "POST", headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify(payload), signal: AbortSignal.timeout(2000) });
      const identity = async () => (await get()).json();
      let runtime = await identity(), oldState: LocalizationState | undefined;
      expect(runtime.phase).toBe("idle");
      expect((await get("")).status).toBe(503);
      for (let iteration = 0; iteration < 2; iteration++) {
        expect((await post("start", { serviceId: runtime.serviceId, expectedGeneration: runtime.generation,
          mapId: `slam-${"b".repeat(32)}`, receiptSha256: "c".repeat(64) })).status).toBe(202);
        runtime = await until(identity, (value) => value.phase === "running" || value.phase === "failed");
        expect(runtime.phase).toBe("running");
        const stateResponse = await until(() => get(""), (value) => value.status === 200);
        const state = await stateResponse.json() as LocalizationState;
        pids.push(Number(await readFile(path.join(runs, runtime.runId, "child.pid"), "utf8")));
        if (oldState) {
          expect(state.binding.session).not.toBe(oldState.binding.session);
          expect((await post("pause", { binding: oldState.binding, expectedGeneration: oldState.generation })).status).toBe(409);
          expect(() => process.kill(pids[0], 0)).toThrow();
        }
        expect((await post("pause", { binding: state.binding, expectedGeneration: state.generation })).status).toBe(202);
        oldState = await (await get("")).json() as LocalizationState;
        expect((await post("shutdown", { serviceId: runtime.serviceId, expectedGeneration: runtime.generation,
          runId: runtime.runId })).status).toBe(202);
        expect((await get("")).status).toBe(503);
        runtime = await until(identity, (value) => value.cleanupComplete);
        expect(runtime.phase).toBe("stopped");
        expect(() => process.kill(pids.at(-1)!, 0)).toThrow();
      }
      child.stdin.end();
      await until(async () => closed, Boolean);
      expect(await exit).toEqual({ code: 0, signal: null });
      await expect(access(socket)).rejects.toThrow();
      expect(error).toBe("");
    } finally {
      await app.close();
      if (!closed) child.kill("SIGTERM");
      try { await until(async () => closed, Boolean); await exit; }
      finally {
        if (!closed) { child.kill("SIGKILL"); await exit; }
        await rm(root, { recursive: true, force: true });
      }
    }
  }, 30_000);
