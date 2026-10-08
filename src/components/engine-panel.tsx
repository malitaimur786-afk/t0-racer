import { SPEED_WINS } from "@/lib/t0-engine";

export function EnginePanel() {
  return (
    <section id="engine" className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="font-mono text-micro uppercase tracking-[0.18em] text-subtle">The rebuild</p>
        <h2 className="font-display text-3xl tracking-tight text-balance sm:text-4xl">
          Eight cuts on the hot path
        </h2>
        <p className="max-w-xl text-pretty text-sm leading-relaxed text-muted-foreground">
          The old racer was already careful. T0 takes the remaining milliseconds
          off the path between a green cell existing and PRENOTA being pressed.
          Still one browser. Still you on the captcha.
        </p>
      </header>
      <ol className="flex flex-col divide-y divide-border">
        {SPEED_WINS.map((w, i) => (
          <li key={w.id} className="grid gap-3 py-5 sm:grid-cols-[4.5rem_1fr_1fr] sm:gap-6">
            <span className="font-mono text-sm tabular-nums text-subtle">
              {String(i + 1).padStart(2, "0")}
            </span>
            <div className="flex flex-col gap-1">
              <h3 className="text-sm font-medium">{w.title}</h3>
              <p className="text-sm leading-relaxed text-muted-foreground">{w.after}</p>
            </div>
            <p className="text-sm leading-relaxed text-subtle">{w.before}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
