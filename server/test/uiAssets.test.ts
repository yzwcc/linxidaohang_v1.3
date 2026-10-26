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

  it("uses the current mapping release cache key for browser assets", () => {
    expect(html).toContain("/console/app.js?v=20261026-r18-mapfit16");
  });

  it("keeps robot motion out of the mapping workspace and exposes AIMDK mapping controls", () => {
    expect(html).toContain("不发送底盘速度或导航运动命令");
    expect(html).toContain('id="startMappingBtn" class="primary-large" type="button" disabled');
    expect(html).toContain('id="saveMappingBtn" class="warning" type="button" disabled');
    expect(appScript).toContain('if (nextView === "mapping") void refreshMappingHealth()');
    expect(appScript).toContain('$("startMappingBtn").disabled = mappingActive || mappingSaving || !canStartMapping');
    expect(appScript).toContain('$("saveMappingBtn").disabled = !mappingActive && !mappingSaving');
    expect(appScript).toContain('$("saveMappingBtn").textContent = mappingSaving ? "重试地图保存确认"');
    expect(appScript).toContain('/api/robots/default/mapping/start');
    expect(appScript).toContain('/api/robots/default/mapping/save');
    expect(html).toContain('id="mappingPreviewCanvas"');
    expect(appScript).toContain('/api/robots/default/mapping/preview');
    expect(appScript).toContain('setInterval(() => void refreshMappingPreview(), 2500)');
    expect(appScript).toContain('const ready = mappingActive || mappingSaving || canStartMapping;');
    expect(appScript).toContain('state.mappingStatus?.last_result?.status === "saved"');
    expect(appScript).toContain('等待 AIMDK 确认保存');
    expect(appScript).toContain('canStartMapping ? "可开始" : "尚未就绪"');
    expect(appScript).toContain('health.mapping_backend_available ? "接口就绪" : "未就绪"');
    expect(appScript).toContain("timeoutMs: 65000");
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
    expect(appScript).toContain("运行桥接尚未确认应用");
    expect(appScript).toContain("已应用并回读一致");
    expect(appScript).toContain("R16 将拒绝下发 task_type=2、pnc_mode=0 全局导航");
    expect(appScript).not.toContain("R16 不提供桥接应用回执");
  });
});
