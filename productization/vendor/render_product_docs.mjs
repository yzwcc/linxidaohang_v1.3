#!/usr/bin/env node
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";


const scriptDir = dirname(fileURLToPath(import.meta.url));
const productization = resolve(scriptDir, "..");
const docs = join(productization, "docs");
const outputDir = join(docs, "pdf");
const edgeCandidates = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"
];

const style = `
@page { size: A4; margin: 18mm 16mm 18mm; }
* { box-sizing: border-box; }
html { color: #17202a; background: white; font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }
body { margin: 0; font-size: 10.5pt; line-height: 1.68; }
h1 { color: #0f3d3e; font-size: 24pt; line-height: 1.25; margin: 0 0 18pt; padding-bottom: 10pt; border-bottom: 2px solid #159895; }
h2 { color: #164b50; font-size: 16pt; margin: 22pt 0 8pt; break-after: avoid; }
h3 { color: #235c61; font-size: 12.5pt; margin: 16pt 0 6pt; break-after: avoid; }
p { margin: 5pt 0 9pt; }
ul, ol { margin: 5pt 0 10pt; padding-left: 22pt; }
li { margin: 2pt 0; }
table { width: 100%; border-collapse: collapse; margin: 10pt 0 14pt; font-size: 9pt; break-inside: auto; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { color: white; background: #176b70; font-weight: 600; }
th, td { border: 1px solid #aebfc0; padding: 6pt 7pt; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
tbody tr:nth-child(even) { background: #f4f8f8; }
code { font-family: Consolas, "Microsoft YaHei", monospace; font-size: 8.8pt; background: #eef3f3; padding: 1pt 3pt; border-radius: 2px; overflow-wrap: anywhere; }
pre { color: #eaf5f4; background: #15383b; padding: 10pt 12pt; border-radius: 4px; overflow-wrap: anywhere; white-space: pre-wrap; break-inside: avoid; }
pre code { color: inherit; background: transparent; padding: 0; }
blockquote { margin: 10pt 0; padding: 7pt 12pt; border-left: 4px solid #159895; background: #f3f8f7; }
.cover { min-height: 225mm; display: flex; flex-direction: column; justify-content: center; border-top: 10px solid #159895; }
.cover h1 { font-size: 30pt; border: 0; margin-bottom: 14pt; }
.cover p { color: #526466; font-size: 13pt; }
.classification { color: #8b2f2f; font-weight: 700; }
.page-break { break-before: page; }
`;

function run(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8", stdio: "pipe", shell: false });
  if (result.status !== 0) {
    throw new Error(`${command} 执行失败：${result.error?.message || result.stderr || result.stdout || result.status}`);
  }
}

function renderMarkdown(markdown, workDir, name) {
  const input = join(workDir, `${name}.md`);
  const fragment = join(workDir, `${name}.fragment.html`);
  writeFileSync(input, markdown, "utf8");
  if (process.platform === "win32") {
    const npxCli = join(dirname(process.execPath), "node_modules", "npm", "bin", "npx-cli.js");
    run(process.execPath, [npxCli, "--yes", "marked", "-i", input, "-o", fragment, "--gfm"]);
  } else {
    run("npx", ["--yes", "marked", "-i", input, "-o", fragment, "--gfm"]);
  }
  return readFileSync(fragment, "utf8");
}

function printPdf(edge, html, output, workDir, name) {
  const htmlPath = join(workDir, `${name}.html`);
  writeFileSync(htmlPath, html, "utf8");
  const profile = join(workDir, `${name}-edge-profile`);
  run(edge, [
    "--headless=new",
    "--disable-gpu",
    "--no-pdf-header-footer",
    "--print-to-pdf-no-header",
    `--user-data-dir=${profile}`,
    `--print-to-pdf=${output}`,
    pathToFileURL(htmlPath).href
  ]);
  const data = readFileSync(output);
  if (data.length < 10_000 || data.subarray(0, 5).toString("ascii") !== "%PDF-") {
    throw new Error(`PDF 生成结果无效：${output}`);
  }
}

function htmlDocument(title, cover, body) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${title}</title><style>${style}</style></head><body>${cover}${body}</body></html>`;
}

const edge = edgeCandidates.find((candidate) => {
  try { return statSync(candidate).isFile(); } catch { return false; }
});
if (!edge) throw new Error("未找到 Microsoft Edge，无法生成 PDF");

mkdirSync(outputDir, { recursive: true });
const workDir = mkdtempSync(join(tmpdir(), "sparkrobot-docs-"));
try {
  const internalFiles = [
    "厂商部署与运维手册.md",
    "厂商密钥与许可证管理手册.md",
    "产品保密与安全边界说明.md",
    "新机器人部署验收清单.md"
  ];
  const internalMarkdown = internalFiles
    .map((name) => readFileSync(join(docs, "internal", name), "utf8"))
    .join("\n\n<div class=\"page-break\"></div>\n\n");
  const internalBody = renderMarkdown(internalMarkdown, workDir, "internal");
  const internalCover = `<section class="cover"><h1>灵犀导航产品化<br>厂商部署与运维手册</h1><p class="classification">厂商内部保密，不得交付客户</p><p>设备绑定发布 · 密钥与许可证 · SSH 部署 · 升级回滚 · 安全边界 · 验收清单</p><p>版本日期：2026-08-26</p></section><div class="page-break"></div>`;
  printPdf(
    edge,
    htmlDocument("灵犀导航产品化厂商部署与运维手册", internalCover, internalBody),
    join(outputDir, "灵犀导航产品化-厂商部署与运维手册.pdf"),
    workDir,
    "internal"
  );

  const customerMarkdown = readFileSync(join(docs, "customer", "灵犀导航客户使用手册.md"), "utf8");
  const customerBody = renderMarkdown(customerMarkdown, workDir, "customer");
  const customerCover = `<section class="cover"><h1>灵犀机器人导航控制台<br>客户使用手册</h1><p>开机与访问 · 一键启动 · 地图定位 · 点位导航 · 多任务讲解</p><p>版本日期：2026-08-26</p></section><div class="page-break"></div>`;
  printPdf(
    edge,
    htmlDocument("灵犀机器人导航控制台客户使用手册", customerCover, customerBody),
    join(outputDir, "灵犀导航客户使用手册.pdf"),
    workDir,
    "customer"
  );
} finally {
  rmSync(workDir, { recursive: true, force: true });
}

console.log(`PDF 已生成：${outputDir}`);
