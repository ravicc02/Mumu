import { test } from 'node:test';
import assert from 'node:assert/strict';
import { companyLayout } from '../src/lib/task-company.js';
import { CompanyPhysics } from '../src/lib/task-company-physics.js';
import { CompanyNavigation } from '../src/lib/task-company-navigation.js';

function setup(t) {
  const layout = companyLayout([]), physics = new CompanyPhysics(layout);
  t.after(() => physics.dispose());
  return { layout, physics, navigation: new CompanyNavigation(physics, layout.bounds) };
}
function finish(physics, navigation, seconds = 20) {
  for (let i = 0; i < seconds * 60 && navigation.state === 'walking'; i++) {
    const velocity = navigation.velocity(physics.player.position, 1 / 60);
    physics.step(1 / 60, velocity.x, velocity.z);
  }
}

test('floor clicks arrive at the chosen coordinate using the actual physics body', t => {
  const { physics, navigation } = setup(t), goal = { x: 1.3, z: 8 };
  assert.equal(navigation.moveTo(physics.player.position, goal), true);
  finish(physics, navigation);
  assert.equal(navigation.state, 'arrived');
  assert.ok(Math.hypot(physics.player.position.x - goal.x, physics.player.position.z - goal.z) < .13);
});

test('click routes pass through room doorways and around desks and the sofa', t => {
  const { physics, navigation } = setup(t);
  for (const goal of [{ x: 0, z: -5 }, { x: 10, z: -3.3 }, { x: -10, z: -7 }, { x: 11, z: 4.2 }]) {
    assert.equal(navigation.moveTo(physics.player.position, goal), true, JSON.stringify(goal));
    let previous = { ...physics.player.position };
    for (const point of navigation.path) { assert.ok(navigation.clearSegment(previous, point), 'path intersects an inflated collision obstacle'); previous = point; }
    finish(physics, navigation, 35);
    assert.equal(navigation.state, 'arrived', 'did not reach ' + JSON.stringify(goal));
    assert.ok(Math.hypot(physics.player.position.x - goal.x, physics.player.position.z - goal.z) < .13);
  }
});

test('blocked furniture and outside targets do not produce a fake arrival or push forever', t => {
  const { physics, navigation } = setup(t);
  for (const goal of [{ x: 5.6, z: 2.4 }, { x: -10, z: -4.25 }, { x: 50, z: 0 }]) {
    assert.equal(navigation.moveTo(physics.player.position, goal), false);
    assert.equal(navigation.state, 'blocked');
    assert.deepEqual(navigation.velocity(physics.player.position, 1 / 60), { x: 0, z: 0 });
  }
  navigation.moveTo(physics.player.position, { x: 1, z: 8 });
  for (let i = 0; i < 100; i++) navigation.velocity(physics.player.position, 1 / 60);
  assert.equal(navigation.state, 'blocked');
});

test('new clicks replace the goal and keyboard cancellation never resumes an old path', t => {
  const { physics, navigation } = setup(t);
  navigation.moveTo(physics.player.position, { x: 1, z: 8 });
  navigation.moveTo(physics.player.position, { x: -1, z: 5 });
  assert.deepEqual(navigation.goal, { x: -1, z: 5 });
  navigation.cancel();
  assert.equal(navigation.state, 'idle'); assert.equal(navigation.goal, null);
  assert.deepEqual(navigation.velocity(physics.player.position, 1 / 60), { x: 0, z: 0 });
});

test('click movement can take over after keyboard movement has pressed the body against a wall', t => {
  const { physics, navigation } = setup(t);
  physics.teleport(4, -4);
  for (let i = 0; i < 60; i++) physics.step(1 / 60, 4, 0);
  assert.equal(navigation.moveTo(physics.player.position, { x: 2, z: -4 }), true);
  finish(physics, navigation);
  assert.equal(navigation.state, 'arrived');
});
