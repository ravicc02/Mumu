import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { createCompanyCharacters } from '../../task-town/lib/characters.js';

export function createBrandScene(canvas, { animate = true } = {}) {
  const resources = new Set();
  const keep = value => { resources.add(value); return value; };
  const kit = { materials: resources, geometry: keep,
    material: (color, extra = {}) => keep(new THREE.MeshStandardMaterial({ color, roughness: .28, metalness: .03, ...extra })),
    box: (w, h, d, r) => keep(new RoundedBoxGeometry(w, h, d, 3, r)),
    mesh(shape, paint, parent, x = 0, y = 0, z = 0, shadows = true) {
      const mesh = new THREE.Mesh(shape, paint); mesh.position.set(x, y, z); mesh.castShadow = shadows; mesh.receiveShadow = true; parent.add(mesh); return mesh;
    } };
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2)); renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap; renderer.localClippingEnabled = true;
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(34, 1, .1, 80);
  camera.position.set(0, 1.1, 13); camera.lookAt(0, .1, 0);
  scene.add(new THREE.HemisphereLight('#f5ffff', '#718b99', 2.7));
  const light = new THREE.DirectionalLight('#fff4e7', 3.2); light.position.set(-4, 8, 6); light.castShadow = true;
  light.shadow.mapSize.set(1024, 1024); Object.assign(light.shadow.camera, { left: -7, right: 7, top: 5, bottom: -5 });
  light.shadow.normalBias = .03; scene.add(light);
  const floor = kit.mesh(keep(new THREE.PlaneGeometry(30, 30)), keep(new THREE.ShadowMaterial({ opacity: .12 })), scene, 0, -1.37, 0, false); floor.rotation.x = -Math.PI / 2;
  const characters = createCompanyCharacters(kit), mascot = characters.mascot();
  mascot.root.scale.setScalar(1.75); scene.add(mascot.root);
  const colors = ['#e5b448', '#54bda7', '#d47ea5', '#779ad8', '#b58cce', '#e89868'];
  const bots = ['soft-cube', 'droplet', 'clover', 'capsule', 'diamond', 'pillow'].map((shape, index) => {
    const bot = characters.bot({ shape, color: colors[index], x: 0, y: 0, z: 0, scale: .86 });
    bot.harness.visible = false; scene.add(bot.root); return bot;
  });
  let disposed = false, frame = 0, playing = animate, time = 0, last = 0, visible = !document.hidden;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  function renderAt(seconds = 0) {
    const rect = canvas.getBoundingClientRect(), width = Math.max(1, rect.width), height = Math.max(1, rect.height);
    if (canvas.width !== Math.round(width * renderer.getPixelRatio()) || canvas.height !== Math.round(height * renderer.getPixelRatio())) renderer.setSize(width, height, false);
    camera.aspect = width / height; camera.position.z = Math.max(8, 14.6 / camera.aspect); camera.lookAt(0, .1, 0); camera.updateProjectionMatrix();
    // The orbit closes after eight seconds; the center stays recognizably Mumu.
    const phase = seconds * Math.PI / 4;
    mascot.root.position.y = Math.sin(phase * 2) * .06;
    mascot.root.rotation.y = Math.sin(phase) * .11;
    mascot.waterClip.constant = .02 + Math.sin(phase * 2) * .06;
    mascot.surface.position.y = .012; mascot.surface.scale.setScalar(.595);
    const blink = Math.cos(phase * 3) > .985 ? .18 : 1;
    mascot.eyes.forEach(eye => eye.scale.y = blink);
    for (let index = 0; index < bots.length; index++) {
      const angle = phase + index * Math.PI / 3, bot = bots[index];
      bot.root.position.set(Math.cos(angle) * 3.45, Math.sin(angle) * 1.65 + .08, Math.sin(angle) * .5 - .15);
      bot.root.rotation.set(Math.sin(angle * 2) * .08, Math.sin(angle) * .24, Math.cos(angle) * .12);
      bot.body.scale.y = 1 + Math.sin(phase * 2 + index) * .045;
    }
    renderer.render(scene, camera);
  }
  function tick(now) { frame = 0; if (disposed || !playing || !visible || reduced.matches) return; if (last) time += Math.min(now - last, 50) / 1000; last = now; renderAt(time); frame = requestAnimationFrame(tick); }
  function sync() { cancelAnimationFrame(frame); frame = 0; last = 0; renderAt(reduced.matches ? .4 : time); if (playing && visible && !reduced.matches && !disposed) frame = requestAnimationFrame(tick); }
  const observer = new ResizeObserver(() => renderAt(reduced.matches ? .4 : time)); observer.observe(canvas);
  const onVisibility = () => { visible = !document.hidden; sync(); };
  document.addEventListener('visibilitychange', onVisibility); reduced.addEventListener('change', sync); sync();
  return { renderAt, pause(value) { playing = !value; sync(); }, dispose() { disposed = true; cancelAnimationFrame(frame); observer.disconnect(); document.removeEventListener('visibilitychange', onVisibility); reduced.removeEventListener('change', sync); for (const item of resources) item.dispose?.(); light.shadow.map?.dispose(); light.shadow.mapPass?.dispose(); renderer.dispose(); renderer.forceContextLoss(); } };
}
