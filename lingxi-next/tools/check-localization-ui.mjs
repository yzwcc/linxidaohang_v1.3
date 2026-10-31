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
const fillSeed = () => {
  for (const [key, value] of Object.entries({ x: "1.25", y: "-0.5", z: "0.1", roll: "5", pitch: "-10", yaw: "90" })) {
    run("fill", `#seed-${key}`, value);
  }
};
const control = (command) => {
  const result = evaluate(`(async () => {
    const response = await fetch('/__synthetic/${command}', {signal:AbortSignal.timeout(3000)});
    return {status:response.status,body:await response.json()};
  })()`);
  assert.equal(result.status, 200);
  assert.equal(result.body.classification, "isolated_synthetic_ui_fixture");
  return result.body;
};
const report = { schemaVersion: 1, date: new Date().toISOString().slice(0, 10), classification: "isolated_synthetic_ui_fixture",
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
    seedFields: document.querySelectorAll('.seed-fields input[type="number"]').length,
    seedHeading: document.querySelector(".localization-controls form h2")?.textContent ?? null,
    seedDisabled: button("提交初始位姿")?.disabled, stopDisabled: button("停止会话")?.disabled,
    startDisabled: button("启动定位进程")?.disabled, shutdownDisabled: button("关闭定位进程")?.disabled,
    runtime: document.querySelector('[data-testid="localization-runtime"]')?.textContent ?? null,
    alert: document.querySelector('[role="alert"]')?.textContent ?? null };
}
async function capture(name, pose, cloud = true) {
  const value = evaluate("window.__localizationMetric()");
  assert.equal(value.horizontalOverflow, false);
  assert.equal(value.panelOverlap, false);
  assert.equal(value.syntheticBanner, true);
  assert.equal(value.seedFields, 6);
  assert.equal(value.seedHeading, "初始雷达位姿");
  assert.equal(Boolean(value.pose), pose);
  if (cloud) assert(value.nonBackground > 100);
  if (pose) assert(value.pose.includes("雷达") && !value.pose.includes("IMU"));
  if (pose) assert(value.markerPixels > 10); else assert.equal(value.markerPixels, 0);
  report.stages[name] = value;
  const filename = path.join(output, name + ".png");
  run("screenshot", filename);
  const bytes = await readFile(filename);
  report.screenshots[name] = { bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
}

try {
  run("open", url);
  const serviceMode = control("status").serviceMode;
  report.serviceMode = serviceMode;
  run("set", "viewport", "1440", "900");
  run("snapshot", "-i");
  run("fill", "#robot-address", parsed.origin);
  run("fill", "#pairing-code", "synthetic-localization-ui");
  click("连接机器人");
  wait("Boolean(document.querySelector('#seed-x'))");
  evaluate(`window.__localizationMetric = ${metric.toString()}`);
  if (serviceMode) {
    wait("window.__localizationMetric().runtime?.includes('未启动定位进程')");
    await capture("service-idle", false, false);
    assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
    run("snapshot", "-i");
    run("select", "#localization-map", `slam-${"b".repeat(32)}`);
    wait("!window.__localizationMetric().startDisabled");
    click("启动定位进程");
    wait("window.__localizationMetric().runtime?.includes('启动中')");
    await capture("service-starting", false);
    assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
    assert.deepEqual(control("status").lifecycleCommands, ["start"]);
    control("ready");
  }
  wait("window.__localizationMetric().nonBackground > 100 && !document.querySelector('#seed-x').disabled");
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
  await capture("unseeded", false);
  assert.equal(evaluate("Boolean(document.querySelector('button[aria-label=\"测量 XY 投影距离\"]'))"), false);
  const beforeDisplay = control("status").commands.length;
  click("主体范围");
  run("find", "label", "原始 Z 分层", "check");
  run("find", "label", "原始 Z 下限", "fill", "-.15");
  run("find", "label", "原始 Z 上限", "fill", ".15");
  wait("Number(document.querySelector('[data-testid=lidar-slice-count]')?.dataset.visible)>0");
  assert.equal(control("status").commands.length, beforeDisplay);
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
  report.display = { noCommandsSent: true, measurementAbsentInSeedPicker: true, slicedSeedPickerChecked: true };
  const draftFields = () => evaluate(`Object.fromEntries([...document.querySelectorAll('.seed-fields input[type="number"]')]
    .map(input => [input.id.slice(5), input.value]))`);
  const beforeReference = control("status").commands.length;
  run("select", "#seed-reference", "last");
  const referenceFields = draftFields();
  assert.deepEqual([referenceFields.x, referenceFields.y, referenceFields.z], ["1", "-0.5", "0.4"]);
  assert(Math.abs(Number(referenceFields.roll) - 90) < 1e-8);
  assert.equal(Number(referenceFields.pitch), 0); assert.equal(Number(referenceFields.yaw), 0);
  assert.equal(control("status").commands.length, beforeReference);
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
  run("check", "#seed-reference-confirmed");
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), false);
  await capture("reference-draft", false);
  run("select", "#seed-reference", "first");
  assert(Object.values(draftFields()).every(value => Number(value) === 0));
  assert.equal(evaluate("document.querySelector('#seed-reference-confirmed').checked"), false);
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
  assert.equal(control("status").commands.length, beforeReference);
  report.savedMapReferences = { explicitOnly: true, fullPoseLoaded: true, noCommandsSent: true,
    physicalReferenceConfirmationRequired: true, changingReferenceRevokesConfirmation: true };
  run("set", "viewport", "844", "390");
  evaluate("document.querySelector('#seed-reference').scrollIntoView({block:'center'}); true");
  run("select", "#seed-reference", "last");
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
  run("check", "#seed-reference-confirmed");
  assert.equal(evaluate("window.__localizationMetric().seedDisabled"), false);
  assert.equal(control("status").commands.length, beforeReference);
  await capture("reference-draft-landscape", false);
  run("set", "viewport", "1440", "900");
  evaluate("window.scrollTo(0,0); true");
  const beginDrag = (dx, dy) => {
    const point = evaluate(`(() => { const r=document.querySelector('.localization-cloud canvas').getBoundingClientRect();
      return {x:r.left+r.width*.45,y:r.top+r.height*.55}; })()`);
    run("mouse", "move", String(Math.round(point.x)), String(Math.round(point.y))); run("mouse", "down", "left");
    run("mouse", "move", String(Math.round(point.x + dx)), String(Math.round(point.y + dy)));
  };
  run("snapshot", "-i");
  for (const [key, value] of Object.entries({ z: "0.4", roll: "7", pitch: "-3" })) run("fill", `#seed-${key}`, value);
  click("设置初始位置与朝向");
  const beforePicking = control("status").commands.length;
  beginDrag(0, -50); run("mouse", "up", "left");
  const picked = draftFields();
  assert(Number.isFinite(Number(picked.x)) && Number.isFinite(Number(picked.y)));
  assert(Math.abs(Number(picked.yaw) - 90) < 1e-6);
  assert.deepEqual([picked.z, picked.roll, picked.pitch], ["0.4", "7", "-3"]);
  assert.equal(control("status").commands.length, beforePicking);
  run("click", ".localization-cloud canvas");
  assert.deepEqual(draftFields(), picked);
  beginDrag(40, 0); run("press", "Escape"); run("mouse", "up", "left");
  assert.deepEqual(draftFields(), picked);
  click("平移地图"); beginDrag(25, 12); run("mouse", "up", "left");
  assert.deepEqual(draftFields(), picked);
  click("放大扫描视图");
  click("设置初始位置与朝向"); beginDrag(40, 0); run("mouse", "up", "left");
  assert.equal(Number(draftFields().yaw), 0);
  assert.equal(control("status").commands.length, beforePicking);
  evaluate("window.__oldSeedCanvas = document.querySelector('.localization-cloud canvas'); true");
  beginDrag(0, -40);
  control("session-change");
  wait("!window.__oldSeedCanvas.isConnected && [...document.querySelectorAll('.seed-fields input[type=\"number\"]')].every(input => input.value === '')");
  run("mouse", "up", "left");
  assert(Object.values(draftFields()).every(value => value === ""));
  assert.equal(control("status").commands.length, beforePicking);
  for (const [key, value] of Object.entries({ z: "0.4", roll: "7", pitch: "-3" })) run("fill", `#seed-${key}`, value);
  click("设置初始位置与朝向"); beginDrag(40, 0); run("mouse", "up", "left");
  await capture("seed-draft", false);
  run("set", "viewport", "844", "390");
  await capture("seed-draft-landscape", false);
  run("set", "viewport", "1440", "900");
  report.pointCloudPicking = { explicitOnly: true, headingChecked: true, clickIgnored: true,
    escapeCancelled: true, panPreservedDraft: true, zoomedPickChecked: true, nonPlanarFieldsPreserved: true,
    sameMapSessionChangeCancelsGesture: true };
  fillSeed();
  click("提交初始位姿");
  wait("window.__localizationMetric().status?.includes('初始位姿已提交')");
  await capture("pending", false);
  const submitted = control("status");
  assert.deepEqual(submitted.commands, ["seed"]);
  assert.equal(submitted.seedSourceFrame, "lidar_chest_front");
  assert.deepEqual(submitted.seed.map(row => row[3]), [1.25, -0.5, 0.1, 1]);
  assert(Math.abs(submitted.seed[1][0] - Math.cos(Math.PI / 18)) < 1e-6);
  control("track");
  wait("Boolean(window.__localizationMetric().pose)");
  await capture("tracking", true);
  control("map-mismatch");
  wait(serviceMode ? "!window.__localizationMetric().pose && window.__localizationMetric().seedDisabled" :
    "!window.__localizationMetric().canvas && !window.__localizationMetric().pose");
  await capture("map-mismatch", false, serviceMode);
  assert.equal(evaluate("document.querySelector('#seed-reference').disabled"), true);
  control("map-restore");
  wait("Boolean(window.__localizationMetric().pose)");
  assert.equal(evaluate("document.querySelector('#seed-x').value"), "");
  fillSeed();
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
  wait("!document.querySelector('#seed-x').disabled");
  fillSeed();
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
  fillSeed();
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
  if (serviceMode) {
    const firstRun = control("status").runtime.runId;
    click("关闭定位进程");
    wait("window.__localizationMetric().runtime?.includes('关闭中')");
    await capture("service-stopping", false);
    assert.equal(evaluate("window.__localizationMetric().startDisabled"), true);
    assert.equal(evaluate("window.__localizationMetric().shutdownDisabled"), true);
    control("cleaned");
    wait("!window.__localizationMetric().startDisabled");
    await capture("service-cleaned", false);
    control("conflict-service-next");
    const commandsBefore = control("status").lifecycleCommands.length;
    click("启动定位进程");
    wait("Boolean(window.__localizationMetric().alert)");
    assert.equal(control("status").lifecycleCommands.length, commandsBefore + 1);
    wait("!window.__localizationMetric().startDisabled");
    click("启动定位进程");
    wait("window.__localizationMetric().runtime?.includes('启动中')");
    assert.notEqual(control("status").runtime.runId, firstRun);
    control("ready");
    wait("!document.querySelector('#seed-x').disabled");
    await capture("service-restarted", false);
    assert.equal(evaluate("document.querySelector('#seed-x').value"), "");
    assert.equal(evaluate("window.__localizationMetric().seedDisabled"), true);
    click("关闭定位进程");
    control("cleanup-failed");
    wait("window.__localizationMetric().runtime?.includes('进程异常')");
    assert.equal(evaluate("window.__localizationMetric().startDisabled"), true);
    await capture("service-cleanup-failed", false);
    report.cleanupFailureBlocksReplacement = true;
  }
  click("地图管理");
  run("wait", "--text", "合成测试地图 / Synthetic");
  report.readonlyMaps = evaluate(`![...document.querySelectorAll('button')].some(item =>
    ['新建点位','删除地图','切换地图','开始导航'].some(text => item.textContent.includes(text)))`);
  assert.equal(report.readonlyMaps, true);
  click("定位验证");
  wait(serviceMode ? "window.__localizationMetric().runtime?.includes('进程异常')" :
    "window.__localizationMetric().status?.includes('会话已停止')");
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
