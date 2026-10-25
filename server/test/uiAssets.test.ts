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

  it("provides all four product workspaces and mobile navigation", () => {
    for (const workspace of ["nav", "task", "map", "mapping", "system"]) {
      expect(html).toContain(`data-view="${workspace}"`);
      expect(html).toContain(`data-view-section="${workspace}"`);
    }
    expect(styles).toContain("@media (max-width: 640px)");
    expect(styles).toContain("position: fixed");
    expect(styles).toContain(".mapping-panel { grid-column: 1 / -1; }");
    expect(styles).toContain(".mapping-panel .split-actions { grid-template-columns: 1fr; }");
    expect(appScript).toContain("if (session.active) startMappingPreviewPolling();");
    expect(appScript).toContain("!state.mapping?.session?.active");
  });

  it("uses the modular API client without legacy robot IP editing", () => {
    expect(appScript).toContain('from "./apiClient.js"');
    expect(appScript).not.toContain("saveRobotHost");
    expect(html).not.toContain("saveRobotHostBtn");
  });

  it("revalidates versioned console assets so mobile browsers cannot keep stale mapping controls", () => {
    expect(html).toContain("/console/app.js?v=20261025-r18-live-mapfit10");
    expect(html).toContain("/console/styles.css?v=20261025-r18-live-mapfit10");
    expect(appScript).toContain('button.textContent = "检查中…"');
    expect(appScript).toContain('setText("mappingBadge", "检查失败")');
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
    expect(appScript).toContain("运行桥接尚未确认应用");
    expect(appScript).toContain("已应用并回读一致");
    expect(appScript).toContain("R17 将拒绝下发 task_type=2、pnc_mode=0 全局导航");
    expect(appScript).not.toContain("R17 不提供桥接应用回执");
  });

  it("uses the AIMDK 1.1.0 minimum target pose radius in the point editor", () => {
    expect(html).toContain('id="pointRadius" type="number" min="0.25"');
    expect(html).toContain("AIMDK 1.1.0 最小 0.25 米");
    expect(appScript).toContain("Math.max(0.25, Number(point.navigation?.target_pose_radius ?? 0.25))");
    expect(appScript).toContain("target_pose_radius: Math.max(0.25");
  });
});
