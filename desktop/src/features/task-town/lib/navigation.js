import { Grid, compressPath, findPath } from './path-grid.js';

export class CompanyNavigation {
  constructor(physics, bounds, cellSize = .2) {
    this.physics = physics; this.cellSize = cellSize; this.state = 'idle'; this.goal = null; this.path = [];
    this.rebuild(bounds);
  }

  rebuild(bounds) {
    this.bounds = bounds;
    this.margin = this.physics.player.shapes[0].radius + .035;
    this.obstacles = this.physics.navigationObstacles();
    const { left, right, back, front } = bounds;
    const columns = Math.ceil((right - left) / this.cellSize) + 1, rows = Math.ceil((front - back) / this.cellSize) + 1;
    this.grid = new Grid(columns, rows);
    for (let z = 0; z < rows; z++) for (let x = 0; x < columns; x++) this.grid.setWalkableAt(x, z, this.walkable(this.world(x, z)));
    if (this.state === 'walking' && this.goal) this.moveTo(this.physics.player.position, this.goal);
  }

  world(x, z) { return { x: this.bounds.left + x * this.cellSize, z: this.bounds.back + z * this.cellSize }; }
  walkable(point, margin = this.margin) {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.z)) return false;
    const b = this.bounds, m = margin;
    return point.x > b.left + m && point.x < b.right - m && point.z > b.back + m && point.z < b.front - m
      && !this.obstacles.some(obstacle => point.x >= obstacle.left - m && point.x <= obstacle.right + m && point.z >= obstacle.back - m && point.z <= obstacle.front + m);
  }

  clearSegment(from, to, margin = this.margin) {
    const samples = Math.max(1, Math.ceil(Math.hypot(to.x - from.x, to.z - from.z) / (this.cellSize / 3)));
    for (let index = 0; index <= samples; index++) {
      const t = index / samples;
      if (!this.walkable({ x: from.x + (to.x - from.x) * t, z: from.z + (to.z - from.z) * t }, margin)) return false;
    }
    return true;
  }

  cell(point, margin = this.margin) {
    const x = Math.round((point.x - this.bounds.left) / this.cellSize), z = Math.round((point.z - this.bounds.back) / this.cellSize);
    const options = [];
    for (let dz = -2; dz <= 2; dz++) for (let dx = -2; dx <= 2; dx++) {
      const nx = x + dx, nz = z + dz;
      if (!this.grid.isWalkableAt(nx, nz)) continue;
      const candidate = this.world(nx, nz);
      if (this.clearSegment(point, candidate, margin)) options.push({ x: nx, z: nz, distance: Math.hypot(point.x - candidate.x, point.z - candidate.z) });
    }
    options.sort((a, b) => a.distance - b.distance);
    return options[0];
  }

  plan(from, to) {
    // A held movement key can leave the Cannon body slightly inside its contact margin.
    const startMargin = this.walkable(from) ? this.margin : this.physics.player.shapes[0].radius - .04;
    if (!this.walkable(from, startMargin) || !this.walkable(to)) return null;
    if (this.clearSegment(from, to)) return [{ x: to.x, z: to.z }];
    const start = this.cell(from, startMargin), end = this.cell(to);
    if (!start || !end) return null;
    const cells = findPath(this.grid, start.x, start.z, end.x, end.z);
    if (!cells.length) return null;
    const route = compressPath(cells).map(([x, z]) => this.world(x, z));
    route.push({ x: to.x, z: to.z });
    let previous = from;
    for (let index = 0; index < route.length; index++) {
      if (!this.clearSegment(previous, route[index], index === 0 ? startMargin : this.margin)) return null;
      previous = route[index];
    }
    return route;
  }

  moveTo(from, destination) {
    const goal = { x: destination.x, z: destination.z };
    const route = this.plan(from, goal);
    this.goal = goal; this.path = route || []; this.state = route ? 'walking' : 'blocked';
    this.progressAt = 0; this.progressPoint = { x: from.x, z: from.z };
    return Boolean(route);
  }

  cancel() { this.state = 'idle'; this.path = []; this.goal = null; }

  velocity(position, dt, speed = 4.4) {
    if (this.state !== 'walking') return { x: 0, z: 0 };
    if (Math.hypot(position.x - this.goal.x, position.z - this.goal.z) < .12) {
      this.state = 'arrived'; this.path = []; return { x: 0, z: 0 };
    }
    while (this.path.length && Math.hypot(position.x - this.path[0].x, position.z - this.path[0].z) < .035) this.path.shift();
    const target = this.path[0];
    if (!target) { this.state = 'blocked'; return { x: 0, z: 0 }; }
    this.progressAt += dt;
    if (this.progressAt > 1.2) {
      if (Math.hypot(position.x - this.progressPoint.x, position.z - this.progressPoint.z) < .06) { this.state = 'blocked'; this.path = []; return { x: 0, z: 0 }; }
      this.progressAt = 0; this.progressPoint = { x: position.x, z: position.z };
    }
    const dx = target.x - position.x, dz = target.z - position.z, distance = Math.hypot(dx, dz);
    const velocity = Math.min(speed, distance * 7);
    return { x: dx / distance * velocity, z: dz / distance * velocity };
  }
}
