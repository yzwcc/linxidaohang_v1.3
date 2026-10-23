import { afterEach, describe, expect, it, vi } from "vitest";
import Fastify from "fastify";

const runRobotSshCommand = vi.fn();
vi.mock("../src/sshControl.js", () => ({
  runRobotSshCommand: (...args: unknown[]) => runRobotSshCommand(...args),
  shellQuote: (value: string) => `'${value.replace(/'/g, "'\\''")}'`
}));

afterEach(() => {
  runRobotSshCommand.mockReset();
  delete process.env.ROBOT_MAPPING_START_COMMAND;
  delete process.env.ROBOT_MAPPING_STOP_COMMAND;
  delete process.env.ROBOT_MAPPING_MAP_TOPIC;
});

describe("mapping API", () => {
  it("serves diagnostics and blocks mapping mutation before any SSH operation", async () => {
    const { getMappingHealth, mappingAdapterUnavailableMessage } = await import("../src/mappingControl.js");
    const app = Fastify();
    app.addHook("preHandler", async (request, reply) => {
      if (request.headers.authorization !== "Bearer test") await reply.code(401).send({ ok: false });
    });
    app.get("/api/robots/default/mapping/health", async () => ({ ok: false }));
    app.post("/api/robots/default/mapping/start", async (_request, reply) =>
      reply.code(409).send({ ok: false, message: mappingAdapterUnavailableMessage() }));
    expect(getMappingHealth).toBeTypeOf("function");
    const denied = await app.inject({ method: "GET", url: "/api/robots/default/mapping/health" });
    expect(denied.statusCode).toBe(401);
    const blocked = await app.inject({
      method: "POST", url: "/api/robots/default/mapping/start",
      headers: { authorization: "Bearer test" }
    });
    expect(blocked.statusCode).toBe(409);
    expect(blocked.json().message).toContain("接口尚未确认");
    expect(runRobotSshCommand).not.toHaveBeenCalled();
    await app.close();
  });
});
