const { Client } = require("ssh2");

const robotHost = process.env.ROBOT_HOST?.trim();
const robotPassword = process.env.ROBOT_SSH_PASSWORD;
if (!robotHost || !robotPassword) throw new Error("请设置 ROBOT_HOST 和 ROBOT_SSH_PASSWORD");

const services = [
  "/aimdk_2Eprotocol_2ERelocalizationService/StartNormalRelocalization",
  "/aimdk_2Eprotocol_2ERelocalizationService/StartGlobalRelocalization",
  "/aimdk_2Eprotocol_2ERelocalizationService/StopNormalRelocalization",
  "/aimdk_2Eprotocol_2ERelocalizationService/SetRelocalizationResult"
];
const command = [
  "source /opt/ros/humble/setup.bash 2>/dev/null || true",
  'source "$ROBOT_AIMDK_SETUP" 2>/dev/null || true',
  "python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName' 2>/dev/null || echo AIMDK_v1_1_0_interfaces_missing",
  "echo WRAPPER",
  "ros2 interface show ros2_plugin_proto/srv/RosRpcWrapper 2>&1 || true",
  ...services.flatMap((service) => [
    `echo SERVICE ${service}`,
    `type=$(ros2 service type ${service} 2>/dev/null || true); echo TYPE $type; if [ -n "$type" ]; then ros2 interface show "$type" 2>/dev/null; fi`
  ])
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
