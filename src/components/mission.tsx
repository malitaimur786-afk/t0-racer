"use client";

import { Download, Gauge } from "lucide-react";
import { Countdown } from "@/components/countdown";
import { RaceLab } from "@/components/race-lab";
import { EnginePanel } from "@/components/engine-panel";
import { ConfigDesk } from "@/components/config-desk";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";

export function Mission() {
  return (
    <TooltipProvider delayDuration={200}>
      <div className="min-h-svh bg-background text-foreground">
        <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur-sm">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
            <a href="#top" className="flex items-baseline gap-2">
              <span className="font-display text-2xl tracking-tight">T0</span>
              <span className="hidden font-mono text-micro uppercase tracking-[0.18em] text-subtle sm:inline">
                prenot@mi racer
              </span>
            </a>
            <nav className="flex items-center gap-1 sm:gap-2">
              <a
                href="#lab"
                className="hidden h-9 items-center px-3 text-xs text-muted-foreground hover:text-foreground sm:inline-flex"
              >
                Lab
              </a>
              <a
                href="#engine"
                className="hidden h-9 items-center px-3 text-xs text-muted-foreground hover:text-foreground sm:inline-flex"
              >
                Engine
              </a>
              <a
                href="#desk"
                className="hidden h-9 items-center px-3 text-xs text-muted-foreground hover:text-foreground md:inline-flex"
              >
                Config
              </a>
              <Button asChild size="sm" variant="outline">
                <a href="/t0-racer.zip" download>
                  <Download className="size-4" />
                  <span className="hidden sm:inline">Download</span>
                </a>
              </Button>
            </nav>
          </div>
        </header>

        <main id="top" className="mx-auto flex max-w-6xl flex-col gap-20 px-4 py-10 sm:px-6 sm:py-16">
          <section className="flex flex-col gap-10">
            <div className="flex flex-col gap-4">
              <p className="inline-flex items-center gap-2 font-mono text-micro uppercase tracking-[0.2em] text-slot">
                <Gauge className="size-3.5" />
                In-page midnight instrument
              </p>
              <h1 className="max-w-3xl font-display text-5xl leading-[1.05] tracking-tight text-balance sm:text-6xl md:text-7xl">
                Book the first green cell.
              </h1>
            </div>
            <Countdown />
            <div className="flex flex-wrap gap-3">
              <Button asChild size="lg">
                <a href="#lab">Arm the lab</a>
              </Button>
              <Button asChild size="lg" variant="outline">
                <a href="#desk">Build config.json</a>
              </Button>
            </div>
          </section>

          <Separator />
          <RaceLab />
          <Separator />
          <EnginePanel />
          <Separator />
          <ConfigDesk />
        </main>

        <footer className="border-t border-border">
          <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-8 text-sm text-muted-foreground sm:px-6">
            <p>
              One headed Chromium. One session. You solve the captcha. T0 does
              the rest — on the server’s clock.
            </p>
            <p className="font-mono text-micro uppercase tracking-[0.14em] text-subtle">
              Not a bypass · not a farm · not an API hammer
            </p>
          </div>
        </footer>
      </div>
    </TooltipProvider>
  );
}
