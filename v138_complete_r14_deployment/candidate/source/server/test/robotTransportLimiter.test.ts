import { describe, expect, it } from "vitest";
import { withRobotTransportSlot } from "../src/robotTransportLimiter.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("robot transport limiter", () => {
  it("bounds concurrent status probes on the reverse tunnel", async () => {
    let active = 0;
    let maximum = 0;
    await Promise.all(Array.from({ length: 9 }, () => withRobotTransportSlot(async () => {
      active += 1;
      maximum = Math.max(maximum, active);
      await sleep(10);
      active -= 1;
    })));
    expect(maximum).toBe(4);
  });

  it("reserves an additional channel for control operations", async () => {
    let active = 0;
    let maximum = 0;
    const run = (priority = false) => withRobotTransportSlot(async () => {
      active += 1;
      maximum = Math.max(maximum, active);
      await sleep(20);
      active -= 1;
    }, priority);

    await Promise.all([run(), run(true)]);
    expect(maximum).toBe(2);
  });

  it("drops stale queued probes instead of growing an unbounded backlog", async () => {
    const blockers = Array.from({ length: 4 }, () => withRobotTransportSlot(() => sleep(40)));
    await expect(withRobotTransportSlot(async () => undefined, false, 5)).rejects.toThrow("排队超时");
    await Promise.all(blockers);
  });
});
