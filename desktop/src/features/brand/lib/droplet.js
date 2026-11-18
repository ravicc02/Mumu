/* Visual-only completion feedback: a droplet travels from a finished task row
 * to the Mumu mascot. It never touches task state and is skipped for reduced motion. */
const verified = job => job.status === "completed" && (job.task_kind === "learning" || job.submission_status === "submitted_verified");

export function newlyCompleted(previous, jobs) {
  const finished = [];
  for (const job of jobs) {
    const before = previous.get(job.id);
    if (before !== undefined && !before && verified(job)) finished.push(job);
  }
  return finished;
}

export function completionSnapshot(jobs) {
  return new Map(jobs.map(job => [job.id, verified(job)]));
}

function visible(element) {
  if (!element) return null;
  const box = element.getBoundingClientRect();
  if (!box.width || !box.height || box.bottom < 0 || box.top > innerHeight) return null;
  return box;
}

export function launchDroplet(source, target, { reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches } = {}) {
  const to = visible(target);
  if (!to || reducedMotion) return false;
  const from = visible(source) || { left: to.left - 160, top: to.top + 140, width: 0, height: 0 };
  const drop = document.createElement("span");
  drop.className = "mumu-droplet";
  drop.setAttribute("aria-hidden", "true");
  document.body.append(drop);
  const x0 = from.left + Math.min(from.width, 60) / 2, y0 = from.top + from.height / 2;
  const x1 = to.left + to.width / 2, y1 = to.top + to.height * .62;
  const lift = Math.min(120, Math.abs(y0 - y1) * .4 + 40);
  const frames = Array.from({ length: 19 }, (_, index) => {
    const t = index / 18, ease = 1 - (1 - t) ** 3;
    const x = x0 + (x1 - x0) * ease, y = y0 + (y1 - y0) * ease - Math.sin(Math.PI * t) * lift;
    const scale = t < .15 ? .4 + t * 4 : 1 - Math.max(0, t - .8) * 2.5;
    return { transform: `translate3d(${x - 6}px,${y - 8}px,0) scale(${scale.toFixed(3)})`, opacity: t > .94 ? 0 : 1 };
  });
  const flight = drop.animate(frames, { duration: 720, easing: "linear" });
  flight.finished.catch(() => {}).finally(() => {
    drop.remove();
    target.classList.remove("is-catching");
    void target.offsetWidth;
    target.classList.add("is-catching");
    setTimeout(() => target.classList.remove("is-catching"), 520);
  });
  return true;
}
