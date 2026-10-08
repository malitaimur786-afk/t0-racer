"use client";

import { useEffect, useState } from "react";
import {
  formatHms,
  formatZoneDate,
  formatZoneTime,
  nextDropMs,
} from "@/lib/t0-clock";

export function Countdown() {
  const [now, setNow] = useState<number | null>(null);
  const drop = nextDropMs(now ?? 0);
  const remain = now == null ? 0 : drop - now;
  const t = formatHms(remain);
  const ready = now != null;

  useEffect(() => {
    let id = 0;
    const loop = () => {
      setNow(Date.now());
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, []);

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <p className="max-w-md text-pretty text-sm leading-relaxed text-muted-foreground">
          Next drop is Rome midnight — 23:00 in Tunis while Italy is on summer
          time. T0 fires on the server clock, then the in-page racer books the
          first green cell.
        </p>
        <div className="flex flex-col items-end gap-1 font-mono text-micro uppercase tracking-[0.16em] text-subtle">
          {ready ? (
            <>
              <span>Rome {formatZoneTime(drop, "Europe/Rome")}</span>
              <span>Tunis {formatZoneTime(drop, "Africa/Tunis")}</span>
              <span className="text-muted-foreground">{formatZoneDate(drop, "Europe/Rome")}</span>
            </>
          ) : (
            <span>Rome midnight</span>
          )}
        </div>
      </div>
      <div
        className="flex items-baseline gap-1 font-mono text-foreground tabular-nums"
        aria-live="polite"
        aria-label={ready ? `${t.h} hours ${t.m} minutes ${t.s} seconds until drop` : "Counting down to drop"}
      >
        <DigitPair value={ready ? t.h : "00"} unit="hr" />
        <Colon />
        <DigitPair value={ready ? t.m : "00"} unit="min" />
        <Colon />
        <DigitPair value={ready ? t.s : "00"} unit="sec" />
        <span className="ml-1 text-3xl text-subtle sm:text-5xl">.{ready ? t.cs : "00"}</span>
      </div>
    </section>
  );
}

function DigitPair({ value, unit }: { value: string; unit: string }) {
  return (
    <span className="flex flex-col">
      <span className="text-5xl leading-none tracking-tight sm:text-7xl md:text-8xl">{value}</span>
      <span className="mt-2 font-sans text-micro uppercase tracking-[0.2em] text-subtle">
        {unit}
      </span>
    </span>
  );
}

function Colon() {
  return (
    <span className="self-start pt-1 text-4xl text-subtle sm:pt-2 sm:text-6xl md:text-7xl" aria-hidden>
      :
    </span>
  );
}
