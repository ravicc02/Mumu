import { World, Body, Box, Sphere, Vec3, Material, ContactMaterial, SAPBroadphase } from 'cannon-es';

export class CompanyPhysics {
  constructor(layout) {
    this.world = new World({ gravity: new Vec3(0, -12, 0) });
    this.world.broadphase = new SAPBroadphase(this.world);
    this.world.solver.iterations = 8;
    this.material = new Material('company-floor');
    this.world.addContactMaterial(new ContactMaterial(this.material, this.material, { friction: 0, restitution: 0 }));
    this.player = new Body({ mass: 1, shape: new Sphere(.42), material: this.material, fixedRotation: true,
      position: new Vec3(layout.spawn.x, .55, layout.spawn.z), linearDamping: .05, allowSleep: false });
    this.world.addBody(this.player);
    this.grounded = false;
    this.obstacles = [];
    this.configure(layout);
  }

  box(x, z, width, depth, height = 3, y = height / 2) {
    const body = new Body({ mass: 0, material: this.material, shape: new Box(new Vec3(width / 2, height / 2, depth / 2)), position: new Vec3(x, y, z) });
    this.world.addBody(body); this.obstacles.push(body);
  }

  configure(layout) {
    for (const obstacle of this.obstacles) this.world.removeBody(obstacle);
    this.obstacles = [];
    const { left, right, back, front } = layout.bounds;
    this.box(0, (back + front) / 2, 32, front - back + 2, .2, -.1);
    this.box(left, (back + front) / 2, .3, front - back);
    this.box(right, (back + front) / 2, .3, front - back);
    this.box(0, back, 30, .3); this.box(0, front, 30, .3);
    for (const x of [-5, 5]) this.box(x, (back - 1.3) / 2, .22, -1.3 - back);
    for (const center of [-10, 0, 10]) for (const side of [-1, 1]) this.box(center + side * 3.1, -1.3, 3.8, .22);
    for (let row = 0; row < layout.deskRows; row++) for (const x of [-11, -5.6, 5.6, 11]) {
      this.box(x, 2.4 + row * 3.3, 3.35, 1.35, .95);
    }
    for (let row = 0; row < layout.sofaRows; row++) this.box(-10, -4.25 - row * 2.3, 7.6, 1.45, .7);
    this.box(-13.5, -2.35, 1.15, 1.1, .47);
    this.box(10, -5.6, 5.5, 1.55, 1.15);
  }

  teleport(x, z) {
    this.player.position.set(x, .55, z); this.player.velocity.set(0, 0, 0);
    this.grounded = false;
    this.player.aabbNeedsUpdate = true;
  }

  jump() {
    if (!this.grounded) return false;
    this.player.velocity.y = 5.2;
    this.grounded = false;
    return true;
  }

  step(dt, dx, dz) {
    this.player.velocity.x = dx; this.player.velocity.z = dz;
    this.world.step(1 / 60, Math.min(dt, .05), 4);
    this.grounded = this.player.velocity.y <= .5 && this.world.contacts.some(contact => {
      if (!contact.enabled) return false;
      if (contact.bi === this.player) return -contact.ni.y > .5;
      if (contact.bj === this.player) return contact.ni.y > .5;
      return false;
    });
    return this.player.position;
  }

  navigationObstacles() {
    return this.obstacles.filter(body => body.shapes[0].halfExtents.y > .13).map(body => {
      const extent = body.shapes[0].halfExtents;
      return { left: body.position.x - extent.x, right: body.position.x + extent.x,
        back: body.position.z - extent.z, front: body.position.z + extent.z };
    });
  }

  dispose() { for (const body of [...this.world.bodies]) this.world.removeBody(body); this.obstacles = []; }
}
