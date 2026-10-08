"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Download, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DEFAULT_CONFIG,
  loadConfig,
  saveConfig,
  toConfigJson,
  type RacerConfig,
} from "@/lib/t0-config";

const FILES = [
  { href: "/racer/bot.py", name: "bot.py" },
  { href: "/racer/t0.js", name: "t0.js" },
  { href: "/racer/config.example.json", name: "config.example.json" },
  { href: "/racer/selftest.py", name: "selftest.py" },
  { href: "/racer/requirements.txt", name: "requirements.txt" },
  { href: "/racer/README.md", name: "README.md" },
  { href: "/t0-racer.zip", name: "t0-racer.zip" },
];

export function ConfigDesk() {
  const [cfg, setCfg] = useState<RacerConfig>(DEFAULT_CONFIG);
  const [copied, setCopied] = useState(false);
  const json = useMemo(() => toConfigJson(cfg), [cfg]);

  useEffect(() => {
    setCfg(loadConfig());
  }, []);

  function patch(p: Partial<RacerConfig>) {
    setCfg((c) => {
      const next = { ...c, ...p };
      saveConfig(next);
      return next;
    });
  }

  async function copyJson() {
    await navigator.clipboard.writeText(json);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <section id="desk" className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="font-mono text-micro uppercase tracking-[0.18em] text-subtle">Config desk</p>
        <h2 className="font-display text-3xl tracking-tight text-balance sm:text-4xl">
          Stays on this device
        </h2>
        <p className="max-w-xl text-pretty text-sm leading-relaxed text-muted-foreground">
          Credentials never leave the browser. Copy the JSON onto your machine as
          <span className="font-mono text-foreground"> config.json</span> next to
          the racer, then run it where you can see the captcha.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <form
          className="flex flex-col gap-4 rounded-xl bg-card p-4 shadow-[var(--shadow-border)] sm:p-5"
          onSubmit={(e) => e.preventDefault()}
        >
          <Field label="Email" htmlFor="email">
            <Input
              id="email"
              type="email"
              autoComplete="off"
              value={cfg.email}
              onChange={(e) => patch({ email: e.target.value })}
              placeholder="you@example.com"
            />
          </Field>
          <Field label="Password" htmlFor="password">
            <Input
              id="password"
              type="password"
              autoComplete="off"
              value={cfg.password}
              onChange={(e) => patch({ password: e.target.value })}
              placeholder="••••••••"
            />
          </Field>
          <Field label="Passport number" htmlFor="passport">
            <Input
              id="passport"
              value={cfg.passport_number}
              onChange={(e) => patch({ passport_number: e.target.value })}
              placeholder="AB1234567"
            />
          </Field>
          <Field label="Service match" htmlFor="svc">
            <Input
              id="svc"
              value={cfg.service_match}
              onChange={(e) => patch({ service_match: e.target.value })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Drop time" htmlFor="drop">
              <Input
                id="drop"
                value={cfg.drop_time}
                onChange={(e) => patch({ drop_time: e.target.value })}
              />
            </Field>
            <Field label="Burst seconds" htmlFor="burst">
              <Input
                id="burst"
                type="number"
                min={1}
                step={0.5}
                value={cfg.burst_s}
                onChange={(e) => patch({ burst_s: Number(e.target.value) })}
              />
            </Field>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button type="button" onClick={copyJson} size="sm">
              {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
              {copied ? "Copied" : "Copy config.json"}
            </Button>
            <Button type="button" variant="outline" size="sm" asChild>
              <a href="/t0-racer.zip" download>
                <Download className="size-4" />
                Download racer
              </a>
            </Button>
          </div>
        </form>

        <div className="flex flex-col gap-3">
          <pre className="max-h-[28rem] overflow-auto rounded-xl bg-elevated p-4 font-mono text-micro leading-relaxed text-muted-foreground shadow-[var(--shadow-border)]">
            {json}
          </pre>
          <ul className="flex flex-wrap gap-2">
            {FILES.map((f) => (
              <li key={f.name}>
                <a
                  href={f.href}
                  download={f.name}
                  className="inline-flex h-9 items-center rounded-sm bg-elevated px-3 font-mono text-micro text-foreground shadow-[var(--shadow-border)] hover:shadow-[var(--shadow-border-hover)]"
                >
                  {f.name}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}
