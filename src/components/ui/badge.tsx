import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Badge({
  className,
  tone = "mute",
  children,
}: {
  className?: string;
  tone?: "mute" | "slot" | "closed";
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-2 py-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.14em]",
        tone === "mute" && "bg-elevated text-muted-foreground",
        tone === "slot" && "bg-slot/15 text-slot",
        tone === "closed" && "bg-closed/15 text-closed",
        className,
      )}
    >
      {children}
    </span>
  );
}
