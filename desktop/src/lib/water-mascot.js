import { buildScene, createDefinition, interpolatePose, sampleAnimation } from "open-mascot";

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const MODES = { idle: "sleepy", working: "focused", angry: "angry", happy: "beaming" };

export function createWaterMascot(canvas, { detail = false } = {}) {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("浏览器未能创建角色画布");
  const definition = createDefinition({ name: "水球", shape: "round", renderMode: "rigged-2d", width: 240, height: 240 });
  Object.assign(definition.face, { eyeWidth: 19, eyeHeight: 35, eyeGap: 63, eyeY: 0 });
  for (const [mode, expression] of Object.entries(MODES)) definition.animations[mode] = {
    playback: "loop", ambient: mode === "idle" ? .04 : .18,
    blink: mode === "idle" ? "none" : mode === "happy" ? "bright" : "normal",
    steps: [{ expression, holdMs: 6500, transitionMs: 0, easing: "gentle" }]
  };
  // The glass stays still; upstream motion is used for the expression, not body deformation.
  definition.expressions.angry.motion = { body: "none", eyes: "none" };
  let snapshot = { mood: "idle", water: .09, total: 0, paused: false };
  let disposed = false, visible = true, frame = 0, last = 0, elapsed = 0, width = 0, dpr = 1;
  let fill = .09, lookX = 0, lookY = 0, targetX = 0, targetY = 0, ripple = 0;
  let oldPose = null, transitionAt = 0, currentPose = null;
  const media = matchMedia("(prefers-reduced-motion: reduce)");
  let reduced = media.matches;
  let colors;
  function readColors() {
    const style = getComputedStyle(canvas);
    colors = Object.fromEntries(["ink", "air", "rim", "edge", "light", "shadow", "water-top", "water-bottom", "water-back", "foam", "caustic"].map(key => [key, style.getPropertyValue("--mascot-" + key).trim()]));
    definition.face.eyeColor = colors.ink;
  }
  function size() {
    width = canvas.getBoundingClientRect().width || 56;
    dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr); canvas.height = canvas.width;
    readColors(); paint(0); wake();
  }
  function wavePath(level, phase, amplitude, offset = 0) {
    const path = new Path2D();
    path.moveTo(80, 480);
    for (let x = 80; x <= 480; x += 5) path.lineTo(x, level + offset + Math.sin(x * .022 + phase) * amplitude + Math.sin(x * .043 - phase * .7) * amplitude * .35);
    path.lineTo(480, 480); path.closePath();
    return path;
  }
  function paint(delta) {
    if (disposed || !width) return;
    const quiet = reduced || snapshot.paused;
    elapsed += quiet ? 0 : delta;
    const t = quiet ? 0 : elapsed / 1000;
    const ease = quiet ? 1 : 1 - Math.exp(-delta / 520);
    fill += (snapshot.water - fill) * ease;
    if (Math.abs(fill - snapshot.water) < .0004) fill = snapshot.water;
    lookX += (targetX - lookX) * (quiet ? 1 : .14); lookY += (targetY - lookY) * (quiet ? 1 : .14);
    ripple *= quiet ? 0 : .95;
    context.setTransform(canvas.width / 560, 0, 0, canvas.height / 560, 0, 0);
    context.clearRect(0, 0, 560, 560);
    const cx = 280, cy = 265, radius = 188;
    const breath = quiet ? 0 : Math.sin(t * (snapshot.mood === "idle" ? 1.2 : 1.8)) * 2.2;
    const shadow = context.createRadialGradient(280, 478, 5, 280, 478, 154);
    shadow.addColorStop(0, colors.shadow); shadow.addColorStop(1, "transparent");
    context.save(); context.translate(0, 478); context.scale(1, .1); context.translate(0, -478);
    context.fillStyle = shadow; context.fillRect(100, 300, 360, 356); context.restore();
    context.save(); context.translate(0, breath);
    context.beginPath(); context.arc(cx, cy, radius, 0, Math.PI * 2); context.save(); context.clip();
    const air = context.createLinearGradient(110, 90, 450, 440);
    air.addColorStop(0, colors.light); air.addColorStop(.38, colors.air); air.addColorStop(1, colors.rim);
    context.fillStyle = air; context.fillRect(80, 70, 400, 400);
    context.strokeStyle = colors.edge; context.globalAlpha = .14; context.lineWidth = 1.2;
    for (const offset of [-104, 104]) { context.beginPath(); context.ellipse(cx + offset, cy, 46, 180, 0, -.9, .9); context.stroke(); }
    context.globalAlpha = 1;
    if (fill > .001) {
      const level = cy + radius - fill * radius * 2;
      const amplitude = quiet || fill >= .999 ? 0 : (snapshot.mood === "working" ? 5 : 2) + Math.abs(ripple) * .45;
      const water = context.createLinearGradient(0, level - 10, 0, cy + radius);
      water.addColorStop(0, colors["water-top"]); water.addColorStop(1, colors["water-bottom"]);
      context.fillStyle = colors["water-back"]; context.fill(wavePath(level, t * .9 + 2, amplitude * .8, -4));
      context.fillStyle = water; context.fill(wavePath(level, t * 1.1, amplitude));
      context.save(); context.clip(wavePath(level, t * 1.1, amplitude));
      const caustic = context.createRadialGradient(cx - 48, cy + radius - 34, 2, cx - 48, cy + radius - 34, 180);
      caustic.addColorStop(0, colors.caustic); caustic.addColorStop(1, "transparent");
      context.fillStyle = caustic; context.fillRect(80, 70, 400, 400);
      context.strokeStyle = colors.foam; context.globalAlpha = .24; context.lineWidth = 7;
      context.beginPath(); context.ellipse(cx - 8, cy + radius - 44, 124, 29, -.18, 0, Math.PI); context.stroke();
      context.restore(); context.globalAlpha = .65; context.lineWidth = 2; context.strokeStyle = colors.foam;
      context.beginPath();
      for (let x = 80; x <= 480; x += 5) {
        const y = level + Math.sin(x * .022 + t * 1.1) * amplitude + Math.sin(x * .043 - t * .77) * amplitude * .35;
        if (x === 80) context.moveTo(x, y); else context.lineTo(x, y);
      }
      context.stroke(); context.globalAlpha = 1;
    }
    if (detail && snapshot.total > 1 && snapshot.total <= 12) {
      context.lineWidth = 2; context.strokeStyle = colors.ink; context.globalAlpha = .23;
      for (let index = 1; index < snapshot.total; index++) {
        const y = cy + radius - index / snapshot.total * radius * 2;
        const x = cx + Math.sqrt(Math.max(0, radius ** 2 - (y - cy) ** 2));
        context.beginPath(); context.moveTo(x - 17, y); context.lineTo(x - 6, y); context.stroke();
      }
      context.globalAlpha = 1;
    }
    context.restore();
    const rim = context.createLinearGradient(100, 90, 440, 450);
    rim.addColorStop(0, colors.light); rim.addColorStop(.3, colors.edge); rim.addColorStop(.65, colors.light); rim.addColorStop(1, colors.edge);
    context.lineWidth = 3.5; context.strokeStyle = rim; context.beginPath(); context.arc(cx, cy, radius, 0, Math.PI * 2); context.stroke();
    context.globalAlpha = .7; context.strokeStyle = colors.light; context.lineWidth = 13; context.lineCap = "round";
    context.beginPath(); context.arc(cx, cy, radius - 20, Math.PI * 1.09, Math.PI * 1.39); context.stroke();
    context.lineWidth = 5; context.beginPath(); context.arc(cx, cy, radius - 9, .02, .47); context.stroke(); context.globalAlpha = 1;
    let pose = sampleAnimation(definition, snapshot.mood, quiet ? 0 : elapsed, { reducedMotion: quiet }).pose;
    if (oldPose && !quiet) {
      const fraction = Math.min(1, (elapsed - transitionAt) / 450);
      pose = interpolatePose(oldPose, pose, fraction);
      if (fraction === 1) oldPose = null;
    }
    currentPose = structuredClone(pose);
    pose.gaze.x += lookX * 6; pose.gaze.y += lookY * 5;
    const scene = buildScene(definition, pose);
    context.save(); context.translate(0, -15); context.fillStyle = colors.ink;
    for (const eye of scene.eyes) {
      if (snapshot.mood === "happy") {
        context.strokeStyle = colors.ink; context.lineWidth = 8; context.lineCap = "round";
        context.beginPath(); context.moveTo(eye.cx - 14, eye.cy + 3);
        context.quadraticCurveTo(eye.cx, eye.cy - 19, eye.cx + 14, eye.cy + 3); context.stroke();
      } else context.fill(new Path2D(eye.path));
    }
    context.strokeStyle = colors.ink; context.lineWidth = 6; context.lineCap = "round";
    context.beginPath();
    if (snapshot.mood === "happy") {
      context.moveTo(253, 309); context.quadraticCurveTo(280, 351, 309, 309); context.quadraticCurveTo(280, 321, 253, 309); context.fill();
      context.strokeStyle = colors.light; context.lineWidth = 3; context.beginPath(); context.moveTo(264, 317); context.quadraticCurveTo(280, 325, 297, 317); context.stroke();
    } else if (snapshot.mood === "angry") {
      context.moveTo(263, 325); context.quadraticCurveTo(280, 309, 298, 325); context.stroke();
      context.lineWidth = 5; context.beginPath(); context.moveTo(215, 226); context.lineTo(248, 239); context.moveTo(313, 239); context.lineTo(346, 226); context.stroke();
    } else if (snapshot.mood === "working") {
      context.moveTo(266, 313); context.quadraticCurveTo(279, 321, 297, 310); context.stroke();
    } else {
      context.lineWidth = 4; context.ellipse(282, 317, 7, 5.5, 0, 0, Math.PI * 2); context.stroke();
    }
    context.restore();
    if (snapshot.mood === "idle") {
      context.fillStyle = colors.edge; context.globalAlpha = .75; context.font = "600 31px Segoe UI";
      context.fillText("z", 397, 137 + (quiet ? 0 : Math.sin(t) * 4));
      context.globalAlpha = .45; context.font = "600 23px Segoe UI"; context.fillText("z", 427, 108 + (quiet ? 0 : Math.sin(t + 1) * 4)); context.globalAlpha = 1;
    }
    context.restore();
  }
  function tick(time) {
    frame = 0;
    if (disposed || document.hidden || !visible || reduced || snapshot.paused) return;
    const delta = last ? Math.min(64, time - last) : 50;
    if (delta >= 1000 / (detail ? 30 : 24)) { last = time; paint(delta); }
    frame = requestAnimationFrame(tick);
  }
  function wake() {
    if (!disposed && !frame && visible && !document.hidden && !reduced && !snapshot.paused) { last = 0; frame = requestAnimationFrame(tick); }
  }
  function stop() { cancelAnimationFrame(frame); frame = 0; last = 0; }
  function motionChanged() { reduced = media.matches; stop(); paint(0); wake(); }
  function visibilityChanged() { if (document.hidden) stop(); else { paint(0); wake(); } }
  const resize = new ResizeObserver(size); resize.observe(canvas);
  const observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; if (visible) { paint(0); wake(); } else stop(); }); observer.observe(canvas);
  media.addEventListener("change", motionChanged); document.addEventListener("visibilitychange", visibilityChanged); size();
  return {
    setState(next) {
      if (next.mood && next.mood !== snapshot.mood) { oldPose = currentPose; transitionAt = elapsed; }
      snapshot = { ...snapshot, ...next, water: clamp(Number(next.water ?? snapshot.water), 0, 1) };
      if (reduced || snapshot.paused) { fill = snapshot.water; stop(); }
      paint(0); wake();
    },
    look(x, y) { targetX = clamp(x, -1, 1); targetY = clamp(y, -1, 1); if (reduced || snapshot.paused) paint(0); },
    poke() { if (!reduced && !snapshot.paused) ripple = 12; },
    dispose() { disposed = true; stop(); resize.disconnect(); observer.disconnect(); media.removeEventListener("change", motionChanged); document.removeEventListener("visibilitychange", visibilityChanged); }
  };
}
