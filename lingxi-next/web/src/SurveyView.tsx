import { useEffect, useMemo, useRef, useState } from "react";
import type { OccupancyGrid, RobotPose, SurveyOverview } from "../../shared/contracts";
import { api, type Connection } from "./api";
import { lidarBounds, lidarSeedGesture, lidarToScreen, lidarViewport, type CanvasPoint, type LidarViewport } from "./lidar-view";

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
  useEffect(() => {
    const el = canvas.current, ctx = el?.getContext("2d"); if (!el || !ctx) return;
    const { width, height } = el.getBoundingClientRect(); if (width <= 48 || height <= 48) return;
    const dpr = Math.min(devicePixelRatio || 1, 2); el.width = width*dpr; el.height = height*dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.fillStyle = "#fafafa"; ctx.fillRect(0, 0, width, height);
    if (!cloud?.xyz.length) { view.current = undefined; return; }
    const b = lidarBounds([{ points: cloud.xyz }]);
    const v = lidarViewport(b, width, height, zoom, pan); view.current = v;
    const xy = (x: number, y: number) => lidarToScreen(v, { x, y });
    if (grid && raster) {
      const p = xy(grid.originX, grid.originY + grid.height * grid.resolution); ctx.imageSmoothingEnabled = false;
      ctx.drawImage(raster, p.x, p.y, grid.width * grid.resolution * v.scale, grid.height * grid.resolution * v.scale);
    }
    for (let i=0; i < cloud.xyz.length; i+=3) {
      const z = cloud.xyz[i+2], p = xy(cloud.xyz[i], cloud.xyz[i+1]);
      const structure = saved ? z >= .12 && z <= 1.7 : z >= -.6 && z <= .65;
      ctx.fillStyle = structure ? "rgba(30,49,61,.72)" : "rgba(121,168,191,.20)";
      ctx.fillRect(p.x, p.y, structure ? 1.65 : 1.2, structure ? 1.65 : 1.2);
    }
    const trail = cloud.trail; ctx.beginPath(); ctx.strokeStyle = "#e77a25"; ctx.lineWidth=2.5;
    for (let i=0; i<trail.length; i+=3) {
      const p = xy(trail[i], trail[i+1]); if (!i || trail[i+2]-trail[i-1]>2) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y);
    } ctx.stroke();
    const dot = (x:number,y:number,color:string,yaw?:number) => {
      const p=xy(x,y); ctx.fillStyle=color; ctx.beginPath(); ctx.arc(p.x,p.y,5,0,Math.PI*2); ctx.fill();
      if (yaw !== undefined) { ctx.strokeStyle=color; ctx.lineWidth=3; ctx.beginPath(); ctx.moveTo(p.x,p.y);
        ctx.lineTo(p.x+16*Math.cos(yaw),p.y-16*Math.sin(yaw)); ctx.stroke(); }
    };
    if (trail.length) { dot(trail[0],trail[1],"#22915f"); dot(trail.at(-3)!,trail.at(-2)!,"#ba493e"); }
    if (!saved && !notice && cloud.pose) dot(cloud.pose[0],cloud.pose[1],"#ae3535",cloud.pose[2]);
    if (saved && located) dot(located.x, located.y, "#007989", located.yaw);
    if (seed) dot(seed.x,seed.y,"#6945cd",seed.yaw);
  }, [cloud,grid,raster,zoom,pan,size,seed,saved,located,notice]);
  function local(e: React.PointerEvent<HTMLCanvasElement>) { const r=e.currentTarget.getBoundingClientRect(); return {x:e.clientX-r.left,y:e.clientY-r.top}; }
  return <div className="stack">
    <div className="map-canvas-wrap lidar-canvas-wrap"><canvas ref={canvas} aria-label={saved ? "已保存累计点云与采集轨迹" : "本轮累计点云与采集轨迹"}
      onPointerDown={e=>{if(!view.current || !e.isPrimary || e.button!==0) return; const start=local(e);
        drag.current={start,last:start,view:view.current,picking:picking && !!allowSeed}; e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={e=>{const d=drag.current;if(!d || !e.isPrimary || d.picking)return; const p=local(e),dx=p.x-d.last.x,dy=p.y-d.last.y;
        setPan(old=>({x:old.x+dx,y:old.y+dy})); d.last=p;}}
      onPointerUp={e=>{const d=drag.current, v=view.current; drag.current=undefined; if(d?.picking && allowSeed && v &&
        d.view.width===v.width && d.view.height===v.height && d.view.scale===v.scale && d.view.centerX===v.centerX && d.view.centerY===v.centerY && d.view.pan.x===v.pan.x && d.view.pan.y===v.pan.y){
        const p=lidarSeedGesture(d.view,d.start,local(e)); if(p){setSeed(p);setResult("");pick(false);} }}}
      onLostPointerCapture={()=>{drag.current=undefined;}} onPointerCancel={()=>{drag.current=undefined;}} />
      <div className="map-tools"><button className="btn" aria-label="放大累计地图" onClick={()=>setZoom(n=>Math.min(12,n*1.4))}>＋</button>
      <button className="btn" aria-label="缩小累计地图" onClick={()=>setZoom(n=>Math.max(.5,n/1.4))}>−</button>
      <button className="btn" onClick={()=>{setZoom(1);setPan({x:0,y:0});}}>全图</button></div></div>
    <div className="helper">绿点：采集起点 · 橙线：{saved ? "记录的 IMU 轨迹" : "机身采集轨迹"} · 红点：{saved ? "采集终点，不是当前位置" : "最近采集位置"}。{saved ? "浅蓝底：栅格确认的空闲区域；深色：障碍与结构点迹。" : "点迹为整轮空间抽样，深浅仅表示高度，不代表可通行。"}</div>
    {cloud && <div className="helper">全程预览 {cloud.xyz.length/3} 点 · {Math.floor(cloud.trail.length/3)} 个轨迹样本{located && saved ? " · 青色箭头：当前定位" : ""}</div>}
    {(notice || gridError) && <div role="status">{notice} {gridError && "栅格暂不可用。"} {saved && <button className="btn small" onClick={()=>retry(n=>n+1)}>重新读取</button>}</div>}
    {saved && <><button className="btn" disabled={!allowSeed || !cloud?.xyz.length || pending} onClick={()=>{pick(!picking);setResult("");}}>{picking ? "取消选取" : "在点云上选位置和朝向"}</button>
      {picking && <div role="status">在机器人实际站位按下，向机头朝向拖动后松开。</div>}
      {seed && <div className="inline"><span>紫色初值草稿：{seed.x.toFixed(2)}, {seed.y.toFixed(2)} m · {(seed.yaw*180/Math.PI).toFixed(1)}°</span>
        <button className="btn primary" disabled={!allowSeed || pending || picking} onClick={async()=>{if(!submit || !window.confirm(`已现场核对机器人在「${name}」的位置和朝向，开始重定位？`))return;
          busy(true);setResult("正在用新的现场扫描验证…");try {await submit(seed);setResult("已收到重定位确认");setSeed(undefined);} catch {setResult("重定位未确认，请核对站位和朝向");} finally {busy(false);} }}>确认并重定位</button></div>}
      {result && <div role="status">{result}</div>}</>}
  </div>;
}
