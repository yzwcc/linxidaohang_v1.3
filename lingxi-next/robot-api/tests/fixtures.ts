import { randomUUID } from "node:crypto";
import type { MapPoint, RobotMap, TourTask, VoiceEvent } from "../../shared/contracts";
import { emptyDatabaseState, type DatabaseState } from "../src/domain";
import type { StateStore } from "../src/store";

export const map: RobotMap = { id: "hall", name: "展厅", mapId: 10, active: true };
export const point: MapPoint = { id: "desk", mapId: map.id, name: "接待台", x: 1, y: 2, yaw: 0, isStart: false };
export const startPoint: MapPoint = { ...point, id: "home", name: "入口", x: 0, isStart: true };
export const tour: TourTask = {
  id: "tour", mapId: map.id, name: "展厅导览", repeatCount: 1, idleReturnSeconds: 30,
  steps: [{ id: "step", pointId: point.id, narration: "欢迎参观。", waitSeconds: 0 }],
  updatedAt: new Date().toISOString(),
};

export class MemoryStore implements StateStore {
  state: DatabaseState = { ...emptyDatabaseState(), maps: [map], activeMapId: map.id, points: [point, startPoint], tours: [tour] };
  credentials: Record<string, string> = {};
  async read() { return structuredClone(this.state); }
  async update(mutator: (db: DatabaseState) => DatabaseState) { this.state = mutator(await this.read()); return this.read(); }
  async publicAiSettings() { return { ...this.state.ai, credentialConfigured: !!this.credentials.apiKey, retrieverConfigured: false }; }
  async writeAiSettings(settings: DatabaseState["ai"]) { this.state.ai = settings; }
  async writeCredentials(values: Record<string, string>) { Object.assign(this.credentials, values); }
  async readCredentials() { return { ...this.credentials }; }
}

export function recognition(transcript = "去接待台", overrides: Partial<Extract<VoiceEvent, { kind: "recognition" }>> = {}): VoiceEvent {
  return {
    kind: "recognition", eventId: randomUUID(), sessionId: "session",
    occurredAt: Date.now(), isFinal: true, transcript, confidence: 0.95, ...overrides,
  };
}

export const context = { points: [point, startPoint], tours: [tour], selectedTourId: tour.id, motions: ["挥手"] };
