import { mkdir, open, readFile, rename, stat } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { MapPoint, RobotMap, TourTask } from "../../shared/contracts";
import { emptyDatabaseState, type AiSettings, type DatabaseState } from "./domain";

export interface StateStore {
  read(): Promise<DatabaseState>;
  update(mutator: (current: DatabaseState) => DatabaseState): Promise<DatabaseState>;
  publicAiSettings(): Promise<AiSettings & { credentialConfigured: boolean; retrieverConfigured: boolean }>;
  writeAiSettings(settings: AiSettings): Promise<void>;
  writeCredentials(credentials: Record<string, string>): Promise<void>;
  readCredentials(): Promise<Record<string, string>>;
}

export class JsonStateStore implements StateStore {
  private queue: Promise<unknown> = Promise.resolve();
  private readonly statePath: string;
  private readonly credentialsPath: string;

  constructor(private readonly directory: string) {
    this.statePath = path.join(directory, "state.json");
    this.credentialsPath = path.join(directory, "credentials", "ai.json");
  }

  async read(): Promise<DatabaseState> {
    await this.ensureDirectory();
    try {
      return normalizeState(JSON.parse(await readFile(this.statePath, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      return emptyDatabaseState();
    }
  }

  async update(mutator: (current: DatabaseState) => DatabaseState): Promise<DatabaseState> {
    return this.serial(async () => {
      const current = await this.read();
      const next = normalizeState(mutator(structuredClone(current)));
      await this.writeState(next);
      return next;
    });
  }

  async publicAiSettings() {
    const state = await this.read();
    const credentials = await this.readCredentials();
    return {
      ...state.ai,
      credentialConfigured: Boolean(credentials.apiKey),
      retrieverConfigured: Boolean(credentials.linksoulAppId && credentials.linksoulAppKey && credentials.linksoulAppSecret && credentials.agentCode),
    };
  }

  async writeAiSettings(settings: AiSettings): Promise<void> {
    await this.update((state) => ({ ...state, ai: settings }));
  }

  async writeCredentials(credentials: Record<string, string>): Promise<void> {
    await this.serial(async () => {
      await this.ensureDirectory();
      const current = await this.readCredentials();
      const next = {
        ...current,
        ...Object.fromEntries(
          Object.entries(credentials).filter(([, value]) => typeof value === "string" && value.length > 0),
        ),
      };
      await this.writeRestricted(this.credentialsPath, JSON.stringify(next));
    });
  }

  async readCredentials(): Promise<Record<string, string>> {
    try {
      const parsed = JSON.parse(await readFile(this.credentialsPath, "utf8"));
      return parsed && typeof parsed === "object" ? parsed as Record<string, string> : {};
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
      throw error;
    }
  }

  async listMaps(): Promise<RobotMap[]> {
    return (await this.read()).maps;
  }

  async putMap(map: RobotMap): Promise<void> {
    await this.update((state) => ({
      ...state,
      maps: [...state.maps.filter((item) => item.id !== map.id), map],
    }));
  }

  async setActiveMap(id: string): Promise<void> {
    await this.update((state) => ({
      ...state,
      activeMapId: id,
      maps: state.maps.map((map) => ({ ...map, active: map.id === id })),
    }));
  }

  async addPoint(point: Omit<MapPoint, "id">): Promise<MapPoint> {
    const created = { ...point, id: randomUUID() };
    await this.update((state) => ({
      ...state,
      points: [
        ...state.points.map((item) => point.isStart && item.mapId === point.mapId ? { ...item, isStart: false } : item),
        created,
      ],
    }));
    return created;
  }

  async updatePoint(id: string, point: MapPoint): Promise<void> {
    await this.update((state) => ({
      ...state,
      points: state.points.map((item) => {
        if (item.id === id) return point;
        if (point.isStart && item.mapId === point.mapId) return { ...item, isStart: false };
        return item;
      }),
    }));
  }

  async deletePoint(id: string): Promise<void> {
    await this.update((state) => ({
      ...state,
      points: state.points.filter((point) => point.id !== id),
      tours: state.tours.map((tour) => ({ ...tour, steps: tour.steps.filter((step) => step.pointId !== id) })),
    }));
  }

  async saveTour(tour: TourTask): Promise<void> {
    await this.update((state) => ({
      ...state,
      tours: [...state.tours.filter((item) => item.id !== tour.id), tour],
    }));
  }

  async deleteTour(id: string): Promise<void> {
    await this.update((state) => ({
      ...state,
      tours: state.tours.filter((tour) => tour.id !== id),
      selectedTourId: state.selectedTourId === id ? undefined : state.selectedTourId,
    }));
  }

  async selectTour(id: string): Promise<void> {
    await this.update((state) => ({ ...state, selectedTourId: id }));
  }

  private async ensureDirectory() {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    await mkdir(path.dirname(this.credentialsPath), { recursive: true, mode: 0o700 });
  }

  private async writeState(state: DatabaseState) {
    await this.ensureDirectory();
    await this.writeRestricted(this.statePath, JSON.stringify(state, null, 2));
  }

  private async writeRestricted(target: string, content: string) {
    const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
    const file = await open(temporary, "wx", 0o600);
    try {
      await file.writeFile(content, "utf8");
      await file.sync();
    } finally {
      await file.close();
    }
    await rename(temporary, target);
  }

  private serial<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation, operation);
    this.queue = result.catch(() => undefined);
    return result;
  }
}

function normalizeState(input: unknown): DatabaseState {
  const defaults = emptyDatabaseState();
  if (!input || typeof input !== "object") return defaults;
  const value = input as Partial<DatabaseState>;
  return {
    ...defaults,
    ...value,
    maps: Array.isArray(value.maps) ? value.maps : [],
    points: Array.isArray(value.points) ? value.points : [],
    tours: Array.isArray(value.tours) ? value.tours : [],
    jobRecords: Array.isArray(value.jobRecords) ? value.jobRecords.slice(-500) : [],
    idleReturnSeconds: Number.isInteger(value.idleReturnSeconds) ? value.idleReturnSeconds! : 30,
    ai: value.ai && typeof value.ai === "object" ? { ...defaults.ai, ...value.ai } : defaults.ai,
  };
}

async function exists(file: string): Promise<boolean> {
  try {
    const metadata = await stat(file);
    return metadata.isFile();
  } catch {
    return false;
  }
}
