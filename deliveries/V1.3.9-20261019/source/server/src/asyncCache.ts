type CacheEntry<T> = {
  value: T;
  freshUntil: number;
  staleUntil: number;
};

export class AsyncCache<T> {
  private entry: CacheEntry<T> | null = null;
  private inFlight: Promise<T> | null = null;
  private generation = 0;

  constructor(
    private readonly loader: () => Promise<T>,
    private readonly freshMs: number,
    private readonly staleMs = freshMs * 5
  ) {}

  get(force = false): Promise<T> {
    const now = Date.now();
    if (!force && this.entry?.freshUntil && this.entry.freshUntil > now) {
      return Promise.resolve(this.entry.value);
    }
    if (!force && this.entry?.staleUntil && this.entry.staleUntil > now) {
      void this.refresh().catch(() => undefined);
      return Promise.resolve(this.entry.value);
    }
    return this.refresh();
  }

  invalidate(): void {
    this.generation += 1;
    this.entry = null;
  }

  private refresh(): Promise<T> {
    if (this.inFlight) return this.inFlight;
    const generation = this.generation;
    const request = this.loader();
    this.inFlight = request;
    request.then((value) => {
      if (generation !== this.generation) return;
      const now = Date.now();
      this.entry = {
        value,
        freshUntil: now + this.freshMs,
        staleUntil: now + Math.max(this.freshMs, this.staleMs)
      };
    }).finally(() => {
      if (this.inFlight === request) this.inFlight = null;
    }).catch(() => undefined);
    return request;
  }
}
