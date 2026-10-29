/** Offline recorded-input diagnostic, never a production or live source selector. */
import { createHash, randomBytes } from "node:crypto";
import { once } from "node:events";
import { chmodSync, existsSync, statSync, writeFileSync } from "node:fs";
import { createServer, type Socket } from "node:net";
import path from "node:path";
import { PassThrough, type Readable } from "node:stream";
import { pathToFileURL } from "node:url";
import { DemoAdapter } from "../robot-api/src/adapters/demo";
import { pairingCodeHash } from "../robot-api/src/auth";
import { buildServer } from "../robot-api/src/server";
import { SlamPointPipeSource } from "../robot-api/src/slam-points";

export class RecordedPointAudit extends SlamPointPipeSource {
  records = 0;
  copiedPoints = 0;
  firstNativeFrameId?: number;
  lastNativeFrameId = 0;
  retired = 0;
  private readonly digest = createHash("sha256");

  constructor(pipe: Readable, session: string, clock?: () => number) {
    super(pipe, session, "recorded", clock);
  }

  override offer(payload: Buffer) {
    if (this.records >= 20_000) {
      this.invalidate();
      throw new Error("bounded_recorded_point_count_required");
    }
    const accepted = super.offer(payload);
    if (!accepted) { this.retired += 1; return false; }
    const id = Number(payload.readBigUInt64LE(24));
    if (id !== this.lastNativeFrameId + 1) {
      this.invalidate();
      throw new Error("complete_recorded_point_sequence_required");
    }
    const metadata = Buffer.alloc(24);
    metadata.writeBigUInt64LE(BigInt(id), 0);
    metadata.writeDoubleLE(payload.readDoubleLE(32), 8);
    metadata.writeBigUInt64LE(BigInt(payload.readUInt32LE(16)), 16);
    this.digest.update(metadata).update(payload.subarray(192)).update(payload.subarray(64, 192));
    this.records += 1;
    this.copiedPoints += payload.readUInt32LE(12);
    this.firstNativeFrameId ??= id;
    this.lastNativeFrameId = id;
    return true;
  }

  report() {
    return {
      records: this.records, copiedPoints: this.copiedPoints, retired: this.retired,
      firstNativeFrameId: this.firstNativeFrameId, lastNativeFrameId: this.lastNativeFrameId,
      allPacketBytesSha256: this.digest.digest("hex"), inputMode: "recorded" as const,
    };
  }
}

class OfflineAdapter extends DemoAdapter {
  override async getSnapshot() {
    const state = await super.getSnapshot();
    return { ...state, connected: false, checks: state.checks.map((check) => ({ ...check, ready: false })) };
  }
  override async listMaps() { return []; }
  override async getLidarFrame(): Promise<never> { throw new Error("offline_adapter_has_no_sensor_data"); }
}

export async function verifyRecordedApi(
  source: RecordedPointAudit, directory: string, ended: Promise<boolean>,
  trace: (phase: string) => void = () => undefined,
) {
  const code = randomBytes(24).toString("hex");
  const app = await buildServer({
    demoMode: true, adapter: new OfflineAdapter(), lidarSource: source,
    stateDirectory: path.join(directory, "private-api-state"), staticDirectory: path.join(directory, "no-ui"),
    pairingCodeHash: pairingCodeHash(code),
    agentSpeaker: { speak: async () => { throw new Error("offline_diagnostic_has_no_speech"); },
      cancel: async () => undefined },
  });
  trace("app_created");
  let client: Awaited<ReturnType<typeof app.injectWS>> | undefined;
  try {
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: code },
    });
    if (paired.statusCode !== 201) throw new Error("isolated_api_pairing_required");
    const token: string = paired.json().token;
    const headers = { authorization: `Bearer ${token}` };
    const before = (await app.inject({ url: "/api/v1/state", headers })).json();
    if (before.checks.some((check: { ready: boolean }) => check.ready)) {
      throw new Error("offline_api_must_not_grant_readiness");
    }
    await app.ready();
    client = await app.injectWS("/api/v1/lidar/events", {
      headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
    });
    trace("socket_opened");
    const next = async () => JSON.parse((await once(client!, "message"))[0].toString());
    const during = await next();
    trace("first_unavailable");
    if (during.type !== "lidar-unavailable" || during.frame) throw new Error("recorded_input_cannot_be_live");
    if (!await ended) throw new Error("complete_recorded_point_pipe_eof_required");
    trace("source_ended");
    let rejected = false;
    try { await source.getLidarFrame(); } catch { rejected = true; }
    const afterEof = await next();
    trace("after_eof_unavailable");
    const after = (await app.inject({ url: "/api/v1/state", headers })).json();
    const unchanged = JSON.stringify([before.checks, before.mapping, before.navigation]) ===
      JSON.stringify([after.checks, after.mapping, after.navigation]);
    if (!rejected || !unchanged || afterEof.type !== "lidar-unavailable" || afterEof.frame) {
      throw new Error("recorded_api_isolation_and_closed_pipe_required");
    }
    return {
      pairedWebSocketChecked: true, recordedInputRejectedAsLive: true,
      beforeAndAfterEofUnavailable: true, readinessUnchanged: true,
      robotCommandsSent: false, tcpListenerStarted: false,
    };
  } finally {
    trace("closing_peers");
    // injectWS uses paired in-memory streams; close both owned peers explicitly.
    for (const peer of app.websocketServer.clients) peer.terminate();
    client?.terminate();
    trace("closing_app");
    await app.close();
    trace("closed_app");
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
}

export function validateDirectory(directory: string, socketPath: string) {
  const parent = path.resolve(directory), socket = path.resolve(socketPath);
  const info = statSync(parent);
  if (process.platform !== "linux" || !info.isDirectory() || info.uid !== process.getuid!() ||
      (info.mode & 0o077) !== 0 || path.dirname(socket) !== parent || existsSync(socket)) {
    throw new Error("new_private_owned_linux_point_socket_required");
  }
}

export async function consume(directory: string, socketPath: string, session: string) {
  validateDirectory(directory, socketPath);
  if (!/^[a-f0-9]{32}$/.test(session)) throw new Error("explicit_recorded_session_required");
  let input: Socket | undefined;
  const bridge = new PassThrough();
  const source = new RecordedPointAudit(bridge, session);
  let finish: (value: unknown) => void, fail: (error: Error) => void;
  const completed = new Promise((resolve, reject) => { finish = resolve; fail = reject; });
  // Install a rejection handler immediately, including while waiting for listen().
  void completed.catch(() => undefined);
  let closeInput!: (eof: boolean) => void, ready!: () => void;
  const ended = new Promise<boolean>((resolve) => { closeInput = resolve; });
  const apiReady = new Promise<void>((resolve) => { ready = resolve; });
  bridge.once("close", () => {
    if (!input?.readableEnded) { input?.destroy(); closeInput(false); }
  });
  void verifyRecordedApi(source, directory, ended, (phase) => {
    process.stderr.write(`point_consumer_phase=${phase}\n`);
    if (phase === "first_unavailable") ready();
  }).then((api) => {
    const points = source.report();
    if (!points.records || points.retired) throw new Error("nonempty_complete_recorded_points_required");
    finish!({
      schemaVersion: 1, result: "recorded_native_points_received_api_live_guard_checked",
      host: { machine: process.arch, node: process.version }, points, api,
      transport: "private_same_host_unix_stream", inputMode: "recorded",
    });
  }).catch((error: unknown) => fail!(error instanceof Error ? error : new Error("consumer_failed")));
  const server = createServer((socket) => {
    if (input) { socket.destroy(); return; }
    input = socket;
    server.close();
    let eof = false;
    socket.once("end", () => { eof = true; closeInput(true); });
    socket.once("close", () => closeInput(eof));
    socket.once("error", () => closeInput(false));
    socket.pipe(bridge);
  });
  server.once("error", (error) => fail!(error));
  const deadline = setTimeout(() => fail!(new Error("recorded_point_consumer_deadline_exceeded")), 120_000);
  try {
    await Promise.race([apiReady, completed]);
    server.listen(socketPath);
    await once(server, "listening");
    chmodSync(socketPath, 0o600);
    process.stdout.write("ready\n");
    const result = await completed;
    writeFileSync(path.join(directory, "consumer-report.json"), JSON.stringify(result, null, 2) + "\n",
      { flag: "wx", mode: 0o600 });
    return result;
  } finally {
    clearTimeout(deadline);
    source.close();
    input?.destroy();
    if (server.listening) server.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const [directory, socketPath, session] = process.argv.slice(2);
  void consume(directory, socketPath, session).catch(() => {
    process.stderr.write("recorded_point_api_consumer_failed\n");
    process.exitCode = 1;
  });
}
