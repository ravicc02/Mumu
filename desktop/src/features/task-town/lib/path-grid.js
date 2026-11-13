const DIRECTIONS = [
  [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1],
  [1, 1, Math.SQRT2], [1, -1, Math.SQRT2],
  [-1, 1, Math.SQRT2], [-1, -1, Math.SQRT2],
];

export class Grid {
  constructor(width, height, walkable = true) {
    this.width = width;
    this.height = height;
    this.cells = Array.from({ length: height }, () => Array(width).fill(walkable));
  }

  isInside(x, z) {
    return x >= 0 && z >= 0 && x < this.width && z < this.height;
  }

  isWalkableAt(x, z) {
    return this.isInside(x, z) && this.cells[z][x];
  }

  setWalkableAt(x, z, walkable) {
    if (this.isInside(x, z)) this.cells[z][x] = Boolean(walkable);
  }
}

class MinHeap {
  constructor() { this.items = []; }

  push(item) {
    const items = this.items;
    items.push(item);
    let index = items.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (items[parent].estimate <= item.estimate) break;
      items[index] = items[parent];
      index = parent;
    }
    items[index] = item;
  }

  pop() {
    const items = this.items;
    if (!items.length) return null;
    const root = items[0], tail = items.pop();
    if (!items.length) return root;
    let index = 0;
    while (true) {
      const left = index * 2 + 1, right = left + 1;
      if (left >= items.length) break;
      const child = right < items.length && items[right].estimate < items[left].estimate ? right : left;
      if (items[child].estimate >= tail.estimate) break;
      items[index] = items[child];
      index = child;
    }
    items[index] = tail;
    return root;
  }
}

const key = (x, z) => z * 100000 + x;
const heuristic = (x, z, tx, tz) => {
  const dx = Math.abs(tx - x), dz = Math.abs(tz - z);
  return Math.max(dx, dz) + (Math.SQRT2 - 1) * Math.min(dx, dz);
};

export function findPath(grid, sx, sz, tx, tz) {
  if (!grid.isWalkableAt(sx, sz) || !grid.isWalkableAt(tx, tz)) return [];
  if (sx === tx && sz === tz) return [[sx, sz]];
  const open = new MinHeap(), score = new Map(), previous = new Map(), closed = new Set();
  open.push({ x: sx, z: sz, cost: 0, estimate: heuristic(sx, sz, tx, tz) });
  score.set(key(sx, sz), 0);
  while (open.items.length) {
    const current = open.pop(), currentKey = key(current.x, current.z);
    if (closed.has(currentKey)) continue;
    if (current.x === tx && current.z === tz) {
      const cells = [[current.x, current.z]];
      let cursor = currentKey;
      while (previous.has(cursor)) {
        cursor = previous.get(cursor);
        cells.push([cursor % 100000, Math.floor(cursor / 100000)]);
      }
      return cells.reverse();
    }
    closed.add(currentKey);
    for (const [dx, dz, cost] of DIRECTIONS) {
      const x = current.x + dx, z = current.z + dz;
      if (!grid.isWalkableAt(x, z)) continue;
      if (dx && dz && (!grid.isWalkableAt(current.x + dx, current.z) || !grid.isWalkableAt(current.x, current.z + dz))) continue;
      const nextKey = key(x, z);
      if (closed.has(nextKey)) continue;
      const candidate = current.cost + cost;
      if (candidate >= (score.get(nextKey) ?? Infinity)) continue;
      score.set(nextKey, candidate);
      previous.set(nextKey, currentKey);
      open.push({ x, z, cost: candidate, estimate: candidate + heuristic(x, z, tx, tz) });
    }
  }
  return [];
}

export function compressPath(cells) {
  if (cells.length < 3) return cells.map(cell => cell.slice());
  const output = [cells[0]];
  let previousDirection = null;
  for (let index = 1; index < cells.length; index += 1) {
    const previous = cells[index - 1], current = cells[index];
    const direction = [Math.sign(current[0] - previous[0]), Math.sign(current[1] - previous[1])];
    const sameDirection = previousDirection && direction[0] === previousDirection[0] && direction[1] === previousDirection[1];
    if (!sameDirection && index > 1) output.push(previous);
    previousDirection = direction;
  }
  output.push(cells[cells.length - 1]);
  return output;
}
