import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nearestTownResident, TOWN_MOVE_KEYS, townTypingTarget } from '../src/features/task-town/lib/interaction.js';

test('proximity offers only one nearby, visible, settled resident', () => {
  const bots = [{ key: 'one', x: 1.2, z: 0 }, { key: 'two', x: 1.6, z: 0 },
    { key: 'hidden', x: .1, z: 0, visible: false }, { key: 'crossing', x: .2, z: 0, transitioning: true }];
  assert.equal(nearestTownResident({ x: 0, z: 0 }, bots), 'one');
  assert.equal(nearestTownResident({ x: -4, z: 0 }, bots), null);
});

test('proximity hysteresis keeps the hint stable until another bot is clearly closer', () => {
  const bots = [{ key: 'one', x: 1.5, z: 0 }, { key: 'two', x: 1.4, z: 0 }];
  assert.equal(nearestTownResident({ x: 0, z: 0 }, bots, 'one'), 'one');
  bots[1].x = .9;
  assert.equal(nearestTownResident({ x: 0, z: 0 }, bots, 'one'), 'two');
  assert.equal(nearestTownResident({ x: -.7, z: 0 }, [bots[0]], 'one'), 'one');
  assert.equal(nearestTownResident({ x: -1, z: 0 }, [bots[0]], 'one'), null);
  assert.equal(nearestTownResident({ x: 0, z: 0 }, [], 'one'), null);
});

test('movement keys support both layouts without consuming text editing or dialogs', () => {
  assert.equal(TOWN_MOVE_KEYS.size, 8);
  assert.ok(TOWN_MOVE_KEYS.has('w') && TOWN_MOVE_KEYS.has('arrowleft'));
  assert.equal(townTypingTarget({ closest: () => ({}) }), true);
  assert.equal(townTypingTarget({ closest: () => null }), false);
  assert.equal(townTypingTarget(null), false);
});
