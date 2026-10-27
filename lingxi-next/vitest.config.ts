import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["robot-api/tests/**/*.test.ts", "tools/**/*.test.ts"],
    testTimeout: 10_000,
    fileParallelism: false,
  },
});
