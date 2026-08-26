import fs from "node:fs/promises";
import { execFile } from "node:child_process";
import path from "node:path";
import { parse } from "yaml";

const DEFAULT_CONFIG_PATH = "/agibot/nfs/soc0/var/robot_proxy/resources/resource_config.yaml";
const DEFAULT_DURATION_SEC = 30;
const MAX_DURATION_SEC = 300;

type ResourceEntry = {
  resource_key?: unknown;
  current_version?: {
    version?: unknown;
    name?: unknown;
    files?: unknown;
  };
};

export type LinkcraftResource = {
  resource_key: string;
  resource_version: string;
  name: string;
  resource_type: "BODY_MONTION" | "ARM_MONTION";
  duration_sec: number;
  audio_duration_sec: number | null;
  duration_source: "audio" | "default";
  available: boolean;
  configured_files: string[];
};

export async function discoverLocalLinkcraftResources(
  configPath = process.env.LINKCRAFT_RESOURCE_CONFIG_PATH || DEFAULT_CONFIG_PATH
): Promise<LinkcraftResource[]> {
  const document = parse(await fs.readFile(configPath, "utf8")) as { resource_config?: unknown } | null;
  const entries = Array.isArray(document?.resource_config) ? document.resource_config : [];
  const resources = await Promise.all(entries.map(async (rawEntry) => {
    const entry = rawEntry && typeof rawEntry === "object" ? rawEntry as ResourceEntry : {};
    const resourceKey = String(entry.resource_key || "").trim();
    const currentVersion = entry.current_version && typeof entry.current_version === "object"
      ? entry.current_version
      : {};
    const resourceVersion = String(currentVersion.version || "").trim();
    if (!resourceKey.toLowerCase().startsWith("linkcraft_resource_") || !resourceVersion) return null;
    const configuredFiles = Array.isArray(currentVersion.files)
      ? currentVersion.files.map((item) => String(item).trim()).filter(Boolean)
      : [];
    const resolvedPaths = await Promise.all(configuredFiles.map(resolveResourcePath));
    const audioPath = await findAudioFile(resolvedPaths);
    const audioDurationSec = audioPath ? await wavDurationSec(audioPath) : null;
    return {
      resource_key: resourceKey,
      resource_version: resourceVersion,
      name: String(currentVersion.name || resourceKey).trim() || resourceKey,
      resource_type: resourceKey.toLowerCase().includes("onnx") ? "BODY_MONTION" : "ARM_MONTION",
      duration_sec: round(Math.min(audioDurationSec || DEFAULT_DURATION_SEC, MAX_DURATION_SEC)),
      audio_duration_sec: audioDurationSec ? round(audioDurationSec) : null,
      duration_source: audioDurationSec ? "audio" : "default",
      available: resolvedPaths.some((item) => item.exists),
      configured_files: configuredFiles
    } satisfies LinkcraftResource;
  }));
  return resources
    .filter((item): item is LinkcraftResource => item !== null)
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

export async function discoverLinkcraftResourcesAsRobotUser(
  configPath = process.env.LINKCRAFT_RESOURCE_CONFIG_PATH || DEFAULT_CONFIG_PATH
): Promise<LinkcraftResource[]> {
  const pythonPath = process.env.ROBOT_NAV_HOME || "/home/agi/robot_local_navigation_v3";
  const output = await execFileText("runuser", [
    "-u",
    process.env.ROBOT_RESOURCE_USER || "run",
    "--",
    "/usr/bin/python3",
    "-m",
    "robot_local_navigation_v3.linkcraft_resources",
    "--json",
    "--config",
    configPath
  ], {
    ...process.env,
    PYTHONPATH: [pythonPath, process.env.PYTHONPATH || ""].filter(Boolean).join(":"),
  });
  const payload = JSON.parse(output) as { resources?: unknown };
  return Array.isArray(payload.resources) ? payload.resources as LinkcraftResource[] : [];
}

async function resolveResourcePath(rawPath: string): Promise<{ path: string; exists: boolean }> {
  const candidates = [rawPath];
  if (rawPath.startsWith("/agibot/data/var/")) {
    candidates.push(rawPath.replace("/agibot/data/var/", "/agibot/nfs/soc0/var/"));
  }
  if (rawPath.startsWith("/agibot/data/resources/")) {
    candidates.push(rawPath.replace(
      "/agibot/data/resources/",
      "/agibot/nfs/soc0/var/robot_proxy/resources/"
    ));
  }
  for (const candidate of candidates) {
    if (await exists(candidate)) return { path: candidate, exists: true };
  }
  return { path: candidates.at(-1) || rawPath, exists: false };
}

async function findAudioFile(resources: Array<{ path: string; exists: boolean }>): Promise<string | null> {
  for (const resource of resources) {
    if (!resource.exists) continue;
    const stat = await fs.stat(resource.path);
    if (stat.isFile() && path.basename(resource.path).toLowerCase() === "audio.wav") return resource.path;
    if (!stat.isDirectory()) continue;
    const queue = [resource.path];
    while (queue.length) {
      const directory = queue.shift()!;
      for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
        const candidate = path.join(directory, entry.name);
        if (entry.isFile() && entry.name.toLowerCase() === "audio.wav") return candidate;
        if (entry.isDirectory()) queue.push(candidate);
      }
    }
  }
  return null;
}

async function wavDurationSec(audioPath: string): Promise<number | null> {
  try {
    const buffer = await fs.readFile(audioPath);
    if (buffer.length < 12 || buffer.toString("ascii", 0, 4) !== "RIFF" || buffer.toString("ascii", 8, 12) !== "WAVE") {
      return null;
    }
    let offset = 12;
    let byteRate = 0;
    let dataSize = 0;
    while (offset + 8 <= buffer.length) {
      const id = buffer.toString("ascii", offset, offset + 4);
      const size = buffer.readUInt32LE(offset + 4);
      const dataOffset = offset + 8;
      if (id === "fmt " && size >= 12 && dataOffset + 12 <= buffer.length) {
        byteRate = buffer.readUInt32LE(dataOffset + 8);
      }
      if (id === "data") dataSize = Math.min(size, Math.max(0, buffer.length - dataOffset));
      offset = dataOffset + size + (size % 2);
    }
    return byteRate > 0 && dataSize > 0 ? dataSize / byteRate : null;
  } catch {
    return null;
  }
}

async function exists(candidate: string): Promise<boolean> {
  try {
    await fs.access(candidate);
    return true;
  } catch {
    return false;
  }
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function execFileText(file: string, args: string[], env: NodeJS.ProcessEnv): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { env, maxBuffer: 2 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(String(stderr || error.message).trim()));
        return;
      }
      resolve(stdout);
    });
  });
}
