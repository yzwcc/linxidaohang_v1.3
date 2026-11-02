import type { LidarFrame, MapPoint, RobotMap } from "../../../shared/contracts";
import {
  navigationAdmission,
  physicalNavigationStateSchema,
} from "../../../shared/physical-navigation";
import type {
  PhysicalNavigationMapActivation,
  PhysicalNavigationCommand,
  PhysicalNavigationState,
} from "../../../shared/physical-navigation";
import type { RobotAdapter, RobotSnapshot } from "../domain";
import type { PhysicalNavigationBackend } from "../physical-navigation-backend";

const unavailable = (message: string, statusCode = 409) => Object.assign(new Error(message), { statusCode });

function sameIdentity(a: PhysicalNavigationState["identity"], b: PhysicalNavigationState["identity"]) {
  return a.mapId === b.mapId && a.receiptSha256 === b.receiptSha256 &&
    a.calibrationSha256 === b.calibrationSha256 && a.footprintSha256 === b.footprintSha256;
}

function navigationState(state: PhysicalNavigationState): RobotSnapshot["navigation"] {
  const status = state.phase === "stopping" ? "requested" : state.phase;
  return {
    status,
    taskId: state.ticket?.task_id,
    pointId: state.pointId ?? undefined,
    requiresStop: state.requiresStop,
    detail: state.detail,
  };
}

/**
 * Composes the independently-built mapping/localization adapter with the
 * owned ROS 2/Nav2 bridge. Mapping remains remote-control-only; this wrapper
 * only exposes navigation after every physical readiness gate is true.
 */
export class PhysicalNavigationAdapter implements RobotAdapter {
  readonly demoMode = false;
  private previous?: PhysicalNavigationState;
  private uncertain = false;
  private lastError = "physical_navigation_bridge_unavailable";
  private tail: Promise<void> = Promise.resolve();

  constructor(
    private readonly delegate: RobotAdapter,
    private readonly backend: PhysicalNavigationBackend,
  ) {
    if (backend.physical !== true) throw new Error("physical_navigation_backend_required");
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.tail.then(operation);
    this.tail = result.then(() => undefined, () => undefined);
    return result;
  }

  private async readState(options: { allowReseed?: boolean } = {}): Promise<PhysicalNavigationState> {
    try {
      const state = physicalNavigationStateSchema.parse(await this.backend.state());
      if (!options.allowReseed && this.previous && (state.session !== this.previous.session ||
          state.sequence <= this.previous.sequence || !sameIdentity(state.identity, this.previous.identity) ||
          (state.ticket && (state.ticket.task_id !== this.previous.ticket?.task_id ||
            state.ticket.epoch !== this.previous.ticket?.epoch)))) {
        throw unavailable("physical_navigation_state_changed_without_reseed");
      }
      this.previous = state;
      return state;
    } catch (error) {
      this.uncertain = true;
      this.lastError = error instanceof Error ? error.message : "physical_navigation_state_invalid";
      throw error;
    }
  }

  private async command(value: PhysicalNavigationCommand) {
    if (this.uncertain && value.action !== "stop") {
      throw unavailable("physical_navigation_requires_confirmed_stop_after_uncertain_state");
    }
    try {
      const result = physicalNavigationStateSchema.parse(await this.backend.command(value));
      if (result.session !== value.session || !sameIdentity(result.identity, value.evidence) ||
          (this.previous && result.sequence <= this.previous.sequence)) {
        throw unavailable("physical_navigation_command_ack_identity_mismatch");
      }
      const sameTask = result.ticket?.task_id === value.taskId && result.pointId === value.pointId;
      const newTask = sameTask && result.ticket?.epoch !== value.expectedEpoch &&
        ["requested", "running", "finished"].includes(result.phase) && !result.requiresStop;
      const paused = sameTask && result.ticket?.epoch === value.expectedEpoch &&
        result.phase === "paused" && result.outputQuiescent && !result.requiresStop;
      const stopped = result.outputQuiescent && !result.requiresStop &&
        ["idle", "finished", "failed"].includes(result.phase) &&
        (!result.ticket || (result.ticket.task_id === value.taskId && result.ticket.epoch === value.expectedEpoch));
      if (!(value.action === "stop" ? stopped : value.action === "pause" ? paused : newTask)) {
        throw unavailable("physical_navigation_command_unconfirmed");
      }
      this.previous = result;
      if (value.action === "stop") this.uncertain = false;
      return result;
    } catch (error) {
      this.uncertain = true;
      this.lastError = error instanceof Error ? error.message : "physical_navigation_command_unconfirmed";
      throw error;
    }
  }

  getSnapshot(): Promise<RobotSnapshot> {
    return this.serialized(() => this.snapshot());
  }

  private async snapshot(): Promise<RobotSnapshot> {
    const original = await this.delegate.getSnapshot();
    let state: PhysicalNavigationState;
    try { state = await this.readState(); }
    catch {
      return {
        ...original,
        navigation: { status: "failed", requiresStop: true, detail: this.lastError },
        navigationExecution: { backend: "nav2-ros2", syntheticInputs: false,
          physicalMotionSent: this.previous?.physicalMotionSent ?? false, navigationReady: false },
        checks: original.checks.map((check) => check.id === "navigation"
          ? { ...check, ready: false, detail: this.lastError } : check),
      };
    }
    const admission = this.uncertain ? { ok: false as const, reason: "physical_navigation_state_uncertain" } : navigationAdmission(state);
    const ready = admission.ok;
    return {
      ...original,
      pose: state.pose,
      navigation: this.uncertain
        ? { status: "failed", requiresStop: true, detail: this.lastError }
        : navigationState(state),
      navigationExecution: {
        backend: "nav2-ros2", syntheticInputs: false,
        physicalMotionSent: state.physicalMotionSent, navigationReady: ready,
      },
      checks: original.checks.map((check) => check.id === "navigation"
        ? { ...check, ready, detail: ready ? "ROS 2 Nav2 admission verified" : admission.reason }
        : check.id === "odom" ? { ...check, ready: state.checks.localization && state.checks.tf,
          detail: state.checks.localization && state.checks.tf ? "Localization and TF fresh" : "Localization/TF freshness gate failed" }
        : check),
    };
  }

  getLidarFrame?(): Promise<LidarFrame> {
    if (!this.delegate.getLidarFrame) return Promise.reject(unavailable("lidar_source_unavailable", 503));
    return this.delegate.getLidarFrame();
  }
  listMaps() { return this.delegate.listMaps(); }
  startMapping() { return this.delegate.startMapping(); }
  stopAndSaveMap(name: string) { return this.delegate.stopAndSaveMap(name); }
  getStoredMapGrid(map: RobotMap) { return this.delegate.getStoredMapGrid(map); }
  activateMap(map: RobotMap, initialPose?: { x: number; y: number; yaw: number }) {
    return this.serialized(() => this.activatePhysicalMap(map, initialPose));
  }

  private async activatePhysicalMap(map: RobotMap, initialPose?: { x: number; y: number; yaw: number }) {
    if (this.uncertain) throw unavailable("physical_navigation_requires_confirmed_stop_after_uncertain_state");
    const state = await this.readState();
    if (state.requiresStop || !state.outputQuiescent ||
        !["idle", "finished", "failed"].includes(state.phase)) {
      throw unavailable("physical_navigation_output_not_confirmed_idle");
    }
    if (!this.backend.activateMap) {
      // A bridge without an activation endpoint can still confirm an already
      // selected map, but it may not silently select a different map.
      if (state.identity.mapId !== map.id) throw unavailable("physical_navigation_map_identity_mismatch");
      if (initialPose) throw unavailable("physical_navigation_map_activation_unavailable");
      this.uncertain = false;
      return;
    }
    const request: PhysicalNavigationMapActivation = {
      action: "activate_map", session: state.session, mapId: map.id,
      ...(state.identity.mapId === map.id ? { evidence: state.identity } : {}),
      ...(initialPose ? { initialPose } : {}),
    };
    try {
      const result = physicalNavigationStateSchema.parse(await this.backend.activateMap(request));
      if (result.session !== state.session || result.sequence <= state.sequence ||
          result.identity.mapId !== map.id ||
          result.requiresStop || !result.outputQuiescent || result.ticket || result.phase !== "idle" ||
          (state.identity.mapId === map.id && !sameIdentity(result.identity, state.identity))) {
        throw unavailable("physical_navigation_map_activation_identity_mismatch");
      }
      this.previous = result;
      this.uncertain = false;
    } catch (error) {
      this.uncertain = true;
      this.lastError = error instanceof Error ? error.message : "physical_navigation_map_activation_unconfirmed";
      throw error;
    }
  }

  startNavigation(point: MapPoint, map: RobotMap, taskId: string) {
    return this.serialized(() => this.startPhysicalNavigation(point, map, taskId));
  }

  private async startPhysicalNavigation(point: MapPoint, map: RobotMap, taskId: string) {
    if (this.uncertain) throw unavailable("physical_navigation_requires_confirmed_stop_after_uncertain_state");
    const state = await this.readState();
    const admission = navigationAdmission(state);
    if (!admission.ok) throw unavailable(admission.reason);
    if (state.identity.mapId !== map.id || state.identity.mapId !== point.mapId) {
      throw unavailable("physical_navigation_map_identity_mismatch");
    }
    if (!state.outputQuiescent || !["idle", "finished", "failed"].includes(state.phase)) {
      throw unavailable("physical_navigation_task_already_active");
    }
    await this.command({
      action: "start", session: state.session,
      expectedEpoch: state.ticket ? (state.ticket as { epoch: string }).epoch : null,
      taskId, mapId: point.mapId, pointId: point.id,
      goal: { x: point.x, y: point.y, yaw: point.yaw }, evidence: state.identity,
    });
  }

  controlNavigation(action: "pause" | "resume" | "stop", taskId?: string) {
    return this.serialized(() => this.controlPhysicalNavigation(action, taskId));
  }

  private async controlPhysicalNavigation(action: "pause" | "resume" | "stop", taskId?: string) {
    const state = await this.readState({ allowReseed: action === "stop" });
    if (action !== "stop") {
      const admission = navigationAdmission(state);
      if (!admission.ok) throw unavailable(admission.reason);
      if (!state.ticket || (action === "pause"
        ? !["requested", "running"].includes(state.phase) : state.phase !== "paused")) {
        throw unavailable("physical_navigation_task_not_controllable");
      }
    }
    if (state.ticket && taskId && state.ticket.task_id !== taskId) {
      throw unavailable("physical_navigation_task_epoch_mismatch");
    }
    const commandTaskId = state.ticket?.task_id ?? taskId ?? "operator-stop";
    await this.command({
      action, session: state.session,
      expectedEpoch: state.ticket ? (state.ticket as { epoch: string }).epoch : null,
      taskId: commandTaskId, mapId: state.identity.mapId,
      ...(state.pointId ? { pointId: state.pointId } : {}), evidence: state.identity,
    });
  }

  confirmActionsIdle() {
    return this.serialized(() => this.confirmPhysicalIdle());
  }

  private async confirmPhysicalIdle() {
    if (this.uncertain) throw unavailable("physical_navigation_requires_confirmed_stop_after_uncertain_state");
    const state = await this.readState();
    if (state.requiresStop || !state.outputQuiescent || !["idle", "finished", "failed"].includes(state.phase)) {
      throw unavailable("physical_navigation_output_not_confirmed_idle");
    }
  }

  startPresetMotion(name: string, monitorCompletion?: boolean) { return this.delegate.startPresetMotion(name, monitorCompletion); }
  waitPresetMotion(taskId: string) { return this.delegate.waitPresetMotion(taskId); }
  playExpression(name: string) { return this.delegate.playExpression(name); }
  executeLinkcraftResource(id: string) { return this.delegate.executeLinkcraftResource(id); }

  close() { return this.backend.close(); }
}
