const { Client } = require("ssh2");

const robotHost = process.env.ROBOT_HOST?.trim();
const robotPassword = process.env.ROBOT_SSH_PASSWORD;
if (!robotHost || !robotPassword) throw new Error("请设置 ROBOT_HOST 和 ROBOT_SSH_PASSWORD");

const command = "python3 -c \"from pathlib import Path; s=Path('/home/agi/robot_local_navigation_v3/robot_local_navigation_v3/relocalize_once.py').read_text(encoding='utf-8'); print('subscriber timeout patch', 'Relocalization subscriber wait timed out' in s)\"";

const client = new Client();
client.on("ready", () => {
  client.exec(command, (error, stream) => {
    if (error) throw error;
    stream.on("close", (code) => {
      console.log(`exit ${code}`);
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
