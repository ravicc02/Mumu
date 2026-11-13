import { test } from "node:test";
import assert from "node:assert/strict";
import { accountSchedule, restorePreferenceDraft, scheduleError } from "../src/features/settings/lib/preferences.js";

test("schedule drafts preserve seconds, units and legacy minute configurations", () => {
  assert.deepEqual(accountSchedule({ id: "a", auto_collect_interval_minutes: 30 }), { account_id: "a", enabled: false, auto_execute: false, interval_value: 30, interval_unit: "min" });
  const schedule = accountSchedule({ id: "a", auto_collect_interval_value: 5, auto_collect_interval_unit: "s", auto_collect_enabled: 1, auto_execute_enabled: 1 });
  assert.equal(schedule.interval_value, 5);
  assert.equal(schedule.interval_unit, "s");
  assert.equal(scheduleError(schedule), "");
  assert.ok(scheduleError({ ...schedule, interval_value: 4 }));
  assert.ok(scheduleError({ ...schedule, enabled: false }));
  assert.ok(scheduleError({ ...schedule, interval_value: 200, interval_unit: "h" }));
  assert.equal(scheduleError({ ...schedule, interval_value: 1.5, interval_unit: "min" }), "");
});

test("restored drafts retain changes without dropping new preferences or accounts", () => {
  const preferences = { assignments: { max_steps: 100, auto_submit: false }, learning: { volume: 0 }, system: { log_size_mb: 200 } };
  const schedules = [accountSchedule({ id: "a" }), accountSchedule({ id: "new-account" })];
  const restored = restorePreferenceDraft(preferences, schedules, { assignments: { max_steps: 50, unknown_field: "ignore" }, schedules: [{ ...schedules[0], interval_value: 10, interval_unit: "s" }, accountSchedule({ id: "deleted" })] });
  assert.equal(restored.preferences.assignments.max_steps, 50);
  assert.equal(restored.preferences.assignments.auto_submit, false);
  assert.equal(restored.preferences.assignments.unknown_field, undefined);
  assert.equal(restored.schedules[0].interval_unit, "s");
  assert.equal(restored.schedules.length, 2);
  assert.equal(restored.schedules[1].account_id, "new-account");
  assert.equal(preferences.assignments.max_steps, 100);
});

test("a malformed local schedule draft cannot prevent settings from loading", () => {
  const preferences = { assignments: { max_steps: 100 }, learning: { volume: 0 }, system: { log_size_mb: 200 } };
  const schedules = [accountSchedule({ id: "a" })];
  const result = restorePreferenceDraft(preferences, schedules, { schedules: "old-or-corrupt-format" });
  assert.deepEqual(result.schedules, schedules);
});
