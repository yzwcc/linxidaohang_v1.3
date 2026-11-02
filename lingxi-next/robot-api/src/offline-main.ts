import { buildServer } from "./server";
import { StdioOfflineNavigationBackend } from "./offline-navigation-backend";
import { checkOfflineWorkspace, offlineEnvironment, offlineWorkspacePlan, startOfflineWorkspace } from "./offline-workspace";

const plan = offlineWorkspacePlan(process.env);
process.env = offlineEnvironment(process.env);
const controller = new AbortController();
let workspace: Awaited<ReturnType<typeof startOfflineWorkspace>> | undefined;
const signals = ["SIGINT", "SIGTERM"] as const;
const removeSignals = () => { for (const event of signals) process.removeListener(event, stop); };
function stop() {
  controller.abort(new Error("offline_workspace_interrupted"));
  if (workspace) void workspace.close().catch(() => { process.exitCode = 1; }).finally(removeSignals);
}
for (const event of signals) process.once(event, stop);
try {
  workspace = await startOfflineWorkspace(plan, {
    check: checkOfflineWorkspace,
    backend: (value) => new StdioOfflineNavigationBackend(value.executable, value.args, value.cwd, value.environment),
    app: (backend, value) => buildServer({ offlineNavigationBackend: backend,
      stateDirectory: value.stateDirectory, staticDirectory: value.staticDirectory, internalAgentToken: "" }),
  }, controller.signal);
  console.info(`Offline Nav2 workspace: http://127.0.0.1:${plan.port} (synthetic inputs, no robot IO)`);
} catch (error) {
  removeSignals();
  if (!controller.signal.aborted || error !== controller.signal.reason) {
    console.error(error instanceof Error ? error.message : "offline_workspace_start_failed");
    process.exitCode = 1;
  }
}
