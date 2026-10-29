import { startManagedMappingApi } from "./mapping-api";

startManagedMappingApi().catch(() => {
  console.error("Lingxi Next managed-mapping API startup failed.");
  process.exitCode = 1;
});
