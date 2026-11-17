import path from 'node:path';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = path.join(root, 'backend');
const python = process.env.MUMU_PYTHON || process.env.ANSWER_STUDIO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const env = {...process.env, PYTHONPATH:[path.join(root, 'scripts/pyinstaller_bootstrap'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)};
function run(command, args) {
  const result = spawnSync(command, args, {cwd:backend, env, stdio:'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(command + ' failed: ' + result.status);
}
run(process.execPath, [path.join(root,'scripts/version.mjs'), 'check']);
run(python, [path.join(root,'scripts/write_build_info.py')]);
const resources = [
  ['../VERSION','app'],
  ['app/services/learning/runtime.js','app/services/learning'],
  ['app/services/learning/study-runtime.js','app/services/learning'],
  ['app/services/learning/platform-runtime.js','app/services/learning'],
  ['app/services/platforms/catalog.json','app/services/platforms'],
  ['app/services/attendance/share.html','app/services/attendance'],
  ['app/services/attendance/qr-decoder.js','app/services/attendance'],
  ['build/diagnostic-build.json','app/diagnostics'],
];
run(python, [path.join(root,'scripts/run_pyinstaller.py'), '--noconfirm','--clean','--onedir','--name','mumu-backend',
  '--collect-all','browser_use','--collect-all','playwright',
  ...['torch','torchvision','tensorflow','onnxruntime','pandas','scipy','matplotlib','numba','llvmlite','sklearn','openpyxl','sqlalchemy'].flatMap(name=>['--exclude-module',name]),
  '--runtime-hook',path.join(root,'scripts/pyinstaller_runtime_hook.py'),'--collect-submodules','app',
  ...resources.flatMap(([source,destination])=>['--add-data',source+path.delimiter+destination]),
  ...['app.main','uvicorn.logging','uvicorn.loops.auto','uvicorn.protocols.http.auto','uvicorn.protocols.websockets.auto','uvicorn.lifespan.on'].flatMap(name=>['--hidden-import',name]),
  'run_server.py']);
const executable = process.platform === 'win32' ? 'mumu-backend.exe' : 'mumu-backend';
if (!fs.statSync(path.join(backend, 'dist/mumu-backend', executable)).isFile()) throw new Error('Packaged backend executable is missing');
