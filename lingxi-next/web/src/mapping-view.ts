import type { RobotState } from "../../shared/contracts";
import type { MappingFailure } from "../../shared/mapping";

export function mappingCaptureLimit(state: Pick<RobotState,
  "mappingBackend" | "mappingInputMode" | "mappingCaptureLimitSeconds">): number | undefined {
  const limit = state.mappingCaptureLimitSeconds;
  return state.mappingBackend === "glim" && state.mappingInputMode === "live" &&
    typeof limit === "number" && Number.isFinite(limit) && limit >= 5 && limit <= 300 ? limit : undefined;
}

export function inactiveMappingScanNotice(state: Pick<RobotState,
  "mappingBackend" | "mappingStatus">): string | undefined {
  if (!state.mappingBackend) return undefined;
  if (state.mappingStatus === "requested" || state.mappingStatus === "running") return undefined;
  if (state.mappingStatus === "idle") return "等待开始采集";
  if (state.mappingStatus === "saving") return "正在结束采集并处理地图";
  if (state.mappingStatus === "failed") return "建图状态异常，请查看采集控制";
  return "等待建图状态确认";
}

export function confirmedMappingSave(result: unknown, session: string | undefined,
  name: string): { id: string; name: string } | undefined {
  if (!session || !/^[a-f0-9]{32}$/.test(session) || !result || typeof result !== "object") return undefined;
  const response = result as Record<string, unknown>;
  if (response.confirmed !== true || !response.savedSlamMap || typeof response.savedSlamMap !== "object") return undefined;
  const map = response.savedSlamMap as Record<string, unknown>;
  if (map.id !== `slam-${session}` || map.name !== name.trim()) return undefined;
  return { id: map.id as string, name: map.name as string };
}

const failures: Record<MappingFailure, string> = {
  process_start_failed: "建图进程未能启动",
  process_startup_deadline_exceeded: "建图进程启动超时",
  process_progress_deadline_exceeded: "建图输入处理进度超时",
  process_shutdown_deadline_exceeded: "建图进程结束超时",
  child_reported_failure: "建图工作线程报告异常",
  child_exited_without_confirmed_stop: "建图进程意外退出",
  child_health_pipe_closed: "建图进程状态通道中断",
  invalid_child_health_protocol: "建图进程状态数据无效",
  child_descendants_outlived_owner: "建图子进程未按期退出",
  process_cleanup_failed: "建图进程清理未完成",
  mapping_process_failed: "建图会话未完成",
  mapping_service_unavailable: "无法确认建图服务状态",
};

export function mappingFeedback(state: Pick<RobotState,
  "mappingBackend" | "mappingStatus" | "mappingInputMode" | "mappingProgress" |
  "mappingFailure" | "mappingCleanupComplete" | "checks">): {
    tone: "error" | "warn" | ""; text: string;
  } | undefined {
  if (!state.mappingBackend) return undefined;
  if (state.mappingStatus === "failed") {
    const failure = state.mappingFailure ?? "mapping_process_failed";
    const cleanup = failure === "mapping_service_unavailable" ? "采集是否结束尚未确认" :
      state.mappingCleanupComplete === true ? "进程已清理" : "进程清理尚未确认";
    return { tone: "error", text: `${failures[failure]}；${cleanup}` };
  }
  if (state.mappingStatus === "requested") return { tone: "", text: "建图进程正在启动" };
  if (state.mappingStatus === "saving") return { tone: "", text: "正在结束采集并处理地图" };
  if (state.mappingStatus !== "running") return undefined;
  const progress = state.mappingProgress;
  if (!progress) return { tone: "warn", text: "等待采集进度确认" };
  if (progress.completedMessages === 0) return { tone: "warn", text:
    state.mappingInputMode === "live" ? "等待传感器输入" : "等待录制数据输入" };
  if (progress.processedScans === 0) return { tone: "warn", text: "已接收输入消息，等待有效扫描" };
  if (progress.estimatedPoses === 0) return { tone: "", text: "已处理扫描，正在初始化位姿" };
  if (state.mappingInputMode === "live" && !state.checks.some((check) => check.id === "lidar" && check.ready)) {
    return { tone: "warn", text: "位姿已生成，当前实时点云尚未确认或已过期" };
  }
  return { tone: "", text: state.mappingInputMode === "live" ? "实时点云持续接收中" : "录制扫描处理中" };
}
