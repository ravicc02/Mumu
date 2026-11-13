export const settingsSections = ["model", "assignments", "learning", "system"];
export const intervalFactors = { s: 1, min: 60, h: 3600 };

export function accountSchedule(account) {
  return {
    account_id: account.id, enabled: Boolean(account.auto_collect_enabled),
    auto_execute: Boolean(account.auto_execute_enabled),
    interval_value: Number(account.auto_collect_interval_value || account.auto_collect_interval_minutes || 360),
    interval_unit: account.auto_collect_interval_unit || "min"
  };
}

export function scheduleError(schedule) {
  const seconds = Number(schedule.interval_value) * intervalFactors[schedule.interval_unit];
  if (!Number.isFinite(seconds) || seconds < 5 || seconds > 604800) return "检测间隔须为 5 秒至 7 天";
  if (schedule.auto_execute && !schedule.enabled) return "自动完成需要启用自动检测";
  return "";
}

export function restorePreferenceDraft(preferences, schedules, cached) {
  const restored = structuredClone(preferences);
  for (const section of ["assignments", "learning", "system"]) {
    if (cached?.[section] && typeof cached[section] === "object") {
      for (const key of Object.keys(restored[section])) {
        if (Object.hasOwn(cached[section], key)) restored[section][key] = cached[section][key];
      }
    }
  }
  return {
    preferences: restored,
    schedules: schedules.map(schedule => {
      const saved = (Array.isArray(cached?.schedules) ? cached.schedules : []).find(item => item?.account_id === schedule.account_id);
      return saved ? { ...schedule, ...Object.fromEntries(Object.keys(schedule).map(key => [key, saved[key] ?? schedule[key]])) } : schedule;
    })
  };
}
