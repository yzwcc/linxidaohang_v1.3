import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.agibot.lingxinext",
  appName: "灵犀建图与导航",
  webDir: "dist",
  server: {
    androidScheme: "https",
    cleartext: false,
  },
};

export default config;
