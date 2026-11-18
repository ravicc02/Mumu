import {spawnSync} from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.MUMU_PYTHON || process.env.ANSWER_STUDIO_PYTHON || (process.platform === "win32" ? "python" : "python3");
// Tests must never touch the real ~/.mumu data directory.
const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mumu-test-data-"));
const env = {...process.env, PYTHONPATH:path.join(root,"backend"), PYTHONUTF8:"1", MUMU_DATA:dataRoot};
delete env.ANSWER_STUDIO_DATA;
const result = spawnSync(python, ["-m", "unittest", "discover", "-s", "backend/test", "-v"], {cwd:root, env, stdio:"inherit"});
try { fs.rmSync(dataRoot, {recursive:true, force:true}); } catch { /* 文件被占用时留给系统临时目录清理 */ }
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
