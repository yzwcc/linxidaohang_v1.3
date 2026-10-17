import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  discoverLocalLinkcraftResources,
  isResourceConfigAccessDenied,
  robotPythonPathEntries
} from "../src/linkcraftResources.js";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => fs.rm(directory, { recursive: true })));
});

describe("local LinkCraft resource discovery", () => {
  it("filters resources and reads WAV duration", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "linkcraft-test-"));
    temporaryDirectories.push(root);
    const dance = path.join(root, "dance");
    await fs.mkdir(dance);
    await fs.writeFile(path.join(dance, "audio.wav"), wavBuffer(8000, 1.5));
    const config = path.join(root, "resource_config.yaml");
    await fs.writeFile(config, [
      "resource_config:",
      "  - resource_key: app_other",
      "    current_version: {version: 1.0.0, name: other, files: []}",
      "  - resource_key: linkcraft_resource_onnx_test",
      "    current_version:",
      "      version: 0.0.1",
      "      name: 测试舞蹈",
      `      files: [${dance.replaceAll("\\", "/")}]`
    ].join("\n"), "utf8");

    const resources = await discoverLocalLinkcraftResources(config);

    expect(resources).toHaveLength(1);
    expect(resources[0]).toMatchObject({
      name: "测试舞蹈",
      resource_type: "BODY_MONTION",
      duration_sec: 1.5,
      duration_source: "audio",
      available: true
    });
  });

  it("includes the navigation subdirectory for robot-local package discovery", () => {
    const entries = robotPythonPathEntries("/opt/sparkrobot/lingxi-nav/current");
    expect(entries.split(":")).toContain("/opt/sparkrobot/lingxi-nav/current/navigation");
  });

  it("recognizes the SoC0 SSHFS permission failure", () => {
    expect(isResourceConfigAccessDenied(new Error(
      "PermissionError: [Errno 13] Permission denied: '/agibot/nfs/soc0/var/robot_proxy/resources/resource_config.yaml'"
    ))).toBe(true);
    expect(isResourceConfigAccessDenied(new Error("python module not found"))).toBe(false);
  });
});

function wavBuffer(frameRate: number, durationSec: number): Buffer {
  const dataSize = frameRate * durationSec * 2;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(frameRate, 24);
  buffer.writeUInt32LE(frameRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataSize, 40);
  return buffer;
}
