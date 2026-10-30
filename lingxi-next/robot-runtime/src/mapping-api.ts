import { readFileSync } from "node:fs";
import path from "node:path";
import { buildServer } from "../../robot-api/src/server";

export async function startManagedMappingApi(options: { slamConsole?: true } = {}) {
  if (process.platform !== "linux" || process.env.LINGXI_MAPPING_PARENT_STDIN !== "1" ||
      !process.env.LINGXI_MAPPING_SOCKET || !process.env.LINGXI_SAVED_SLAM_DIR ||
      !process.env.LINGXI_DATA_DIR || process.env.LINGXI_SLAM_POINT_FD) {
    throw new Error("explicit_owned_linux_mapping_configuration_required");
  }
  for (const name of ["LINGXI_MAPPING_SOCKET", "LINGXI_SAVED_SLAM_DIR", "LINGXI_DATA_DIR"]) {
    if (!path.isAbsolute(process.env[name]!)) throw new Error("absolute_mapping_runtime_paths_required");
  }
  if (options.slamConsole && (process.env.LINGXI_LOCALIZATION_SERVICE !== "1" ||
      !process.env.LINGXI_LOCALIZATION_SOCKET || !path.isAbsolute(process.env.LINGXI_LOCALIZATION_SOCKET) ||
      process.env.LINGXI_LOCALIZATION_SOCKET === process.env.LINGXI_MAPPING_SOCKET)) {
    throw new Error("distinct_owned_localization_service_required_for_slam_workspace");
  }
  const demo = process.env.LINGXI_DEMO_MODE === "1";
  const key = process.env.LINGXI_TLS_KEY, certificate = process.env.LINGXI_TLS_CERT;
  if (!demo && (!key || !certificate)) throw new Error("production_tls_key_and_certificate_required");
  const port = process.env.LINGXI_PORT ?? (demo ? "3010" : options.slamConsole ? "3443" : "3000");
  if (!/^(0|[1-9][0-9]{0,4})$/.test(port) || Number(port) > 65535) {
    throw new Error("valid_mapping_api_listen_port_required");
  }
  if (options.slamConsole && port === "3000") throw new Error("existing_console_port_reserved");
  const app = await buildServer({
    ...(options.slamConsole ? { slamConsole: true } : { mappingConsole: true }),
    tlsKey: key ? readFileSync(key) : undefined,
    tlsCert: certificate ? readFileSync(certificate) : undefined,
  });
  const stop = () => { void app.close().catch(() => { process.exitCode = 1; }); };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  process.stdin.once("end", stop);
  process.stdin.once("error", stop);
  process.stdin.resume();
  app.addHook("onClose", async () => {
    process.off("SIGINT", stop);
    process.off("SIGTERM", stop);
    process.stdin.off("end", stop);
    process.stdin.off("error", stop);
    process.stdin.destroy();
  });
  try {
    if (process.stdin.destroyed || process.stdin.readableEnded) throw new Error("mapping_owner_closed");
    await app.listen({ host: demo ? "127.0.0.1" : process.env.LINGXI_HOST ?? "0.0.0.0", port: Number(port) });
    const address = app.server.address();
    if (!address || typeof address === "string") throw new Error("mapping_api_network_address_required");
    console.log(JSON.stringify({ service: "lingxi-next", event: "listening", port: address.port }));
    return app;
  } catch (error) {
    await app.close();
    throw error;
  }
}
