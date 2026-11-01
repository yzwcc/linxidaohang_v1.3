import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { BUILD_IDENTITY_FILE, webBuildIdentity } from "./tools/build-identity.mjs";

const packageMetadata = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string };
let identityAtStart: Awaited<ReturnType<typeof webBuildIdentity>> | undefined;

export default defineConfig({
  plugins: [react(), {
    name: "lingxi-source-identity",
    apply: "build",
    async buildStart() { identityAtStart = await webBuildIdentity(process.cwd()); },
    async generateBundle() {
      const identity = await webBuildIdentity(process.cwd());
      if (JSON.stringify(identity) !== JSON.stringify(identityAtStart)) this.error("Source changed during web build");
      this.emitFile({ type: "asset", fileName: BUILD_IDENTITY_FILE, source: JSON.stringify(identity) });
    },
  }],
  define: {
    __APP_VERSION__: JSON.stringify(packageMetadata.version),
  },
  root: "web",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:3010",
        changeOrigin: true,
        ws: true,
      },
      "/health": "http://127.0.0.1:3010",
    },
  },
});
