import {spawnSync} from "node:child_process";
import path from "node:path";
import {fileURLToPath} from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.MUMU_PYTHON || process.env.ANSWER_STUDIO_PYTHON || (process.platform === "win32" ? "python" : "python3");
const result = spawnSync(python, ["-m", "unittest", "discover", "-s", "backend/test", "-v"], {cwd:root, env:{...process.env, PYTHONPATH:path.join(root,"backend"), PYTHONUTF8:"1"}, stdio:"inherit"});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
