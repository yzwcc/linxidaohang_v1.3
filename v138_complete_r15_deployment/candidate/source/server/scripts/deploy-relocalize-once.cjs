const fs = require("node:fs");
const path = require("node:path");
const { Client } = require("ssh2");

const robotHost = process.env.ROBOT_HOST?.trim();
const robotPassword = process.env.ROBOT_SSH_PASSWORD;
if (!robotHost || !robotPassword) throw new Error("请设置 ROBOT_HOST 和 ROBOT_SSH_PASSWORD");

const localFile = path.resolve(
  __dirname,
  "../../robot_local_navigation_v3/robot_local_navigation_v3-new-auto-return-interaction-end/robot_local_navigation_v3/relocalize_once.py"
);
const remoteFile = "/home/agi/robot_local_navigation_v3/robot_local_navigation_v3/relocalize_once.py";

const client = new Client();
client.on("ready", () => {
  client.sftp((sftpError, sftp) => {
    if (sftpError) throw sftpError;
    sftp.fastPut(localFile, remoteFile, { mode: 0o644 }, (putError) => {
      if (putError) {
        console.error(putError.message);
        client.end();
        process.exit(1);
        return;
      }
      console.log(`uploaded ${localFile}`);
      console.log(`remote ${remoteFile}`);
      client.end();
    });
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
