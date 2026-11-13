import { test } from 'node:test';
import assert from 'node:assert/strict';
import { companyLayout, companyRoomForTask, companyRoute, companyLiveTask } from '../src/lib/task-company.js';
import { CompanyPhysics } from '../src/lib/task-company-physics.js';
import { createCompanyScreenFeed } from '../src/lib/task-company-screen.js';

const task = (id, status) => ({ id, status, account_id: 'account', assignment_id: id, assignment_title: id, task_kind: 'assignment' });

test('company rooms use actual task states and retain all individual intervention colors', () => {
  const rows = ['running', 'queued', 'completed', 'needs_intervention', 'interrupted', 'incomplete', 'paused', 'stopped'].map((status, i) => task(String(i), status));
  assert.deepEqual(rows.map(companyRoomForTask), ['office', 'office', 'lounge', 'intervention', 'intervention', 'intervention', 'intervention', 'intervention']);
  const layout = companyLayout(rows);
  assert.equal(layout.residents.length, rows.length);
  assert.equal(layout.residents.find(item => item.task.status === 'running').pose, 'working');
  assert.equal(layout.residents.find(item => item.task.status === 'completed').pose, 'sleeping');
  assert.ok(layout.residents.filter(item => item.room === 'intervention').every(item => item.pose === 'suspended'));
  assert.equal(new Set(layout.residents.filter(item => ['needs_intervention', 'interrupted', 'incomplete', 'paused'].includes(item.task.status)).map(item => item.color)).size, 4);
});

test('capacity expands furniture and room depth without overlapping resident slots', () => {
  for (const status of ['running', 'completed', 'incomplete']) {
    const tasks = Array.from({ length: 100 }, (_, i) => task(String(i), status));
    const layout = companyLayout(tasks), reversed = companyLayout([...tasks].reverse());
    assert.deepEqual(layout.residents, reversed.residents);
    assert.equal(new Set(layout.residents.map(item => item.x + ':' + item.z)).size, 100);
    assert.ok(layout.residents.every(item => item.z > layout.bounds.back && item.z < layout.bounds.front));
    if (status === 'running') assert.equal(layout.deskRows, 25);
    if (status === 'completed') assert.equal(layout.sofaRows, 25);
    if (status === 'incomplete') assert.equal(layout.suspensionRows, 25);
  }
});

test('state transitions preserve task identity and use the corridor between rooms', () => {
  const before = companyLayout([task('one', 'running')]).residents[0];
  const after = companyLayout([task('one', 'completed')]).residents[0];
  assert.equal(before.key, after.key); assert.equal(before.shape, after.shape); assert.notEqual(before.room, after.room);
  const route = companyRoute({ x: -10, z: -5 }, { x: 10, z: -5 });
  assert.ok(route.some(point => point.x === -10 && point.z === -.05));
  assert.ok(route.some(point => point.x === 10 && point.z === -.05));
  const leavingDesk = companyRoute({ x: -5.6, z: 3.25 }, { x: -10, z: -4.25 });
  assert.deepEqual(leavingDesk[0], { x: -5.6, z: 3.95 });
  assert.equal(leavingDesk[1].x, -2.8, 'worker first steps away from the desk and into an aisle');
  assert.ok(leavingDesk.some(point => point.x === -5.6 && point.z === -2.35), 'lounge route uses the space beside the sofa');
});

function walk(physics, seconds, dx, dz) { for (let i = 0; i < seconds * 60; i++) physics.step(1 / 60, dx, dz); }

test('jump requires ground support, follows gravity, and becomes available again after landing', () => {
  const physics = new CompanyPhysics(companyLayout([]));
  try {
    assert.equal(physics.jump(), false, 'spawning in the air is not ground contact');
    walk(physics, 1, 0, 0);
    const ground = physics.player.position.y;
    assert.equal(physics.grounded, true);
    assert.equal(physics.jump(), true);
    assert.equal(physics.jump(), false, 'no second impulse while airborne');
    let peak = ground;
    for (let i = 0; i < 120; i++) { physics.step(1 / 60, 0, 0); peak = Math.max(peak, physics.player.position.y); }
    assert.ok(peak > ground + .9, 'the body really leaves the floor');
    assert.ok(Math.abs(physics.player.position.y - ground) < .02);
    assert.equal(physics.grounded, true);
    assert.equal(physics.jump(), true);
    physics.teleport(0, 1);
    assert.equal(physics.jump(), false, 'teleport clears previous support');
  } finally { physics.dispose(); }
});

test('jump preserves horizontal movement and wall collisions', () => {
  const physics = new CompanyPhysics(companyLayout([]));
  try {
    physics.teleport(3, 0); walk(physics, 1, 0, 0);
    assert.equal(physics.jump(), true);
    walk(physics, 1, 0, -4);
    assert.ok(physics.player.position.z < 0, 'movement works while jumping');
    assert.ok(physics.player.position.z > -.85, 'jump does not pass through the room partition');
  } finally { physics.dispose(); }
});
test('character physics blocks room partitions and furniture while leaving real doorways passable', () => {
  const physics = new CompanyPhysics(companyLayout([]));
  try {
    physics.teleport(3, 0); walk(physics, 1, 0, -4);
    assert.ok(physics.player.position.z > -.85, 'glass partition remains solid');
    physics.teleport(0, 0); walk(physics, 1, 0, -4);
    assert.ok(physics.player.position.z < -3, 'doorway is open');
    physics.teleport(4, -4); walk(physics, 1, 4, 0);
    assert.ok(physics.player.position.x < 4.55, 'room side wall remains solid');
    physics.teleport(-11, 4.5); walk(physics, 1, 0, -4);
    assert.ok(physics.player.position.z > 3.4, 'desk blocks walking through it');
    assert.deepEqual([physics.world.gravity.x, physics.world.gravity.y, physics.world.gravity.z], [0, -12, 0]);
  } finally { physics.dispose(); }
});

test('wall display selects only an existing running browser, with account and kind identity preserved', () => {
  const rows = [task('queued', 'queued'), task('review', 'needs_intervention'), { ...task('one', 'running'), browser_available: true }, { ...task('two', 'running'), task_kind: 'learning', browser_available: true }];
  assert.equal(companyLiveTask(rows).id, 'one');
  assert.equal(companyLiveTask(rows, 'learning:account:two').id, 'two');
  assert.equal(companyLiveTask([task('none', 'running')]), null);
});

test('passive wall display sends only its viewer ticket, ignores stale sockets, and closes without stopping a task', async () => {
  const sockets = [], calls = [], frames = [];
  class Socket {
    constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
    send(payload) { this.sent.push(JSON.parse(payload)); }
    close() { this.closed = true; }
  }
  const api = { browserSession: async (kind, id) => { calls.push([kind, id]); return { token: id }; }, browserUrl: (kind, id) => 'ws://fixture/' + kind + '/' + id };
  const feed = createCompanyScreenFeed(api, { status() {}, frame: frame => frames.push(frame) }, Socket);
  await feed.select(task('one', 'running')); sockets[0].onopen();
  const old = sockets[0].onmessage;
  await feed.select(task('one', 'running')); assert.equal(calls.length, 1);
  await feed.select({ ...task('two', 'running'), task_kind: 'learning' }); sockets[1].onopen();
  old({ data: JSON.stringify({ type: 'frame', image: 'stale' }) });
  sockets[1].onmessage({ data: JSON.stringify({ type: 'frame', image: 'current' }) });
  assert.deepEqual(frames, [{ type: 'frame', image: 'current' }]);
  assert.deepEqual(sockets[0].sent, [{ token: 'one' }]); assert.deepEqual(sockets[1].sent, [{ token: 'two' }]);
  feed.dispose(); assert.ok(sockets.every(socket => socket.closed));
  assert.deepEqual(calls, [['assignment', 'one'], ['learning', 'two']]);
});
