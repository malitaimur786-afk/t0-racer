type Wall = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

function tzParts(ms: number, timeZone: string): Wall {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const o: Record<string, string> = {};
  for (const p of dtf.formatToParts(new Date(ms))) {
    if (p.type !== "literal") o[p.type] = p.value;
  }
  return {
    year: Number(o.year),
    month: Number(o.month),
    day: Number(o.day),
    hour: Number(o.hour),
    minute: Number(o.minute),
    second: Number(o.second),
  };
}

function zonedTimeToUtc(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  timeZone: string,
): number {
  let t = Date.UTC(year, month - 1, day, hour, minute, second);
  for (let i = 0; i < 4; i++) {
    const p = tzParts(t, timeZone);
    const asIf = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
    const wanted = Date.UTC(year, month - 1, day, hour, minute, second);
    t += wanted - asIf;
  }
  return t;
}

export function nextDropMs(
  now = Date.now(),
  timeZone = "Europe/Rome",
  hhmm = "00:00",
): number {
  const [hh, mm] = hhmm.split(":").map(Number);
  const wall = tzParts(now, timeZone);
  let cand = zonedTimeToUtc(wall.year, wall.month, wall.day, hh, mm, 0, timeZone);
  if (cand <= now) {
    const nxt = new Date(Date.UTC(wall.year, wall.month - 1, wall.day + 1));
    cand = zonedTimeToUtc(
      nxt.getUTCFullYear(),
      nxt.getUTCMonth() + 1,
      nxt.getUTCDate(),
      hh,
      mm,
      0,
      timeZone,
    );
  }
  return cand;
}

export function formatHms(ms: number): { h: string; m: string; s: string; cs: string } {
  const clamped = Math.max(0, ms);
  const totalCs = Math.floor(clamped / 10);
  const cs = totalCs % 100;
  const totalS = Math.floor(clamped / 1000);
  const s = totalS % 60;
  const totalM = Math.floor(totalS / 60);
  const m = totalM % 60;
  const h = Math.floor(totalM / 60);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return { h: pad(h), m: pad(m), s: pad(s), cs: pad(cs) };
}

export function formatZoneTime(ms: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
  }).format(new Date(ms));
}

export function formatZoneDate(ms: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
    day: "2-digit",
    month: "short",
  }).format(new Date(ms));
}
