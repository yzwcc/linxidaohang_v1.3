import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const html = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const appScript = readFileSync(new URL("../public/app.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../public/styles.css", import.meta.url), "utf8");

describe("console UI assets", () => {
  it("does not contain duplicate element ids", () => {
    const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
    expect(duplicates).toEqual([]);
  });

  it("provides all five product workspaces and mobile navigation", () => {
    for (const workspace of ["nav", "task", "mapping", "map", "system"]) {
      expect(html).toContain(`data-view="${workspace}"`);
      expect(html).toContain(`data-view-section="${workspace}"`);
    }
    expect(styles).toContain("@media (max-width: 640px)");
    expect(styles).toContain("position: fixed");
  });

  it("keeps robot motion out of the mapping workspace and gates unverified collection", () => {
    expect(html).toContain("本页面不会发送底盘速度或导航运动命令");
    expect(html).toContain('id="startMappingBtn" class="primary-large" type="button" disabled');
    expect(html).toContain('id="saveMappingBtn" class="warning" type="button" disabled');
    expect(appScript).toContain('if (nextView === "mapping") void refreshMappingHealth()');
    expect(appScript).toContain('$("startMappingBtn").disabled = true');
    expect(styles).toContain(".mapping-grid");
  });

  it("uses the modular API client without legacy robot IP editing", () => {
    expect(appScript).toContain('from "./apiClient.js"');
    expect(appScript).not.toContain("saveRobotHost");
    expect(html).not.toContain("saveRobotHostBtn");
  });

  it("places global path safety inputs beside point task_type without making point editing require a radius", () => {
    expect(html.indexOf('id="pointTaskType"')).toBeLessThan(html.indexOf('id="robotRadius"'));
    expect(html.indexOf('id="robotRadius"')).toBeLessThan(html.indexOf('id="pointStart"'));
    expect(html).toContain('<form id="pathSafetyForm"></form>');
    expect(html).toContain('id="robotRadius" form="pathSafetyForm"');
    expect(html).toContain('id="extraClearance" form="pathSafetyForm"');
    expect(html).toContain('id="savePathSafetyBtn" form="pathSafetyForm" type="submit"');
    expect(html).toContain('id="extraClearance"');
    expect(html).not.toMatch(/id="robotRadius"[^>]*\brequired/);
    expect(appScript).toContain("radius.value.trim() === \"\"");
    expect(appScript).not.toContain("radius.required");
    expect(appScript).toContain("if (!state.pathSafetyLoaded)");
    expect(appScript).toContain("state.pathSafetyLoaded = true");
    expect(appScript).toContain("R16 不提供桥接应用回执");
    expect(appScript).not.toContain("已保存并应用：半径");
  });
});
