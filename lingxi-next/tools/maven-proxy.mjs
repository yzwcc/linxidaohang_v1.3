import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

// Build-only transport workaround: external HTTPS remains certificate-verified.
// The listening socket is loopback-only and forwards only to official public repos.
export async function startMavenProxy(directory) {
  const pending = new Map();
  const origins = {
    google: "https://dl.google.com/dl/android/maven2/",
    central: "https://repo.maven.apache.org/maven2/",
  };
  async function load(repository, relative) {
    const file = path.join(directory, repository, relative);
    const cached = await readFile(file).catch(() => null);
    if (cached) return { code: 200, bytes: cached };
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        const response = await fetch(`${origins[repository]}${relative}`, { signal: AbortSignal.timeout(30_000) });
        if (response.status === 404) return { code: 404 };
        if (!response.ok) throw new Error("Upstream unavailable");
        const bytes = Buffer.from(await response.arrayBuffer());
        if (bytes.length > 64 * 1024 * 1024) throw new Error("Artifact too large");
        await mkdir(path.dirname(file), { recursive: true });
        await writeFile(file, bytes);
        return { code: 200, bytes };
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
      }
    }
    return { code: 502 };
  }
  const server = createServer(async (request, response) => {
    const match = request.url?.match(/^\/(google|central)\/([A-Za-z0-9_./+@-]+)$/);
    if (!["GET", "HEAD"].includes(request.method) || !match || match[2].split("/").includes("..")) {
      response.writeHead(404).end(); return;
    }
    const key = `${match[1]}/${match[2]}`;
    try {
      if (!pending.has(key)) pending.set(key, load(match[1], match[2]));
      const result = await pending.get(key);
      response.writeHead(result.code, result.bytes ? { "Content-Length": result.bytes.length } : {});
      response.end(request.method === "HEAD" ? undefined : result.bytes);
    } catch { response.writeHead(502).end(); }
    finally { pending.delete(key); }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => { server.close(resolve); server.closeAllConnections(); }),
  };
}
