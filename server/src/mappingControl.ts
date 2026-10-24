import type { AppConfig } from "./config.js";
import fs from "node:fs";
import path from "node:path";
import { runRobotSshCommand, shellQuote } from "./sshControl.js";

const lidarTopic = "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud";
const imuTopic = "/aima/hal/sensor/lidar_chest_front/imu";
const odomTopic = "/slam/lidar_odom";

export type MappingHealth = {
  ok: boolean;
  agibot_software: string;
  hal_sensor_running: boolean;
  slam_running: boolean;
  lidar_publishers: number;
  imu_publishers: number;
  odom_publishers: number;
  lidar_data_available: boolean;
  imu_data_available: boolean;
  odom_data_available: boolean;
  mapping_related_topics: string[];
  mapping_related_services: string[];
  checked_at: string;
  diagnosis: string;
};

type MappingSession = { active: boolean; awaiting_save: boolean; map_name: string | null; started_at: string | null };
const sessionPath = process.env.MAPPING_SESSION_PATH || "/var/lib/sparkrobot/lingxi-nav/mapping-session.json";
let session: MappingSession = loadSession();
let operation: Promise<unknown> | null = null;

function loadSession(): MappingSession {
  try {
    const value = JSON.parse(fs.readFileSync(sessionPath, "utf8")) as Partial<MappingSession>;
    if (typeof value.active !== "boolean" || typeof value.awaiting_save !== "boolean") throw new Error("invalid session");
    return {
      active: value.active,
      awaiting_save: value.awaiting_save,
      map_name: typeof value.map_name === "string" ? value.map_name : null,
      started_at: typeof value.started_at === "string" ? value.started_at : null
    };
  } catch {
    return { active: false, awaiting_save: false, map_name: null, started_at: null };
  }
}

function saveSession(): void {
  const directory = path.dirname(sessionPath);
  fs.mkdirSync(directory, { recursive: true, mode: 0o770 });
  const temporary = `${sessionPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(session)}\n`, { encoding: "utf8", mode: 0o660 });
  fs.renameSync(temporary, sessionPath);
}

function updateSession(value: MappingSession): void {
  session = value;
  try {
    saveSession();
  } catch (error) {
    session = loadSession();
    throw new Error(`无法持久化建图会话状态：${error instanceof Error ? error.message : String(error)}`);
  }
}

function environment(): string {
  return String.raw`set -u
ROS_SETUP=/opt/ros/humble/setup.bash
AIMDK_SETUP=/home/agi/aimdk/install/setup.bash
set +u
[ ! -r "$ROS_SETUP" ] || source "$ROS_SETUP"
[ ! -r "$AIMDK_SETUP" ] || source "$AIMDK_SETUP"
set -u
[ ! -r /agibot/software/entry/cfg/ros_dds_configuration.xml ] || export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
[ ! -r /agibot/software/nav/bin/librmw_fastrtps.so ] || export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so
for prefix in /opt/ros/humble /home/agi/aimdk/install; do
  if [ -d "$prefix/lib" ]; then
    export PYTHONPATH="$prefix/local/lib/python3.10/dist-packages:$prefix/lib/python3.10/site-packages:\${PYTHONPATH:-}"
    export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/aarch64-linux-gnu:\${LD_LIBRARY_PATH:-}"
  fi
done`;
}

function runRos(command: string, config: AppConfig, timeoutMs = 15_000): Promise<string> {
  return runRobotSshCommand(`bash -lc ${shellQuote(`${environment()}; ${command}`)}`, config, timeoutMs);
}

export function mappingCommandScript(): string {
  return String.raw`import sys, time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from std_msgs.msg import String

rclpy.init()
node = Node('lingxi_mapping_command_publisher')
qos = QoSProfile(depth=10, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL, reliability=QoSReliabilityPolicy.RELIABLE)
publisher = node.create_publisher(String, '/integrated_command', qos)
deadline = time.monotonic() + 8.0
while rclpy.ok() and publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
if publisher.get_subscription_count() == 0:
    raise RuntimeError('AIMDK /integrated_command 暂无订阅者，未发送建图命令')
message = String()
message.data = sys.argv[1]
publisher.publish(message)
deadline = time.monotonic() + 1.5
while rclpy.ok() and time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
node.destroy_node()
rclpy.shutdown()`;
}

function publishIntegratedCommand(command: string, config: AppConfig): Promise<string> {
  const source = mappingCommandScript();
  const encoded = Buffer.from(source, "utf8").toString("base64");
  return runRos(
    `python3 -c "import base64;exec(compile(base64.b64decode('${encoded}'),'<aimdk-mapping-command>','exec'))" ${shellQuote(command)}`,
    config,
    12_000
  );
}

export function getMappingSession(): MappingSession {
  return { ...session };
}

export async function getMappingHealth(config: AppConfig): Promise<MappingHealth> {
  const lines = [
    "set +e",
    environment(),
    "echo agibot_software=$(systemctl is-active agibot_software.service 2>/dev/null || echo unknown)",
    "pgrep -f 'aima-hal_sensor-app-main' >/dev/null && echo hal_sensor_running=1 || echo hal_sensor_running=0",
    "pgrep -f './slam --cfg_file_path' >/dev/null && echo slam_running=1 || echo slam_running=0",
    ...[["lidar", lidarTopic], ["imu", imuTopic], ["odom", odomTopic]].map(([name, topic]) =>
      `${name}_info=$(timeout 3 ros2 topic info ${shellQuote(topic)} 2>/dev/null); ${name}_publishers=$(printf '%s\\n' "$${name}_info" | awk '/Publisher count/ {print $3; exit}'); echo ${name}_publishers=$${name}_publishers`
    ),
    `if [ "$lidar_publishers" -gt 0 ] 2>/dev/null; then timeout 3 ros2 topic echo --once --qos-reliability best_effort ${shellQuote(lidarTopic)} sensor_msgs/msg/PointCloud2 >/dev/null 2>&1 && echo lidar_data=1 || echo lidar_data=0; else echo lidar_data=0; fi`,
    `if [ "$imu_publishers" -gt 0 ] 2>/dev/null; then timeout 3 ros2 topic echo --once --qos-reliability best_effort ${shellQuote(imuTopic)} sensor_msgs/msg/Imu >/dev/null 2>&1 && echo imu_data=1 || echo imu_data=0; else echo imu_data=0; fi`,
    `if [ "$odom_publishers" -gt 0 ] 2>/dev/null; then timeout 3 ros2 topic echo --once --qos-reliability best_effort ${shellQuote(odomTopic)} nav_msgs/msg/Odometry >/dev/null 2>&1 && echo odom_data=1 || echo odom_data=0; else echo odom_data=0; fi`,
    "timeout 3 ros2 topic list -t 2>/dev/null | grep -Ei 'map|slam|lidar|imu|integrated_command' | head -80 | sed 's/^/topic=/'",
    "timeout 3 ros2 service list -t 2>/dev/null | grep -Ei 'map|slam|lidar|imu|GetStoredMapByName' | head -80 | sed 's/^/service=/'"
  ].join("\n");
  const output = await runRobotSshCommand(`bash -lc ${shellQuote(lines)}`, config, 42_000);
  const fields: Record<string, string> = {};
  for (const line of output.split(/\r?\n/)) {
    const index = line.indexOf("=");
    if (index > 0) fields[line.slice(0, index)] = line.slice(index + 1).trim();
  }
  const hal = fields.hal_sensor_running === "1";
  const slam = fields.slam_running === "1";
  const lidar = Number(fields.lidar_publishers) || 0;
  const imu = Number(fields.imu_publishers) || 0;
  const odom = Number(fields.odom_publishers) || 0;
  const sensorsHealthy = (fields.agibot_software || "unknown") === "active" && hal
    && lidar > 0 && imu > 0 && fields.lidar_data === "1" && fields.imu_data === "1";
  const topics = output.split(/\r?\n/).filter((line) => line.startsWith("topic=")).map((line) => line.slice(6));
  const services = output.split(/\r?\n/).filter((line) => line.startsWith("service=")).map((line) => line.slice(8));
  const mappingService = services.some((item) => item.includes("GetStoredMapByName"));
  const odomHealthy = slam && odom > 0 && fields.odom_data === "1";
  const diagnosis = !sensorsHealthy
    ? "底层服务、雷达点云或 IMU 数据未就绪"
    : !odomHealthy
    ? "传感器输入可用，但 SLAM 定位输出尚未确认"
    : !mappingService
    ? "传感器与定位可用，但 AIMDK 地图存储服务尚未发现"
    : "传感器、SLAM 与 AIMDK 地图服务均已发现；仍需实机验证完整建图保存链路";
  return {
    ok: sensorsHealthy && odomHealthy && mappingService,
    agibot_software: fields.agibot_software || "unknown",
    hal_sensor_running: hal,
    slam_running: slam,
    lidar_publishers: lidar,
    imu_publishers: imu,
    odom_publishers: odom,
    lidar_data_available: fields.lidar_data === "1",
    imu_data_available: fields.imu_data === "1",
    odom_data_available: fields.odom_data === "1",
    mapping_related_topics: topics,
    mapping_related_services: services,
    checked_at: new Date().toISOString(),
    diagnosis
  };
}

export async function startMapping(config: AppConfig) {
  if (operation) throw new Error("建图操作正在执行，请稍候");
  if (session.active || session.awaiting_save) throw new Error("当前已有建图任务或待保存地图，请先完成保存");
  const task = (async () => {
    const health = await getMappingHealth(config);
    if (!health.ok) throw new Error(`建图前置条件未就绪：${health.diagnosis}`);
    const output = await publishIntegratedCommand("start_mapping", config);
    updateSession({ active: true, awaiting_save: false, map_name: null, started_at: new Date().toISOString() });
    return { ok: true, session: getMappingSession(), output: output.trim() };
  })();
  operation = task;
  try { return await task; } finally { if (operation === task) operation = null; }
}

export async function stopAndSaveMapping(config: AppConfig, mapName: string) {
  const name = mapName.trim();
  if (!/^[\p{L}\p{N}][\p{L}\p{N} _-]{0,47}$/u.test(name)) {
    throw new Error("地图名称须为 1-48 位中文、字母、数字、空格、下划线或短横线");
  }
  if (operation) throw new Error("建图操作正在执行，请稍候");
  if (!session.active && !session.awaiting_save) throw new Error("当前没有由本控制台启动的建图任务");
  const task = (async () => {
    if (session.active) {
      await publishIntegratedCommand(`stop_mapping:${name}`, config);
      updateSession({ active: false, awaiting_save: true, map_name: name, started_at: null });
    } else if (session.map_name !== name) {
      throw new Error(`待保存地图名称为 ${session.map_name}，请使用相同名称重试`);
    }
    const script = Buffer.from(mapSaveScript(), "utf8").toString("base64");
    const output = await runRos(
      `python3 -c "import base64;exec(compile(base64.b64decode('${script}'),'<aimdk-map-save>','exec'))" ${shellQuote(name)} ${shellQuote(config.robot.mapRoot)}`,
      config,
      90_000
    );
    const payload = JSON.parse(output.trim().split(/\r?\n/).at(-1) || "{}");
    if (!payload.ok) throw new Error(payload.message || "AIMDK 返回地图保存失败");
    updateSession({ active: false, awaiting_save: false, map_name: name, started_at: null });
    return { ok: true, session: getMappingSession(), map: payload };
  })();
  operation = task;
  try { return await task; } finally { if (operation === task) operation = null; }
}

export function mapSaveScript(): string {
  return String.raw`import json, math, os, pathlib, struct, sys, tempfile, time, zlib
import rclpy
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from std_msgs.msg import Header
from aimdk_msgs.srv import GetStoredMapByName

name, root_value = sys.argv[1], sys.argv[2]
root = pathlib.Path(root_value).expanduser().resolve()
root.mkdir(parents=True, exist_ok=True)
node = None
try:
    rclpy.init()
    node = Node('lingxi_mapping_map_saver')
    client = node.create_client(GetStoredMapByName, '/aimdk_5Fmsgs/srv/GetStoredMapByName')
    if not client.wait_for_service(timeout_sec=10.0):
        raise RuntimeError('AIMDK GetStoredMapByName 服务不可用')
    request = GetStoredMapByName.Request()
    request.header = Header()
    request.header.stamp = node.get_clock().now().to_msg()
    request.map_name = name
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    result = None
    for attempt in range(6):
        future = client.call_async(request)
        executor.spin_until_future_complete(future, timeout_sec=15.0)
        result = future.result()
        if result is not None and result.code == 0:
            break
        if attempt < 5:
            time.sleep(2.0)
    executor.remove_node(node)
    if result is None:
        raise RuntimeError('等待 AIMDK 地图响应超时')
    if result.code != 0:
        raise RuntimeError('AIMDK 地图服务返回错误码 ' + str(result.code))
    info = result.map_info
    width, height, resolution = int(info.width), int(info.height), float(info.resolution)
    if width < 1 or height < 1 or width * height != len(result.data) or width * height > 100000000:
        raise RuntimeError('AIMDK 地图宽高与栅格数据不一致')
    if not math.isfinite(resolution) or resolution <= 0:
        raise RuntimeError('AIMDK 地图分辨率无效')
    q = info.origin.orientation
    if abs(q.x) > 1e-5 or abs(q.y) > 1e-5 or abs(q.z) > 1e-5 or abs(q.w - 1.0) > 1e-5:
        raise RuntimeError('当前导航地图格式不支持带旋转的地图原点，已拒绝写入以避免坐标错位')
    origin = info.origin.position
    ppm = 1.0 / resolution
    origin_u, origin_v = -origin.x * ppm, (height - 1) + origin.y * ppm
    values = bytes(205 if int(v) < 0 else 254 if int(v) == 0 else 0 if int(v) >= 65 else 254 - int(v) * 254 // 100 for v in result.data)
    rows = b''.join(b'\x00' + values[y * width:(y + 1) * width] for y in range(height - 1, -1, -1))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b'')
    map_id = int(result.map_id)
    if map_id <= 0:
        raise RuntimeError('AIMDK 未返回有效的数字地图 ID')
    target = root / str(map_id)
    if target.exists():
        existing_png = target / 'occupancy_map.png'
        existing_info = target / 'grid_map_info.txt'
        if not existing_png.is_file() or not existing_info.is_file():
            raise RuntimeError('AIMDK 地图 ID 已存在，但不是完整导航地图；为避免覆盖，拒绝写入：' + str(map_id))
        expected_info = f'{ppm:.12g}\n{origin_u:.12g} {origin_v:.12g}\n{width} {height}\n'.encode('utf-8')
        if existing_png.read_bytes() != png or existing_info.read_bytes() != expected_info:
            raise RuntimeError('该地图 ID 已存在且内容不同，为避免覆盖其他地图，拒绝写入：' + str(map_id))
        print(json.dumps({'ok': True, 'map_name': name, 'map_id': map_id, 'width': width, 'height': height, 'resolution_m': resolution, 'path': str(target), 'reused_existing_map': True}, ensure_ascii=False))
        sys.exit(0)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='.lingxi-map-', dir=root))
    try:
        (tmp / 'occupancy_map.png').write_bytes(png)
        (tmp / 'grid_map_info.txt').write_text(f'{ppm:.12g}\n{origin_u:.12g} {origin_v:.12g}\n{width} {height}\n', encoding='utf-8')
        os.chmod(tmp / 'occupancy_map.png', 0o644)
        os.chmod(tmp / 'grid_map_info.txt', 0o644)
        os.chmod(tmp, 0o755)
        os.rename(tmp, target)
    finally:
        if tmp.exists():
            import shutil; shutil.rmtree(tmp)
    print(json.dumps({'ok': True, 'map_name': name, 'map_id': map_id, 'width': width, 'height': height, 'resolution_m': resolution, 'path': str(target)}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'ok': False, 'message': str(exc)}, ensure_ascii=False))
    sys.exit(2)
finally:
    if node is not None:
        node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()`;
}
