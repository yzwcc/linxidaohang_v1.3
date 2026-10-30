import { describe, expect, it } from "vitest";
import { mappingFeedback } from "../web/src/mapping-view";

type ViewState = Parameters<typeof mappingFeedback>[0];
const running: ViewState = {
  mappingBackend: "glim", mappingStatus: "running", mappingInputMode: "live", checks: [],
};
const progress = (completedMessages: number, processedScans: number, estimatedPoses: number) => ({
  completedMessages, processedScans, estimatedPoses,
});

describe("mapping startup and failure feedback", () => {
  it("leaves vendor and idle sessions unchanged", () => {
    expect(mappingFeedback({ ...running, mappingBackend: undefined })).toBeUndefined();
    expect(mappingFeedback({ ...running, mappingStatus: "idle" })).toBeUndefined();
  });

  it.each([
    ["requested", "建图进程正在启动"], ["saving", "正在结束采集并处理地图"],
  ] as const)("reports %s without claiming sensor progress", (mappingStatus, text) => {
    expect(mappingFeedback({ ...running, mappingStatus })).toEqual({ tone: "", text });
  });

  it("does not interpret missing counters as zero input", () => {
    expect(mappingFeedback(running)).toEqual({ tone: "warn", text: "等待采集进度确认" });
  });

  it.each([
    ["live", "等待传感器输入"], ["recorded", "等待录制数据输入"],
  ] as const)("distinguishes %s input before scanning", (mappingInputMode, text) => {
    expect(mappingFeedback({ ...running, mappingInputMode, mappingProgress: progress(0, 0, 0) }))
      .toEqual({ tone: "warn", text });
  });

  it("distinguishes messages, valid scans and initialized poses", () => {
    expect(mappingFeedback({ ...running, mappingProgress: progress(20, 0, 0) }))
      .toEqual({ tone: "warn", text: "已接收输入消息，等待有效扫描" });
    expect(mappingFeedback({ ...running, mappingProgress: progress(20, 1, 0) }))
      .toEqual({ tone: "", text: "已处理扫描，正在初始化位姿" });
    expect(mappingFeedback({ ...running, mappingProgress: progress(20, 1, 1) }))
      .toEqual({ tone: "warn", text: "位姿已生成，当前实时点云尚未确认或已过期" });
  });

  it("requires current lidar readiness for the live-point message, not for recorded processing", () => {
    const state = { ...running, mappingProgress: progress(100, 5, 2),
      checks: [{ id: "lidar", label: "LiDAR", ready: true }] };
    expect(mappingFeedback(state)).toEqual({ tone: "", text: "实时点云持续接收中" });
    expect(mappingFeedback({ ...state, checks: [], mappingInputMode: "recorded" }))
      .toEqual({ tone: "", text: "录制扫描处理中" });
  });

  it.each([false, undefined, true])("only confirms cleanup when explicit: %s", (mappingCleanupComplete) => {
    const result = mappingFeedback({ ...running, mappingStatus: "failed",
      mappingFailure: "process_startup_deadline_exceeded", mappingCleanupComplete });
    expect(result).toEqual({ tone: "error", text: "建图进程启动超时；" +
      (mappingCleanupComplete === true ? "进程已清理" : "进程清理尚未确认") });
  });

  it("does not infer termination from a service disconnect, even with stale cleanup data", () => {
    expect(mappingFeedback({ ...running, mappingStatus: "failed",
      mappingFailure: "mapping_service_unavailable", mappingCleanupComplete: true }))
      .toEqual({ tone: "error", text: "无法确认建图服务状态；采集是否结束尚未确认" });
    expect(mappingFeedback({ ...running, mappingStatus: "failed" }))
      .toEqual({ tone: "error", text: "建图会话未完成；进程清理尚未确认" });
  });
});
