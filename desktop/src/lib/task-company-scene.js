import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { companyLayout, companyRoomAt, companyRoute } from './task-company.js';
import { CompanyPhysics } from './task-company-physics.js';
import { CompanyNavigation } from './task-company-navigation.js';
import { createCompanyCharacters } from './task-company-characters.js';
import { buildCompanyEnvironment } from './task-company-environment.js';
import { nearestTownResident, TOWN_MOVE_KEYS, townTypingTarget } from './task-town-interaction.js';

function resourceKit() {
  const geometries = new Set(), materials = new Set(), textures = new Set(), boxes = new Map(), paints = new Map();
  const geometry = item => { geometries.add(item); return item; };
  const texture = item => { textures.add(item); item.colorSpace = THREE.SRGBColorSpace; return item; };
  function material(color, options = {}) {
    const key = options.clippingPlanes ? null : color + JSON.stringify(options);
    if (key && paints.has(key)) return paints.get(key);
    const paint = new THREE.MeshStandardMaterial({ color, roughness: .65, metalness: .02, ...options });
    materials.add(paint); if (key) paints.set(key, paint); return paint;
  }
  function box(w, h, d, radius = .06) {
    const key = [w, h, d, radius].join(':');
    if (!boxes.has(key)) boxes.set(key, geometry(new RoundedBoxGeometry(w, h, d, 2, Math.min(radius, Math.min(w, h, d) / 2))));
    return boxes.get(key);
  }
  function mesh(shape, paint, parent, x = 0, y = 0, z = 0, shadows = true) {
    const item = new THREE.Mesh(shape, paint); item.position.set(x, y, z);
    item.castShadow = shadows; item.receiveShadow = true; parent.add(item); return item;
  }
  return { geometries, materials, textures, geometry, texture, material, box, mesh,
    dispose() { for (const item of [...geometries, ...materials, ...textures]) item.dispose(); } };
}

export function createTaskTown(canvas, callbacks = {}) {
  const scene = new THREE.Scene(); scene.background = new THREE.Color('#eaf0f3');
  scene.fog = new THREE.Fog('#eaf0f3', 80, 300);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  renderer.localClippingEnabled = true;
  const camera = new THREE.PerspectiveCamera(49, 1, .12, 450);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = .085; controls.enablePan = false;
  controls.minDistance = 3; controls.maxDistance = 190; controls.minPolarAngle = .28; controls.maxPolarAngle = 1.43;
  scene.add(new THREE.HemisphereLight('#f7ffff', '#7d909c', 2.2));
  const sunlight = new THREE.DirectionalLight('#fff8ef', 3.0); sunlight.position.set(-14, 26, 18); sunlight.castShadow = true;
  sunlight.shadow.mapSize.set(2048, 2048); Object.assign(sunlight.shadow.camera, { left: -24, right: 24, top: 29, bottom: -29, near: 1, far: 75 });
  sunlight.shadow.normalBias = .035; sunlight.shadow.bias = -.0003; scene.add(sunlight);
  scene.add(new THREE.DirectionalLight('#c5e8ff', .8));
  const kit = resourceKit(), characters = createCompanyCharacters(kit), residents = new Map();
  let layout = companyLayout([]), environment, environmentKit, environmentSignature = '';
  const physics = new CompanyPhysics(layout);
  const navigation = new CompanyNavigation(physics, layout.bounds);
  const destinationPaint = new THREE.MeshBasicMaterial({ color: '#4287c7', transparent: true, opacity: .85, side: THREE.DoubleSide, depthWrite: false }); kit.materials.add(destinationPaint);
  const destinationMarker = kit.mesh(kit.geometry(new THREE.RingGeometry(.18, .31, 40)), destinationPaint, scene, 0, .095, 0, false);
  destinationMarker.rotation.x = -Math.PI / 2; destinationMarker.visible = false;
  const mascot = characters.mascot(); mascot.root.position.set(0, .76, 6.4); mascot.root.rotation.y = Math.PI; scene.add(mascot.root);
  const screenCanvas = document.createElement('canvas'); screenCanvas.width = 1280; screenCanvas.height = 720;
  const screenTexture = kit.texture(new THREE.CanvasTexture(screenCanvas));
  const screenContext = screenCanvas.getContext('2d');
  let selected = null, hovered = null, nearby = null, interaction = null, roaming = true, overview = false, seated = false;
  let paused = false, disposed = false, water = .12, mood = 'idle', width = 1, height = 1;
  let elapsed = 0, last = 0, flight = null, down = null, screenTask = null, screenState = 'idle', imageGeneration = 0, decoding = false, pendingFrame = null;
  let frameNumber = 0, currentRoom = 'office';
  let navigationState = 'idle', destinationAge = 0;
  const keys = new Set(), reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(), forward = new THREE.Vector3(), right = new THREE.Vector3(), movement = new THREE.Vector3();
  const projected = new THREE.Vector3(), color = new THREE.Color();
  function screenStatus(task, state) {
    imageGeneration++; decoding = false; pendingFrame = null; screenTask = task; screenState = state;
    screenContext.fillStyle = '#132936'; screenContext.fillRect(0, 0, 1280, 720);
    screenContext.fillStyle = '#4dcbb5'; screenContext.fillRect(54, 54, 8, 32);
    screenContext.fillStyle = '#b4d6df'; screenContext.font = '500 24px "Microsoft YaHei", sans-serif'; screenContext.fillText('ASSIGNMENT STUDIO / LIVE', 83, 79);
    screenContext.fillStyle = '#f0f6f8'; screenContext.font = '600 39px "Microsoft YaHei", sans-serif';
    const title = task?.assignment_title || '任务运行总览'; screenContext.fillText(title.slice(0, 28), 58, 290);
    screenContext.font = '26px "Microsoft YaHei", sans-serif'; screenContext.fillStyle = '#92b2bf';
    screenContext.fillText(state === 'connecting' ? '正在连接任务浏览器' : state === 'disconnected' ? '画面已断开，可重新连接' : '当前没有运行中的浏览器', 58, 355);
    if (!task) {
      const counts = layout.rooms;
      screenContext.fillStyle = '#d3e8ed'; screenContext.fillText('办公 ' + counts[0].count + '    休息 ' + counts[1].count + '    待处理 ' + counts[2].count, 58, 485);
    }
    screenTexture.needsUpdate = true;
  }
  function browserFrame(frame) {
    if (disposed) return; pendingFrame = frame;
    function paint() {
      if (decoding || !pendingFrame || disposed) return;
      const next = pendingFrame, token = imageGeneration; pendingFrame = null; decoding = true;
      const image = new Image();
      image.onload = () => {
        if (disposed || token !== imageGeneration) return;
        screenContext.fillStyle = '#101b24'; screenContext.fillRect(0, 0, 1280, 720);
        const scale = Math.min(1280 / image.naturalWidth, 676 / image.naturalHeight);
        const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
        screenContext.drawImage(image, (1280 - w) / 2, (676 - h) / 2 + 44, w, h);
        screenContext.fillStyle = '#182e3c'; screenContext.fillRect(0, 0, 1280, 44);
        screenContext.fillStyle = '#6de1bd'; screenContext.font = '20px "Microsoft YaHei", sans-serif'; screenContext.fillText('LIVE  /  ' + (screenTask?.assignment_title || ''), 22, 29);
        screenTexture.needsUpdate = true; screenState = 'connected'; frameNumber++; decoding = false; paint();
      };
      image.onerror = () => { if (token === imageGeneration && !disposed) { decoding = false; screenStatus(screenTask, 'disconnected'); } };
      image.src = 'data:image/jpeg;base64,' + next.image;
    }
    paint();
  }
  function updateEnvironment() {
    const signature = [layout.deskRows, layout.sofaRows, layout.suspensionRows, layout.roomDepth].join(':');
    if (signature === environmentSignature) return;
    if (environment) { scene.remove(environment.group); environmentKit.dispose(); }
    environmentSignature = signature; environmentKit = resourceKit();
    environment = buildCompanyEnvironment(environmentKit, layout, screenTexture); scene.add(environment.group); physics.configure(layout);
    navigation.rebuild(layout.bounds);
    const center = (layout.bounds.back + layout.bounds.front) / 2, reach = Math.max(25, (layout.bounds.front - layout.bounds.back) / 2 + 4);
    sunlight.position.set(-14, 26, center + 18); sunlight.target.position.set(0, 0, center); scene.add(sunlight.target);
    Object.assign(sunlight.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, far: Math.max(75, reach * 3) }); sunlight.shadow.camera.updateProjectionMatrix();
  }
  function setTasks(tasks, visibleKeys = new Set()) {
    const previousLayout = layout; layout = companyLayout(tasks); updateEnvironment();
    const incoming = new Set(layout.residents.map(entry => entry.key));
    for (const [key, item] of residents) if (!incoming.has(key)) {
      scene.remove(item.root, item.rope, item.ring); kit.materials.delete(item.paint); item.paint.dispose(); residents.delete(key);
      if (selected === key) { selected = null; callbacks.selection?.(null); }
    }
    for (const entry of layout.residents) {
      let item = residents.get(entry.key);
      if (!item) { item = characters.bot(entry); residents.set(entry.key, item); scene.add(item.root, item.rope, item.ring); }
      else if (item.room !== entry.room || Math.hypot(item.x - entry.x, item.z - entry.z) > .05) {
        item.path = companyRoute(item.root.position, entry); item.moving = true;
      }
      Object.assign(item, entry); item.root.visible = visibleKeys.has(entry.key); item.rope.visible = item.root.visible && entry.pose === 'suspended';
    }
    if (physics.player.position.x < layout.bounds.left || physics.player.position.z < layout.bounds.back || physics.player.position.z > layout.bounds.front) physics.teleport(layout.spawn.x, layout.spawn.z);
    if (!screenTask && screenState === 'idle') screenStatus(null, 'idle');
    if (overview && previousLayout.officeDepth !== layout.officeDepth) reset();
  }
  function fly(position, target) {
    if (reduced.matches) { camera.position.copy(position); controls.target.copy(target); flight = null; }
    else flight = { position: position.clone(), target: target.clone() };
  }
  function reset() {
    navigation.cancel();
    selected = null; hovered = null; callbacks.selection?.(null); keys.clear(); overview = true; callbacks.roaming?.(false);
    const center = (layout.bounds.front + layout.bounds.back) / 2;
    const halfAngle = Math.min(camera.fov * Math.PI / 360, Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect));
    const radius = Math.hypot(15.4, (layout.bounds.front - layout.bounds.back) / 2, 4.8);
    const offset = new THREE.Vector3(.4, 1, .85).normalize().multiplyScalar(radius / Math.sin(halfAngle) * 1.06);
    fly(new THREE.Vector3(0, .65, center).add(offset), new THREE.Vector3(0, .65, center));
  }
  function follow() {
    overview = false; roaming = true; keys.clear(); callbacks.roaming?.(true);
    const inside = companyRoomAt(mascot.root.position) !== 'office';
    fly(mascot.root.position.clone().add(new THREE.Vector3(0, inside ? 3.4 : 4.5, inside ? 5.1 : 7.4)), mascot.root.position.clone().add(new THREE.Vector3(0, .35, -1)));
  }
  function focus(key) {
    const item = residents.get(key); if (!item?.root.visible) return;
    navigation.cancel(); selected = key; keys.clear(); overview = false;
    const point = item.root.position.clone();
    fly(point.clone().add(new THREE.Vector3(1.8, 2.2, 4.2)), point.clone()); callbacks.selection?.(key);
  }
  function visitRoom(id) {
    const room = layout.rooms.find(item => item.id === id); if (!room) return;
    navigation.cancel(); selected = null; seated = false; callbacks.selection?.(null);
    physics.teleport(room.x, id === 'office' ? 1 : -2.45); mascot.root.position.set(room.x, .76, id === 'office' ? 1 : -2.45); follow();
  }
  function sitBoss() {
    navigation.cancel();
    keys.clear(); selected = null; seated = true; overview = false; callbacks.selection?.(null);
    physics.teleport(layout.boss.seatX, layout.boss.seatZ); mascot.root.position.set(layout.boss.seatX, 1.28, layout.boss.seatZ);
    fly(new THREE.Vector3(12.7, 3.5, -2.4), new THREE.Vector3(10, 1.7, layout.bounds.back)); callbacks.monitor?.();
  }
  function resize() {
    width = Math.max(canvas.clientWidth, 1); height = Math.max(canvas.clientHeight, 1);
    renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
  }
  function project(point) {
    projected.copy(point).project(camera);
    return { x: (projected.x + 1) * width / 2, y: (1 - projected.y) * height / 2, depth: projected.z,
      visible: projected.z > -1 && projected.z < 1 && Math.abs(projected.x) < 1.15 && Math.abs(projected.y) < 1.15 };
  }
  function nearbyUpdate() {
    const playerRoom = companyRoomAt(mascot.root.position);
    const next = selected ? null : nearestTownResident(mascot.root.position, [...residents].map(([key, item]) => ({ key, x: item.root.position.x, z: item.root.position.z, visible: item.root.visible && item.room === playerRoom, transitioning: item.moving })), nearby);
    if (next !== nearby) { nearby = next; callbacks.nearby?.(next); }
    const atDesk = Math.hypot(mascot.root.position.x - layout.boss.exitX, mascot.root.position.z - layout.boss.exitZ) < 2;
    const nextInteraction = seated ? 'stand' : atDesk ? 'boss' : next ? 'task' : null;
    if (interaction !== nextInteraction) { interaction = nextInteraction; callbacks.interaction?.(interaction); }
  }
  function interact() {
    navigation.cancel();
    if (seated) { seated = false; physics.teleport(layout.boss.exitX, layout.boss.exitZ); mascot.root.position.set(layout.boss.exitX, .76, layout.boss.exitZ); follow(); }
    else if (interaction === 'boss') sitBoss();
    else if (nearby) focus(nearby);
  }
  function keyDown(event) {
    if (event.isComposing || townTypingTarget(event.target)) return;
    const key = event.key.toLowerCase();
    if (event.code === 'Space' || key === ' ') {
      event.preventDefault();
      if (event.repeat || selected || seated) return;
      if (overview) follow();
      physics.jump();
      return;
    }
    if (key === 'escape') { navigation.cancel(); selected = null; callbacks.selection?.(null); follow(); return; }
    if (key === 'f') { event.preventDefault(); if (!event.repeat) interact(); return; }
    if (!TOWN_MOVE_KEYS.has(key)) return;
    event.preventDefault();
    navigation.cancel();
    if (seated) interact();
    if (selected) { selected = null; callbacks.selection?.(null); follow(); }
    if (overview) follow();
    keys.add(key);
  }
  function keyUp(event) { keys.delete(event.key.toLowerCase()); }
  function clearKeys() { keys.clear(); navigation.cancel(); down = null; }
  function pointerDown(event) { if (event.button !== 0) return; canvas.focus({ preventScroll: true }); down = { x: event.clientX, y: event.clientY }; }
  function pointerUp(event) {
    if (event.button !== 0) return;
    if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) { down = null; return; } down = null;
    const rect = canvas.getBoundingClientRect(); pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1); raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([...residents.values()].filter(item => item.root.visible).map(item => item.shell));
    if (hits[0]) { const match = [...residents.values()].find(item => item.shell === hits[0].object); if (match) focus(match.key); return; }
    if (raycaster.intersectObject(mascot.root, true).length) { callbacks.monitor?.(); return; }
    if (environment && raycaster.intersectObject(environment.screen).length) { callbacks.screen?.(); return; }
    const ground = raycaster.intersectObject(environment.group, true).find(hit => {
      for (let object = hit.object; object; object = object.parent) if (!object.visible) return false;
      return true;
    });
    if (!ground || ground.point.y > .14) return;
    if (seated) interact();
    if (selected) { selected = null; callbacks.selection?.(null); follow(); }
    keys.clear(); navigation.moveTo(physics.player.position, ground.point); destinationAge = 0;
    destinationMarker.position.set(ground.point.x, .095, ground.point.z);
  }
  function lost(event) { event.preventDefault(); callbacks.error?.('3D 画面连接已中断，请重新加载场景。'); }
  canvas.addEventListener('pointerdown', pointerDown); canvas.addEventListener('pointerup', pointerUp); canvas.addEventListener('pointercancel', clearKeys); canvas.addEventListener('keydown', keyDown); canvas.addEventListener('blur', clearKeys); canvas.addEventListener('webglcontextlost', lost);
  window.addEventListener('keyup', keyUp); window.addEventListener('blur', clearKeys);
  const observer = new ResizeObserver(resize); observer.observe(canvas);
  updateEnvironment(); screenStatus(null, 'idle'); resize();
  camera.position.copy(mascot.root.position).add(new THREE.Vector3(0, 4.5, 7.4)); controls.target.copy(mascot.root.position).add(new THREE.Vector3(0, .35, -1)); controls.update();
  let notifiedAt = 0;
  function animate(timestamp) {
    if (disposed) return;
    const dt = Math.min(.05, Math.max(.001, last ? (timestamp - last) / 1000 : .016)); last = timestamp;
    if (document.hidden) return;
    const animated = !paused && !reduced.matches;
    if (animated) elapsed += dt;
    const before = mascot.root.position.clone();
    movement.set(0, 0, 0);
    if (keys.size && !selected && !seated) {
      camera.getWorldDirection(forward); forward.y = 0; forward.normalize(); right.crossVectors(forward, camera.up).normalize();
      if (keys.has('w') || keys.has('arrowup')) movement.add(forward);
      if (keys.has('s') || keys.has('arrowdown')) movement.sub(forward);
      if (keys.has('a') || keys.has('arrowleft')) movement.sub(right);
      if (keys.has('d') || keys.has('arrowright')) movement.add(right);
      movement.normalize().multiplyScalar(4.4);
    }
    else if (!selected && !seated) {
      const velocity = navigation.velocity(physics.player.position, dt); movement.set(velocity.x, 0, velocity.z);
    }
    if (!seated) {
      const position = physics.step(dt, movement.x, movement.z);
      mascot.root.position.set(position.x, position.y + .24 + (animated ? Math.sin(elapsed * 2.4) * .025 : 0), position.z);
      if (movement.lengthSq() > .01) {
        const heading = Math.atan2(movement.x, movement.z);
        mascot.root.rotation.y += Math.atan2(Math.sin(heading - mascot.root.rotation.y), Math.cos(heading - mascot.root.rotation.y)) * (1 - Math.exp(-dt * 10));
      }
    }
    const delta = mascot.root.position.clone().sub(before); delta.y = 0;
    if (!overview && !selected && !seated) {
      if (flight) { flight.position.add(delta); flight.target.add(delta); }
      else { camera.position.add(delta); controls.target.add(delta); }
    }
    if (flight) {
      camera.position.lerp(flight.position, 1 - Math.exp(-dt * 6)); controls.target.lerp(flight.target, 1 - Math.exp(-dt * 6));
      if (camera.position.distanceTo(flight.position) < .015 && controls.target.distanceTo(flight.target) < .015) flight = null;
    }
    if (navigationState !== navigation.state) {
      navigationState = navigation.state; destinationAge = 0; callbacks.navigation?.(navigationState);
    }
    destinationAge += dt;
    destinationMarker.visible = Boolean(navigation.goal) && (navigation.state === 'walking' || destinationAge < 1.2);
    destinationPaint.color.set(navigation.state === 'blocked' ? '#c66b56' : '#4287c7');
    destinationPaint.opacity = navigation.state === 'walking' ? .8 : Math.max(0, 1 - destinationAge / 1.2);
    destinationMarker.scale.setScalar(1 + (animated ? Math.sin(elapsed * 5) * .08 : 0));
    for (const [key, item] of residents) {
      if (item.moving) {
        if (!animated) { item.path = []; item.root.position.set(item.x, item.y, item.z); item.moving = false; }
        else if (item.path.length) {
          const target = item.path[0], dx = target.x - item.root.position.x, dz = target.z - item.root.position.z, distance = Math.hypot(dx, dz);
          if (distance < .06) item.path.shift();
          else { const step = Math.min(distance, dt * 3.1); item.root.position.x += dx / distance * step; item.root.position.z += dz / distance * step; item.heading = Math.atan2(dx, dz); }
          item.root.position.y = THREE.MathUtils.damp(item.root.position.y, .65, 6, dt);
        } else item.moving = false;
      } else {
        const phase = elapsed * 1.5 + item.seed % 29;
        item.root.position.set(item.x + (animated && item.pose === 'suspended' ? Math.sin(phase) * .13 : 0), item.y, item.z);
        if (item.pose !== 'sleeping') item.body.rotation.set(0, item.pose === 'working' ? Math.PI : .3, animated ? Math.sin(phase) * (item.pose === 'suspended' ? .07 : .025) : 0);
        else item.body.rotation.set(.2, .15, Math.PI / 2 + (animated ? Math.sin(phase * .45) * .025 : 0));
      }
      const engaged = key === selected || key === hovered || key === nearby;
      const breath = animated ? Math.sin(elapsed * (item.pose === 'sleeping' ? 1 : 3) + item.seed) * .025 : 0;
      item.body.scale.set(1 + breath, 1 - breath, 1 + breath * .3);
      if (item.moving) { item.body.rotation.set(0, item.heading, animated ? Math.sin(elapsed * 9) * .045 : 0); item.body.position.y = animated ? Math.abs(Math.sin(elapsed * 9)) * .05 : 0; }
      else item.body.position.y = 0;
      const blink = item.pose === 'sleeping' ? .12 : animated && Math.sin(elapsed * .8 + item.seed % 19) > .995 ? .12 : 1;
      item.left.scale.y = item.right.scale.y = blink;
      item.paint.color.lerp(color.set(item.color), 1 - Math.exp(-dt * 5));
      item.harness.visible = item.pose === 'suspended' && !item.moving;
      item.rope.visible = item.root.visible && item.harness.visible;
      const ropePoints = item.rope.geometry.attributes.position;
      ropePoints.setXYZ(0, item.x, 3.6, item.z); ropePoints.setXYZ(1, item.root.position.x, item.root.position.y + .3, item.root.position.z); ropePoints.needsUpdate = true;
      item.ring.visible = engaged && item.root.visible;
      item.ring.position.set(item.root.position.x, .09, item.root.position.z);
    }
    nearbyUpdate();
    const room = companyRoomAt(mascot.root.position);
    if (room !== currentRoom) { currentRoom = room; callbacks.room?.(room); }
    const level = THREE.MathUtils.lerp(-.5, .53, water);
    mascot.waterClip.constant = mascot.root.position.y + level; mascot.surface.position.y = level;
    mascot.surface.scale.setScalar(Math.sqrt(Math.max(0, .595 ** 2 - level ** 2)));
    for (const eye of mascot.eyes) eye.scale.y = mood === 'idle' ? .25 : 1;
    mascot.mouth.rotation.z = mood === 'angry' ? 0 : Math.PI;
    for (const [id, front] of environment.roomFronts) front.visible = overview || id !== currentRoom || camera.position.z < -1.4;
    controls.update();
    if (!overview && !flight) {
      const offset = camera.position.clone().sub(controls.target), length = offset.length();
      raycaster.set(controls.target, offset.normalize());
      const hit = raycaster.intersectObjects(environment.cameraWalls, false)[0];
      if (hit && hit.distance < length) camera.position.copy(controls.target).addScaledVector(offset, Math.max(.9, hit.distance - .2));
    }
    renderer.render(scene, camera);
    if (timestamp - notifiedAt < 45) return; notifiedAt = timestamp;
    const bots = [...residents].filter(([, item]) => item.root.visible).map(([key, item]) => {
      const point = project(item.root.position), upper = project(item.root.position.clone().add(new THREE.Vector3(0, .52, 0)));
      const pixels = Math.max(18, Math.min(95, Math.abs(point.y - upper.y) * 2));
      return { key, ...point, width: pixels, height: pixels * 1.1 };
    });
    callbacks.frame?.({ bots, districts: layout.rooms.map(room => ({ key: room.id, ...project(new THREE.Vector3(room.x, room.id === 'office' ? .2 : 3.8, room.id === 'office' ? 1 : -1.3)) })),
      mascot: project(mascot.root.position), boss: project(new THREE.Vector3(layout.boss.exitX, 1.4, layout.boss.exitZ)),
      selected, nearby, width, height, room: currentRoom, interaction, seated, overview, frameNumber,
      navigation: { state: navigation.state, target: navigation.goal },
      player: { x: mascot.root.position.x, y: mascot.root.position.y, z: mascot.root.position.z, grounded: physics.grounded && !seated } });
  }
  renderer.setAnimationLoop(animate);
  return {
    setTasks, reset, focus, focusDistrict: visitRoom, visitRoom, sitBoss, interact, screenStatus, browserFrame,
    hover(key) { hovered = key; },
    setWater(value) { water = Math.max(.08, Math.min(.95, value)); },
    setMood(value) { mood = value; },
    setPaused(value) { paused = value; },
    setRoaming(value) { if (value) follow(); else reset(); canvas.focus({ preventScroll: true }); },
    zoom(direction) {
      const offset = camera.position.clone().sub(controls.target); offset.multiplyScalar(direction > 0 ? .82 : 1.22);
      offset.clampLength(3, 190); fly(controls.target.clone().add(offset), controls.target); canvas.focus({ preventScroll: true });
    },
    dispose() {
      disposed = true; imageGeneration++; keys.clear(); renderer.setAnimationLoop(null); observer.disconnect(); controls.dispose(); physics.dispose();
      canvas.removeEventListener('pointerdown', pointerDown); canvas.removeEventListener('pointerup', pointerUp); canvas.removeEventListener('pointercancel', clearKeys); canvas.removeEventListener('keydown', keyDown); canvas.removeEventListener('blur', clearKeys); canvas.removeEventListener('webglcontextlost', lost);
      window.removeEventListener('keyup', keyUp); window.removeEventListener('blur', clearKeys);
      environmentKit?.dispose(); kit.dispose(); scene.clear(); renderer.dispose();
    },
  };
}
