import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, open, readFile, readdir, realpath, rename, stat, unlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const object = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const text = (value, fallback = "") => typeof value === "string" && value.trim() ? value.trim() : fallback;
const number = (value, fallback = 0) => value === undefined ? fallback : Number(value);
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const key = (map, id) => `${map}:${String(id)}`;

function identifier(value, fallback, label) {
  if (value === undefined || value === null || value === "") {
    if (fallback !== undefined) return fallback;
    throw new Error(`Missing ${label}`);
  }
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return String(value);
  throw new Error(`Invalid ${label}`);
}

function finite(value, label) {
  if (!Number.isFinite(value) || Math.abs(value) > 1000) throw new Error(`Invalid coordinate: ${label}`);
  return value;
}

function startFlag(value, pointId) {
  if (value === undefined || value === null) return false;
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "1" || (typeof value === "string" && ["true", "yes", "on"].includes(value.trim().toLowerCase()))) return true;
  if (value === 0 || value === "0" || (typeof value === "string" && ["false", "no", "off"].includes(value.trim().toLowerCase()))) return false;
  throw new Error(`Invalid start-point flag: ${pointId}`);
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
  const rawMapId = payload.map_id ?? payload.mapId;
  const mapId = rawMapId === undefined || rawMapId === null || rawMapId === ""
    ? undefined
    : typeof rawMapId === "number" && Number.isSafeInteger(rawMapId)
      ? rawMapId
      : typeof rawMapId === "string" && /^\d+$/.test(rawMapId.trim())
        ? Number(rawMapId.trim())
        : Number.NaN;
  if (mapId !== undefined && (!Number.isSafeInteger(mapId) || mapId <= 0)) {
    throw new Error("Invalid legacy map ID");
  }
  const map = { id: mapName, name: mapName, mapId, active: false };
  const warnings = [];
  const rawById = new Map();
  const points = payload.points.map((raw, index) => {
    const item = object(raw);
    const name = text(item.name);
    if (!name || name.length > 80) throw new Error(`Point name missing or too long at index ${index}`);
    const oldId = identifier(item.id, name, `point ID at index ${index}`);
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
      yaw, isStart: startFlag(item.is_start ?? item.isStart ?? item.is_home, oldId),
    };
  });
  if (points.filter((point) => point.isStart).length > 1) throw new Error(`Multiple start points in ${mapName}; resolve before migration`);
  const rawTasks = Array.isArray(payload.tour_tasks) ? payload.tour_tasks :
    payload.tour_task ? [payload.tour_task] : [];
  const taskIds = new Set();
  const tours = rawTasks.map((raw, taskIndex) => {
    const task = object(raw);
    const oldId = identifier(task.id, `task-${taskIndex + 1}`, `tour ID at index ${taskIndex}`);
    const id = key(mapName, oldId);
    if (id.length > 94) throw new Error("Tour ID too long");
    if (taskIds.has(id)) throw new Error("Duplicate tour ID");
    taskIds.add(id);
    const repeatCount = number(task.repeat_count ?? task.repeatCount ?? payload.tour_repeat_count, 1);
    if (!Number.isInteger(repeatCount) || repeatCount < 1 || repeatCount > 20) throw new Error("Tour repeat count outside 1..20");
    let requiresReview = task.enabled === false;
    const steps = (Array.isArray(task.steps) ? task.steps : []).filter((step) => object(step).enabled !== false).map((rawStep, stepIndex) => {
      const step = typeof rawStep === "string" ? { point_id: rawStep } : object(rawStep);
      const oldPointId = identifier(step.point_id ?? step.pointId ?? step.id, undefined, "tour point reference");
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
  const activeTaskId = payload.active_tour_task_id;
  const selectedTourId = activeTaskId === undefined || activeTaskId === null || activeTaskId === "" || activeTaskId === 0
    ? undefined : key(mapName, identifier(activeTaskId, undefined, "active tour ID"));
  if (selectedTourId && !tours.some((tour) => tour.id === selectedTourId)) {
    throw new Error("Active tour ID does not match a migrated task");
  }
  return { map, points, tours, warnings, selectedTourId };
}

async function restrictedWrite(filePath, value) {
  const handle = await open(filePath, "wx", 0o600);
  try { await handle.writeFile(value); await handle.sync(); } finally { await handle.close(); }
}

async function optionalState(statePath) {
  try {
    if (!(await lstat(statePath)).isFile()) throw new Error("Expected a regular state.json file");
    return await readFile(statePath);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return undefined;
  }
}

const stateHash = (bytes) => bytes === undefined ? null : digest(bytes);

async function requireState(statePath, expected, error) {
  if (stateHash(await optionalState(statePath)) !== expected) throw new Error(error);
}

async function editState(target, operation) {
  const lockPath = path.join(target, ".legacy-migration.lock");
  const lock = await open(lockPath, "wx", 0o600).catch((error) => {
    if (error.code === "EEXIST") throw new Error("A migration or rollback is already locked; check the maintenance window");
    throw error;
  });
  try { return await operation(); }
  finally {
    await lock.close();
    await unlink(lockPath);
  }
}

async function replaceState(target, statePath, next, beforePublish) {
  const temporary = path.join(target, `state.${randomUUID()}.tmp`);
  try {
    await restrictedWrite(temporary, next);
    await beforePublish();
    await rename(temporary, statePath);
  } catch (error) {
    await unlink(temporary).catch(() => undefined);
    throw error;
  }
}

export function assignRebuiltMaps(entries, assignments, maps) {
  if (!assignments || Object.getPrototypeOf(assignments) !== Object.prototype) {
    throw new Error("Point assignments must be an object keyed by legacy map name");
  }
  if (Object.keys(assignments).length !== entries.length) throw new Error("Every legacy map needs one assignment");
  for (const entry of entries) {
    const assignment = Object.hasOwn(assignments, entry.map.id) ? assignments[entry.map.id] : undefined;
    if (typeof assignment?.mapId !== "string" || !/^fastlio-[0-9a-f]{32}$/.test(assignment.mapId) ||
        !maps.some((map) => map.id === assignment.mapId)) throw new Error(`Rebuilt map unavailable for ${entry.map.id}`);
    const poses = assignment.points;
    if (!poses || Object.getPrototypeOf(poses) !== Object.prototype || Object.keys(poses).length !== entry.points.length) {
      throw new Error(`Confirm every rebuilt position and yaw for ${entry.map.id}`);
    }
    entry.targetMap = assignment.mapId;
    entry.points = entry.points.map((point) => {
      const pose = Object.hasOwn(poses, point.id) ? poses[point.id] : undefined;
      if (!pose || ![pose.x, pose.y, pose.yaw].every(Number.isFinite) || Math.abs(pose.x) > 1000 ||
          Math.abs(pose.y) > 1000 || Math.abs(pose.yaw) > 2 * Math.PI) throw new Error(`Invalid rebuilt pose for ${point.id}`);
      return { ...point, mapId: assignment.mapId, x: pose.x, y: pose.y, yaw: pose.yaw };
    });
    entry.tours = entry.tours.map((tour) => ({ ...tour, mapId: assignment.mapId, requiresReview: true }));
  }
  const targets = entries.map((entry) => entry.targetMap);
  if (new Set(targets).size !== targets.length) throw new Error("Rebuilt maps must be assigned one-to-one");
}

export async function migrateLegacy({ source, destination, apply = false, beforeCommit, pointAssignments, expectedReview }) {
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
  for (const field of ["points", "tours"]) {
    const ids = entries.flatMap((entry) => entry[field].map((item) => item.id));
    if (new Set(ids).size !== ids.length) throw new Error(`Legacy map names and IDs collide in ${field}`);
  }
  const statePath = path.join(target, "state.json");
  const initialStateBytes = await optionalState(statePath);
  const current = initialStateBytes === undefined ? {
    schemaVersion: 1, robotName: "Lingxi X2", maps: [], points: [], tours: [],
    idleReturnSeconds: 30, jobRecords: [], ai: { provider: "ark", model: "configure-model-id" },
  } : JSON.parse(initialStateBytes.toString("utf8"));
  const beforeSha256 = stateHash(initialStateBytes);
  if (pointAssignments !== undefined) assignRebuiltMaps(entries, pointAssignments, current.maps);
  const reviewSha256 = digest(JSON.stringify({ beforeSha256, maps: entries.map((entry) => ({
    input: entry.sha256, map: entry.map.id, targetMap: entry.targetMap, points: entry.points,
  })) }));
  const report = {
    schemaVersion: 2, applied: false, reviewSha256, mapFilesMoved: 0, knowledgeBaseMigrated: false,
    files: entries.map((entry) => ({
      source: entry.filename, sha256: entry.sha256, map: entry.map.id,
      points: entry.points.length, tours: entry.tours.length, warnings: entry.warnings,
      ...(entry.targetMap ? {
        targetMap: entry.targetMap,
        placements: entry.points.map(({ id, name, x, y, yaw, isStart }) => ({ id, name, x, y, yaw, isStart })),
        tourReviews: entry.tours.map(({ id, name, repeatCount, steps }) => ({ id, name, repeatCount,
          pointOrder: steps.map((step) => step.pointId) })),
      } : { pointIds: entry.points.map(({ id, name }) => ({ id, name })) }),
    })),
  };
  for (const entry of entries) {
    if ((!entry.targetMap && current.maps.some((map) => map.id === entry.map.id)) ||
        current.points.some((point) => entry.points.some((candidate) => candidate.id === point.id) ||
          point.mapId === entry.targetMap && point.isStart && entry.points.some((candidate) => candidate.isStart)) ||
        current.tours.some((tour) => entry.tours.some((candidate) => candidate.id === tour.id))) {
      throw new Error(`Destination already contains ${entry.map.id}; no data overwritten`);
    }
  }
  if (!apply) return report;
  if ((pointAssignments !== undefined || expectedReview !== undefined) && expectedReview !== reviewSha256) {
    throw new Error("Migration review does not match current inputs; preview again before applying");
  }
  await mkdir(target, { recursive: true, mode: 0o700 });
  const stateChanged = "Destination state changed during migration; no concurrent data was overwritten";
  return editState(target, async () => {
    await requireState(statePath, beforeSha256, stateChanged);
    const backup = path.join(target, "backups", `migration-${new Date().toISOString().replace(/[:.]/g, "-")}-${randomUUID()}`);
    await mkdir(backup, { recursive: true, mode: 0o700 });
    for (const [index, entry] of entries.entries()) {
      await restrictedWrite(path.join(backup, `${index + 1}-${path.basename(entry.filename)}`), entry.bytes);
      if (!entry.targetMap) current.maps.push(entry.map);
      current.points.push(...entry.points);
      current.tours.push(...entry.tours);
      if (entry.selectedTourId) current.selectedTourId = entry.selectedTourId;
    }
    if (initialStateBytes !== undefined) await restrictedWrite(path.join(backup, "previous-state.json"), initialStateBytes);
    const next = JSON.stringify(current, null, 2);
    const transaction = { kind: "legacy-migration", beforeSha256, afterSha256: digest(next) };
    // The receipt describes the intended transition; the live hash proves it happened, even if the process died after rename.
    await restrictedWrite(path.join(backup, "receipt.json"), JSON.stringify({ ...report, applied: undefined,
      backupDirectory: backup, transaction }, null, 2));
    await replaceState(target, statePath, next, async () => {
      if (beforeCommit) await beforeCommit();
      for (const entry of entries) {
        if (digest(await readFile(entry.filename)) !== entry.sha256) throw new Error("Legacy source changed during migration");
      }
      await requireState(statePath, beforeSha256, stateChanged);
    });
    return { ...report, applied: true, backupDirectory: backup, transaction };
  });
}

export async function rollbackLegacyMigration({ destination, backupDirectory, apply = false }) {
  const target = path.resolve(destination);
  const backup = path.resolve(backupDirectory);
  if (path.dirname(backup) !== path.join(target, "backups") || !/^migration-[\w-]+$/.test(path.basename(backup)) ||
      (await realpath(backup)) !== path.join(await realpath(target), "backups", path.basename(backup))) {
    throw new Error("Migration backup must be a direct directory in the selected destination's backups");
  }
  const receiptFile = path.join(backup, "receipt.json");
  if (!(await lstat(receiptFile)).isFile()) throw new Error("Invalid migration receipt file");
  const receipt = JSON.parse(await readFile(receiptFile, "utf8"));
  const { kind, beforeSha256, afterSha256 } = receipt.transaction ?? {};
  const valid = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  if (receipt.schemaVersion !== 2 || kind !== "legacy-migration" || receipt.backupDirectory !== backup ||
      !valid(afterSha256) || (beforeSha256 !== null && !valid(beforeSha256))) throw new Error("Invalid or incompatible migration receipt");
  const previous = await optionalState(path.join(backup, "previous-state.json"));
  if (stateHash(previous) !== beforeSha256) throw new Error("Original state backup does not match migration receipt");
  const statePath = path.join(target, "state.json");
  const changed = "Current state differs from the applied migration; refusing to overwrite later edits or undo an unapplied receipt";
  await requireState(statePath, afterSha256, changed);
  const report = { schemaVersion: 2, backupDirectory: backup, applied: false, restoresPreviousState: previous !== undefined };
  if (!apply) return report;
  return editState(target, async () => {
    const migrated = await optionalState(statePath);
    if (stateHash(migrated) !== afterSha256) throw new Error(changed);
    const saved = path.join(backup, `rollback-${randomUUID()}-migrated-state.json`);
    if (previous === undefined) {
      // No state existed originally. Move the migrated file into the backup rather than deleting it.
      await requireState(statePath, afterSha256, changed);
      await rename(statePath, saved);
    } else {
      await restrictedWrite(saved, migrated);
      await replaceState(target, statePath, previous, () => requireState(statePath, afterSha256, changed));
    }
    return { ...report, applied: true, migratedStateBackup: saved };
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { values } = parseArgs({ options: {
      "source-dir": { type: "string" }, "source-file": { type: "string" }, destination: { type: "string" },
      rollback: { type: "string" }, "point-assignments": { type: "string" }, "expect-review": { type: "string" },
      apply: { type: "boolean", default: false },
    } });
    const { "source-dir": sourceDir, "source-file": sourceFile, destination, rollback: backupDirectory,
      "point-assignments": assignments, "expect-review": expectedReview, apply } = values;
    if (Object.values(values).includes("") || !destination || [sourceDir, sourceFile, backupDirectory].filter(Boolean).length !== 1 ||
        (backupDirectory && (assignments !== undefined || expectedReview !== undefined))) throw new Error("Invalid migration arguments");
    const operation = backupDirectory ? rollbackLegacyMigration({ backupDirectory, destination, apply }) :
      (assignments !== undefined ? readFile(assignments, "utf8").then(JSON.parse) : Promise.resolve(undefined))
        .then((pointAssignments) => migrateLegacy({ source: sourceDir ?? sourceFile, destination, apply, pointAssignments, expectedReview }));
    operation.then((report) => console.log(JSON.stringify(report, null, 2)))
      .catch((error) => { console.error(error.message); process.exitCode = 1; });
  } catch (error) {
    console.error(error.message);
    console.error("Usage: node tools/migrate-legacy.mjs --source-dir DIR --destination DIR [--point-assignments FILE] [--expect-review SHA256] [--apply]\n" +
      "       node tools/migrate-legacy.mjs --rollback BACKUP_DIR --destination DIR [--apply]");
    process.exitCode = 2;
  }
}
