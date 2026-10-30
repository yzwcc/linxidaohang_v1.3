import { startManagedMappingApi } from "./mapping-api";

startManagedMappingApi({ slamConsole: true }).catch(() => {
  console.error("Lingxi Next SLAM workspace API startup failed.");
  process.exitCode = 1;
});
