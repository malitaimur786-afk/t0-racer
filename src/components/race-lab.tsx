"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { Crosshair } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { attachRacer, type RaceEvent } from "@/lib/t0-racer";
import { cn } from "@/lib/utils";

const DAYS = Array.from({ length: 31 }, (_, i) => i + 1);
const OPEN_DAYS = [7, 18];
const SLOTS = [
  { t: "10:01-10:30", open: false },
  { t: "11:01-11:30", open: true },
  { t: "14:01-14:30", open: true },
];

type LaneState = {
  pickedDay: number | null;
  pickedSlot: string | null;
  booked: boolean;
  events: RaceEvent[];
};

const IDLE: LaneState = { pickedDay: null, pickedSlot: null, booked: false, events: [] };

type Phase = "idle" | "armed" | "live" | "done";

export function RaceLab() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [armLeft, setArmLeft] = useState(3);
  const [t0, setT0] = useState<LaneState>(IDLE);
  const [legacy, setLegacy] = useState<LaneState>(IDLE);
  const [open, setOpen] = useState(false);
  const t0Root = useRef<HTMLDivElement>(null);
  const legacyRoot = useRef<HTMLDivElement>(null);
  const originRef = useRef(0);
  const handles = useRef<{ stop: () => void }[]>([]);

  useEffect(() => {
    if (!open) return;
    const origin = originRef.current;
    const onT0 = (e: RaceEvent) => {
      setT0((prev) => applyEvent(prev, e));
      if (e.type === "prenota") setPhase("done");
    };
    const onLeg = (e: RaceEvent) => setLegacy((prev) => applyEvent(prev, e));
    if (t0Root.current) {
      handles.current.push(
        attachRacer(t0Root.current, { mode: "observer", t0: origin, onEvent: onT0 }),
      );
    }
    if (legacyRoot.current) {
      handles.current.push(
        attachRacer(legacyRoot.current, { mode: "poll", pollMs: 50, t0: origin, onEvent: onLeg }),
      );
    }
    return () => {
      handles.current.forEach((h) => h.stop());
      handles.current = [];
    };
  }, [open]);

  function reset() {
    handles.current.forEach((h) => h.stop());
    handles.current = [];
    setT0(IDLE);
    setLegacy(IDLE);
    setOpen(false);
  }

  function arm() {
    reset();
    setPhase("armed");
    setArmLeft(3);
    let n = 3;
    const tick = () => {
      n -= 1;
      setArmLeft(n);
      if (n <= 0) {
        setPhase("live");
        originRef.current = performance.now();
        window.setTimeout(() => setOpen(true), 40);
        return;
      }
      window.setTimeout(tick, 700);
    };
    window.setTimeout(tick, 700);
  }

  const t0Ms = lastMs(t0);
  const legMs = lastMs(legacy);
  const delta = t0Ms != null && legMs != null ? Math.max(0, legMs - t0Ms) : null;

  return (
    <section id="lab" className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <p className="font-mono text-2xs uppercase tracking-[0.18em] text-subtle">
            Race laboratory
          </p>
          <h2 className="font-display text-3xl tracking-tight text-balance sm:text-4xl">
            Same drop. Two engines.
          </h2>
          <p className="max-w-lg text-pretty text-sm leading-relaxed text-muted-foreground">
            A mock calendar goes green 40 ms after T-0 — the way a real drop
            paints cells. T0 clicks in the mutation turn. The legacy lane still
            waits on a 50 ms CDP poll.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {phase === "armed" ? (
            <span className="font-mono text-4xl tabular-nums text-foreground">{armLeft}</span>
          ) : null}
          <Button onClick={arm} disabled={phase === "armed" || phase === "live"} size="lg">
            <Crosshair className="size-4" />
            {phase === "idle" ? "Arm the lab" : phase === "done" ? "Run again" : "Armed"}
          </Button>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Lane
          title="T0 in-page"
          hint="MutationObserver + rAF"
          tone="slot"
          state={t0}
          open={open}
          rootRef={t0Root}
          onDay={(d) => setT0((s) => ({ ...s, pickedDay: d }))}
          onSlot={(t) => setT0((s) => ({ ...s, pickedSlot: t }))}
          onBook={() => setT0((s) => ({ ...s, booked: true }))}
        />
        <Lane
          title="Legacy CDP poll"
          hint="50 ms Python evaluate"
          tone="mute"
          state={legacy}
          open={open}
          rootRef={legacyRoot}
          onDay={(d) => setLegacy((s) => ({ ...s, pickedDay: d }))}
          onSlot={(t) => setLegacy((s) => ({ ...s, pickedSlot: t }))}
          onBook={() => setLegacy((s) => ({ ...s, booked: true }))}
        />
      </div>

      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-border">
        <Stat label="T0" value={fmtMs(t0Ms)} />
        <Stat label="Legacy" value={fmtMs(legMs)} />
        <Stat label="Saved" value={delta != null ? `${delta.toFixed(1)} ms` : "—"} accent />
      </div>
    </section>
  );
}

function applyEvent(prev: LaneState, e: RaceEvent): LaneState {
  if (e.type === "day" && prev.events.some((x) => x.type === "day")) return prev;
  if (e.type === "slot" && prev.events.some((x) => x.type === "slot")) return prev;
  if (e.type === "prenota" && prev.events.some((x) => x.type === "prenota")) return prev;
  if (e.type === "done" && prev.events.some((x) => x.type === "done")) return prev;
  const next = { ...prev, events: [...prev.events, e] };
  if (e.type === "day") next.pickedDay = Number(e.label);
  if (e.type === "slot") next.pickedSlot = e.label;
  if (e.type === "done") next.booked = true;
  return next;
}

function lastMs(s: LaneState): number | null {
  const done = s.events.find((e) => e.type === "prenota");
  return done ? done.at : null;
}

function fmtMs(n: number | null): string {
  if (n == null) return "—";
  return `${n.toFixed(1)} ms`;
}

function Lane({
  title,
  hint,
  tone,
  state,
  open,
  rootRef,
  onDay,
  onSlot,
  onBook,
}: {
  title: string;
  hint: string;
  tone: "slot" | "mute";
  state: LaneState;
  open: boolean;
  rootRef: RefObject<HTMLDivElement | null>;
  onDay: (d: number) => void;
  onSlot: (t: string) => void;
  onBook: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 rounded-xl bg-card p-4 shadow-[var(--shadow-border)] sm:p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          <p className="font-mono text-micro uppercase tracking-[0.14em] text-subtle">{hint}</p>
        </div>
        {state.booked ? (
          <Badge tone="slot">Booked</Badge>
        ) : (
          <Badge tone={tone === "slot" ? "slot" : "mute"}>{open ? "Live" : "Idle"}</Badge>
        )}
      </div>
      <div ref={rootRef} className="flex flex-col gap-3">
        <div className="grid grid-cols-7 gap-1">
          {["L", "M", "M", "G", "V", "S", "D"].map((d, i) => (
            <span
              key={`${d}-${i}`}
              className="text-center font-mono text-micro uppercase tracking-wider text-subtle"
            >
              {d}
            </span>
          ))}
          {DAYS.map((d) => {
            const isOpen = open && OPEN_DAYS.includes(d);
            const picked = state.pickedDay === d;
            return (
              <button
                key={d}
                type="button"
                data-day={d}
                data-open={isOpen ? "1" : undefined}
                className={cn(
                  "flex size-9 items-center justify-center rounded-sm font-mono text-xs tabular-nums transition-colors duration-[var(--motion-micro)] sm:size-10",
                  isOpen ? "bg-slot text-background" : "bg-closed/80 text-background/90",
                  picked && "outline outline-2 outline-offset-1 outline-foreground",
                )}
                onClick={() => onDay(d)}
              >
                {d}
              </button>
            );
          })}
        </div>
        <div className="flex min-h-10 flex-wrap gap-2">
          {state.pickedDay ? (
            SLOTS.map((s) => (
              <button
                key={s.t}
                type="button"
                data-slot={s.t}
                className={cn(
                  "rounded-sm px-2.5 py-2 font-mono text-2xs tabular-nums",
                  s.open ? "bg-slot text-background" : "bg-closed text-background/90",
                  state.pickedSlot === s.t && "outline outline-2 outline-offset-1 outline-foreground",
                )}
                onClick={() => s.open && onSlot(s.t)}
              >
                {s.t}
              </button>
            ))
          ) : (
            <p className="text-xs text-subtle">Waiting for a green day</p>
          )}
        </div>
        {state.pickedSlot ? (
          <button
            type="button"
            data-prenota="1"
            className="h-10 rounded-sm bg-primary px-4 text-sm font-medium text-primary-foreground"
            onClick={onBook}
          >
            PRENOTA
          </button>
        ) : null}
        {state.booked ? (
          <p data-receipt="1" className="font-mono text-xs uppercase tracking-[0.14em] text-slot">
            Prenotazione confermata · {state.pickedSlot}
          </p>
        ) : null}
      </div>
      <ol className="flex flex-col gap-1 font-mono text-2xs tabular-nums text-muted-foreground">
        {state.events
          .filter((e) => e.type !== "done")
          .map((e, i) => (
            <li key={`${e.type}-${i}`}>
              +{e.at.toFixed(1)} ms{" "}
              {e.type === "day"
                ? `day ${e.label}`
                : e.type === "slot"
                  ? `slot ${e.label}`
                  : "PRENOTA"}
            </li>
          ))}
      </ol>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex flex-col gap-1 bg-card px-4 py-4">
      <span className="font-mono text-micro uppercase tracking-[0.16em] text-subtle">{label}</span>
      <span className={cn("font-mono text-xl tabular-nums sm:text-2xl", accent && "text-slot")}>
        {value}
      </span>
    </div>
  );
}
