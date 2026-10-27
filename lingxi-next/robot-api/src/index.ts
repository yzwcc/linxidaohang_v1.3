import { startServer } from "./server";

startServer().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "unknown startup failure";
  console.error(`Lingxi Next API startup failed: ${message}`);
  process.exitCode = 1;
});
