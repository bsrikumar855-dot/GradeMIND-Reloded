import { Alert } from "@/components/ui/alert";
import type { Issue } from "@/lib/client";

export function IssueList({ issues, title = "Fix these before approving" }: { issues: Issue[]; title?: string }) {
  if (!issues.length) return null;
  return (
    <Alert variant="destructive" aria-live="polite">
      <p className="mb-1 font-medium">{title}</p>
      <ul className="list-disc space-y-0.5 pl-5">
        {issues.map((i, k) => (
          <li key={k}>
            {i.message} <span className="text-xs opacity-70">({i.path})</span>
          </li>
        ))}
      </ul>
    </Alert>
  );
}
