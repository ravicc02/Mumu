import { TOWN_DISTRICTS, townDistrict, townTaskKey, stableSeed, botShape } from './task-town.js';

export const COMPANY_ROOMS = [
  { id: 'office', label: '办公区', color: '#458bca', statuses: ['active'], x: 0, z: 4 },
  { id: 'lounge', label: '休息区', color: '#36a889', statuses: ['completed'], x: -10, z: -4 },
  { id: 'intervention', label: '介入区', color: '#d48c5c', statuses: ['review', 'interrupted', 'incomplete', 'paused'], x: 0, z: -4 },
  { id: 'boss', label: 'Boss 工作室', color: '#6879a0', statuses: [], x: 10, z: -4 },
];

export function companyRoomForTask(task) {
  const district = townDistrict(task);
  return district === 'active' ? 'office' : district === 'completed' ? 'lounge' : 'intervention';
}

export function companyLayout(tasks) {
  const groups = new Map(COMPANY_ROOMS.map(room => [room.id, []]));
  for (const task of tasks) groups.get(companyRoomForTask(task)).push(task);
  for (const group of groups.values()) group.sort((a, b) => townTaskKey(a).localeCompare(townTaskKey(b)));
  const deskRows = Math.max(3, Math.ceil(groups.get('office').length / 4));
  const sofaRows = Math.max(1, Math.ceil(groups.get('lounge').length / 4));
  const suspensionRows = Math.max(2, Math.ceil(groups.get('intervention').length / 4));
  const roomDepth = Math.max(11, Math.max(sofaRows, suspensionRows) * 2.3 + 5);
  const officeDepth = deskRows * 3.3 + 2.6;
  const residents = [];
  for (const [room, group] of groups) group.forEach((task, index) => {
    const district = townDistrict(task), seed = stableSeed(townTaskKey(task));
    const column = index % 4, row = Math.floor(index / 4);
    const x = room === 'office' ? [-5.6, 5.6, -11, 11][column] : (room === 'lounge' ? -10 : 0) + (column - 1.5) * (room === 'lounge' ? 1.75 : 2.05);
    const z = room === 'office' ? 3.25 + row * 3.3 : -4.25 - row * 2.3;
    residents.push({ key: townTaskKey(task), task, district, room, seed, shape: botShape(townTaskKey(task)),
      color: TOWN_DISTRICTS.find(item => item.id === district).color, x, z,
      y: room === 'office' ? 1.13 : room === 'lounge' ? 1.02 : 1.8,
      scale: .97, pose: room === 'office' ? 'working' : room === 'lounge' ? 'sleeping' : 'suspended' });
  });
  return { residents, deskRows, sofaRows, suspensionRows, roomDepth, officeDepth,
    bounds: { left: -15, right: 15, back: -roomDepth - 1.2, front: officeDepth },
    spawn: { x: 0, z: 6.4 }, boss: { x: 10, z: -4.8, seatX: 10, seatZ: -6.9, exitX: 10, exitZ: -3.25 },
    rooms: COMPANY_ROOMS.map(room => ({ ...room, count: groups.get(room.id).length })) };
}

export function companyRoomAt(point) {
  if (point.z >= -1.3) return 'office';
  return point.x < -5 ? 'lounge' : point.x > 5 ? 'boss' : 'intervention';
}

// Routes pass through the corridor and doorways rather than through partitions.
export function companyRoute(from, to) {
  const start = companyRoomAt(from), end = companyRoomAt(to), points = [];
  const doorX = room => room === 'lounge' ? -10 : room === 'boss' ? 10 : 0;
  const officeAisle = x => Math.abs(x) > 8 ? Math.sign(x) * 8.3 : Math.sign(x || 1) * 2.8;
  if (start !== end || start === 'office' || start === 'lounge') {
    if (start === 'office') points.push({ x: from.x, z: from.z + .7 }, { x: officeAisle(from.x), z: from.z + .7 }, { x: officeAisle(from.x), z: -.05 });
    else if (start === 'lounge') points.push({ x: from.x, z: from.z + 1.05 }, { x: -5.6, z: from.z + 1.05 }, { x: -5.6, z: -2.35 }, { x: -10, z: -2.35 }, { x: -10, z: -.05 });
    else points.push({ x: doorX(start), z: from.z }, { x: doorX(start), z: -.05 });
    if (end === 'office') points.push({ x: officeAisle(to.x), z: -.05 }, { x: officeAisle(to.x), z: to.z + .7 }, { x: to.x, z: to.z + .7 });
    else if (end === 'lounge') points.push({ x: -10, z: -.05 }, { x: -10, z: -2.35 }, { x: -5.6, z: -2.35 }, { x: -5.6, z: to.z + 1.05 }, { x: to.x, z: to.z + 1.05 });
    else points.push({ x: doorX(end), z: -.05 }, { x: doorX(end), z: to.z });
  }
  points.push({ x: to.x, z: to.z });
  return points;
}

export function companyLiveTask(tasks, preferredKey = '') {
  const candidates = tasks.filter(task => task.status === 'running' && task.browser_available === true);
  return candidates.find(task => townTaskKey(task) === preferredKey) || candidates[0] || null;
}
