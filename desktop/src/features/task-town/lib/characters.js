import * as THREE from 'three';

export function createCompanyCharacters(kit) {
  const { mesh, box, geometry, material } = kit;
  const eyes = geometry(new THREE.CapsuleGeometry(.043, .1, 3, 8));
  const eyePaint = material('#fcffff', { emissive: '#d8f8ff', emissiveIntensity: .12 });
  const dark = material('#213d49'), ropePaint = new THREE.LineBasicMaterial({ color: '#75675f' }); kit.materials.add(ropePaint);
  const mouthShape = geometry(new THREE.TorusGeometry(.08, .014, 6, 16, Math.PI));
  const shapes = new Map();
  function shape(kind) {
    if (shapes.has(kind)) return shapes.get(kind);
    let result;
    if (kind === 'soft-cube') result = box(.84, .82, .8, .21);
    else if (kind === 'pillow') result = box(.95, .68, .78, .22);
    else if (kind === 'capsule') result = geometry(new THREE.CapsuleGeometry(.33, .2, 6, 20));
    else if (kind === 'diamond') result = geometry(new THREE.OctahedronGeometry(.53, 1));
    else {
      result = new THREE.SphereGeometry(.44, 28, 20);
      const points = result.attributes.position;
      for (let i = 0; i < points.count; i++) {
        let x = points.getX(i), y = points.getY(i), z = points.getZ(i);
        const ny = y / .44, angle = Math.atan2(z, x);
        if (kind === 'pebble') { x *= 1.12; y *= .78; z *= .92; }
        if (kind === 'bean') { x += .11 * (1 - ny * ny); z *= .92; }
        if (kind === 'pear' || kind === 'droplet') { const wide = 1 - ny * .3; x *= wide; z *= wide; if (kind === 'droplet') y *= 1.15; }
        if (kind === 'squash') { x *= 1.18; y *= .72; z *= 1.04; }
        if (kind === 'triangle') { const wide = 1 - ny * .35; x *= wide; y *= .95; z *= .84; }
        if (kind === 'clover') { const wide = 1 + Math.cos(angle * 3) * .18 * (1 - ny * ny); x *= wide; z *= wide; }
        points.setXYZ(i, x, y, z);
      }
      result.computeVertexNormals(); result.computeBoundingSphere(); geometry(result);
    }
    shapes.set(kind, result); return result;
  }
  function bot(entry) {
    const root = new THREE.Group(), body = new THREE.Group(); root.add(body);
    const paint = new THREE.MeshStandardMaterial({ color: entry.color, roughness: .25, metalness: .09 }); kit.materials.add(paint);
    const shell = mesh(shape(entry.shape), paint, body);
    function faceDepth(x, y) {
      const probe = new THREE.Raycaster(new THREE.Vector3(x, y, 2), new THREE.Vector3(0, 0, -1));
      return (probe.intersectObject(shell, false)[0]?.point.z ?? .35) + .017;
    }
    const left = mesh(eyes, eyePaint, body, -.13, .05, faceDepth(-.13, .05), false);
    const right = mesh(eyes, eyePaint, body, .13, .05, faceDepth(.13, .05), false);
    const mouth = mesh(mouthShape, dark, body, 0, -.14, faceDepth(0, -.14), false); mouth.rotation.z = Math.PI;
    const harness = mesh(geometry(new THREE.TorusGeometry(.39, .035, 6, 32)), material('#bd9c79'), body, 0, .14, 0); harness.rotation.x = Math.PI / 2;
    const ropeGeometry = geometry(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]));
    const rope = new THREE.Line(ropeGeometry, ropePaint); rope.frustumCulled = false;
    const ring = mesh(geometry(new THREE.TorusGeometry(.6, .027, 6, 48)), material('#4caabb', { emissive: '#3f8293', emissiveIntensity: .25 }), new THREE.Group());
    ring.rotation.x = -Math.PI / 2; ring.visible = false;
    root.position.set(entry.x, entry.y, entry.z); root.scale.setScalar(entry.scale);
    return { ...entry, root, body, paint, shell, left, right, mouth, harness, rope, ring, path: [], moving: false, heading: entry.pose === 'working' ? Math.PI : .3 };
  }
  function mascot() {
    const root = new THREE.Group();
    const glass = new THREE.MeshPhysicalMaterial({ color: '#c3e6ef', roughness: .08, transparent: true, opacity: .34, transmission: .16, thickness: .3 }); kit.materials.add(glass);
    mesh(geometry(new THREE.SphereGeometry(.64, 32, 24)), glass, root);
    const waterClip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
    mesh(geometry(new THREE.SphereGeometry(.595, 28, 20)), material('#31acc9', { roughness: .17, metalness: .15, clippingPlanes: [waterClip] }), root);
    const surface = mesh(geometry(new THREE.CircleGeometry(1, 32)), material('#77d4df', { side: THREE.DoubleSide }), root); surface.rotation.x = -Math.PI / 2;
    const eyeItems = [-.16, .16].map(x => mesh(eyes, dark, root, x, .04, .605, false));
    const mouth = mesh(mouthShape, dark, root, 0, -.15, .609, false); mouth.rotation.z = Math.PI;
    const highlight = mesh(geometry(new THREE.TorusGeometry(.61, .025, 6, 22, .8)), material('#f5ffff', { transparent: true, opacity: .8 }), root, 0, 0, .09, false); highlight.rotation.z = 1.65;
    return { root, waterClip, surface, eyes: eyeItems, mouth };
  }
  return { bot, mascot };
}
