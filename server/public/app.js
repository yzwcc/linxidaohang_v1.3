import { api, toChineseMessage } from "./apiClient.js";

let state = {
  config: null,
  status: null,
  startup: null,
  systemHealth: null,
  mappingHealth: null,
  localization: null,
  relocalization: null,
  lastRelocalizationPoint: "",
  maps: [],
  currentMap: "",
  currentMapInfo: null,
  mapPayload: null,
  points: [],
  selectedPoint: null,
  targetPixel: null,
  headingPixel: null,
  awaitingHeading: false,
  currentYaw: 0,
  linkcraftResources: [],
  pathSafety: { robot_radius_m: null, extra_clearance_m: 0.1 },
  pathSafetyLoaded: false,
  tasks: [],
  task: { name: "默认讲解任务", enabled: true, steps: [] }
};

const $ = (id) => document.getElementById(id);
const mapImage = new Image();
let mapListRefreshInFlight = false;
let dashboardRefreshInFlight = false;
let startAllInFlight = false;
let dashboardRefreshTimer = null;

const INHERIT_SETTING = "__inherit__";
const SPEECH_MOTION_DOMAINS = Array.from({ length: 28 }, (_, index) => String(index + 21));
const MOTION_OPTIONS = [
  ["random_speech", "讲话随机动作", SPEECH_MOTION_DOMAINS],
  ["empty_motion", "空动作（打断当前动作）", ["0"]],
  ["raise_hand", "举手", ["right_hand", "left_hand"]],
  ["wave", "挥手", ["right_hand", "left_hand"]],
  ["handshake", "握手", ["right_hand", "left_hand"]],
  ["flying_kiss", "飞吻", ["right_hand", "left_hand"]],
  ["salute", "敬礼", ["right_hand", "left_hand"]],
  ["sweetheart", "比心", ["both_hands", "right_hand", "left_hand"]],
  ["horizontal_arm", "平举", ["both_hands", "right_hand", "left_hand"]],
  ["high_five", "击掌", ["right_hand", "left_hand"]],
  ["chestwave", "胸前挥手", ["right_hand", "left_hand"]],
  ["clap", "鼓掌", ["whole_body"]], ["hug", "拥抱", ["whole_body"]],
  ["cheer", "加油", ["whole_body"]], ["bye", "拜拜", ["whole_body"]],
  ["lightwave", "动感光波", ["whole_body"]], ["handx", "双手打叉", ["whole_body"]],
  ["bow", "鞠躬", ["whole_body"]], ["scratch_head", "挠头", ["whole_body"]],
  ["scratch_back", "背后动作", ["whole_body"]]
];
const MOTION_AREA_OPTIONS = [
  ["right_hand", "右手"], ["left_hand", "左手"],
  ["both_hands", "双手"], ["whole_body", "全身动作"],
  ["0", "域 0（空动作）"],
  ...SPEECH_MOTION_DOMAINS.map((value) => [value, `域 ${value}`])
];
const EMOJI_OPTIONS = [
  ["blink", "自然眨眼"], ["calm", "平静"], ["calm2", "平静二"],
  ["game", "游戏"], ["cute", "可爱"], ["cute2", "可爱二"],
  ["cute3", "可爱三"], ["cute4", "可爱四"], ["eye_close", "闭眼"],
  ["eye_open", "睁眼"], ["boring", "无聊"], ["abnormal", "异常"],
  ["sleepy", "困倦"], ["happy", "开心"], ["extreme_happy", "非常开心"],
  ["extreme_happy2", "非常开心二"], ["sad", "难过"], ["sympathy", "同情"],
  ["confuse", "疑惑"], ["shock", "震惊"], ["act_cute", "卖萌"],
  ["serious", "认真"], ["thinking", "思考"], ["angry", "生气"],
  ["extreme_angry", "非常生气"], ["adore", "喜爱"],
  ["extreme_adore", "非常喜爱"], ["charge", "充电"]
];
const EMOJI_MODE_OPTIONS = [["once", "播放一次"], ["loop", "循环播放"]];

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function setClass(id, className) {
  const el = $(id);
  if (el) el.className = className;
}

function renderStartAllResult(result, running = false) {
  const el = $("startAllResult");
  if (!el) return;
  el.classList.add("show");
  if (running) {
    el.innerHTML = `<div><strong>正在启动</strong><span>正在连接机器人并启动语音、导航和地图服务，请不要关闭页面。</span></div>`;
    return;
  }
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  if (!steps.length) {
    el.innerHTML = "";
    el.classList.remove("show");
    return;
  }
  el.innerHTML = steps.map((step) => `
    <div class="${step.ok ? "ok" : "bad"}">
      <strong>${step.ok ? "已完成" : "需处理"}：${step.label || "服务步骤"}</strong>
      <span>${step.detail || ""}</span>
    </div>
  `).join("");
}

function switchWorkspace(view, { scroll = false } = {}) {
  const allowed = new Set(["nav", "task", "mapping", "map", "system"]);
  const nextView = allowed.has(view) ? view : "nav";
  document.querySelectorAll("[data-view-section]").forEach((section) => {
    section.hidden = section.dataset.viewSection !== nextView;
  });
  document.querySelectorAll(".workspace-tab").forEach((tab) => {
    const active = tab.dataset.view === nextView;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  if (nextView === "mapping") void refreshMappingHealth().catch((error) => {
    setText("mappingDiagnosis", error.message || "暂时无法读取机器人传感器状态");
  });
  try { sessionStorage.setItem("lingxi-workspace", nextView); } catch { /* no-op */ }
  if (scroll) document.querySelector(".layout")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderMappingHealth() {
  const health = state.mappingHealth;
  if (!health) return;
  const ready = Boolean(health.ok);
  setText("mappingBadge", ready ? "传感器就绪" : "尚未就绪");
  setClass("mappingBadge", `badge ${ready ? "ok" : "bad"}`);
  setText("mappingSoftware", health.agibot_software || "未知");
  setText("mappingLidar", `${health.lidar_data_available ? "有实时数据" : "无实时数据"} · ${health.lidar_publishers || 0} 个发布者`);
  setText("mappingImu", `${health.imu_data_available ? "有实时数据" : "无实时数据"} · ${health.imu_publishers || 0} 个发布者`);
  setText("mappingSlam", health.slam_running ? "运行中" : "未运行");
  setText("mappingOdom", `${health.odom_data_available ? "有实时数据" : "无实时数据"} · ${health.odom_publishers || 0} 个发布者`);
  setText("mappingBackend", health.mapping_backend_available ? "已验证" : "未验证");
  setText("mappingTopics", (health.mapping_related_topics || []).join("\n") || "未发现相关话题");
  setText("mappingServices", (health.mapping_related_services || []).join("\n") || "未发现相关服务");
  setText("mappingDiagnosis", `${health.diagnosis || ""}\n采集与保存仅在机器人后端接口完成实机验证后开放。`);
  const list = $("mappingPrerequisites");
  if (list) {
    list.replaceChildren();
    const items = health.mapping_prerequisites || [];
    for (const item of items) {
      const row = document.createElement("li");
      row.className = item.available ? "available" : "missing";
      row.textContent = `${item.name}：${item.available ? "可用" : "未安装"}`;
      list.append(row);
    }
    if (!items.length) list.append(Object.assign(document.createElement("li"), { textContent: "未获取依赖状态" }));
  }
  $("startMappingBtn").disabled = true;
  $("saveMappingBtn").disabled = true;
}

async function refreshMappingHealth() {
  state.mappingHealth = await api("/api/robots/default/mapping/health", { force: true });
  renderMappingHealth();
  return state.mappingHealth;
}

function label(value, dict) {
  return dict[value] || value || "-";
}

function statusKeyName(key) {
  return ({
    nav_status: "导航状态",
    points: "点位列表",
    maps: "地图服务"
  })[key] || key;
}

function renderActiveMapNotice() {
  const activeMap = state.status?.nav_status?.active_map_name || "";
  const viewingMap = state.currentMap || "";
  const same = activeMap && viewingMap && activeMap === viewingMap;
  $("activeMapNotice").textContent = `当前导航地图：${activeMap || "未读取到"}；正在查看/编辑：${viewingMap || "-"}${same ? "，两者一致" : "，如需让机器人使用这张地图请点击切换按钮"}`;
}

function radiansToDegrees(rad) {
  return rad * 180 / Math.PI;
}

function degreesToRadians(deg) {
  return deg * Math.PI / 180;
}

function normalizeAngle(angle) {
  let value = angle;
  while (value <= -Math.PI) value += Math.PI * 2;
  while (value > Math.PI) value -= Math.PI * 2;
  return value;
}

function setCurrentYaw(yaw) {
  state.currentYaw = normalizeAngle(yaw);
  $("pointYaw").value = radiansToDegrees(state.currentYaw).toFixed(1);
}

function quaternionToYaw(point) {
  const orientation = point.pose?.orientation || {};
  const qx = Number(orientation.x || 0);
  const qy = Number(orientation.y || 0);
  const qz = Number(orientation.z || 0);
  const qw = Number(orientation.w == null ? 1 : orientation.w);
  const siny = 2 * (qw * qz + qx * qy);
  const cosy = 1 - 2 * (qy * qy + qz * qz);
  return Math.atan2(siny, cosy);
}

function worldToPixel(position) {
  const map = state.currentMapInfo || {};
  return {
    u: Number(position.x || 0) * Number(map.resolution_pixels_per_meter || 1) + Number(map.origin_u || 0),
    v: Number(map.origin_v || 0) - Number(position.y || 0) * Number(map.resolution_pixels_per_meter || 1)
  };
}

function pixelToWorld(u, v) {
  const map = state.currentMapInfo || {};
  return {
    x: (u - Number(map.origin_u || 0)) / Number(map.resolution_pixels_per_meter || 1),
    y: -(v - Number(map.origin_v || 0)) / Number(map.resolution_pixels_per_meter || 1)
  };
}

function getArrivalActions(point) {
  return Array.isArray(point.business?.on_arrival) ? point.business.on_arrival : [];
}

function defaultTask() {
  return { id: "default", name: "默认讲解任务", enabled: true, steps: [] };
}

function hasOwn(source, key) {
  return Object.prototype.hasOwnProperty.call(source, key);
}

function normalizeArrivalMotion(value) {
  if (!value || typeof value !== "object" || !String(value.motion || "").trim()) return null;
  const motion = String(value.motion).trim();
  const allowedAreas = motionAreaOptions(motion).map(([area]) => area);
  const requestedArea = String(value.area || "").trim();
  return {
    motion,
    area: allowedAreas.includes(requestedArea) ? requestedArea : (allowedAreas[0] || requestedArea || "right_hand"),
    interrupt: motion === "empty_motion" ? true : value.interrupt !== false,
    force: value.force !== false
  };
}

function normalizeArrivalEmoji(value) {
  if (!value || typeof value !== "object" || !String(value.emotion || "").trim()) return null;
  return {
    emotion: String(value.emotion).trim(),
    mode: String(value.mode || "once").trim() || "once"
  };
}

function normalizeLinkcraftAction(value) {
  if (!value || typeof value !== "object") return null;
  const resourceKey = String(value.resource_key || "").trim();
  const resourceVersion = String(value.resource_version || "").trim();
  if (!resourceKey.toLowerCase().startsWith("linkcraft_resource_") || !resourceVersion) return null;
  const duration = Number(value.duration_sec || 30);
  return {
    resource_key: resourceKey,
    resource_version: resourceVersion,
    name: String(value.name || resourceKey).trim() || resourceKey,
    resource_type: value.resource_type === "ARM_MONTION" ? "ARM_MONTION" : "BODY_MONTION",
    duration_sec: Math.min(300, Math.max(0.1, Number.isFinite(duration) ? duration : 30))
  };
}

function normalizeTaskStep(step) {
  const normalized = {
    point_id: String(step.point_id || step.id || "").trim(),
    enabled: step.enabled !== false,
    speech_text: String(step.speech_text || step.text || "").trim()
  };
  if (hasOwn(step, "arrival_motion")) normalized.arrival_motion = normalizeArrivalMotion(step.arrival_motion);
  if (hasOwn(step, "arrival_emoji")) normalized.arrival_emoji = normalizeArrivalEmoji(step.arrival_emoji);
  if (hasOwn(step, "linkcraft_action")) normalized.linkcraft_action = normalizeLinkcraftAction(step.linkcraft_action);
  return normalized;
}

function taskStepForSave(step) {
  const payload = {
    point_id: step.point_id,
    enabled: step.enabled !== false,
    speech_text: String(step.speech_text || "").trim()
  };
  if (hasOwn(step, "arrival_motion")) payload.arrival_motion = step.arrival_motion;
  if (hasOwn(step, "arrival_emoji")) payload.arrival_emoji = step.arrival_emoji;
  if (hasOwn(step, "linkcraft_action")) payload.linkcraft_action = step.linkcraft_action;
  return payload;
}

function normalizeTask(task, fallbackId = "default") {
  const source = task && typeof task === "object" ? task : {};
  return {
    id: String(source.id || fallbackId || `task_${Date.now()}`).trim(),
    name: String(source.name || "默认讲解任务").trim() || "默认讲解任务",
    enabled: source.enabled !== false,
    steps: Array.isArray(source.steps)
      ? source.steps.map(normalizeTaskStep).filter((step) => step.point_id)
      : []
  };
}

function loadTasksFromMapPayload(payload) {
  const activeId = String(payload.active_tour_task_id || payload.tour_task?.id || "default");
  const rawTasks = Array.isArray(payload.tour_tasks) && payload.tour_tasks.length
    ? payload.tour_tasks
    : [payload.tour_task || defaultTask()];
  const byId = new Map();
  rawTasks.forEach((task, index) => {
    const normalized = normalizeTask(task, index === 0 ? activeId : `task_${index + 1}`);
    byId.set(normalized.id, normalized);
  });
  if (payload.tour_task?.id && !byId.has(payload.tour_task.id)) {
    const activeTask = normalizeTask(payload.tour_task, activeId);
    byId.set(activeTask.id, activeTask);
  }
  state.tasks = [...byId.values()];
  state.task = state.tasks.find((task) => task.id === activeId) || state.tasks[0] || defaultTask();
  if (!state.tasks.length) state.tasks = [state.task];
}

function normalizePoint(point) {
  const pose = point.pose || {};
  const position = pose.position || {};
  const orientation = pose.orientation || {};
  const actions = getArrivalActions(point);
  const tts = actions.find((item) => item.type === "tts") || {};
  const motion = actions.find((item) => item.type === "motion") || {};
  const emoji = actions.find((item) => item.type === "emoji") || {};
  const yaw = quaternionToYaw(point);
  const ui = point.ui || {};
  const pixel = Number.isFinite(Number(ui.pixel_u)) && Number.isFinite(Number(ui.pixel_v))
    ? { u: Number(ui.pixel_u), v: Number(ui.pixel_v) }
    : worldToPixel(position);
  return {
    ...point,
    aliasesText: Array.isArray(point.aliases) ? point.aliases.join(",") : "",
    x: Number(position.x || 0),
    y: Number(position.y || 0),
    z: Number(position.z || 0),
    qx: Number(orientation.x || 0),
    qy: Number(orientation.y || 0),
    qz: Number(orientation.z || 0),
    qw: Number(orientation.w == null ? 1 : orientation.w),
    yaw,
    pixel,
    headingPixel: Number.isFinite(Number(ui.heading_pixel_u)) && Number.isFinite(Number(ui.heading_pixel_v))
      ? { u: Number(ui.heading_pixel_u), v: Number(ui.heading_pixel_v) }
      : { u: pixel.u + 28 * Math.cos(yaw), v: pixel.v - 28 * Math.sin(yaw) },
    radius: Number(point.navigation?.target_pose_radius ?? 0.15),
    maxSpeed: Number(point.navigation?.max_forward_speed ?? 0.3),
    pncMode: Number(point.navigation?.pnc_mode ?? 0),
    taskType: Number(point.navigation?.task_type ?? 2),
    arrivalText: tts.text || "",
    arrivalMotion: motion.motion || "",
    arrivalMotionArea: motion.area || "right_hand",
    arrivalEmoji: emoji.emotion || "",
    arrivalEmojiMode: emoji.mode || "once"
  };
}

function pointFromForm() {
  const mapName = state.currentMap;
  const name = $("pointName").value.trim();
  const id = $("pointId").value || `${mapName}:${name}`;
  const actions = [];
  const motion = $("arrivalMotion").value;
  const emoji = $("arrivalEmoji").value;
  const tts = $("arrivalText").value.trim();
  if (motion) {
    actions.push({
      type: "motion",
      motion,
      area: $("arrivalMotionArea").value || "right_hand",
      interrupt: motion === "empty_motion"
    });
  }
  if (emoji) {
    actions.push({
      type: "emoji",
      emotion: emoji,
      mode: $("arrivalEmojiMode").value || "once"
    });
  }
  if (tts) actions.push({ type: "tts", text: tts });
  return {
    id,
    name,
    map_name: mapName,
    map_id: Number(state.currentMapInfo?.map_id || mapName) || mapName,
    is_start: $("pointStart").checked,
    aliases: $("pointAliases").value.split(",").map((item) => item.trim()).filter(Boolean),
    pose: {
      position: { x: Number($("pointX").value || 0), y: Number($("pointY").value || 0), z: Number($("pointZ").value || 0) },
      orientation: { x: 0, y: 0, z: Math.sin(state.currentYaw / 2), w: Math.cos(state.currentYaw / 2) }
    },
    navigation: {
      target_pose_radius: Number($("pointRadius").value || 0.15),
      pnc_mode: Number($("pointPncMode").value || 0),
      max_forward_speed: Number($("pointSpeed").value || 0.3),
      task_type: Number($("pointTaskType").value || 2)
    },
    ui: {
      pixel_u: state.targetPixel?.u ?? null,
      pixel_v: state.targetPixel?.v ?? null,
      heading_pixel_u: state.headingPixel?.u ?? null,
      heading_pixel_v: state.headingPixel?.v ?? null
    },
    voice: { enabled: true, display_name: name },
    business: {
      on_arrival: actions
    }
  };
}

function fillPointForm(point) {
  state.selectedPoint = point;
  $("editorTitle").textContent = point?.id ? "编辑点位" : "新增点位";
  $("pointId").value = point?.id || "";
  $("pointName").value = point?.name || "";
  $("pointAliases").value = point?.aliasesText || "";
  $("pointX").value = point?.x ?? 0;
  $("pointY").value = point?.y ?? 0;
  $("pointZ").value = point?.z ?? 0;
  $("pointRadius").value = point?.radius ?? 0.15;
  $("pointSpeed").value = point?.maxSpeed ?? 0.3;
  $("pointPncMode").value = point?.pncMode ?? 0;
  $("pointTaskType").value = point?.taskType ?? 2;
  $("pointStart").checked = Boolean(point?.is_start);
  $("arrivalText").value = point?.arrivalText || "";
  $("arrivalMotion").value = point?.arrivalMotion || "";
  syncPointMotionArea(point?.arrivalMotionArea || "right_hand");
  $("arrivalEmoji").value = point?.arrivalEmoji || "";
  $("arrivalEmojiMode").value = point?.arrivalEmojiMode || "once";
  $("deletePointBtn").disabled = !point?.id;
  state.targetPixel = point?.pixel || null;
  state.headingPixel = point?.headingPixel || null;
  state.awaitingHeading = false;
  setCurrentYaw(point?.yaw ?? 0);
  drawMap();
}

function renderStatus() {
  const payload = state.status || {};
  const nav = payload.nav_status || {};
  $("onlineBadge").textContent = payload.bridge_online ? "在线" : "离线";
  $("onlineBadge").className = `badge ${payload.bridge_online ? "ok" : "bad"}`;
  $("activeMap").textContent = nav.active_map_name || "-";
  renderActiveMapNotice();
  $("navMode").textContent = nav.nav_mode || "-";
  $("navState").textContent = label(nav.navigation?.status, {
    idle: "空闲",
    running: "导航中",
    stopping: "停止中"
  });
  $("returnState").textContent = label(nav.auto_return?.state, {
    idle: "空闲",
    waiting_auto_return: "等待返航",
    returning_home: "返航中",
    navigating_task_step: "讲解任务中",
    task_arrival_action: "任务到点讲解中",
    task_linkcraft_action: "正在执行灵创舞蹈/动作",
    task_paused: "讲解已暂停"
  });
  $("mapWebState").textContent = payload.map_web_online ? "在线" : "离线";
  $("sdkState").textContent = nav.sdk_services
    ? `动作 ${nav.sdk_services.motion ? "正常" : "异常"} / 表情 ${nav.sdk_services.emoji ? "正常" : "异常"} / 灵创 ${nav.sdk_services.action_resource ? "正常" : "异常"}`
    : "-";
  const errors = payload.errors || {};
  $("statusErrors").textContent = Object.entries(errors)
    .filter(([, value]) => value)
    .map(([key, value]) => `${statusKeyName(key)}：${toChineseMessage(value)}`)
    .join("\n");

  const pointNames = payload.points?.point_names || [];
  $("pointButtons").innerHTML = "";
  pointNames.forEach((name) => {
    const button = document.createElement("button");
    button.textContent = name;
    button.onclick = () => runAction(`正在导航到 ${name}`, () => api("/api/robots/default/nav/start", {
      method: "POST",
      body: JSON.stringify({ target_name: name })
    }));
    $("pointButtons").appendChild(button);
  });
}

function renderConfig() {
  const config = state.config || {};
  if (config.deployment_mode === "robot") {
    const urls = config.access_urls || [];
    $("robotUrls").textContent = urls.length
      ? urls.join("\n")
      : `当前访问地址：${window.location.origin}/console/`;
    return;
  }
  if ($("robotHostInput")) $("robotHostInput").value = config.robot_host || "";
  const proxyHost = config.proxy_host || config.robot_host || "";
  const bridge = proxyHost ? `http://${proxyHost}:${config.bridge_port}` : "-";
  const web = proxyHost ? `http://${proxyHost}:${config.web_port}` : "-";
  const modeText = config.connection_mode === "tunnel"
    ? "当前为公网网关模式：请填写现场机器人 IP，现场网关会按这个 IP 连接机器人。"
    : "当前为直连模式：后端必须能访问这里填写的机器人 IP。";
  $("robotUrls").textContent = `${modeText}\n当前目标机器人：${config.robot_host || "-"}\n当前代理地址：导航 ${bridge}，地图 ${web}`;
}

function renderStartup() {
  const payload = state.startup || {};
  $("readyBadge").textContent = payload.ready ? "可使用" : "未就绪";
  $("readyBadge").className = `badge ${payload.ready ? "ok" : "bad"}`;
  const list = $("startupChecklist");
  list.innerHTML = "";
  (payload.checklist || []).forEach((step, index) => {
    const item = document.createElement("div");
    item.className = `wizard-step ${step.ok ? "ok" : ""}`;
    item.innerHTML = `<strong>${index + 1}. ${step.label}</strong><small>${step.ok ? "已完成" : "待处理"}：${step.detail || ""}</small>`;
    list.appendChild(item);
  });
  renderCustomerDashboard();
}

function renderCustomerDashboard() {
  const startup = state.startup || {};
  const status = state.status || startup.robot || {};
  const nav = status.nav_status || {};
  const tunnel = startup.tunnel || {};
  const javaAgent = startup.java_agent || {};
  const localization = nav.localization || {};
  const ready = Boolean(startup.ready);

  setClass("customerReadyDot", `status-dot ${ready ? "ok" : "bad"}`);
  setText("customerReadyTitle", ready ? "机器人已准备好" : "机器人还没准备好");
  setText("customerReadyDetail", ready
    ? (startup.navigation_ready
        ? "语音、导航、地图和现场定位均已就绪。"
        : "语音、导航和地图服务已启动，正式移动前请完成现场重定位。")
    : "请点击“一键启动全部服务”，系统会自动启动导航、地图和语音服务。");

  const tunnelOk = Boolean(tunnel.ok || (status.bridge_online && status.map_web_online));
  setText("customerTunnelState", tunnelOk ? "已连接" : "未连接");
  setText("customerTunnelDetail", tunnelOk
    ? (state.config?.deployment_mode === "robot"
        ? "机器人本机控制台、导航服务和地图服务可用。"
        : "运维通道、导航服务和地图服务可用。")
    : "请确认机器人已开机并连接现场网络。");

  const voiceOk = Boolean(javaAgent.running && javaAgent.lingxiConnected);
  setText("customerVoiceState", voiceOk ? "已连接" : (javaAgent.running ? "启动中" : "未启动"));
  setText("customerVoiceDetail", voiceOk
    ? "语音服务已连接灵心平台。"
    : "点击一键启动后会自动启动语音服务。");

  const activeMap = nav.active_map_name || "-";
  const pointCount = status.points?.point_names?.length || 0;
  setText("customerMapState", activeMap);
  setText("customerMapDetail", pointCount ? `已读取 ${pointCount} 个导航点。` : "请确认当前地图和导航点。");

  const locOk = Boolean(localization.ok);
  setText("customerLocalizationState", locOk ? "已定位" : "待确认");
  setText("customerLocalizationDetail", locOk
    ? "已收到机器人实时定位数据。"
    : "正式导航前建议点击现场重定位。");
}

function renderSystemHealth() {
  const payload = state.systemHealth || {};
  const checked = Boolean(state.systemHealth);
  $("systemHealthBadge").textContent = !checked ? "未检查" : (payload.ok ? "正常" : "异常");
  $("systemHealthBadge").className = `badge ${payload.ok ? "ok" : "bad"}`;
  $("systemServiceState").textContent = payload.agibot_software || "-";
  $("systemHalState").textContent = checked ? (payload.hal_sensor_running ? "运行中" : "未运行") : "-";
  $("systemSlamState").textContent = checked ? (payload.slam_running ? "运行中" : "未运行") : "-";
  const topics = payload.topics || {};
  const topicLines = Object.values(topics).map((topic) => {
    const pub = topic.publishers == null ? "未知" : topic.publishers;
    const sub = topic.subscriptions == null ? "未知" : topic.subscriptions;
    return `${topic.topic}：发布者 ${pub}，订阅者 ${sub}`;
  });
  const adviceLines = (payload.advice || []).map((item) => `建议：${item}`);
  const nodeLines = [
    checked ? `SOC1 节点：${payload.soc1_visible ? "可见" : "不可见"}` : "",
    checked ? `SOC2 节点：${payload.soc2_visible ? "可见" : "不可见"}` : "",
    payload.system_state ? `系统状态：${payload.system_state}` : "",
    Array.isArray(payload.network_interfaces) && payload.network_interfaces.length
      ? `网络接口：${payload.network_interfaces.join("，")}`
      : ""
  ];
  $("systemHealthDetails").textContent = [
    payload.diagnosis || "尚未检查底层服务",
    ...adviceLines,
    ...nodeLines,
    ...topicLines,
    payload.checked_at ? `检查时间：${new Date(payload.checked_at).toLocaleString()}` : ""
  ].filter(Boolean).join("\n");
}

function renderRelocalization() {
  const payload = state.relocalization || {};
  const result = payload.result || {};
  $("relocRunning").textContent = payload.running ? `运行中${payload.pid ? ` pid=${payload.pid}` : ""}` : "未运行";
  $("relocResult").textContent = result.status ? `${result.status}${result.message ? ` - ${result.message}` : ""}` : "-";
  $("relocPoint").textContent = state.lastRelocalizationPoint || "-";
  $("relocDetails").textContent = [
    result.map_id ? `地图 ID：${result.map_id}` : "",
    result.pixel_x != null ? `像素坐标：${Number(result.pixel_x).toFixed(2)}, ${Number(result.pixel_y).toFixed(2)}` : "",
    result.expected_world_x != null ? `预期世界坐标：${Number(result.expected_world_x).toFixed(3)}, ${Number(result.expected_world_y).toFixed(3)}` : "",
    result.slam_lidar_odom_publishers != null ? `/slam/lidar_odom 发布者数量：${result.slam_lidar_odom_publishers}` : "",
    result.integrated_command_subscriptions != null ? `/integrated_command 订阅者数量：${result.integrated_command_subscriptions}` : "",
    result.relocalization_pose_subscriptions != null ? `/relocalization_pose 订阅者数量：${result.relocalization_pose_subscriptions}` : "",
    payload.log_file ? `日志文件：${payload.log_file}` : ""
  ].filter(Boolean).join("\n");
}

function fillRelocalizationDefaults() {
  const map = state.currentMapInfo || {};
  $("relocMapId").value = map.map_id || state.currentMap || "";
  $("relocX").value = Number.isFinite(Number(map.origin_u)) ? Number(map.origin_u).toFixed(3) : "";
  $("relocY").value = Number.isFinite(Number(map.origin_v)) ? Number(map.origin_v).toFixed(3) : "";
  $("relocZ").value = "0";
  $("relocTimeout").value = $("relocTimeout").value || "30";
  $("relocQx").value = "0";
  $("relocQy").value = "0";
  $("relocQz").value = "0";
  $("relocQw").value = "1";
  state.lastRelocalizationPoint = "地图原点像素";
  renderRelocalization();
}

function fillRelocalizationFromSelectedPoint() {
  if (!state.currentMapInfo) {
    toast("请先选择地图");
    return;
  }
  if (!state.targetPixel) {
    toast("请先在地图上点击重定位位置");
    return;
  }
  $("relocMapId").value = state.currentMapInfo.map_id || state.currentMap || "";
  $("relocX").value = Number(state.targetPixel.u).toFixed(3);
  $("relocY").value = Number(state.targetPixel.v).toFixed(3);
  $("relocZ").value = "0";
  $("relocQx").value = "0";
  $("relocQy").value = "0";
  $("relocQz").value = Math.sin(state.currentYaw / 2).toFixed(12);
  $("relocQw").value = Math.cos(state.currentYaw / 2).toFixed(12);
  state.lastRelocalizationPoint = "当前地图选点";
  renderRelocalization();
}

function preferredRelocalizationPoint() {
  if (!state.points.length) return null;
  return state.points.find((point) => point.is_start)
    || state.points.find((point) => /待机点|起点|原点/.test(point.name || ""))
    || state.points[0];
}

function selectedAutoRelocalizationPoint() {
  const selectedId = $("autoRelocPointSelect").value;
  return state.points.find((point) => point.id === selectedId)
    || preferredRelocalizationPoint();
}

function renderAutoRelocalizationOptions() {
  const select = $("autoRelocPointSelect");
  if (!select) return;
  const preferred = preferredRelocalizationPoint();
  const previous = select.value;
  select.innerHTML = "";
  state.points.forEach((point) => {
    const option = document.createElement("option");
    option.value = point.id;
    option.textContent = `${point.name}${point.is_start ? "（起点）" : ""}`;
    select.appendChild(option);
  });
  select.value = state.points.some((point) => point.id === previous)
    ? previous
    : (preferred?.id || "");
}

function fillRelocalizationFromSavedPoint() {
  if (!state.currentMapInfo) {
    throw new Error("请先选择地图");
  }
  const point = selectedAutoRelocalizationPoint();
  if (!point) {
    throw new Error("当前地图没有可用于自动重定位的点位");
  }
  const offsetX = Number($("autoRelocOffsetX").value || 0);
  const offsetY = Number($("autoRelocOffsetY").value || 0);
  const yawOffset = degreesToRadians(Number($("autoRelocYawOffset").value || 0));
  if (![offsetX, offsetY, yawOffset].every(Number.isFinite)) {
    throw new Error("请填写有效的修正量");
  }
  const yaw = normalizeAngle(point.yaw + yawOffset);
  $("relocMapId").value = state.currentMapInfo.map_id || state.currentMap || "";
  $("relocX").value = Number(point.pixel.u + offsetX).toFixed(3);
  $("relocY").value = Number(point.pixel.v + offsetY).toFixed(3);
  $("relocZ").value = Number(point.z || 0).toFixed(3);
  $("relocQx").value = "0";
  $("relocQy").value = "0";
  $("relocQz").value = Math.sin(yaw / 2).toFixed(12);
  $("relocQw").value = Math.cos(yaw / 2).toFixed(12);
  state.lastRelocalizationPoint = `${point.name} 自动点位，修正 X=${offsetX}, Y=${offsetY}, 角度=${$("autoRelocYawOffset").value || 0}°`;
  renderRelocalization();
}

async function startAutoRelocalization() {
  fillRelocalizationFromSavedPoint();
  return startRelocalizationFromForm();
}

function relocalizationPayloadFromForm() {
  const mapId = Number($("relocMapId").value);
  const x = Number($("relocX").value);
  const y = Number($("relocY").value);
  const z = Number($("relocZ").value || 0);
  if (!Number.isFinite(mapId) || mapId <= 0) throw new Error("请填写有效的地图 ID");
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("请填写有效的像素 X/Y");
  if (!Number.isFinite(z)) throw new Error("请填写有效的 Z");
  const width = Number(state.currentMapInfo?.width);
  const height = Number(state.currentMapInfo?.height);
  const currentMapId = Number(state.currentMapInfo?.map_id || state.currentMap);
  if (
    Number.isFinite(width)
    && Number.isFinite(height)
    && mapId === currentMapId
    && (x < 0 || y < 0 || x > width || y > height)
  ) {
    throw new Error(`重定位像素坐标超出地图范围：x=${x}, y=${y}, map=${width}x${height}`);
  }
  return {
    map_id: mapId,
    x,
    y,
    map_root: state.config?.map_root || undefined,
    z,
    qx: Number($("relocQx").value || 0),
    qy: Number($("relocQy").value || 0),
    qz: Number($("relocQz").value || 0),
    qw: Number($("relocQw").value || 1),
    timeout_sec: Number($("relocTimeout").value || 30)
  };
}

async function startRelocalizationFromForm() {
  const payload = relocalizationPayloadFromForm();
  const result = await api("/api/robots/default/relocalization/start", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  state.relocalization = result;
  renderRelocalization();
  return result;
}

function renderLocalization() {
  const payload = state.localization;
  if (!payload?.pose) {
    $("locX").textContent = "-";
    $("locY").textContent = "-";
    $("locYaw").textContent = "-";
    $("locDetails").textContent = payload?.message || "尚未获取当前定位";
    return;
  }
  const position = payload.pose.position;
  const orientation = payload.pose.orientation;
  $("locX").textContent = Number(position.x).toFixed(3);
  $("locY").textContent = Number(position.y).toFixed(3);
  $("locYaw").textContent = `${Number(payload.pose.yaw_deg).toFixed(1)}°`;
  $("locDetails").textContent = [
    `来源话题：${payload.topic}`,
    `获取时间：${new Date(payload.acquired_at).toLocaleString()}`,
    `Z 坐标：${Number(position.z).toFixed(3)}`,
    `四元数：x=${Number(orientation.x).toFixed(4)}, y=${Number(orientation.y).toFixed(4)}, z=${Number(orientation.z).toFixed(4)}, w=${Number(orientation.w).toFixed(4)}`
  ].join("\n");
  drawMap();
}

function renderMaps() {
  const select = $("mapSelect");
  select.innerHTML = "";
  state.maps.forEach((map, index) => {
    const option = document.createElement("option");
    option.value = map.map_name || map.name || map;
    option.textContent = index === 0 ? `${option.value}（最新地图）` : option.value;
    select.appendChild(option);
  });
  select.value = state.currentMap;
  renderActiveMapNotice();
}

function drawArrow(ctx, startU, startV, yaw, color, length = 28) {
  const endU = startU + length * Math.cos(yaw);
  const endV = startV - length * Math.sin(yaw);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(startU, startV);
  ctx.lineTo(endU, endV);
  ctx.stroke();
  const headAngle = Math.atan2(startV - endV, endU - startU);
  ctx.beginPath();
  ctx.moveTo(endU, endV);
  ctx.lineTo(endU - 8 * Math.cos(headAngle - Math.PI / 6), endV + 8 * Math.sin(headAngle - Math.PI / 6));
  ctx.lineTo(endU - 8 * Math.cos(headAngle + Math.PI / 6), endV + 8 * Math.sin(headAngle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

function drawMap() {
  const canvas = $("mapCanvas");
  if (!canvas || !mapImage.complete || !mapImage.naturalWidth) return;
  const ctx = canvas.getContext("2d");
  canvas.width = mapImage.naturalWidth;
  canvas.height = mapImage.naturalHeight;
  ctx.drawImage(mapImage, 0, 0);
  ctx.font = "14px Arial";
  state.points.forEach((point) => {
    const p = point.pixel || worldToPixel({ x: point.x, y: point.y });
    ctx.fillStyle = point.is_start ? "rgba(15,118,110,.95)" : "rgba(220,38,38,.9)";
    ctx.beginPath();
    ctx.arc(p.u, p.v, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#111827";
    ctx.fillText(point.name, p.u + 8, p.v - 8);
    drawArrow(ctx, p.u, p.v, point.yaw || 0, "#f59e0b");
  });
  if (state.targetPixel) {
    ctx.strokeStyle = "#0ea5e9";
    ctx.lineWidth = 2;
    ctx.strokeRect(state.targetPixel.u - 6, state.targetPixel.v - 6, 12, 12);
    drawArrow(ctx, state.targetPixel.u, state.targetPixel.v, state.currentYaw, "#0ea5e9");
  }
  if (state.targetPixel && state.headingPixel) {
    ctx.strokeStyle = "rgba(14,165,233,.9)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(state.targetPixel.u, state.targetPixel.v);
    ctx.lineTo(state.headingPixel.u, state.headingPixel.v);
    ctx.stroke();
  }
  if (state.localization?.pose) {
    const position = state.localization.pose.position;
    const p = worldToPixel(position);
    ctx.fillStyle = "#7c3aed";
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(p.u, p.v, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    drawArrow(ctx, p.u, p.v, Number(state.localization.pose.yaw_rad || 0), "#7c3aed", 34);
    ctx.fillStyle = "#4c1d95";
    ctx.fillText("机器人当前位置", p.u + 10, p.v + 18);
  }
}

function setHeadingPixel(u, v) {
  state.headingPixel = { u, v };
  const dx = u - state.targetPixel.u;
  const dy = state.targetPixel.v - v;
  setCurrentYaw(Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6 ? 0 : Math.atan2(dy, dx));
  state.awaitingHeading = false;
  drawMap();
  toast(`朝向已设置为 ${$("pointYaw").value}°`);
}

function handleMapPointer(event, forceHeading = false) {
  if (!state.currentMapInfo) {
    toast("请先选择地图");
    return;
  }
  const canvas = $("mapCanvas");
  const rect = canvas.getBoundingClientRect();
  const u = (event.clientX - rect.left) * (canvas.width / rect.width);
  const v = (event.clientY - rect.top) * (canvas.height / rect.height);
  if (!state.targetPixel || (!forceHeading && !state.awaitingHeading)) {
    state.targetPixel = { u, v };
    state.headingPixel = null;
    state.awaitingHeading = true;
    const world = pixelToWorld(u, v);
    $("pointX").value = world.x.toFixed(3);
    $("pointY").value = world.y.toFixed(3);
    if (!$("pointName").value.trim()) $("pointName").focus();
    setCurrentYaw(0);
    drawMap();
    toast("已选择点位，请再次点击或右键设置朝向");
    return;
  }
  setHeadingPixel(u, v);
}

function renderMapPayload() {
  renderAutoRelocalizationOptions();
  if (state.currentMap) {
    mapImage.onload = drawMap;
    mapImage.src = `/api/robots/default/maps/${encodeURIComponent(state.currentMap)}/image`;
  }
  $("pointsList").innerHTML = "";
  state.points.forEach((point) => {
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `<div><strong>${point.name}</strong><small>${point.is_start ? "起点 · " : ""}${point.aliasesText || "无别名"} · x=${point.x.toFixed(3)}, y=${point.y.toFixed(3)}, yaw=${radiansToDegrees(point.yaw).toFixed(1)}°</small></div>`;
    const button = document.createElement("button");
    button.className = "secondary";
    button.textContent = "编辑";
    button.onclick = () => fillPointForm(point);
    item.appendChild(button);
    $("pointsList").appendChild(item);
  });

  $("taskPointSelect").innerHTML = "";
  state.points.forEach((point) => {
    const option = document.createElement("option");
    option.value = point.id;
    option.textContent = point.name;
    $("taskPointSelect").appendChild(option);
  });
  renderTask();
}

function optionName(options, value) {
  return options.find(([optionValue]) => optionValue === value)?.[1] || "未设置";
}

function linkcraftResourceOptions(selectedAction) {
  const options = [["", "不执行灵创舞蹈/动作"]];
  state.linkcraftResources
    .filter((resource) => resource.available !== false)
    .forEach((resource) => {
      const duration = Number(resource.duration_sec || 30).toFixed(1);
      options.push([resource.resource_key, `${resource.name}（约 ${duration} 秒）`]);
    });
  if (selectedAction && !options.some(([value]) => value === selectedAction.resource_key)) {
    options.push([
      selectedAction.resource_key,
      `${selectedAction.name || selectedAction.resource_key}（当前机器人未找到）`
    ]);
  }
  return options;
}

function createStepNumberInput(labelText, value, onChange, disabled = false) {
  const field = document.createElement("label");
  field.textContent = labelText;
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0.1";
  input.max = "300";
  input.step = "0.1";
  input.value = Number(value || 30).toFixed(1);
  input.disabled = disabled;
  input.onchange = () => {
    const parsed = Number(input.value);
    const duration = Math.min(300, Math.max(0.1, Number.isFinite(parsed) ? parsed : 30));
    input.value = duration.toFixed(1);
    onChange(duration);
  };
  field.appendChild(input);
  return field;
}

function motionAreaOptions(motion) {
  const allowed = MOTION_OPTIONS.find(([value]) => value === motion)?.[2] || [];
  return MOTION_AREA_OPTIONS.filter(([value]) => allowed.includes(value));
}

function createStepSelect(labelText, options, value, onChange, disabled = false) {
  const field = document.createElement("label");
  field.textContent = labelText;
  const select = document.createElement("select");
  options.forEach(([optionValue, optionText]) => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionText;
    select.appendChild(option);
  });
  select.value = value;
  select.disabled = disabled;
  select.onchange = () => onChange(select.value);
  field.appendChild(select);
  return field;
}

function populatePointEffectOptions() {
  const fill = (id, options) => {
    const select = $(id);
    if (!select) return;
    select.innerHTML = "";
    options.forEach(([value, text]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      select.appendChild(option);
    });
  };
  fill("arrivalMotion", [["", "无动作"], ...MOTION_OPTIONS]);
  fill("arrivalEmoji", [["", "无表情"], ...EMOJI_OPTIONS]);
  fill("arrivalEmojiMode", EMOJI_MODE_OPTIONS);
  $("arrivalMotion").onchange = () => syncPointMotionArea();
  syncPointMotionArea();
}

function syncPointMotionArea(preferredArea = "") {
  const motion = $("arrivalMotion").value;
  const options = motionAreaOptions(motion);
  const select = $("arrivalMotionArea");
  const current = preferredArea || select.value;
  select.innerHTML = "";
  (options.length ? options : MOTION_AREA_OPTIONS).forEach(([value, text]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.appendChild(option);
  });
  select.value = [...select.options].some((option) => option.value === current)
    ? current
    : (select.options[0]?.value || "right_hand");
  select.disabled = !motion;
}

function renderTask() {
  renderTaskSelector();
  $("taskName").value = state.task.name || "默认讲解任务";
  const byId = new Map(state.points.map((point) => [point.id, point]));
  $("taskSteps").innerHTML = "";
  (state.task.steps || []).forEach((step, index) => {
    const point = byId.get(step.point_id);
    const item = document.createElement("div");
    item.className = "item task-step-item";
    const content = document.createElement("div");
    content.className = "task-step-content";
    const title = document.createElement("strong");
    title.textContent = `${index + 1}. ${point?.name || step.point_id}`;
    const meta = document.createElement("small");
    const motionSummary = !hasOwn(step, "arrival_motion")
      ? "动作沿用点位"
      : (step.arrival_motion ? `动作：${optionName(MOTION_OPTIONS, step.arrival_motion.motion)}` : "不执行动作");
    const emojiSummary = !hasOwn(step, "arrival_emoji")
      ? "表情沿用点位"
      : (step.arrival_emoji ? `表情：${optionName(EMOJI_OPTIONS, step.arrival_emoji.emotion)}` : "不显示表情");
    const linkcraftSummary = step.linkcraft_action
      ? `灵创：${step.linkcraft_action.name}（约 ${Number(step.linkcraft_action.duration_sec).toFixed(1)} 秒）`
      : "不执行灵创资源";
    meta.textContent = `${step.enabled === false ? "已禁用" : "启用"} · ${motionSummary} · ${emojiSummary} · ${linkcraftSummary}`;
    const speech = document.createElement("textarea");
    speech.rows = 3;
    speech.placeholder = "到达该点后的讲解内容";
    speech.value = step.speech_text || "";
    speech.oninput = () => {
      state.task.steps[index].speech_text = speech.value;
    };
    content.appendChild(title);
    content.appendChild(meta);
    content.appendChild(speech);
    const effects = document.createElement("div");
    effects.className = "task-step-effects";
    const motionValue = !hasOwn(step, "arrival_motion")
      ? INHERIT_SETTING
      : (step.arrival_motion?.motion || "");
    effects.appendChild(createStepSelect("讲解动作", [
      [INHERIT_SETTING, "沿用点位设置"], ["", "不执行动作"], ...MOTION_OPTIONS
    ], motionValue, (value) => {
      if (value === INHERIT_SETTING) delete state.task.steps[index].arrival_motion;
      else if (!value) state.task.steps[index].arrival_motion = null;
      else {
        const allowedAreas = motionAreaOptions(value).map(([area]) => area);
        const previousArea = step.arrival_motion?.area || "";
        state.task.steps[index].arrival_motion = {
          motion: value,
          area: allowedAreas.includes(previousArea) ? previousArea : allowedAreas[0],
          interrupt: true,
          force: true
        };
      }
      renderTask();
    }));
    const stepAreaOptions = step.arrival_motion
      ? motionAreaOptions(step.arrival_motion.motion)
      : MOTION_AREA_OPTIONS;
    effects.appendChild(createStepSelect(
      "动作部位",
      stepAreaOptions,
      step.arrival_motion?.area || stepAreaOptions[0]?.[0] || "right_hand",
      (value) => { state.task.steps[index].arrival_motion.area = value; },
      !step.arrival_motion
    ));
    const emojiValue = !hasOwn(step, "arrival_emoji")
      ? INHERIT_SETTING
      : (step.arrival_emoji?.emotion || "");
    effects.appendChild(createStepSelect("讲解表情", [
      [INHERIT_SETTING, "沿用点位设置"], ["", "不显示表情"], ...EMOJI_OPTIONS
    ], emojiValue, (value) => {
      if (value === INHERIT_SETTING) delete state.task.steps[index].arrival_emoji;
      else if (!value) state.task.steps[index].arrival_emoji = null;
      else state.task.steps[index].arrival_emoji = {
        emotion: value,
        mode: step.arrival_emoji?.mode || "once"
      };
      renderTask();
    }));
    effects.appendChild(createStepSelect(
      "表情模式",
      EMOJI_MODE_OPTIONS,
      step.arrival_emoji?.mode || "once",
      (value) => { state.task.steps[index].arrival_emoji.mode = value; },
      !step.arrival_emoji
    ));
    const linkcraftOptions = linkcraftResourceOptions(step.linkcraft_action);
    effects.appendChild(createStepSelect(
      "灵创舞蹈/动作",
      linkcraftOptions,
      step.linkcraft_action?.resource_key || "",
      (value) => {
        if (!value) {
          state.task.steps[index].linkcraft_action = null;
        } else {
          const resource = state.linkcraftResources.find((item) => item.resource_key === value);
          if (!resource) return;
          state.task.steps[index].linkcraft_action = normalizeLinkcraftAction(resource);
        }
        renderTask();
      }
    ));
    effects.appendChild(createStepNumberInput(
      "预计执行时长（秒）",
      step.linkcraft_action?.duration_sec || 30,
      (value) => { state.task.steps[index].linkcraft_action.duration_sec = value; },
      !step.linkcraft_action
    ));
    content.appendChild(effects);
    const controls = document.createElement("div");
    controls.className = "actions";
    const toggle = document.createElement("button");
    toggle.className = "secondary";
    toggle.textContent = step.enabled === false ? "启用" : "禁用";
    toggle.onclick = () => {
      state.task.steps[index].enabled = step.enabled === false;
      renderTask();
    };
    controls.appendChild(toggle);
    [["上移", -1], ["下移", 1]].forEach(([text, delta]) => {
      const button = document.createElement("button");
      button.className = "secondary";
      button.textContent = text;
      button.onclick = () => moveStep(index, delta);
      controls.appendChild(button);
    });
    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = "移除";
    remove.onclick = () => removeStep(index);
    controls.appendChild(remove);
    item.appendChild(content);
    item.appendChild(controls);
    $("taskSteps").appendChild(item);
  });
}

function renderTaskSelector() {
  const select = $("taskSelect");
  if (!select) return;
  select.innerHTML = "";
  (state.tasks.length ? state.tasks : [state.task]).forEach((task) => {
    const option = document.createElement("option");
    option.value = task.id;
    option.textContent = task.name || task.id;
    select.appendChild(option);
  });
  select.value = state.task.id || "";
}

async function loadConfig(force = false) {
  state.config = await api("/api/robots/default/config", { force });
  renderConfig();
}

function renderPathSafetyStatus(message, stateName = "") {
  const status = $("pathSafetyStatus");
  if (!status) return;
  status.textContent = message;
  status.dataset.state = stateName;
}

async function loadPathSafety() {
  const payload = await api("/api/robots/default/navigation/path-safety", { force: true });
  const safety = payload.path_safety || {};
  state.pathSafety = {
    robot_radius_m: safety.robot_radius_m ?? null,
    extra_clearance_m: safety.extra_clearance_m ?? 0.1
  };
  state.pathSafetyLoaded = true;
  $("robotRadius").value = state.pathSafety.robot_radius_m ?? "";
  $("extraClearance").value = state.pathSafety.extra_clearance_m;
  if (state.pathSafety.robot_radius_m == null) {
    renderPathSafetyStatus("尚未填写实测半径；R16 将拒绝下发 task_type=2、pnc_mode=0 全局导航。", "error");
  } else {
    renderPathSafetyStatus(
      `已读取配置：半径 ${state.pathSafety.robot_radius_m} 米，额外净空 ${state.pathSafety.extra_clearance_m} 米。R16 不提供桥接应用回执。`,
      "ok"
    );
  }
  return state.pathSafety;
}

async function savePathSafety() {
  const radius = $("robotRadius");
  const clearance = $("extraClearance");
  if (!state.pathSafetyLoaded) {
    renderPathSafetyStatus("尚未成功读取机器人当前配置，请刷新页面后再保存，避免覆盖已有净空值。", "error");
    return;
  }
  if (radius.value.trim() === "") {
    renderPathSafetyStatus("必须先填写机器人实测外接半径，不能用估算默认值代替。", "error");
    radius.focus();
    return;
  }
  if (!radius.reportValidity()) return;
  if (!clearance.reportValidity()) return;
  const payload = await api("/api/robots/default/navigation/path-safety", {
    method: "POST",
    body: JSON.stringify({
      robot_radius_m: Number(radius.value),
      extra_clearance_m: Number(clearance.value)
    })
  });
  const safety = payload.path_safety || {};
  state.pathSafety = {
    robot_radius_m: safety.robot_radius_m,
    extra_clearance_m: safety.extra_clearance_m
  };
  radius.value = state.pathSafety.robot_radius_m;
  clearance.value = state.pathSafety.extra_clearance_m;
  renderPathSafetyStatus(
    `已写入机器人全局配置：半径 ${state.pathSafety.robot_radius_m} 米，额外净空 ${state.pathSafety.extra_clearance_m} 米。R16 不提供桥接应用回执。`,
    "ok"
  );
}

function applyStartup(payload) {
  state.startup = payload;
  if (state.startup?.robot) {
    state.status = state.startup.robot;
    const statusMaps = state.status?.maps;
    const maps = statusMaps?.maps || statusMaps?.map_names || statusMaps?.items;
    if (Array.isArray(maps)) applyMapList(maps, { notify: state.maps.length > 0 });
    renderStatus();
  }
  if (state.startup?.relocalization) {
    state.relocalization = state.startup.relocalization;
    renderRelocalization();
  }
  renderStartup();
}

async function refreshStartup(force = false) {
  const payload = await api("/api/robots/default/startup", { force });
  applyStartup(payload);
  return payload;
}

async function refreshSystemHealth() {
  state.systemHealth = await api("/api/robots/default/system/health");
  renderSystemHealth();
}

async function refreshRelocalization() {
  state.relocalization = await api("/api/robots/default/relocalization/status");
  renderRelocalization();
}

async function refreshLocalization() {
  state.localization = await api("/api/robots/default/localization/current");
  renderLocalization();
  if (!state.localization.ok) {
    throw new Error(state.localization.message || "暂未收到当前定位数据");
  }
}

function mapNameOf(map) {
  return String(map?.map_name || map?.name || map || "");
}

function applyMapList(maps, { notify = false } = {}) {
  const previousNames = new Set(state.maps.map(mapNameOf));
  state.maps = [...maps].sort((left, right) => {
    const leftName = mapNameOf(left);
    const rightName = mapNameOf(right);
    const numericOrder = Number(rightName) - Number(leftName);
    return Number.isFinite(numericOrder) && numericOrder !== 0
      ? numericOrder
      : rightName.localeCompare(leftName, "zh-CN");
  });

  const names = state.maps.map(mapNameOf).filter(Boolean);
  const activeMap = String(state.status?.nav_status?.active_map_name || "");
  if (!state.currentMap || !names.includes(state.currentMap)) {
    state.currentMap = names.includes(activeMap) ? activeMap : (names[0] || "");
  }
  renderMaps();

  const discovered = names.filter((name) => !previousNames.has(name));
  if (notify && previousNames.size && discovered.length) {
    toast(`发现新地图：${discovered.join("、")}`);
  }
  return discovered;
}

async function refreshMapList({ loadCurrent = false, notify = false, force = false } = {}) {
  if (mapListRefreshInFlight) return [];
  mapListRefreshInFlight = true;
  const button = $("refreshMapsBtn");
  const oldText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "正在刷新";
  }
  try {
    const payload = await api(`/api/robots/default/maps${force ? "?refresh=1" : ""}`, { force });
    const maps = payload.maps || payload.map_names || payload.items || [];
    const discovered = applyMapList(maps, { notify });
    if (loadCurrent && state.currentMap) await loadMap(state.currentMap, { force });
    return discovered;
  } finally {
    mapListRefreshInFlight = false;
    if (button) {
      button.disabled = false;
      button.textContent = oldText || "刷新地图";
    }
  }
}

async function loadMaps() {
  if (state.maps.length) {
    applyMapList(state.maps);
    if (state.currentMap) await loadMap(state.currentMap);
    return;
  }
  await refreshMapList({ loadCurrent: true });
}

async function loadLinkcraftResources(force = false) {
  const payload = await api(
    `/api/robots/default/linkcraft/resources${force ? "?refresh=1" : ""}`,
    { force }
  );
  state.linkcraftResources = Array.isArray(payload.resources) ? payload.resources : [];
  renderTask();
  return state.linkcraftResources;
}

async function loadMap(mapName, { force = false } = {}) {
  const panel = $("mapConfigPanel");
  const select = $("mapSelect");
  panel?.classList.add("is-loading");
  panel?.setAttribute("aria-busy", "true");
  if (select) select.disabled = true;
  try {
    state.currentMap = mapName;
    state.mapPayload = await api(`/api/robots/default/maps/${encodeURIComponent(mapName)}${force ? "?refresh=1" : ""}`, { force });
    state.currentMapInfo = state.mapPayload.map || null;
    state.points = (state.mapPayload.points || []).map(normalizePoint);
    loadTasksFromMapPayload(state.mapPayload);
    state.targetPixel = null;
    state.headingPixel = null;
    state.awaitingHeading = false;
    setCurrentYaw(0);
    renderMaps();
    fillRelocalizationDefaults();
    renderMapPayload();
    fillPointForm(state.points[0] || null);
  } finally {
    panel?.classList.remove("is-loading");
    panel?.removeAttribute("aria-busy");
    if (select) select.disabled = false;
  }
}

async function runAction(successMessage, fn) {
  try {
    await fn();
    toast(successMessage);
    scheduleDashboardRefresh();
  } catch (error) {
    toast(error.message);
  }
}

function scheduleDashboardRefresh(delay = 350) {
  clearTimeout(dashboardRefreshTimer);
  dashboardRefreshTimer = setTimeout(() => {
    dashboardRefreshTimer = null;
    void refreshDashboard(true);
  }, delay);
}

async function startJavaAgent() {
  return api("/api/robots/default/java-agent/start", { method: "POST" });
}

async function startAllServices() {
  const button = $("startAllServicesBtn");
  const oldText = button?.textContent || "一键启动全部服务";
  if (button) {
    button.disabled = true;
    button.textContent = "正在启动，请稍等...";
  }
  startAllInFlight = true;
  const startedAt = Date.now();
  const progressTimer = setInterval(() => {
    if (button) button.textContent = `正在启动 ${Math.floor((Date.now() - startedAt) / 1000)} 秒`;
  }, 1000);
  try {
    renderStartAllResult(null, true);
    const result = await api("/api/robots/default/start-all", { method: "POST", timeoutMs: 120000 });
    if (result.startup) applyStartup(result.startup);
    else await refreshStartup(true);
    renderStartAllResult(result);
    if (!result.ok) {
      const failed = (result.steps || []).filter((step) => !step.ok).map((step) => step.label).join("、");
      throw new Error(failed ? `部分服务未启动：${failed}` : "部分服务未启动，请查看启动检查");
    }
    return result;
  } finally {
    clearInterval(progressTimer);
    startAllInFlight = false;
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
    }
  }
}

async function refreshDashboard(force = false) {
  if (dashboardRefreshInFlight || startAllInFlight) return;
  dashboardRefreshInFlight = true;
  try {
    await refreshStartup(force);
    renderCustomerDashboard();
  } catch {
    // A later polling cycle retries transient tunnel or robot failures.
  } finally {
    dashboardRefreshInFlight = false;
  }
}

async function setActiveMap() {
  if (!state.currentMap) {
    toast("请先选择地图");
    return;
  }
  const navStatus = state.status?.nav_status?.navigation?.status || "idle";
  if (navStatus !== "idle") {
    toast("机器人正在导航，请先停止导航后再切换地图");
    return;
  }
  await api("/api/robots/default/nav/map", {
    method: "POST",
    body: JSON.stringify({ active_map_name: state.currentMap })
  });
  await Promise.all([
    refreshStartup(true),
    loadMap(state.currentMap, { force: true })
  ]);
}

function moveStep(index, delta) {
  const steps = [...(state.task.steps || [])];
  const next = index + delta;
  if (next < 0 || next >= steps.length) return;
  const [step] = steps.splice(index, 1);
  steps.splice(next, 0, step);
  state.task.steps = steps;
  renderTask();
}

function removeStep(index) {
  state.task.steps = [...(state.task.steps || [])];
  state.task.steps.splice(index, 1);
  renderTask();
}

function bindEvents() {
  document.querySelectorAll(".workspace-tab").forEach((tab) => {
    tab.onclick = () => switchWorkspace(tab.dataset.view, { scroll: true });
  });
  let initialWorkspace = "nav";
  const requestedWorkspace = new URLSearchParams(window.location.search).get("workspace");
  try { initialWorkspace = requestedWorkspace || sessionStorage.getItem("lingxi-workspace") || "nav"; } catch {
    initialWorkspace = requestedWorkspace || "nav";
  }
  switchWorkspace(initialWorkspace);
  $("mapCanvas").onclick = (event) => handleMapPointer(event);
  $("mapCanvas").oncontextmenu = (event) => {
    event.preventDefault();
    if (!state.targetPixel) {
      toast("请先左键选择点位位置");
      return;
    }
    handleMapPointer(event, true);
  };
  $("refreshBtn").onclick = () => runAction("状态与地图已更新", async () => {
    await Promise.all([
      loadConfig(true),
      refreshStartup(true),
      refreshMapList({ force: true, notify: true })
    ]);
  });
  if ($("startAllServicesBtn")) {
    $("startAllServicesBtn").onclick = () => runAction("所有服务已启动，可以开始使用机器人", () => startAllServices());
  }
  if ($("customerRefreshBtn")) {
    $("customerRefreshBtn").onclick = () => runAction("状态已刷新", async () => {
      await Promise.all([loadConfig(true), refreshStartup(true)]);
    });
  }
  $("startupRefreshBtn").onclick = () => runAction("启动状态已检查", () => refreshStartup());
  $("refreshMappingBtn").onclick = () => runAction("传感器状态已刷新", () => refreshMappingHealth());
  $("checkSystemHealthBtn").onclick = () => runAction("底层服务已检查", () => refreshSystemHealth());
  $("restartSystemBtn").onclick = () => runAction("底层导航/传感器重启命令已提交", async () => {
    const result = await api("/api/robots/default/system/restart", { method: "POST" });
    state.systemHealth = result.health;
    renderSystemHealth();
  });
  $("recoverLocalizationBtn").onclick = () => runAction("定位链路恢复流程已完成，请查看检查结果", async () => {
    const result = await api("/api/robots/default/system/recover-localization", { method: "POST" });
    state.systemHealth = result.after || result.health || result.before;
    renderSystemHealth();
    await refreshRelocalization();
  });
  $("startupBridgeBtn").onclick = () => runAction("机器人导航层启动命令已提交", async () => {
    await api("/api/robots/default/bridge/start", { method: "POST" });
    await refreshStartup();
  });
  $("startupRelocBtn").onclick = () => runAction("重定位已启动，请等待状态刷新", async () => {
    await startAutoRelocalization();
    await refreshStartup();
  });
  $("startupJavaBtn").onclick = () => runAction("语音服务启动命令已提交", async () => {
    await startJavaAgent();
    await refreshStartup();
  });
  $("startBridgeBtn").onclick = () => runAction("启动命令已提交", () => api("/api/robots/default/bridge/start", { method: "POST" }));
  $("restartBridgeBtn").onclick = () => runAction("重启命令已提交", () => api("/api/robots/default/bridge/restart", { method: "POST" }));
  $("stopNavBtn").onclick = () => runAction("停止导航已提交", () => api("/api/robots/default/nav/stop", { method: "POST" }));
  $("startTaskBtn").onclick = () => runAction("讲解任务已启动", () => api("/api/robots/default/task/start", { method: "POST" }));
  $("pauseTaskBtn").onclick = () => runAction("讲解任务已暂停", () => api("/api/robots/default/task/pause", { method: "POST" }));
  $("resumeTaskBtn").onclick = () => runAction("讲解任务已继续", () => api("/api/robots/default/task/resume", { method: "POST" }));
  $("stopTaskBtn").onclick = () => runAction("讲解任务已结束", () => api("/api/robots/default/task/stop", { method: "POST" }));
  $("refreshRelocBtn").onclick = () => runAction("重定位状态已刷新", () => refreshRelocalization());
  $("getLocalizationBtn").onclick = () => runAction("当前定位已获取", () => refreshLocalization());
  $("fillAutoRelocBtn").onclick = () => runAction("已按点位和修正量填入重定位参数", () => {
    fillRelocalizationFromSavedPoint();
  });
  $("startAutoRelocBtn").onclick = () => runAction("自动重定位已启动，请等待状态刷新", () => startAutoRelocalization());
  $("fillRelocFromPointBtn").onclick = () => runAction("已填入当前选点坐标", () => {
    fillRelocalizationFromSelectedPoint();
  });
  $("startRelocBtn").onclick = () => runAction("重定位已启动，请等待状态刷新", () => startRelocalizationFromForm());
  $("mapSelect").onchange = (event) => runAction("地图已载入", () => loadMap(event.target.value));
  $("refreshMapsBtn").onclick = () => runAction("地图列表已刷新", () => refreshMapList({ force: true, notify: true }));
  $("refreshLinkcraftBtn").onclick = () => runAction("灵创舞蹈和动作列表已刷新", () => loadLinkcraftResources(true));
  $("setActiveMapBtn").onclick = () => runAction("已切换为当前导航地图", () => setActiveMap());
  $("pathSafetyForm").onsubmit = (event) => {
    event.preventDefault();
    void runAction("全局路径安全参数已保存", () => savePathSafety());
  };
  $("newPointBtn").onclick = () => {
    fillPointForm(normalizePoint({
      name: "",
      aliases: [],
      is_start: false,
      pose: { position: { x: 0, y: 0, z: 0 }, orientation: { z: 0, w: 1 } },
      navigation: { target_pose_radius: 0.15, max_forward_speed: 0.3, pnc_mode: 0, task_type: 2 },
      business: { on_arrival: [] }
    }));
    state.targetPixel = null;
    state.headingPixel = null;
    state.awaitingHeading = false;
    $("pointX").value = "";
    $("pointY").value = "";
    drawMap();
    toast("请在地图上点击新增点位的位置");
  };
  $("resetYawBtn").onclick = () => {
    state.headingPixel = null;
    state.awaitingHeading = Boolean(state.targetPixel);
    setCurrentYaw(0);
    drawMap();
    toast("朝向已重置为 0°");
  };
  $("pointForm").onsubmit = async (event) => {
    event.preventDefault();
    const point = pointFromForm();
    if (!point.name) return toast("请填写点位名称");
    if (!state.currentMapInfo) return toast("请先选择地图");
    if (!state.targetPixel) return toast("请先在地图上选择点位位置");
    await api(`/api/robots/default/maps/${encodeURIComponent(state.currentMap)}/points`, {
      method: "POST",
      body: JSON.stringify(point)
    });
    toast("点位已保存");
    await loadMap(state.currentMap, { force: true });
  };
  $("deletePointBtn").onclick = async () => {
    if (!state.selectedPoint?.id) return;
    await api(`/api/robots/default/maps/${encodeURIComponent(state.currentMap)}/points/${encodeURIComponent(state.selectedPoint.id)}`, {
      method: "DELETE"
    });
    toast("点位已删除");
    await loadMap(state.currentMap, { force: true });
  };
  $("addTaskStepBtn").onclick = () => {
    const pointId = $("taskPointSelect").value;
    if (!pointId) return;
    const speechText = $("taskSpeechText").value.trim();
    state.task.steps = [...(state.task.steps || []), { point_id: pointId, enabled: true, speech_text: speechText }];
    $("taskSpeechText").value = "";
    renderTask();
  };
  $("taskSelect").onchange = () => {
    const task = state.tasks.find((item) => item.id === $("taskSelect").value);
    if (!task) return;
    state.task = task;
    renderTask();
  };
  $("newTaskBtn").onclick = () => {
    const task = {
      id: `task_${Date.now()}`,
      name: "新讲解任务",
      enabled: true,
      steps: []
    };
    state.tasks = [...state.tasks, task];
    state.task = task;
    renderTask();
  };
  $("saveTaskBtn").onclick = async () => {
    state.task.name = $("taskName").value.trim() || "默认讲解任务";
    state.task.id = state.task.id || `task_${Date.now()}`;
    state.tasks = state.tasks.map((task) => task.id === state.task.id ? state.task : task);
    if (!state.tasks.some((task) => task.id === state.task.id)) state.tasks.push(state.task);
    await api(`/api/robots/default/maps/${encodeURIComponent(state.currentMap)}/task`, {
      method: "POST",
      body: JSON.stringify({
        id: state.task.id,
        name: state.task.name,
        enabled: true,
        steps: (state.task.steps || []).map(taskStepForSave)
      })
    });
    toast("任务已保存并设为当前任务");
    await loadMap(state.currentMap, { force: true });
  };
}

async function main() {
  populatePointEffectOptions();
  bindEvents();
  try {
    await Promise.all([loadConfig(), refreshStartup()]);
    try {
      await loadPathSafety();
    } catch (error) {
      renderPathSafetyStatus(`读取机器人全局安全配置失败：${error.message}`, "error");
    }
    renderSystemHealth();
    renderLocalization();
    try {
      await loadLinkcraftResources();
    } catch (error) {
      state.linkcraftResources = [];
      toast(`灵创资源暂未读取：${error.message}`);
    }
    await loadMaps();
  } catch (error) {
    toast(error.message);
  }
  setInterval(() => {
    if (!document.hidden) void refreshDashboard();
  }, 15000);
  window.addEventListener("focus", () => scheduleDashboardRefresh(100));
}

main();
