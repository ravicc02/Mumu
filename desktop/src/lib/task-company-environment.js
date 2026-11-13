import * as THREE from 'three';

export function buildCompanyEnvironment(kit, layout, screenTexture) {
  const { mesh, box, material, geometry, texture } = kit;
  const group = new THREE.Group();
  const paper = material('#f4f7f9'), graphite = material('#36424b'), metal = material('#73838b');
  const oak = material('#c8c5b7'), teal = material('#356c70'), green = material('#5a9b7b');
  const { back, front } = layout.bounds, depth = front - back;
  mesh(box(30.5, .28, depth + .5, .07), material('#c5cdd0'), group, 0, -.19, (back + front) / 2);
  mesh(box(30, .07, layout.officeDepth + 1.3, .02), material('#e1e8ec'), group, 0, -.025, (layout.officeDepth - 1.3) / 2);
  for (let x = -14; x < 15; x += 1) mesh(box(.018, .012, depth, .005), material('#d1dadd'), group, x, .02, (back + front) / 2, false);
  for (const [x, color] of [[-10, '#cce1d6'], [0, '#ebe0dc'], [10, '#d6dce7']]) {
    mesh(box(9.8, .055, -1.3 - back, .03), material(color), group, x, .015, (back - 1.3) / 2);
  }
  mesh(box(29.7, .04, 1.25, .02), material('#fcfdfd'), group, 0, .04, -.05);

  const cameraWalls = [];
  function wall(x, z, width, depth, color = '#f0f2f1', height = 3.5) {
    cameraWalls.push(mesh(box(width, height, depth, .03), material(color), group, x, height / 2, z));
    mesh(box(width + .03, .1, depth + .03, .02), metal, group, x, .1, z);
    mesh(box(width + .03, .08, depth + .03, .02), paper, group, x, height, z);
  }
  wall(0, back, 30, .2, '#c3ced0');
  for (const x of [-15, 15]) wall(x, (back + front) / 2, .2, depth, '#d8e1e4', 2.65);
  for (const x of [-5, 5]) wall(x, (back - 1.3) / 2, .15, -1.3 - back, '#dce3e4');
  const roomFronts = new Map();
  for (const center of [-10, 0, 10]) {
    const front = new THREE.Group(); group.add(front); roomFronts.set(center === -10 ? 'lounge' : center === 0 ? 'intervention' : 'boss', front);
    for (const side of [-1, 1]) {
      mesh(box(3.8, .65, .16, .03), paper, front, center + side * 3.1, .325, -1.3);
      mesh(box(3.8, 2.45, .06, .01), material('#a8c4ca', { transparent: true, opacity: .23, depthWrite: false }), front, center + side * 3.1, 1.9, -1.3, false);
      for (const offset of [-1.9, 1.9]) mesh(box(.055, 3.5, .14, .01), metal, front, center + side * 3.1 + offset, 1.75, -1.3);
    }
    mesh(box(10, .22, .2, .03), graphite, front, center, 3.5, -1.3);
  }
  function sign(text, x, z, color, parent = group) {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 160;
    const context = canvas.getContext('2d'); context.fillStyle = color; context.fillRect(0, 0, 640, 160);
    context.font = '600 54px "Microsoft YaHei", sans-serif'; context.textAlign = 'center'; context.textBaseline = 'middle';
    context.fillStyle = '#ffffff'; context.fillText(text, 320, 80);
    const paint = new THREE.MeshBasicMaterial({ map: texture(new THREE.CanvasTexture(canvas)), toneMapped: false }); kit.materials.add(paint);
    mesh(geometry(new THREE.PlaneGeometry(2.8, .7)), paint, parent, x, 3.15, z, false);
  }
  sign('休 息 区', -10, -1.17, '#377d68', roomFronts.get('lounge')); sign('介 入 区', 0, -1.17, '#a36c57', roomFronts.get('intervention')); sign('BOSS STUDIO', 10, -1.17, '#536981', roomFronts.get('boss'));

  const cylinder = (top, bottom, height, segments = 12) => geometry(new THREE.CylinderGeometry(top, bottom, height, segments));
  function plant(x, z, scale = 1) {
    const pot = new THREE.Group(); pot.position.set(x, 0, z); pot.scale.setScalar(scale); group.add(pot);
    mesh(cylinder(.25, .2, .48), paper, pot, 0, .25, 0);
    mesh(cylinder(.03, .04, .72, 8), oak, pot, 0, .77, 0);
    for (let i = 0; i < 5; i++) {
      const angle = i * 2.4;
      const leaf = mesh(geometry(new THREE.SphereGeometry(.3, 10, 8)), green, pot, Math.cos(angle) * .17, 1.05 + i * .06, Math.sin(angle) * .17);
      leaf.scale.set(1, .3, .6); leaf.rotation.z = Math.sin(angle) * .65;
    }
  }
  for (const x of [-14, 14]) for (const z of [1, front - 1.1, back + 1.1]) plant(x, z, 1.4);
  for (const x of [-6.1, 6.1]) plant(x, -2.5, 1.1);

  function chair(parent, x, z, color = '#556b7c') {
    const paint = material(color);
    mesh(box(1.05, .18, .94, .12), paint, parent, x, .61, z);
    mesh(box(1.05, 1, .15, .12), paint, parent, x, 1.05, z + .48);
    mesh(cylinder(.065, .075, .5), metal, parent, x, .31, z);
    for (let i = 0; i < 5; i++) {
      const angle = i * Math.PI * .4;
      const spoke = mesh(box(.065, .07, .5, .02), metal, parent, x + Math.sin(angle) * .22, .11, z + Math.cos(angle) * .22);
      spoke.rotation.y = angle;
      mesh(geometry(new THREE.SphereGeometry(.075, 8, 6)), graphite, parent, x + Math.sin(angle) * .43, .08, z + Math.cos(angle) * .43);
    }
  }
  function monitor(parent, x, y, z, scale = 1) {
    const item = new THREE.Group(); item.position.set(x, y, z); item.scale.setScalar(scale); parent.add(item);
    mesh(box(1.34, .88, .12, .05), graphite, item, 0, .48, 0);
    mesh(box(1.18, .7, .02, .02), material('#405d77', { emissive: '#2c4e75', emissiveIntensity: .4 }), item, 0, .49, .072, false);
    mesh(box(.5, .025, .015, .005), material('#7ca8c8'), item, -.21, .63, .088, false);
    mesh(box(.8, .025, .015, .005), material('#aac9dc'), item, -.07, .5, .088, false);
    mesh(box(.6, .025, .015, .005), material('#66bda5'), item, -.17, .38, .088, false);
    mesh(box(.09, .2, .1, .02), metal, item, 0, -.04, 0);
    mesh(box(.5, .05, .35, .03), graphite, item, 0, -.14, .04);
  }
  const deskPositions = [];
  for (let row = 0; row < layout.deskRows; row++) for (const x of [-11, -5.6, 5.6, 11]) {
    const z = 2.4 + row * 3.3; deskPositions.push({ x, z });
    mesh(box(3.35, .13, 1.35, .07), paper, group, x, 1.02, z);
    for (const dx of [-1.4, 1.4]) mesh(box(.1, 1.02, 1.04, .03), metal, group, x + dx, .51, z);
    mesh(box(3.2, .5, .06, .03), material('#a9bdc9'), group, x, 1.3, z - .61);
    monitor(group, x, 1.25, z - .23);
    mesh(box(.8, .04, .27, .025), material('#acb8c2'), group, x, 1.12, z + .38);
    for (let i = 0; i < 6; i++) mesh(box(.065, .015, .18, .006), paper, group, x - .29 + i * .115, 1.15, z + .38, false);
    mesh(geometry(new THREE.SphereGeometry(.09, 10, 8)), graphite, group, x + .64, 1.14, z + .4);
    mesh(cylinder(.09, .075, .21), material('#ebae71'), group, x + 1.05, 1.18, z + .2);
    chair(group, x, z + .85);
  }
  for (const x of [-11, -5.6, 5.6, 11]) {
    mesh(box(.055, .9, .055, .01), metal, group, x, 3.5, 5.8);
    mesh(box(2.4, .08, .25, .04), material('#f8fffd', { emissive: '#d7f3ed', emissiveIntensity: .5 }), group, x, 3.1, 5.8, false);
  }

  const upholstery = material('#4e977e', { roughness: .95 });
  for (let row = 0; row < layout.sofaRows; row++) {
    const z = -4.25 - row * 2.3;
    mesh(box(7.6, .38, 1.55, .15), graphite, group, -10, .24, z);
    mesh(box(7.6, 1.05, .35, .16), upholstery, group, -10, .9, z - .63);
    for (let i = 0; i < 4; i++) mesh(box(1.72, .33, 1.26, .17), upholstery, group, -10 + (i - 1.5) * 1.75, .62, z);
    for (const x of [-13.65, -6.35]) mesh(box(.35, .7, 1.55, .14), upholstery, group, x, .7, z);
  }
  mesh(box(1.15, .1, 1.1, .1), oak, group, -13.5, .42, -2.35);
  for (const x of [-13.9, -13.1]) mesh(box(.08, .42, .8, .025), graphite, group, x, .21, -2.35);
  plant(-14, -2.5); plant(-6, back + 1.2);
  sign('REST / RECHARGE', -10, back + .13, '#4d7e6d');

  for (let row = 0; row < layout.suspensionRows; row++) {
    const z = -4.25 - row * 2.3;
    mesh(box(9.7, .15, .18, .025), graphite, group, 0, 3.65, z);
    for (const x of [-4.65, 4.65]) mesh(box(.12, 3.65, .12, .025), metal, group, x, 1.82, z);
  }
  mesh(box(9.65, .03, layout.roomDepth - .7, .02), material('#e7d1c6', { transparent: true, opacity: .13, depthWrite: false }), group, 0, 3.8, (back - 1.3) / 2, false);
  sign('WAITING FOR YOU', 0, back + .13, '#9c7165');

  mesh(box(5.5, .18, 1.6, .09), oak, group, 10, 1.08, -5.6);
  mesh(box(4.9, 1.05, .16, .03), material('#4f626e'), group, 10, .52, -5.6);
  monitor(group, 8.5, 1.32, -5.6, .8); chair(group, 10, -6.9, '#3a4c5e');
  mesh(box(.95, .035, .55, .025), paper, group, 11.6, 1.2, -5.5);
  const screenZ = back + .2;
  mesh(box(8.2, 4.5, .16, .08), graphite, group, 10, 2.5, screenZ);
  const screenPaint = new THREE.MeshBasicMaterial({ map: screenTexture, toneMapped: false }); kit.materials.add(screenPaint);
  const screen = mesh(geometry(new THREE.PlaneGeometry(7.8, 4.12)), screenPaint, group, 10, 2.5, screenZ + .091, false);
  mesh(box(8.1, .055, .06, .02), material('#5dcbb7', { emissive: '#5dcbb7', emissiveIntensity: .7 }), group, 10, .26, screenZ + .1, false);
  plant(6.1, -8); plant(13.9, -8);
  return { group, screen, deskPositions, roomFronts, cameraWalls };
}
