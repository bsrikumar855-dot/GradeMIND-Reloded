import * as React from "react";
import { cn } from "@/lib/utils";

function Alert({ className, variant = "default", ...props }: React.ComponentProps<"div"> & { variant?: "default" | "destructive" }) {
  return (
    <div
      data-slot="alert"
      role={variant === "destructive" ? "alert" : "status"}
      className={cn(
        "w-full rounded-lg border px-4 py-3 text-sm",
        variant === "destructive" ? "border-destructive/40 bg-destructive/5 text-destructive" : "bg-card text-card-foreground",
        className,
      )}
      {...props}
    />
  );
}

export { Alert };
