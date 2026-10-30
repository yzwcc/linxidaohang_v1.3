import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, openSync, readFileSync, unlinkSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const [url, spki, session, outputArgument] = process.argv.slice(2);
assert.equal(process.argv.length, 6);
const parsed = new URL(url);
assert.equal(parsed.hostname, "127.0.0.1");
assert.equal(parsed.protocol, "https:");
assert.equal(parsed.pathname, "/__synthetic/index");
assert(!parsed.username && !parsed.password && !parsed.search && !parsed.hash);
assert.match(spki, /^[A-Za-z0-9+/]{43}=$/);
assert.match(session, /^localization-ui-[a-z0-9-]+$/);
const output = path.resolve(outputArgument);
await mkdir(output, { mode: 0o700 });
const browser = process.env.LINGXI_POINT_BROWSER_BIN ??
  "C:/Users/李伟铭/AppData/Roaming/npm/node_modules/agent-browser/bin/agent-browser-win32-x64.exe";
const run = (...args) => {
  // The Windows browser daemon must not inherit pipes from this synchronous driver.
  const stdoutPath = path.join(output, "cli-stdout.tmp"), stderrPath = path.join(output, "cli-stderr.tmp");
  const stdout = openSync(stdoutPath, "w", 0o600), stderr = openSync(stderrPath, "w", 0o600);
  let command;
  try {
    command = spawnSync(browser, ["--session", session, "--allowed-domains", "127.0.0.1,localhost",
      "--args", `--ignore-certificate-errors-spki-list=${spki}`, "--json", ...args], {
      stdio: ["ignore", stdout, stderr], timeout: 40_000, windowsHide: true,
    });
  } finally { closeSync(stdout); closeSync(stderr); }
  const text = readFileSync(stdoutPath, "utf8"), error = readFileSync(stderrPath, "utf8");
  unlinkSync(stdoutPath); unlinkSync(stderrPath);
  assert.ifError(command.error);
  assert.equal(command.status, 0, error || text);
  const result = JSON.parse(text);
  assert.equal(result.success, true, JSON.stringify(result));
  return result.data;
};
const evaluate = (source) => run("eval", "-b", Buffer.from(source).toString("base64")).result;
const wait = (source) => run("wait", "--fn", source);
const click = (name) => run("find", "role", "button", "click", "--name", name, "--exact");
const control = (command) => {
  const result = evaluate(`(async () => {
    const response = await fetch('/__synthetic/${command}', {signal:AbortSignal.timeout(3000)});
    return {status:response.status,body:await response.json()};
  })()`);
  assert.equal(result.status, 200);
  assert.equal(result.body.classification, "isolated_synthetic_ui_fixture");
  return result.body;
};
const report = { schemaVersion: 1, date: "2026-10-30", classification: "isolated_synthetic_ui_fixture",
  robotContacted: false, nativeSlamExecuted: false, targetRenderingValidated: false,
  motionCommandsSent: false, result: "failed", stages: {}, screenshots: {} };

function metric() {
  const canvas = document.querySelector(".localization-cloud canvas");
  let nonBackground = 0, markerPixels = 0;
  if (canvas) {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] !== 250 || data[i + 1] !== 251 || data[i + 2] !== 252) nonBackground++;
      if (data[i] === 40 && data[i + 1] === 109 && data[i + 2] === 155) markerPixels++;
    }
  }
  const controls = document.querySelector(".localization-controls");
  const cloud = document.querySelector(".localization-cloud");
  const a = controls?.getBoundingClientRect(), b = cloud?.getBoundingClientRect();
  const button = (name) => [...document.querySelectorAll("button")].find(item => item.textContent === name);
  return { viewport: [innerWidth, innerHeight], canvas: canvas ? [canvas.width, canvas.height] : null,
    nonBackground, markerPixels, pose: document.querySelector('[data-testid="localization-pose"]')?.textContent ?? null,
    status: document.querySelector('[data-testid="localization-status"]')?.innerText ?? null,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
    panelOverlap: Boolean(a && b && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom),
    syntheticBanner: Boolean(document.querySelector(".synthetic-fixture-banner")),
    seedFields: document.querySelectorAll('input[id^="seed-"]').length,
    seedDisabled: button("提交初始位姿")?.disabled, stopDisabled: button("停止会话")?.disabled,
    alert: document.querySelector('[role="alert"]')?.textContent ?? null };
}
async function capture(name, pose, cloud = true) {
  const value = evaluate("window.__localizationMetric()");
  assert.equal(value.horizontalOverflow, false);
  assert.equal(value.panelOverlap, false);
  assert.equal(value.syntheticBanner, true);
  assert.equal(value.seedFields, 6);
  assert.equal(Boolean(value.pose), pose);
  if (cloud) assert(value.nonBackground > 100);
  if (pose) assert(value.markerPixels > 10); else assert.equal(value.markerPixels, 0);
  report.stages[name] = value;
  const filename = path.join(output, name + ".png");
  run("screenshot", filename);
  const bytes = await readFile(filename);
  report.screenshots[name] = { bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
}

try {
  run("open", url);
  control("status");
  run("set", "viewport", "1440", "900");
  run("snapshot", "-i");
  run("fill", "#robot-address", parsed.origin);
  run("fill", "#pairing-code", "synthetic-localization-ui");
  click("连接机器人");
  wait("Boolean(document.querySelector('#seed-x'))");
  evaluate(`window.__localizationMetric = ${metric.toString()}`);
  wait("window.__localizationMetric().nonBackground > 100 && !window.__localizationMetric().seedDisabled");
  await capture("unseeded", false);
  for (const [key, value] of Object.entries({ x: "1.25", y: "-0.5", z: "0.1", roll: "5", pitch: "-10", yaw: "90" })) {
    run("fill", `#seed-${key}`, value);
  }
  click("提交初始位姿");
  wait("window.__localizationMetric().status?.includes('初始位姿已提交')");
  await capture("pending", false);
  const submitted = control("status");
  assert.deepEqual(submitted.commands, ["seed"]);
  assert.deepEqual(submitted.seed.map(row => row[3]), [1.25, -0.5, 0.1, 1]);
  assert(Math.abs(submitted.seed[1][0] - Math.cos(Math.PI / 18)) < 1e-6);
  control("track");
  wait("Boolean(window.__localizationMetric().pose)");
  await capture("tracking", true);
  control("map-mismatch");
  wait("!window.__localizationMetric().canvas && !window.__localizationMetric().pose");
  await capture("map-mismatch", false, false);
  control("map-restore");
  wait("Boolean(window.__localizationMetric().pose)");
  evaluate(`(() => {
    const original = window.fetch;
    window.__restoreFetch = () => { window.fetch = original; };
    window.__lateQueryCaptured = false;
    window.__lateQueryReturned = false;
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (new URL(String(args[0]), location.href).pathname !== '/api/v1/slam/localization' ||
          window.__lateQueryCaptured) return response;
      const cached = new Response(await response.text(), {status:response.status,headers:response.headers});
      window.__lateQueryCaptured = true;
      const held = new Promise(resolve => { window.__releaseQuery = resolve; });
      [...document.querySelectorAll('button')].find(button => button.textContent === '暂停定位').click();
      await held;
      window.__lateQueryReturned = true;
      return cached;
    };
  })()`);
  wait("window.__localizationMetric().status?.includes('已暂停')");
  await capture("paused", false);
  const late = evaluate(`(async () => {
    window.__restoreFetch(); window.__releaseQuery();
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
    return {captured:window.__lateQueryCaptured,returned:window.__lateQueryReturned,
      status:window.__localizationMetric().status,pose:window.__localizationMetric().pose};
  })()`);
  assert.equal(late.captured, true);
  assert.equal(late.returned, true);
  assert(late.status.includes("已暂停"));
  assert.equal(late.pose, null);
  report.lateQueryAfterPauseRejected = true;
  await capture("late-query", false);
  control("ack");
  wait("!window.__localizationMetric().seedDisabled");
  click("提交初始位姿");
  control("track");
  wait("Boolean(window.__localizationMetric().pose)");
  run("set", "viewport", "844", "390");
  await capture("landscape", true);
  control("outage");
  wait("window.__localizationMetric().status?.includes('定位服务不可用')");
  await capture("outage", false, false);
  control("recover");
  wait("Boolean(window.__localizationMetric().pose)");
  await capture("recovered", true);
  control("conflict-next");
  const before = control("status").commands.length;
  click("提交初始位姿");
  wait("Boolean(window.__localizationMetric().alert)");
  const conflict = control("status");
  assert.equal(conflict.commands.length, before + 1);
  assert.equal(conflict.rejected, 1);
  await capture("conflict", Boolean(evaluate("window.__localizationMetric().pose")));
  click("停止会话");
  wait("window.__localizationMetric().status?.includes('会话已停止')");
  control("ack");
  await capture("stopped", false);
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
  assert.equal(evaluate("window.__localizationMetric().stopDisabled"), true);
  click("地图管理");
  run("wait", "--text", "合成测试地图 / Synthetic");
  report.readonlyMaps = evaluate(`![...document.querySelectorAll('button')].some(item =>
    ['新建点位','删除地图','切换地图','开始导航'].some(text => item.textContent.includes(text)))`);
  assert.equal(report.readonlyMaps, true);
  click("定位验证");
  wait("window.__localizationMetric().status?.includes('会话已停止')");
  assert.equal(control("status").commands.length, before + 2);
  report.storageKeys = evaluate("Object.keys(localStorage)");
  assert.deepEqual(report.storageKeys, ["lingxi.robotAddress"]);
  report.browserErrors = run("errors");
  assert.deepEqual(report.browserErrors.errors, []);
  report.result = "synthetic_localization_browser_checks_passed";
} catch (error) {
  report.failure = error.message;
  try { run("screenshot", path.join(output, "failure.png")); } catch { /* Preserve the first failure. */ }
  throw error;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
  try { run("close"); } catch { /* The service also expires independently. */ }
}
console.log(JSON.stringify({ result: report.result, stages: Object.keys(report.stages).length }));
