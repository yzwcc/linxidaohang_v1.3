import { fstatSync, readFileSync } from "node:fs";
import { Socket } from "node:net";
import type { Readable } from "node:stream";
import { buildServer } from "../../robot-api/src/server";
import { SlamPointPipeSource, type PointInputMode } from "../../robot-api/src/slam-points";

export type PointSocketConfiguration = { fd: number; session: string; inputMode: PointInputMode };
type ApiOptions = Omit<NonNullable<Parameters<typeof buildServer>[0]>, "lidarSource">;
export type PointSocketOperations = {
  platform: string;
  isSocket(fd: number): boolean;
  open(fd: number): Readable;
};

const nativeSocket: PointSocketOperations = {
  platform: process.platform,
  isSocket: (fd) => fstatSync(fd).isSocket(),
  open: (fd) => new Socket({ fd, readable: true, writable: false }),
};

export function pointSocketConfiguration(environment: NodeJS.ProcessEnv): PointSocketConfiguration {
  const fd = environment.LINGXI_SLAM_POINT_FD;
  const session = environment.LINGXI_SLAM_SESSION;
  const inputMode = environment.LINGXI_SLAM_INPUT_MODE;
  if (!fd || !/^[1-9][0-9]{0,3}$/.test(fd) || Number(fd) < 3 || Number(fd) > 1023 ||
      !session || !/^[a-f0-9]{32}$/.test(session) ||
      !["live", "recorded", "synthetic"].includes(inputMode ?? "")) {
    throw new Error("explicit_dedicated_point_socket_session_and_input_mode_required");
  }
  return { fd: Number(fd), session, inputMode: inputMode as PointInputMode };
}

export async function buildInheritedPointApi(
  environment: NodeJS.ProcessEnv = process.env,
  options: ApiOptions = {},
  operations: PointSocketOperations = nativeSocket,
) {
  const configuration = pointSocketConfiguration(environment);
  if (operations.platform !== "linux" || !operations.isSocket(configuration.fd)) {
    throw new Error("inherited_linux_point_stream_socket_required");
  }
  const source = new SlamPointPipeSource(
    operations.open(configuration.fd), configuration.session, configuration.inputMode,
  );
  try {
    const app = await buildServer({ ...options, lidarSource: source });
    // Release the owned input before waiting for HTTP/WebSocket shutdown.
    app.addHook("preClose", async () => { source.close(); });
    app.addHook("onClose", async () => { source.close(); });
    return app;
  } catch (error) {
    source.close();
    throw error;
  }
}

export async function startInheritedPointApi() {
  const demo = process.env.LINGXI_DEMO_MODE === "1";
  const key = process.env.LINGXI_TLS_KEY, certificate = process.env.LINGXI_TLS_CERT;
  const portText = process.env.LINGXI_PORT ?? (demo ? "3010" : "3000");
  if (!demo && (!key || !certificate)) throw new Error("production_tls_key_and_certificate_required");
  if (!/^(0|[1-9][0-9]{0,4})$/.test(portText) || Number(portText) > 65535) {
    throw new Error("valid_point_api_listen_port_required");
  }
  const app = await buildInheritedPointApi(process.env, {
    tlsKey: key ? readFileSync(key) : undefined,
    tlsCert: certificate ? readFileSync(certificate) : undefined,
  });
  const stop = () => { void app.close().catch(() => { process.exitCode = 1; }); };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  app.addHook("onClose", async () => {
    process.off("SIGINT", stop);
    process.off("SIGTERM", stop);
  });
  try {
    await app.listen({ host: demo ? "127.0.0.1" : process.env.LINGXI_HOST ?? "0.0.0.0", port: Number(portText) });
    const address = app.server.address();
    if (!address || typeof address === "string") throw new Error("point_api_network_address_required");
    console.log(JSON.stringify({ service: "lingxi-next", event: "listening", port: address.port }));
    return app;
  } catch (error) {
    await app.close();
    throw error;
  }
}
