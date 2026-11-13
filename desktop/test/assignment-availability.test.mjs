import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { assignmentDeadline, assignmentExpired } from "../src/features/assignments/lib/availability.js";
const fixtures = JSON.parse(await readFile(new URL("../../backend/test/fixtures/assignment-deadlines.json", import.meta.url), "utf8"));
test("deadline filtering follows the shared platform timezone and academic-period contract", () => {
  for (const fixture of fixtures) {
    const now = new Date(fixture.now);
    assert.equal(assignmentExpired(fixture.item, now), fixture.expired, fixture.name);
    if (fixture.unknown) assert.equal(assignmentDeadline(fixture.item, now), null, fixture.name);
  }
});
