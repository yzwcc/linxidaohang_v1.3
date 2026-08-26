const fs = require("node:fs");
const path = require("node:path");
const { Client } = require("ssh2");

const [action, ...args] = process.argv.slice(2);
const host = process.env.SSH_HOST;
const username = process.env.SSH_USER;
const password = process.env.SSH_PASSWORD;
const port = Number(process.env.SSH_PORT || 22);

if (!host || !username || !password) {
  throw new Error("SSH_HOST、SSH_USER、SSH_PASSWORD 均为必填环境变量");
}

function connect() {
  return new Promise((resolve, reject) => {
    const client = new Client();
    client.once("ready", () => resolve(client));
    client.once("error", reject);
    client.connect({ host, port, username, password, readyTimeout: 12000 });
  });
}

function exec(client, command) {
  return new Promise((resolve, reject) => {
    client.exec(command, (error, stream) => {
      if (error) return reject(error);
      stream.pipe(process.stdout);
      stream.stderr.pipe(process.stderr);
      stream.once("close", (code) => code === 0 ? resolve() : reject(new Error(`远程命令退出码：${code}`)));
    });
  });
}

function upload(client, localFile, remoteFile, mode) {
  return new Promise((resolve, reject) => {
    client.sftp((error, sftp) => {
      if (error) return reject(error);
      const remoteDir = path.posix.dirname(remoteFile);
      exec(client, `mkdir -p '${remoteDir.replace(/'/g, "'\\''")}'`)
        .then(() => sftp.fastPut(localFile, remoteFile, mode ? { mode: Number.parseInt(mode, 8) } : {}, (putError) => {
          sftp.end();
          putError ? reject(putError) : resolve();
        }))
        .catch(reject);
    });
  });
}

async function main() {
  const client = await connect();
  try {
    if (action === "exec" && args[0]) {
      await exec(client, args[0]);
      return;
    }
    if (action === "upload" && args[0] && args[1]) {
      if (!fs.existsSync(args[0])) throw new Error(`本地文件不存在：${args[0]}`);
      await upload(client, args[0], args[1], args[2]);
      return;
    }
    throw new Error("用法：ssh-tool.cjs exec <command> | upload <local> <remote> [mode]");
  } finally {
    client.end();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
