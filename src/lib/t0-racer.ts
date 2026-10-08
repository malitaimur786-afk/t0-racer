export type RaceEvent =
  | { type: "day"; label: string; at: number }
  | { type: "slot"; label: string; at: number }
  | { type: "prenota"; at: number }
  | { type: "done"; at: number };

export type RacerMode = "observer" | "poll";

type Handle = {
  stop: () => void;
};

const GREEN_RX = /green|dispon|available|prenotab|bookable|\bok\b|open|attivo|success|libero/i;
const RED_RX =
  /red|rosso|non[\-]?disp|unavail|disabled|closed|full|passat|error|scadut|busy|occupat/i;
const DAY_RX = /^\d{1,2}$/;
const SLOT_RX = /^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}$/;

function rgb(str: string): [number, number, number] | null {
  const m = str.match(/[\d.]+/g);
  if (!m || m.length < 3) return null;
  return [Number(m[0]), Number(m[1]), Number(m[2])];
}

function greenish(cs: CSSStyleDeclaration): boolean {
  const c = rgb(cs.backgroundColor);
  return !!c && c[1] > 90 && c[1] > c[0] + 25 && c[1] > c[2] + 25;
}

function reddish(cs: CSSStyleDeclaration): boolean {
  const c = rgb(cs.backgroundColor);
  return !!c && c[0] > 140 && c[1] < 110 && c[2] < 110;
}

function isGreenHolder(el: Element, holder: Element): boolean {
  const cls = `${holder.className} ${el.className}`;
  if (RED_RX.test(cls)) return false;
  if (el.getAttribute("aria-disabled") === "true") return false;
  if (GREEN_RX.test(cls)) return true;
  try {
    const cs = getComputedStyle(holder);
    if (reddish(cs)) return false;
    if (greenish(cs)) return true;
    if (holder !== el) {
      const cs2 = getComputedStyle(el);
      if (greenish(cs2) && !reddish(cs2)) return true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

function fire(el: HTMLElement): boolean {
  try {
    el.click();
    return true;
  } catch {
    return false;
  }
}

export function attachRacer(
  root: HTMLElement,
  opts: {
    mode: RacerMode;
    pollMs?: number;
    t0: number;
    onEvent: (e: RaceEvent) => void;
  },
): Handle {
  let stopped = false;
  let lastDay = "";
  let lastSlot = "";
  let prenota = false;
  let done = false;
  const pollMs = opts.pollMs ?? 50;

  const tick = () => {
    if (stopped || done) return;

    if (!lastDay) {
      const nodes = root.querySelectorAll<HTMLElement>("button[data-day], [data-day]");
      for (const el of nodes) {
        const t = (el.textContent || "").trim();
        if (!DAY_RX.test(t)) continue;
        if (!isGreenHolder(el, el)) continue;
        if (fire(el)) {
          lastDay = t;
          opts.onEvent({ type: "day", label: t, at: performance.now() - opts.t0 });
          break;
        }
      }
    }

    if (lastDay && !lastSlot) {
      const nodes = root.querySelectorAll<HTMLElement>("[data-slot]");
      for (const el of nodes) {
        const t = (el.textContent || "").replace(/\s+/g, " ").trim();
        if (!SLOT_RX.test(t)) continue;
        if (!isGreenHolder(el, el)) continue;
        if (fire(el)) {
          lastSlot = t;
          opts.onEvent({ type: "slot", label: t, at: performance.now() - opts.t0 });
          break;
        }
      }
    }

    if (lastSlot && !prenota) {
      const btn = root.querySelector<HTMLElement>("[data-prenota]");
      if (btn && btn.offsetParent !== null && fire(btn)) {
        prenota = true;
        opts.onEvent({ type: "prenota", at: performance.now() - opts.t0 });
      }
    }

    if (root.querySelector("[data-receipt]")) {
      done = true;
      opts.onEvent({ type: "done", at: performance.now() - opts.t0 });
    }
  };

  let mo: MutationObserver | null = null;
  let interval: number | null = null;
  let raf = 0;

  if (opts.mode === "observer") {
    mo = new MutationObserver(() => tick());
    mo.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "data-open", "aria-disabled"],
    });
    const loop = () => {
      tick();
      if (!stopped && !done) raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  } else {
    interval = window.setInterval(tick, pollMs);
  }

  if (opts.mode === "observer") tick();

  return {
    stop: () => {
      stopped = true;
      mo?.disconnect();
      if (interval != null) window.clearInterval(interval);
      if (raf) cancelAnimationFrame(raf);
    },
  };
}
