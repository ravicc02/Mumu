const PREFIX = 'studio:onboarding:v1:';
export function onboardingComplete(scope, storage = localStorage) {
  if (!scope) return false;
  try { const value = JSON.parse(storage.getItem(PREFIX + scope) || 'null'); return value?.completed === true && value?.version === 1; } catch { return false; }
}
export function completeOnboarding(scope, choices, storage = localStorage) {
  if (!scope) throw new Error('尚未连接本地数据目录，请重新检测环境。');
  const value = { version: 1, completed: true, completedAt: new Date().toISOString(), model: choices.model === 'configured' ? 'configured' : 'skipped', platform: choices.platform === 'authorized' ? 'authorized' : 'skipped' };
  try { storage.setItem(PREFIX + scope, JSON.stringify(value)); }
  catch { throw new Error('无法保存初始化进度，请检查本地存储权限。'); }
  return value;
}
export function environmentReady(state) {
  return state?.status === 'ready' && state.backendReady === true && state.items?.length > 0 && state.items.every(item => item.status === 'ready');
}
