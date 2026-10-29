import { spawn } from "node:child_process";
import { createHash, X509Certificate } from "node:crypto";
import { once } from "node:events";
import { chmod, mkdir, open, readFile, realpath, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

import { session, syntheticPacket } from "./synthetic-point-packet.mjs";
export { session, syntheticPacket };
export const pairingCode = "isolated-synthetic-point-ui";

async function bounded(promise, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(label)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

export async function startSyntheticPreview({ api, web, output, seconds = 900 }) {
  if (process.platform !== "linux" || process.arch !== "x64" ||
      !Number.isFinite(seconds) || seconds < 5 || seconds > 900) {
    throw new Error("bounded_linux_x64_synthetic_preview_required");
  }
  api = await realpath(api);
  web = await realpath(web);
  output = path.resolve(output);
  if (await realpath(path.dirname(output)) !== path.dirname(output) ||
      [api, web].some((input) => input === output || input.startsWith(output + path.sep) ||
        output.startsWith(input + path.sep))) throw new Error("new_output_outside_preview_inputs_required");
  await mkdir(output, { mode: 0o700 });
  const certificate = path.join(output, "synthetic-cert.pem"), key = path.join(output, "synthetic-key.pem");
  const openssl = spawn("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", key, "-out", certificate, "-days", "1",
    "-subj", "/CN=Lingxi isolated synthetic UI", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
  { stdio: "ignore" });
  if ((await bounded(once(openssl, "exit"), 15_000, "fixture_certificate_deadline"))[0] !== 0) {
    throw new Error("fixture_certificate_generation_failed");
  }
  await Promise.all([chmod(key, 0o600), chmod(certificate, 0o600)]);
  const cert = new X509Certificate(await readFile(certificate));
  const spki = createHash("sha256").update(cert.publicKey.export({ type: "spki", format: "der" })).digest("base64");
  const log = await open(path.join(output, "private-api.log"), "wx", 0o600);
  const child = spawn(process.execPath, [api], {
    cwd: output,
    env: {
      PATH: "/usr/bin:/bin", LANG: "C.UTF-8", LINGXI_DEMO_MODE: "1", LINGXI_PORT: "0",
      LINGXI_DATA_DIR: path.join(output, "data"), LINGXI_WEB_ROOT: web,
      LINGXI_DEMO_PAIRING_CODE: pairingCode, LINGXI_SLAM_POINT_FD: "3",
      LINGXI_SLAM_SESSION: session, LINGXI_SLAM_INPUT_MODE: "live",
      LINGXI_POINT_READ_ONLY: "1", LINGXI_POINT_PARENT_STDIN: "1",
      LINGXI_TLS_KEY: key, LINGXI_TLS_CERT: certificate,
    },
    stdio: ["pipe", "pipe", log.fd, "pipe"],
  });
  await log.close();
  const exit = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  let sequence = 0, paused = false, ended = false, timer, expiry, stopping, control, readyText = "";
  const report = { inputClassification: "isolated_synthetic_ui_fixture", robotContacted: false,
    nativeSlamExecuted: false, targetRenderingValidated: false, motionCommandsSent: false };
  const status = () => ({ inputClassification: report.inputClassification, sequence, paused, ended });
  const stop = () => stopping ??= (async () => {
    clearInterval(timer); clearTimeout(expiry);
    if (control) {
      control.closeAllConnections();
      await new Promise((resolve) => control.close(resolve));
    }
    child.stdin.end();
    child.stdio[3].end();
    let forced = false, result;
    try { result = await bounded(exit, 5000, "fixture_api_shutdown_deadline"); }
    catch {
      forced = true;
      child.kill("SIGKILL");
      result = await bounded(exit, 2000, "fixture_forced_cleanup_deadline");
    }
    const final = { ...report, ...status(), apiExit: result, forcedTermination: forced };
    await writeFile(path.join(output, "synthetic-preview-report.json"), JSON.stringify(final, null, 2) + "\n",
      { flag: "wx", mode: 0o600 });
    return final;
  })();
  child.stdin.on("error", () => { void stop(); });
  child.stdio[3].on("error", () => { void stop(); });
  try {
    const ready = await bounded(new Promise((resolve, reject) => {
      child.stdout.on("data", (bytes) => {
        readyText += bytes;
        if (readyText.length > 4096) { reject(new Error("bounded_fixture_api_status_required")); return; }
        if (!readyText.includes("\n")) return;
        try {
          const event = JSON.parse(readyText.trim());
          if (event.event !== "listening" || !Number.isInteger(event.port) || event.port < 1) {
            throw new Error("fixture_api_listening_status_required");
          }
          resolve(event);
        } catch (error) { reject(error); }
      });
      void exit.then(() => reject(new Error("fixture_api_exited_before_ready")), reject);
    }), 15_000, "fixture_api_startup_deadline");
    timer = setInterval(() => {
      if (paused || ended || stopping || child.stdio[3].writableNeedDrain) return;
      child.stdio[3].write(syntheticPacket(++sequence));
    }, 200);
    expiry = setTimeout(() => { void stop(); }, seconds * 1000);
    const runtime = {
      info: { ...report, url: `https://127.0.0.1:${ready.port}`, pairingCode, certificateSpkiSha256: spki },
      exit, stop,
      command(command) {
        if (stopping) throw new Error("active_synthetic_preview_required");
        if (command === "pause") paused = true;
        else if (command === "resume" && !ended) paused = false;
        else if (command === "eof") { ended = true; child.stdio[3].end(); }
        else if (command !== "status") throw new Error("known_synthetic_preview_command_required");
        return status();
      },
    };
    control = createServer((request, response) => {
      const command = request.url?.slice(1);
      if (request.method !== "POST" || !["pause", "resume", "eof", "status"].includes(command)) {
        response.writeHead(404).end();
        return;
      }
      try {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify(runtime.command(command)));
      } catch { response.writeHead(409).end(); }
    });
    await new Promise((resolve, reject) => {
      control.once("error", reject);
      control.listen(0, "127.0.0.1", resolve);
    });
    runtime.info.controlUrl = `http://127.0.0.1:${control.address().port}`;
    return runtime;
  } catch (error) {
    await stop();
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 7 || process.argv[6] !== "--isolated-synthetic-only") {
      throw new Error("explicit_synthetic_ui_fixture_arguments_required");
    }
    const runtime = await startSyntheticPreview({
      api: process.argv[2], web: process.argv[3], output: process.argv[4], seconds: Number(process.argv[5]),
    });
    console.log(JSON.stringify(runtime.info));
    const input = createInterface({ input: process.stdin });
    input.on("line", (line) => {
      if (line === "stop") { input.close(); return; }
      try { console.log(JSON.stringify(runtime.command(line))); }
      catch { console.error("Unknown synthetic preview command."); }
    });
    input.once("close", () => { void runtime.stop(); });
    for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => input.close());
    await runtime.exit;
    input.close();
    const report = await runtime.stop();
    process.exitCode = report.apiExit.code === 0 && !report.forcedTermination ? 0 : 1;
  } catch {
    console.error("Isolated synthetic point UI preview failed.");
    process.exitCode = 1;
  }
}
