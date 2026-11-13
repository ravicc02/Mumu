export const TOWN_MOVE_KEYS = new Set(['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright']);

export function nearestTownResident(position, residents, previousKey = null) {
  let nearest = null, previous = null;
  for (const resident of residents) {
    if (resident.visible === false || resident.transitioning) continue;
    const distance = Math.hypot(resident.x - position.x, resident.z - position.z);
    if (resident.key === previousKey && distance <= 2.3) previous = { key: resident.key, distance };
    if (distance <= 1.95 && (!nearest || distance < nearest.distance)) nearest = { key: resident.key, distance };
  }
  if (previous && (!nearest || previous.distance <= nearest.distance + .25)) return previous.key;
  return nearest?.key ?? null;
}

export function townTypingTarget(target) {
  return Boolean(target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="dialog"]'));
}
