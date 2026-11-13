import { test } from "node:test";
import assert from "node:assert/strict";
import { createWaterMascot } from "../src/features/brand/lib/mascot.js";

function environment(reduced = false) {
  const previous = new Map(), callbacks = new Map(), events = new Map();
  let serial = 0, disconnects = 0;
  const gradient = { addColorStop() {} };
  const context = new Proxy({}, { get: (target, key) => key in target ? target[key] : key.startsWith("create") ? () => gradient : () => {} });
  const install = (key, value) => { previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, { configurable: true, writable: true, value }); };
  const motion = { matches: reduced, addEventListener: (name, fn) => events.set("media:" + name, fn), removeEventListener: name => events.delete("media:" + name) };
  install("matchMedia", () => motion);
  install("getComputedStyle", () => ({ getPropertyValue: key => key === "--mascot-ink" ? "#1e3848" : "rgba(100,180,210,.6)" }));
  install("devicePixelRatio", 1);
  install("document", { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) });
  install("ResizeObserver", class { observe() {} disconnect() { disconnects++; } });
  install("IntersectionObserver", class { observe() {} disconnect() { disconnects++; } });
  install("Path2D", class { moveTo() {} lineTo() {} closePath() {} });
  install("requestAnimationFrame", callback => { callbacks.set(++serial, callback); return serial; });
  install("cancelAnimationFrame", key => callbacks.delete(key));
  return { canvas: { getContext: () => context, getBoundingClientRect: () => ({ width: 160 }) }, callbacks, events, motion,
    get disconnects() { return disconnects; },
    restore() { for (const [key, descriptor] of previous) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key]; }
  };
}

test("reduced motion paints the true state without starting an animation loop", () => {
  const env = environment(true);
  try {
    const mascot = createWaterMascot(env.canvas);
    mascot.setState({ mood: "happy", water: 1, total: 5 });
    mascot.look(.5, -.2); mascot.poke();
    assert.equal(env.callbacks.size, 0);
    assert.equal(env.canvas.width, 160);
    mascot.dispose();
    assert.equal(env.disconnects, 2);
    assert.equal(env.events.size, 0);
  } finally { env.restore(); }
});

test("pausing motion and hiding the page release scheduled animation frames", () => {
  const env = environment();
  try {
    const mascot = createWaterMascot(env.canvas);
    assert.equal(env.callbacks.size, 1);
    mascot.setState({ mood: "working", water: .4, total: 5, paused: true });
    assert.equal(env.callbacks.size, 0);
    mascot.setState({ paused: false });
    assert.equal(env.callbacks.size, 1);
    document.hidden = true; env.events.get("visibilitychange")();
    assert.equal(env.callbacks.size, 0);
    document.hidden = false; env.events.get("visibilitychange")();
    assert.equal(env.callbacks.size, 1);
    mascot.dispose();
    assert.equal(env.callbacks.size, 0);
    assert.equal(env.events.size, 0);
    assert.equal(env.disconnects, 2);
  } finally { env.restore(); }
});

test("a missing canvas context fails explicitly rather than claiming a rendered bot", () => {
  assert.throws(() => createWaterMascot({ getContext: () => null }), /未能创建角色画布/);
});
