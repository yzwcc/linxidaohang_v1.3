const api = require("../../utils/api");

function navigationLabel(status) {
  const value = status && status.navigation && status.navigation.status;
  if (!value) return "未知";
  const labels = {
    idle: "空闲",
    running: "导航中",
    stopping: "停止中",
    completed: "已完成",
    failed: "失败",
    timeout: "超时"
  };
  return labels[value] || value;
}

function autoReturnLabel(status) {
  const value = status && status.auto_return && status.auto_return.state;
  if (!value) return "未知";
  const labels = {
    idle: "空闲",
    navigating_user_target: "前往目标点",
    arrival_action: "执行到达动作",
    waiting_auto_return: "等待自动返航",
    interaction_active: "用户交互中",
    returning_home: "返航中",
    navigating_task_step: "讲解任务中",
    task_arrival_action: "任务到点动作",
    task_paused: "讲解已暂停"
  };
  return labels[value] || value;
}

Page({
  data: {
    bridgeOnline: false,
    activeMapName: "",
    navigationText: "未知",
    autoReturnText: "未知",
    relocalizationText: "未知",
    points: [],
    error: "",
    busyAction: ""
  },
  async onLoad() {
    await this.ensureLogin();
    this.refresh();
  },
  async onShow() {
    if (api) this.refresh();
  },
  async ensureLogin() {
    try {
      await api.login();
    } catch (error) {
      this.setData({ error: error.message });
    }
  },
  async refresh() {
    this.setData({ error: "" });
    try {
      const payload = await api.status();
      let relocText = this.data.relocalizationText;
      try {
        const reloc = await api.relocalizationStatus();
        const result = reloc.result || {};
        relocText = result.status ? `${result.status} ${result.message || ""}` : (reloc.running ? "运行中" : "未运行");
      } catch (error) {
        relocText = "无法读取";
      }
      const navStatus = payload.nav_status || {};
      const pointPayload = payload.points || {};
      this.setData({
        bridgeOnline: Boolean(payload.bridge_online),
        activeMapName: navStatus.active_map_name || "",
        navigationText: navigationLabel(navStatus),
        autoReturnText: autoReturnLabel(navStatus),
        relocalizationText: relocText,
        points: pointPayload.point_names || []
      });
    } catch (error) {
      this.setData({ bridgeOnline: false, error: error.message });
    }
  },
  async run(action, fn) {
    this.setData({ busyAction: action, error: "" });
    try {
      const result = await fn();
      const message = result && result.message ? result.message : "操作已提交";
      wx.showToast({ title: message, icon: "none" });
      await this.refresh();
    } catch (error) {
      this.setData({ error: error.message });
      wx.showToast({ title: error.message, icon: "none" });
    } finally {
      this.setData({ busyAction: "" });
    }
  },
  goPoint(event) {
    const name = event.currentTarget.dataset.name;
    this.run(name, () => api.navStart(name));
  },
  stopNav() {
    this.run("stopNav", () => api.navStop());
  },
  startTask() {
    this.run("taskStart", () => api.taskStart());
  },
  pauseTask() {
    this.run("taskPause", () => api.taskPause());
  },
  resumeTask() {
    this.run("taskResume", () => api.taskResume());
  },
  stopTask() {
    this.run("taskStop", () => api.taskStop());
  },
  startBridge() {
    this.run("start", () => api.bridgeAction("start"));
  },
  restartBridge() {
    this.run("restart", () => api.bridgeAction("restart"));
  },
  autoRelocalize() {
    this.run("autoReloc", () => api.autoRelocalize());
  }
});
