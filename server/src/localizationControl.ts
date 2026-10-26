import type { AppConfig } from "./config.js";
import { runRobotSshCommand, shellQuote } from "./sshControl.js";

export type CurrentLocalization = {
  ok: true;
  topic: string;
  acquired_at: string;
  pose: {
    position: { x: number; y: number; z: number };
    orientation: { x: number; y: number; z: number; w: number };
    yaw_rad: number;
    yaw_deg: number;
  };
};

const odomTopic = "/slam/lidar_odom";

export async function getCurrentLocalization(config: AppConfig): Promise<CurrentLocalization> {
  const script = [
    "set +u",
    `export ROBOT_LOCAL_NAVIGATION_V3_HOME=${shellQuote(config.robot.navHome)}`,
    "export PYTHONPATH=" + shellQuote(config.robot.navHome) + ":${PYTHONPATH:-}",
    "[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash",
    "[ -f /agibot/software/common/local_setup.bash ] && source /agibot/software/common/local_setup.bash",
    `AIMDK_SETUP=${shellQuote(config.robot.aimdkSetup || "")}`,
    "[ -n \"$AIMDK_SETUP\" ] && [ -r \"$AIMDK_SETUP\" ] || { echo AIMDK_v1_1_0_setup_missing; exit 1; }",
    "source \"$AIMDK_SETUP\"",
    "python3 -c 'from aimdk_msgs.msg import PncTaskRequest' >/dev/null 2>&1 || { echo AIMDK_v1_1_0_interface_unavailable; exit 1; }",
    "[ -f /agibot/software/entry/cfg/ros_dds_configuration.xml ] && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml",
    "[ -f /agibot/software/nav/bin/librmw_fastrtps.so ] && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so",
    `timeout 8 ros2 topic echo --once ${odomTopic} --field pose.pose`
  ].join("; ");
  const command = `cd ${shellQuote(config.robot.navHome)} && bash -lc ${shellQuote(script)}`;
  const output = await runRobotSshCommand(command, config, 12000);
  const pose = parsePose(output);
  if (!pose) {
    throw new Error("未能读取当前定位，请确认机器人导航层已启动，且 /slam/lidar_odom 有数据");
  }
  return {
    ok: true,
    topic: odomTopic,
    acquired_at: new Date().toISOString(),
    pose
  };
}

function parsePose(output: string): CurrentLocalization["pose"] | null {
  const positionBlock = output.match(/position:\s*([\s\S]*?)orientation:/);
  const orientationBlock = output.match(/orientation:\s*([\s\S]*)/);
  if (!positionBlock || !orientationBlock) return null;
  const position = {
    x: readNumber(positionBlock[1], "x"),
    y: readNumber(positionBlock[1], "y"),
    z: readNumber(positionBlock[1], "z")
  };
  const orientation = {
    x: readNumber(orientationBlock[1], "x"),
    y: readNumber(orientationBlock[1], "y"),
    z: readNumber(orientationBlock[1], "z"),
    w: readNumber(orientationBlock[1], "w")
  };
  if (![position.x, position.y, position.z, orientation.x, orientation.y, orientation.z, orientation.w].every(Number.isFinite)) {
    return null;
  }
  const yawRad = yawFromQuaternion(orientation);
  return {
    position,
    orientation,
    yaw_rad: yawRad,
    yaw_deg: yawRad * 180 / Math.PI
  };
}

function readNumber(block: string, key: string): number {
  const match = block.match(new RegExp(`(?:^|\\n)\\s*${key}:\\s*([-+0-9.eE]+)`));
  return match ? Number(match[1]) : Number.NaN;
}

function yawFromQuaternion(q: { x: number; y: number; z: number; w: number }): number {
  const siny = 2 * (q.w * q.z + q.x * q.y);
  const cosy = 1 - 2 * (q.y * q.y + q.z * q.z);
  return Math.atan2(siny, cosy);
}
