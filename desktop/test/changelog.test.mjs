import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parseChangelog } from "../src/features/settings/lib/changelog.js";

test("release notes select the installed version and omit development changes", async () => {
  const source = await readFile(new URL("../../CHANGELOG.md", import.meta.url), "utf8");
  const entries = parseChangelog(source);
  assert.equal(entries[0].version, "Unreleased");
  const version = (await readFile(new URL("../package.json", import.meta.url), "utf8"));
  const current = entries.find(entry => entry.version === JSON.parse(version).version);
  assert.ok(current?.date);
  assert.ok(current.groups.some(group => group.items.length));
  const fixture = parseChangelog('## [Unreleased]\n### Added\n- 未来开发内容\n## [1.2.3] - 2026-11-20\n### Fixed\n- 当前版本内容\n');
  assert.ok(!fixture.find(entry => entry.version === '1.2.3').groups.some(group => group.items.includes('未来开发内容')));
});

test("changelog parser joins wrapped bullets without treating headings as items", () => {
  const entries = parseChangelog("## [1.2.3] - 2026-11-20\n### Added\n- First item\n  continuation\n### Fixed\n- Second item\n");
  assert.deepEqual(entries, [{ version: "1.2.3", date: "2026-11-20", groups: [
    { title: "Added", items: ["First item continuation"] },
    { title: "Fixed", items: ["Second item"] },
  ] }]);
});
