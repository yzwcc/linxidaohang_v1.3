import { buildApp } from "./app.js";
import { loadConfig } from "./config.js";
import { startJavaAgentAndRebindRobotVoice } from "./javaAgentControl.js";

const config = loadConfig();
const app = buildApp(config);

try {
  await app.listen({ host: config.host, port: config.port });
  if (config.robot.javaAgentAutoStart) {
    startJavaAgentAndRebindRobotVoice(config)
      .then((status) => {
        app.log.info({
          pid: status.pid,
          lingxiConnected: status.lingxiConnected,
          robotVoice: status.robotVoice
        }, "Java agent auto-start and robot voice rebind finished");
      })
      .catch((error) => {
        app.log.error({ error }, "Java agent auto-start failed");
      });
  }
} catch (error) {
  app.log.error(error);
  process.exit(1);
}
