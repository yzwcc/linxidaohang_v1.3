const { Client } = require("ssh2");

const robotHost = process.env.ROBOT_HOST?.trim();
const robotPassword = process.env.ROBOT_SSH_PASSWORD;
if (!robotHost || !robotPassword) throw new Error("请设置 ROBOT_HOST 和 ROBOT_SSH_PASSWORD");

const command = [
  "source /opt/ros/humble/setup.bash 2>/dev/null || true",
  'source "$ROBOT_AIMDK_SETUP" 2>/dev/null || true',
  "python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName' 2>/dev/null || echo AIMDK_v1_1_0_interfaces_missing",
  "echo integrated_command",
  "ros2 topic info /integrated_command 2>/dev/null",
  "echo relocalization_pose",
  "ros2 topic info /relocalization_pose 2>/dev/null",
  "echo slam_odom",
  "ros2 topic info /slam/lidar_odom 2>/dev/null",
  "echo nodes",
  "ros2 node list 2>/dev/null",
  "echo relocalize_process",
  "ps -ef | grep relocalize_once | grep -v grep || true"
].join("; ");

const client = new Client();
client.on("ready", () => {
  client.exec(`bash -lc '${command.replace(/'/g, "'\\''")}'`, (error, stream) => {
    if (error) throw error;
    stream.on("close", (code) => {
      console.log("exit", code);
      client.end();
    });
    stream.on("data", (data) => process.stdout.write(data));
    stream.stderr.on("data", (data) => process.stderr.write(data));
  });
});
client.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
client.connect({
  host: robotHost,
  username: process.env.ROBOT_SSH_USER || "agi",
  password: robotPassword,
  readyTimeout: 8000
});
