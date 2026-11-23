import { useEffect, useMemo, useRef, useState } from "react";
import type { OccupancyGrid, RobotPose, SurveyOverview } from "../../shared/contracts";
import { api, type Connection } from "./api";
import { lidarBounds, lidarScaleBar, lidarSeedGesture, lidarToScreen, lidarViewport, surveyTrailBounds, type CanvasPoint, type LidarViewport } from "./lidar-view";

// Key this component by connection, map/session and mode: neither pixels nor a
// pending draft may cross a coordinate-system boundary.
export default function SurveyView({ connection, id, saved, name, allowSeed, located, submit }: {
  connection: Connection; id: string; saved?: boolean; name?: string; allowSeed?: boolean; located?: RobotPose;
  submit?: (pose: RobotPose) => Promise<unknown>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null), view = useRef<LidarViewport | undefined>(undefined);
  const [cloud, setCloud] = useState<SurveyOverview>(), [grid, setGrid] = useState<OccupancyGrid>();
  const [refresh, retry] = useState(0), [notice, setNotice] = useState("正在读取点云…"), [gridError, setGridError] = useState(false);
  const [zoom, setZoom] = useState(1), [pan, setPan] = useState({ x: 0, y: 0 }), [size, resize] = useState(0);
  const [fit, setFit] = useState<"route" | "full">(saved ? "route" : "full");
  const [showCloud, toggleCloud] = useState(true), [showGrid, toggleGrid] = useState(true), [showTrail, toggleTrail] = useState(true);
  const [picking, pick] = useState(false), [seed, setSeed] = useState<RobotPose>(), [pending, busy] = useState(false);
  const [result, setResult] = useState("");
  const drag = useRef<{ start: CanvasPoint; last: CanvasPoint; view: LidarViewport; picking: boolean } | undefined>(undefined);
  const endpoint = `/api/v1/${saved ? "maps" : "surveys"}/${encodeURIComponent(id)}`;
  useEffect(() => {
    const cancel = new AbortController(); let timer: number | undefined;
    async function load() {
      try {
        const data = await api<SurveyOverview>(connection, `${endpoint}/overview`, { signal: cancel.signal }, 20000);
        if (cancel.signal.aborted) return;
        if (data.id !== id || data.frame !== (saved ? "map" : "start-base")) throw Error();
        setCloud(data); setNotice(!data.xyz.length ? "等待本次扫描与同时间戳机身 TF 配对" : !saved && !data.pose ? "扫描未更新，保留历史点云" : "");
      } catch { if (!cancel.signal.aborted) setNotice(saved ? "点云读取失败，请重试" : "扫描连接中断，保留本轮历史点云"); }
      if (!saved && !cancel.signal.aborted) timer = window.setTimeout(load, 1000);
    }
    void load();
    if (saved) void api<OccupancyGrid>(connection, `${endpoint}/grid`, { signal: cancel.signal }, 22000)
      .then(g => { if (!cancel.signal.aborted) { setGrid(g); setGridError(false); } })
      .catch(() => { if (!cancel.signal.aborted) setGridError(true); });
    return () => { cancel.abort(); clearTimeout(timer); };
  }, [connection, endpoint, id, saved, refresh]);
  useEffect(() => {
    if (!canvas.current) return;
    const observer = new ResizeObserver(() => resize(n => n + 1)); observer.observe(canvas.current);
    return () => observer.disconnect();
  }, []);
  const raster = useMemo(() => {
    if (!grid || grid.originYaw !== 0) return;
    const layer = document.createElement("canvas"); layer.width = grid.width; layer.height = grid.height;
    const ctx = layer.getContext("2d"); if (!ctx) return;
    const image = ctx.createImageData(grid.width, grid.height);
    for (let i = 0; i < grid.cells.length; i++) {
      const j = 4 * ((grid.height - 1 - Math.floor(i / grid.width)) * grid.width + i % grid.width);
      const v = grid.cells[i]; if (v < 0) continue;
      image.data[j] = v ? 36 : 211; image.data[j+1] = v ? 55 : 232; image.data[j+2] = v ? 65 : 243; image.data[j+3] = 255;
    }
    ctx.putImageData(image, 0, 0); return layer;
  }, [grid]);
  const routeBounds = useMemo(() => saved && cloud ? surveyTrailBounds(cloud.trail) : undefined, [saved, cloud]);
  const fullBounds = useMemo(() => lidarBounds(cloud ? [{ points: cloud.xyz }] : []), [cloud]);
  const focusRoute = fit === "route" && Boolean(routeBounds);
  const canPick = Boolean(allowSeed && cloud?.xyz.length && (showCloud || showGrid && raster) && !pending);
  useEffect(() => {
    const el = canvas.current, ctx = el?.getContext("2d"); if (!el || !ctx) return;
    view.current = undefined;
    const { width, height } = el.getBoundingClientRect(); if (width <= 48 || height <= 48) return;
    const dpr = Math.min(devicePixelRatio || 1, 2); el.width = width*dpr; el.height = height*dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.fillStyle = "#fafafa"; ctx.fillRect(0, 0, width, height);
    if (!cloud?.xyz.length) return;
    const b = focusRoute && routeBounds ? routeBounds : fullBounds;
    const v = lidarViewport(b, width, height, zoom, pan); view.current = v;
    const xy = (x: number, y: number) => lidarToScreen(v, { x, y });
    if (showGrid && grid && raster) {
      const p = xy(grid.originX, grid.originY + grid.height * grid.resolution); ctx.imageSmoothingEnabled = false;
      ctx.drawImage(raster, p.x, p.y, grid.width * grid.resolution * v.scale, grid.height * grid.resolution * v.scale);
    }
    if (showCloud) for (let i=0; i < cloud.xyz.length; i+=3) {
      const z = cloud.xyz[i+2], p = xy(cloud.xyz[i], cloud.xyz[i+1]);
      if (p.x < -2 || p.x > width || p.y < -2 || p.y > height) continue;
      const structure = saved ? z >= .12 && z <= 1.7 : z >= -.6 && z <= .65;
      ctx.fillStyle = structure ? "rgba(30,49,61,.72)" : "rgba(121,168,191,.20)";
      ctx.fillRect(p.x, p.y, structure ? 1.65 : 1.2, structure ? 1.65 : 1.2);
    }
    const trail = cloud.trail;
    if (showTrail) {
      ctx.beginPath(); ctx.strokeStyle = "#e77a25"; ctx.lineWidth=2.5;
      for (let i=0; i<trail.length; i+=3) {
        const p = xy(trail[i], trail[i+1]); if (!i || trail[i+2]-trail[i-1]>2) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y);
      } ctx.stroke();
    }
    const dot = (x:number,y:number,color:string,yaw?:number) => {
      const p=xy(x,y); ctx.fillStyle=color; ctx.beginPath(); ctx.arc(p.x,p.y,5,0,Math.PI*2); ctx.fill();
      if (yaw !== undefined) { ctx.strokeStyle=color; ctx.lineWidth=3; ctx.beginPath(); ctx.moveTo(p.x,p.y);
        ctx.lineTo(p.x+16*Math.cos(yaw),p.y-16*Math.sin(yaw)); ctx.stroke(); }
    };
    if (showTrail && trail.length) { dot(trail[0],trail[1],"#22915f"); dot(trail.at(-3)!,trail.at(-2)!,"#ba493e"); }
    if (!saved && !notice && cloud.pose) dot(cloud.pose[0],cloud.pose[1],"#ae3535",cloud.pose[2]);
    if (saved && located) dot(located.x, located.y, "#007989", located.yaw);
    if (seed) dot(seed.x,seed.y,"#6945cd",seed.yaw);
    const scale = lidarScaleBar(v.scale, Math.min(96, width / 3));
    if (scale) {
      ctx.font = "12px sans-serif";
      ctx.fillStyle = "rgba(255,255,255,.92)";
      ctx.fillRect(8, height - 35, 12 + Math.max(scale.pixels, ctx.measureText(scale.label).width), 27);
      ctx.fillStyle = "#243741";
      ctx.fillText(scale.label, 14, height - 21);
      ctx.fillRect(14, height - 16, scale.pixels, 2);
    }
  }, [cloud,grid,raster,zoom,pan,size,seed,saved,located,notice,focusRoute,routeBounds,fullBounds,showCloud,showGrid,showTrail]);
  function local(e: React.PointerEvent<HTMLCanvasElement>) { const r=e.currentTarget.getBoundingClientRect(); return {x:e.clientX-r.left,y:e.clientY-r.top}; }
  function recenter(mode: "route" | "full") {
    drag.current = undefined; setFit(mode); setZoom(1); setPan({ x: 0, y: 0 });
  }
  return <div className="stack survey-view">
    <div className="inline survey-layers" role="group" aria-label="累计地图图层">
      <label><input type="checkbox" checked={showCloud} onChange={e=>{drag.current=undefined; toggleCloud(e.target.checked);}} />累计点云</label>
      {saved && <label><input type="checkbox" checked={showGrid} disabled={!raster} onChange={e=>{drag.current=undefined; toggleGrid(e.target.checked);}} />导航栅格</label>}
      <label><input type="checkbox" checked={showTrail} onChange={e=>{drag.current=undefined; toggleTrail(e.target.checked);}} />采集轨迹</label>
    </div>
    <div className="map-canvas-wrap lidar-canvas-wrap"><canvas ref={canvas} aria-label={saved ? "已保存累计点云与采集轨迹" : "本轮累计点云与采集轨迹"}
      data-interaction={picking && canPick ? "seed" : undefined}
      onPointerDown={e=>{if(!view.current || !e.isPrimary || e.button!==0) return; const start=local(e);
        drag.current={start,last:start,view:view.current,picking:picking && canPick}; e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={e=>{const d=drag.current;if(!d || !e.isPrimary || d.picking)return; const p=local(e),dx=p.x-d.last.x,dy=p.y-d.last.y;
        setPan(old=>({x:old.x+dx,y:old.y+dy})); d.last=p;}}
      onPointerUp={e=>{const d=drag.current, v=view.current; drag.current=undefined; if(d?.picking && canPick && v &&
        d.view.width===v.width && d.view.height===v.height && d.view.scale===v.scale && d.view.centerX===v.centerX && d.view.centerY===v.centerY && d.view.pan.x===v.pan.x && d.view.pan.y===v.pan.y){
        const p=lidarSeedGesture(d.view,d.start,local(e)); if(p){setSeed(p);setResult("");pick(false);} }}}
      onLostPointerCapture={()=>{drag.current=undefined;}} onPointerCancel={()=>{drag.current=undefined;}} />
      <div className="map-tools"><button className="btn" aria-label="放大累计地图" onClick={()=>setZoom(n=>Math.min(12,n*1.4))}>＋</button>
      <button className="btn" aria-label="缩小累计地图" onClick={()=>setZoom(n=>Math.max(.5,n/1.4))}>−</button>
      {saved && <button className="btn" disabled={!routeBounds} aria-pressed={focusRoute} onClick={()=>recenter("route")}>路线附近</button>}
      <button className="btn" aria-pressed={!focusRoute} onClick={()=>recenter("full")}>全图</button></div></div>
    {focusRoute && <div className="helper">当前聚焦采集路线，点击「全图」可查看远处点迹。</div>}
    {showCloud && <div className="helper">累计点云按高度着色，深色点迹不一定是墙。</div>}
    {saved && showGrid && raster && <div className="helper">导航栅格：浅蓝为空闲、深灰为占据、透明为未知。未知不代表可通行。</div>}
    {showTrail && <div className="helper">绿点：采集起点 · 橙线：{saved ? "记录的 IMU 轨迹" : "机身采集轨迹"} · 红点：{saved ? "采集终点，不是当前位置" : "最近采集位置"}。</div>}
    {cloud && <div className="helper">全程预览 {cloud.xyz.length/3} 点 · {Math.floor(cloud.trail.length/3)} 个轨迹样本{located && saved ? " · 青色箭头：当前定位" : ""}</div>}
    {(notice || gridError) && <div role="status">{notice} {gridError && "栅格暂不可用。"} {saved && <button className="btn small" onClick={()=>retry(n=>n+1)}>重新读取</button>}</div>}
    {saved && <><button className="btn" disabled={!canPick && !picking} onClick={()=>{drag.current=undefined;pick(!picking);setResult("");}}>{picking ? "取消选取" : "在地图上选位置和朝向"}</button>
      {picking && <div role="status">{canPick ? "在机器人实际站位按下，向机头朝向拖动后松开。" : "选取已暂停；需要显示点云或栅格，且机器人处于可重定位状态。"}</div>}
      {seed && <div className="inline"><span>紫色初值草稿：{seed.x.toFixed(2)}, {seed.y.toFixed(2)} m · {(seed.yaw*180/Math.PI).toFixed(1)}°</span>
        <button className="btn primary" disabled={!canPick || picking} onClick={async()=>{if(!submit || !window.confirm(`已现场核对机器人在「${name}」的位置和朝向，开始重定位？`))return;
          busy(true);setResult("正在用新的现场扫描验证…");try {await submit(seed);setResult("已收到重定位确认");setSeed(undefined);} catch {setResult("重定位未确认，请核对站位和朝向");} finally {busy(false);} }}>确认并重定位</button></div>}
      {result && <div role="status">{result}</div>}</>}
  </div>;
}
