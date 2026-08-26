const api = require("../../utils/api");

function normalizePoint(point, mapName) {
  const pose = point.pose || {};
  const position = pose.position || {};
  const orientation = pose.orientation || {};
  const actions = point.business && Array.isArray(point.business.on_arrival) ? point.business.on_arrival : [];
  const tts = actions.find((item) => item.type === "tts") || {};
  return {
    ...point,
    map_name: point.map_name || mapName,
    aliasesText: Array.isArray(point.aliases) ? point.aliases.join(",") : "",
    x: String(position.x || 0),
    y: String(position.y || 0),
    qz: String(orientation.z || 0),
    qw: String(orientation.w == null ? 1 : orientation.w),
    arrivalText: tts.text || ""
  };
}

function denormalizePoint(point, mapName) {
  const name = (point.name || "").trim();
  const id = point.id || `${mapName}:${name}`;
  const arrivalText = (point.arrivalText || "").trim();
  return {
    id,
    name,
    map_name: mapName,
    map_id: Number(mapName) || mapName,
    is_start: Boolean(point.is_start),
    aliases: (point.aliasesText || "").split(",").map((item) => item.trim()).filter(Boolean),
    pose: {
      position: { x: Number(point.x || 0), y: Number(point.y || 0), z: 0 },
      orientation: { x: 0, y: 0, z: Number(point.qz || 0), w: Number(point.qw || 1) }
    },
    navigation: point.navigation || {
      target_pose_radius: 0.15,
      pnc_mode: 0,
      max_forward_speed: 0.3,
      task_type: 2
    },
    business: {
      on_arrival: arrivalText ? [{ type: "tts", text: arrivalText }] : []
    },
    voice: {
      enabled: true,
      display_name: name
    }
  };
}

Page({
  data: {
    maps: [],
    mapNames: [],
    currentMapName: "",
    mapImageUrl: "",
    points: [],
    pointNames: [],
    tourTask: { name: "默认讲解任务", enabled: true, steps: [] },
    editing: null,
    busy: false,
    error: ""
  },
  async onLoad() {
    try {
      await api.login();
    } catch (error) {
      this.setData({ error: error.message });
    }
    this.loadMaps();
  },
  async loadMaps() {
    this.setData({ error: "" });
    try {
      const payload = await api.maps();
      const maps = payload.maps || payload.map_names || payload.items || [];
      const mapNames = maps.map((item) => String(item.map_name || item.name || item)).filter(Boolean);
      this.setData({ maps, mapNames });
      if (!this.data.currentMapName && mapNames.length) {
        await this.loadMap(mapNames[0]);
      }
    } catch (error) {
      this.setData({ error: error.message });
    }
  },
  selectMap(event) {
    const name = this.data.mapNames[Number(event.detail.value)];
    if (name) this.loadMap(name);
  },
  async loadMap(mapName) {
    this.setData({ error: "", currentMapName: mapName, mapImageUrl: api.imageUrl(mapName), editing: null });
    try {
      const payload = await api.map(mapName);
      const points = (payload.points || []).map((point) => normalizePoint(point, mapName));
      const pointById = {};
      points.forEach((point) => { pointById[point.id] = point; });
      const tourTask = payload.tour_task || { name: "默认讲解任务", enabled: true, steps: [] };
      tourTask.steps = (tourTask.steps || []).map((step) => ({
        ...step,
        name: pointById[step.point_id] ? pointById[step.point_id].name : step.point_id
      }));
      this.setData({
        points,
        pointNames: points.map((point) => point.name),
        tourTask
      });
    } catch (error) {
      this.setData({ error: error.message });
    }
  },
  newPoint() {
    this.setData({
      editing: normalizePoint({
        name: "",
        aliases: [],
        is_start: false,
        pose: { position: { x: 0, y: 0 }, orientation: { z: 0, w: 1 } }
      }, this.data.currentMapName)
    });
  },
  editPoint(event) {
    const point = this.data.points[Number(event.currentTarget.dataset.index)];
    this.setData({ editing: { ...point } });
  },
  updateEditing(event) {
    const field = event.currentTarget.dataset.field;
    this.setData({ editing: { ...this.data.editing, [field]: event.detail.value } });
  },
  toggleStart() {
    this.setData({ editing: { ...this.data.editing, is_start: !this.data.editing.is_start } });
  },
  cancelEdit() {
    this.setData({ editing: null });
  },
  async savePoint() {
    const editing = this.data.editing;
    if (!editing || !editing.name.trim()) {
      this.setData({ error: "请填写点位名称" });
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      await api.savePoint(this.data.currentMapName, denormalizePoint(editing, this.data.currentMapName));
      wx.showToast({ title: "点位已保存", icon: "none" });
      await this.loadMap(this.data.currentMapName);
    } catch (error) {
      this.setData({ error: error.message });
    } finally {
      this.setData({ busy: false });
    }
  },
  async deletePoint(event) {
    const pointId = event.currentTarget.dataset.id;
    this.setData({ busy: true, error: "" });
    try {
      await api.deletePoint(this.data.currentMapName, pointId);
      wx.showToast({ title: "点位已删除", icon: "none" });
      await this.loadMap(this.data.currentMapName);
    } catch (error) {
      this.setData({ error: error.message });
    } finally {
      this.setData({ busy: false });
    }
  },
  updateTask(event) {
    const field = event.currentTarget.dataset.field;
    this.setData({ tourTask: { ...this.data.tourTask, [field]: event.detail.value } });
  },
  addTaskStep(event) {
    const point = this.data.points[Number(event.detail.value)];
    if (!point) return;
    const steps = [...(this.data.tourTask.steps || []), { point_id: point.id, enabled: true, name: point.name }];
    this.setData({ tourTask: { ...this.data.tourTask, steps } });
  },
  removeTaskStep(event) {
    const index = Number(event.currentTarget.dataset.index);
    const steps = [...(this.data.tourTask.steps || [])];
    steps.splice(index, 1);
    this.setData({ tourTask: { ...this.data.tourTask, steps } });
  },
  async saveTask() {
    const task = {
      ...this.data.tourTask,
      steps: (this.data.tourTask.steps || []).map((step) => ({ point_id: step.point_id, enabled: step.enabled !== false }))
    };
    this.setData({ busy: true, error: "" });
    try {
      await api.saveTask(this.data.currentMapName, task);
      wx.showToast({ title: "任务已保存", icon: "none" });
      await this.loadMap(this.data.currentMapName);
    } catch (error) {
      this.setData({ error: error.message });
    } finally {
      this.setData({ busy: false });
    }
  }
});
