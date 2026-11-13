import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { projectRoot } from "./version.mjs";

const forbidden = /(^|\/)(?:\.env(?:$|\.(?!example$))|storage[-_]state\.json$|node_modules\/|\.runtime\/|\.finesse\/)|\.(?:sqlite3?(?:-(?:wal|shm))?|log|jsonl|exe|blockmap)$/i;
const credentialRules = [
  ["private-key", /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
  ["provider-key", /\bsk-[A-Za-z0-9_-]{28,}/],
  ["github-token", /\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})/]
];

export function auditSource(root = projectRoot, files) {
  files ??= execFileSync("git", ["-C",root,"ls-files","--cached","--others","--exclude-standard","-z"], {encoding:"utf8"}).split("\0").filter(Boolean);
  const findings = [];
  for (const file of new Set(files)) {
    const absolute = path.resolve(root, file);
    if (!absolute.startsWith(path.resolve(root) + path.sep)) { findings.push({file, rule:"outside-workspace"}); continue; }
    if (!fs.existsSync(absolute)) continue;
    if (forbidden.test(file.replaceAll("\\", "/"))) { findings.push({file, rule:"runtime-data"}); continue; }
    const stats = fs.lstatSync(absolute);
    if (stats.isSymbolicLink()) { findings.push({file, rule:"symlink"}); continue; }
    if (!stats.isFile() || stats.size > 4000000) continue;
    const bytes = fs.readFileSync(absolute);
    if (bytes.includes(0)) continue;
    const lines = bytes.toString("utf8").split(/\r?\n/);
    for (let i=0;i<lines.length;i++) for (const [rule, pattern] of credentialRules) {
      if (pattern.test(lines[i])) findings.push({file, line:i+1, rule});
    }
  }
  return findings;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const findings = auditSource();
  if (findings.length) { console.error(JSON.stringify(findings,null,2)); process.exitCode=1; }
  else console.log("Source audit passed: no runtime data or credential patterns in publishable files.");
}
