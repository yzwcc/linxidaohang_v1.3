// Opt-in, bounded timer diagnostics for isolated process-exit tests only.
import { createHook } from "node:async_hooks";

const timers = new Map();
createHook({
  init(id, type, _parent, resource) {
    if (type === "Timeout" && timers.size < 64) {
      timers.set(id, { reference: new WeakRef(resource), stack: new Error("timer-created").stack });
    }
  },
  destroy(id) { timers.delete(id); },
}).enable();
setInterval(() => {
  const active = [];
  for (const { reference, stack } of timers.values()) {
    const timer = reference.deref();
    if (timer?.hasRef()) {
      active.push({ milliseconds: timer._idleTimeout, stack: stack.split("\n").slice(0, 9) });
    }
  }
  process.stderr.write(JSON.stringify({ phase: "isolated-node-exit-probe",
    resources: process.getActiveResourcesInfo(), timers: active }) + "\n");
}, 1000).unref();
