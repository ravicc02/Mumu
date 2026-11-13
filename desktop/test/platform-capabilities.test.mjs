import { test } from "node:test";
import assert from "node:assert/strict";
import { accountsForBusiness, businessAccountId, recordsForAccount, supportsBusiness } from "../src/lib/platforms.js";

const accounts = [
  { id: "educoder", name: "Same name", platform: "educoder", status: "ready" },
  { id: "cx-pending", name: "Pending", platform: "chaoxing", status: "new" },
  { id: "cx-ready", name: "Same name", platform: "chaoxing", status: "ready" },
  { id: "zhs", name: "Tree", platform: "zhihuishu", status: "ready" },
];

test("assignment and course account scopes follow the shared platform capability matrix", () => {
  assert.deepEqual(accountsForBusiness(accounts, "assignments").map(a => a.id), ["educoder", "cx-pending", "cx-ready"]);
  assert.deepEqual(accountsForBusiness(accounts, "learning").map(a => a.id), ["cx-pending", "cx-ready", "zhs"]);
  assert.equal(supportsBusiness("educoder", "learning"), false);
  assert.equal(supportsBusiness("educoder", "assignments"), true);
  assert.equal(supportsBusiness("chaoxing", "learning"), true);
  assert.equal(supportsBusiness("chaoxing", "assignments"), true);
  assert.equal(supportsBusiness("icourse", "assignments"), false);
  assert.equal(supportsBusiness("icourse", "learning"), true);
});

test("unknown capabilities fail closed and an explicit server disable overrides a local supported entry", () => {
  assert.equal(supportsBusiness({ platform: "chaoxing", capabilities: { learning: false } }, "learning"), false);
  assert.equal(supportsBusiness({ platform: "chaoxing", capabilities: { learning: "true" } }, "learning"), false);
  assert.equal(supportsBusiness({ platform: "educoder", capabilities: { learning: true } }, "learning"), false);
  assert.equal(supportsBusiness({ platform: "unknown", capabilities: { learning: true } }, "learning"), false);
  assert.equal(supportsBusiness(null, "learning"), false);
  assert.equal(supportsBusiness("chaoxing", "unknown"), false);
});

test("switching from an unsupported account selects the previous business account or a ready supported account", () => {
  assert.equal(businessAccountId(accounts, "learning", "educoder"), "cx-ready");
  assert.equal(businessAccountId(accounts, "learning", "educoder", "zhs"), "zhs");
  assert.equal(businessAccountId(accounts, "learning", "cx-pending", "zhs"), "cx-pending");
  assert.equal(businessAccountId(accounts, "assignments", "zhs", "educoder"), "educoder");
  assert.equal(businessAccountId([accounts[0]], "learning", "educoder"), "");
  assert.equal(businessAccountId([], "assignments"), "");
});

test("same display names cannot mix course or task records between authorized profiles", () => {
  const rows = [{ id: "one", account_id: "educoder" }, { id: "two", account_id: "cx-ready" }, { id: "bad" }];
  assert.deepEqual(recordsForAccount(rows, "cx-ready").map(r => r.id), ["two"]);
  assert.deepEqual(recordsForAccount(rows, "educoder").map(r => r.id), ["one"]);
  assert.deepEqual(recordsForAccount(rows, ""), []);
  assert.equal(rows.length, 3);
});
