import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import type { LocalizationState } from "../../shared/localization";
import type { Connection } from "./api";
import { advanceRoute, observeRoute, routeMetrics, routeSample, type RouteCheck, type RouteSample } from "./route-check";

export default function RouteCheckPanel({ state, receivedAt, runtimeKey, mapName, connectionKey }: {
  state?: LocalizationState; receivedAt: number; runtimeKey: string; mapName?: string; connectionKey: Connection;
}) {
  const [check, setCheck] = useState<RouteCheck>();
  const [distance, setDistance] = useState("");
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState(false);
  useEffect(() => { setCheck(undefined); setDistance(""); setError(""); }, [connectionKey]);
  useEffect(() => {
    function refresh() {
      const sample = routeSample(state, receivedAt, performance.now());
      setFresh(Boolean(sample));
      setCheck((value) => value ? observeRoute(value, sample, runtimeKey) : value);
    }
    refresh();
    const timer = window.setInterval(refresh, 150);
    return () => clearInterval(timer);
  }, [state, receivedAt, runtimeKey]);

  const sampleNow = (): RouteSample | undefined => routeSample(state, receivedAt, performance.now());
  function capture() {
    const sample = sampleNow();
    if (!sample || !runtimeKey) { setError("请等待当前地图的实时连续匹配"); return; }
    try {
      setCheck(!check || check.status !== "recording" ? { runtimeKey, mapName, status: "recording", origin: sample }
        : advanceRoute(check, sample, runtimeKey));
      setError("");
    } catch (caught) { setError((caught as Error).message); }
  }
  const measured = distance.trim() ? Number(distance) : undefined;
  const invalidDistance = measured !== undefined && (!Number.isFinite(measured) || measured < .1 || measured > 50);
  const metrics = check ? routeMetrics(check, measured) : undefined;
  const formatted = (value?: number, suffix = "") => value === undefined ? "—" : value.toFixed(3) + suffix;
  function download() {
    if (!check || invalidDistance) return;
    const file = new Blob([JSON.stringify({ schemaVersion: 1, kind: "localization-route-diagnostic",
      ...check, metrics: routeMetrics(check, measured),
      notes: "A–B 为实测直线距离；检查点不能证明中间全程连续。记录不应用标定、不开放导航。" }, null, 2)],
    { type: "application/json" });
    const url = URL.createObjectURL(file), link = document.createElement("a");
    link.href = url; link.download = "lingxi-route-check-" + new Date().toISOString().replace(/[:.]/g, "-") + ".json";
    link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="stack route-check" aria-label="短距离往返验证">
    <h2>短距离往返验证</h2>
    <p className="helper">机器人停稳后依次记录 A、B、返回 A；移动使用外置遥控器。A–B 填两点直线距离，转弯走过的路程不能代替它。</p>
    <div className="field"><label htmlFor="route-distance">A–B 实测直线距离 / m（可留空）</label>
      <input id="route-distance" type="number" min="0.1" max="50" step="any" value={distance}
        onChange={(event) => setDistance(event.target.value)} aria-invalid={invalidDistance} />
    </div>
    <div role="status" data-testid="route-check-status">{!check ? "等待记录 A 点" :
      check.status === "interrupted" ? `本轮中断：${check.interruption}` : check.status === "complete" ? "往返检查点已记录" :
      check.outward ? "B 点已记录，请返回 A 点并恢复初始朝向" : "A 点已记录，请移动到 B 点"}</div>
    <div className="task-controls">
      <button className="btn" disabled={!fresh || !runtimeKey} onClick={capture}>
        {!check || check.status !== "recording" ? "记录 A 点（新一轮）" : check.outward ? "记录返回 A 点" : "记录 B 点"}
      </button>
      <button className="btn icon" title="导出往返验证记录" aria-label="导出往返验证记录" disabled={!check || invalidDistance}
        onClick={download}><Download size={14} /></button>
    </div>
    {check && metrics && <dl className="calibration-metrics" data-testid="route-check-metrics">
      <div><dt>雷达 A–B 空间位移</dt><dd>{formatted(metrics.outwardRaw, " raw")}</dd></div>
      <div><dt>返回起点误差</dt><dd>{formatted(metrics.closureRaw, " raw")}</dd></div>
      <div><dt>雷达返回姿态角差</dt><dd>{formatted(metrics.rotationDegrees, "°")}</dd></div>
      <div><dt>底盘 A–B 平面位移（声明尺度）</dt><dd>{formatted(metrics.outwardBaseMetres, " m")}</dd></div>
      <div><dt>底盘返回误差（声明尺度）</dt><dd>{formatted(metrics.closureBaseMetres, " m")}</dd></div>
      <div><dt>底盘返回朝向差</dt><dd>{formatted(metrics.headingBaseDegrees, "°")}</dd></div>
      <div><dt>本次距离比例参考</dt><dd>{formatted(metrics.measuredMetresPerRawUnit, " m/raw")}</dd></div>
    </dl>}
    {invalidDistance && <p className="helper">实测直线距离请填写 0.1–50 m，或留空。</p>}
    {check && <p className="helper">记录仅保留在当前页面，请导出后再离开。底盘值使用声明尺度；本轮检查不自动应用标定，也不代表导航已验收。</p>}
    {error && <div className="notice error" role="alert">{error}</div>}
  </section>;
}
