import { startInheritedPointApi } from "./point-api";

startInheritedPointApi().catch(() => {
  console.error("Lingxi Next inherited-point API startup failed.");
  process.exitCode = 1;
});
