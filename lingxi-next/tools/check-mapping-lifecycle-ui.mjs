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
const inputMode = process.argv[2] ?? "recorded";
assert(["recorded", "live"].includes(inputMode));
const captureLimitSeconds = inputMode === "live" ? 120 : undefined;
const browser = process.env.LINGXI_POINT_BROWSER_BIN ??
  "C:/Users/李伟铭/AppData/Roaming/npm/node_modules/agent-browser/bin/agent-browser-win32-x64.exe";
const report = { classification: "isolated_synthetic_ui_fixture", robotContacted: false,
  nativeSlamExecuted: false, motionCommandsSent: false, inputMode, result: "failed", stages: {} };
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
      captureLimit: document.querySelector('[data-testid="mapping-capture-limit"]')?.innerText,
      optimization: document.querySelector('[data-testid="mapping-optimization"]')?.innerText,
      measurement: document.querySelector('[data-testid="lidar-measurement"]')?.innerText,
      savedMessage: document.querySelector('[data-testid="mapping-save-result"]')?.innerText,
      footer: document.querySelector('.lidar-footer').innerText,
      lidarSockets: window.__lifecycleSockets.length,
      liveLidarSockets: window.__lifecycleSockets.filter(s => s.readyState < 2).length };
  })()`);
  assert.equal(metrics.overflow, false);
  assert.equal(metrics.resultOverflow, false);
  assert.equal(metrics.buttonsWithinViewport, true);
  if (metrics.startColors) assert.notEqual(...metrics.startColors);
  if (name.startsWith("idle") || name.startsWith("saved") || name.startsWith("discarded")) {
    if (inputMode === "live") assert(metrics.captureLimit.includes(`${captureLimitSeconds} 秒`));
    else assert.equal(metrics.captureLimit, undefined);
  }
  report.stages[name] = metrics;
  await run("screenshot", path.join(output, name + ".png"));
  return metrics;
}

try {
  fixture = spawn(process.execPath, ["--import", "tsx", "tools/fixtures/mapping-control-ui.ts",
    path.join(output, "fixture"), "600", inputMode, ...(captureLimitSeconds === undefined ? [] : [String(captureLimitSeconds)])],
    { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
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
  await evaluate(`(() => { window.__lifecycleSockets=[]; window.__lifecycleControls=[]; const Original=window.WebSocket;
    window.WebSocket=class extends Original { constructor(...args) { super(...args);
      if (String(args[0]).endsWith('/api/v1/lidar/events')) window.__lifecycleSockets.push(this); } };
    const originalFetch=window.fetch;
    window.fetch=async (...args) => { const response=await originalFetch(...args);
      const pathname=new URL(String(args[0]),location.href).pathname;
      if (args[1]?.method && args[1].method !== 'GET') window.__lifecycleControls.push(pathname);
      if (pathname.endsWith('/mapping/stop-save') && response.ok) {
        window.__lifecycleSavedId=(await response.clone().json()).savedSlamMap?.id;
      }
      const match=pathname.match(/^\\/api\\/v1\\/slam\\/maps\\/(slam-[a-f0-9]{32})\\/points$/);
      if (match) {
        window.__lifecyclePreviewId=match[1];
        const preview=await response.clone().json();
        const zs=preview.points.filter((_,index)=>index%3===2);
        window.__lifecyclePreviewCounts={available:zs.length,sliced:zs.filter(z=>z>=0&&z<=.15).length};
      }
      return response;
    }; })()`);
  await click("实时建图");
  await wait("document.querySelector('.map-empty-label')?.innerText === '等待开始采集'");
  const idle = await capture("idle-desktop");
  assert.equal(idle.lidarSockets, 0);
  assert.equal(idle.nonBackground, 0);
  await click("开始采集");
  await wait("window.__lifecycleSockets.length > 0 && Boolean(document.querySelector('#map-name'))");
  await wait("document.querySelector('[data-testid=\"mapping-optimization\"]')?.innerText.includes('待优化 4 / 128 帧')");
  const running = await capture("running-desktop");
  assert(running.optimization.includes("历史最大排队 1.2 秒"));
  await run("set", "viewport", "844", "390");
  await evaluate("document.querySelector('[data-testid=\"mapping-optimization\"]').scrollIntoView({block:'center'})");
  await capture("running-landscape");
  await run("set", "viewport", "1440", "900");
  if (inputMode === "live") {
    assert((await evaluate("document.querySelector('[data-testid=\"mapping-capture-limit\"]').innerText"))
      .includes(`${captureLimitSeconds} 秒`));
  }
  const name = "横屏与桌面点云保存验证-" + "展厅".repeat(20);
  await run("fill", "#map-name", name);
  await click("停止并保存");
  await wait("Boolean(document.querySelector('[data-testid=\"mapping-save-result\"]'))");
  await wait("!document.querySelector('.toast')");
  const saved = await capture("saved-desktop");
  assert.equal(saved.optimization, undefined);
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
  const defaultBand = () => evaluate(`({enabled:document.querySelector('input[aria-label="原始 Z 分层"]').checked,
    min:document.querySelector('input[aria-label="原始 Z 下限"]').value,
    max:document.querySelector('input[aria-label="原始 Z 上限"]').value})`);
  assert.deepEqual(await defaultBand(), { enabled: true, min: "0.1", max: "1.4" });
  const handoff = await evaluate("({ saved: window.__lifecycleSavedId, preview: window.__lifecyclePreviewId })");
  assert.match(handoff.saved, /^slam-[a-f0-9]{32}$/);
  assert.equal(handoff.preview, handoff.saved);
  report.savedMapIdentityMatched = true;
  await run("set", "viewport", "1440", "900");
  assert.equal((await capture("preview-desktop")).canvasInViewport, true);
  const controlsBeforeMeasurement = await evaluate("window.__lifecycleControls.length");
  const readMeasurement = () => evaluate(`(() => { const el=document.querySelector('[data-testid="lidar-measurement"]');
    return el ? {distance:Number(el.dataset.distance),text:el.innerText} : null; })()`);
  async function beginMeasurement(dx, dy) {
    const point = await evaluate(`(() => { const r=document.querySelector('.lidar-view canvas').getBoundingClientRect();
      return {x:Math.round(r.left+r.width*.4),y:Math.round(r.top+r.height*.5)}; })()`);
    await run("mouse", "move", String(point.x), String(point.y));
    await run("mouse", "down", "left");
    await run("mouse", "move", String(point.x + dx), String(point.y + dy));
    return point;
  }
  await click("测量 XY 投影距离");
  await beginMeasurement(60, -40); await run("mouse", "up", "left");
  const measured = await readMeasurement();
  assert(measured.distance > 0 && measured.text.includes("raw") && measured.text.includes("非米制标定"));
  await capture("measurement-desktop");
  await click("放大扫描视图");
  assert.deepEqual(await readMeasurement(), measured);
  await click("平移地图");
  await beginMeasurement(20, 10); await run("mouse", "up", "left");
  assert.deepEqual(await readMeasurement(), measured);
  await click("测量 XY 投影距离");
  await beginMeasurement(40, 20); await run("press", "Escape"); await run("mouse", "up", "left");
  assert.deepEqual(await readMeasurement(), measured);
  await run("click", ".lidar-view canvas");
  assert.deepEqual(await readMeasurement(), measured);
  await run("set", "viewport", "844", "390");
  await evaluate("document.querySelector('.lidar-view').scrollIntoView({block:'center'})");
  await capture("measurement-landscape");
  assert.deepEqual(await readMeasurement(), measured);
  const measurementPoint = await beginMeasurement(40, 20);
  await run("mouse", "move", String(measurementPoint.x - 1000), String(measurementPoint.y));
  await run("mouse", "up", "left");
  assert.deepEqual(await readMeasurement(), measured);
  await click("清除测距");
  assert.equal(await readMeasurement(), null);
  await click("测量 XY 投影距离");
  await beginMeasurement(40, 20); await run("mouse", "up", "left");
  assert((await readMeasurement()).distance > 0);
  assert.equal(await evaluate("window.__lifecycleControls.length"), controlsBeforeMeasurement);
  report.measurement = { classification: "raw_xy_projection_only", motionOrCalibrationRequested: false,
    preservedAcrossZoomPanResize: true, cancellationPreservesPrevious: true, clearPassed: true };
  await run("set", "viewport", "1440", "900");
  await click("主体范围");
  assert.equal(await readMeasurement(), null);
  assert.equal(await evaluate("document.querySelector('.segmented button[aria-pressed=true]').textContent"), "主体范围");
  await run("find", "label", "原始 Z 分层", "check");
  await run("find", "label", "原始 Z 下限", "fill", "2");
  await wait("document.querySelector('.map-empty-label')?.innerText === '请输入有效的原始 Z 范围'");
  await run("find", "label", "原始 Z 下限", "fill", "0");
  await run("find", "label", "原始 Z 上限", "fill", ".15");
  const expected = await evaluate("window.__lifecyclePreviewCounts");
  assert(expected.sliced > 0 && expected.sliced < expected.available);
  const sliceCounts = () => evaluate(`(() => {const el=document.querySelector('[data-testid="lidar-slice-count"]');
    return el ? {visible:Number(el.dataset.visible),available:Number(el.dataset.available)} : null;})()`);
  await wait(`document.querySelector('[data-testid="lidar-slice-count"]')?.dataset.visible === '${expected.sliced}'`);
  assert.deepEqual(await sliceCounts(), { visible: expected.sliced, available: expected.available });
  const sliced = await capture("sliced-desktop");
  assert(sliced.nonBackground > 100);
  await run("set", "viewport", "844", "390");
  await evaluate("document.querySelector('.lidar-view').scrollIntoView({block:'center'})");
  assert.equal((await capture("sliced-landscape")).canvasInViewport, true);
  assert.deepEqual(await sliceCounts(), { visible: expected.sliced, available: expected.available });
  await run("set", "viewport", "1440", "900");
  await run("find", "label", "原始 Z 下限", "fill", "100");
  await run("find", "label", "原始 Z 上限", "fill", "101");
  await wait("document.querySelector('.map-empty-label')?.innerText === '当前原始 Z 范围内没有点'");
  assert.deepEqual(await sliceCounts(), { visible: 0, available: expected.available });
  assert.equal((await capture("slice-empty-desktop")).nonBackground, 0);
  await run("find", "label", "原始 Z 上限", "fill", "-1");
  await wait("document.querySelector('.map-empty-label')?.innerText === '请输入有效的原始 Z 范围'");
  await run("uncheck", 'input[aria-label="原始 Z 分层"]');
  assert.equal(await sliceCounts(), null);
  assert((await capture("slice-restored-desktop")).nonBackground > 100);
  await click("全部范围");
  assert.equal(await evaluate("document.querySelector('.segmented button[aria-pressed=true]').textContent"), "全部范围");
  assert.equal(await evaluate("window.__lifecycleControls.length"), controlsBeforeMeasurement);
  report.display = { classification: "display_only_raw_coordinates", defaultBand: { min: .1, max: 1.4, enabled: true },
    sourceCountsMatched: true, emptyAndInvalidBandsDistinct: true, disableRestoresPoints: true,
    rangeTogglePassed: true, displayChangeClearsMeasurement: true, controlRequestsSent: 0 };
  await click("主体范围");
  await run("find", "label", "原始 Z 分层", "check");
  await click("实时建图");
  assert.equal(await readMeasurement(), null);
  await click("地图管理");
  await click(`查看 ${name} 点云`);
  await wait("document.querySelector('.lidar-footer')?.innerText.includes('已保存点云')");
  assert.equal(await readMeasurement(), null);
  assert.equal(await evaluate("document.querySelector('.lidar-view canvas').dataset.interaction"), "pan");
  assert.equal(await evaluate("document.querySelector('.segmented button[aria-pressed=true]').textContent"), "全部范围");
  assert.deepEqual(await defaultBand(), { enabled: true, min: "0.1", max: "1.4" });
  report.display.resetOnReopenPassed = true;
  report.measurement.resetOnReopenPassed = true;
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
