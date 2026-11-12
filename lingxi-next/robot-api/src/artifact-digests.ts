import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat } from "node:fs/promises";

async function fingerprint(file: string): Promise<string> {
  const stat = await lstat(file, { bigint: true });
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("direct_digest_artifact_required");
  // ctime also invalidates same-size writes whose mtime was restored by a tool.
  return [stat.dev, stat.ino, stat.mode, stat.nlink, stat.uid, stat.gid,
    stat.size, stat.mtimeNs, stat.ctimeNs].join(":");
}

async function hashFile(file: string): Promise<string> {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

/** Reuse SHA256 only while the direct file's complete change identity is stable. */
export class ArtifactDigests {
  private readonly entries = new Map<string, { identity: string; value: Promise<string> }>();

  constructor(private readonly hash = hashFile, private readonly maximumEntries = 4096) {
    if (!Number.isSafeInteger(maximumEntries) || maximumEntries < 1) throw new Error("bounded_digest_cache_required");
  }

  async digest(file: string): Promise<string> {
    const identity = await fingerprint(file);
    const existing = this.entries.get(file);
    if (existing?.identity === identity) {
      this.entries.delete(file);
      this.entries.set(file, existing);
      return existing.value;
    }
    const entry = { identity, value: Promise.resolve("") };
    entry.value = (async () => {
      const value = await this.hash(file);
      if (await fingerprint(file) !== identity) throw new Error("artifact_changed_during_digest");
      return value;
    })();
    this.entries.delete(file);
    this.entries.set(file, entry);
    while (this.entries.size > this.maximumEntries) this.entries.delete(this.entries.keys().next().value!);
    void entry.value.catch(() => {
      if (this.entries.get(file) === entry) this.entries.delete(file);
    });
    return entry.value;
  }
}
