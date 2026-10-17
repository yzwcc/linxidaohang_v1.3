import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

const html = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const appScript = readFileSync(new URL("../public/app.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../public/styles.css", import.meta.url), "utf8");
const CACHE_BUST = "20261017-v138-r14";
const ui = runInNewContext(`${appScript
  .replace(/^import .*;\r?\n/, "")
  .replace(/\nmain\(\);\s*$/, "")}\n({ taskProgressText, normalizeTaskStep, taskStepForSave });`, {
  Image: class {}
});

describe("console UI assets", () => {
  it("does not contain duplicate element ids", () => {
    const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
    expect(duplicates).toEqual([]);
  });

  it("provides all four product workspaces and mobile navigation", () => {
    for (const workspace of ["nav", "task", "map", "system"]) {
      expect(html).toContain(`data-view="${workspace}"`);
      expect(html).toContain(`data-view-section="${workspace}"`);
    }
    expect(styles).toContain("@media (max-width: 640px)");
    expect(styles).toContain("position: fixed");
  });

  it("uses the modular API client without legacy robot IP editing", () => {
    expect(appScript).toContain(`from "./apiClient.js?v=${CACHE_BUST}"`);
    expect(appScript).not.toContain("saveRobotHost");
    expect(html).not.toContain("saveRobotHostBtn");
  });

  it("cache-busts the UI and API modules together", () => {
    expect(html).toContain(`styles.css?v=${CACHE_BUST}`);
    expect(html).toContain(`app.js?v=${CACHE_BUST}`);
    expect(appScript).toContain(`apiClient.js?v=${CACHE_BUST}`);
    expect(html).not.toContain("task-behavior-note");
  });

  it("uses Chinese labels for every robot status error channel", () => {
    for (const label of ["导航状态", "点位列表", "地图服务", "语音导航网关"]) {
      expect(appScript).toContain(label);
    }
    expect(appScript).toContain("statusKeyName(key)");
    expect(appScript).toContain("toChineseMessage(value)");
  });

  it.each([
    [{ active: true, interaction_active: true, remaining_sec: 5 }, "讲解已被打断，正在交流，等待交流结束"],
    [{ active: true, interaction_active: true, counting_down: true, remaining_sec: 5 }, "讲解已被打断，正在交流，等待交流结束"],
    [{ active: true, interaction_active: false, remaining_sec: 3.2 }, "讲解已被打断，无人交流后恢复（剩余约 4 秒）"],
    [{ active: true, counting_down: true, remaining_sec: 2 }, "讲解已被打断，无人交流后恢复（剩余约 2 秒）"],
    [{ active: true, interaction_active: false, counting_down: false, remaining_sec: 5 }, "讲解已被打断，等待无人交流后恢复"],
    [{ active: true, remaining_sec: 5 }, "讲解已被打断，等待无人交流后恢复"],
    [{ active: true, interaction_active: false, remaining_sec: 0 }, "讲解已被打断，等待恢复确认"],
    [{ active: true, interaction_active: false, remaining_sec: null }, "讲解已被打断，等待无人交流后恢复"],
    [{ active: true, counting_down: true, remaining_sec: -1 }, "讲解已被打断，等待无人交流后恢复"],
    [{ active: true, counting_down: true, remaining_sec: "invalid" }, "讲解已被打断，等待无人交流后恢复"],
    [{ active: false, remaining_sec: 5 }, "讲解已暂停"]
  ])("only reports an interruption countdown with confirmed silence: %j", (interruption, expected) => {
    expect(ui.taskProgressText({ task: { interruption } }, "讲解已暂停")).toBe(expected);
  });

  it("does not count down point waits during interaction or a manual pause", () => {
    const nav = { auto_return: { state: "task_waiting" }, task: { wait: { interaction_active: true, remaining_sec: 30 } } };
    expect(ui.taskProgressText(nav, "等待")).toBe("点位等待中，正在交流");
    nav.task.wait.interaction_active = false;
    expect(ui.taskProgressText(nav, "等待")).toBe("无人交流后继续（剩余约 30 秒）");
    nav.auto_return.state = "task_paused";
    expect(ui.taskProgressText(nav, "讲解已暂停")).toBe("讲解已暂停");
    expect(ui.taskProgressText({ auto_return: { state: "task_waiting" }, task: { wait: { remaining_sec: 30 } } }, "等待"))
      .toBe("点位等待中，无人交流后继续");
  });

  it.each([[undefined, 0], [-1, 0], [0, 0], [5, 5], [10, 10], [30, 30], [300, 300], [301, 300], ["invalid", 0]])(
    "preserves the 0-300 second wait range for %j", (wait_sec, expected) => {
      const normalized = ui.normalizeTaskStep({ point_id: "p1", wait_sec });
      expect(normalized.wait_sec).toBe(expected);
      expect(ui.taskStepForSave(normalized).wait_sec).toBe(expected);
    }
  );
});
