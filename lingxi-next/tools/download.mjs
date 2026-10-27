import { createHash } from "node:crypto";
import { open, readFile, rename, stat } from "node:fs/promises";

export async function downloadVerified(urls, file, algorithm, expected) {
  const checksum = (bytes) => createHash(algorithm).update(bytes).digest("hex");
  const existing = await readFile(file).catch(() => null);
  if (existing) {
    if (checksum(existing) !== expected) throw new Error(`Checksum mismatch: ${file}`);
    return file;
  }
  const partial = `${file}.partial`;
  const saved = await readFile(partial).catch(() => null);
  if (saved && checksum(saved) === expected) {
    await rename(partial, file);
    return file;
  }
  let offset = await stat(partial).then((entry) => entry.size).catch(() => 0);
  let complete = false;
  console.log(`Fetching ${file.split(/[\\/]/).pop()} from byte ${offset}`);
  while (!complete) {
    let received = false;
    let lastError;
    for (const url of Array.isArray(urls) ? urls : [urls]) {
      for (let attempt = 0; attempt < 3 && !received; attempt += 1) {
        try {
          const response = await fetch(url, {
            headers: { Range: `bytes=${offset}-${offset + 4 * 1024 * 1024 - 1}` },
            signal: AbortSignal.timeout(60_000),
          });
          if (![200, 206].includes(response.status)) throw new Error(`HTTP ${response.status}`);
          const range = response.headers.get("content-range")?.match(/^bytes (\d+)-(\d+)\/(\d+)$/);
          if (response.status === 206 && (!range || Number(range[1]) !== offset)) {
            throw new Error("Invalid download range");
          }
          const bytes = Buffer.from(await response.arrayBuffer());
          if (!bytes.length || bytes.length > 512 * 1024 * 1024) throw new Error("Invalid archive size");
          if (range && (bytes.length !== Number(range[2]) - Number(range[1]) + 1 || Number(range[3]) > 512 * 1024 * 1024)) {
            throw new Error("Incomplete download range");
          }
          const output = await open(partial, response.status === 206 && offset ? "a" : "w");
          try { await output.writeFile(bytes); } finally { await output.close(); }
          offset = response.status === 206 ? offset + bytes.length : bytes.length;
          complete = response.status === 200 || offset === Number(range[3]);
          received = true;
        } catch (error) { lastError = error; }
      }
      if (received) break;
    }
    if (!received) throw new Error(`Archive download failed: ${file}`, { cause: lastError });
  }
  if (checksum(await readFile(partial)) !== expected) throw new Error(`Checksum mismatch: ${file}`);
  await rename(partial, file);
  return file;
}
