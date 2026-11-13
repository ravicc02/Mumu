/* Shared spring, resize and dropdown motion primitives. */
export function stepSpring(state, dt, stiffness = 330, damping = 26) {
  state.v += ((state.target - state.p) * stiffness - state.v * damping) * dt;
  state.p += state.v * dt;
  if (Math.abs(state.p - state.target) < 0.01 && Math.abs(state.v) < 0.05) {
    state.p = state.target;
    state.v = 0;
  }
  return state.p !== state.target || state.v !== 0;
}

export function clamp(value, lower, upper) {
  return Math.max(lower, Math.min(upper, value));
}

export function resistEdge(value, lower, upper) {
  const bounded = clamp(value, lower, upper);
  const excess = value - bounded;
  return bounded + Math.sign(excess) * 15 * (1 - Math.exp(-Math.abs(excess) / 35));
}

// Dropdown pop, arc and landing trajectory, in viewport coordinates.
export function pillFlightFrames(from, to) {
  const back = t => 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2;
  const cubic = t => t < .5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;
  const x0 = from.left;
  const y0 = from.top;
  const x1 = to.left + (to.width - from.width) / 2;
  const y1 = to.top + (to.height - from.height) / 2;
  return Array.from({ length: 23 }, (_, index) => {
    const t = index / 22;
    const pop = t <= .36;
    const p = pop ? back(t / .36) : cubic((t - .36) / .64);
    const arc = pop ? 0 : Math.sin(Math.PI * p);
    const x = pop ? x0 : x0 + (x1 - x0) * p - 6 * arc;
    const y = pop ? y0 - 12 * p : y0 - 12 + (y1 - y0 + 12) * p;
    const scale = pop ? 1 + .36 * p : 1.36 - .36 * p + .08 * arc;
    const tilt = -5.5 * (pop ? p : 1 - p);
    return { transform: `translate3d(${x}px,${y}px,0) rotate(${tilt}deg) scale(${scale})` };
  });
}
