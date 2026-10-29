import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, it } from "vitest";
import { SlamPointPipeSource } from "../src/slam-points";

it("receives isolated Python records across a real child pipe and clears them on child exit", async () => {
  const fixture = JSON.parse(readFileSync("shared/fixtures/slam-point-v1.synthetic.json", "utf8"));
  const child = spawn(process.platform === "win32" ? "python" : "python3", [
    "-B", path.resolve("tools/tests/fixtures/slam_point_pipe_child.py"),
    path.resolve("shared/fixtures/slam-point-v1.synthetic.json"),
  ], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
  const exited = once(child, "close");
  const source = new SlamPointPipeSource(child.stdout, fixture.session, "live", () => 10);
  try {
    child.stdin.write("send-isolated-synthetic\n");
    let frame;
    const deadline = Date.now() + 3000;
    while (Date.now() < deadline) {
      try { frame = await source.getLidarFrame(); } catch { /* Await the first isolated record. */ }
      if (frame?.sequence === 3) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    expect(frame).toMatchObject({ sequence: 3, points: fixture.points, epoch: fixture.session });
    child.stdin.end("close\n");
    expect((await exited)[0]).toBe(0);
    await expect(source.getLidarFrame()).rejects.toThrow();
  } finally {
    source.close();
    if (child.exitCode === null) child.kill();
    await exited;
  }
}, 8000);
