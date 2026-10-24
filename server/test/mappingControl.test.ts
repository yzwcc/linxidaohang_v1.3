import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { inflateSync } from "node:zlib";
import { describe, expect, it, vi } from "vitest";
import { getMappingHealth, mapSaveScript, mappingCommandScript } from "../src/mappingControl.js";
import type { AppConfig } from "../src/config.js";

const config = {
  robot: { host: "127.0.0.1", aimdkSetup: "/home/agi/aimdk/install/setup.bash" }
} as AppConfig;

const fakeAimdkRunner = String.raw`
import base64, json, os, sys, types

class Obj: pass
origin = Obj(); origin.position = Obj(); origin.orientation = Obj()
origin.position.x, origin.position.y, origin.position.z = -1.25, 2.5, 0.0
origin.orientation.x, origin.orientation.y, origin.orientation.z, origin.orientation.w = 0.0, 0.0, 0.0, 2.0
info = Obj(); info.width, info.height, info.resolution, info.origin = 3, 2, 0.5, origin
response = Obj(); response.code = 0; response.map_id = 456; response.map_info = info
response.map_path = '/agibot/data/var/MapManagerModule/456/source.map'; response.data = [0, 100, -1, 25, 64, 65]
if os.environ.get('BAD_OCCUPANCY') == '1': response.data[0] = 101
if os.environ.get('ROTATED_ORIGIN') == '1': origin.orientation.z, origin.orientation.w = 0.7071067811865476, 0.7071067811865476
if os.environ.get('NO_MAP_ID') == '1': response.map_id = 0
if os.environ.get('DELAY_MAP_ID') == '1': response.map_id = 0
class Request: pass
service = types.ModuleType('aimdk_msgs.srv'); service.GetStoredMapByName = type('GetStoredMapByName', (), {'Request': Request})
aimdk = types.ModuleType('aimdk_msgs'); aimdk.srv = service
sys.modules['aimdk_msgs'] = aimdk; sys.modules['aimdk_msgs.srv'] = service
std_msgs = types.ModuleType('std_msgs'); std_msg = types.ModuleType('std_msgs.msg'); std_msg.Header = type('Header', (), {})
std_msgs.msg = std_msg; sys.modules['std_msgs'] = std_msgs; sys.modules['std_msgs.msg'] = std_msg
class Future:
    def __init__(self, attempt): self.attempt = attempt
    def result(self):
        if os.environ.get('DELAY_MAP_ID') == '1' and self.attempt < 3: return None
        return response
class Client:
    def wait_for_service(self, timeout_sec): return True
    def __init__(self): self.attempt = 0
    def call_async(self, request):
        self.attempt += 1
        if os.environ.get('DELAY_MAP_ID') == '1' and self.attempt == 3: response.map_id = 456
        return Future(self.attempt)
class Clock:
    def now(self): return types.SimpleNamespace(to_msg=lambda: None)
class Node:
    def __init__(self, name): pass
    def create_client(self, service_type, name): return Client()
    def get_clock(self): return Clock()
    def destroy_node(self): pass
node_module = types.ModuleType('rclpy.node'); node_module.Node = Node
executor_module = types.ModuleType('rclpy.executors')
class SingleThreadedExecutor:
    def add_node(self, node): pass
    def spin_until_future_complete(self, future, timeout_sec): pass
    def remove_node(self, node): pass
executor_module.SingleThreadedExecutor = SingleThreadedExecutor
rclpy = types.ModuleType('rclpy'); rclpy.init = lambda: None; rclpy.ok = lambda: True; rclpy.shutdown = lambda: None
rclpy.node = node_module; rclpy.executors = executor_module
sys.modules['rclpy'] = rclpy; sys.modules['rclpy.node'] = node_module; sys.modules['rclpy.executors'] = executor_module
script = base64.b64decode(sys.argv[1]).decode('utf-8')
name, root = sys.argv[2], sys.argv[3]
sys.argv = ['<aimdk-map-save>', name, root]
try:
    exec(compile(script, '<aimdk-map-save>', 'exec'), {'__name__': '__main__'})
except SystemExit:
    pass
`;

function runMapSave(root: string, env: NodeJS.ProcessEnv = {}) {
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  return execFileSync(python, [
    "-c",
    fakeAimdkRunner,
    Buffer.from(mapSaveScript(), "utf8").toString("base64"),
    "demo_map",
    root
  ], { encoding: "utf8", env: { ...process.env, ...env } }).trim().split(/\r?\n/).at(-1);
}

function decodeGrayPng(png: Buffer): { width: number; height: number; values: number[] } {
  let offset = 8;
  let width = 0;
  let height = 0;
  const compressed: Buffer[] = [];
  while (offset + 12 <= png.length) {
    const length = png.readUInt32BE(offset);
    const kind = png.toString("ascii", offset + 4, offset + 8);
    const payload = png.subarray(offset + 8, offset + 8 + length);
    if (kind === "IHDR") {
      width = payload.readUInt32BE(0);
      height = payload.readUInt32BE(4);
      expect(payload[8]).toBe(8);
      expect(payload[9]).toBe(0);
    } else if (kind === "IDAT") {
      compressed.push(payload);
    }
    offset += length + 12;
    if (kind === "IEND") break;
  }
  const raw = inflateSync(Buffer.concat(compressed));
  const values: number[] = [];
  for (let row = 0; row < height; row += 1) {
    expect(raw[row * (width + 1)]).toBe(0);
    values.push(...raw.subarray(row * (width + 1) + 1, (row + 1) * (width + 1)));
  }
  return { width, height, values };
}

describe("AIMDK mapping adapter", () => {
  it("checks the robot's documented SLAM process and live sensor and odometry data", async () => {
    const runCommand = vi.fn(async () => [
      "agibot_software=active",
      "hal_sensor_running=1",
      "slam_running=1",
      "lidar_publishers=1",
      "imu_publishers=1",
      "odom_publishers=1",
      "mapping_command_subscribers=1",
      "lidar_data=1",
      "imu_data=1",
      "odom_data=1",
      "topic=/slam/lidar_odom",
      "service=/aimdk_5Fmsgs/srv/GetStoredMapByName"
    ].join("\n"));

    const health = await getMappingHealth(config, runCommand);

    expect(health.ok).toBe(true);
    expect(runCommand.mock.calls[0][0]).toContain("./slam --cfg_file_path");
    expect(runCommand.mock.calls[0][0]).toContain("/slam/lidar_odom");
    expect(runCommand.mock.calls[0][0]).toContain("GetStoredMapByName");
  });

  it("uses the SDK service and protects navigation map output", () => {
    const script = mapSaveScript();
    expect(script).toContain("/aimdk_5Fmsgs/srv/GetStoredMapByName");
    expect(script).toContain("GetStoredMapByName.Request()");
    expect(script).toContain("width * height != len(result.data)");
    expect(script).toContain("带旋转的地图原点");
    expect(script).toContain("os.rename(tmp, target)");
    expect(script).toContain("reused_existing_map");
    expect(script).toContain("grid_map_info.txt");
    expect(script).toContain("range(8)");
    expect(script).toContain("timeout_sec=15.0");
    expect(script).toContain("int(result.map_id) <= 0");
  });

  it("publishes the SDK command with transient-local reliable QoS and waits for a subscriber", () => {
    const script = mappingCommandScript();
    expect(script).toContain("QoSDurabilityPolicy.TRANSIENT_LOCAL");
    expect(script).toContain("QoSReliabilityPolicy.RELIABLE");
    expect(script).toContain("publisher.get_subscription_count()");
    expect(script).toContain("/integrated_command");
  });

  it("executes the generated saver and writes correctly oriented navigation map files", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "lingxi-map-save-"));
    try {
      const result = JSON.parse(runMapSave(root) || "{}") as Record<string, unknown>;
      const mapDir = path.join(root, "456");
      expect(result, JSON.stringify(result)).toMatchObject({ ok: true, map_id: 456, width: 3, height: 2 });
      expect(fs.readFileSync(path.join(mapDir, "grid_map_info.txt"), "utf8").replace(/\r\n/g, "\n")).toBe("2\n2.5 6\n3 2\n");
      expect(decodeGrayPng(fs.readFileSync(path.join(mapDir, "occupancy_map.png")))).toEqual({
        width: 3,
        height: 2,
        values: [191, 92, 0, 254, 0, 205]
      });
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("reuses an identical numeric map id and rejects a conflicting existing map", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "lingxi-map-id-"));
    try {
      runMapSave(root);
      const reused = JSON.parse(runMapSave(root) || "{}") as Record<string, unknown>;
      expect(reused).toMatchObject({ ok: true, map_id: 456, reused_existing_map: true });
      fs.writeFileSync(path.join(root, "456", "occupancy_map.png"), "different map");
      const conflict = JSON.parse(runMapSave(root) || "{}") as Record<string, unknown>;
      expect(conflict.ok).toBe(false);
      expect(String(conflict.message)).toMatch(/456/);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("fails closed on invalid occupancy values and rotated map origins", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "lingxi-map-invalid-"));
    try {
      const badOccupancy = JSON.parse(runMapSave(root, { BAD_OCCUPANCY: "1" }) || "{}") as Record<string, unknown>;
      expect(badOccupancy.ok).toBe(false);
      expect(fs.existsSync(path.join(root, "456"))).toBe(false);

      const rotatedOrigin = JSON.parse(runMapSave(root, { ROTATED_ORIGIN: "1" }) || "{}") as Record<string, unknown>;
      expect(rotatedOrigin.ok).toBe(false);
      expect(fs.existsSync(path.join(root, "456"))).toBe(false);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("retries a temporarily empty AIMDK response until the saved map id becomes available", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "lingxi-map-pending-"));
    try {
      const saved = JSON.parse(runMapSave(root, { DELAY_MAP_ID: "1" }) || "{}") as Record<string, unknown>;
      expect(saved).toMatchObject({ ok: true, map_id: 456 });
      expect(fs.existsSync(path.join(root, "456", "occupancy_map.png"))).toBe(true);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
