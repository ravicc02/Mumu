import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = path.join(root, 'backend');
const python = process.env.MUMU_PYTHON || process.env.ANSWER_STUDIO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const separator = process.platform === 'win32' ? ';' : ':';
const executable = process.platform === 'win32' ? 'mumu-backend.exe' : 'mumu-backend';
const env = { ...process.env, PYTHONPATH: [path.join(root, 'scripts', 'pyinstaller_bootstrap'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };

function run(args) {
  const result = spawnSync(python, args, { cwd: backend, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Python build failed with exit code ${result.status}`);
}

run([path.join(root, 'scripts', 'write_build_info.py')]);
run([path.join(root, 'scripts', 'run_pyinstaller.py'), '--noconfirm', '--clean', '--onedir', '--name', 'mumu-backend',
  '--collect-all', 'browser_use', '--collect-all', 'playwright',
  '--exclude-module', 'torch', '--exclude-module', 'torchvision', '--exclude-module', 'tensorflow',
  '--exclude-module', 'onnxruntime', '--exclude-module', 'pandas', '--exclude-module', 'scipy',
  '--exclude-module', 'matplotlib', '--exclude-module', 'numba', '--exclude-module', 'llvmlite',
  '--exclude-module', 'sklearn', '--exclude-module', 'openpyxl', '--exclude-module', 'sqlalchemy',
  '--runtime-hook', path.join(root, 'scripts', 'pyinstaller_runtime_hook.py'),
  '--collect-submodules', 'app', '--add-data', `../VERSION${separator}app`,
  '--add-data', `app/services/learning/runtime.js${separator}app/services/learning`,
  '--add-data', `app/services/learning/study-runtime.js${separator}app/services/learning`,
  '--add-data', `app/services/learning/platform-runtime.js${separator}app/services/learning`,
  '--add-data', `app/services/platforms/catalog.json${separator}app/services/platforms`,
  '--add-data', `app/services/attendance/share.html${separator}app/services/attendance`,
  '--add-data', `app/services/attendance/qr-decoder.js${separator}app/services/attendance`,
  '--add-data', `build/diagnostic-build.json${separator}app/diagnostics`,
  '--hidden-import', 'app.main', '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.auto',
  '--hidden-import', 'uvicorn.protocols.http.auto', '--hidden-import', 'uvicorn.protocols.websockets.auto',
  '--hidden-import', 'uvicorn.lifespan.on', 'run_server.py']);

const output = path.join(backend, 'dist', 'mumu-backend');
if (!fs.existsSync(output)) throw new Error(`Backend bundle missing: ${output}`);
console.log(`Backend bundle: ${output} (${executable})`);
