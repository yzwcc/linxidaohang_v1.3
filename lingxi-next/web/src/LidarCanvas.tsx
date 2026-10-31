import { useEffect, useRef, useState } from "react";
import { Maximize, Minus, Move, Navigation2, Plus, Trash2 } from "lucide-react";
import type { LidarFrame, SavedSlamPointPreview } from "../../shared/contracts";
import { openLidarEvents, type Connection } from "./api";
import { appendScan, lidarBounds, lidarViewport, lidarToScreen, lidarSeedGesture,
  type CanvasPoint, type LidarViewport, type PlanarLidarPose, type ScanHistory } from "./lidar-view";

type Props = ({ connection: Connection; preview?: never; inactiveNotice?: string } |
  { preview: SavedSlamPointPreview; connection?: never; inactiveNotice?: never }) &
  { overlayPose?: PlanarLidarPose;
    seedSelection?: { enabled: boolean; pose?: PlanarLidarPose; onSelect: (pose: PlanarLidarPose) => void } };

export default function LidarCanvas({ connection, preview, inactiveNotice, overlayPose, seedSelection }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const historyRef = useRef<ScanHistory>({ key: "", frames: [] });
  const [frame, setFrame] = useState<LidarFrame>();
  const [inputMode, setInputMode] = useState<"live" | "recorded" | "synthetic">("live");
  const [coordinateUnits, setCoordinateUnits] = useState<"metres" | "native-raw">("metres");
  const [notice, setNotice] = useState("等待激光雷达扫描数据");
  const [accumulate, setAccumulate] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [revision, setRevision] = useState(0);
  const [interaction, setInteraction] = useState<"pan" | "seed">("pan");
  const [gesturePose, setGesturePose] = useState<PlanarLidarPose>();
  const drag = useRef<{ pointerId: number; start: CanvasPoint; last: CanvasPoint;
    mode: "pan" | "seed"; key: string; view: LidarViewport } | undefined>(undefined);
  const viewRef = useRef<LidarViewport | undefined>(undefined);
  const boundsRef = useRef<ReturnType<typeof lidarBounds> | undefined>(undefined);
  const contextKey = useRef("");
  const previewKey = preview ? `${preview.mapId}:${preview.receiptSha256}:${preview.pointsSha256}` : "";

  function cancelGesture() { drag.current = undefined; setGesturePose(undefined); }

  useEffect(() => {
    drag.current = undefined; setGesturePose(undefined); setInteraction("pan");
    viewRef.current = undefined; boundsRef.current = undefined;
    historyRef.current = { key: "", frames: [] };
    setZoom(1); setPan({ x: 0, y: 0 });
  }, [previewKey]);

  useEffect(() => {
    if (!seedSelection?.enabled) { drag.current = undefined; setGesturePose(undefined); setInteraction("pan"); }
  }, [seedSelection?.enabled]);

  useEffect(() => {
    if (preview) {
      setFrame({
        sequence: 1, epoch: preview.mapId, source: "point-cloud", frameId: "GLIM",
        sensorFrame: "saved-map", fixedFrame: true, stampMs: 0, receivedAtMs: 0, points: preview.points,
      });
      setNotice("");
      return;
    }
    if (!connection) return;
    setFrame(undefined);
    setNotice("等待激光雷达扫描数据");
    let previous: LidarFrame | undefined, lastReceived = 0;
    const unavailable = (message: string) => {
      previous = undefined; lastReceived = 0;
      historyRef.current = { key: "", frames: [] };
      boundsRef.current = undefined;
      setFrame(undefined); setNotice(message);
    };
    if (inactiveNotice) { unavailable(inactiveNotice); return; }
    const subscription = openLidarEvents(connection, (event) => {
      if (event.type === "lidar-unavailable") { unavailable(event.message); return; }
      if (previous?.epoch === event.frame.epoch &&
          (event.frame.sequence <= previous.sequence || event.frame.stampMs < previous.stampMs)) return;
      previous = event.frame; lastReceived = performance.now();
      setInputMode(event.type === "slam-preview" ? event.inputMode : "live");
      setCoordinateUnits(event.type === "slam-preview" ? event.coordinateUnits : "metres");
      setFrame(event.frame);
      setNotice("");
    }, () => unavailable("扫描连接中断，正在重连"));
    const freshness = window.setInterval(() => {
      if (lastReceived && performance.now() - lastReceived > 2000) unavailable("扫描数据已中断");
    }, 250);
    return () => { subscription.close(); clearInterval(freshness); };
  }, [connection, preview, inactiveNotice]);

  useEffect(() => {
    if (!canvasRef.current) return;
    const observer = new ResizeObserver(() => setRevision((value) => value + 1));
    observer.observe(canvasRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current, ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const rect = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#fafbfc"; ctx.fillRect(0, 0, rect.width, rect.height);
    if (!frame || rect.width <= 48 || rect.height <= 48) {
      viewRef.current = undefined; historyRef.current = { key: "", frames: [] }; return;
    }
    const history = appendScan(historyRef.current, frame, accumulate);
    historyRef.current = history;
    if (contextKey.current !== history.key) {
      contextKey.current = history.key; boundsRef.current = undefined;
      setZoom(1); setPan({ x: 0, y: 0 });
    }
    const currentBounds = lidarBounds(history.frames);
    // Grow the view as a new area is scanned; do not rescale on every noisy ray.
    const previous = boundsRef.current;
    const bounds = previous ? {
      minX: Math.min(previous.minX, currentBounds.minX), minY: Math.min(previous.minY, currentBounds.minY),
      maxX: Math.max(previous.maxX, currentBounds.maxX), maxY: Math.max(previous.maxY, currentBounds.maxY),
    } : currentBounds;
    boundsRef.current = bounds;
    const view = lidarViewport(bounds, rect.width, rect.height, zoom, pan);
    viewRef.current = view;
    const { scale } = view;
    const screen = (x: number, y: number) => {
      const point = lidarToScreen(view, { x, y });
      return [point.x, point.y];
    };
    for (const item of history.frames) {
      const current = item.sequence === frame.sequence;
      ctx.fillStyle = current ? "#607387" : "#bac4d3";
      ctx.globalAlpha = current ? 0.88 : 0.26;
      const size = current ? 1.8 : 1.4;
      for (let index = 0; index < item.points.length; index += 3) {
        const [x, y] = screen(item.points[index], item.points[index + 1]);
        ctx.fillRect(x - size / 2, y - size / 2, size, size);
      }
    }
    ctx.globalAlpha = 1;
    if (frame.fixedFrame && history.frames.length > 1) {
      ctx.beginPath();
      let began = false;
      for (const item of history.frames) {
        if (!item.robotPose) continue;
        const [x, y] = screen(item.robotPose.x, item.robotPose.y);
        if (began) ctx.lineTo(x, y); else { ctx.moveTo(x, y); began = true; }
      }
      ctx.strokeStyle = "#c78c97"; ctx.lineWidth = 1.2; ctx.stroke();
    }
    const pose = preview ? overlayPose : frame.robotPose;
    if (pose) {
      const [x, y] = screen(pose.x, pose.y);
      ctx.save(); ctx.translate(x, y); ctx.rotate(-pose.yaw);
      ctx.fillStyle = "#e0eef9"; ctx.strokeStyle = "#4487b1"; ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.arc(0, 0, 13, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#286d9b"; ctx.beginPath(); ctx.moveTo(18, 0); ctx.lineTo(-7, -7); ctx.lineTo(-3, 0); ctx.lineTo(-7, 7); ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    const candidate = gesturePose ?? seedSelection?.pose;
    if (preview && candidate) {
      const [x, y] = screen(candidate.x, candidate.y);
      ctx.save(); ctx.translate(x, y); ctx.rotate(-candidate.yaw);
      ctx.strokeStyle = "#ba6038"; ctx.fillStyle = "#ba6038"; ctx.lineWidth = 2;
      ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.arc(0, 0, 17, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]); ctx.beginPath(); ctx.moveTo(-8, 0); ctx.lineTo(30, 0); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(30, 0); ctx.lineTo(20, -6); ctx.lineTo(20, 6); ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    const targetUnits = 90 / scale;
    const unit = 10 ** Math.floor(Math.log10(targetUnits));
    const distance = (targetUnits / unit >= 5 ? 5 : targetUnits / unit >= 2 ? 2 : 1) * unit;
    const bar = distance * scale;
    ctx.strokeStyle = "#526371"; ctx.fillStyle = "#526371"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(18, rect.height - 20); ctx.lineTo(18 + bar, rect.height - 20); ctx.stroke();
    ctx.font = "11px sans-serif"; ctx.fillText(`${distance} ${preview || coordinateUnits === "native-raw" ? "raw" : "m"}`, 18, rect.height - 27);
  }, [frame, accumulate, zoom, pan, revision, preview, coordinateUnits, overlayPose, gesturePose, seedSelection?.pose]);

  function clearHistory() {
    historyRef.current = { key: "", frames: [] }; boundsRef.current = undefined;
    setRevision((value) => value + 1);
  }

  return <div className="lidar-view">
    <div className="map-canvas-wrap lidar-canvas-wrap">
      <canvas ref={canvasRef} data-interaction={interaction} tabIndex={seedSelection ? 0 : undefined}
        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); cancelGesture(); } }}
        aria-label={preview ? "已保存 SLAM 地图点云" : inputMode === "live" ? "激光雷达实时扫描点云" : inputMode === "recorded" ? "录制数据建图点云" : "合成测试点云"} onPointerDown={(event) => {
        if (event.button !== 0 || !event.isPrimary || !viewRef.current || drag.current ||
            interaction === "seed" && !seedSelection?.enabled) return;
        const rect = event.currentTarget.getBoundingClientRect();
        const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        drag.current = { pointerId: event.pointerId, start: point, last: point, mode: interaction,
          key: previewKey, view: viewRef.current };
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
      }} onPointerMove={(event) => {
        const active = drag.current;
        if (!active || active.pointerId !== event.pointerId) return;
        const rect = event.currentTarget.getBoundingClientRect();
        const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        if (active.mode === "seed") setGesturePose(lidarSeedGesture(active.view, active.start, point));
        else setPan((value) => ({ x: value.x + point.x - active.last.x, y: value.y + point.y - active.last.y }));
        active.last = point;
      }} onPointerUp={(event) => {
        const active = drag.current, view = viewRef.current;
        if (!active || active.pointerId !== event.pointerId) return;
        if (active.mode === "seed" && seedSelection?.enabled && active.key === previewKey && view &&
            view.width === active.view.width && view.height === active.view.height && view.scale === active.view.scale &&
            view.centerX === active.view.centerX && view.centerY === active.view.centerY &&
            view.pan.x === active.view.pan.x && view.pan.y === active.view.pan.y) {
          const rect = event.currentTarget.getBoundingClientRect();
          const pose = lidarSeedGesture(active.view, active.start, { x: event.clientX - rect.left, y: event.clientY - rect.top });
          if (pose) seedSelection.onSelect(pose);
        }
        cancelGesture();
      }} onPointerCancel={cancelGesture} onLostPointerCapture={cancelGesture} />
      <div className="map-tools">
        {seedSelection && <>
          <button className="btn icon" title="平移地图" aria-label="平移地图" aria-pressed={interaction === "pan"}
            onClick={() => { cancelGesture(); setInteraction("pan"); }}><Move size={16} /></button>
          <button className="btn icon" title="设置初始位置与朝向" aria-label="设置初始位置与朝向"
            disabled={!seedSelection.enabled} aria-pressed={interaction === "seed"}
            onClick={() => { cancelGesture(); setInteraction("seed"); }}><Navigation2 size={16} /></button>
        </>}
        <button className="btn icon" title="放大扫描视图" onClick={() => { cancelGesture(); setZoom((value) => Math.min(8, value * 1.3)); }}><Plus size={16} /></button>
        <button className="btn icon" title="缩小扫描视图" onClick={() => { cancelGesture(); setZoom((value) => Math.max(0.5, value / 1.3)); }}><Minus size={16} /></button>
        <button className="btn icon" title="适应扫描范围" onClick={() => { cancelGesture(); boundsRef.current = undefined; setZoom(1); setPan({ x: 0, y: 0 }); setRevision((value) => value + 1); }}><Maximize size={16} /></button>
        {!preview && <button className="btn icon" title="清除累计点迹" onClick={clearHistory}><Trash2 size={16} /></button>}
      </div>
      {notice && <span className="map-empty-label" role="status">{notice}</span>}
    </div>
    <div className="lidar-footer">
      <span className="tag">{frame ? `${Math.floor(frame.points.length / 3).toLocaleString()} 点${preview ? "" : " / 帧"}` : "等待扫描"}</span>
      {frame && <span className="tag mono">{frame.frameId}</span>}
      {frame && <span className="tag">{preview ? "已保存点云" : inputMode === "recorded" ? "录制数据验证" : inputMode === "synthetic" ? "合成测试" : coordinateUnits === "native-raw" ? "实时 SLAM" : frame.source === "point-cloud" ? "PointCloud2" : "LaserScan"}</span>}
      {frame && (preview || coordinateUnits === "native-raw") && <span className="tag">原始坐标 / 未标定</span>}
      {preview?.sampled && <span className="tag">抽样预览 / 共 {preview.totalPoints.toLocaleString()} 点</span>}
      {preview && (gesturePose || seedSelection?.pose) && <span className="tag seed-draft-label">初值草稿</span>}
      {!preview && <label className="inline" title="保留近15秒扫描点迹"><input type="checkbox" checked={accumulate} onChange={(event) => { clearHistory(); setAccumulate(event.target.checked); }} />累计点迹</label>}
    </div>
    {frame && !frame.fixedFrame && <div className="helper lidar-detail" role="status">{frame.detail ?? "当前为雷达坐标，点迹不累计"}</div>}
    {!preview && frame?.fixedFrame && !frame.robotPose && <div className="helper lidar-detail">机器人位姿变换不可用</div>}
  </div>;
}
