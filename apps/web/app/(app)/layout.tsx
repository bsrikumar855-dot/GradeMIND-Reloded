import Link from "next/link";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiGet, type Me } from "@/lib/api";

const ROLE_LABEL: Record<Me["role"], string> = { admin: "Administrator", teacher: "Teacher", examiner: "Examiner" };

/** Authenticated shell. Every page below it requires a valid session (checked against the API on each request). */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await apiGet<Me>("/api/me");
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b bg-background">
        <div className="mx-auto flex min-h-14 max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2">
          <Link href="/" className="font-semibold">
            GradeMIND
          </Link>
          <nav aria-label="Main" className="flex gap-1">
            <Button asChild variant="ghost" size="sm">
              <Link href="/">Dashboard</Link>
            </Button>
            <Button asChild variant="ghost" size="sm">
              <Link href="/exams">Exams</Link>
            </Button>
            {me.role === "admin" ? (
              <Button asChild variant="ghost" size="sm">
                <Link href="/admin/users">Users</Link>
              </Button>
            ) : null}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden sm:inline">
              <span className="font-medium">{me.display_name}</span>
              <span className="text-muted-foreground"> · {ROLE_LABEL[me.role]}</span>
            </span>
            <form method="post" action="/api/session/logout">
              <Button type="submit" variant="outline" size="sm">
                <LogOut aria-hidden="true" />
                Sign out
              </Button>
            </form>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        {children}
      </main>
    </div>
  );
}
