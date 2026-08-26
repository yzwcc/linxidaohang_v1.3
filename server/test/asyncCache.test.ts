import { describe, expect, it, vi } from "vitest";
import { AsyncCache } from "../src/asyncCache.js";

describe("AsyncCache", () => {
  it("coalesces concurrent reads into one upstream request", async () => {
    let complete: ((value: number) => void) | undefined;
    const loader = vi.fn(() => new Promise<number>((resolve) => { complete = resolve; }));
    const cache = new AsyncCache(loader, 1000);

    const first = cache.get();
    const second = cache.get();
    expect(loader).toHaveBeenCalledTimes(1);

    complete?.(42);
    await expect(first).resolves.toBe(42);
    await expect(second).resolves.toBe(42);
  });

  it("returns stale data immediately while refreshing in the background", async () => {
    vi.useFakeTimers();
    const loader = vi.fn()
      .mockResolvedValueOnce("cached")
      .mockResolvedValueOnce("fresh");
    const cache = new AsyncCache(loader, 1000, 5000);

    await expect(cache.get()).resolves.toBe("cached");
    vi.advanceTimersByTime(1200);
    await expect(cache.get()).resolves.toBe("cached");
    await vi.waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    await expect(cache.get()).resolves.toBe("fresh");
    vi.useRealTimers();
  });

  it("does not publish an in-flight value after invalidation", async () => {
    let complete: ((value: string) => void) | undefined;
    const loader = vi.fn(() => new Promise<string>((resolve) => { complete = resolve; }));
    const cache = new AsyncCache(loader, 1000);

    const request = cache.get();
    cache.invalidate();
    complete?.("old");
    await expect(request).resolves.toBe("old");
    void cache.get();
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
