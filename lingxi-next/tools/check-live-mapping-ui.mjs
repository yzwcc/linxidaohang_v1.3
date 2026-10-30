/** One browser lifetime for a paired live mapper. Pairing code arrives on stdin only. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, openSync, readFileSync } from "node:fs";
import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

const [url, spki, session, directory] = process.argv.slice(2);
const target = new URL(url);
assert.equal(target.protocol, "https:");
assert(!target.username && !target.password && !target.hash && !target.search && target.pathname === "/");
assert.match(spki, /^[A-Za-z0-9+/]{43}=$/);
assert.match(session, /^live-mapping-ui-[a-z0-9-]+$/);
assert(path.isAbsolute(directory));
const code = readFileSync(0, "utf8").trim();
assert(code.length >= 8 && code.length <= 128);
await mkdir(directory, { mode: 0o700 });
const browser = process.env.LINGXI_POINT_BROWSER_BIN ??
  "C:/Users/李伟铭/AppData/Roaming/npm/node_modules/agent-browser/bin/agent-browser-win32-x64.exe";
const run = (...arguments_) => {
  const output = path.join(directory, "cli-output.tmp");
  const errors = path.join(directory, "cli-errors.tmp");
  const stdout = openSync(output, "w", 0o600), stderr = openSync(errors, "w", 0o600);
  let command;
  try {
    command = spawnSync(browser, ["--session", session, "--allowed-domains", target.hostname,
      "--args", `--ignore-certificate-errors-spki-list=${spki}`, "--json", ...arguments_], {
      stdio: ["ignore", stdout, stderr], windowsHide: true, timeout: 45_000,
    });
  } finally { closeSync(stdout); closeSync(stderr); }
  const text = readFileSync(output, "utf8"), error = readFileSync(errors, "utf8");
  assert.ifError(command.error);
  assert.equal(command.status, 0, error || text);
  const result = JSON.parse(text);
  assert.equal(result.success, true, JSON.stringify(result));
  return result.data;
};
const click = (name) => run("find", "role", "button", "click", "--name", name, "--exact");
const evaluate = (code) => run("eval", "-b", Buffer.from(code).toString("base64")).result;
const report = { result: "failed", robotContacted: true, syntheticData: false,
  motionCommandsSent: false, geometryValidated: false, navigationReady: false, viewports: [], screenshots: {} };
try {
  run("open", url);
  run("fill", "#robot-address", url);
  run("fill", "#pairing-code", code);
  click("连接机器人");
  run("wait", "--fn", "document.body.innerText.includes('运行概览') || document.querySelector('nav') !== null");
  click("实时建图");
  run("wait", "--fn", "Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('开始采集') && !b.disabled)");
  click("开始采集");
  run("wait", "--fn", "document.querySelector('.lidar-footer')?.innerText.includes('实时 SLAM')");
  for (const [width, height] of [[1440, 900], [844, 390]]) {
    run("set", "viewport", String(width), String(height));
    const metric = evaluate(`(() => {
      const canvas = document.querySelector('.lidar-view canvas');
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let nonBackground = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] !== 250 || pixels[i + 1] !== 251 || pixels[i + 2] !== 252) nonBackground++;
      }
      return { viewport: [innerWidth, innerHeight], canvas: [canvas.width, canvas.height], nonBackground,
        label: canvas.getAttribute('aria-label'), footer: document.querySelector('.lidar-footer').innerText,
        summary: document.querySelector('.mapping-summary').innerText,
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
        demoBanner: Boolean(document.querySelector('.demo-banner')),
        liveMode: document.body.innerText.includes('GLIM · 实时激光惯性建图'),
        recordedMode: document.body.innerText.includes('录制数据建图联调') };
    })()`);
    assert.equal(metric.horizontalOverflow, false);
    assert.equal(metric.demoBanner, false);
    assert.equal(metric.liveMode, true);
    assert.equal(metric.recordedMode, false);
    assert.equal(metric.label, "激光雷达实时扫描点云");
    assert(metric.canvas.every((size) => size > 100) && metric.nonBackground > 100);
    assert(metric.summary.includes("扫描") && metric.summary.includes("位姿"));
    report.viewports.push(metric);
    const file = path.join(directory, `live-${width}x${height}.png`);
    run("screenshot", file);
    report.screenshots[path.basename(file)] = createHash("sha256").update(await readFile(file)).digest("hex");
  }
  run("fill", "#map-name", "现场实时建图验证");
  click("停止并保存");
  run("wait", "--fn", "document.body.innerText.includes('地图保存已确认。')");
  report.saveConfirmedInUi = true;
  report.result = "actual_robot_live_mapping_desktop_landscape_rendering_and_save_checked";
} catch (error) {
  report.error = { name: error.name, message: error.message.replaceAll(code, "[redacted]") };
  try {
    report.pageText = evaluate("document.body.innerText").replaceAll(code, "[redacted]");
    report.browserErrors = run("errors");
    run("screenshot", path.join(directory, "failure.png"));
  } catch { /* Keep the original failure when browser diagnostics are unavailable. */ }
  throw error;
} finally {
  run("close");
  await Promise.all(["cli-output.tmp", "cli-errors.tmp"].map((file) => unlink(path.join(directory, file)).catch(() => {})));
  await writeFile(path.join(directory, "browser-report.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx", mode: 0o600 });
}
console.log(JSON.stringify({ result: report.result, viewports: report.viewports.length }));
