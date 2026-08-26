const { Client } = require("ssh2");

const robotHost = process.env.ROBOT_HOST?.trim();
const robotPassword = process.env.ROBOT_SSH_PASSWORD;
if (!robotHost || !robotPassword) throw new Error("请设置 ROBOT_HOST 和 ROBOT_SSH_PASSWORD");

const command = [
  "source /opt/ros/humble/setup.bash 2>/dev/null || true",
  "source /home/agi/aimdk/install/setup.bash 2>/dev/null || true",
  "ros2 topic list 2>/dev/null",
  "echo services",
  "ros2 service list 2>/dev/null"
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
