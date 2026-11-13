import catalog from "../../../backend/app/services/platforms/catalog.json" with { type: "json" };

export const platformCatalog = catalog;
export const platformById = Object.fromEntries(catalog.map(item => [item.name, item]));

export const businessTypes = [
  { id: "assignments", label: "作业" },
  { id: "learning", label: "网课" },
  { id: "attendance", label: "签到" },
];

export function supportsBusiness(subject, business) {
  if (!businessTypes.some(item => item.id === business)) return false;
  const name = typeof subject === "string" ? subject : subject?.platform || subject?.name;
  if (platformById[name]?.[business] !== true) return false;
  const declared = subject?.capabilities || (subject?.name === name ? subject : null);
  return !declared || !Object.hasOwn(declared, business) ? true : declared[business] === true;
}

export function accountsForBusiness(accounts, business) {
  return accounts.filter(account => supportsBusiness(account, business));
}

export function businessAccountId(accounts, business, preferredId = "", previousId = "") {
  const eligible = accountsForBusiness(accounts, business);
  for (const id of [preferredId, previousId]) {
    if (id && eligible.some(account => account.id === id)) return id;
  }
  return (eligible.find(account => account.status === "ready") || eligible[0])?.id || "";
}

export function recordsForAccount(records, accountId) {
  return accountId ? records.filter(record => record.account_id === accountId) : [];
}
