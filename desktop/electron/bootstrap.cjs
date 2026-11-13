const fs = require("node:fs/promises");
const path = require("node:path");
const { execFile } = require("node:child_process");

const quote = value => "'" + String(value).replaceAll("'", "''") + "'";
function safeOutput(value) {
  return String(value || "").replace(/\x1b\[[0-9;]*m/g, "")
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/g, "$1[REDACTED]@")
    .replace(/\bsk-[\w-]{8,}/g, "[REDACTED]").slice(-8000);
}
function runFile(file, args, options = {}) {
  return new Promise((resolve, reject) => execFile(file, args, { windowsHide: true, timeout: 90000, maxBuffer: 2 * 1024 * 1024, ...options }, (error, stdout, stderr) => {
    if (error) { error.message = safeOutput(stderr || stdout || error.message); reject(error); }
    else resolve({ stdout, stderr });
  }));
}
function browserCandidates(env) {
  return [env.ANSWER_STUDIO_CHROME_PATH,
    path.join(env.ProgramFiles || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
    path.join(env['ProgramFiles(x86)'] || 'C:/Program Files (x86)', 'Google/Chrome/Application/chrome.exe'),
    path.join(env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe'),
    path.join(env.ProgramFiles || 'C:/Program Files', 'Microsoft/Edge/Application/msedge.exe'),
    path.join(env['ProgramFiles(x86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe')].filter(Boolean);
}
function verifyBackendHealth(payload, version) {
  if (payload?.ok !== true || typeof payload.version !== 'string') throw new Error('本地服务未返回有效的健康检查。');
  if (payload.version !== version) {
    const error = new Error('8877 端口正在运行其他版本的服务，请先关闭旧版应用，再重新检测。');
    error.code = 'VERSION_MISMATCH'; throw error;
  }
  return true;
}
function createBootstrap({ packaged, backendDirectory, dataRoot, python = 'python', env = process.env, run = runFile, io = fs, onReady = async () => {} }) {
  let state = { status: 'idle', items: [], output: '', error: '', backendReady: false };
  let pending = null;
  const backendExe = path.join(backendDirectory, 'answer-studio-backend.exe');
  const requirements = path.join(backendDirectory, 'requirements.txt');
  const manual = () => packaged ? "Start-Process 'https://github.com/ravicc02/ai-quiz-assistant/releases'"
    : '& ' + quote(python) + ' -m pip install -r ' + quote(requirements);
  const get = () => structuredClone(state);
  const exists = async target => { try { return (await io.stat(target)).isFile(); } catch { return false; } };
  async function inspect() {
    state = { status: 'checking', items: [], output: state.output, error: '', backendReady: false };
    state.items.push({ id: 'runtime', label: packaged ? '内置运行环境' : 'Python 与运行库', status: 'checking', installable: !packaged, command: manual() });
    const runtime = state.items[0];
    try {
      if (packaged) {
        if (!await exists(backendExe)) throw new Error('内置服务文件缺失，请重新安装沐沐。');
        runtime.detail = '由安装包提供';
      } else {
        await run(python, ['-c', 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"; import fastapi, uvicorn, playwright, browser_use, pydantic, bs4, multipart; print(sys.version.split()[0])'], { cwd: backendDirectory });
        runtime.detail = 'Python 与业务运行库可用';
      }
      runtime.status = 'ready';
    } catch (error) {
      runtime.status = 'missing'; runtime.detail = safeOutput(error.message);
      if (!packaged && (error.code === 'ENOENT' || error.code === 9009 || /Python was not found|Python 3\.11\+ required/.test(error.message))) {
        runtime.missingPython = true;
        runtime.command = 'winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements; ' + manual();
      }
    }
    const browser = { id: 'browser', label: 'Chrome / Edge', status: 'checking', installable: true,
      command: 'winget install --id Google.Chrome --exact --accept-package-agreements --accept-source-agreements' };
    state.items.push(browser);
    let found = '';
    for (const candidate of browserCandidates(env)) if (await exists(candidate)) { found = candidate; break; }
    browser.status = found ? 'ready' : 'missing'; browser.detail = found || '未检测到 Chrome 或 Edge';
    const storage = { id: 'storage', label: '本地数据目录', status: 'checking', installable: false, command: 'explorer.exe ' + quote(dataRoot) };
    state.items.push(storage);
    try {
      await io.mkdir(dataRoot, { recursive: true }); await io.access(dataRoot, require('node:fs').constants.W_OK);
      const probe = path.join(dataRoot, '.mumu-setup-' + require('node:crypto').randomUUID() + '.tmp');
      await io.writeFile(probe, '', { flag: 'wx' }); await io.unlink(probe);
      storage.status = 'ready'; storage.detail = dataRoot;
    }
    catch (error) { storage.status = 'error'; storage.detail = safeOutput(error.message); }
    if (state.items.some(item => item.status !== 'ready')) { state.status = 'blocked'; return get(); }
    state.status = 'starting';
    try { await onReady(python); state.status = 'ready'; state.backendReady = true; }
    catch (error) {
      state.status = 'error'; state.error = safeOutput(error.message);
      state.recoveryCommand = packaged ? '& ' + quote(backendExe) + ' --host 127.0.0.1 --port 8877'
        : '& ' + quote(python) + ' ' + quote(path.join(backendDirectory, 'run_server.py')) + ' --host 127.0.0.1 --port 8877';
    }
    return get();
  }
  const check = () => {
    if (pending) return pending;
    pending = inspect().finally(() => { pending = null; });
    return pending;
  };
  const install = () => {
    if (pending) return pending;
    // Only fixed dependency commands run; renderer text never becomes a shell command.
    const missing = state.items.filter(item => item.installable && item.status === 'missing');
    if (!missing.length) return check();
    state.status = 'installing'; state.output = ''; state.error = '';
    pending = (async () => {
      try {
        for (const item of missing) {
          if (item.id === 'runtime' && item.missingPython) {
            await run('winget', ['install', '--id', 'Python.Python.3.13', '--exact', '--accept-package-agreements', '--accept-source-agreements'], { timeout: 15 * 60 * 1000 });
            const candidates = [path.join(env.LOCALAPPDATA || '', 'Programs/Python/Python313/python.exe'), path.join(env.ProgramFiles || 'C:/Program Files', 'Python313/python.exe')];
            const found = [];
            for (const candidate of candidates) if (await exists(candidate)) found.push(candidate);
            if (!found.length) throw new Error('Python 安装完成后尚未找到可执行文件，请重启应用或执行手动命令。');
            python = found[0];
            item.command = manual();
          }
          const result = item.id === 'runtime'
            ? await run(python, ['-m', 'pip', 'install', '-r', requirements], { cwd: backendDirectory, timeout: 15 * 60 * 1000 })
            : await run('winget', ['install', '--id', 'Google.Chrome', '--exact', '--accept-package-agreements', '--accept-source-agreements'], { timeout: 15 * 60 * 1000 });
          state.output = safeOutput(state.output + '\n' + result.stdout + '\n' + result.stderr);
        }
        return await inspect();
      } catch (error) {
        state.status = 'error'; state.output = safeOutput(state.output + '\n' + error.message);
        state.error = '依赖安装未完成，请查看安装输出或运行手动命令。'; return get();
      }
    })().finally(() => { pending = null; });
    return pending;
  };
  return { get, check, install };
}
module.exports = { createBootstrap, browserCandidates, safeOutput, verifyBackendHealth };
