import { useCallback, useEffect, useMemo, useState } from "react";
import { Capacitor } from "@capacitor/core";
import { ScreenOrientation } from "@capacitor/screen-orientation";
import {
  Activity,
  ArrowUp,
  ArrowDown,
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  CircleHelp,
  Compass,
  DoorOpen,
  Gauge,
  Map,
  MapPinned,
  Mic2,
  MoreHorizontal,
  Navigation,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  ShieldCheck,
  Square,
  Play,
  Pause,
  Trash2,
  Waypoints,
  X,
} from "lucide-react";
import type {
  MapPoint,
  OccupancyGrid,
  PublicSettings,
  RobotState,
  TourStep,
  TourTask,
} from "../../shared/contracts";
import { api, ApiError, jsonBody, openEvents, pairRobot, type Connection } from "./api";
import MapCanvas from "./MapCanvas";
import LidarCanvas from "./LidarCanvas";

type View = "overview" | "mapping" | "maps" | "navigation" | "tours" | "settings";

const navigationItems: { id: View; label: string; icon: typeof Map }[] = [
  { id: "overview", label: "运行总览", icon: Gauge },
  { id: "mapping", label: "实时建图", icon: Radio },
  { id: "maps", label: "地图管理", icon: Map },
  { id: "navigation", label: "导航管理", icon: Navigation },
  { id: "tours", label: "讲解管理", icon: Waypoints },
  { id: "settings", label: "设置", icon: Settings },
];

function demoState(): RobotState {
  return {
    connected: true,
    demoMode: true,
    robotName: "Lingxi X2",
    checks: [
      { id: "aimdk", label: "AIMDK", ready: true, detail: "v1.1.0" },
      { id: "slam", label: "SLAM", ready: true, detail: "建图接口在线" },
      { id: "lidar", label: "激光雷达", ready: true, detail: "10 Hz" },
      { id: "odom", label: "里程计", ready: true, detail: "实时" },
      { id: "navigation", label: "原生导航", ready: true, detail: "就绪" },
      { id: "linksoul", label: "灵心", ready: false, detail: "未连接" },
    ],
    mapping: false,
    activeMapId: "map-hall",
    pose: { x: 3.1, y: 2.4, yaw: 0.3 },
    navigation: { status: "idle" },
    maps: [
      { id: "map-hall", name: "一层展厅", mapId: 1001, active: true, createdAt: "2026-10-24" },
      { id: "map-lab", name: "体验区", mapId: 1002, active: false, createdAt: "2026-10-21" },
    ],
    points: [
      { id: "point-1", mapId: "map-hall", name: "入口", x: 1.4, y: 1.2, yaw: 0, isStart: true },
      { id: "point-2", mapId: "map-hall", name: "灵犀展台", x: 3.4, y: 2.2, yaw: 1.1, isStart: false },
      { id: "point-3", mapId: "map-hall", name: "互动区", x: 5.8, y: 3.7, yaw: 2.7, isStart: false },
    ],
    tours: [
      {
        id: "tour-1",
        name: "展厅标准讲解",
        mapId: "map-hall",
        repeatCount: 1,
        idleReturnSeconds: 30,
        updatedAt: "2026-10-25 14:12",
        steps: [
          { id: "step-1", pointId: "point-2", waitSeconds: 3, narration: "欢迎来到灵犀展台。", expression: "微笑" },
          { id: "step-2", pointId: "point-3", waitSeconds: 5, narration: "这里是互动体验区。", presetMotion: "挥手" },
        ],
      },
    ],
    selectedTourId: "tour-1",
    motionPresets: ["挥手", "点头", "比心"],
    expressions: ["微笑", "惊喜", "平静"],
    linkcraftResources: [],
    updatedAt: new Date().toISOString(),
  };
}

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 10_000);
    return () => window.clearInterval(timer);
  }, []);
  return now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function ConnectScreen({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const isDev = import.meta.env.DEV;
  const initialAddress =
    localStorage.getItem("lingxi.robotAddress") ??
    (isDev ? "http://127.0.0.1:3010" : window.location.hostname === "localhost" ? "" : window.location.origin);
  const [address, setAddress] = useState(initialAddress);
  const [pairingCode, setPairingCode] = useState(isDev ? "lingxi-dev-only" : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const connection = await pairRobot(address, pairingCode);
      setPairingCode("");
      onConnected(connection);
    } catch (caught) {
      setError(
        caught instanceof TypeError
          ? "无法连接。请确认机器人地址、同一局域网和 HTTPS 证书信任。"
          : (caught as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="connect-screen">
      <section className="connect-brand">
        <div>
          <div className="brand">
            <div className="brand-mark"><Bot size={19} /></div>
            <div>
              <div className="brand-title">灵犀 X2</div>
              <div className="brand-subtitle">MAPPING · NAVIGATION · GUIDE</div>
            </div>
          </div>
          <h1>建图与导航<br />工作台</h1>
          <p>机器人本机运行 · 同一局域网连接</p>
        </div>
          <span className="brand-subtitle">LINGXI NEXT / 0.1.8</span>
      </section>
      <section className="connect-main">
        <form className="connect-form" onSubmit={connect}>
          <div className="eyebrow">连接机器人</div>
          <h2>输入机器人地址</h2>
          <p>{isDev ? "本地演示模式，不会连接或控制真实机器人。" : "请在同一局域网输入机器人 IP 和端口。"}</p>
          <div className="connect-fields">
            <div className="field">
              <label htmlFor="robot-address">机器人地址</label>
              <input
                id="robot-address"
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                placeholder="https://192.168.1.20:3000"
                autoCapitalize="off"
                autoCorrect="off"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="pairing-code">配对码</label>
              <input
                id="pairing-code"
                value={pairingCode}
                onChange={(event) => setPairingCode(event.target.value)}
                type="password"
                autoComplete="off"
                placeholder="机器人部署时生成"
                required
              />
              <span className="helper">配对码仅用于建立会话，不会保存在此设备。</span>
            </div>
            {error && <div className="notice error"><AlertTriangle size={14} />{error}</div>}
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? <RefreshCw size={14} className="spin" /> : <Radio size={14} />}
              {busy ? "正在连接" : "连接机器人"}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}

function StatusDot({ ready }: { ready: boolean }) {
  return <span className={`status-dot ${ready ? "ok" : "warn"}`} />;
}

function PageHeading({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="page-heading">
      <div><h1>{title}</h1><p>{description}</p></div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
  actions,
}: {
  title: string;
  icon?: typeof Map;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-head">
        <div className="section-title">{Icon && <Icon size={15} />}<h2>{title}</h2></div>
        {actions}
      </div>
      <div className="section-body">{children}</div>
    </section>
  );
}

function CheckList({ checks }: { checks: RobotState["checks"] }) {
  return (
    <div className="check-list">
      {checks.map((check) => (
        <div className="check-row" key={check.id}>
          <span className={`check-icon ${check.ready ? "" : "off"}`}>
            {check.ready ? <Check size={12} /> : <X size={12} />}
          </span>
          <strong>{check.label}</strong>
          <span className="check-detail">{check.detail ?? (check.ready ? "在线" : "待检查")}</span>
        </div>
      ))}
    </div>
  );
}

function Overview({
  state,
  setView,
  onRefresh,
}: {
  state: RobotState;
  setView: (view: View) => void;
  onRefresh: () => void;
}) {
  const activeMap = state.maps.find((map) => map.id === state.activeMapId);
  const activePoints = state.points.filter((point) => point.mapId === state.activeMapId);
  const readyCount = state.checks.filter((check) => check.ready).length;
  return (
    <div className="page">
      <PageHeading
        title="运行总览"
        description={`${state.robotName} · ${activeMap?.name ?? "未选择地图"}`}
        actions={<button className="btn" onClick={onRefresh}><RefreshCw size={13} />刷新状态</button>}
      />
      <div className="content-grid three">
        <div className="section metric">
          <div className="metric-label"><Activity size={14} />系统健康</div>
          <div className="metric-value">{readyCount}<span className="muted"> / {state.checks.length}</span></div>
          <div className="metric-note">核心接口已就绪</div>
        </div>
        <div className="section metric">
          <div className="metric-label"><MapPinned size={14} />地图与点位</div>
          <div className="metric-value">{state.maps.length}<span className="muted"> 张地图 · </span>{state.points.length}<span className="muted"> 个点位</span></div>
          <div className="metric-note">当前地图：{activeMap?.name ?? "未选择"}</div>
        </div>
        <div className="section metric">
          <div className="metric-label"><Navigation size={14} />导航状态</div>
          <div className="metric-value">{navigationLabel(state.navigation.status)}</div>
          <div className="metric-note">{state.navigation.detail ?? "无活动导航任务"}</div>
        </div>
      </div>
      <div className="content-grid two" style={{ marginTop: 14 }}>
        <Section title="实时地图" icon={Map} actions={<button className="btn small" onClick={() => setView("mapping")}>打开建图</button>}>
          <MapCanvas grid={state.mapGrid} pose={state.pose} points={activePoints} activeMapName={state.activeMapId} />
          <div className="inline" style={{ marginTop: 12 }}>
            <span className="tag"><StatusDot ready={Boolean(state.activeMapId)} />{activeMap?.name ?? "未加载地图"}</span>
            <span className="tag"><StatusDot ready={Boolean(state.pose)} />定位 {state.pose ? "正常" : "等待"}</span>
            <span className="spacer" />
            <button className="btn small" onClick={() => setView("maps")}>地图管理</button>
          </div>
        </Section>
        <div className="stack">
          <Section title="设备接口" icon={ShieldCheck}><CheckList checks={state.checks} /></Section>
          <Section title="当前讲解任务" icon={Waypoints} actions={<button className="btn small" onClick={() => setView("tours")}>管理</button>}>
            {state.tours.length ? (
              <div className="stack">
                <strong style={{ fontSize: 13 }}>{state.tours.find((task) => task.id === state.selectedTourId)?.name ?? state.tours[0].name}</strong>
                <span className="helper">{state.tours.find((task) => task.id === state.selectedTourId)?.steps.length ?? 0} 个讲解点 · {state.tours.find((task) => task.id === state.selectedTourId)?.repeatCount ?? 1} 轮</span>
                <button className="btn primary" onClick={() => setView("tours")}><Play size={13} />打开任务</button>
              </div>
            ) : <div className="empty">尚未创建讲解任务</div>}
          </Section>
          <Section title="语音链路" icon={Mic2}>
            <div className="stack">
              <div className="inline">
                <strong style={{ fontSize: 20 }}>
                  {state.voiceDiagnostics?.recognitionToFirstAudioP95Ms === undefined
                    ? "暂无数据"
                    : `${state.voiceDiagnostics.recognitionToFirstAudioP95Ms} ms`}
                </strong>
                <span className="helper">成功播报样本首音 P95</span>
              </div>
              <span className="helper">
                最近 {state.voiceDiagnostics?.sampleCount ?? 0} 次 · 有首音 {state.voiceDiagnostics?.audibleSampleCount ?? 0} 次
              </span>
              <span className="helper">
                {state.voiceDiagnostics?.latest
                  ? `最近结果：${state.voiceDiagnostics.latest.category} · ${state.voiceDiagnostics.latest.speechDelivered ? "已播报" : "未播报"}`
                  : "等待语音事件"}
              </span>
              <span className="helper">不含失败样本与 ASR 上游时间，不作为 VAD 结束至首音验收值</span>
            </div>
          </Section>
          <div className="notice"><CircleHelp size={14} />移动机器人请始终使用外置遥控器。网页和 App 不发送行走命令。</div>
        </div>
      </div>
    </div>
  );
}

function Mapping({
  state,
  run,
  connection,
}: {
  state: RobotState;
  run: (path: string, body?: unknown, method?: string) => Promise<unknown>;
  connection: Connection;
}) {
  const [mapName, setMapName] = useState("");
  const [busy, setBusy] = useState(false);
  const activeMap = state.maps.find((map) => map.id === state.activeMapId);
  const canStart = !navigationBusy(state) &&
    !state.actionBusy &&
    ["aimdk", "slam", "lidar"].every((id) => state.checks.some((item) => item.id === id && item.ready));
  const mappingLabel = {
    idle: "未启动",
    requested: "等待 AIMDK 确认",
    running: "采集中",
    saving: "等待保存确认",
    failed: "保存未确认",
  }[state.mappingStatus ?? (state.mapping ? "requested" : "idle")];

  async function start() {
    setBusy(true);
    try { await run("/api/v1/mapping/start"); } finally { setBusy(false); }
  }
  async function stopAndSave(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await run("/api/v1/mapping/stop-save", { name: mapName }, "POST");
      setMapName("");
    } finally { setBusy(false); }
  }

  return (
    <div className="page">
      <PageHeading title="实时建图" description="激光雷达实时扫描与 SLAM 建图" />
      <div className="notice warn" style={{ marginBottom: 14 }}>
        <AlertTriangle size={15} />
        <span>移动机器人始终使用外置遥控器。停止建图时 AIMDK 会按地图名保存，并等待地图服务确认结果。</span>
      </div>
      {state.actionStatus === "linkcraft-awaiting-operator" && (
        <div className="notice warn" style={{ marginBottom: 14 }}>
          <AlertTriangle size={15} />
          <span>灵创动作服务已接受请求，但 SDK 不提供播放完成状态。请现场确认动作已停止后解除互锁。</span>
          <button className="btn small" disabled={busy} onClick={() =>
            window.confirm("请确认已在机器人旁观察到动作停止。继续解除动作互锁？") &&
            void run("/api/v1/actions/confirm-idle", { confirmed: true })
          }><Check size={12} />确认动作已停止</button>
        </div>
      )}
      {state.actionStatus === "uncertain" && (
        <div className="notice error" style={{ marginBottom: 14 }}>
          <AlertTriangle size={15} />
          <span>动作状态未确认，建图与导航已互锁。请现场检查并确认动作已停止后解除互锁。</span>
          <button className="btn small" disabled={busy} onClick={() =>
            window.confirm("请确认已在机器人旁检查，机器人动作已完全停止。继续解除动作互锁？") &&
            void run("/api/v1/actions/confirm-idle", { confirmed: true })
          }><Check size={12} />现场确认动作已停止</button>
        </div>
      )}
      <div className="content-grid two">
        <Section
          title="激光雷达实时扫描"
          icon={Radio}
          actions={state.mapping
            ? <span className="tag"><StatusDot ready={state.mappingStatus === "running"} />{mappingLabel}</span>
            : <span className="tag"><StatusDot ready={state.checks.some((check) => check.id === "lidar" && check.ready)} />雷达点迹</span>}
        >
          <LidarCanvas connection={connection} />
          <div className="inline" style={{ marginTop: 12 }}>
            <span className="tag">当前地图 {activeMap?.name ?? "未选择"}</span>
            {state.pose && <span className="tag mono">x {state.pose.x.toFixed(2)} · y {state.pose.y.toFixed(2)}</span>}
          </div>
        </Section>
        <div className="stack">
          <Section title="建图准备检查" icon={ShieldCheck}><CheckList checks={state.checks.filter((item) => ["aimdk", "slam", "lidar", "odom"].includes(item.id))} /></Section>
          <Section title="采集控制" icon={Activity}>
            <div className="stack">
              <div className="helper">建图期间由现场人员使用外置遥控器移动机器人。</div>
              {!state.mapping ? (
                <button className="btn accent" disabled={!canStart || busy} onClick={start}><Play size={13} />开始采集</button>
              ) : (
                <form className="stack" onSubmit={stopAndSave}>
                  <div className="field">
                    <label htmlFor="map-name">保存地图名称</label>
                    <input id="map-name" value={mapName} onChange={(event) => setMapName(event.target.value)} maxLength={64} required placeholder="例如：一层展厅" />
                  </div>
                  <button className="btn danger" disabled={busy || !mapName.trim()} type="submit"><Save size={13} />停止并保存</button>
                </form>
              )}
              {!canStart && !state.mapping && <span className="helper danger-text">接口检查未通过，开始采集已禁用。</span>}
            </div>
          </Section>
          <div className="notice"><CircleHelp size={14} />建图和地图保存通过 AIMDK 本机接口执行；本页面没有底盘遥控能力。</div>
        </div>
      </div>
    </div>
  );
}

function Maps({
  state,
  run,
  refresh,
}: {
  state: RobotState;
  run: (path: string, body?: unknown, method?: string) => Promise<unknown>;
  refresh: () => void;
}) {
  const [selected, setSelected] = useState<MapPoint | null>(null);
  const [mapGrid, setMapGrid] = useState<OccupancyGrid>();
  const [pointName, setPointName] = useState("");
  const [coordinates, setCoordinates] = useState({ x: "0", y: "0", yaw: "0" });
  const activeMap = state.maps.find((map) => map.id === state.activeMapId);

  useEffect(() => {
    if (!activeMap) {
      setMapGrid(undefined);
      return;
    }
    let cancelled = false;
    void run(`/api/v1/maps/${encodeURIComponent(activeMap.id)}/grid`, undefined, "GET")
      .then((grid) => {
        if (!cancelled) setMapGrid(grid as OccupancyGrid);
      })
      .catch(() => {
        if (!cancelled) setMapGrid(undefined);
      });
    return () => { cancelled = true; };
  }, [activeMap?.id, run]);

  async function addPoint(event: React.FormEvent) {
    event.preventDefault();
    if (!activeMap) return;
    await run("/api/v1/points", {
      mapId: activeMap.id,
      name: pointName,
      x: Number(coordinates.x),
      y: Number(coordinates.y),
      yaw: Number(coordinates.yaw),
    });
    setPointName("");
    refresh();
  }

  async function updatePoint(point: MapPoint, patch: Partial<MapPoint>) {
    await run(`/api/v1/points/${encodeURIComponent(point.id)}`, { ...point, ...patch }, "PUT");
    refresh();
  }

  return (
    <div className="page">
      <PageHeading title="地图管理" description="地图切换、点位编辑与起始位姿" actions={<button className="btn" onClick={refresh}><RefreshCw size={13} />刷新</button>} />
      <div className="content-grid two">
        <div className="stack">
          <Section title="地图列表" icon={Map}>
            <table className="table">
              <thead><tr><th>地图名称</th><th>地图 ID</th><th>状态</th><th /></tr></thead>
              <tbody>
                {state.maps.map((map) => (
                  <tr key={map.id}>
                    <td><strong>{map.name}</strong></td>
                    <td className="mono">{map.mapId ?? "未查询"}</td>
                    <td>{map.active ? <span className="tag">当前使用</span> : <span className="muted">可用</span>}</td>
                    <td><button className="btn small" disabled={state.mapping || navigationBusy(state)} onClick={() => run(`/api/v1/maps/${encodeURIComponent(map.id)}/activate`, {})}>从建图起点定位</button></td>
                  </tr>
                ))}
                {!state.maps.length && <tr><td colSpan={4} className="empty">未发现已保存地图</td></tr>}
              </tbody>
            </table>
          </Section>
          <Section title={`点位 · ${activeMap?.name ?? "未选择地图"}`} icon={MapPinned}>
            <table className="table">
              <thead><tr><th>名称</th><th>坐标</th><th>方向</th><th>起点</th><th /></tr></thead>
              <tbody>
                {state.points.filter((point) => point.mapId === activeMap?.id).map((point) => (
                  <tr key={point.id}>
                    <td><strong>{point.name}</strong></td>
                    <td className="mono">{point.x.toFixed(2)}, {point.y.toFixed(2)}</td>
                    <td className="mono">{point.yaw.toFixed(2)} rad</td>
                    <td><input aria-label={`${point.name}设为起点`} type="radio" checked={point.isStart} onChange={() => updatePoint(point, { isStart: true })} /></td>
                    <td className="row-actions">
                      <button className="btn icon small" title="编辑坐标" onClick={() => setSelected(point)}><MoreHorizontal size={14} /></button>
                      <button className="btn icon small danger" title="删除点位" onClick={() => run(`/api/v1/points/${encodeURIComponent(point.id)}`, undefined, "DELETE")}><Trash2 size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <form className="form-grid" style={{ marginTop: 14 }} onSubmit={addPoint}>
              <div className="field"><label htmlFor="new-point-name">新点位名称</label><input id="new-point-name" value={pointName} onChange={(event) => setPointName(event.target.value)} required /></div>
              <div className="field"><label htmlFor="new-point-x">X 坐标（米）</label><input id="new-point-x" type="number" step="0.01" value={coordinates.x} onChange={(event) => setCoordinates({ ...coordinates, x: event.target.value })} required /></div>
              <div className="field"><label htmlFor="new-point-y">Y 坐标（米）</label><input id="new-point-y" type="number" step="0.01" value={coordinates.y} onChange={(event) => setCoordinates({ ...coordinates, y: event.target.value })} required /></div>
              <div className="field"><label htmlFor="new-point-yaw">朝向（弧度）</label><input id="new-point-yaw" type="number" step="0.01" value={coordinates.yaw} onChange={(event) => setCoordinates({ ...coordinates, yaw: event.target.value })} required /></div>
              <div className="field full"><button className="btn primary" type="submit" disabled={!activeMap}><Plus size={13} />添加点位</button></div>
            </form>
            <div className="inline" style={{ marginTop: 12 }}>
              <button className="btn" disabled={!activeMap || state.mapping || navigationBusy(state)} onClick={() => {
                if (activeMap) void run(`/api/v1/maps/${encodeURIComponent(activeMap.id)}/activate`, {
                  initialPose: { x: Number(coordinates.x), y: Number(coordinates.y), yaw: Number(coordinates.yaw) },
                });
              }}><Compass size={13} />按所填位姿重定位</button>
            </div>
            <div className="helper" style={{ marginTop: 8 }}>定位前确认机器人实际位置与所填坐标、朝向一致。</div>
          </Section>
        </div>
        <Section title="地图与点位" icon={Compass}>
          <MapCanvas
            grid={mapGrid ?? (activeMap?.active ? state.mapGrid : undefined)}
            pose={state.pose}
            points={state.points}
            activeMapName={activeMap?.id}
            onMapClick={activeMap ? (position) => setCoordinates({
              x: position.x.toFixed(2),
              y: position.y.toFixed(2),
              yaw: (state.pose?.yaw ?? 0).toFixed(2),
            }) : undefined}
          />
          <div className="helper" style={{ marginTop: 10 }}>
            {activeMap?.mapId ? `AIMDK map_id ${activeMap.mapId}` : "地图 ID 由 AIMDK 查询确认"}
          </div>
        </Section>
      </div>
      {selected && (
        <PointEditor point={selected} onClose={() => setSelected(null)} onSave={async (patch) => {
          await updatePoint(selected, patch);
          setSelected(null);
        }} />
      )}
    </div>
  );
}

function PointEditor({
  point,
  onClose,
  onSave,
}: {
  point: MapPoint;
  onClose: () => void;
  onSave: (patch: Partial<MapPoint>) => Promise<void>;
}) {
  const [values, setValues] = useState({ name: point.name, x: String(point.x), y: String(point.y), yaw: String(point.yaw) });
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className="modal" role="dialog" aria-modal="true" aria-label="编辑点位" onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }} onSubmit={async (event) => {
        event.preventDefault();
        await onSave({ name: values.name, x: Number(values.x), y: Number(values.y), yaw: Number(values.yaw) });
      }}>
        <div className="section-head"><h2>编辑点位</h2><button className="btn icon" type="button" title="关闭" onClick={onClose}><X size={15} /></button></div>
        <div className="modal-body form-grid">
          <div className="field full"><label htmlFor="edit-point-name">名称</label><input id="edit-point-name" value={values.name} onChange={(event) => setValues({ ...values, name: event.target.value })} required /></div>
          <div className="field"><label htmlFor="edit-point-x">X 坐标（米）</label><input id="edit-point-x" type="number" step="0.01" value={values.x} onChange={(event) => setValues({ ...values, x: event.target.value })} /></div>
          <div className="field"><label htmlFor="edit-point-y">Y 坐标（米）</label><input id="edit-point-y" type="number" step="0.01" value={values.y} onChange={(event) => setValues({ ...values, y: event.target.value })} /></div>
          <div className="field"><label htmlFor="edit-point-yaw">朝向（弧度）</label><input id="edit-point-yaw" type="number" step="0.01" value={values.yaw} onChange={(event) => setValues({ ...values, yaw: event.target.value })} /></div>
        </div>
        <div className="modal-foot"><button className="btn" type="button" onClick={onClose}>取消</button><button className="btn primary" type="submit"><Save size={13} />保存</button></div>
      </form>
    </div>
  );
}

function NavigationPage({
  state,
  run,
}: {
  state: RobotState;
  run: (path: string, body?: unknown, method?: string) => Promise<unknown>;
}) {
  const points = state.points.filter((point) => point.mapId === state.activeMapId);
  const ready = state.checks.find((item) => item.id === "navigation")?.ready;
  return (
    <div className="page">
      <PageHeading title="导航管理" description="基于 X2 原生点到点导航与安全链路" />
      {state.actionBusy && (
        <div className={state.actionStatus === "uncertain" ? "notice error" : "notice warn"} style={{ marginBottom: 14 }}>
          <AlertTriangle size={14} />
          <span>{state.actionStatus === "linkcraft-awaiting-operator"
            ? "灵创动作等待现场确认，导航已互锁。请在实时建图页确认动作停止后解除。"
            : state.actionStatus === "uncertain"
              ? "动作状态未确认，导航已互锁。请现场检查机器人并在实时建图页解除互锁。"
              : "AIMDK 预设动作仍在执行或等待状态确认。"}
          </span>
        </div>
      )}
      <div className="content-grid two">
        <div className="stack">
          <Section title="当前导航" icon={Navigation}>
            <div className="inline">
              <span className={`status-dot ${state.navigation.status === "running" ? "ok" : state.navigation.status === "failed" ? "bad" : ""}`} />
              <strong style={{ fontSize: 17 }}>{navigationLabel(state.navigation.status)}</strong>
              <span className="spacer" />
              <JobControls state={state} run={run} />
            </div>
            {state.navigation.detail && <div className="helper" style={{ marginTop: 9 }}>{state.navigation.detail}</div>}
          </Section>
          <Section title="点位导航" icon={MapPinned}>
            <table className="table">
              <thead><tr><th>点位</th><th>地图</th><th>坐标</th><th /></tr></thead>
              <tbody>
                {points.map((point) => (
                  <tr key={point.id}>
                    <td><strong>{point.name}</strong>{point.isStart && <span className="tag" style={{ marginLeft: 6 }}>起点</span>}</td>
                    <td>{state.maps.find((map) => map.id === point.mapId)?.name}</td>
                    <td className="mono">{point.x.toFixed(2)}, {point.y.toFixed(2)}</td>
                    <td><button className="btn small primary" disabled={!ready || !state.activeMapId || state.mapping || state.actionBusy || navigationBusy(state)} onClick={() => run("/api/v1/navigation/point", { pointId: point.id })}><Navigation size={12} />前往</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!ready && <div className="notice warn" style={{ marginTop: 12 }}><AlertTriangle size={14} />未检测到 X2 原生导航组件，导航命令已禁用。</div>}
          </Section>
        </div>
        <Section title="运行条件" icon={ShieldCheck}>
          <CheckList checks={state.checks.filter((item) => ["navigation", "slam", "odom"].includes(item.id))} />
          <hr className="divider" />
          <div className="field">
            <label htmlFor="idle-return">普通点位导航无交互返航</label>
            <select id="idle-return" value={state.idleReturnSeconds ?? 30} onChange={(event) => run("/api/v1/settings/navigation", { idleReturnSeconds: Number(event.target.value) }, "PUT")}>
              <option value={0}>关闭</option><option value={30}>30 秒</option><option value={60}>60 秒</option><option value={120}>120 秒</option>
            </select>
            <span className="helper">讲解任务结束不自动返航。</span>
          </div>
        </Section>
      </div>
    </div>
  );
}

function newStep(pointId = ""): TourStep {
  return { id: crypto.randomUUID(), pointId, waitSeconds: 0, narration: "" };
}

function Tours({
  state,
  run,
  refresh,
}: {
  state: RobotState;
  run: (path: string, body?: unknown, method?: string) => Promise<unknown>;
  refresh: () => void;
}) {
  const activeMap = state.maps.find((map) => map.id === state.activeMapId);
  const availablePoints = state.points.filter((point) => point.mapId === activeMap?.id);
  const selected = state.tours.find((tour) => tour.id === state.selectedTourId) ?? state.tours[0];
  const [draft, setDraft] = useState<TourTask | null>(null);
  const [error, setError] = useState("");

  useEffect(() => setDraft(selected ? structuredClone(selected) : null), [selected?.id, selected?.updatedAt]);

  function editStep(id: string, patch: Partial<TourStep>) {
    setDraft((current) => current && ({ ...current, steps: current.steps.map((step) => step.id === id ? { ...step, ...patch } : step) }));
  }

  async function saveTask() {
    if (!draft) return;
    setError("");
    try {
      const isExisting = state.tours.some((tour) => tour.id === draft.id);
      await run(isExisting ? `/api/v1/tours/${encodeURIComponent(draft.id)}` : "/api/v1/tours", draft, isExisting ? "PUT" : "POST");
      refresh();
    } catch (caught) {
      setError((caught as Error).message);
    }
  }

  async function createTask() {
    if (!activeMap) return;
    const task: TourTask = {
      id: crypto.randomUUID(),
      name: "新建讲解任务",
      mapId: activeMap.id,
      repeatCount: 1,
      idleReturnSeconds: 30,
      updatedAt: new Date().toISOString(),
      steps: availablePoints[0] ? [newStep(availablePoints[0].id)] : [],
    };
    await run("/api/v1/tours", task);
    refresh();
  }

  return (
    <div className="page">
      <PageHeading
        title="讲解管理"
        description="多任务、多点顺序编排与讲解效果设置"
        actions={<button className="btn primary" disabled={!activeMap || !availablePoints.length} onClick={createTask}><Plus size={13} />新建任务</button>}
      />
      <div className="content-grid two">
        <div className="stack">
          <Section title="讲解任务" icon={Waypoints}>
            {state.tours.length ? (
              <table className="table">
                <thead><tr><th>任务名称</th><th>讲解点</th><th>轮数</th><th>更新时间</th><th /></tr></thead>
                <tbody>
                  {state.tours.map((tour) => (
                    <tr key={tour.id}>
                      <td><button className="btn small" onClick={() => run("/api/v1/tours/select", { taskId: tour.id })}>{tour.name}</button></td>
                      <td>{tour.steps.length}</td><td>{tour.repeatCount}</td>
                      <td className="muted">{tour.updatedAt.slice(0, 16).replace("T", " ")}</td>
                      <td className="row-actions">
                        <button className="btn small primary" disabled={tour.requiresReview || state.mapping || navigationBusy(state) || tour.mapId !== state.activeMapId} title="开始讲解" onClick={() => run(`/api/v1/tours/${encodeURIComponent(tour.id)}/start`)}><Play size={12} />开始</button>
                        <button className="btn icon small danger" title="删除任务" onClick={() => run(`/api/v1/tours/${encodeURIComponent(tour.id)}`, undefined, "DELETE")}><Trash2 size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="empty">尚未创建任务</div>}
            {["requested", "running", "paused", "failed"].includes(state.navigation.status) && (
              <div className="task-controls" style={{ marginTop: 13 }}>
                <button className="btn small" onClick={() => run("/api/v1/navigation/control", { action: "pause" })}><Pause size={12} />暂停</button>
                <button className="btn small" onClick={() => run("/api/v1/navigation/control", { action: "resume" })}><Play size={12} />继续</button>
                <button className="btn small danger" onClick={() => run("/api/v1/navigation/control", { action: "stop" })}><Square size={12} />停止</button>
              </div>
            )}
          </Section>
          {draft && (
            <Section title="任务编排" icon={Waypoints} actions={<button className="btn small primary" onClick={saveTask}><Save size={12} />保存</button>}>
              {draft.requiresReview && <div className="notice warn">迁移任务待核对。请逐项检查点位、动作、表情与资源后确认。<button className="btn small" onClick={() => setDraft({ ...draft, requiresReview: false })}><Check size={13} />已核对</button></div>}
              <div className="form-grid">
                <div className="field"><label htmlFor="tour-name">任务名称</label><input id="tour-name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
                <div className="field"><label htmlFor="tour-map">地图</label><select id="tour-map" value={draft.mapId} onChange={(event) => setDraft({ ...draft, mapId: event.target.value, steps: [] })}>{state.maps.map((map) => <option key={map.id} value={map.id}>{map.name}</option>)}</select></div>
                <div className="field"><label htmlFor="tour-repeat">循环轮数（1–20）</label><input id="tour-repeat" type="number" min={1} max={20} value={draft.repeatCount} onChange={(event) => setDraft({ ...draft, repeatCount: Number(event.target.value) })} /></div>
                <div className="notice">讲解任务结束后原地待命。普通导航返航时间在导航管理中设置。</div>
              </div>
              {draft.steps.some((step) => step.linkcraftResourceId) && (
                <div className="notice warn" style={{ marginTop: 10 }}>
                  {draft.steps.filter((step) => step.linkcraftResourceId).length !== 1 ||
                    draft.repeatCount !== 1 ||
                    !draft.steps[draft.steps.length - 1]?.linkcraftResourceId
                    ? "灵创资源只能设置一次，并放在单轮讲解的最后一步；AIMDK 不提供播放完成回执。"
                    : "AIMDK 仅确认灵创请求已接受，不提供播放完成状态；该资源将在最后一步讲解后触发。"}
                </div>
              )}
              <hr className="divider" />
              <div className="task-steps">
                {draft.steps.map((step, index) => (
                  <div className="task-step" key={step.id}>
                    <div className="step-index">{String(index + 1).padStart(2, "0")}</div>
                    <div className="field"><label htmlFor={`point-${step.id}`}>点位</label><select id={`point-${step.id}`} value={step.pointId} onChange={(event) => editStep(step.id, { pointId: event.target.value })}><option value="">选择点位</option>{state.points.filter((point) => point.mapId === draft.mapId).map((point) => <option key={point.id} value={point.id}>{point.name}</option>)}</select></div>
                    <div className="field"><label htmlFor={`wait-${step.id}`}>安静等待（秒）</label><input id={`wait-${step.id}`} type="number" min={0} max={300} value={step.waitSeconds} onChange={(event) => editStep(step.id, { waitSeconds: Number(event.target.value) })} /></div>
                    <div className="field"><label htmlFor={`motion-${step.id}`}>预设动作</label><select id={`motion-${step.id}`} value={step.presetMotion ?? ""} onChange={(event) => editStep(step.id, { presetMotion: event.target.value || undefined })}><option value="">不执行</option>{state.motionPresets.map((motion) => <option key={motion}>{motion}</option>)}</select></div>
                    <div className="inline">
                      <button className="btn icon" disabled={index === 0} title="上移讲解点" onClick={() => {
                        const steps = [...draft.steps]; [steps[index - 1], steps[index]] = [steps[index], steps[index - 1]]; setDraft({ ...draft, steps });
                      }}><ArrowUp size={13} /></button>
                      <button className="btn icon" disabled={index === draft.steps.length - 1} title="下移讲解点" onClick={() => {
                        const steps = [...draft.steps]; [steps[index + 1], steps[index]] = [steps[index], steps[index + 1]]; setDraft({ ...draft, steps });
                      }}><ArrowDown size={13} /></button>
                      <button className="btn icon danger" title="移除讲解点" onClick={() => setDraft({ ...draft, steps: draft.steps.filter((item) => item.id !== step.id) })}><Trash2 size={13} /></button>
                    </div>
                    <div className="field"><label htmlFor={`narration-${step.id}`}>讲解内容</label><textarea id={`narration-${step.id}`} value={step.narration} onChange={(event) => editStep(step.id, { narration: event.target.value })} /></div>
                    <div className="field"><label htmlFor={`expression-${step.id}`}>面部表情</label><select id={`expression-${step.id}`} value={step.expression ?? ""} onChange={(event) => editStep(step.id, { expression: event.target.value || undefined })}><option value="">不设置</option>{state.expressions.map((expression) => <option key={expression}>{expression}</option>)}</select></div>
                    <div className="field"><label htmlFor={`resource-${step.id}`}>灵创资源</label><select id={`resource-${step.id}`} value={step.linkcraftResourceId ?? ""} onChange={(event) => editStep(step.id, { linkcraftResourceId: event.target.value || undefined })}><option value="">不设置</option>{state.linkcraftResources.map((resource) => <option key={resource.id} value={resource.id}>{resource.name}</option>)}</select></div>
                  </div>
                ))}
              </div>
              <div className="inline" style={{ marginTop: 12 }}>
                <button className="btn small" onClick={() => setDraft({ ...draft, steps: [...draft.steps, newStep(availablePoints[0]?.id ?? "")] })}><Plus size={12} />添加步骤</button>
                <span className="helper">每点安静等待 0–300 秒</span>
              </div>
              {error && <div className="notice error" style={{ marginTop: 10 }}><AlertTriangle size={13} />{error}</div>}
            </Section>
          )}
        </div>
        <div className="stack">
          <Section title="语音交互" icon={Mic2}>
            <div className="check-row"><span className="check-icon"><Check size={12} /></span><strong>唤醒事件验证</strong><span className="check-detail">真实会话回调</span></div>
            <div className="check-row"><span className="check-icon"><Check size={12} /></span><strong>命令路由</strong><span className="check-detail">高置信白名单</span></div>
            <div className="check-row"><span className="check-icon"><Check size={12} /></span><strong>知识检索</strong><span className="check-detail">{state.checks.find((item) => item.id === "linksoul")?.detail ?? "待检查"}</span></div>
            <div className="notice warn" style={{ marginTop: 12 }}><AlertTriangle size={14} />不清晰地点、未配置动作和缺少依据的场馆事实不会触发执行或猜测回答。</div>
          </Section>
          <Section title="任务状态" icon={Activity}>
            <div className="stack">
              <div className="inline"><span className="muted">执行中</span><span className="spacer" /><strong>{state.navigation.status === "running" ? state.tours.find((tour) => tour.id === state.navigation.taskId)?.name ?? "点位导航" : "无"}</strong></div>
              <div className="inline"><span className="muted">当前点位</span><span className="spacer" /><strong>{state.points.find((point) => point.id === state.navigation.pointId)?.name ?? "—"}</strong></div>
              <JobControls state={state} run={run} />
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function SettingsPage({
  connection,
  settings,
  run,
  onDisconnect,
  refresh,
}: {
  connection: Connection;
  settings?: PublicSettings;
  run: (path: string, body?: unknown, method?: string) => Promise<unknown>;
  onDisconnect: () => void;
  refresh: () => void;
}) {
  const [provider, setProvider] = useState<"ark" | "openai-compatible">(settings?.provider ?? "ark");
  const [model, setModel] = useState(settings?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(settings?.baseUrl ?? "");
  const [apiKey, setApiKey] = useState("");
  const [linksoulAppId, setLinksoulAppId] = useState("");
  const [linksoulAppKey, setLinksoulAppKey] = useState("");
  const [linksoulAppSecret, setLinksoulAppSecret] = useState("");
  const [agentCode, setAgentCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!settings) return;
    setProvider(settings.provider);
    setModel(settings.model);
    setBaseUrl(settings.baseUrl ?? "");
  }, [settings]);

  async function saveSettings(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    try {
      await run("/api/v1/settings/ai", { provider, model, baseUrl }, "PUT");
      if (apiKey) await run("/api/v1/settings/credentials", { apiKey }, "PUT");
      setApiKey("");
      setMessage("设置已保存。凭据不会再次显示。");
      refresh();
    } catch (caught) {
      setMessage((caught as Error).message);
    } finally { setSaving(false); }
  }

  async function saveRetrieverCredentials(event: React.FormEvent) {
    event.preventDefault();
    const values = [linksoulAppId, linksoulAppKey, linksoulAppSecret, agentCode];
    if (values.some(Boolean) && !values.every((value) => value.trim())) {
      setMessage("请同时填写 App ID、App Key、App Secret 和 Agent Code。");
      return;
    }
    setSaving(true);
    setMessage("");
    try {
      if (!values.some(Boolean)) return;
      await run("/api/v1/settings/credentials", {
        linksoulAppId,
        linksoulAppKey,
        linksoulAppSecret,
        agentCode,
      }, "PUT");
      setLinksoulAppId("");
      setLinksoulAppKey("");
      setLinksoulAppSecret("");
      setAgentCode("");
      setMessage("Retriever 凭据已写入机器人本地受限文件。");
      refresh();
    } catch (caught) {
      setMessage((caught as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <PageHeading title="设置" description="模型、知识检索与本机连接安全" />
      <div className="content-grid two">
        <div className="stack">
          <Section title="大模型" icon={Settings}>
            <form className="stack" onSubmit={saveSettings}>
              <div className="segmented" role="group" aria-label="模型服务商">
                <button type="button" aria-pressed={provider === "ark"} className={provider === "ark" ? "active" : ""} onClick={() => setProvider("ark")}>豆包 Ark</button>
                <button type="button" aria-pressed={provider === "openai-compatible"} className={provider === "openai-compatible" ? "active" : ""} onClick={() => setProvider("openai-compatible")}>OpenAI-compatible</button>
              </div>
              {provider === "openai-compatible" && <div className="field"><label htmlFor="model-base-url">API 地址</label><input id="model-base-url" type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" /></div>}
              <div className="field"><label htmlFor="model-name">模型名称</label><input id="model-name" value={model} onChange={(event) => setModel(event.target.value)} placeholder="模型 ID" required /></div>
              <div className="field">
                <label htmlFor="model-key">API Key</label>
                <input id="model-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" placeholder={settings?.credentialConfigured ? "已配置，留空保持不变" : "仅本次写入"} />
                <span className="helper">仅通过 HTTPS 提交至机器人受限凭据文件；不回读、不写入浏览器存储。</span>
              </div>
              <button className="btn primary" disabled={saving} type="submit"><Save size={13} />保存设置</button>
              {message && <div className="notice"><Check size={13} />{message}</div>}
            </form>
          </Section>
          <Section title="灵心知识库" icon={CircleHelp}>
            <form className="stack" onSubmit={saveRetrieverCredentials}>
              <div className="inline"><span>Agent Retriever</span><span className="spacer" /><span className="tag"><StatusDot ready={settings?.retrieverConfigured ?? false} />{settings?.retrieverConfigured ? "已配置" : "未配置"}</span></div>
              <div className="field"><label htmlFor="linksoul-app-id">App ID</label><input id="linksoul-app-id" type="password" autoComplete="new-password" value={linksoulAppId} onChange={(event) => setLinksoulAppId(event.target.value)} /></div>
              <div className="field"><label htmlFor="linksoul-app-key">App Key</label><input id="linksoul-app-key" type="password" autoComplete="new-password" value={linksoulAppKey} onChange={(event) => setLinksoulAppKey(event.target.value)} /></div>
              <div className="field"><label htmlFor="linksoul-app-secret">App Secret</label><input id="linksoul-app-secret" type="password" autoComplete="new-password" value={linksoulAppSecret} onChange={(event) => setLinksoulAppSecret(event.target.value)} /></div>
              <div className="field"><label htmlFor="linksoul-agent-code">Agent Code</label><input id="linksoul-agent-code" type="password" autoComplete="new-password" value={agentCode} onChange={(event) => setAgentCode(event.target.value)} /></div>
              <span className="helper">凭据仅写入机器人本地受限文件，不回读或保存到客户端。</span>
              <button className="btn" type="submit" disabled={saving}><Save size={13} />保存检索凭据</button>
              <div className="helper">场馆事实回答必须有检索或实时状态依据；无依据时使用固定拒答。</div>
            </form>
          </Section>
        </div>
        <div className="stack">
          <Section title="本机安全" icon={ShieldCheck}>
            <div className="check-row"><span className="check-icon"><Check size={12} /></span><strong>机器人端 HTTPS</strong><span className="check-detail">安全会话</span></div>
            <div className="check-row"><span className="check-icon"><Check size={12} /></span><strong>会话密钥</strong><span className="check-detail">仅内存</span></div>
            <div className="check-row"><span className="check-icon"><Check size={12} /></span><strong>模型密钥</strong><span className="check-detail">{settings?.credentialConfigured ? "受限文件" : "未设置"}</span></div>
            <div className="notice warn" style={{ marginTop: 12 }}><AlertTriangle size={14} />请先将机器人本地 CA 导入设备信任库，再通过 HTTPS 连接和配对。</div>
          </Section>
          <Section title="当前连接" icon={Radio}>
            <div className="stack">
              <div className="inline"><span className="muted">地址</span><span className="spacer" /><strong className="mono">{connection.baseUrl}</strong></div>
              <div className="inline"><span className="muted">运行模式</span><span className="spacer" /><span className="tag">{connection.demo ? "模拟设备" : "机器人"}</span></div>
              <button className="btn danger" onClick={onDisconnect}><DoorOpen size={13} />断开并清除会话</button>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [state, setState] = useState<RobotState | null>(null);
  const [settings, setSettings] = useState<PublicSettings>();
  const [view, setView] = useState<View>("overview");
  const [toast, setToast] = useState("");
  const clock = useClock();

  useEffect(() => { window.scrollTo({ top: 0, behavior: "auto" }); }, [view]);

  useEffect(() => {
    if (Capacitor.isNativePlatform()) {
      void ScreenOrientation.lock({ orientation: "landscape" }).catch(() => undefined);
    }
  }, []);

  const refresh = useCallback(async (active = connection) => {
    if (!active) return;
    try {
      const [nextState, nextSettings] = await Promise.all([
        api<RobotState>(active, "/api/v1/state"),
        api<PublicSettings>(active, "/api/v1/settings"),
      ]);
      setState(nextState);
      setSettings(nextSettings);
    } catch (error) {
      setState((current) => current && ({ ...current, connected: false }));
      if (error instanceof ApiError && error.status === 401) {
        setConnection(null);
        setState(null);
      }
      throw error;
    }
  }, [connection]);

  useEffect(() => {
    if (!connection) return;
    void refresh(connection).catch((error) => notify((error as Error).message));
    const socket = openEvents(connection, setState, () => { void refresh(connection).catch(() => undefined); });
    const timer = window.setInterval(() => void refresh(connection).catch(() => undefined), 5000);
    return () => {
      window.clearInterval(timer);
      socket.close();
    };
  }, [connection, refresh]);

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(""), 3600);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  function notify(message: string) {
    if (message) setToast(message);
  }

  const run = useCallback(async (path: string, body?: unknown, method = "POST") => {
    if (!connection) return;
    try {
      const result = await api<unknown>(connection, path, {
        method,
        ...(body === undefined ? {} : { body: jsonBody(body) }),
      });
      if (method !== "GET") {
        notify(path.includes("stop-save") ? "AIMDK 已确认地图保存。" : "请求已处理");
        await refresh(connection);
      }
      return result;
    } catch (error) {
      notify((error as Error).message);
      throw error;
    }
  }, [connection, refresh]);

  const activeMap = useMemo(() => state?.maps.find((map) => map.id === state.activeMapId), [state]);

  if (!connection || !state) {
    return <ConnectScreen onConnected={(next) => {
      setConnection(next);
      setView("overview");
    }} />;
  }

  const activeItem = navigationItems.find((item) => item.id === view);
  const mobileItems = navigationItems;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Bot size={18} /></div>
          <div className="brand-copy"><div className="brand-title">灵犀 X2</div><div className="brand-subtitle">MAPPING WORKSPACE</div></div>
        </div>
        <div className="nav-group">
          <div className="nav-label">机器人</div>
          {navigationItems.map(({ id, label, icon: Icon }) => (
            <button className={`nav-button ${view === id ? "active" : ""}`} key={id} title={label} onClick={() => setView(id)}>
              <Icon size={16} /><span>{label}</span>
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-robot"><span className="status-dot ok" /><span>{state.robotName}</span></div>
          <div style={{ marginTop: 7 }} className="mono">{connection.baseUrl.replace(/^https?:\/\//, "")}</div>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div className="topbar-title">{activeItem?.label ?? "运行总览"}</div>
          <div className="topbar-right">
            {state.demoMode && <span className="status-pill"><span className="status-dot warn" />演示数据</span>}
            <span className="status-pill"><StatusDot ready={state.connected} />{state.connected ? "机器人在线" : "连接中断"}</span>
            <span className="status-pill"><span className="mono">{clock}</span></span>
          </div>
        </header>
        {state.demoMode && <div className="demo-banner">模拟设备 · 页面中的状态与控制不会作用于真实机器人</div>}
        {view === "overview" && <Overview state={state} setView={setView} onRefresh={() => void refresh().catch((error) => notify((error as Error).message))} />}
        {view === "mapping" && <Mapping state={state} run={run} connection={connection} />}
        {view === "maps" && <Maps state={state} run={run} refresh={() => void refresh().catch(() => undefined)} />}
        {view === "navigation" && <NavigationPage state={state} run={run} />}
        {view === "tours" && <Tours state={state} run={run} refresh={() => void refresh().catch(() => undefined)} />}
        {view === "settings" && <SettingsPage connection={connection} settings={settings} run={run} refresh={() => void refresh().catch(() => undefined)} onDisconnect={() => {
          void api(connection, "/api/v1/session/revoke", { method: "POST" }).catch(() => undefined);
          setConnection(null);
          setState(null);
          setSettings(undefined);
        }} />}
      </main>
      <nav className="mobile-nav" aria-label="主导航">
        {mobileItems.map(({ id, label, icon: Icon }) => <button className={view === id ? "active" : ""} key={id} onClick={() => setView(id)}><Icon size={16} />{label}</button>)}
      </nav>
      <div className="portrait-warning"><RotateCcw size={12} />请将设备横向持握</div>
      {toast && <div role="status" className="toast">{toast}</div>}
    </div>
  );
}

function navigationLabel(status: RobotState["navigation"]["status"]): string {
  return { idle: "待命", requested: "等待确认", running: "执行中", paused: "已暂停", finished: "已完成", failed: "需检查" }[status];
}

function navigationBusy(state: RobotState): boolean {
  return state.navigation.requiresStop === true || ["requested", "running", "paused"].includes(state.navigation.status);
}

function JobControls({ state, run }: {
  state: RobotState;
  run: (path: string, body?: unknown, method?: string) => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  async function control(action: "pause" | "resume" | "stop") {
    setBusy(true);
    try { await run("/api/v1/navigation/control", { action }); }
    catch { /* The shared request handler already displays the error. */ }
    finally { setBusy(false); }
  }
  if (!navigationBusy(state)) return null;
  return <div className="task-controls">
    {["requested", "running"].includes(state.navigation.status) && <button className="btn small" disabled={busy || state.actionBusy} onClick={() => void control("pause")}><Pause size={12} />暂停</button>}
    {state.navigation.status === "paused" && <button className="btn small primary" disabled={busy || state.actionBusy} onClick={() => void control("resume")}><Play size={12} />继续</button>}
    <button className="btn small danger" disabled={busy} onClick={() => void control("stop")}><Square size={12} />停止</button>
  </div>;
}
