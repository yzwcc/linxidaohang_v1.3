import { useEffect, useRef, useState } from "react";
import { Calculator, Check, Download, Plus, Ruler, Trash2 } from "lucide-react";
import type { SavedSlamPointPreview } from "../../shared/contracts";
import { calibrationSurveySchema, calibrationCandidateSchema, calibrationHistorySchema, calibrationReviewSchema,
  type CalibrationCandidate, type CalibrationHistory, type CalibrationSurvey, type SurveyLandmark,
} from "../../shared/calibration";
import { api, jsonBody, type Connection } from "./api";
import LidarCanvas from "./LidarCanvas";

const localTime = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
const issueLabels: Record<CalibrationCandidate["issues"][number], string> = {
  fit_residual: "拟合误差超限", check_residual: "独立检查误差超限",
  scale_out_of_range: "候选尺度或平移超出边界", synthetic_input: "合成数据不可审核为现场候选",
};

export default function CalibrationPanel({ connection, preview }: { connection: Connection; preview: SavedSlamPointPreview }) {
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState<number>();
  const [landmarks, setLandmarks] = useState<SurveyLandmark[]>([]);
  const [label, setLabel] = useState("");
  const [role, setRole] = useState<SurveyLandmark["role"]>("fit");
  const [xyz, setXyz] = useState(["", "", ""]);
  const [method, setMethod] = useState<CalibrationSurvey["method"]>("laser");
  const [measuredAt, setMeasuredAt] = useState(localTime);
  const [operator, setOperator] = useState("");
  const [origin, setOrigin] = useState("");
  const [xAxis, setXAxis] = useState("");
  const [zAxis, setZAxis] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [candidate, setCandidate] = useState<CalibrationCandidate>();
  const [history, setHistory] = useState<CalibrationHistory>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const request = useRef<AbortController | undefined>(undefined);
  const root = `/api/v1/slam/maps/${encodeURIComponent(preview.mapId)}/calibration`;
  const available = Boolean(preview.seedReferences);

  useEffect(() => {
    if (!available) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true);
    void api(connection, root, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setHistory(calibrationHistorySchema.parse(value));
    }).catch((caught) => {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "审核记录读取失败");
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => { controller.abort(); request.current?.abort(); };
  }, [connection, root, available]);

  function invalidate() { setCandidate(undefined); setConfirmed(false); setMessage(""); setError(""); }
  const survey = () => calibrationSurveySchema.safeParse({
    binding: { mapId: preview.mapId, receiptSha256: preview.receiptSha256, pointsSha256: preview.pointsSha256,
      profileSha256: preview.seedReferences?.profileSha256 },
    method, measuredAt: measuredAt && Number.isFinite(Date.parse(measuredAt)) ? new Date(measuredAt).toISOString() : "",
    operator, originDescription: origin, xAxisDescription: xAxis, zAxisDescription: zAxis, landmarks,
  });

  async function calculateOrReview(review: boolean) {
    const parsed = survey();
    if (!parsed.success) { setError("请完整填写测量依据、至少三个拟合点和两个独立检查点"); return; }
    if (review && (!candidate?.reviewable || !confirmed || !reviewer.trim())) return;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api(connection, `${root}/${review ? "review" : "candidate"}`, {
        method: "POST", signal: controller.signal,
        body: jsonBody(review ? { survey: parsed.data, candidateSha256: candidate!.candidateSha256,
          reviewer, acknowledgeCandidateOnly: true } : parsed.data),
      });
      if (controller.signal.aborted) return;
      if (review) {
        const saved = calibrationReviewSchema.parse(result);
        setHistory((old) => ({ binding: saved.candidate.survey.binding,
          reviews: [saved, ...(old?.reviews ?? []).filter((entry) => entry.id !== saved.id)].slice(0, 64) }));
        setConfirmed(false); setMessage("测量候选已审核保存；运行标定未变更，导航未开放");
      } else { setCandidate(calibrationCandidateSchema.parse(result)); setConfirmed(false); }
    } catch (caught) {
      if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : "标定请求失败"); setCandidate(undefined); }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  const selectedPoint = selected === undefined ? undefined : preview.points.slice(selected * 3, selected * 3 + 3);
  function addLandmark(event: React.FormEvent) {
    event.preventDefault();
    if (selected === undefined || !label.trim() || xyz.some((v) => !v.trim() || !Number.isFinite(Number(v))) ||
        landmarks.some((p) => p.pointIndex === selected || p.label === label.trim()) || landmarks.length >= 32) {
      setError("请选择不同的点，填写唯一名称和完整的米制坐标"); return;
    }
    const point: SurveyLandmark = { pointIndex: selected, label: label.trim(), role,
      measured: xyz.map(Number) as [number, number, number] };
    if (point.measured.some((v) => Math.abs(v) > 500)) { setError("测量坐标必须在 ±500 米内"); return; }
    invalidate(); setLandmarks((old) => [...old, point]); setSelected(undefined); setLabel(""); setXyz(["", "", ""]);
  }
  function download(value: unknown, name: string) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = name; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="stack calibration-workspace">
    <LidarCanvas preview={preview} pointSelection={expanded && available ? {
      enabled: !busy, selected, landmarks, onSelect: (index) => { setSelected(index); setError(""); },
    } : undefined} />
    <details className="calibration-panel" onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary><Ruler size={15} />测量标定<span className="tag">候选 / 导航未验证</span></summary>
      {!available ? <div className="notice error">地图缺少传感器配置来源，当前不可标定</div> : <div className="stack">
        <form className="calibration-landmark" onSubmit={addLandmark}>
          <fieldset disabled={busy}>
            <legend>测量地标</legend>
            <div className="mono calibration-raw" role="status">{selectedPoint
              ? `点 #${selected} · ${selectedPoint.map((v) => v.toFixed(4)).join(", ")} raw` : "尚未选择地标"}</div>
            <div className="form-grid">
              <div className="field"><label htmlFor="survey-label">地标名称</label><input id="survey-label" value={label} maxLength={40}
                onChange={(e) => { invalidate(); setLabel(e.target.value); }} required /></div>
              <div className="field"><label htmlFor="survey-role">用途</label><select id="survey-role" value={role}
                onChange={(e) => { invalidate(); setRole(e.target.value as SurveyLandmark["role"]); }}>
                <option value="fit">拟合点</option><option value="check">独立检查点</option></select></div>
              {["X", "Y", "Z"].map((axis, i) => <div className="field" key={axis}><label htmlFor={`survey-${axis}`}>{axis} / m</label>
                <input id={`survey-${axis}`} type="number" step="any" min={-500} max={500} required value={xyz[i]}
                  onChange={(e) => { invalidate(); setXyz((old) => old.map((v, j) => j === i ? e.target.value : v)); }} /></div>)}
              <div className="field"><button className="btn" type="submit" disabled={selected === undefined || landmarks.length >= 32}>
                <Plus size={13} />记录地标</button></div>
            </div>
          </fieldset>
        </form>
        <div className="table-scroll"><table className="table calibration-table"><thead><tr>
          <th>地标</th><th>用途</th><th>实测 X / Y / Z（m）</th><th>误差</th><th /></tr></thead><tbody>
          {landmarks.map((point, i) => <tr key={point.pointIndex}><td>{point.label}</td><td>{point.role === "fit" ? "拟合" : "检查"}</td>
            <td className="mono">{point.measured.map((v) => v.toFixed(3)).join(" / ")}</td>
            <td className="mono">{candidate ? `${(candidate.errors[i] * 1000).toFixed(1)} mm` : "--"}</td>
            <td><button className="btn icon small danger" title={`删除地标 ${point.label}`} disabled={busy}
              onClick={() => { invalidate(); setLandmarks((old) => old.filter((p) => p.pointIndex !== point.pointIndex)); }}><Trash2 size={13} /></button></td></tr>)}
          {!landmarks.length && <tr><td colSpan={5} className="empty">暂无测量记录</td></tr>}
        </tbody></table></div>
        <fieldset disabled={busy} className="calibration-evidence" onChange={invalidate}>
          <legend>测量依据</legend><div className="form-grid">
            <div className="field"><label htmlFor="survey-method">测量工具</label><select id="survey-method" value={method}
              onChange={(e) => setMethod(e.target.value as CalibrationSurvey["method"])}><option value="laser">激光测距</option>
              <option value="tape">卷尺</option><option value="survey_instrument">测量仪器</option></select></div>
            <div className="field"><label htmlFor="survey-time">测量时间</label><input id="survey-time" type="datetime-local" value={measuredAt}
              onChange={(e) => setMeasuredAt(e.target.value)} /></div>
            {[["operator", "测量人", operator, setOperator], ["origin", "坐标原点", origin, setOrigin],
              ["x-axis", "+X 方向依据", xAxis, setXAxis], ["z-axis", "+Z 向上依据", zAxis, setZAxis]].map(([id, title, value, setter]) =>
              <div className="field" key={String(id)}><label htmlFor={`survey-${id}`}>{String(title)}</label>
                <input id={`survey-${id}`} maxLength={200} value={String(value)} onChange={(e) => (setter as (v: string) => void)(e.target.value)} /></div>)}
          </div>
        </fieldset>
        <div className="inline"><button className="btn primary" disabled={busy || !survey().success}
          onClick={() => void calculateOrReview(false)}><Calculator size={14} />计算候选</button>
          {busy && <span role="status">正在校验地图与测量记录</span>}</div>
        {candidate && <div className="stack calibration-result" data-testid="calibration-candidate">
          <div className="inline"><strong>{candidate.reviewable ? "测量几何检查通过" : "候选未通过检查"}</strong>
            <button className="btn icon small" title="导出候选记录" onClick={() => download(candidate, `calibration-${candidate.candidateSha256.slice(0, 12)}.json`)}><Download size={14} /></button></div>
          <dl className="calibration-metrics"><div><dt>候选尺度</dt><dd>{candidate.metresPerMapUnit.toPrecision(7)} m / raw</dd></div>
            <div><dt>拟合 RMSE / 最大</dt><dd>{(candidate.fit.rmseMetres * 1000).toFixed(1)} / {(candidate.fit.maxMetres * 1000).toFixed(1)} mm</dd></div>
            <div><dt>独立检查 RMSE / 最大</dt><dd>{(candidate.check.rmseMetres * 1000).toFixed(1)} / {(candidate.check.maxMetres * 1000).toFixed(1)} mm</dd></div></dl>
          {candidate.issues.map((issue) => <div className="notice error" key={issue}>{issueLabels[issue]}</div>)}
          <details><summary>候选坐标变换</summary><pre className="calibration-matrix">{candidate.navigationFromRaw.map((row) => row.map((v) => v.toFixed(6)).join("  ")).join("\n")}</pre></details>
          <div className="form-grid"><div className="field"><label htmlFor="survey-reviewer">审核人</label>
            <input id="survey-reviewer" value={reviewer} maxLength={200} disabled={busy} onChange={(e) => { setReviewer(e.target.value); setConfirmed(false); }} /></div></div>
          <label className="inline calibration-confirm"><input type="checkbox" checked={confirmed} disabled={busy || !candidate.reviewable}
            onChange={(e) => setConfirmed(e.target.checked)} />仅保存测量候选；不应用运行标定，不开放导航</label>
          <button className="btn" disabled={busy || !candidate.reviewable || !reviewer.trim() || !confirmed}
            onClick={() => void calculateOrReview(true)}><Check size={14} />审核并保存候选</button>
        </div>}
        {error && <div className="notice error" role="alert">{error}</div>}
        {message && <div className="notice" role="status">{message}</div>}
        <div className="calibration-history"><h3>已审核候选</h3>{history?.reviews.length ? history.reviews.map((review) =>
          <div key={review.id} className="calibration-history-row"><span>{new Date(review.reviewedAt).toLocaleString()} · {review.reviewer}</span>
            <span className="mono">{review.candidate.metresPerMapUnit.toPrecision(6)} m / raw</span>
            <button className="btn icon small" title="导出审核记录" onClick={() => download(review, `calibration-review-${review.id}.json`)}><Download size={14} /></button></div>)
          : <div className="muted">暂无审核记录</div>}</div>
      </div>}
    </details>
  </div>;
}
