import { useEffect, useRef, useState } from "react";
import { Maximize, Minus, Plus } from "lucide-react";
import type { MapPoint, OccupancyGrid, RobotPose } from "../../shared/contracts";

type Props = {
  grid?: OccupancyGrid;
  pose?: RobotPose;
  points: MapPoint[];
  activeMapName?: string;
  onMapClick?: (point: { x: number; y: number }) => void;
};

export default function MapCanvas({ grid, pose, points, activeMapName, onMapClick }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [revision, setRevision] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | undefined>(undefined);
  const viewRef = useRef<{ grid?: OccupancyGrid; bounds: DOMRect } | undefined>(undefined);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const resize = new ResizeObserver(() => setRevision((value) => value + 1));
    resize.observe(canvas);
    return () => resize.disconnect();
  }, []);
  useEffect(() => { setZoom(1); setPan({ x: 0, y: 0 }); }, [activeMapName]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const bounds = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.floor(bounds.width * dpr));
    canvas.height = Math.max(1, Math.floor(bounds.height * dpr));
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.fillStyle = "#edf1ef";
    context.fillRect(0, 0, bounds.width, bounds.height);

    const padding = 26;
    const availableWidth = Math.max(1, bounds.width - padding * 2);
    const availableHeight = Math.max(1, bounds.height - padding * 2);
    const width = grid?.width ?? 72;
    const height = grid?.height ?? 52;
    const resolution = grid?.resolution && grid.resolution > 0 ? grid.resolution : 0.1;
    const yaw = grid?.originYaw ?? 0;
    const originX = grid?.originX ?? 0;
    const originY = grid?.originY ?? 0;
    const cosine = Math.cos(yaw);
    const sine = Math.sin(yaw);
    const mapWidth = width * resolution;
    const mapHeight = height * resolution;
    const corners = [
      [0, 0],
      [mapWidth, 0],
      [0, mapHeight],
      [mapWidth, mapHeight],
    ].map(([localX, localY]) => ({
      x: originX + cosine * localX - sine * localY,
      y: originY + sine * localX + cosine * localY,
    }));
    const minX = Math.min(...corners.map((corner) => corner.x));
    const maxX = Math.max(...corners.map((corner) => corner.x));
    const minY = Math.min(...corners.map((corner) => corner.y));
    const maxY = Math.max(...corners.map((corner) => corner.y));
    const scale = zoom * Math.min(availableWidth / Math.max(0.01, maxX - minX), availableHeight / Math.max(0.01, maxY - minY));
    const drawWidth = (maxX - minX) * scale;
    const drawHeight = (maxY - minY) * scale;
    const left = (bounds.width - drawWidth) / 2 + pan.x;
    const top = (bounds.height - drawHeight) / 2 + pan.y;
    const toCanvas = (x: number, y: number) => ({
      x: left + (x - minX) * scale,
      y: top + (maxY - y) * scale,
    });

    context.strokeStyle = "rgba(51, 77, 71, 0.08)";
    context.lineWidth = 1;
    for (let x = left; x <= left + drawWidth; x += 24) {
      context.beginPath();
      context.moveTo(x, top);
      context.lineTo(x, top + drawHeight);
      context.stroke();
    }
    for (let y = top; y <= top + drawHeight; y += 24) {
      context.beginPath();
      context.moveTo(left, y);
      context.lineTo(left + drawWidth, y);
      context.stroke();
    }

    if (grid && grid.cells.length === grid.width * grid.height) {
      const image = context.createImageData(grid.width, grid.height);
      for (let row = 0; row < grid.height; row += 1) {
        for (let column = 0; column < grid.width; column += 1) {
          const occupancy = grid.cells[row * grid.width + column];
          const intensity = occupancy < 0 ? 246 : Math.max(24, 244 - Math.min(100, occupancy) * 2.1);
          const offset = ((grid.height - row - 1) * grid.width + column) * 4;
          image.data[offset] = occupancy < 0 ? 235 : intensity;
          image.data[offset + 1] = occupancy < 0 ? 241 : intensity;
          image.data[offset + 2] = occupancy < 0 ? 238 : intensity;
          image.data[offset + 3] = 255;
        }
      }
      const layer = document.createElement("canvas");
      layer.width = grid.width;
      layer.height = grid.height;
      layer.getContext("2d")?.putImageData(image, 0, 0);
      const upperLeft = {
        x: originX - sine * mapHeight,
        y: originY + cosine * mapHeight,
      };
      const upperLeftScreen = toCanvas(upperLeft.x, upperLeft.y);
      context.save();
      context.setTransform(
        dpr * cosine * scale * resolution,
        dpr * -sine * scale * resolution,
        dpr * sine * scale * resolution,
        dpr * cosine * scale * resolution,
        dpr * upperLeftScreen.x,
        dpr * upperLeftScreen.y,
      );
      context.imageSmoothingEnabled = false;
      context.drawImage(layer, 0, 0);
      context.restore();
    }

    for (const point of grid ? points : []) {
      if (activeMapName && point.mapId !== activeMapName) continue;
      const position = grid
        ? toCanvas(point.x, point.y)
        : {
            x: left + drawWidth * (0.18 + (Math.abs(point.x * 0.17) % 0.65)),
            y: top + drawHeight * (0.2 + (Math.abs(point.y * 0.13) % 0.65)),
          };
      context.fillStyle = point.isStart ? "#167c70" : "#dc5b3f";
      context.beginPath();
      context.arc(position.x, position.y, 5, 0, Math.PI * 2);
      context.fill();
    }

    if (pose && grid) {
      const position = grid
        ? toCanvas(pose.x, pose.y)
        : { x: left + drawWidth * 0.5, y: top + drawHeight * 0.55 };
      context.save();
      context.translate(position.x, position.y);
      context.rotate(-pose.yaw);
      context.fillStyle = "#156f66";
      context.beginPath();
      context.moveTo(13, 0);
      context.lineTo(-8, -7);
      context.lineTo(-5, 0);
      context.lineTo(-8, 7);
      context.closePath();
      context.fill();
      context.restore();
    }

    context.strokeStyle = "#cbd5d1";
    context.strokeRect(left, top, drawWidth, drawHeight);
    viewRef.current = { grid, bounds };
  }, [grid, pose, points, activeMapName, revision, zoom, pan]);

  function handleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const view = viewRef.current;
    const grid = view?.grid;
    if (!view || !grid || !onMapClick) return;
    if (dragRef.current?.moved) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const padding = 26;
    const availableWidth = Math.max(1, rect.width - padding * 2);
    const availableHeight = Math.max(1, rect.height - padding * 2);
    const mapWidth = grid.width * grid.resolution;
    const mapHeight = grid.height * grid.resolution;
    const yaw = grid.originYaw ?? 0;
    const corners = [
      [0, 0],
      [mapWidth, 0],
      [0, mapHeight],
      [mapWidth, mapHeight],
    ].map(([localX, localY]) => ({
      x: grid.originX + Math.cos(yaw) * localX - Math.sin(yaw) * localY,
      y: grid.originY + Math.sin(yaw) * localX + Math.cos(yaw) * localY,
    }));
    const minX = Math.min(...corners.map((corner) => corner.x));
    const maxX = Math.max(...corners.map((corner) => corner.x));
    const minY = Math.min(...corners.map((corner) => corner.y));
    const maxY = Math.max(...corners.map((corner) => corner.y));
    const scale = zoom * Math.min(availableWidth / Math.max(0.01, maxX - minX), availableHeight / Math.max(0.01, maxY - minY));
    const left = (rect.width - (maxX - minX) * scale) / 2 + pan.x;
    const top = (rect.height - (maxY - minY) * scale) / 2 + pan.y;
    const worldX = minX + (event.clientX - rect.left - left) / scale;
    const worldY = maxY - (event.clientY - rect.top - top) / scale;
    if (worldX < minX || worldX > maxX || worldY < minY || worldY > maxY) return;
    const localX = Math.cos(yaw) * (worldX - grid.originX) + Math.sin(yaw) * (worldY - grid.originY);
    const localY = -Math.sin(yaw) * (worldX - grid.originX) + Math.cos(yaw) * (worldY - grid.originY);
    if (localX < 0 || localY < 0 || localX > mapWidth || localY > mapHeight) return;
    onMapClick({ x: worldX, y: worldY });
  }

  return (
    <div className="map-canvas-wrap">
      <canvas
        ref={canvasRef}
        onClick={handleClick}
        onPointerDown={(event) => {
          dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag || !event.buttons) return;
          const dx = event.clientX - drag.x;
          const dy = event.clientY - drag.y;
          if (Math.abs(dx) + Math.abs(dy) < 4 && !drag.moved) return;
          drag.moved = true;
          drag.x = event.clientX; drag.y = event.clientY;
          setPan((current) => ({ x: current.x + dx, y: current.y + dy }));
        }}
        className={onMapClick && grid ? "map-clickable" : undefined}
        aria-label="机器人实时栅格地图"
      />
      {grid && <div className="map-tools">
        <button className="btn icon" title="放大地图" onClick={() => setZoom((value) => Math.min(6, value * 1.3))}><Plus size={16} /></button>
        <button className="btn icon" title="缩小地图" onClick={() => setZoom((value) => Math.max(0.5, value / 1.3))}><Minus size={16} /></button>
        <button className="btn icon" title="适应地图" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}><Maximize size={16} /></button>
      </div>}
      {!grid && <span className="map-empty-label">等待 AIMDK 地图数据</span>}
    </div>
  );
}
