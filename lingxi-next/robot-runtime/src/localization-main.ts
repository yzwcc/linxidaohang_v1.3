import { startLocalizationApi } from "./localization-api";

startLocalizationApi().catch(() => {
  console.error("Lingxi Next localization API startup failed.");
  process.exitCode = 1;
});
