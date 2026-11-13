export function assignmentDeadline(item, now = new Date()) {
  const text = String(item?.deadline_text || item?.deadlineText || "");
  const matches = [...text.matchAll(/(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})日?(?:(?:\s+|T)(\d{1,2}):(\d{2})(?::(\d{2}))?)?/g)];
  if (!matches.length) return null;
  const [, explicitYear, monthText, dayText, hour, minute, second] = matches.at(-1);
  const month = Number(monthText), day = Number(dayText);
  let year = Number(explicitYear);
  if (!year) {
    const semester = String(item?.semester || "").match(/^(\d{4})-(\d{4})-([12])/);
    const chinaNow = new Date(now.getTime() + 8 * 3600000);
    if (matches[0][1]) year = Number(matches[0][1]) + (Number(matches[0][2]) > month ? 1 : 0);
    else if (semester) year = Number(semester[3]) === 2 || month < 8 ? Number(semester[2]) : Number(semester[1]);
    else {
      const academic = Number(item?.academic_year || item?.academicYear);
      year = academic >= 2000 ? academic : chinaNow.getUTCFullYear();
      if (matches.length > 1 && Number(matches[0][2]) > month && (academic >= 2000 || chinaNow.getUTCMonth() >= 7)) year++;
    }
  }
  const local = new Date(Date.UTC(year, month - 1, day, Number(hour ?? 23), Number(minute ?? 59), Number(second ?? 59), 999));
  if (local.getUTCFullYear() !== year || local.getUTCMonth() !== month - 1 || local.getUTCDate() !== day || Number(hour || 0) > 23 || Number(minute || 0) > 59 || Number(second || 0) > 59) return null;
  const iso = text.trim().match(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/);
  if (iso) return Number.isNaN(Date.parse(iso[0])) ? null : new Date(iso[0]);
  return new Date(local.getTime() - 8 * 3600000);
}

export function assignmentExpired(item, now = new Date()) {
  const deadline = assignmentDeadline(item, now);
  return deadline !== null && deadline.getTime() < now.getTime();
}
