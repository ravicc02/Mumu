# LeiBox Application Adaptations

Upstream repository: https://github.com/leishifu666/LeiBox

Reference checkout: f7a4c8acf55bc1cf817d6804373ae224340f3357.

The Vue packages for `glass-tab-menu` and `elastic-panel-resize` were retrieved with the repository's LeiBox integration script and its SHA-256 verification on 2026-11-11.

The `blister-dropdown` Vue package was also retrieved and verified on 2026-11-11. `pillFlightFrames` adapts the original 23-keyframe pop/arc/landing trajectory from `src/lib/motion-core/blister-dropdown.mjs`. `LeiStatusSelect.vue` binds it to the real task statuses, supports keyboard selection, cancels and cleans up flights, and respects reduced motion. The foil artwork, reference HTML, sound and demo counters are not included. The catalog records the design reference as Adeline / AstraOS.

- `motion.js`: spring integration and settling thresholds adapted from `src/lib/motion-core/glass-tab-menu.js`; bounded drag resistance from `src/lib/motion-core/elastic-panel-resize.js`.
- `LeiTabs.vue`: connects the selection spring, reduced-motion handling and arrow/Home/End keyboard navigation to application filter values. Demonstration menus, fixed dimensions and artwork are removed.
- `LeiResizeHandle.vue`: connects pointer capture, cancel, bounds, double-click reset and keyboard resizing to the actual assignment column. It emits real dimensions, rather than rendering the demonstration panel.

Design references recorded by LeiBox: solutionb for glass-tab-menu and Alvish for elastic-panel-resize. The original reference videos/artwork are not included. The adaptations are integral parts of this application, not a redistributed component library.

Retain the adjacent upstream LICENSE.md when distributing this application.
