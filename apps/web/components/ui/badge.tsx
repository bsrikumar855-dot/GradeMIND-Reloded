import * as React from "react";
import { cn } from "@/lib/utils";

const tones = {
  neutral: "bg-muted text-foreground",
  success: "bg-success/10 text-success",
  warning: "bg-amber-100 text-amber-900",
  danger: "bg-destructive/10 text-destructive",
} as const;

function Badge({ className, tone = "neutral", ...props }: React.ComponentProps<"span"> & { tone?: keyof typeof tones }) {
  return <span data-slot="badge" className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium", tones[tone], className)} {...props} />;
}

export { Badge };
