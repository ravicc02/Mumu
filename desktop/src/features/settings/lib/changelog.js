export function parseChangelog(source) {
  const entries = [];
  let entry = null;
  let group = null;
  for (const line of String(source).split(/\r?\n/)) {
    const version = line.match(/^## \[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?/);
    if (version) {
      entry = { version: version[1], date: version[2] || "", groups: [] };
      entries.push(entry);
      group = null;
    } else if (entry && /^###\s+/.test(line)) {
      group = { title: line.replace(/^###\s+/, ""), items: [] };
      entry.groups.push(group);
    } else if (group && /^-\s+/.test(line)) {
      group.items.push(line.replace(/^-\s+/, ""));
    } else if (group && line.trim() && group.items.length && !/^#/.test(line)) {
      group.items[group.items.length - 1] += " " + line.trim();
    }
  }
  return entries;
}
