/** Isolated synthetic UI checks; no GLIM, robot connection or motion commands. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { closeSync, openSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

const output = path.resolve(".local", `mapping-lifecycle-ui-${randomUUID()}`);
await mkdir(output, { recursive: true });
const session = `mapping-lifecycle-${randomUUID()}`;
const browser = process.env.LINGXI_POINT_BROWSER_BIN ??
  "C:/Users/李伟铭/AppData/Roaming/npm/node_modules/agent-browser/bin/agent-browser-win32-x64.exe";
const report = { classification: "isolated_synthetic_ui_fixture", robotContacted: false,
  nativeSlamExecuted: false, motionCommandsSent: false, result: "failed", stages: {} };
let fixture, web;

async function run(...args) {
  const stdout = openSync(path.join(output, "browser-out.tmp"), "w");
  const stderr = openSync(path.join(output, "browser-err.tmp"), "w");
  try {
    const child = spawn(browser, ["--session", session,
      "--allowed-domains", "127.0.0.1,localhost", "--json", ...args], {
      stdio: ["ignore", stdout, stderr], windowsHide: true,
      env: { ...process.env, NO_PROXY: "127.0.0.1,localhost" },
    });
    const code = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { child.kill(); reject(new Error("Browser command timed out")); }, 45_000);
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      child.once("exit", (status) => { clearTimeout(timer); resolve(status); });
    });
    const text = await readFile(path.join(output, "browser-out.tmp"), "utf8");
    assert.equal(code, 0, text || await readFile(path.join(output, "browser-err.tmp"), "utf8"));
    const result = JSON.parse(text);
    assert.equal(result.success, true, text);
    return result.data;
  } finally { closeSync(stdout); closeSync(stderr); }
}
const evaluate = async (source) => (await run("eval", "-b", Buffer.from(source).toString("base64"))).result;
const wait = (source) => run("wait", "--fn", source);
const click = (name) => run("find", "role", "button", "click", "--name", name, "--exact");

async function capture(name) {
  const metrics = await evaluate(`(() => {
    const canvas = document.querySelector('.lidar-view canvas');
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let nonBackground = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i] !== 250 || pixels[i+1] !== 251 || pixels[i+2] !== 252) nonBackground++;
    }
    const controls = [...document.querySelectorAll('.mapping-save-result button')];
    const start = document.querySelector('.btn.accent');
    const startStyle = start && getComputedStyle(start);
    const rect=canvas.getBoundingClientRect();
    return { viewport: [innerWidth,innerHeight], canvas: [canvas.width,canvas.height], nonBackground,
      overflow: document.documentElement.scrollWidth > innerWidth,
      canvasInViewport: Math.max(0,Math.min(rect.bottom,innerHeight)-Math.max(rect.top,0)) >= rect.height * .8,
      resultOverflow: [...document.querySelectorAll('.mapping-save-result, .mapping-save-result span')]
        .some(el => el.scrollWidth > el.clientWidth + 1),
      buttonsWithinViewport: controls.every(el => { const r=el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; }),
      startColors: startStyle && [startStyle.color, startStyle.backgroundColor],
      placeholder: document.querySelector('.map-empty-label')?.innerText,
      savedMessage: document.querySelector('[data-testid="mapping-save-result"]')?.innerText,
      footer: document.querySelector('.lidar-footer').innerText,
      lidarSockets: window.__lifecycleSockets.length,
      liveLidarSockets: window.__lifecycleSockets.filter(s => s.readyState < 2).length };
  })()`);
  assert.equal(metrics.overflow, false);
  assert.equal(metrics.resultOverflow, false);
  assert.equal(metrics.buttonsWithinViewport, true);
  if (metrics.startColors) assert.notEqual(...metrics.startColors);
  report.stages[name] = metrics;
  await run("screenshot", path.join(output, name + ".png"));
  return metrics;
}

try {
  fixture = spawn(process.execPath, ["--import", "tsx", "tools/fixtures/mapping-control-ui.ts",
    path.join(output, "fixture"), "600"], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  const info = await new Promise((resolve, reject) => {
    let text = "", errors = "";
    const timer = setTimeout(() => reject(new Error("Fixture startup deadline: " + errors)), 15_000);
    fixture.stderr.on("data", bytes => { errors += bytes; });
    fixture.once("error", reject);
    fixture.once("exit", code => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${errors}`)); });
    fixture.stdout.on("data", bytes => {
      text += bytes;
      if (text.includes("\n")) { clearTimeout(timer); resolve(JSON.parse(text.split("\n")[0])); }
    });
  });
  assert.equal(info.classification, "isolated_synthetic_ui_fixture");
  assert.equal(new URL(info.url).hostname, "127.0.0.1");
  web = await createServer({ server: { host: "127.0.0.1", port: 5197, strictPort: false, open: false } });
  await web.listen();
  const url = web.resolvedUrls.local[0];
  await run("open", url);
  await run("set", "viewport", "1440", "900");
  await run("fill", "#robot-address", info.url);
  await run("fill", "#pairing-code", info.pairingCode);
  await run("click", "button[type=submit]");
  await wait("Boolean(document.querySelector('.demo-banner'))");
  await evaluate(`(() => { window.__lifecycleSockets=[]; const Original=window.WebSocket;
    window.WebSocket=class extends Original { constructor(...args) { super(...args);
      if (String(args[0]).endsWith('/api/v1/lidar/events')) window.__lifecycleSockets.push(this); } };
    const originalFetch=window.fetch;
    window.fetch=async (...args) => { const response=await originalFetch(...args);
      const pathname=new URL(String(args[0]),location.href).pathname;
      if (pathname.endsWith('/mapping/stop-save') && response.ok) {
        window.__lifecycleSavedId=(await response.clone().json()).savedSlamMap?.id;
      }
      const match=pathname.match(/^\\/api\\/v1\\/slam\\/maps\\/(slam-[a-f0-9]{32})\\/points$/);
      if (match) window.__lifecyclePreviewId=match[1];
      return response;
    }; })()`);
  await click("实时建图");
  await wait("document.querySelector('.map-empty-label')?.innerText === '等待开始采集'");
  const idle = await capture("idle-desktop");
  assert.equal(idle.lidarSockets, 0);
  assert.equal(idle.nonBackground, 0);
  await click("开始采集");
  await wait("window.__lifecycleSockets.length > 0 && Boolean(document.querySelector('#map-name'))");
  const name = "横屏与桌面点云保存验证-" + "展厅".repeat(20);
  await run("fill", "#map-name", name);
  await click("停止并保存");
  await wait("Boolean(document.querySelector('[data-testid=\"mapping-save-result\"]'))");
  await wait("!document.querySelector('.toast')");
  const saved = await capture("saved-desktop");
  assert.equal(saved.placeholder, "等待开始采集");
  assert.equal(saved.liveLidarSockets, 0);
  assert(saved.savedMessage.includes(name));
  await run("set", "viewport", "844", "390");
  await evaluate(`document.querySelector('[data-testid="mapping-save-result"]').scrollIntoView({block:'center'})`);
  await capture("saved-landscape");
  await click("查看点云");
  await wait("document.querySelector('.lidar-footer')?.innerText.includes('已保存点云')");
  const preview = await capture("preview-landscape");
  assert(preview.nonBackground > 0);
  assert.equal(preview.canvasInViewport, true);
  assert.equal(preview.liveLidarSockets, 0);
  assert(preview.footer.includes("原始坐标 / 未标定"));
  const handoff = await evaluate("({ saved: window.__lifecycleSavedId, preview: window.__lifecyclePreviewId })");
  assert.match(handoff.saved, /^slam-[a-f0-9]{32}$/);
  assert.equal(handoff.preview, handoff.saved);
  report.savedMapIdentityMatched = true;
  await run("set", "viewport", "1440", "900");
  assert.equal((await capture("preview-desktop")).canvasInViewport, true);
  await click("实时建图");
  await click("开始采集");
  await wait("Boolean(document.querySelector('#map-name'))");
  await click("停止并放弃");
  await run("dialog", "accept");
  await wait("document.querySelector('.map-empty-label')?.innerText === '等待开始采集'");
  const discarded = await capture("discarded-desktop");
  assert.equal(discarded.savedMessage, undefined);
  assert.equal(discarded.liveLidarSockets, 0);
  assert.deepEqual(await evaluate("Object.keys(localStorage)"), ["lingxi.robotAddress"]);
  assert.deepEqual((await run("errors")).errors, []);
  report.result = "synthetic_mapping_lifecycle_and_saved_pointcloud_handoff_passed";
} catch (error) {
  report.error = { name: error.name, message: error.message };
  throw error;
} finally {
  try { await run("close"); } catch { /* The local fixture has an independent deadline. */ }
  if (web) await web.close();
  if (fixture && fixture.exitCode === null) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture shutdown deadline")), 10_000);
      fixture.once("exit", () => { clearTimeout(timer); resolve(); });
      fixture.kill();
    });
  }
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify({ result: report.result, output }));
