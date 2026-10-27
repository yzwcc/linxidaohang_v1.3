import { useEffect, useRef, useState } from "react";
import { Maximize, Minus, Plus, Trash2 } from "lucide-react";
import type { LidarFrame } from "../../shared/contracts";
import { openLidarEvents, type Connection } from "./api";
import { appendScan, lidarBounds, type ScanHistory } from "./lidar-view";

export default function LidarCanvas({ connection }: { connection: Connection }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const historyRef = useRef<ScanHistory>({ key: "", frames: [] });
  const [frame, setFrame] = useState<LidarFrame>();
  const [notice, setNotice] = useState("等待激光雷达扫描数据");
  const [accumulate, setAccumulate] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [revision, setRevision] = useState(0);
  const drag = useRef<{ x: number; y: number } | undefined>(undefined);
  const boundsRef = useRef<ReturnType<typeof lidarBounds> | undefined>(undefined);
  const contextKey = useRef("");

  useEffect(() => {
    let closed = false, socket: WebSocket | undefined, retry: number | undefined;
    let previous: LidarFrame | undefined, lastReceived = 0;
    const unavailable = (message: string) => { setFrame(undefined); setNotice(message); };
    const connect = () => {
      socket = openLidarEvents(connection, (event) => {
        if (closed) return;
        if (event.type === "lidar-unavailable") { unavailable(event.message); return; }
        if (previous?.epoch === event.frame.epoch &&
            (event.frame.sequence <= previous.sequence || event.frame.stampMs < previous.stampMs)) return;
        previous = event.frame; lastReceived = performance.now();
        setFrame(event.frame);
        setNotice("");
      }, () => {
        if (closed) return;
        unavailable("扫描连接中断，正在重连");
        retry = window.setTimeout(connect, 1500);
      });
    };
    connect();
    const freshness = window.setInterval(() => {
      if (lastReceived && performance.now() - lastReceived > 2000) unavailable("扫描数据已中断");
    }, 250);
    return () => { closed = true; socket?.close(); clearTimeout(retry); clearInterval(freshness); };
  }, [connection]);

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
    if (!frame) { historyRef.current = { key: "", frames: [] }; return; }
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
    const scale = zoom * Math.min((rect.width - 48) / (bounds.maxX - bounds.minX), (rect.height - 48) / (bounds.maxY - bounds.minY));
    const centerX = (bounds.minX + bounds.maxX) / 2, centerY = (bounds.minY + bounds.maxY) / 2;
    const screen = (x: number, y: number) => [rect.width / 2 + pan.x + (x - centerX) * scale, rect.height / 2 + pan.y - (y - centerY) * scale];
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
    if (frame.robotPose) {
      const [x, y] = screen(frame.robotPose.x, frame.robotPose.y);
      ctx.save(); ctx.translate(x, y); ctx.rotate(-frame.robotPose.yaw);
      ctx.fillStyle = "#e0eef9"; ctx.strokeStyle = "#4487b1"; ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.arc(0, 0, 13, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#286d9b"; ctx.beginPath(); ctx.moveTo(18, 0); ctx.lineTo(-7, -7); ctx.lineTo(-3, 0); ctx.lineTo(-7, 7); ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    const targetMetres = 90 / scale;
    const unit = 10 ** Math.floor(Math.log10(targetMetres));
    const metres = (targetMetres / unit >= 5 ? 5 : targetMetres / unit >= 2 ? 2 : 1) * unit;
    const bar = metres * scale;
    ctx.strokeStyle = "#526371"; ctx.fillStyle = "#526371"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(18, rect.height - 20); ctx.lineTo(18 + bar, rect.height - 20); ctx.stroke();
    ctx.font = "11px sans-serif"; ctx.fillText(`${metres} m`, 18, rect.height - 27);
  }, [frame, accumulate, zoom, pan, revision]);

  function clearHistory() {
    historyRef.current = { key: "", frames: [] }; boundsRef.current = undefined;
    setRevision((value) => value + 1);
  }

  return <div className="lidar-view">
    <div className="map-canvas-wrap lidar-canvas-wrap">
      <canvas ref={canvasRef} aria-label="激光雷达实时扫描点云" onPointerDown={(event) => {
        drag.current = { x: event.clientX, y: event.clientY }; event.currentTarget.setPointerCapture(event.pointerId);
      }} onPointerMove={(event) => {
        if (!drag.current || !event.buttons) return;
        const dx = event.clientX - drag.current.x, dy = event.clientY - drag.current.y;
        drag.current = { x: event.clientX, y: event.clientY };
        setPan((value) => ({ x: value.x + dx, y: value.y + dy }));
      }} onPointerUp={() => { drag.current = undefined; }} onPointerCancel={() => { drag.current = undefined; }} />
      <div className="map-tools">
        <button className="btn icon" title="放大扫描视图" onClick={() => setZoom((value) => Math.min(8, value * 1.3))}><Plus size={16} /></button>
        <button className="btn icon" title="缩小扫描视图" onClick={() => setZoom((value) => Math.max(0.5, value / 1.3))}><Minus size={16} /></button>
        <button className="btn icon" title="适应扫描范围" onClick={() => { boundsRef.current = undefined; setZoom(1); setPan({ x: 0, y: 0 }); setRevision((value) => value + 1); }}><Maximize size={16} /></button>
        <button className="btn icon" title="清除累计点迹" onClick={clearHistory}><Trash2 size={16} /></button>
      </div>
      {notice && <span className="map-empty-label" role="status">{notice}</span>}
    </div>
    <div className="lidar-footer">
      <span className="tag">{frame ? `${Math.floor(frame.points.length / 3).toLocaleString()} 点 / 帧` : "等待扫描"}</span>
      {frame && <span className="tag mono">{frame.frameId}</span>}
      {frame && <span className="tag">{frame.source === "point-cloud" ? "PointCloud2" : "LaserScan"}</span>}
      <label className="inline" title="保留近15秒扫描点迹"><input type="checkbox" checked={accumulate} onChange={(event) => { clearHistory(); setAccumulate(event.target.checked); }} />累计点迹</label>
    </div>
    {frame && !frame.fixedFrame && <div className="helper lidar-detail" role="status">{frame.detail ?? "当前为雷达坐标，点迹不累计"}</div>}
    {frame?.fixedFrame && !frame.robotPose && <div className="helper lidar-detail">机器人位姿变换不可用</div>}
  </div>;
}
