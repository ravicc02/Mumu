# OCS Course Runtime Adaptation

Source: https://github.com/ocsjs/ocsjs
Release: 5.4.0
Commit: 1e93399a22b54fac80bb6bae131a4b208b5d8af9
Copyright (c) 2022 enncy, MIT License.

The executable adaptations are `backend/app/services/learning/runtime.js`,
`ocs-study.js`, `platform-runtime.js` and the Python course host.
Its license is included beside it as `OCS-LICENSE` and in the desktop notices.

Reused upstream implementations:

- `packages/scripts/src/projects/cx.ts`: CXAnalyses chapter extraction,
  searchIFrame traversal, searchJob resource selectors and attachment identity,
  native media handling from JobRunner.media.
- `packages/scripts/src/utils/study.ts`: waitForMedia polling and timeout.
- The original Chaoxing `JobRunner.read`, `readPPTWithAudio` and `hyperlink` methods are now extracted by the TypeScript build script as executable handlers. Their sleeps use our cancellable local runtime.
- Timed book reading and ordinary book navigation follow the upstream page stages, using real elapsed waits and platform controls.

Changes: self-contained browser API, explicit unknown completion state,
cancellation, preservation of playback position, no automatic resume after
manual pause, no random exercise answers, no external OCS services or GM APIs.
The Python host inspects cross-origin frames using Playwright and verifies
course completion from a fresh authenticated platform response.

The host also recognizes the newer Chaoxing `toOld` chapter catalog.
The interactive viewer uses our local Chrome CDP screencast and input channel;
it shares the task page and does not use OCS RPC or hosted infrastructure.

OCS Desktop, ScriptCat, Tampermonkey and OCS-hosted assets are not bundled or
required by this implementation. All six upstream platform families are registered.
Video/audio and supported Chaoxing readers use our task state machine. A failed
resource is deferred while other cards and chapters continue. Final completion
still requires a fresh platform receipt. Tests and verification challenges remain
explicit manual work; no guessed answers or forged completion flags are added.

The six original platform files are preserved under `upstream/projects/`.
`scripts/build-ocs-study.mjs` extracts pure study handlers and navigation functions
with a TypeScript AST and compiles a local browser script. `upstream-manifest.json`
records source hashes, extracted line ranges and the generated runtime hash.
See `docs/multi-platform-learning.md` for the implemented scope and validation.
