import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// This server never imports the real backend or forwards a request to a robot.
const publicDir = fileURLToPath(new URL("../public/", import.meta.url));
const outputDir = fileURLToPath(new URL("./artifacts/visual-20260918/", import.meta.url));
const baseline = process.argv.includes("--baseline");
const mapName = "1789696800000";
const points = ["接待区", "业务办理区"].map((name, index) => ({
  id: `p${index + 1}`, name, aliases: [], is_start: index === 0,
  pose: { position: { x: index, y: index, z: 0 }, orientation: { z: 0, w: 1 } },
  navigation: { target_pose_radius: 0.15, max_forward_speed: 0.3 },
  business: { on_arrival: [] }
}));
let task = {
  id: "business", name: "业务办理", enabled: true,
  command_topic: "办理业务", start_phrases: ["我要办理业务"],
  steps: [{ point_id: "p1", enabled: true, speech_text: "欢迎来到业务办理区。", wait_sec: 30 }]
};
let interruption = { active: false };
let wait = { active: false };
let autoReturnState = "idle";
let ready = true;
let startFailure = false;
let startupRequests = 0;
const writes = [];
const unexpected = [];
function startup() {
  return {
    ready, navigation_ready: ready, tunnel: { ok: ready },
    java_agent: { running: ready, lingxiConnected: ready },
    checklist: ["本机连接", "语音服务", "导航服务", "现场定位"].map(label => ({ label, ok: ready, detail: ready ? "已就绪" : "待检查" })),
    robot: {
      bridge_online: ready, map_web_online: ready,
      points: { point_names: points.map(p => p.name) }, maps: { maps: [mapName] },
      nav_status: {
        active_map_name: mapName, nav_mode: "navigation", navigation: { status: "idle" },
        localization: { ok: ready }, auto_return: { state: autoReturnState },
        task: { interruption, wait }, sdk_services: { motion: true, emoji: true, action_resource: true }
      }
    }, relocalization: { running: false, result: { ok: true } }
  };
}
const staticTypes = { ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png" };
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  const send = (body, type = "application/json; charset=utf-8", status = 200) => {
    response.writeHead(status, { "content-type": type, "cache-control": "no-store" });
    response.end(type.startsWith("application/json") ? JSON.stringify(body) : body);
  };
  try {
    if (url.pathname.startsWith("/console/") && request.method === "GET") {
      const name = url.pathname.slice("/console/".length) || "index.html";
      if (name !== path.basename(name) || !staticTypes[path.extname(name)]) return send({ ok: false }, undefined, 404);
      return send(await readFile(path.join(publicDir, name)), staticTypes[path.extname(name)]);
    }
    if (url.pathname === "/favicon.ico") return send("", "image/x-icon", 204);
    const apiPath = url.pathname.replace("/api/robots/default", "");
    if (request.method === "POST") {
      let raw = "";
      for await (const chunk of request) raw += chunk;
      const body = raw ? JSON.parse(raw) : {};
      writes.push({ path: apiPath, body });
      if (apiPath === `/maps/${mapName}/task`) { task = body; return send({ ok: true, ...task }); }
      if (apiPath === "/start-all") {
        await new Promise(resolve => setTimeout(resolve, 150));
        ready = !startFailure;
        return send({ ok: ready, startup: startup(), steps: [{ ok: ready, label: "导航服务", detail: ready ? "已就绪" : "模拟启动失败：请检查导航服务状态。" }] });
      }
      unexpected.push(`${request.method} ${url.pathname}`);
      return send({ ok: false, message: "Blocked by isolated visual test" }, undefined, 403);
    }
    if (request.method !== "GET") return send({ ok: false }, undefined, 405);
    if (apiPath === "/config") return send({ deployment_mode: "robot", access_urls: ["http://127.0.0.1:3000/console/"] });
    if (apiPath === "/startup") { startupRequests++; return send(startup()); }
    if (apiPath === "/maps") return send({ maps: [mapName] });
    if (apiPath === "/linkcraft/resources") return send({ ok: true, resources: [] });
    if (apiPath === `/maps/${mapName}`) return send({ map_name: mapName, map: { origin_u: 30, origin_v: 30, resolution_pixels_per_meter: 10 }, points, tour_task: task, tour_tasks: [task], active_tour_task_id: task.id });
    if (apiPath === `/maps/${mapName}/image`) return send(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=", "base64"), "image/png");
    unexpected.push(`${request.method} ${url.pathname}`);
    send({ ok: false, message: "No mock fixture" }, undefined, 404);
  } catch (error) { send({ ok: false, message: error.message }, undefined, 500); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
if (process.argv.includes("--serve")) {
  console.log(`Isolated mock preview: ${origin}/console/`);
} else {
  let browser;
  try {
    const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
    browser = await chromium.launch({ headless: true, ...(process.platform === "win32" ? { channel: "msedge" } : {}) });
    await mkdir(outputDir, { recursive: true });
    const report = { origin, baseline, widths: [], errors: [], externalRequests: [], writes, unexpected };
    for (const width of [320, 390, 1440]) {
      ready = true;
      const context = await browser.newContext({ viewport: { width, height: width < 640 ? 844 : 1000 }, deviceScaleFactor: 1, reducedMotion: "reduce", serviceWorkers: "block" });
      await context.route("**/*", route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        report.externalRequests.push(route.request().url());
        return route.abort();
      });
      const page = await context.newPage();
      page.on("pageerror", error => report.errors.push(error.message));
      await page.goto(`${origin}/console/`, { waitUntil: "networkidle" });
      await page.locator("#taskSteps .task-step-item").waitFor({ state: "attached" });
      const imageMetrics = await page.evaluate(async () => {
        const image = new Image();
        image.src = "/console/robot-hero.png";
        await image.decode();
        const hero = document.querySelector(".customer-hero").getBoundingClientRect();
        const small = innerWidth <= 640;
        const imageHeight = small ? 240 : 420;
        const imageWidth = imageHeight * image.naturalWidth / image.naturalHeight;
        const left = hero.right - imageWidth;
        const top = hero.top + 1 + (small ? 0 : (330 - imageHeight) / 2);
        const robot = { left: left + imageWidth * 0.53, right: left + imageWidth * 0.75, top: top + imageHeight * 0.18, bottom: top + imageHeight * 0.81 };
        const overlaps = [".hero-copy", ".hero-card"].filter(selector => {
          const box = document.querySelector(selector).getBoundingClientRect();
          return box.left < robot.right && box.right > robot.left && box.top < robot.bottom && box.bottom > robot.top;
        });
        return { loaded: image.naturalWidth > 0, robot, overlaps, fullyVisible: robot.left >= hero.left && robot.right <= hero.right && robot.top >= hero.top && robot.bottom <= hero.bottom };
      });
      await page.screenshot({ path: path.join(outputDir, `${baseline ? "before" : "after"}-${width}.png`), fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      report.widths.push({ width, overflow, imageMetrics });
      if (!baseline) {
        await page.locator(".customer-hero").screenshot({ path: path.join(outputDir, `hero-${width}.png`) });
        assert.equal(overflow, false, `${width}px page overflow`);
        assert.equal(imageMetrics.loaded, true);
        assert.equal(imageMetrics.fullyVisible, true);
        assert.deepEqual(imageMetrics.overlaps, [], `${width}px robot obscured by controls`);
        for (const view of ["task", "map", "system", "nav"]) {
          await page.locator(`[data-view="${view}"]`).click();
          assert.equal(await page.locator(`[data-view="${view}"]`).getAttribute("aria-selected"), "true");
          assert.equal(await page.locator(`[data-view-section="${view}"]`).first().isVisible(), true);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${width}px ${view} overflow`);
        }
        await page.locator('[data-view="task"]').click();
        const waitInput = page.getByLabel("无人交流后继续（秒）", { exact: true });
        assert.equal(await waitInput.getAttribute("min"), "10");
        assert.equal(await waitInput.getAttribute("max"), "300");
        for (const [input, expected] of [["9", "10"], ["301", "300"], ["", "10"], ["30", "30"]]) {
          await waitInput.fill(input);
          await waitInput.blur();
          assert.equal(await waitInput.inputValue(), expected);
        }
        await page.locator("#saveTaskBtn").click();
        await page.waitForFunction(() => document.querySelector("#toast").textContent.includes("任务已保存"));
        assert.equal(writes.at(-1).body.steps[0].wait_sec, 30);
        await page.waitForFunction(() => !document.querySelector("#toast").classList.contains("show"));
        await page.locator('[data-view="task"]').click();
        await page.evaluate(() => new Promise(resolve => {
          let last = scrollY;
          let stableFrames = 0;
          const settle = () => {
            stableFrames = scrollY === last ? stableFrames + 1 : 0;
            last = scrollY;
            if (stableFrames >= 12) resolve();
            else requestAnimationFrame(settle);
          };
          requestAnimationFrame(settle);
        }));
        await page.screenshot({ path: path.join(outputDir, `task-${width}.png`) });
        const refresh = async () => {
          const previous = startupRequests;
          const response = page.waitForResponse(response => response.url().endsWith("/startup"));
          await page.locator("#refreshBtn").click();
          await response;
          assert(startupRequests > previous);
        };
        await page.locator('[data-view="system"]').click();
        for (const [status, expected] of [
          [{ active: true, interaction_active: true, remaining_sec: 5 }, "讲解已被打断，正在交流，等待交流结束"],
          [{ active: true, interaction_active: true, counting_down: true, remaining_sec: 5 }, "讲解已被打断，正在交流，等待交流结束"],
          [{ active: true, interaction_active: false, remaining_sec: 3.2 }, "讲解已被打断，无人交流后恢复（剩余约 4 秒）"],
          [{ active: true, counting_down: true, remaining_sec: 2 }, "讲解已被打断，无人交流后恢复（剩余约 2 秒）"],
          [{ active: true, interaction_active: false, counting_down: false, remaining_sec: 5 }, "讲解已被打断，等待无人交流后恢复"],
          [{ active: true, remaining_sec: 5 }, "讲解已被打断，等待无人交流后恢复"],
          [{ active: true, interaction_active: false, remaining_sec: 0 }, "讲解已被打断，等待恢复确认"],
          [{ active: true, interaction_active: false, remaining_sec: "invalid" }, "讲解已被打断，等待无人交流后恢复"]
        ]) {
          interruption = status;
          autoReturnState = "task_paused";
          await refresh();
          await page.waitForFunction(text => document.querySelector("#returnState").textContent === text, expected);
        }
        interruption = { active: false };
        autoReturnState = "task_waiting";
        wait = { active: true, interaction_active: true, remaining_sec: 30 };
        await refresh();
        await page.waitForFunction(() => document.querySelector("#returnState").textContent === "点位等待中，正在交流");
        wait = { active: true, interaction_active: false, remaining_sec: 12.1 };
        await refresh();
        await page.waitForFunction(() => document.querySelector("#returnState").textContent === "无人交流后继续（剩余约 13 秒）");
        await page.locator(".status-panel").screenshot({ path: path.join(outputDir, `status-${width}.png`) });
        autoReturnState = "idle";
        wait = { active: false };
        ready = false;
        await refresh();
        await page.evaluate(() => scrollTo(0, 0));
        await page.locator(".customer-hero").screenshot({ path: path.join(outputDir, `offline-${width}.png`) });
        startFailure = true;
        await page.locator("#startAllServicesBtn").click();
        await page.waitForFunction(() => document.querySelector("#startAllResult").textContent.includes("模拟启动失败"));
        assert.equal(await page.locator("#startAllServicesBtn").isEnabled(), true);
        await page.locator(".customer-hero").screenshot({ path: path.join(outputDir, `failure-${width}.png`) });
        startFailure = false;
        await page.locator("#startAllServicesBtn").click();
        await page.waitForFunction(() => document.querySelector("#customerReadyTitle").textContent === "机器人已准备好");
        await page.locator(".customer-hero").screenshot({ path: path.join(outputDir, `started-${width}.png`) });
      }
      await context.close();
    }
    await writeFile(path.join(outputDir, baseline ? "before-report.json" : "report.json"), JSON.stringify(report, null, 2));
    assert.deepEqual(report.errors, []);
    assert.deepEqual(report.externalRequests, []);
    assert.deepEqual(unexpected, []);
    console.log(JSON.stringify({ passed: true, outputDir, widths: report.widths, mockWrites: writes.length }));
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
}
