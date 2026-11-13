# UI/UX Review and Desktop Redesign

Date: 2026-11-11. Scope: PC application and local browser preview.

## Findings and Changes

| Priority | Finding | Implemented result |
| --- | --- | --- |
| High | Saved full-page screenshots were compressed into an unreadable vertical strip. | Task captures and modal screenshots render at panel width in an independently scrollable viewport. Saved task captures and current account sessions are separate views. |
| High | Quick account changes could apply stale assignment, semester, collection or preview responses. | Responses are checked against the current account and semester before they update the screen. Preview polling skips overlapping requests. |
| High | Batch selection could silently lose hidden selections or clear unrelated selections. | Execution uses selected assignment IDs across filters. Select-all affects only visible results. Creation failures preserve failed selections and show individual errors. |
| Medium | Task history had no search, state/account filters or pagination. | Latest run per account/assignment remains unique. Added text search, status filters, account filters, ten-row pagination, counts, and direct account navigation. |
| Medium | Browser, controls and logs competed for space. | Rebuilt the workspace with a narrow navigation rail, adjustable assignment column, large browser viewport and collapsible log dock. Wide desktops show the log at the right; narrower desktops dock it below the browser. |
| Medium | Logs exposed only shortened summaries. | Each log row expands to full text and can be copied. Full JSON logs can be exported without embedded screenshot data. |
| Medium | Web preview directory buttons did nothing. | Electron keeps native directory opening. Browser preview explicitly offers clipboard copying of the directory path. |
| Medium | Services were represented by generic text and unrelated icons. | Added local Chaoxing platform marks from its official download page and a WeChat mark. Logos appear in account overview, platform selection, task rows, browser heading and connector entry. |
| Medium | Many visible connector entries had no implementation. | Removed unavailable connector entries; kept the supported WeChat connector. |
| Low | Fragmented CSS and repeated controls/copy made the interface harder to scan. | Replaced the obsolete stylesheet with one token-based desktop design. Removed duplicate browser entry controls and introductory subtitles. |

## Reused Components

- LeiBox `glass-tab-menu`: adapted spring integrator, settling behavior, keyboard navigation and reduced-motion handling into `LeiTabs.vue`, bound to real filter state.
- LeiBox `elastic-panel-resize`: adapted bounded pointer dragging, edge resistance, pointer cancellation, keyboard resizing and reset into `LeiResizeHandle.vue`, bound to the actual assignment-column width.
- Source notices, reference authors and upstream license are retained in the component directory and in the production `third-party-notices.txt` asset.
- No demonstration iframe, reference video, original artwork, GSAP runtime or unrelated effects were added to the application.

## Creative Interaction Pass

- Added `LeiStatusSelect.vue`, adapting LeiBox's blister-dropdown trajectory: the selected status capsule pops out, arcs into the trigger and lands with a short compression. It changes the actual task filter immediately and supports exact incomplete/interrupted/paused filters.
- Added one shared navigation highlight that moves between page entries, with an icon arrival animation.
- Task rows enter in a short stagger and move into their new positions when filters change. New log events enter from the side.
- Account switching animates the selected avatar and account identity while immediately clearing the previous account's screenshot and logs.
- Browser expansion and collapse use FLIP transforms from the current panel rectangle to the expanded rectangle. It respects reduced motion and supports Escape.
- Recorded a real screenshot sequence of the status capsule interaction; output: `.finesse/qa/status-interaction.gif`.
- Verified rapid account switching back to gym reloads its saved screenshot and 17 log entries, rather than leaving the cleared workspace empty.

## Workflow Coverage

1. Task overview -> filter/search -> task status dialog -> select historical attempt -> close back to the same list.
2. Account overview -> account workspace -> semester and assignment filtering -> multi-selection -> execute selection using existing backend endpoints.
3. Task capture -> expanded capture -> close with Escape -> current authenticated account session -> native browser entry.
4. Live log -> expand entry -> copy or export -> open execution evidence.
5. Connector and model settings remain backed by the existing APIs.

The five user-defined task outcomes and their colors are preserved: interrupted/red, incomplete/red, needs review/amber, manually paused/gray, completed and submitted/green.

## Verification

- Desktop screenshots inspected at 1280, 1440 and 1703 pixels. No document horizontal overflow or off-screen action buttons in observed views.
- Verified task and assignment filters, keyboard and pointer resizing, double-click reset, width persistence, cross-filter selection, browser expansion, Escape, log collapse, full log details, task history dialog and connector/settings rendering.
- Current account session successfully loaded the authenticated Chaoxing personal workspace.
- Log export produced a real task JSON download. Real platform images loaded from packaged local assets.
- Frontend: six Node tests covering service errors, validation errors, action request payloads, bounded resize, spring settling and finite capsule flight/landing.
- Backend regression suite: 32 tests.
- Production frontend build and stylesheet detector passed.

Screenshots: `.finesse/qa/leibox-dashboard.jpg` and `.finesse/qa/leibox-workspace.jpg`.

## Boundaries

- This verification did not execute or submit a real assignment, change model credentials, rebind WeChat, or delete an account.
- Live agent stress testing and automatic submission are outside this UI change. The native browser still uses the existing session APIs.
- Local preview and source are updated; the Windows installer has not been rebuilt or published.
