"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

export function ExamTabs({ examId, tabs }: { examId: string; tabs: { slug: string; label: string }[] }) {
  const path = usePathname();
  return (
    <nav aria-label="Exam sections" className="flex flex-wrap gap-1 border-b">
      {tabs.map((t) => {
        const href = `/exams/${examId}/${t.slug}`;
        const active = path === href;
        return (
          <Link
            key={t.slug}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
              active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
