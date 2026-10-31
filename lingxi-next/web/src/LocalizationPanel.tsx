import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Compass, Pause, Play, Power, RefreshCw, Square } from "lucide-react";
import type { RobotState, SavedSlamPointPreview } from "../../shared/contracts";
import {
  localizationStateSchema, sameLocalizationBinding, localizationRuntimeSchema, sameLocalizationRuntime,
  localizationStateMatchesRuntime, localizationLifecycleConfirmed,
  type LocalizationCommand, type LocalizationState, type LocalizationRuntime, type LocalizationLifecycleCommand,
} from "../../shared/localization";
import { api, ApiError, jsonBody, type Connection } from "./api";
import LidarCanvas from "./LidarCanvas";
import { emptySeedFields, planarLidarPose, planarSeedDraft, seedDraftReady, seedMatrix,
  withPlanarSeed, type SeedFields } from "./localization-view";
import type { PlanarLidarPose } from "./lidar-view";

const labels: Record<LocalizationState["state"], string> = {
  unseeded: "等待初始位姿", seed_requested: "初始位姿已提交，等待确认", acquiring: "正在建立连续匹配",
  tracking: "连续匹配已建立", lost: "匹配丢失", paused: "已暂停", stopped: "会话已停止", failed: "定位进程异常",
};
const reasons: Record<string, string> = {
  explicit_seed_required: "需要手动提交初始位姿",
  worker_seed_confirmation_required: "工作线程尚未确认初始位姿",
  corrected_point_stream_expired: "扫描数据已过期",
  fresh_matching_seed_reference_required: "参考帧已过期，请重新确认",
  explicit_stop: "本次定位已停止",
  runtime_stopping: "定位进程正在停止",
  seed_application_failed: "初始位姿应用失败",
};
const initial = emptySeedFields();
const runtimeLabels: Record<LocalizationRuntime["phase"], string> = {
  idle: "未启动定位进程", starting: "定位进程启动中", running: "定位进程运行中",
  stopping: "定位进程关闭中", stopped: "定位进程已关闭", failed: "定位进程异常",
};
const canStart = (value: LocalizationRuntime) => !value.closed && value.cleanupComplete &&
  ["idle", "stopped", "failed"].includes(value.phase);

export default function LocalizationPanel({ connection, maps, mappingActive = false }: {
  connection: Connection; maps: RobotState["savedSlamMaps"]; mappingActive?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<LocalizationState>();
  const [runtime, setRuntime] = useState<LocalizationRuntime>();
  const [mode, setMode] = useState<"unknown" | "fixed" | "service">("unknown");
  const [selectedMapId, setSelectedMapId] = useState("");
  const [preview, setPreview] = useState<SavedSlamPointPreview>();
  const [fields, setFields] = useState<SeedFields>(initial);
  const [draftBinding, setDraftBinding] = useState<LocalizationState["binding"]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  const current = useRef<{ value: LocalizationState; at: number } | undefined>(undefined);
  const currentRuntime = useRef<{ value: LocalizationRuntime; at: number } | undefined>(undefined);
  const rememberedRun = useRef("");
  const rememberedBinding = useRef("");
  const request = useRef<AbortController | undefined>(undefined);
  const commandRequest = useRef<AbortController | undefined>(undefined);
  const latestRequest = useRef(0);
  const connectionEpoch = useRef(0);

  useEffect(() => {
    ++connectionEpoch.current;
    commandRequest.current?.abort(); commandRequest.current = undefined;
    setBusy(false); setError(""); setMessage(""); setMode("unknown"); setSelectedMapId("");
    setFields(initial); setDraftBinding(undefined); rememberedRun.current = ""; rememberedBinding.current = "";
    return () => { ++connectionEpoch.current; commandRequest.current?.abort(); };
  }, [connection]);

  useEffect(() => {
    let closed = false, timer: number | undefined;
    current.current = undefined;
    currentRuntime.current = undefined;
    setSnapshot(undefined);
    if (busy) return;
    setRuntime(undefined);
    let fixed = false;
    const poll = async () => {
      const controller = new AbortController();
      request.current = controller;
      const version = ++latestRequest.current;
      try {
        const read = (route: string) => api(connection, route, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(1500)]),
        });
        let processState: LocalizationRuntime | undefined;
        if (!fixed) {
          try { processState = localizationRuntimeSchema.parse(await read("/api/v1/slam/localization/runtime")); }
          catch (caught) {
            if (!(caught instanceof ApiError) || caught.status !== 501) throw caught;
            fixed = true;
          }
        }
        let value: LocalizationState | undefined, stateAt = 0;
        if (fixed || (processState?.phase === "running" && !processState.closed)) {
          try {
            value = localizationStateSchema.parse(await read("/api/v1/slam/localization"));
            stateAt = performance.now();
          } catch (caught) { if (fixed) throw caught; }
        }
        if (processState && value) {
          // A child response is visible only inside one unchanged service/run observation.
          const after = localizationRuntimeSchema.parse(await read("/api/v1/slam/localization/runtime"));
          if (!sameLocalizationRuntime(processState, after) || !localizationStateMatchesRuntime(value, after) ||
              performance.now() - stateAt > 1200) value = undefined;
          processState = after;
        }
        if (closed || version !== latestRequest.current) return;
        setMode(fixed ? "fixed" : "service");
        currentRuntime.current = processState ? { value: processState, at: performance.now() } : undefined;
        setRuntime(processState);
        current.current = value ? { value, at: stateAt } : undefined;
        setSnapshot(value);
      } catch {
        if (closed || version !== latestRequest.current) return;
        current.current = undefined;
        currentRuntime.current = undefined;
        setSnapshot(undefined); setRuntime(undefined);
      } finally {
        if (!closed) timer = window.setTimeout(poll, 300);
      }
    };
    void poll();
    const freshness = window.setInterval(() => {
      if (currentRuntime.current && performance.now() - currentRuntime.current.at > 1200) {
        currentRuntime.current = undefined; setRuntime(undefined);
        current.current = undefined; setSnapshot(undefined);
      } else if (current.current && performance.now() - current.current.at > 1200) {
        current.current = undefined; setSnapshot(undefined);
      }
    }, 150);
    return () => {
      closed = true; controllerAbort(); clearTimeout(timer); clearInterval(freshness);
    };
    function controllerAbort() { request.current?.abort(); }
  }, [connection, revision, busy]);

  useEffect(() => {
    if (!runtime?.runId) return;
    const identity = runtime.serviceId + runtime.runId;
    if (identity !== rememberedRun.current) {
      rememberedRun.current = identity; setFields(initial); setDraftBinding(undefined);
    }
  }, [runtime?.serviceId, runtime?.runId]);

  const bindingKey = snapshot ? JSON.stringify(snapshot.binding) : "";
  useEffect(() => {
    if (bindingKey !== rememberedBinding.current) {
      rememberedBinding.current = bindingKey; setFields(initial); setDraftBinding(undefined);
    }
  }, [bindingKey]);

  const choosing = runtime && canStart(runtime);
  const selection = runtime?.selectedMap;
  const mapId = mode === "fixed" ? snapshot?.binding.map_id : runtime
    ? (choosing && selectedMapId ? selectedMapId : selection?.mapId) : undefined;
  const receiptSha256 = mode === "fixed" ? snapshot?.binding.receipt_sha256 :
    !choosing ? selection?.receiptSha256 : undefined;
  const pointsSha256 = mode === "fixed" ? snapshot?.binding.points_sha256 : undefined;
  useEffect(() => {
    setPreview(undefined);
    if (!mapId) return;
    const controller = new AbortController();
    void api<SavedSlamPointPreview>(connection, `/api/v1/slam/maps/${encodeURIComponent(mapId)}/points`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
    }).then((value) => {
      if (!controller.signal.aborted && value.mapId === mapId &&
          (!receiptSha256 || value.receiptSha256 === receiptSha256) &&
          (!pointsSha256 || value.pointsSha256 === pointsSha256)) setPreview(value);
    })
      .catch(() => undefined);
    return () => controller.abort();
  }, [connection, mapId, receiptSha256, pointsSha256]);

  async function command(action: LocalizationCommand["action"]) {
    const source = current.current;
    if (!source || performance.now() - source.at > 900 || busy || commandRequest.current) return;
    if (mode !== "fixed" && (!currentRuntime.current || performance.now() - currentRuntime.current.at > 900 ||
        !localizationStateMatchesRuntime(source.value, currentRuntime.current.value))) return;
    const state = source.value;
    let body: Omit<LocalizationCommand, "action">;
    try {
      if (action === "seed" && !seedDraftReady(fields, draftBinding, state, preview)) {
        throw new Error("请完整填写当前地图与会话的初始雷达位姿");
      }
      body = {
        binding: state.binding, expectedGeneration: state.generation,
        ...(action === "seed" ? { sourceFrame: state.sourceFrame,
          referenceFrame: state.referenceFrame, mapFromSensor: seedMatrix(fields) } : {}),
      } as Omit<LocalizationCommand, "action">;
      if (action === "seed" && state.referenceFrame === null) throw new Error("当前没有新鲜参考帧，请等待扫描数据");
    } catch (caught) { setError((caught as Error).message); return; }
    setBusy(true); setError(""); setMessage("");
    // Revoke visible pose before dispatch; an ambiguous acknowledgement is never retried.
    ++latestRequest.current; request.current?.abort();
    current.current = undefined; setSnapshot(undefined);
    const epoch = connectionEpoch.current, controller = new AbortController();
    commandRequest.current = controller;
    try {
      const result = localizationStateSchema.parse(await api(connection, `/api/v1/slam/localization/${action}`, {
        method: "POST", body: jsonBody(body), signal: AbortSignal.any([controller.signal, AbortSignal.timeout(2000)]),
      }));
      if (epoch !== connectionEpoch.current) return;
      if (!sameLocalizationBinding(result.binding, state.binding) || result.generation !== state.generation + 1 ||
          result.state !== ({ seed: "seed_requested", pause: "paused", stop: "stopped" } as const)[action] ||
          !result.requestPending || result.mapFromSensor !== null) throw new Error("响应未通过会话校验，请刷新状态后确认");
      setMessage(action === "seed" ? "初始位姿请求已接受，等待扫描匹配确认" :
        action === "pause" ? "暂停请求已接受；继续时需重新提交初始位姿" : "停止请求已接受；本会话不能恢复");
    } catch (caught) {
      if (epoch !== connectionEpoch.current) return;
      setError(`${(caught as Error).message}。请核对当前状态，操作不会自动重试。`);
    } finally {
      if (epoch === connectionEpoch.current) {
        commandRequest.current = undefined;
        setBusy(false); setRevision((value) => value + 1);
      }
    }
  }

  async function lifecycle(action: LocalizationLifecycleCommand["action"]) {
    const source = currentRuntime.current;
    if (!source || performance.now() - source.at > 900 || busy || commandRequest.current) return;
    const value = source.value;
    let body: LocalizationLifecycleCommand;
    if (action === "start") {
      if (!canStart(value) || !preview || preview.mapId !== selectedMapId ||
          preview.inputMode === "synthetic" || !maps?.find((item) => item.id === selectedMapId)?.sensorProvenance) return;
      body = { action, serviceId: value.serviceId, expectedGeneration: value.generation,
        mapId: selectedMapId, receiptSha256: preview.receiptSha256 };
    } else {
      if (!value.runId || value.closed || value.cleanupComplete || value.phase === "stopping") return;
      body = { action, serviceId: value.serviceId, runId: value.runId, expectedGeneration: value.generation };
    }
    setBusy(true); setError(""); setMessage("");
    ++latestRequest.current; request.current?.abort();
    current.current = undefined; currentRuntime.current = undefined; setSnapshot(undefined);
    const epoch = connectionEpoch.current, controller = new AbortController();
    commandRequest.current = controller;
    try {
      const { action: _action, ...payload } = body;
      const result = localizationRuntimeSchema.parse(await api(connection, `/api/v1/slam/localization/${action}`, {
        method: "POST", body: jsonBody(payload), signal: AbortSignal.any([controller.signal, AbortSignal.timeout(2000)]),
      }));
      if (epoch !== connectionEpoch.current) return;
      if (!localizationLifecycleConfirmed(body, result)) throw new Error("响应未通过定位进程校验");
      setMessage(action === "start" ? "启动请求已接受，等待进程与地图加载" : "关闭请求已接受，等待进程清理确认");
    } catch (caught) {
      if (epoch === connectionEpoch.current) setError(`${(caught as Error).message}。请核对当前状态，操作不会自动重试。`);
    } finally {
      if (epoch === connectionEpoch.current) {
        commandRequest.current = undefined; setBusy(false); setRevision((value) => value + 1);
      }
    }
  }

  const terminal = !snapshot || ["stopped", "failed"].includes(snapshot.state) || snapshot.workerClosed;
  const matchedPreview = preview?.mapId === mapId && (!receiptSha256 || preview?.receiptSha256 === receiptSha256) &&
    (!pointsSha256 || preview?.pointsSha256 === pointsSha256) ? preview : undefined;
  const pose = snapshot?.state === "tracking" && matchedPreview && snapshot.mapFromSensor &&
    matchedPreview.receiptSha256 === snapshot.binding.receipt_sha256 &&
    matchedPreview.pointsSha256 === snapshot.binding.points_sha256
    ? planarLidarPose(snapshot.mapFromSensor) : undefined;
  const pickEnabled = Boolean(!busy && !terminal && snapshot?.workerReady && !snapshot.requestPending &&
    snapshot.referenceFrame !== null && matchedPreview?.mapId === snapshot.binding.map_id &&
    matchedPreview?.receiptSha256 === snapshot.binding.receipt_sha256 &&
    matchedPreview?.pointsSha256 === snapshot.binding.points_sha256 && matchedPreview?.inputMode === snapshot.binding.input_mode);
  const draftPose = snapshot && draftBinding && sameLocalizationBinding(draftBinding, snapshot.binding) &&
    matchedPreview?.receiptSha256 === draftBinding.receipt_sha256 && matchedPreview?.pointsSha256 === draftBinding.points_sha256
    ? planarSeedDraft(fields) : undefined;
  const seedReady = seedDraftReady(fields, draftBinding, snapshot, matchedPreview);

  function editSeed(change: (value: SeedFields) => SeedFields) {
    const source = current.current;
    if (!pickEnabled || !source || !snapshot || performance.now() - source.at > 900 ||
        !sameLocalizationBinding(snapshot.binding, source.value.binding)) return;
    setFields(change); setDraftBinding(source.value.binding); setError("");
  }

  function pickSeed(value: PlanarLidarPose) { editSeed((previous) => withPlanarSeed(previous, value)); }
  const map = maps?.find((item) => item.id === mapId);
  const inputMode = snapshot?.binding.input_mode ?? runtime?.inputMode;
  const inputLabel = inputMode === "live" ? "实时传感器" : inputMode === "recorded" ? "录制数据" : "等待输入状态";

  return <div className="page localization-page">
    <div className="page-heading"><div><h1>定位验证</h1><p>已保存点云地图 · {inputLabel}</p></div>
      <button className="btn icon" title="刷新定位状态" disabled={busy} onClick={() => setRevision((v) => v + 1)}><RefreshCw size={15} /></button>
    </div>
    <div className="notice warn"><AlertTriangle size={15} />
      <span>胸前雷达原始坐标，尚未标定为底盘坐标。定位与导航尚未验收。</span>
    </div>
    <div className="content-grid two localization-layout">
      <section className="localization-cloud">
        <div className="inline localization-heading"><h2>{map?.name ?? "当前会话地图"}</h2>
          <span className="tag">{matchedPreview ? "已保存点云" : "点云暂不可用"}</span></div>
        {matchedPreview ? <LidarCanvas key={bindingKey} preview={matchedPreview} overlayPose={pose}
          seedSelection={{ enabled: pickEnabled, pose: draftPose, onSelect: pickSeed }} /> :
          <div className="empty">等待定位服务与地图点云</div>}
        {pose && <div className="helper mono" data-testid="localization-pose">
          雷达 · X {pose.x.toFixed(3)} / Y {pose.y.toFixed(3)} / Z {snapshot!.mapFromSensor![2][3].toFixed(3)} raw
        </div>}
      </section>
      <section className="localization-controls stack">
        <div className="localization-status" role="status" data-testid="localization-status">
          <strong>{snapshot ? labels[snapshot.state] : runtime ? runtimeLabels[runtime.phase] : "定位服务不可用"}</strong>
          <span className="helper">{snapshot ? reasons[snapshot.reason] ?? `${inputLabel}定位验证` : runtime
            ? "当前无有效定位位姿" : "等待连接，当前位姿已清除"}</span>
          {snapshot && <div className="inline"><span className="tag">代次 {snapshot.generation}</span>
            <span className="tag">连续匹配 {snapshot.consecutiveMatches}</span>
            <span className="tag">参考帧 {snapshot.referenceFrame ?? "无"}</span></div>}
        </div>
        {mode === "service" && <div className="stack localization-lifecycle">
          <div className="inline" role="status" data-testid="localization-runtime">
            <span className="tag">{runtime ? runtimeLabels[runtime.phase] : "进程状态不可用"}</span>
            {runtime?.runId && <span className="tag">{runtime.cleanupComplete ? "清理已确认" :
              ["starting", "running"].includes(runtime.phase) ? "会话占用中" : "清理未确认"}</span>}
          </div>
          <div className="field"><label htmlFor="localization-map">定位地图</label>
            <select id="localization-map" value={choosing ? selectedMapId : selection?.mapId ?? ""}
              disabled={busy || !choosing} onChange={(event) => { setSelectedMapId(event.target.value); setPreview(undefined); }}>
              <option value="">选择已保存地图</option>
              {maps?.map((item) => <option key={item.id} value={item.id}
                disabled={!item.sensorProvenance || item.inputMode === "synthetic"}>
                {item.name}{!item.sensorProvenance ? "（缺少配置来源）" : ""}
              </option>)}
            </select>
          </div>
          <div className="task-controls">
            <button className="btn primary" disabled={busy || mappingActive || !choosing || !selectedMapId ||
              matchedPreview?.mapId !== selectedMapId || matchedPreview?.inputMode === "synthetic"}
              onClick={() => void lifecycle("start")}><Play size={13} />启动定位进程</button>
            <button className="btn danger" disabled={busy || !runtime?.runId || runtime.closed ||
              runtime.cleanupComplete || runtime.phase === "stopping"}
              onClick={() => void lifecycle("shutdown")}><Power size={13} />关闭定位进程</button>
          </div>
          {runtime?.failureCode && <div className="notice error" role="alert">
            {runtime.failureCode === "map_selection_unavailable" ? "地图不可选择或与当前传感器配置不匹配" : "定位进程异常，请检查诊断记录"}
          </div>}
        </div>}
        <form className="stack" onSubmit={(event) => { event.preventDefault(); void command("seed"); }}>
          <h2>初始雷达位姿</h2>
          <div className="form-grid seed-fields">
            {(Object.keys(initial) as (keyof SeedFields)[]).map((key) => <div className="field" key={key}>
              <label htmlFor={`seed-${key}`}>{({ x: "X / raw", y: "Y / raw", z: "Z / raw",
                roll: "横滚 / °", pitch: "俯仰 / °", yaw: "朝向 / °" })[key]}</label>
              <input id={`seed-${key}`} type="number" step="any" required disabled={!pickEnabled}
                value={fields[key]} onChange={(event) => {
                  const value = event.target.value;
                  editSeed((previous) => ({ ...previous, [key]: value }));
                }} />
            </div>)}
          </div>
          <button className="btn primary" type="submit" disabled={busy || !seedReady}>
            <Compass size={14} />提交初始位姿
          </button>
        </form>
        <div className="task-controls">
          <button className="btn" disabled={busy || terminal || snapshot?.state === "paused"} onClick={() => void command("pause")}><Pause size={13} />暂停定位</button>
          <button className="btn danger" disabled={busy || terminal} onClick={() => void command("stop")}><Square size={13} />停止会话</button>
        </div>
        {message && <div className="notice" role="status">{message}</div>}
        {error && <div className="notice error" role="alert">{error}</div>}
      </section>
    </div>
  </div>;
}
