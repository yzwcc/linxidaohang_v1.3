import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { digest, metadataFiles, sourceDirectories, sourceHashes } from "../robot-runtime/build-live.mjs";

async function project(t) {
  const root = await mkdtemp(path.join(tmpdir(), "lingxi-live-build-synthetic-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const name of metadataFiles) {
    await mkdir(path.dirname(path.join(root, name)), { recursive: true });
    await writeFile(path.join(root, name), "synthetic source");
  }
  for (const name of sourceDirectories) {
    await mkdir(path.join(root, name), { recursive: true });
    await writeFile(path.join(root, name, "synthetic.txt"), "synthetic source");
  }
  await mkdir(path.join(root, "tools"));
  await writeFile(path.join(root, "tools/synthetic.py"), '"""Synthetic source, not executed."""');
  return root;
}

test("source inventory is deterministic, explicit and includes runtime Python plus frontend", async (t) => {
  const root = await project(t);
  const first = await sourceHashes(root), second = await sourceHashes(root);
  assert.deepEqual(first, second);
  assert.equal(first["web/synthetic.txt"], digest(Buffer.from("synthetic source")));
  assert.equal(first["robot-runtime/python/synthetic.txt"], first["web/synthetic.txt"]);
  assert.ok(first["tools/synthetic.py"]);
  assert.equal(Object.keys(first).length, metadataFiles.length + sourceDirectories.length + 1);
});

test("environment and private-data files inside source directories reject the build snapshot", async (t) => {
  const root = await project(t);
  for (const name of [".env", "private-profile.json", "synthetic.key", "capture.npz"]) {
    const filename = path.join(root, "web", name);
    await writeFile(filename, "isolated synthetic privacy fixture");
    await assert.rejects(sourceHashes(root), /source_only/);
    await rm(filename);
  }
});

test("Python caches are excluded but ordinary source changes alter their digest", async (t) => {
  const root = await project(t);
  const before = await sourceHashes(root);
  await mkdir(path.join(root, "robot-runtime/python/__pycache__"));
  await writeFile(path.join(root, "robot-runtime/python/__pycache__/synthetic.pyc"), "synthetic cache");
  assert.deepEqual(await sourceHashes(root), before);
  await writeFile(path.join(root, "web/synthetic.txt"), "changed synthetic source");
  assert.notEqual((await sourceHashes(root))["web/synthetic.txt"], before["web/synthetic.txt"]);
});
