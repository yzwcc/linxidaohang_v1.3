import type { RobotState, TourTask } from "./api";

export function navigationBusy({ navigation }: Pick<RobotState, "navigation">): boolean {
  return navigation.requiresStop === true || ["requested", "running", "paused"].includes(navigation.status);
}

type TourReadiness = Pick<RobotState, "connected" | "independentProduct" | "mappingBackend" | "referenceNavigation"
  | "checks" | "activeMapId" | "mapping" | "navigation" | "actionBusy">;

export function tourStartBlock(state: TourReadiness, tour: Pick<TourTask, "mapId" | "requiresReview">): string | undefined {
  if (!state.connected) return "连接中断或正在同步";
  if (state.independentProduct) return "独立导航尚未就绪";
  if (state.mappingBackend === "fastlio" && !state.referenceNavigation?.motionEnabled) return "FAST-LIO 运动输出已关闭";
  if (tour.requiresReview) return "迁移任务需要先核对并保存";
  if (tour.mapId !== state.activeMapId) return "请先加载任务地图";
  if (state.mapping || state.actionBusy || navigationBusy(state)) return "请先结束当前操作并确认停稳";
  if (!state.checks.some((check) => check.id === "navigation" && check.ready)) return "导航接口尚未就绪";
}
