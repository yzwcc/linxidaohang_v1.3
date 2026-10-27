import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, readdir, rename, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const object = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const text = (value, fallback = "") => typeof value === "string" && value.trim() ? value.trim() : fallback;
const number = (value, fallback = 0) => value === undefined ? fallback : Number(value);
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const key = (map, id) => `${map}:${String(id)}`;

function finite(value, label) {
  if (!Number.isFinite(value) || Math.abs(value) > 1000) throw new Error(`Invalid coordinate: ${label}`);
  return value;
}

function yawOf(pose) {
  if (pose.yaw !== undefined) return finite(Number(pose.yaw), "yaw");
  const q = object(pose.orientation);
  const x = number(q.x ?? pose.qx ?? pose.ox);
  const y = number(q.y ?? pose.qy ?? pose.oy);
  const z = number(q.z ?? pose.qz ?? pose.oz);
  const w = number(q.w ?? pose.qw ?? pose.ow, 1);
  const norm = Math.hypot(x, y, z, w);
  if (!Number.isFinite(norm) || norm < 0.01) throw new Error("Invalid orientation quaternion");
  return Math.atan2(2 * (w * z + x * y) / norm ** 2, 1 - 2 * (y ** 2 + z ** 2) / norm ** 2);
}

export function convertLegacy(payload, sourceName) {
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.points)) {
    throw new Error(`Expected map-associated points JSON: ${sourceName}`);
  }
  const mapName = text(payload.map_name, text(payload.mapName, path.parse(sourceName).name));
  if (!mapName || mapName.length > 64 || /[/\\\0]/.test(mapName)) throw new Error("Invalid map name");
  const rawMapId = number(payload.map_id ?? payload.mapId);
  const map = { id: mapName, name: mapName, mapId: rawMapId > 0 ? rawMapId : undefined, active: false };
  const warnings = [];
  const rawById = new Map();
  const points = payload.points.map((raw, index) => {
    const item = object(raw);
    const name = text(item.name);
    if (!name || name.length > 80) throw new Error(`Point name missing or too long at index ${index}`);
    const oldId = text(item.id, name);
    if (rawById.has(oldId)) throw new Error(`Duplicate point ID: ${oldId}`);
    rawById.set(oldId, item);
    const pose = object(item.pose ?? item);
    const position = object(pose.position ?? pose);
    if (position.x === undefined || position.y === undefined) throw new Error(`Missing coordinates: ${oldId}`);
    if (key(mapName, oldId).length > 100) throw new Error(`Point ID too long: ${oldId}`);
    const yaw = yawOf(pose);
    if (Math.abs(yaw) > Math.PI * 2) throw new Error(`Invalid yaw: ${oldId}`);
    return {
      id: key(mapName, oldId), name, mapId: mapName,
      x: finite(number(position.x), "x"), y: finite(number(position.y), "y"),
      yaw, isStart: Boolean(item.is_start ?? item.isStart ?? item.is_home),
    };
  });
  if (points.filter((point) => point.isStart).length > 1) throw new Error(`Multiple start points in ${mapName}; resolve before migration`);
  const rawTasks = Array.isArray(payload.tour_tasks) ? payload.tour_tasks :
    payload.tour_task ? [payload.tour_task] : [];
  const taskIds = new Set();
  const tours = rawTasks.map((raw, taskIndex) => {
    const task = object(raw);
    const oldId = text(task.id, `task-${taskIndex + 1}`);
    const id = key(mapName, oldId);
    if (id.length > 94) throw new Error("Tour ID too long");
    if (taskIds.has(id)) throw new Error("Duplicate tour ID");
    taskIds.add(id);
    const repeatCount = number(task.repeat_count ?? task.repeatCount ?? payload.tour_repeat_count, 1);
    if (!Number.isInteger(repeatCount) || repeatCount < 1 || repeatCount > 20) throw new Error("Tour repeat count outside 1..20");
    let requiresReview = task.enabled === false;
    const steps = (Array.isArray(task.steps) ? task.steps : []).filter((step) => object(step).enabled !== false).map((rawStep, stepIndex) => {
      const step = typeof rawStep === "string" ? { point_id: rawStep } : object(rawStep);
      const oldPointId = text(step.point_id ?? step.pointId ?? step.id);
      const originalPoint = rawById.get(oldPointId);
      if (!originalPoint) throw new Error(`Unknown tour point reference: ${oldPointId}`);
      const actions = object(originalPoint.business).actions;
      const actionList = Array.isArray(actions) ? actions : [];
      const defaultSpeech = actionList.find((action) => action.type === "tts");
      const defaultMotion = actionList.find((action) => action.type === "motion");
      const defaultExpression = actionList.find((action) => action.type === "emoji");
      const narration = text(step.speech_text ?? step.narration ?? step.text, text(defaultSpeech?.text));
      const motion = step.arrival_motion === null ? undefined : object(step.arrival_motion ?? defaultMotion);
      const expression = step.arrival_emoji === null ? undefined : object(step.arrival_emoji ?? defaultExpression);
      const resource = object(step.linkcraft_action);
      const presetMotion = text(motion?.motion) || undefined;
      const expressionName = text(expression?.emotion) || undefined;
      const resourceId = text(step.linkcraftResourceId ?? resource.resource_id ?? resource.id) || undefined;
      if (narration.length > 4000 || (presetMotion?.length ?? 0) > 100 ||
          (expressionName?.length ?? 0) > 80 || (resourceId?.length ?? 0) > 160) {
        throw new Error(`Tour step exceeds supported limits: ${oldId}/${stepIndex + 1}`);
      }
      const waitSeconds = number(step.waitSeconds ?? step.wait_seconds ?? task.wait_seconds ?? payload.tour_wait_seconds, 0);
      if (!Number.isInteger(waitSeconds) || waitSeconds < 0 || waitSeconds > 300) throw new Error("Tour wait outside 0..300");
      if (presetMotion || expressionName || resourceId || Object.keys(resource).length) requiresReview = true;
      if (actionList.some((action) => !["tts", "motion", "emoji"].includes(action.type))) {
        requiresReview = true;
        warnings.push(`Unsupported arrival action on point ${oldPointId}; original retained in backup`);
      }
      return {
        id: key(id, stepIndex + 1), pointId: key(mapName, oldPointId),
        waitSeconds, narration, presetMotion, expression: expressionName, linkcraftResourceId: resourceId,
      };
    });
    if (!steps.length || steps.length > 200) throw new Error(`Tour ${oldId} must contain 1..200 enabled steps; resolve empty placeholders before migration`);
    if (text(task.name).length > 120) throw new Error("Tour name too long");
    return {
      id, name: text(task.name, `讲解任务 ${taskIndex + 1}`), mapId: mapName, repeatCount, steps,
      idleReturnSeconds: 30, updatedAt: new Date().toISOString(), requiresReview,
    };
  });
  return { map, points, tours, warnings, selectedTourId: payload.active_tour_task_id ? key(mapName, payload.active_tour_task_id) : undefined };
}

async function restrictedWrite(filePath, value) {
  const handle = await open(filePath, "wx", 0o600);
  try { await handle.writeFile(value); await handle.sync(); } finally { await handle.close(); }
}

export async function migrateLegacy({ source, destination, apply = false }) {
  const sourcePath = path.resolve(source);
  const target = path.resolve(destination);
  const metadata = await stat(sourcePath);
  if (sourcePath === target || (!metadata.isDirectory() && path.dirname(sourcePath) === target)) {
    throw new Error("Migration destination must be separate from legacy source");
  }
  const candidates = metadata.isDirectory()
    ? (await readdir(sourcePath)).filter((file) => file.endsWith(".json")).sort().map((file) => path.join(sourcePath, file))
    : [sourcePath];
  if (!candidates.length) throw new Error("No map-associated JSON files found");
  const entries = [];
  for (const filename of candidates) {
    const fileInfo = await stat(filename);
    if (fileInfo.size > 20_000_000) throw new Error("Legacy configuration is too large");
    const bytes = await readFile(filename);
    const converted = convertLegacy(JSON.parse(bytes.toString("utf8")), path.basename(filename));
    entries.push({ filename, bytes, sha256: digest(bytes), ...converted });
  }
  if (new Set(entries.map((entry) => entry.map.id)).size !== entries.length) {
    throw new Error("Duplicate map names in legacy sources");
  }
  const report = {
    schemaVersion: 1, applied: apply, mapFilesMoved: 0, knowledgeBaseMigrated: false,
    files: entries.map((entry) => ({
      source: entry.filename, sha256: entry.sha256, map: entry.map.id,
      points: entry.points.length, tours: entry.tours.length, warnings: entry.warnings,
    })),
  };
  if (!apply) return report;
  await mkdir(target, { recursive: true, mode: 0o700 });
  let current;
  const statePath = path.join(target, "state.json");
  try { current = JSON.parse(await readFile(statePath, "utf8")); }
  catch (error) {
    if (error.code !== "ENOENT") throw error;
    current = {
      schemaVersion: 1, robotName: "Lingxi X2", maps: [], points: [], tours: [],
      idleReturnSeconds: 30, jobRecords: [], ai: { provider: "ark", model: "configure-model-id" },
    };
  }
  for (const entry of entries) {
    if (current.maps.some((map) => map.id === entry.map.id) ||
        current.points.some((point) => entry.points.some((candidate) => candidate.id === point.id))) {
      throw new Error(`Destination already contains ${entry.map.id}; no data overwritten`);
    }
  }
  const backup = path.join(target, "backups", `migration-${new Date().toISOString().replace(/[:.]/g, "-")}-${randomUUID()}`);
  await mkdir(backup, { recursive: true, mode: 0o700 });
  for (const [index, entry] of entries.entries()) {
    await restrictedWrite(path.join(backup, `${index + 1}-${path.basename(entry.filename)}`), entry.bytes);
    // Detect a source change during preparation, before committing new state.
    if (digest(await readFile(entry.filename)) !== entry.sha256) throw new Error("Legacy source changed during migration");
    current.maps.push(entry.map);
    current.points.push(...entry.points);
    current.tours.push(...entry.tours);
    if (entry.selectedTourId) current.selectedTourId = entry.selectedTourId;
  }
  const oldState = await readFile(statePath).catch((error) => { if (error.code !== "ENOENT") throw error; return undefined; });
  if (oldState) await restrictedWrite(path.join(backup, "previous-state.json"), oldState);
  report.backupDirectory = backup;
  await restrictedWrite(path.join(backup, "receipt.json"), JSON.stringify(report, null, 2));
  const temporary = path.join(target, `state.${randomUUID()}.tmp`);
  await restrictedWrite(temporary, JSON.stringify(current, null, 2));
  await rename(temporary, statePath);
  return report;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const option = (name) => args[args.indexOf(name) + 1];
  const source = args.includes("--source-dir") ? option("--source-dir") :
    args.includes("--source-file") ? option("--source-file") : undefined;
  const destination = args.includes("--destination") ? option("--destination") : undefined;
  if (!source || !destination) {
    console.error("Usage: node tools/migrate-legacy.mjs --source-dir DIR --destination DIR [--apply]");
    process.exitCode = 2;
  } else {
    migrateLegacy({ source, destination, apply: args.includes("--apply") })
      .then((report) => console.log(JSON.stringify(report, null, 2)))
      .catch((error) => { console.error(error.message); process.exitCode = 1; });
  }
}
