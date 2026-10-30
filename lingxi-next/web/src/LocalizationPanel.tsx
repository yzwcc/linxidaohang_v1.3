import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Compass, Pause, RefreshCw, Square } from "lucide-react";
import type { RobotState, SavedSlamPointPreview } from "../../shared/contracts";
import {
  localizationStateSchema, sameLocalizationBinding, type LocalizationCommand, type LocalizationState,
} from "../../shared/localization";
import { api, jsonBody, type Connection } from "./api";
import LidarCanvas from "./LidarCanvas";
import { planarLidarPose, seedMatrix, type SeedFields } from "./localization-view";

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
const initial: SeedFields = { x: "0", y: "0", z: "0", roll: "0", pitch: "0", yaw: "0" };

export default function LocalizationPanel({ connection, maps }: {
  connection: Connection; maps: RobotState["savedSlamMaps"];
}) {
  const [snapshot, setSnapshot] = useState<LocalizationState>();
  const [preview, setPreview] = useState<SavedSlamPointPreview>();
  const [fields, setFields] = useState<SeedFields>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  const current = useRef<{ value: LocalizationState; at: number } | undefined>(undefined);
  const request = useRef<AbortController | undefined>(undefined);
  const commandRequest = useRef<AbortController | undefined>(undefined);
  const latestRequest = useRef(0);
  const connectionEpoch = useRef(0);

  useEffect(() => {
    ++connectionEpoch.current;
    setBusy(false); setError(""); setMessage("");
    return () => { ++connectionEpoch.current; commandRequest.current?.abort(); };
  }, [connection]);

  useEffect(() => {
    let closed = false, timer: number | undefined;
    current.current = undefined;
    setSnapshot(undefined);
    if (busy) return;
    const poll = async () => {
      const controller = new AbortController();
      request.current = controller;
      const version = ++latestRequest.current;
      try {
        const value = localizationStateSchema.parse(await api(connection, "/api/v1/slam/localization", {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(1500)]),
        }));
        if (closed || version !== latestRequest.current) return;
        current.current = { value, at: performance.now() };
        setSnapshot(value);
      } catch {
        if (closed || version !== latestRequest.current) return;
        current.current = undefined;
        setSnapshot(undefined);
      } finally {
        if (!closed) timer = window.setTimeout(poll, 300);
      }
    };
    void poll();
    const freshness = window.setInterval(() => {
      if (current.current && performance.now() - current.current.at > 1200) {
        current.current = undefined; setSnapshot(undefined);
      }
    }, 150);
    return () => {
      closed = true; controllerAbort(); clearTimeout(timer); clearInterval(freshness);
    };
    function controllerAbort() { request.current?.abort(); }
  }, [connection, revision, busy]);

  const mapId = snapshot?.binding.map_id;
  const receiptSha256 = snapshot?.binding.receipt_sha256;
  const pointsSha256 = snapshot?.binding.points_sha256;
  useEffect(() => {
    setPreview(undefined);
    if (!mapId) return;
    const controller = new AbortController();
    void api<SavedSlamPointPreview>(connection, `/api/v1/slam/maps/${encodeURIComponent(mapId)}/points`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
    }).then((value) => {
      if (!controller.signal.aborted && value.mapId === mapId &&
          value.receiptSha256 === receiptSha256 && value.pointsSha256 === pointsSha256) setPreview(value);
    })
      .catch(() => undefined);
    return () => controller.abort();
  }, [connection, mapId, receiptSha256, pointsSha256]);

  async function command(action: LocalizationCommand["action"]) {
    const source = current.current;
    if (!source || performance.now() - source.at > 900 || busy) return;
    const state = source.value;
    let body: Omit<LocalizationCommand, "action">;
    try {
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
          result.mapFromSensor !== null) throw new Error("响应未通过会话校验，请刷新状态后确认");
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

  const terminal = !snapshot || ["stopped", "failed"].includes(snapshot.state) || snapshot.workerClosed;
  const matchedPreview = preview?.mapId === mapId && preview?.receiptSha256 === receiptSha256 &&
    preview?.pointsSha256 === pointsSha256 ? preview : undefined;
  const pose = snapshot?.state === "tracking" && matchedPreview && snapshot.mapFromSensor
    ? planarLidarPose(snapshot.mapFromSensor) : undefined;
  const map = maps?.find((item) => item.id === mapId);
  const inputLabel = snapshot?.binding.input_mode === "live" ? "实时传感器" :
    snapshot?.binding.input_mode === "recorded" ? "录制数据" : "等待输入状态";

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
        {matchedPreview ? <LidarCanvas preview={matchedPreview} overlayPose={pose} /> :
          <div className="empty">等待定位服务与地图点云</div>}
        {pose && <div className="helper mono" data-testid="localization-pose">
          雷达 · X {pose.x.toFixed(3)} / Y {pose.y.toFixed(3)} / Z {snapshot!.mapFromSensor![2][3].toFixed(3)} raw
        </div>}
      </section>
      <section className="localization-controls stack">
        <div className="localization-status" role="status" data-testid="localization-status">
          <strong>{snapshot ? labels[snapshot.state] : "定位服务不可用"}</strong>
          <span className="helper">{snapshot ? reasons[snapshot.reason] ?? `${inputLabel}定位验证` : "等待连接，当前位姿已清除"}</span>
          {snapshot && <div className="inline"><span className="tag">代次 {snapshot.generation}</span>
            <span className="tag">连续匹配 {snapshot.consecutiveMatches}</span>
            <span className="tag">参考帧 {snapshot.referenceFrame ?? "无"}</span></div>}
        </div>
        <form className="stack" onSubmit={(event) => { event.preventDefault(); void command("seed"); }}>
          <h2>初始雷达位姿</h2>
          <div className="form-grid seed-fields">
            {(Object.keys(initial) as (keyof SeedFields)[]).map((key) => <div className="field" key={key}>
              <label htmlFor={`seed-${key}`}>{({ x: "X / raw", y: "Y / raw", z: "Z / raw",
                roll: "横滚 / °", pitch: "俯仰 / °", yaw: "朝向 / °" })[key]}</label>
              <input id={`seed-${key}`} type="number" step="any" required disabled={busy}
                value={fields[key]} onChange={(event) => setFields({ ...fields, [key]: event.target.value })} />
            </div>)}
          </div>
          <button className="btn primary" type="submit" disabled={busy || terminal || !snapshot?.workerReady || snapshot.requestPending || snapshot.referenceFrame === null}>
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
