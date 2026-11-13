const PREFIX = 'studio:onboarding:v1:';
const COMPLETION_KEY = 'studio:onboarding:complete:v1';
const completed = value => value?.completed === true && value?.version === 1;
export function onboardingComplete(scope, storage = localStorage) {
  if (!scope) return false;
  try { return completed(JSON.parse(storage.getItem(PREFIX + scope) || 'null')); } catch { return false; }
}
export function onboardingPreviouslyCompleted(storage = localStorage) {
  try {
    if (completed(JSON.parse(storage.getItem(COMPLETION_KEY) || 'null'))) return true;
    for (let index = 0; index < Number(storage.length || 0); index += 1) {
      const key = storage.key?.(index);
      if (key?.startsWith(PREFIX) && completed(JSON.parse(storage.getItem(key) || 'null'))) return true;
    }
  } catch { /* Storage can be unavailable in private browser sessions. */ }
  return false;
}
export function completeOnboarding(scope, choices, storage = localStorage) {
  if (!scope) throw new Error('尚未连接本地数据目录，请重新检测环境。');
  const value = { version: 1, completed: true, completedAt: new Date().toISOString(), model: choices.model === 'configured' ? 'configured' : 'skipped', platform: choices.platform === 'authorized' ? 'authorized' : 'skipped' };
  try {
    storage.setItem(PREFIX + scope, JSON.stringify(value));
    storage.setItem(COMPLETION_KEY, JSON.stringify(value));
  }
  catch { throw new Error('无法保存初始化进度，请检查本地存储权限。'); }
  return value;
}
export function environmentReady(state) {
  return state?.status === 'ready' && state.backendReady === true && state.items?.length > 0 && state.items.every(item => item.status === 'ready');
}
