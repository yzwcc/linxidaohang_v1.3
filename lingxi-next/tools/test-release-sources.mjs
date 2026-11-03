import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { copySourceTree, sourceMemberAllowed } from "./release-sources.mjs";

test("release source filter retains native verification inputs and excludes generated/private material", () => {
  for (const name of ["glim-full-mapping/full_mapping.cpp", "glim-full-mapping/submapping-eof.patch",
    "glim-observations/CMakeLists.txt", "native-point-output/exports.map", "runtime-arm64-cp310.lock.json",
    "python/managed_slam.py", "python/product_configuration.py", "check_ground_native.py",
    "check_installed_slam_python.py", "arm64_python_runtime.py", "prepare_installed_mapping_inputs.py",
    "prepare_installed_acquisition_inputs.py",
    "check-runtime.mjs"]) {
    assert.equal(sourceMemberAllowed(name, false), true, name);
  }
  for (const name of ["__pycache__/owner.pyc", ".local/robot.json", "credentials/token.json",
    "secrets/token.txt", "dist/main.mjs", "build/lib.so", "private-key.json", "token.json",
    "tests/fixture.json", "test_owner.py", "file.test.ts", "archive.zip",
    "robot.env", "robot.key", "state.sqlite3", "recording.db3"]) {
    assert.equal(sourceMemberAllowed(name, false), false, name);
  }
});

test("source copy applies filters during recursion", async () => {
  const work = await mkdtemp(path.join(os.tmpdir(), "lingxi-release-source-"));
  try {
    const source = path.join(work, "source");
    await mkdir(path.join(source, "native"), { recursive: true });
    await mkdir(path.join(source, "__pycache__"));
    await writeFile(path.join(source, "native", "CMakeLists.txt"), "fixture-native\n");
    await writeFile(path.join(source, "check_ground_native.py"), "fixture-verifier\n");
    await writeFile(path.join(source, "__pycache__", "owner.pyc"), "cache");
    await writeFile(path.join(source, "private-settings.json"), "excluded");
    const target = path.join(work, "target");
    await copySourceTree(source, target);
    assert.equal(await readFile(path.join(target, "native", "CMakeLists.txt"), "utf8"), "fixture-native\n");
    assert.equal(await readFile(path.join(target, "check_ground_native.py"), "utf8"), "fixture-verifier\n");
    await assert.rejects(readFile(path.join(target, "__pycache__", "owner.pyc")), { code: "ENOENT" });
    await assert.rejects(readFile(path.join(target, "private-settings.json")), { code: "ENOENT" });
  } finally { await rm(work, { recursive: true, force: true }); }
});

test("source copy refuses links", { skip: process.platform === "win32" }, async () => {
  const work = await mkdtemp(path.join(os.tmpdir(), "lingxi-release-link-"));
  try {
    const source = path.join(work, "source");
    await mkdir(source);
    await writeFile(path.join(work, "outside.py"), "fixture");
    await symlink(path.join(work, "outside.py"), path.join(source, "linked.py"));
    await assert.rejects(copySourceTree(source, path.join(work, "target")), /direct_release_source_required/);
  } finally { await rm(work, { recursive: true, force: true }); }
});
