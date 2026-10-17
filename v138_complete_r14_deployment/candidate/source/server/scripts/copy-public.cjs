const fs = require("node:fs");
const path = require("node:path");

const source = path.resolve(__dirname, "../public");
const target = path.resolve(__dirname, "../dist/public");

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(target, { recursive: true });
fs.cpSync(source, target, { recursive: true });
