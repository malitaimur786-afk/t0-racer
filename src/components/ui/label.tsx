import * as React from "react";
import { cn } from "@/lib/utils";

export const Label = React.forwardRef<HTMLLabelElement, React.ComponentProps<"label">>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        "text-[0.6875rem] font-medium uppercase tracking-[0.16em] text-muted-foreground",
        className,
      )}
      {...props}
    />
  ),
);
Label.displayName = "Label";
