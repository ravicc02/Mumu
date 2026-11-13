"""Generate immutable diagnostic build identity for the packaged desktop backend."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
from app.version import __version__

sources = sorted([*root.joinpath('backend/app').rglob('*.py'), *root.joinpath('backend/app').rglob('*.js'), *root.joinpath('backend/app').rglob('*.json'),
                  *root.joinpath('desktop/src').rglob('*.vue'), *root.joinpath('desktop/src').rglob('*.js'), *root.joinpath('desktop/src').rglob('*.css'),
                  *root.joinpath('desktop/electron').rglob('*.cjs'), *root.joinpath('backend/app').rglob('*.html'),
                  root / 'VERSION', root / 'desktop/package.json', root / 'backend/requirements.txt'])
hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
def git(*args):
    try: return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError): return 'unavailable'
if json.loads((root / 'desktop/package.json').read_text(encoding='utf-8'))['version'] != __version__:
    raise RuntimeError('Desktop and backend versions differ; run npm run version:sync')
data = {'schema_version': 1, 'app_version': __version__,
        'built_at': datetime.now(timezone.utc).isoformat(), 'git_commit': git('rev-parse', 'HEAD'),
        'working_tree_dirty': bool(git('status', '--porcelain')), 'source_hashes': hashes,
        'build_fingerprint': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:20]}
target = root / 'backend/build/diagnostic-build.json'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(target)
